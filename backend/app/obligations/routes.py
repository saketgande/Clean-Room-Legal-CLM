from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.obligations.models import Obligation

router = APIRouter(prefix="/obligations", tags=["obligations"])


@router.get("")
def list_obligations(
    contract_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:read")),
):
    query = select(Obligation).where(
        Obligation.org_id == current_user.org_id,
        Obligation.deleted_at.is_(None),
    )
    if contract_id:
        query = query.where(Obligation.contract_id == contract_id)
    return db.scalars(query.order_by(Obligation.due_date.asc())).all()
