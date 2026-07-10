"""Stage-entry triggers: arriving in a stage STARTS the work.

Called from transition_contract_stage for every successful transition, no
matter who initiated it (user, approval auto-advance, signature webhook,
expiry sweep). Best-effort by design — a trigger failure must never block or
roll back a lifecycle transition, so everything is wrapped and logged.

Dispatch note: the caller owns the transaction (transition does not commit),
so Celery dispatch uses a short countdown to let the caller's commit land
before the worker reads the JobRun row. Idempotency keys match the upload /
signature paths, so a re-fired trigger reuses the existing job instead of
duplicating work.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.models import Contract
from app.core.enums import ContractLifecycleStage

logger = logging.getLogger(__name__)

_DISPATCH_DELAY_SECONDS = 4


def fire_stage_entry_triggers(
    db: Session,
    *,
    contract: Contract,
    from_stage: str,
    to_stage: str,
    actor_user_id: str | None,
) -> None:
    try:
        if to_stage == ContractLifecycleStage.REVIEW:
            _on_enter_review(db, contract=contract, actor_user_id=actor_user_id)
        elif to_stage == ContractLifecycleStage.SIGNATURE:
            _on_enter_signature(db, contract=contract, actor_user_id=actor_user_id)
        elif to_stage == ContractLifecycleStage.ACTIVE:
            _on_enter_active(db, contract=contract, actor_user_id=actor_user_id)
    except Exception:
        logger.warning(
            "stage-entry trigger failed (%s -> %s, contract %s)",
            from_stage,
            to_stage,
            contract.id,
            exc_info=True,
        )


def _authoritative_artifacts(db: Session, contract: Contract):
    from app.contract_files.models import ContractTextSnapshot, ContractVersion

    version = (
        db.get(ContractVersion, contract.current_authoritative_version_id)
        if contract.current_authoritative_version_id
        else None
    )
    snapshot = (
        db.get(ContractTextSnapshot, version.text_snapshot_id)
        if version is not None and version.text_snapshot_id
        else None
    )
    return version, snapshot


def _queue_and_schedule(db: Session, *, contract: Contract, actor_user_id: str | None, job_types: tuple[str, ...]) -> list[str]:
    """Create (idempotent) jobs and schedule their dispatch after the caller's
    commit. Returns the job types actually scheduled."""
    from app.jobs.models import JobRun  # noqa: F401  (model import for typing clarity)
    from app.jobs.service import create_job
    from app.jobs.tasks import run_ai_job

    version, snapshot = _authoritative_artifacts(db, contract)
    if version is None or snapshot is None:
        return []
    scheduled: list[str] = []
    for job_type in job_types:
        job = create_job(
            db,
            org_id=contract.org_id,
            job_type=job_type,
            resource_type="contract",
            resource_id=contract.id,
            created_by_user_id=actor_user_id,
            idempotency_key=f"{job_type}:{version.id}:{snapshot.id}",
            metadata={
                "contract_version_id": version.id,
                "text_snapshot_id": snapshot.id,
            },
        )
        db.flush()
        if job.status in {"succeeded", "running"} or job.celery_task_id:
            # Already done, in flight, or already dispatched by the upload path
            # (which also queues these jobs with the same idempotency key). The
            # celery_task_id guard prevents a second dispatch of the same job.
            continue
        run_ai_job.apply_async(args=[job.id], countdown=_DISPATCH_DELAY_SECONDS)
        scheduled.append(job_type)
    return scheduled


def _notify_owner(
    db: Session,
    *,
    contract: Contract,
    actor_user_id: str | None,
    event_type: str,
    subject: str,
    body: str,
) -> None:
    """In-app notification to the contract owner — skipped when the owner did
    the action themselves (no self-noise)."""
    from app.notifications.models import Notification

    if not contract.owner_user_id or contract.owner_user_id == actor_user_id:
        return
    db.add(
        Notification(
            org_id=contract.org_id,
            user_id=contract.owner_user_id,
            channel="in_app",
            event_type=event_type,
            subject=subject,
            body=body,
            status="sent",
        )
    )


def _on_enter_review(db: Session, *, contract: Contract, actor_user_id: str | None) -> None:
    from app.contract_brain.models import ClauseExtraction

    already_analyzed = (
        db.scalar(
            select(ClauseExtraction.id)
            .where(
                ClauseExtraction.contract_id == contract.id,
                ClauseExtraction.is_stale.is_(False),
            )
            .limit(1)
        )
        is not None
    )
    scheduled: list[str] = []
    if not already_analyzed:
        scheduled = _queue_and_schedule(
            db,
            contract=contract,
            actor_user_id=actor_user_id,
            job_types=("metadata_extraction", "clause_extraction", "embeddings"),
        )
    _notify_owner(
        db,
        contract=contract,
        actor_user_id=actor_user_id,
        event_type="lifecycle.entered_review",
        subject=f"In review: {contract.title}",
        body=(
            f'"{contract.title}" entered Review. '
            + (
                "AI analysis was queued automatically — findings will appear shortly."
                if scheduled
                else "AI analysis is ready — work the flagged issues and redlines."
            )
        ),
    )


def _on_enter_signature(db: Session, *, contract: Contract, actor_user_id: str | None) -> None:
    counterparty = contract.counterparty_name or "the counterparty"
    _notify_owner(
        db,
        contract=contract,
        actor_user_id=actor_user_id,
        event_type="lifecycle.entered_signature",
        subject=f"Ready to sign: {contract.title}",
        body=f'"{contract.title}" cleared approval. Send it to {counterparty} for signature.',
    )


def _on_enter_active(db: Session, *, contract: Contract, actor_user_id: str | None) -> None:
    from app.obligations.models import Obligation
    from app.renewals.models import RenewalEvent

    job_types: list[str] = []
    has_obligations = (
        db.scalar(select(Obligation.id).where(Obligation.contract_id == contract.id).limit(1))
        is not None
    )
    if not has_obligations:
        job_types.append("obligation_extraction")
    has_renewal = (
        db.scalar(select(RenewalEvent.id).where(RenewalEvent.contract_id == contract.id).limit(1))
        is not None
    )
    if not has_renewal:
        job_types.append("renewal_extraction")
    scheduled = (
        _queue_and_schedule(
            db, contract=contract, actor_user_id=actor_user_id, job_types=tuple(job_types)
        )
        if job_types
        else []
    )
    _notify_owner(
        db,
        contract=contract,
        actor_user_id=actor_user_id,
        event_type="lifecycle.entered_active",
        subject=f"Now active: {contract.title}",
        body=(
            f'"{contract.title}" is now active. '
            + (
                "Obligation and renewal extraction were queued automatically."
                if scheduled
                else "Obligations and renewals are being monitored."
            )
        ),
    )
