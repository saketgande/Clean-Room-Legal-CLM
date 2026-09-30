from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.obligations.dependencies import get_obligations_service
from app.obligations.models import Obligation
from app.obligations.service import ObligationsService, serialize_obligation

router = APIRouter(prefix="/obligations", tags=["obligations"])


class ObligationUpdate(BaseModel):
    owner_user_id: str | None = None
    responsible_party: str | None = None
    obligation_type: str | None = None
    status: str | None = Field(default=None, pattern="^(open|due_soon|overdue|completed|cancelled)$")
    due_date: date | None = None
    recurrence: str | None = None


def _get_obligation(db: Session, *, obligation_id: str, current_user: User) -> Obligation:
    ob = db.get(Obligation, obligation_id)
    if ob is None or ob.org_id != current_user.org_id or ob.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Obligation not found")
    get_contract_for_user(db, contract_id=ob.contract_id, user=current_user)
    return ob


@router.get("")
def list_obligations(
    contract_id: str | None = None,
    status_filter: str | None = None,
    due_within_days: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:read")),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = (
        select(Obligation, Contract.title, Contract.counterparty_name)
        .join(Contract, Contract.id == Obligation.contract_id)
        .where(
            Obligation.org_id == current_user.org_id,
            Obligation.deleted_at.is_(None),
            accessible_contract_filter(current_user),
        )
    )
    if contract_id:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
        query = query.where(Obligation.contract_id == contract_id)
    if status_filter:
        query = query.where(Obligation.status == status_filter)
    if due_within_days is not None:
        from datetime import date as _date
        from datetime import timedelta as _timedelta
        _today = _date.today()
        query = query.where(
            Obligation.due_date.isnot(None),
            Obligation.due_date >= _today,
            Obligation.due_date <= _today + _timedelta(days=due_within_days),
        )
    rows = db.execute(
        query.order_by(Obligation.due_date.asc().nulls_last()).offset(offset).limit(limit)
    ).all()
    owner_ids = {ob.owner_user_id for ob, _t, _cp in rows if ob.owner_user_id}
    owner_names = (
        {
            u.id: u.full_name
            for u in db.scalars(select(User).where(User.id.in_(owner_ids))).all()
        }
        if owner_ids
        else {}
    )
    return [
        serialize_obligation(
            ob,
            contract_title=title,
            counterparty_name=counterparty,
            owner_name=owner_names.get(ob.owner_user_id),
        )
        for ob, title, counterparty in rows
    ]


@router.get("/{obligation_id}")
def get_obligation(
    obligation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:read")),
    service: ObligationsService = Depends(get_obligations_service),
):
    ob = _get_obligation(db, obligation_id=obligation_id, current_user=current_user)
    return service.serialize_one(ob)


@router.patch("/{obligation_id}")
def update_obligation(
    obligation_id: str,
    payload: ObligationUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
    service: ObligationsService = Depends(get_obligations_service),
):
    ob = _get_obligation(db, obligation_id=obligation_id, current_user=current_user)
    return service.update_obligation(ob=ob, payload=payload, current_user=current_user)


@router.post("/{obligation_id}/complete")
def complete_obligation(
    obligation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
    service: ObligationsService = Depends(get_obligations_service),
):
    ob = _get_obligation(db, obligation_id=obligation_id, current_user=current_user)
    return service.complete_obligation(ob=ob, current_user=current_user)


@router.post("/extract", status_code=status.HTTP_202_ACCEPTED)
def trigger_obligation_extraction(
    contract_id: str,
    request: Request,
    current_user=Depends(require_permission("obligation:update")),
    service: ObligationsService = Depends(get_obligations_service),
):
    return service.trigger_extraction(contract_id=contract_id, current_user=current_user)


@router.post("/run-reminders")
async def run_obligation_reminders(
    request: Request,
    current_user=Depends(require_permission("admin_panel:access")),
    service: ObligationsService = Depends(get_obligations_service),
):
    return await service.run_reminders(
        current_user=current_user,
        request_id=getattr(request.state, "request_id", None),
    )
