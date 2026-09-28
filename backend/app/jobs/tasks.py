import asyncio
import logging
import traceback
from datetime import timedelta

import httpx
from sqlalchemy import and_, delete, func, or_, select, update

from app.ai.controller import ai_controller
from app.ai.embeddings import generate_embeddings_for_snapshot
from app.ai.models import AISkillRun
from app.ai.schemas import TabularCellOutput, TabularRowOutput
from app.contract_brain.ingestion import ingest_contract_brain
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.core.database import SessionLocal, utcnow
from app.core.enums import AISkillRunStatus, JobStatus, ObligationStatus, TabularCellStatus
from app.jobs.celery_app import celery_app
from app.jobs.models import JobRun
from app.tabular_review.models import TabularReview, TabularReviewCell, TabularReviewColumn

logger = logging.getLogger(__name__)

# Only retry on genuinely transient failures (network blips, timeouts). A
# deterministic error — bad input, a validation failure, a missing row — will
# fail again on every retry, so we let it surface immediately instead of
# burning three attempts. Broadening this back to ``Exception`` would mask
# real bugs behind retry noise.
TRANSIENT_ERRORS = (httpx.TransportError, ConnectionError, TimeoutError)


def _humanize_cell_error(exc: Exception) -> str:
    """Turn a raw exception into a reason a lawyer can read.

    The raw ``str(exc)`` (a pydantic ValidationError dump, an httpx timeout, a
    provider 429) is meaningless to an end user and sometimes leaks internals.
    Map the common cases to a plain sentence; fall back to a trimmed message
    for anything unrecognized. The full traceback still lands on the JobRun
    (error_stack) for debugging, so nothing is lost.
    """
    raw = str(exc).strip()
    lowered = raw.lower()
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)) or "timed out" in lowered or "timeout" in lowered:
        return "The AI request timed out before finishing. Re-run this cell to retry."
    if "event loop is closed" in lowered:
        return "A temporary worker error interrupted this cell. Re-run to retry."
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code == 429 or "rate limit" in lowered or "429" in raw:
        return "The AI provider is rate-limiting requests. Wait a moment, then re-run this cell."
    if status_code in (401, 403) or "api key" in lowered or "unauthorized" in lowered:
        return "The AI provider rejected the request (auth/configuration). Contact an admin."
    if isinstance(exc, (httpx.TransportError, ConnectionError)) or "connection" in lowered:
        return "Could not reach the AI provider (network error). Re-run this cell to retry."
    if "validation" in lowered or "validationerror" in exc.__class__.__name__.lower():
        return "The AI returned an unexpected format for this column. Re-run this cell to retry."
    if "no text" in lowered or "snapshot" in lowered or "not found" in lowered:
        return "This contract has no extracted text to analyze yet. Ensure extraction finished, then re-run."
    # Unknown: surface a trimmed single line so the table stays readable.
    first_line = raw.splitlines()[0] if raw else exc.__class__.__name__
    return f"Extraction failed: {first_line[:200]}"

# JobStatus has no dedicated dead-letter member, so terminal failures use the
# closest existing value (FAILED) and are tagged in metadata_json["dead_letter"]
# rather than silently dropped. Introducing a real DEAD_LETTER enum value would
# need an enum change + migration, which is out of scope for this task.
DEAD_LETTER_META_KEY = "dead_letter"

# A worker can't hold a job past Celery's hard time limit (the process is killed),
# so a RUNNING row older than that plus a margin was abandoned by a dead worker.
# Deriving the lease from the limit needs no heartbeat column.
JOB_LEASE = timedelta(seconds=(celery_app.conf.task_time_limit or 900) + 300)
MAX_JOB_ATTEMPTS = 3
_UNDISPATCHED_GRACE = timedelta(minutes=2)  # let a request finish its own commit + dispatch
_RECLAIM_BATCH = 200


def _lease_expired(job: JobRun, now) -> bool:
    return job.started_at is None or job.started_at < now - JOB_LEASE


def _should_run(job: JobRun, now) -> bool:
    """A delivered task proceeds only when nobody legitimately holds the job:
    finished jobs never re-run, and a RUNNING job is taken over only once its
    lease has expired (its worker must be dead)."""
    if job.status in (JobStatus.CANCELLED, JobStatus.SUCCEEDED):
        return False
    if job.status == JobStatus.RUNNING:
        return _lease_expired(job, now)
    return True


