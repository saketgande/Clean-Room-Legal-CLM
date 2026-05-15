from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_files.models import ContractVersion
from app.contracts.access import accessible_contract_filter, user_can_access_contract
from app.contracts.models import Contract
from app.core.audit import write_audit_log


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
    db.commit()
    db.refresh(contract)
    return contract


def contract_hub_summary(db: Session, *, user: User) -> dict:
    by_stage = dict(
        db.execute(
            select(Contract.lifecycle_stage, func.count(Contract.id))
            .where(Contract.org_id == user.org_id, Contract.deleted_at.is_(None))
            .group_by(Contract.lifecycle_stage)
        ).all()
    )
    by_risk = dict(
        db.execute(
            select(Contract.risk_level, func.count(Contract.id))
            .where(Contract.org_id == user.org_id, Contract.deleted_at.is_(None))
            .group_by(Contract.risk_level)
        ).all()
    )
    total_versions = db.scalar(
        select(func.count(ContractVersion.id)).where(ContractVersion.org_id == user.org_id)
    )
    return {
        "contracts_by_stage": by_stage,
        "contracts_by_risk": by_risk,
        "total_contract_versions": total_versions or 0,
        "widgets": {
            "pending_approvals": 0,
            "pending_signatures": 0,
            "upcoming_renewals": 0,
            "overdue_obligations": 0,
            "top_deviated_clauses": [],
            "average_cycle_time_days": None,
            "counterparty_friction": [],
            "recent_activity": [],
        },
    }
