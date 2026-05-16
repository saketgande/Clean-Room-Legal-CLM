import asyncio
import traceback

from sqlalchemy import select

from app.ai.controller import ai_controller
from app.ai.embeddings import generate_embeddings_for_snapshot
from app.ai.models import AISkillRun
from app.contract_brain.ingestion import ingest_contract_brain
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.ai.schemas import TabularCellOutput
from app.core.database import SessionLocal, utcnow
from app.core.enums import AISkillRunStatus, JobStatus, TabularCellStatus
from app.jobs.celery_app import celery_app
from app.jobs.models import JobRun
from app.tabular_review.models import TabularReviewCell, TabularReviewColumn


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def run_ai_job(self, job_id: str) -> dict:
    return asyncio.run(_run_ai_job(job_id))


async def _run_ai_job(job_id: str) -> dict:
    db = SessionLocal()
    try:
        job = db.get(JobRun, job_id)
        if job is None:
            raise RuntimeError(f"Job not found: {job_id}")
        if job.status == JobStatus.CANCELLED:
            return {"job_id": job_id, "status": job.status}
        job.status = JobStatus.RUNNING
        job.started_at = utcnow()
        job.attempt_count += 1
        job.progress = max(job.progress, 5)
        db.commit()

        if job.job_type == "metadata_extraction":
            await ai_controller.run_job_skill(
                db,
                job=job,
                skill_name="contract_metadata_extraction",
                input_payload={
                    "contract_id": job.resource_id,
                    "contract_version_id": job.metadata_json.get("contract_version_id"),
                    "text_snapshot_id": job.metadata_json.get("text_snapshot_id"),
                },
            )
            _sync_job_from_skill_runs(db, job_id=job.id)
        elif job.job_type == "clause_extraction":
            await ai_controller.run_job_skill(
                db,
                job=job,
                skill_name="clause_extraction",
                input_payload={
                    "contract_id": job.resource_id,
                    "contract_version_id": job.metadata_json.get("contract_version_id"),
                    "text_snapshot_id": job.metadata_json.get("text_snapshot_id"),
                },
            )
            _sync_job_from_skill_runs(db, job_id=job.id)
            _queue_contract_brain_ingestion(db, job=job, reason="after_clause_extraction")
        elif job.job_type == "obligation_extraction":
            await ai_controller.run_job_skill(
                db,
                job=job,
                skill_name="obligation_extraction",
                input_payload={
                    "contract_id": job.resource_id,
                    "contract_version_id": job.metadata_json.get("contract_version_id"),
                    "text_snapshot_id": job.metadata_json.get("text_snapshot_id"),
                },
            )
            _sync_job_from_skill_runs(db, job_id=job.id)
            _queue_contract_brain_ingestion(db, job=job, reason="after_obligation_extraction")
        elif job.job_type == "renewal_extraction":
            await ai_controller.run_job_skill(
                db,
                job=job,
                skill_name="renewal_extraction",
                input_payload={
                    "contract_id": job.resource_id,
                    "contract_version_id": job.metadata_json.get("contract_version_id"),
                    "text_snapshot_id": job.metadata_json.get("text_snapshot_id"),
                },
            )
            _sync_job_from_skill_runs(db, job_id=job.id)
            _queue_contract_brain_ingestion(db, job=job, reason="after_renewal_extraction")
        elif job.job_type == "embeddings":
            snapshot = db.get(ContractTextSnapshot, job.metadata_json.get("text_snapshot_id"))
            if snapshot is None:
                raise RuntimeError("Text snapshot not found for embedding job")
            rows = generate_embeddings_for_snapshot(db, snapshot=snapshot, created_by_user_id=job.created_by_user_id)
            _mark_job_succeeded(job)
            job.metadata_json = {**(job.metadata_json or {}), "embedding_count": len(rows)}
            db.commit()
        elif job.job_type == "contract_brain_ingestion":
            contract = db.get(Contract, job.resource_id)
            version = db.get(ContractVersion, job.metadata_json.get("contract_version_id"))
            snapshot = (
                db.get(ContractTextSnapshot, job.metadata_json.get("text_snapshot_id"))
                if job.metadata_json.get("text_snapshot_id")
                else None
            )
            if contract is None or version is None:
                raise RuntimeError("Contract or version not found for brain ingestion job")
            counts = ingest_contract_brain(
                db,
                org_id=job.org_id,
                created_by_user_id=job.created_by_user_id,
                contract=contract,
                version=version,
                snapshot=snapshot,
            )
            _mark_job_succeeded(job)
            job.metadata_json = {**(job.metadata_json or {}), "graph_node_counts": counts}
            db.commit()
        elif job.job_type == "tabular_cell_extraction":
            cell = db.get(TabularReviewCell, job.metadata_json.get("cell_id"))
            if cell is None:
                raise RuntimeError("Tabular review cell not found")
            column = db.get(TabularReviewColumn, cell.column_id)
            if column is None:
                raise RuntimeError("Tabular review column not found")
            cell.status = TabularCellStatus.RUNNING
            db.commit()
            try:
                output = await ai_controller.run_job_skill(
                    db,
                    job=job,
                    skill_name="tabular_cell_extraction",
                    input_payload={
                        "question": column.prompt,
                        "contract_id": cell.contract_id,
                    },
                )
                out = (
                    output
                    if isinstance(output, TabularCellOutput)
                    else TabularCellOutput.model_validate(output)
                )
                cell.answer = out.answer
                cell.reasoning = out.reasoning
                cell.confidence = out.confidence
                cell.citations = [c.model_dump(mode="json") for c in out.citations]
                cell.raw_ai_output = out.model_dump(mode="json")
                cell.error_message = None
                # A cell must be cited or explicitly not_found; otherwise flag it.
                if out.not_found or out.citations:
                    cell.status = TabularCellStatus.COMPLETE
                else:
                    cell.status = TabularCellStatus.NEEDS_REVIEW
                cell.updated_by_user_id = job.created_by_user_id
                _mark_job_succeeded(job)
                db.commit()
            except Exception as cell_exc:
                cell.status = TabularCellStatus.FAILED
                cell.error_message = str(cell_exc)
                cell.updated_by_user_id = job.created_by_user_id
                db.commit()
                raise
        else:
            job.status = JobStatus.FAILED
            job.error_message = f"Unsupported job type for AI architecture spine: {job.job_type}"
            job.finished_at = utcnow()
            db.commit()
        return {"job_id": job_id, "status": db.get(JobRun, job_id).status}
    except Exception as exc:
        job = db.get(JobRun, job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.error_stack = traceback.format_exc()
            job.finished_at = utcnow()
            db.commit()
        raise
    finally:
        db.close()


def _sync_job_from_skill_runs(db, *, job_id: str) -> None:
    job = db.get(JobRun, job_id)
    if job is None:
        return
    skill_run = db.scalar(
        select(AISkillRun)
        .where(AISkillRun.job_id == job_id)
        .order_by(AISkillRun.created_at.desc())
    )
    if skill_run is None:
        return
    if skill_run.status in {AISkillRunStatus.SUCCEEDED, AISkillRunStatus.NEEDS_REVIEW}:
        _mark_job_succeeded(job)
        job.metadata_json = {
            **(job.metadata_json or {}),
            "ai_skill_run_id": skill_run.id,
            "ai_skill_run_status": skill_run.status,
            "ai_validation_status": skill_run.validation_status,
        }
    elif skill_run.status == AISkillRunStatus.FAILED:
        job.status = JobStatus.FAILED
        job.finished_at = utcnow()
        job.error_message = skill_run.error_message or skill_run.validation_error
    db.commit()


def _mark_job_succeeded(job: JobRun) -> None:
    job.status = JobStatus.SUCCEEDED
    job.progress = 100
    job.finished_at = utcnow()
    job.error_message = None
    job.error_stack = None


def _queue_contract_brain_ingestion(db, *, job: JobRun, reason: str) -> None:
    if job.status != JobStatus.SUCCEEDED:
        return
    version_id = job.metadata_json.get("contract_version_id")
    snapshot_id = job.metadata_json.get("text_snapshot_id")
    if not version_id:
        return
    idempotency_key = f"contract_brain_ingestion:{version_id}:{snapshot_id}:{reason}"
    existing = db.scalar(select(JobRun).where(JobRun.idempotency_key == idempotency_key))
    if existing is not None:
        return
    from app.jobs.service import create_job, dispatch_job

    brain_job = create_job(
        db,
        org_id=job.org_id,
        job_type="contract_brain_ingestion",
        resource_type="contract",
        resource_id=job.resource_id,
        created_by_user_id=job.created_by_user_id,
        idempotency_key=idempotency_key,
        metadata={
            "contract_version_id": version_id,
            "text_snapshot_id": snapshot_id,
            "triggered_by_job_id": job.id,
            "trigger_reason": reason,
        },
    )
    db.flush()
    brain_job_id = brain_job.id
    db.commit()
    brain_job = db.get(JobRun, brain_job_id)
    if brain_job is None:
        return
    dispatch_job(db, job=brain_job)
    db.commit()