@celery_app.task(
    bind=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def run_ai_job(self, job_id: str) -> dict:
    try:
        return asyncio.run(_run_ai_job(job_id))
    except TRANSIENT_ERRORS:
        # Let Celery's autoretry handle transient errors. On the final attempt
        # autoretry re-raises here; flag the job as a terminal dead-letter so it
        # isn't left stuck in RUNNING/FAILED with no indication retries are spent.
        if self.request.retries >= self.max_retries:
            _mark_job_dead_letter(job_id, reason="max_retries_exhausted")
        raise


async def _maybe_auto_review(db, *, job) -> None:
    """After clause extraction, if the contract was flagged for auto AI review
    (freshly drafted from intake, or a counterparty revision just landed), run the
    weighted risk score + the matching playbook's deviation analysis so the REVIEW
    stage is ready without a manual click. Best-effort — never fails the job."""
    contract = db.get(Contract, job.resource_id)
    if contract is None or not (contract.metadata_json or {}).get("auto_review_pending"):
        return

    from app.auth.models import User
    from app.contracts.risk import compute_contract_risk
    from app.core.audit import write_timeline_event
    from app.playbooks.service import auto_review_contract

    user = db.get(User, job.created_by_user_id) if job.created_by_user_id else None
    try:
        if user is not None:
            await compute_contract_risk(db, contract=contract, user=user, request_id=None)
    except Exception as exc:
        logger.warning("auto risk failed for %s: %s", contract.id, exc)
    try:
        res = await auto_review_contract(
            db, contract=contract, actor_user_id=job.created_by_user_id, create_redline=True
        )
        logger.info("auto playbook review for %s: %s", contract.id, res)
    except Exception as exc:
        logger.warning("auto playbook review failed for %s: %s", contract.id, exc)

    contract = db.get(Contract, job.resource_id)
    if contract is not None:
        meta = dict(contract.metadata_json or {})
        meta.pop("auto_review_pending", None)
        contract.metadata_json = meta
        db.commit()
        write_timeline_event(
            db, org_id=contract.org_id, resource_type="contract", resource_id=contract.id,
            event_type="contract.auto_review", title="AI review complete — risk + playbook deviations",
            actor_user_id=job.created_by_user_id,
        )
        db.commit()


async def _run_ai_job(job_id: str) -> dict:
    db = SessionLocal()
    try:
        # Locked read + immediate status flip to RUNNING (committed below) is the
        # guard: a redelivered/duplicate task (worker died mid-job, broker
        # requeued) blocks on the lock, then sees RUNNING/SUCCEEDED/CANCELLED and
        # short-circuits instead of re-running the skill and double-spending AI
        # calls / duplicating writes.
        job = db.scalar(select(JobRun).where(JobRun.id == job_id).with_for_update())
        if job is None:
            raise RuntimeError(f"Job not found: {job_id}")
        if not _should_run(job, utcnow()):
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
            await _maybe_auto_review(db, job=job)
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
        elif job.job_type == "document_text_extraction":
            from app.contract_files.service import process_uploaded_document

            outcome = await process_uploaded_document(db, job=job)
            job = db.get(JobRun, job_id)
            _mark_job_succeeded(job)
            job.metadata_json = {**(job.metadata_json or {}), **outcome}
            db.commit()
        elif job.job_type == "intake_triage":
            from app.intake.service import run_intake_triage

            # Sync code whose AI calls bridge through run_coro_blocking: run it in a
            # thread so this worker's own event loop isn't the one blocked.
            await asyncio.to_thread(
                run_intake_triage, db, request_id=job.resource_id, actor_id=job.created_by_user_id
            )
            job = db.get(JobRun, job_id)
            _mark_job_succeeded(job)
            db.commit()
        elif job.job_type == "intake_screening":
            from app.intake.service import run_intake_screening

            # Conflicts and sanctions are database queries, not AI calls, but
            # they still block: off the worker's event loop like the rest.
            await asyncio.to_thread(
                run_intake_screening, db, request_id=job.resource_id,
                actor_id=job.created_by_user_id,
            )
            job = db.get(JobRun, job_id)
            _mark_job_succeeded(job)
            db.commit()
        elif job.job_type in _TABULAR_JOB_TYPES:
            await _run_tabular_job(db, job)
        else:
            job.status = JobStatus.FAILED
            job.error_message = f"Unsupported job type for AI architecture spine: {job.job_type}"
            job.finished_at = utcnow()
            db.commit()
        return {"job_id": job_id, "status": db.get(JobRun, job_id).status}
    except Exception as exc:
        # A failed flush leaves the session unusable; without this rollback the
        # FAILED write below raised PendingRollbackError, the job stayed RUNNING
        # forever, and the RUNNING short-circuit blocked every retry.
        db.rollback()
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


@celery_app.task
def reclaim_stale_jobs() -> dict:
    """Every few minutes: recover jobs no worker will ever finish. job_run is the
    durable outbox; this is its poller.
      * RUNNING past the lease (worker died): re-queue, or dead-letter after
        MAX_JOB_ATTEMPTS so a job that crashes its worker can't loop forever.
      * QUEUED with no celery_task_id (the enqueue failed, e.g. a Redis blip):
        dispatch again."""
    db = SessionLocal()
    try:
        return _reclaim_stale_jobs(db, now=utcnow())
    finally:
        db.close()


def _reclaim_stale_jobs(db, *, now) -> dict:
    from app.jobs.service import dispatch_job

    requeued = dead_lettered = redispatched = dispatch_failed = 0
    stale = db.scalars(
        select(JobRun)
        .where(
            JobRun.status == JobStatus.RUNNING,
            or_(JobRun.started_at.is_(None), JobRun.started_at < now - JOB_LEASE),
        )
        .order_by(JobRun.started_at.asc())
        .limit(_RECLAIM_BATCH)
        .with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        if job.attempt_count >= MAX_JOB_ATTEMPTS:
            job.status = JobStatus.FAILED
            job.finished_at = now
            job.error_message = job.error_message or "The worker stopped before finishing this job."
            job.metadata_json = {
                **(job.metadata_json or {}),
                DEAD_LETTER_META_KEY: {"reason": "lease_expired", "at": now.isoformat()},
            }
            _fail_stuck_cells(db, job)
            dead_lettered += 1
        else:
            job.status = JobStatus.QUEUED
            job.celery_task_id = None
            requeued += 1
    db.commit()

    undispatched = db.scalars(
        select(JobRun)
        .where(
            JobRun.status == JobStatus.QUEUED,
            JobRun.celery_task_id.is_(None),
            JobRun.created_at < now - _UNDISPATCHED_GRACE,
        )
        .order_by(JobRun.created_at.asc())
        .limit(_RECLAIM_BATCH)
        .with_for_update(skip_locked=True)
    ).all()
    for job in undispatched:
        try:
            dispatch_job(db, job=job)
            db.commit()
            redispatched += 1
        except Exception:
            db.rollback()
            dispatch_failed += 1
            logger.warning("reclaim: dispatch still failing for job %s", job.id, exc_info=True)
    return {
        "requeued": requeued,
        "dead_lettered": dead_lettered,
        "redispatched": redispatched,
        "dispatch_failed": dispatch_failed,
    }


_TABULAR_JOB_TYPES = ("tabular_cell_extraction", "tabular_row_extraction")


def _tabular_cell_ids(job: JobRun) -> list[str]:
    meta = job.metadata_json or {}
    return list(meta.get("cell_ids") or ([meta["cell_id"]] if meta.get("cell_id") else []))


def _claim_tabular_cells(db, job: JobRun) -> list[TabularReviewCell]:
    """Lock this job's cells, leaving out any that a newer job was dispatched for.

    The newest job for a cell owns it (it acts as a fencing token), so a
    superseded run, like the first run of a cell that was since re-run, can never
    overwrite newer results. The caller's commit releases the locks.
    """
    cell_ids = _tabular_cell_ids(job)
    if not cell_ids:
        return []
    cells = db.scalars(
        select(TabularReviewCell)
        .where(TabularReviewCell.id.in_(cell_ids))
        .order_by(TabularReviewCell.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    newer = db.scalars(
        select(JobRun).where(
            JobRun.org_id == job.org_id,
            JobRun.job_type.in_(_TABULAR_JOB_TYPES),
            JobRun.created_at > job.created_at,
            or_(
                and_(JobRun.resource_type == "tabular_cell", JobRun.resource_id.in_(cell_ids)),
                and_(
                    JobRun.resource_type == "tabular_review",
                    JobRun.resource_id == (job.metadata_json or {}).get("tabular_review_id"),
                ),
            ),
        )
    ).all()
    taken = {cell_id for other in newer for cell_id in _tabular_cell_ids(other)}
    return [cell for cell in cells if cell.id not in taken]


async def _run_tabular_job(db, job: JobRun) -> None:
    cells = _claim_tabular_cells(db, job)
    if not cells:
        _mark_job_succeeded(job)  # newer jobs own every cell; nothing left to do
        db.commit()
        return
    column_ids = {cell.column_id for cell in cells}
    columns = {
        column.id: column
        for column in db.scalars(select(TabularReviewColumn).where(TabularReviewColumn.id.in_(column_ids))).all()
    }
    for cell in cells:
        cell.status = TabularCellStatus.RUNNING
    db.commit()
    try:
        if job.job_type == "tabular_cell_extraction":
            cell = cells[0]
            output = await ai_controller.run_job_skill(
                db,
                job=job,
                skill_name="tabular_cell_extraction",
                input_payload={"question": columns[cell.column_id].prompt, "contract_id": cell.contract_id},
            )
            answers = {
                cell.column_id: output if isinstance(output, TabularCellOutput) else TabularCellOutput.model_validate(output)
            }
        else:
            output = await ai_controller.run_job_skill(
                db,
                job=job,
                skill_name="tabular_row_extraction",
                input_payload={
                    "contract_id": cells[0].contract_id,
                    "questions": [
                        {"column_id": cell.column_id, "question": columns[cell.column_id].prompt} for cell in cells
                    ],
                },
            )
            row = output if isinstance(output, TabularRowOutput) else TabularRowOutput.model_validate(output)
            answers = {}
            for answer in row.answers:
                answers.setdefault(answer.column_id, answer)
    except Exception as exc:
        db.rollback()
        for cell in _claim_tabular_cells(db, job):
            cell.status = TabularCellStatus.FAILED
            cell.error_message = _humanize_cell_error(exc)
            cell.updated_by_user_id = job.created_by_user_id
        db.commit()
        _settle_review(db, job)
        raise
    for cell in _claim_tabular_cells(db, job):
        out = answers.get(cell.column_id)
        cell.updated_by_user_id = job.created_by_user_id
        if out is None:
            cell.status = TabularCellStatus.FAILED
            cell.error_message = "The AI returned no answer for this question. Re-run this cell to retry."
            continue
        cell.answer = out.answer
        cell.reasoning = out.reasoning
        cell.confidence = out.confidence
        cell.citations = [c.model_dump(mode="json") for c in out.citations]
        cell.raw_ai_output = out.model_dump(mode="json")
        cell.error_message = None
        # A cell must be cited or explicitly not_found; otherwise flag it.
        cell.status = TabularCellStatus.COMPLETE if out.not_found or out.citations else TabularCellStatus.NEEDS_REVIEW
    _mark_job_succeeded(job)
    db.commit()
    _settle_review(db, job)



def _settle_review(db, job: JobRun) -> None:
    """Update the review's own status as soon as a job's cells finish, so the list
    isn't left saying "Running" until the periodic sweep. Best-effort."""
    from app.tabular_review.service import reconcile_review_status

    review_id = (job.metadata_json or {}).get("tabular_review_id")
    if not review_id:
        return
    try:
        review = db.get(TabularReview, review_id)
        if review is not None and reconcile_review_status(db, review=review):
            db.commit()
    except Exception:
        db.rollback()
        logger.warning("could not settle tabular review %s", review_id, exc_info=True)

def _fail_stuck_cells(db, job: JobRun) -> None:
    """A dead-lettered tabular job must not leave its cells showing "Running"."""
    if job.job_type not in _TABULAR_JOB_TYPES:
        return
    for cell in _claim_tabular_cells(db, job):
        if cell.status == TabularCellStatus.RUNNING:
            cell.status = TabularCellStatus.FAILED
            cell.error_message = "A temporary worker error interrupted this cell. Re-run to retry."


_RECONCILE_BATCH = 200


@celery_app.task
def reconcile_tabular_reviews() -> dict:
    """Every few minutes: settle tabular reviews whose cells have all finished and
    fail cells stuck past the window, so no review depends on someone opening it."""
    from app.tabular_review.service import ACTIVE_REVIEW_STATUSES, reconcile_review_status

    db = SessionLocal()
    try:
        reviews = db.scalars(
            select(TabularReview)
            .where(TabularReview.status.in_(ACTIVE_REVIEW_STATUSES), TabularReview.deleted_at.is_(None))
            .order_by(TabularReview.updated_at.asc())
            .limit(_RECONCILE_BATCH)
            .with_for_update(skip_locked=True)  # a job settling the same review wins
        ).all()
        settled = sum(1 for review in reviews if reconcile_review_status(db, review=review))
        db.commit()
        return {"settled": settled}
    finally:
        db.close()


_RESUME_BATCH = 200


@celery_app.task
def resume_workflow_runs() -> dict:
    """Every couple of minutes: progress workflow runs without anyone having the
    ticket open. Settles approvals and signatures that finished (or were rejected)
    and runs AI steps that are mid-run."""
    return asyncio.run(_resume_workflow_runs())


async def _resume_workflow_runs() -> dict:
    from app.auth.models import User
    from app.workflows.models import WorkflowRun
    from app.workflows.service import refresh_run

    db = SessionLocal()
    resumed = errors = 0
    try:
        candidates = db.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.status.in_(("running", "waiting")))
        )
        if candidates and candidates > _RESUME_BATCH:
            # A run that polls to no-op never writes, so onupdate never bumps
            # updated_at and the ASC order is frozen: the same _RESUME_BATCH
            # oldest runs are rescanned forever and everything past them
            # starves silently. Surface it rather than let runs quietly stall.
            # ponytail: a warning, not a fix. The real fix is a last_polled_at
            # column to order by (a migration) — do it when this actually fires.
            logger.warning(
                "workflow resume saturated: %s runs waiting, only the oldest %s are polled "
                "each tick — runs past that will not progress",
                candidates,
                _RESUME_BATCH,
            )
        run_ids = db.scalars(
            select(WorkflowRun.id)
            .where(WorkflowRun.status.in_(("running", "waiting")))
            .order_by(WorkflowRun.updated_at.asc())
            .limit(_RESUME_BATCH)
        ).all()
        for run_id in run_ids:
            try:
                # skip_locked: a user action already holding this run wins.
                run = db.scalar(
                    select(WorkflowRun)
                    .where(WorkflowRun.id == run_id, WorkflowRun.status.in_(("running", "waiting")))
                    .with_for_update(skip_locked=True)
                )
                actor = db.get(User, run.created_by_user_id) if run is not None and run.created_by_user_id else None
                if run is None or actor is None:
                    db.rollback()
                    continue
                await refresh_run(db, run=run, actor=actor)
                db.commit()
                resumed += 1
            except Exception:
                db.rollback()
                errors += 1
                logger.warning("scheduled workflow resume failed for run %s", run_id, exc_info=True)
        return {"resumed": resumed, "errors": errors}
    finally:
        db.close()


def _mark_job_dead_letter(job_id: str, *, reason: str) -> None:
    """Mark a job terminally failed after retries are exhausted.

    Opens a fresh session because the per-run session is already closed by the
    time Celery's autoretry re-raises in the task wrapper. Best-effort: a failure
    here must not mask the original exception, so any error is swallowed.
    """
    db = SessionLocal()
    try:
        job = db.get(JobRun, job_id)
        if job is None:
            return
        job.status = JobStatus.FAILED
        if job.finished_at is None:
            job.finished_at = utcnow()
        # Closest terminal value + an explicit dead-letter marker (see
        # DEAD_LETTER_META_KEY) so operators can distinguish "failed once" from
        # "failed and out of retries".
        job.metadata_json = {
            **(job.metadata_json or {}),
            DEAD_LETTER_META_KEY: {"reason": reason, "at": utcnow().isoformat()},
        }
        if not job.error_message:
            job.error_message = f"Dead-lettered: {reason}"
        db.commit()
    except Exception:  # pragma: no cover - defensive; never mask the real error
        db.rollback()
        logger.exception("Failed to dead-letter job %s", job_id)
    finally:
        db.close()


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


# ---------------------------------------------------------------------------
# Periodic maintenance tasks (celery beat). Each is import-safe and a safe
# no-op when its tables are empty, and runs across ALL orgs (no request user).
# Retention windows mirror the schedule in celery_app.py.
# ---------------------------------------------------------------------------

# Mirrors app.obligations.routes.DUE_SOON_DAYS so scheduled and manual runs
# classify obligations identically.
_OBLIGATION_DUE_SOON_DAYS = 7
_REMINDER_BATCH = 500  # per run; the rest go out on the next run
_RENEWAL_BATCH = 500

# Retention windows (days) for the log sweepers. audit_log is deliberately
# absent: it is immutable and retained forever for compliance.
_REQUEST_LOG_RETENTION_DAYS = 90
_AI_CALL_LOG_RETENTION_DAYS = 180
_RESOURCE_TIMELINE_EVENT_RETENTION_DAYS = 365


@celery_app.task
def send_obligation_reminders() -> dict:
    """Daily: recompute obligation statuses and email due reminders, all orgs.

    Org-agnostic counterpart of the per-org /obligations/run-reminders endpoint.
    Safe no-op when there are no open obligations or due reminders.
    """
    return asyncio.run(_send_obligation_reminders())


async def _send_obligation_reminders() -> dict:
    from app.auth.models import User
    from app.integrations.resend import resend_client
    from app.obligations.models import Obligation, ObligationReminder

    db = SessionLocal()
    try:
        today = utcnow().date()
        # Recompute overdue / due-soon across every org's active obligations as three
        # set-based UPDATEs: memory stays flat however many obligations exist.
        soon = today + timedelta(days=_OBLIGATION_DUE_SOON_DAYS)
        live = (Obligation.deleted_at.is_(None), Obligation.due_date.is_not(None))

        def _set_status(to_status, from_statuses, *window):
            return db.execute(
                update(Obligation)
                .where(*live, Obligation.status.in_(from_statuses), *window)
                .values(status=to_status)
                .execution_options(synchronize_session=False)
            ).rowcount

        overdue = _set_status(
            ObligationStatus.OVERDUE, [ObligationStatus.OPEN, ObligationStatus.DUE_SOON], Obligation.due_date < today
        )
        due_soon = _set_status(
            ObligationStatus.DUE_SOON, [ObligationStatus.OPEN, ObligationStatus.OVERDUE],
            Obligation.due_date >= today, Obligation.due_date <= soon,
        )
        _set_status(ObligationStatus.OPEN, [ObligationStatus.DUE_SOON, ObligationStatus.OVERDUE], Obligation.due_date > soon)

        # The status recompute is committed on its own so a slow email batch can't lose it.
        db.commit()

        due_reminders = db.scalars(
            select(ObligationReminder)
            .where(
                ObligationReminder.sent_at.is_(None),
                ObligationReminder.remind_at <= today,
            )
            .order_by(ObligationReminder.remind_at.asc())
            .limit(_REMINDER_BATCH)
        ).all()
        sent = 0
        failed = 0
        for reminder in due_reminders:
            ob = db.get(Obligation, reminder.obligation_id)
            if ob is None or ob.deleted_at is not None or ob.status in {
                ObligationStatus.COMPLETED,
                ObligationStatus.CANCELLED,
            }:
                continue
            owner = db.get(User, ob.owner_user_id) if ob.owner_user_id else None
            # Record the reminder as sent, and commit, BEFORE the email goes out. A
            # crash or time limit after this point can at worst lose this one
            # reminder; it can no longer re-send the whole batch on the next run.
            reminder.sent_at = today
            db.commit()
            if owner is not None:
                obligation_label = ob.obligation_type or "contract obligation"
                try:
                    await resend_client.send_email(
                        to=owner.email,
                        subject=f"Obligation due: {obligation_label}",
                        html=(
                            f"<p>You have a <b>{obligation_label}</b> due on {ob.due_date}.</p>"
                            f"<p>Open the contract workspace to review the details.</p>"
                        ),
                    )
                except Exception:
                    logger.exception(
                        "obligation reminder email failed",
                        extra={"obligation_id": ob.id, "reminder_id": reminder.id},
                    )
                    reminder.sent_at = None  # not delivered: the next run retries it
                    db.commit()
                    failed += 1
                    continue
            sent += 1
        return {
            "reminders_sent": sent,
            "reminders_failed": failed,
            "marked_overdue": overdue,
            "marked_due_soon": due_soon,
        }
    finally:
        db.close()


@celery_app.task
def prune_expired_tokens() -> dict:
    """Hourly: delete token rows whose ``expires_at`` has passed.

    The auth/approval flows already reject expired tokens on `expires_at`; this
    just reclaims the dead rows. Safe no-op when nothing is expired.
    """
    from app.approvals.models import ApprovalToken
    from app.auth.models import PasswordResetToken, RefreshToken, RevokedAccessToken

    db = SessionLocal()
    try:
        now = utcnow()
        deleted: dict[str, int] = {}
        for model in (RefreshToken, RevokedAccessToken, PasswordResetToken, ApprovalToken):
            result = db.execute(delete(model).where(model.expires_at < now))
            deleted[model.__tablename__] = result.rowcount or 0
        db.commit()
        return {"deleted": deleted}
    finally:
        db.close()


@celery_app.task
def check_stage_slas() -> dict:
    """Daily: flag contracts stuck past their stage SLA.

    Day 1 over SLA -> notify the contract owner. sla_escalation_after_days
    later -> escalate to org admins. Exact-day matching keeps this
    naturally idempotent for a once-daily schedule (no dedupe table needed).
    """
    from sqlalchemy import func

    from app.auth.models import Role, User
    from app.contracts.lifecycle import parse_stage_slas
    from app.contracts.models import Contract, ContractStageHistory
    from app.core.config import settings
    from app.notifications.models import Notification

    sla_map = parse_stage_slas(settings.stage_sla_days)
    if not sla_map:
        return {"notified": 0, "escalated": 0}
    db = SessionLocal()
    try:
        now = utcnow()
        contracts = db.scalars(
            select(Contract).where(
                Contract.deleted_at.is_(None),
                Contract.lifecycle_stage.in_(list(sla_map.keys())),
            )
        ).all()
        notified = escalated = 0
        admin_cache: dict[str, list[str]] = {}

        def org_admin_ids(org_id: str) -> list[str]:
            if org_id not in admin_cache:
                rows = db.scalars(
                    select(User).join(User.roles).where(
                        User.org_id == org_id, Role.name == "admin"
                    )
                ).all()
                admin_cache[org_id] = [u.id for u in rows]
            return admin_cache[org_id]

        for contract in contracts:
            entered = db.scalar(
                select(func.max(ContractStageHistory.changed_at)).where(
                    ContractStageHistory.contract_id == contract.id
                )
            ) or contract.created_at
            days = (now - entered).days
            sla = sla_map[contract.lifecycle_stage]
            over = days - sla
            stage = contract.lifecycle_stage
            if over == 1 and contract.owner_user_id:
                db.add(
                    Notification(
                        org_id=contract.org_id,
                        user_id=contract.owner_user_id,
                        channel="in_app",
                        event_type="lifecycle.sla_breach",
                        subject=f"Stuck in {stage}: {contract.title}",
                        body=(
                            f'"{contract.title}" has been in {stage} for {days} days '
                            f"(SLA {sla}d). Move it along or flag the blocker."
                        ),
                        status="sent",
                    )
                )
                notified += 1
            elif over == settings.sla_escalation_after_days + 1:
                recipients = set(org_admin_ids(contract.org_id))
                if contract.owner_user_id:
                    recipients.add(contract.owner_user_id)
                for uid in recipients:
                    db.add(
                        Notification(
                            org_id=contract.org_id,
                            user_id=uid,
                            channel="in_app",
                            event_type="lifecycle.sla_escalation",
                            subject=f"Escalation — stuck in {stage}: {contract.title}",
                            body=(
                                f'"{contract.title}" has now been in {stage} for {days} days '
                                f"(SLA {sla}d) with no movement. Needs intervention."
                            ),
                            status="sent",
                        )
                    )
                escalated += 1
        db.commit()
        return {"notified": notified, "escalated": escalated}
    finally:
        db.close()


@celery_app.task
def close_expired_contracts() -> dict:
    """Daily: close ACTIVE contracts whose expiration date has passed.

    Guardrails: skip any contract with an undecided renewal (an open decision
    means someone is actively working it) or with the renewal_due flag set.
    ACTIVE -> CLOSED is an allowed transition, so the normal state machine,
    stage history and audit trail all apply; the actor is None (system).
    Idempotent — already-closed contracts never match the query.
    """
    from app.contracts.lifecycle import transition_contract_stage
    from app.contracts.models import Contract
    from app.core.enums import ContractLifecycleStage, RenewalDecision
    from app.notifications.models import Notification
    from app.renewals.models import RenewalEvent

    db = SessionLocal()
    try:
        today = utcnow().date()
        candidates = db.scalars(
            select(Contract).where(
                Contract.deleted_at.is_(None),
                Contract.lifecycle_stage == ContractLifecycleStage.ACTIVE,
                Contract.expiration_date.is_not(None),
                Contract.expiration_date < today,
            )
        ).all()
        closed = 0
        skipped = 0
        for contract in candidates:
            undecided_renewal = db.scalar(
                select(RenewalEvent.id).where(
                    RenewalEvent.contract_id == contract.id,
                    RenewalEvent.decision == RenewalDecision.UNDECIDED,
                )
            )
            if undecided_renewal or contract.renewal_due:
                skipped += 1
                continue
            transition_contract_stage(
                db,
                contract=contract,
                to_stage=ContractLifecycleStage.CLOSED,
                actor_user_id=None,
                reason=f"Closed automatically — expired on {contract.expiration_date.isoformat()}",
            )
            if contract.owner_user_id:
                db.add(
                    Notification(
                        org_id=contract.org_id,
                        user_id=contract.owner_user_id,
                        channel="in_app",
                        event_type="contract.auto_closed",
                        subject=f"Contract closed: {contract.title}",
                        body=(
                            f'"{contract.title}" expired on '
                            f"{contract.expiration_date.isoformat()} and was closed "
                            "automatically. Reopen it from the contract page if this "
                            "was renewed outside Aegis."
                        ),
                        status="sent",
                    )
                )
            closed += 1
        db.commit()
        return {"closed": closed, "skipped_pending_renewal": skipped}
    finally:
        db.close()


@celery_app.task
def run_renewal_window_check() -> dict:
    """Daily: move active contracts into renewal_due when their window opens."""
    return asyncio.run(_run_renewal_window_check())


async def _run_renewal_window_check() -> dict:
    import html

    from app.auth.models import User
    from app.core.enums import ContractLifecycleStage
    from app.integrations.resend import resend_client
    from app.renewals.models import RenewalEvent

    db = SessionLocal()
    try:
        today = utcnow().date()
        window_opens = func.coalesce(RenewalEvent.renewal_window_starts_at, RenewalEvent.notice_date)
        # Only actionable rows, in bounded batches: the window has opened, the term
        # hasn't already ended, the contract is live and not yet flagged, and this
        # event hasn't been notified before (so a reset flag can't re-send it).
        events = db.scalars(
            select(RenewalEvent)
            .join(Contract, Contract.id == RenewalEvent.contract_id)
            .where(
                window_opens <= today,
                or_(RenewalEvent.expiration_date.is_(None), RenewalEvent.expiration_date >= today),
                RenewalEvent.metadata_json["notified_at"].as_string().is_(None),
                Contract.lifecycle_stage == ContractLifecycleStage.ACTIVE,
                Contract.renewal_due.is_(False),
                Contract.deleted_at.is_(None),
            )
            .order_by(window_opens.asc())
            .limit(_RENEWAL_BATCH)
        ).all()
        moved = 0
        notify_failed = 0
        for event in events:
            window = event.renewal_window_starts_at or event.notice_date
            if window is None or window > today:
                continue
            contract = db.get(Contract, event.contract_id)
            if (
                contract is None
                or contract.lifecycle_stage != ContractLifecycleStage.ACTIVE
                or contract.renewal_due
            ):
                continue
            actor_user_id = (
                contract.owner_user_id
                or event.owner_user_id
                or contract.updated_by_user_id
                or contract.created_by_user_id
            )
            if not actor_user_id:
                logger.warning(
                    "renewal window check skipped contract with no actor",
                    extra={"contract_id": contract.id, "renewal_event_id": event.id},
                )
                continue
            # Renewal-due is a flag on the (still ACTIVE) contract, not a stage.
            contract.renewal_due = True
            contract.updated_by_user_id = actor_user_id
            event.metadata_json = {**(event.metadata_json or {}), "notified_at": today.isoformat()}
            # Commit the flag before emailing, so a crash mid-batch can't leave it
            # unset and re-send every notification on the next run.
            db.commit()
            owner = db.get(User, event.owner_user_id or contract.owner_user_id)
            if owner is not None:
                safe_title = html.escape(contract.title or "Untitled contract")
                try:
                    await resend_client.send_email(
                        to=owner.email,
                        subject=f"Renewal window open: {contract.title}",
                        html=(
                            f"<p><b>{safe_title}</b> has entered its renewal window "
                            f"(notice date {event.notice_date}, expires {event.expiration_date}).</p>"
                        ),
                    )
                except Exception:
                    logger.exception(
                        "renewal window notification email failed",
                        extra={"renewal_event_id": event.id, "contract_id": contract.id},
                    )
                    notify_failed += 1
            moved += 1
        db.commit()
        return {"contracts_moved_to_renewal_due": moved, "notifications_failed": notify_failed}
    finally:
        db.close()


def _overdue_decision(
    od: dict, days_over: int, escalate_after: int
) -> tuple[bool, bool, bool]:
    """Pure state machine for overdue-approval enforcement.

    Given the stored ``overdue`` metadata (empty on first sight), how many days
    the request is past due, and the escalation cadence, decide what to do this
    run: ``(flag_first_time, send_reminder, escalate_now)``. Kept side-effect
    free so the day-threshold arithmetic is testable without a DB.
    """
    flag = not od
    remind = bool(od) and days_over >= od.get("reminded_day", 0) + escalate_after
    escalate = days_over >= escalate_after and not od.get("escalated")
    return flag, remind, escalate


@celery_app.task
def mark_overdue_approvals() -> dict:
    """Daily: enforce overdue pending approvals — flag, re-nudge, escalate.

    ApprovalStatus has no OVERDUE member, so rather than invent an enum value
    (which would need a migration) we track enforcement state under
    metadata_json["overdue"] and leave status=PENDING:

      * first day overdue  -> flag + notify the approver(s) and the submitter
      * every ``sla_escalation_after_days`` further days -> re-nudge approvers
      * once ``sla_escalation_after_days`` days overdue -> escalate to org
        admins (once)

    Idempotent per day via the ``reminded_day`` / ``escalated`` markers, so a
    daily beat never double-sends. Safe no-op when nothing is overdue.
    """
    from app.approvals.models import ApprovalRequest, ApproverGroup
    from app.auth.models import Role, User
    from app.contracts.models import Contract
    from app.core.config import settings
    from app.core.enums import ApprovalStatus
    from app.intake.models import IntakeRequest
    from app.notifications.models import Notification

    db = SessionLocal()
    try:
        now = utcnow()
        overdue_requests = db.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.due_at.is_not(None),
                ApprovalRequest.due_at < now,
            )
        ).all()

        escalate_after = max(1, settings.sla_escalation_after_days)
        admin_cache: dict[str, list[str]] = {}

        def org_admin_ids(org_id: str) -> list[str]:
            if org_id not in admin_cache:
                rows = db.scalars(
                    select(User).join(User.roles).where(
                        User.org_id == org_id, Role.name == "admin"
                    )
                ).all()
                admin_cache[org_id] = [u.id for u in rows]
            return admin_cache[org_id]

        def notify(uid: str, org_id: str, event: str, subject: str, body: str) -> None:
            db.add(
                Notification(
                    org_id=org_id,
                    user_id=uid,
                    channel="in_app",
                    event_type=event,
                    subject=subject,
                    body=body,
                    status="sent",
                )
            )

        flagged = reminded = escalated = 0
        for req in overdue_requests:
            meta = req.metadata_json or {}
            od = dict(meta.get("overdue") or {})
            days_over = max(0, (now - req.due_at).days)

            # Resolve a human title from whichever subject this row rides —
            # a contract chain or a Legal Intake request (contract_id is null).
            title = "this request"
            if req.contract_id:
                contract = db.get(Contract, req.contract_id)
                if contract is not None:
                    title = contract.title
            elif req.intake_request_id:
                ir = db.get(IntakeRequest, req.intake_request_id)
                if ir is not None:
                    # IntakeRequest has no `title`; `ref` is never null.
                    title = ir.subject or ir.ref

            # Who must act: the assigned approver, or every group member.
            approver_ids: set[str] = set()
            if req.approver_user_id:
                approver_ids.add(req.approver_user_id)
            elif req.approver_group_id:
                group = db.get(ApproverGroup, req.approver_group_id)
                if group is not None:
                    approver_ids.update(m.id for m in group.members)
            due_label = req.due_at.date().isoformat()
            do_flag, do_remind, do_escalate = _overdue_decision(
                od, days_over, escalate_after
            )

            if do_flag:
                # First time overdue: alert approvers + the submitter.
                recipients = set(approver_ids)
                if req.requested_by_user_id:
                    recipients.add(req.requested_by_user_id)
                for uid in recipients:
                    notify(
                        uid, req.org_id, "approval.overdue",
                        f"Approval overdue: {title}",
                        f"Step {req.step_order} of the approval chain for "
                        f"\"{title}\" passed its due date ({due_label}) and is "
                        "still waiting for a decision.",
                    )
                od = {"at": now.isoformat(), "reminded_day": days_over}
                flagged += 1
            elif do_remind:
                # Still stuck: re-nudge the people who must decide.
                for uid in approver_ids:
                    notify(
                        uid, req.org_id, "approval.overdue_reminder",
                        f"Reminder — approval still overdue: {title}",
                        f"Step {req.step_order} for \"{title}\" has been overdue "
                        f"for {days_over} days. Please decide, delegate, or escalate.",
                    )
                od["reminded_day"] = days_over
                reminded += 1

            # Past the threshold: escalate to org admins, once.
            if do_escalate:
                for uid in org_admin_ids(req.org_id):
                    notify(
                        uid, req.org_id, "approval.escalation",
                        f"Escalation — approval {days_over}d overdue: {title}",
                        f"Step {req.step_order} for \"{title}\" is {days_over} days "
                        "overdue and needs intervention — reassign it or decide it.",
                    )
                od["escalated"] = {"at": now.isoformat(), "day": days_over}
                escalated += 1

            req.metadata_json = {**meta, "overdue": od}

        db.commit()
        return {"marked_overdue": flagged, "reminded": reminded, "escalated": escalated}
    finally:
        db.close()


