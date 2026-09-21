import logging
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractEdit, ContractVersion
from app.contracts.models import Contract, ContractStageHistory
from app.core.audit import write_audit_log, write_timeline_event
from app.core.enums import ContractLifecycleStage, ContractVersionSource

logger = logging.getLogger(__name__)

# Mirrors service.py's _PRE_APPROVAL_STAGES: the stages before the document is
# considered final. Duplicated locally (not imported) to avoid a cross-module
# coupling for three enum values.
_PRE_APPROVAL_STAGES = {
    ContractLifecycleStage.INTAKE,
    ContractLifecycleStage.DRAFTING,
    ContractLifecycleStage.REVIEW,
}

_DISPATCH_DELAY_SECONDS = 4

# Lean 7-stage flow. Most forward hops are auto-advanced by events (approval
# completing → SIGNATURE, signing completing → ACTIVE), so users rarely drive
# these by hand. Backward hops (e.g. APPROVAL/SIGNATURE → REVIEW) cover
# rejections and rework. CLOSED is terminal (use the `archived` flag to retire).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ContractLifecycleStage.INTAKE: {
        ContractLifecycleStage.DRAFTING,
        ContractLifecycleStage.REVIEW,
    },
    ContractLifecycleStage.DRAFTING: {
        ContractLifecycleStage.REVIEW,
    },
    ContractLifecycleStage.REVIEW: {
        ContractLifecycleStage.DRAFTING,
        ContractLifecycleStage.APPROVAL,
        # Allow simple contracts to skip approval and go straight to signing.
        ContractLifecycleStage.SIGNATURE,
    },
    ContractLifecycleStage.APPROVAL: {
        ContractLifecycleStage.SIGNATURE,   # approval chain completed
        ContractLifecycleStage.REVIEW,      # rejected → back to review
    },
    ContractLifecycleStage.SIGNATURE: {
        ContractLifecycleStage.ACTIVE,      # signing completed
        ContractLifecycleStage.REVIEW,      # pulled back for changes
    },
    ContractLifecycleStage.ACTIVE: {
        ContractLifecycleStage.CLOSED,
    },
    ContractLifecycleStage.CLOSED: set(),
}


def parse_stage_slas(raw: str) -> dict[str, int]:
    """Parse the "stage:days,stage:days" SLA setting; malformed entries are
    skipped so a bad env value degrades to fewer SLAs, never a crash."""
    out: dict[str, int] = {}
    for part in (raw or "").split(","):
        if ":" not in part:
            continue
        stage, _, days = part.strip().partition(":")
        try:
            out[stage.strip()] = int(days)
        except ValueError:
            continue
    return out


def allowed_transitions_for(stage: str) -> list[str]:
    return sorted(ALLOWED_TRANSITIONS.get(stage, set()))


