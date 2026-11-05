from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.service import get_contract_for_user
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.obligations.dates import FILTERS, OPEN_STATUSES, matches
from app.obligations.dependencies import get_obligations_service
from app.obligations.models import Obligation
from app.obligations.service import ObligationsService, serialize_obligation, visible_obligations

router = APIRouter(prefix="/obligations", tags=["obligations"])


# Column widths (obligations/models.py); longer text was a 500 from the database, not a message.
_DATE_RANGE = (date(1990, 1, 1), date(2100, 12, 31))


class ObligationUpdate(BaseModel):
    owner_user_id: str | None = Field(default=None, max_length=36)
    responsible_party: str | None = Field(default=None, max_length=255)
    obligation_type: str | None = Field(default=None, max_length=160)
    status: str | None = Field(default=None, pattern="^(open|due_soon|overdue|completed|cancelled)$")
    due_date: date | None = None
    recurrence: str | None = Field(default=None, max_length=120)
    # The day the triggering event happened (invoice received, notice received...):
    # code counts the clause's period from it and shows the formula.
    anchor_date: date | None = None
    # A person accepts the date shown (or, after an anchor moved, its re-count).
    confirm: bool | None = None
    # The row's `updated_at` as the editor last saw it. When it is no longer the row's, someone else
    # changed it meanwhile and the save is refused (409) instead of silently undoing their change.
    expected_updated_at: str | None = None

    @field_validator("due_date", "anchor_date")
    @classmethod
    def _plausible_date(cls, value):
        # 0001-01-01 overflowed the reminder date (due - 7 days) into a 500.
        if value is not None and not _DATE_RANGE[0] <= value <= _DATE_RANGE[1]:
            raise ValueError("Pick a date between 1990 and 2100")
        return value

    @model_validator(mode="after")
    def _status_is_never_cleared(self):
        # status is NOT NULL: an explicit null reached the database as an IntegrityError.
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be empty")
        return self


def _get_obligation(db: Session, *, obligation_id: str, current_user: User, edit: bool = False) -> Obligation:
    ob = db.get(Obligation, obligation_id)
    if ob is None or ob.org_id != current_user.org_id or ob.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Obligation not found")
    get_contract_for_user(db, contract_id=ob.contract_id, user=current_user, edit=edit)
    return ob


@router.get("")
def list_obligations(
    contract_id: str | None = None,
    status_filter: str | None = None,
    due_within_days: int | None = None,
    # A plain default, not Query(): the page and the tests call this function directly. One of dated | waiting_event |
    # needs_person | no_fixed_date | not_read | needs_date (needs_person + not_read); open obligations only.
    date_state: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:read")),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    if date_state is not None and date_state not in FILTERS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"date_state must be one of {', '.join(FILTERS)}"
        )
    # The tracking list: a closed or archived contract is "no longer tracked", so its duties stop showing as
    # overdue here. Asking for that contract by id still lists them, as history. The same query feeds
    # GET /obligations/summary (visible_obligations applies accessible_contract_filter(current_user)).
    query = visible_obligations(current_user, live_only=not contract_id)
    if contract_id:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
        query = query.where(Obligation.contract_id == contract_id)
    if status_filter:
        query = query.where(Obligation.status == status_filter)
    if due_within_days is not None:
        from datetime import timedelta as _timedelta
        _today = utcnow().date()
        query = query.where(
            Obligation.due_date.isnot(None),
            Obligation.due_date >= _today,
            Obligation.due_date <= _today + _timedelta(days=due_within_days),
        )
    # Earliest due first; a stable tail (contract, created, id) keeps a contract's rows together and paging steady.
    query = query.order_by(Obligation.due_date.asc().nulls_last(), Obligation.contract_id, Obligation.created_at,
                           Obligation.id)
    if date_state is None:
        rows = db.execute(query.offset(offset).limit(limit)).all()
    else:
        # Worked out on read, so filtered here before paging. ponytail: Python over every visible row; see
        # ObligationsService.summary for the ceiling.
        everything = db.execute(query).all()
        rows = [r for r in everything if r[0].status in OPEN_STATUSES and matches(r[0], date_state)][
            offset : offset + limit
        ]
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


@router.get("/summary")
def obligation_summary(
    current_user=Depends(require_permission("obligation:read")),
    service: ObligationsService = Depends(get_obligations_service),
):
    """The tiles: obligations per status and per date state, over exactly what the list shows. Declared before
    '/{obligation_id}' so 'summary' is not read as an id."""
    return service.summary(user=current_user)


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
    ob = _get_obligation(db, obligation_id=obligation_id, current_user=current_user, edit=True)
    return service.update_obligation(ob=ob, payload=payload, current_user=current_user)


@router.post("/{obligation_id}/complete")
def complete_obligation(
    obligation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
    service: ObligationsService = Depends(get_obligations_service),
):
    ob = _get_obligation(db, obligation_id=obligation_id, current_user=current_user, edit=True)
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
