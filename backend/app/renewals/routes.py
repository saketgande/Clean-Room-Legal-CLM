from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.renewals.dependencies import get_renewals_service
from app.renewals.models import RenewalEvent
from app.renewals.service import RenewalsService, serialize_renewal

router = APIRouter(prefix="/renewals", tags=["renewals"])


class RenewalDecisionPayload(BaseModel):
    decision: str = Field(pattern="^(renew|terminate|renegotiate)$")
    note: str | None = None


class RenewalConfirmPayload(BaseModel):
    """Both optional: an empty body confirms the dates shown as calculated."""

    expiration_date: date | None = None
    notice_date: date | None = None
    # "Runs until terminated": settles a form end date the contract text doesn't have.
    no_end_date: bool = False


def _get_renewal(db: Session, *, renewal_id: str, current_user: User) -> RenewalEvent:
    row = db.get(RenewalEvent, renewal_id)
    if row is None or row.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Renewal event not found")
    get_contract_for_user(db, contract_id=row.contract_id, user=current_user)
    return row


@router.get("")
def list_renewals(
    contract_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = (
        select(RenewalEvent, Contract.title)
        .join(Contract, Contract.id == RenewalEvent.contract_id)
        .where(
            RenewalEvent.org_id == current_user.org_id,
            accessible_contract_filter(current_user),
        )
    )
    if contract_id:
        # By-contract lookup (contract detail panel) keeps dateless events — it
        # renders them as "Renewal tracked" with the extracted metadata.
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
        query = query.where(RenewalEvent.contract_id == contract_id)
    else:
        # Portfolio index: a renewal event with no dates has nothing to monitor,
        # so skip the empty shells — show real renewals or a clean empty state,
        # never a table of dashes.
        # A dateless event that carries a reason (evergreen, or "needs a person")
        # stays: hiding it is how the evergreen MSA used to vanish from the page.
        query = query.where(
            or_(
                RenewalEvent.expiration_date.is_not(None),
                RenewalEvent.notice_date.is_not(None),
                RenewalEvent.renewal_window_starts_at.is_not(None),
                RenewalEvent.metadata_json["dates"]["reason"].as_string().is_not(None),
            )
        )
    rows = db.execute(
        query.order_by(RenewalEvent.notice_date.asc()).offset(offset).limit(limit)
    ).all()
    return [serialize_renewal(row, contract_title=title) for row, title in rows]


@router.get("/{renewal_id}")
def get_renewal(
    renewal_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    row = _get_renewal(db, renewal_id=renewal_id, current_user=current_user)
    return serialize_renewal(row, contract_title=_title(db, row))


def _title(db: Session, row: RenewalEvent) -> str | None:
    contract = db.get(Contract, row.contract_id)
    return contract.title if contract else None


@router.get("/{renewal_id}/recommendation")
def renewal_recommendation(
    renewal_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
    service: RenewalsService = Depends(get_renewals_service),
):
    """Advisory AI suggestion (renew / renegotiate / terminate) + rationale,
    grounded in the contract's facts on file. Never records a decision."""
    row = _get_renewal(db, renewal_id=renewal_id, current_user=current_user)
    return service.get_recommendation(row=row, org_id=current_user.org_id)


@router.post("/{renewal_id}/decision")
def decide_renewal(
    renewal_id: str,
    payload: RenewalDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:renew")),
    service: RenewalsService = Depends(get_renewals_service),
):
    row = _get_renewal(db, renewal_id=renewal_id, current_user=current_user)
    row = service.decide_renewal(
        row=row,
        decision=payload.decision,
        note=payload.note,
        current_user=current_user,
        request_id=getattr(request.state, "request_id", None),
    )
    return serialize_renewal(row, contract_title=_title(db, row))


@router.post("/{renewal_id}/confirm-dates")
def confirm_renewal_dates(
    renewal_id: str,
    payload: RenewalConfirmPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:renew")),
    service: RenewalsService = Depends(get_renewals_service),
):
    """A person confirms the calculated renewal dates, or types their own."""
    row = _get_renewal(db, renewal_id=renewal_id, current_user=current_user)
    row = service.confirm_dates(
        row=row,
        expiration_date=payload.expiration_date,
        notice_date=payload.notice_date,
        current_user=current_user,
        request_id=getattr(request.state, "request_id", None),
        no_end_date=payload.no_end_date,
    )
    return serialize_renewal(row, contract_title=_title(db, row))


@router.post("/run-window-check")
async def run_renewal_window_check(
    request: Request,
    current_user=Depends(require_permission("admin_panel:access")),
    service: RenewalsService = Depends(get_renewals_service),
):
    return await service.run_window_check(
        current_user=current_user,
        request_id=getattr(request.state, "request_id", None),
    )
