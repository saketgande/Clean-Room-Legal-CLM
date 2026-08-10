from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractEdit, ContractVersion
from app.contracts.models import Contract, ContractStageHistory
from app.core.audit import write_audit_log, write_timeline_event
from app.core.enums import ContractLifecycleStage, ContractVersionSource

# Mirrors service.py's _PRE_APPROVAL_STAGES: the stages before the document is
# considered final. Duplicated locally (not imported) to avoid a cross-module
# coupling for three enum values.
_PRE_APPROVAL_STAGES = {
    ContractLifecycleStage.INTAKE,
    ContractLifecycleStage.DRAFTING,
    ContractLifecycleStage.REVIEW,
}


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
    from app.contracts.stage_triggers import fire_stage_entry_triggers

    fire_stage_entry_triggers(
        db,
        contract=contract,
        from_stage=from_stage,
        to_stage=to_stage,
        actor_user_id=actor_user_id,
    )
    return contract