def _prune_older_than(model, *, days: int) -> int:
    """Delete rows of ``model`` whose ``created_at`` is older than ``days``.

    Shared body for the retention sweepers. Bulk DELETE — a no-op (0 rows) when
    the table is empty or nothing is old enough.
    """
    db = SessionLocal()
    try:
        cutoff = utcnow() - timedelta(days=days)
        result = db.execute(delete(model).where(model.created_at < cutoff))
        db.commit()
        return result.rowcount or 0
    finally:
        db.close()


@celery_app.task
def verify_audit_integrity() -> dict:
    """Daily: verify the audit_log hash chain hasn't been tampered with.

    verify_audit_hash_chain() is a real, working tamper-evidence check, but
    nothing called it — it was protecting nothing. A broken chain means a row
    was altered or deleted outside the ORM (the immutability trigger only
    blocks UPDATE/DELETE through normal app code paths), so this is logged at
    CRITICAL rather than merely returned, since Sentry's default logging
    integration escalates ERROR+ log records into alerts.
    """
    from app.core.audit import verify_audit_hash_chain

    db = SessionLocal()
    try:
        intact = verify_audit_hash_chain(db)
        if not intact:
            logger.critical("audit_log hash chain verification FAILED — possible tampering")
        return {"intact": intact}
    finally:
        db.close()


