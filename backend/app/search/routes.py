from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.contracts.models import Contract
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/contracts")
def search_contracts(
    q: str,
    stage: str | None = None,
    risk_level: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    query = select(Contract).where(
        Contract.org_id == current_user.org_id,
        Contract.deleted_at.is_(None),
        or_(
            Contract.title.ilike(f"%{q}%"),
            Contract.counterparty_name.ilike(f"%{q}%"),
            Contract.contract_type.ilike(f"%{q}%"),
        ),
    )
    if stage:
        query = query.where(Contract.lifecycle_stage == stage)
    if risk_level:
        query = query.where(Contract.risk_level == risk_level)
    return db.scalars(query.limit(50)).all()
