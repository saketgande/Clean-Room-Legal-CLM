from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalRequest
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import ContractVersion
from app.contracts.access import accessible_contract_filter, user_can_access_contract
from app.contracts.models import Contract, ContractStageHistory
from app.core.audit import write_audit_log, write_timeline_event
from app.core.enums import (
    ApprovalStatus,
    ContractLifecycleStage,
    ObligationStatus,
    RenewalDecision,
    SignatureStatus,
)
from app.core.models import ResourceTimelineEvent
from app.obligations.models import Obligation
from app.renewals.models import RenewalEvent
from app.signatures.models import SignatureRequest


def get_contract_for_user(db: Session, *, contract_id: str, user: User) -> Contract:
    contract = db.get(Contract, contract_id)
    if (
        contract is None
        or contract.org_id != user.org_id
        or contract.deleted_at is not None
        or not user_can_access_contract(db, contract=contract, user=user)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    return contract


def list_contracts_for_user(db: Session, *, user: User) -> list[Contract]:
    return db.scalars(
        select(Contract)
        .where(
            Contract.org_id == user.org_id,
            Contract.deleted_at.is_(None),
            accessible_contract_filter(user),
        )
        .order_by(Contract.updated_at.desc())
    ).all()


def update_contract_metadata(
    db: Session,
    *,
    contract: Contract,
    user: User,
    updates: dict,
    request_id: str | None = None,
) -> Contract:
    before = {
        "title": contract.title,
        "contract_type": contract.contract_type,
        "counterparty_name": contract.counterparty_name,
        "jurisdiction": contract.jurisdiction,
        "risk_level": contract.risk_level,
        "value_amount": contract.value_amount,
        "currency": contract.currency,
        "effective_date": contract.effective_date,
        "expiration_date": contract.expiration_date,
        "metadata_json": contract.metadata_json,
    }
    for key, value in updates.items():
        if value is not None and hasattr(contract, key):
            setattr(contract, key, value)
    contract.updated_by_user_id = user.id
    write_audit_log(
        db,
        action="contract.metadata_updated",
        resource_type="contract",
        resource_id=contract.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
        before=before,
        after=updates,
    )
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.metadata_updated",
        title="Contract metadata updated",
        actor_user_id=user.id,
        request_id=request_id,
        details={"updated_fields": sorted(updates.keys())},
    )
    db.commit()
    db.refresh(contract)
    return contract


