import asyncio
import logging
import traceback
from datetime import timedelta

import httpx
from sqlalchemy import delete, select

from app.ai.controller import ai_controller
from app.ai.embeddings import generate_embeddings_for_snapshot
from app.ai.models import AISkillRun
from app.contract_brain.ingestion import ingest_contract_brain
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.ai.schemas import TabularCellOutput
from app.core.database import SessionLocal, utcnow
from app.core.enums import AISkillRunStatus, JobStatus, ObligationStatus, TabularCellStatus
from app.jobs.celery_app import celery_app
from app.jobs.models import JobRun
from app.tabular_review.models import TabularReviewCell, TabularReviewColumn

logger = logging.getLogger(__name__)

# Only retry on genuinely transient failures (network blips, timeouts). A
# deterministic error — bad input, a validation failure, a missing row — will
# fail again on every retry, so we let it surface immediately instead of
# burning three attempts. Broadening this back to ``Exception`` would mask
# real bugs behind retry noise.
TRANSIENT_ERRORS = (httpx.TransportError, ConnectionError, TimeoutError)

# JobStatus has no dedicated dead-letter member, so terminal failures use the
# closest existing value (FAILED) and are tagged in metadata_json["dead_letter"]
# rather than silently dropped. Introducing a real DEAD_LETTER enum value would
# need an enum change + migration, which is out of scope for this task.
DEAD_LETTER_META_KEY = "dead_letter"


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
        # Recompute overdue / due-soon across every org's active obligations.
        open_obligations = db.scalars(
            select(Obligation).where(
                Obligation.deleted_at.is_(None),
                Obligation.status.in_(
                    [ObligationStatus.OPEN, ObligationStatus.DUE_SOON, ObligationStatus.OVERDUE]
                ),
                Obligation.due_date.is_not(None),
            )
        ).all()
        overdue = due_soon = 0
        for ob in open_obligations:
            if ob.due_date < today:
                ob.status = ObligationStatus.OVERDUE
                overdue += 1
            elif ob.due_date <= today + timedelta(days=_OBLIGATION_DUE_SOON_DAYS):
                ob.status = ObligationStatus.DUE_SOON
                due_soon += 1
            else:
                ob.status = ObligationStatus.OPEN

        due_reminders = db.scalars(
            select(ObligationReminder).where(
                ObligationReminder.sent_at.is_(None),
                ObligationReminder.remind_at <= today,
            )
        ).all()
        sent = 0
        for reminder in due_reminders:
            ob = db.get(Obligation, reminder.obligation_id)
            if ob is None or ob.deleted_at is not None or ob.status in {
                ObligationStatus.COMPLETED,
                ObligationStatus.CANCELLED,
            }:
                continue
            owner = db.get(User, ob.owner_user_id) if ob.owner_user_id else None
            if owner is not None:
                await resend_client.send_email(
                    to=owner.email,
                    subject=f"Obligation due: {ob.obligation_type or 'contract obligation'}",
                    html=f"<p>Reminder: <b>{ob.description[:300]}</b> (due {ob.due_date}).</p>",
                )
            reminder.sent_at = today
            sent += 1
        db.commit()
        return {"reminders_sent": sent, "marked_overdue": overdue, "marked_due_soon": due_soon}
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
def mark_overdue_approvals() -> dict:
    """Daily: flag pending approval requests whose ``due_at`` has passed.

    ApprovalStatus has no OVERDUE member, so rather than invent an enum value
    (which would need a migration) we tag metadata_json["overdue"] and leave
    status=PENDING. Idempotent: already-flagged rows are skipped. Safe no-op
    when nothing is overdue.
    """
    from app.approvals.models import ApprovalRequest
    from app.core.enums import ApprovalStatus

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
        flagged = 0
        for req in overdue_requests:
            meta = req.metadata_json or {}
            if meta.get("overdue"):
                continue
            req.metadata_json = {**meta, "overdue": {"at": now.isoformat()}}
            flagged += 1
        db.commit()
        return {"marked_overdue": flagged}
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