class ContractLifecycleService:
    """Lifecycle stage transitions and their stage-entry side effects.

    Part of the DI migration (see backend/DI_MIGRATION.md). Merges the former
    ``lifecycle.py`` (the state machine) and ``stage_triggers.py`` (best-effort
    work started on arriving in a stage) into one class, since the triggers
    were only ever called from inside a transition. Pure helpers that don't
    touch ``db`` (``allowed_transitions_for``, ``parse_stage_slas``,
    ``ALLOWED_TRANSITIONS``) stay module-level.
    """

    def __init__(self, db: Session):
        self.db = db

    def transition_contract_stage(
        self,
        *,
        contract: Contract,
        to_stage: str,
        actor_user_id: str,
        reason: str | None = None,
        override: bool = False,
        override_authorized: bool = False,
        signed_confirmation: bool = False,
        request_id: str | None = None,
    ) -> Contract:
        db = self.db
        # Row-lock the contract for the duration of this transition — the single
        # most-contended state machine in the app (11+ call sites: approvals,
        # signatures, flows, renewals). Without it, two concurrent transitions can
        # both read the same from_stage and one silently overwrites the other,
        # while both still commit audit/history rows that no longer match reality.
        contract = db.scalar(select(Contract).where(Contract.id == contract.id).with_for_update())
        from_stage = contract.lifecycle_stage
        allowed = ALLOWED_TRANSITIONS.get(from_stage, set())
        if to_stage not in allowed:
            if not override:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Invalid lifecycle transition from {from_stage} to {to_stage}",
                )
            if not override_authorized:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Lifecycle override requires the contract:lifecycle_override permission",
                )
        authorized_override = override and override_authorized

        # The ONE real gate on leaving Review: every proposed redline must be
        # accepted or rejected first. The workspace already tells the user this
        # explicitly ("N open redlines — override needs a reason"); this makes
        # that copy true instead of decorative. Overriding needs both the
        # contract:lifecycle_override permission AND a stated reason — the
        # reason is what actually lands on the stage-history audit row.
        if from_stage in _PRE_APPROVAL_STAGES and to_stage not in _PRE_APPROVAL_STAGES:
            pending_redlines = (
                db.scalar(
                    select(func.count(ContractEdit.id)).where(
                        ContractEdit.contract_id == contract.id,
                        ContractEdit.status == "proposed",
                    )
                )
                or 0
            )
            if pending_redlines > 0:
                if not authorized_override:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"{pending_redlines} open redline(s) must be accepted or rejected before "
                        "advancing past Review, or override with the contract:lifecycle_override "
                        "permission.",
                    )
                if not reason or not reason.strip():
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        "Overriding open redlines requires a reason.",
                    )

        if to_stage == ContractLifecycleStage.ACTIVE and signed_confirmation and not authorized_override:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Signed confirmation activation requires contract:lifecycle_override",
            )
        if to_stage == ContractLifecycleStage.ACTIVE and not authorized_override:
            has_signed_version = (
                db.scalar(
                    select(ContractVersion.id)
                    .where(
                        ContractVersion.contract_id == contract.id,
                        ContractVersion.source == ContractVersionSource.SIGNED,
                    )
                    .limit(1)
                )
                is not None
            )
            if not has_signed_version and not signed_confirmation:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Activating a contract requires a signed version or an explicit signed confirmation",
                )
        contract.lifecycle_stage = to_stage
        contract.updated_by_user_id = actor_user_id
        db.add(
            ContractStageHistory(
                org_id=contract.org_id,
                contract_id=contract.id,
                from_stage=from_stage,
                to_stage=to_stage,
                reason=reason,
                changed_by_user_id=actor_user_id,
                changed_at=datetime.now(UTC),
                override_used=override,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
        )
        write_audit_log(
            db,
            action="contract.lifecycle_changed",
            resource_type="contract",
            resource_id=contract.id,
            org_id=contract.org_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            before={"lifecycle_stage": from_stage},
            after={"lifecycle_stage": to_stage},
            metadata={
                "reason": reason,
                "override": override,
                "override_authorized": override_authorized,
                "signed_confirmation": signed_confirmation,
            },
        )
        write_timeline_event(
            db,
            org_id=contract.org_id,
            resource_type="contract",
            resource_id=contract.id,
            event_type="contract.lifecycle_changed",
            title=f"Stage changed to {to_stage}",
            actor_user_id=actor_user_id,
            request_id=request_id,
            details={"from_stage": from_stage, "to_stage": to_stage, "reason": reason},
        )

        # Arriving in a stage starts the work: queue analysis on Review, prompt
        # signature on Signature, extract obligations/renewals on Active.
        # Best-effort — a trigger failure never blocks the transition.
        self._fire_stage_entry_triggers(
            contract=contract,
            from_stage=from_stage,
            to_stage=to_stage,
            actor_user_id=actor_user_id,
        )
        return contract

    # --- Stage-entry triggers (formerly stage_triggers.py) -------------------
    # Called for every successful transition, no matter who initiated it (user,
    # approval auto-advance, signature webhook, expiry sweep). Best-effort by
    # design — a trigger failure must never block or roll back a lifecycle
    # transition, so everything is wrapped and logged.
    #
    # Dispatch note: the caller owns the transaction (transition does not
    # commit), so Celery dispatch uses a short countdown to let the caller's
    # commit land before the worker reads the JobRun row. Idempotency keys
    # match the upload / signature paths, so a re-fired trigger reuses the
    # existing job instead of duplicating work.

    def _fire_stage_entry_triggers(
        self,
        *,
        contract: Contract,
        from_stage: str,
        to_stage: str,
        actor_user_id: str | None,
    ) -> None:
        db = self.db
        try:
            if to_stage == ContractLifecycleStage.REVIEW:
                self._on_enter_review(contract=contract, actor_user_id=actor_user_id)
            elif to_stage == ContractLifecycleStage.SIGNATURE:
                self._on_enter_signature(contract=contract, actor_user_id=actor_user_id)
            elif to_stage == ContractLifecycleStage.ACTIVE:
                self._on_enter_active(contract=contract, actor_user_id=actor_user_id)
            # Auto-resume any workflow run waiting on this contract's stage.
            from app.workflows.service import advance_flow_for_contract
            advance_flow_for_contract(db, contract=contract, actor_user_id=actor_user_id)
        except Exception:
            logger.warning(
                "stage-entry trigger failed (%s -> %s, contract %s)",
                from_stage,
                to_stage,
                contract.id,
                exc_info=True,
            )

    def _authoritative_artifacts(self, contract: Contract):
        from app.contract_files.models import ContractTextSnapshot, ContractVersion

        version = (
            self.db.get(ContractVersion, contract.current_authoritative_version_id)
            if contract.current_authoritative_version_id
            else None
        )
        snapshot = (
            self.db.get(ContractTextSnapshot, version.text_snapshot_id)
            if version is not None and version.text_snapshot_id
            else None
        )
        return version, snapshot

    def _queue_and_schedule(
        self, *, contract: Contract, actor_user_id: str | None, job_types: tuple[str, ...]
    ) -> list[str]:
        """Create (idempotent) jobs and schedule their dispatch after the caller's
        commit. Returns the job types actually scheduled."""
        from app.jobs.models import JobRun  # noqa: F401  (model import for typing clarity)
        from app.jobs.service import create_job
        from app.jobs.tasks import run_ai_job

        db = self.db
        version, snapshot = self._authoritative_artifacts(contract)
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
        self,
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
        self.db.add(
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

    def _on_enter_review(self, *, contract: Contract, actor_user_id: str | None) -> None:
        from app.contract_brain.models import ClauseExtraction

        already_analyzed = (
            self.db.scalar(
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
            scheduled = self._queue_and_schedule(
                contract=contract,
                actor_user_id=actor_user_id,
                job_types=("metadata_extraction", "clause_extraction", "embeddings"),
            )
        self._notify_owner(
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

    def _on_enter_signature(self, *, contract: Contract, actor_user_id: str | None) -> None:
        counterparty = contract.counterparty_name or "the counterparty"
        self._notify_owner(
            contract=contract,
            actor_user_id=actor_user_id,
            event_type="lifecycle.entered_signature",
            subject=f"Ready to sign: {contract.title}",
            body=f'"{contract.title}" cleared approval. Send it to {counterparty} for signature.',
        )

    def _on_enter_active(self, *, contract: Contract, actor_user_id: str | None) -> None:
        from app.obligations.models import Obligation
        from app.renewals.models import RenewalEvent

        job_types: list[str] = []
        has_obligations = (
            self.db.scalar(select(Obligation.id).where(Obligation.contract_id == contract.id).limit(1))
            is not None
        )
        if not has_obligations:
            job_types.append("obligation_extraction")
        has_renewal = (
            self.db.scalar(select(RenewalEvent.id).where(RenewalEvent.contract_id == contract.id).limit(1))
            is not None
        )
        if not has_renewal:
            job_types.append("renewal_extraction")
        scheduled = (
            self._queue_and_schedule(
                contract=contract, actor_user_id=actor_user_id, job_types=tuple(job_types)
            )
            if job_types
            else []
        )
        self._notify_owner(
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


# DI-MIGRATION: temporary wrapper — remove once all callers use
# get_contract_lifecycle_service(). Tracked in backend/DI_MIGRATION.md
def transition_contract_stage(
    db: Session,
    *,
    contract: Contract,
    to_stage: str,
    actor_user_id: str,
    reason: str | None = None,
    override: bool = False,
    override_authorized: bool = False,
    signed_confirmation: bool = False,
    request_id: str | None = None,
) -> Contract:
    return ContractLifecycleService(db).transition_contract_stage(
        contract=contract,
        to_stage=to_stage,
        actor_user_id=actor_user_id,
        reason=reason,
        override=override,
        override_authorized=override_authorized,
        signed_confirmation=signed_confirmation,
        request_id=request_id,
    )