def contract_hub_summary(db: Session, *, user: User) -> dict:
    accessible_contract_ids = (
        select(Contract.id)
        .where(
            Contract.org_id == user.org_id,
            Contract.deleted_at.is_(None),
            accessible_contract_filter(user),
        )
        .subquery()
    )
    contract_filter = Contract.id.in_(select(accessible_contract_ids.c.id))
    by_stage = dict(
        db.execute(
            select(Contract.lifecycle_stage, func.count(Contract.id))
            .where(Contract.org_id == user.org_id, Contract.deleted_at.is_(None), contract_filter)
            .group_by(Contract.lifecycle_stage)
        ).all()
    )
    by_risk = dict(
        db.execute(
            select(Contract.risk_level, func.count(Contract.id))
            .where(Contract.org_id == user.org_id, Contract.deleted_at.is_(None), contract_filter)
            .group_by(Contract.risk_level)
        ).all()
    )
    total_versions = db.scalar(
        select(func.count(ContractVersion.id)).where(
            ContractVersion.org_id == user.org_id,
            ContractVersion.contract_id.in_(select(accessible_contract_ids.c.id)),
        )
    )
    today = datetime.now(UTC).date()
    renewal_horizon = today + timedelta(days=90)
    pending_approvals = db.scalar(
        select(func.count(ApprovalRequest.id)).where(
            ApprovalRequest.org_id == user.org_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
            ApprovalRequest.contract_id.in_(select(accessible_contract_ids.c.id)),
        )
    ) or 0
    pending_signatures = db.scalar(
        select(func.count(SignatureRequest.id)).where(
            SignatureRequest.org_id == user.org_id,
            SignatureRequest.status.in_([SignatureStatus.DRAFT, SignatureStatus.SENT, SignatureStatus.DELIVERED]),
            SignatureRequest.contract_id.in_(select(accessible_contract_ids.c.id)),
        )
    ) or 0
    upcoming_renewals = db.scalar(
        select(func.count(RenewalEvent.id)).where(
            RenewalEvent.org_id == user.org_id,
            RenewalEvent.decision == RenewalDecision.UNDECIDED,
            RenewalEvent.contract_id.in_(select(accessible_contract_ids.c.id)),
            RenewalEvent.notice_date.is_not(None),
            RenewalEvent.notice_date >= today,
            RenewalEvent.notice_date <= renewal_horizon,
        )
    ) or 0
    overdue_obligations = db.scalar(
        select(func.count(Obligation.id)).where(
            Obligation.org_id == user.org_id,
            Obligation.deleted_at.is_(None),
            Obligation.status.in_([ObligationStatus.OPEN, ObligationStatus.DUE_SOON, ObligationStatus.OVERDUE]),
            Obligation.contract_id.in_(select(accessible_contract_ids.c.id)),
            Obligation.due_date.is_not(None),
            Obligation.due_date < today,
        )
    ) or 0
    recent_activity = [
        {
            "id": row.id,
            "resource_id": row.resource_id,
            "event_type": row.event_type,
            "title": row.title,
            "details": row.details,
            "created_at": row.created_at,
        }
        for row in db.scalars(
            select(ResourceTimelineEvent)
            .where(
                ResourceTimelineEvent.org_id == user.org_id,
                ResourceTimelineEvent.resource_type == "contract",
                ResourceTimelineEvent.resource_id.in_(select(accessible_contract_ids.c.id)),
            )
            .order_by(ResourceTimelineEvent.created_at.desc())
            .limit(10)
        ).all()
    ]
    top_deviated_clauses = [
        {"clause_type": clause_type or "unknown", "count": count}
        for clause_type, count in db.execute(
            select(ClauseExtraction.clause_type, func.count(ClauseExtraction.id))
            .where(
                ClauseExtraction.org_id == user.org_id,
                ClauseExtraction.contract_id.in_(select(accessible_contract_ids.c.id)),
                ClauseExtraction.risk_level.in_(["high", "critical"]),
            )
            .group_by(ClauseExtraction.clause_type)
            .order_by(desc(func.count(ClauseExtraction.id)))
            .limit(5)
        ).all()
    ]
    counterparty_friction = [
        {"counterparty_name": counterparty or "unknown", "count": count}
        for counterparty, count in db.execute(
            select(Contract.counterparty_name, func.count(Contract.id))
            .where(
                Contract.org_id == user.org_id,
                Contract.deleted_at.is_(None),
                contract_filter,
                Contract.lifecycle_stage == ContractLifecycleStage.COUNTERPARTY_REVIEW,
            )
            .group_by(Contract.counterparty_name)
            .order_by(desc(func.count(Contract.id)))
            .limit(5)
        ).all()
    ]
    return {
        "contracts_by_stage": by_stage,
        "contracts_by_risk": by_risk,
        "total_contract_versions": total_versions or 0,
        "widgets": {
            "pending_approvals": pending_approvals,
            "pending_signatures": pending_signatures,
            "upcoming_renewals": upcoming_renewals,
            "overdue_obligations": overdue_obligations,
            "top_deviated_clauses": top_deviated_clauses,
            "average_cycle_time_days": None,
            "counterparty_friction": counterparty_friction,
            "recent_activity": recent_activity,
        },
    }


def list_contract_stage_history(db: Session, *, contract: Contract) -> list[ContractStageHistory]:
    return db.scalars(
        select(ContractStageHistory)
        .where(ContractStageHistory.org_id == contract.org_id, ContractStageHistory.contract_id == contract.id)
        .order_by(ContractStageHistory.changed_at.asc())
    ).all()


def list_contract_activity(db: Session, *, contract: Contract, limit: int = 100) -> list[ResourceTimelineEvent]:
    return db.scalars(
        select(ResourceTimelineEvent)
        .where(
            ResourceTimelineEvent.org_id == contract.org_id,
            ResourceTimelineEvent.resource_type == "contract",
            ResourceTimelineEvent.resource_id == contract.id,
        )
        .order_by(ResourceTimelineEvent.created_at.desc())
        .limit(min(limit, 200))
    ).all()
