from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractVersion
from app.contracts.models import Contract, ContractStageHistory
from app.core.audit import write_audit_log, write_timeline_event
from app.core.enums import ContractLifecycleStage, ContractVersionSource


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ContractLifecycleStage.INTAKE: {
        ContractLifecycleStage.DRAFTING,
        ContractLifecycleStage.AI_REVIEW,
        ContractLifecycleStage.ACTIVE,
    },
    ContractLifecycleStage.DRAFTING: {
        ContractLifecycleStage.AI_REVIEW,
        ContractLifecycleStage.INTERNAL_REVIEW,
    },
    ContractLifecycleStage.AI_REVIEW: {
        ContractLifecycleStage.INTERNAL_REVIEW,
        ContractLifecycleStage.COUNTERPARTY_REVIEW,
    },
    ContractLifecycleStage.INTERNAL_REVIEW: {
        ContractLifecycleStage.COUNTERPARTY_REVIEW,
        ContractLifecycleStage.APPROVAL_PENDING,
    },
    ContractLifecycleStage.COUNTERPARTY_REVIEW: {
        ContractLifecycleStage.AI_REVIEW,
        ContractLifecycleStage.INTERNAL_REVIEW,
        ContractLifecycleStage.APPROVAL_PENDING,
    },
    ContractLifecycleStage.APPROVAL_PENDING: {
        ContractLifecycleStage.APPROVED,
        ContractLifecycleStage.INTERNAL_REVIEW,
        ContractLifecycleStage.COUNTERPARTY_REVIEW,
    },
    ContractLifecycleStage.APPROVED: {
        ContractLifecycleStage.SIGNATURE_PENDING,
        ContractLifecycleStage.ACTIVE,
    },
    ContractLifecycleStage.SIGNATURE_PENDING: {
        ContractLifecycleStage.ACTIVE,
        ContractLifecycleStage.APPROVED,
    },
    ContractLifecycleStage.ACTIVE: {
        ContractLifecycleStage.RENEWAL_DUE,
        ContractLifecycleStage.CLOSED,
        ContractLifecycleStage.ARCHIVED,
    },
    ContractLifecycleStage.RENEWAL_DUE: {
        ContractLifecycleStage.ACTIVE,
        ContractLifecycleStage.CLOSED,
    },
    ContractLifecycleStage.CLOSED: {ContractLifecycleStage.ARCHIVED},
}


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
    return contract