@celery_app.task
def prune_request_log() -> dict:
    """Daily: drop RequestLog rows older than the retention window (>90d)."""
    from app.core.models import RequestLog

    return {"deleted": _prune_older_than(RequestLog, days=_REQUEST_LOG_RETENTION_DAYS)}


@celery_app.task
def prune_ai_call_log() -> dict:
    """Daily: drop AICallLog rows older than the retention window (>180d)."""
    from app.core.models import AICallLog

    return {"deleted": _prune_older_than(AICallLog, days=_AI_CALL_LOG_RETENTION_DAYS)}


@celery_app.task
def prune_resource_timeline_event() -> dict:
    """Daily: drop ResourceTimelineEvent rows older than the window (>365d)."""
    from app.core.models import ResourceTimelineEvent

    return {
        "deleted": _prune_older_than(
            ResourceTimelineEvent, days=_RESOURCE_TIMELINE_EVENT_RETENTION_DAYS
        )
    }


@celery_app.task
def send_notice_reminders() -> dict:
    """Daily: chase notices approaching or past their statutory response
    deadline, across every org.

    Idempotent by construction — the sweep only notifies when a notice's
    milestone (7 days out / 3 days out / due today / lapsed) is more urgent than
    the last one recorded on it, so re-running it the same day sends nothing.
    Answered and closed notices fall out of the query, which is what stops the
    chasing once the work is actually done.

    Org-agnostic counterpart of the per-org POST /notices/run-reminders.
    """
    from app.notices import service as notices_service

    db = SessionLocal()
    try:
        return notices_service.run_reminders(db)
    finally:
        db.close()


