from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.renewals.models import RenewalEvent

router = APIRouter(prefix="/renewals", tags=["renewals"])


@router.get("")
def list_renewals(
    contract_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = select(RenewalEvent).where(RenewalEvent.org_id == current_user.org_id)
    if contract_id:
        query = query.where(RenewalEvent.contract_id == contract_id)
    return db.scalars(query.order_by(RenewalEvent.notice_date.asc())).all()
