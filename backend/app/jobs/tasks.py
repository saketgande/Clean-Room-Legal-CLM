import asyncio
import logging
import traceback
from datetime import timedelta

import httpx
from sqlalchemy import delete, select

from app.ai.controller import ai_controller
from app.ai.embeddings import generate_embeddings_for_snapshot
from app.ai.models import AISkillRun
from app.ai.schemas import TabularCellOutput
from app.contract_brain.ingestion import ingest_contract_brain
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
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
        if job.status in (JobStatus.CANCELLED, JobStatus.RUNNING, JobStatus.SUCCEEDED):
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
                cell.error_message = _humanize_cell_error(cell_exc)
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
        failed = 0
        for reminder in due_reminders:
            ob = db.get(Obligation, reminder.obligation_id)
            if ob is None or ob.deleted_at is not None or ob.status in {
                ObligationStatus.COMPLETED,
                ObligationStatus.CANCELLED,
            }:
                continue
            owner = db.get(User, ob.owner_user_id) if ob.owner_user_id else None
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
                    failed += 1
                    continue
            reminder.sent_at = today
            sent += 1
        db.commit()
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
        events = db.scalars(select(RenewalEvent)).all()
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
                    title = ir.title

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