# Beat fires this daily, so the guard never blocks a normal run — it exists for
# the restart case. Celery beat keeps its last-run state in a schedule file that
# a container restart loses, so a crash-looping beat would otherwise re-download
# the ~5 MB Treasury file on every boot.
_SANCTIONS_MIN_REFRESH_AGE = timedelta(hours=12)


@celery_app.task
def refresh_sanctions_lists() -> dict:
    """Daily: pull the OFAC SDN list so screening has something to screen against.

    Nothing refreshed it before — the only caller was a manual admin button — so
    the list aged past ``screening.STALE_AFTER`` and every screen returned
    "unavailable". That posture is correct (an unscreened name is never reported
    "clear"), but it meant sanctions screening was in practice off while looking
    like it was on.

    Refreshed ONCE, for the setup-complete organisation. The list is public
    reference data, not tenant data: ``ux_sanctions_source_ref`` is unique on
    ``(source, source_ref)`` with no org_id, so a second copy cannot exist and
    a per-org loop just trips the constraint. `auth.service` defines "the org"
    the same way — there is only ever one.

    Failures are logged loudly rather than raised: a beat task that throws
    disappears into the worker log, and the thing an operator needs to know is
    that the list is aging. ``refreshed_at`` is the evidence trail, and a stale
    list already announces itself on every screened request.
    """
    from app.intake.models import SanctionsListEntry
    from app.intake.screening import refresh_ofac
    from app.organizations.models import Organization

    db = SessionLocal()
    try:
        org_id = db.scalar(
            select(Organization.id).where(Organization.setup_complete.is_(True))
        )
        if org_id is None:
            return {"status": "skipped", "note": "organization setup not complete"}

        now = utcnow()
        newest = db.scalar(
            select(func.max(SanctionsListEntry.refreshed_at)).where(
                SanctionsListEntry.org_id == org_id
            )
        )
        if newest is not None and (now - newest) < _SANCTIONS_MIN_REFRESH_AGE:
            return {"status": "fresh", "refreshed_at": newest.isoformat()}

        try:
            return {"status": "refreshed", **refresh_ofac(db, org_id)}
        except Exception as exc:
            db.rollback()
            age = "never refreshed" if newest is None else f"{(now - newest).days}d old"
            logger.exception(
                "OFAC refresh failed (list is %s) — sanctions screening returns "
                "'unavailable' until this succeeds", age,
            )
            return {"status": "failed", "error": str(exc), "list_age": age}
    finally:
        db.close()
