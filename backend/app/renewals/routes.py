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
        query = query.where(
            or_(
                RenewalEvent.expiration_date.is_not(None),
                RenewalEvent.notice_date.is_not(None),
                RenewalEvent.renewal_window_starts_at.is_not(None),
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
    return _get_renewal(db, renewal_id=renewal_id, current_user=current_user)


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
    return service.decide_renewal(
        row=row,
        decision=payload.decision,
        note=payload.note,
        current_user=current_user,
        request_id=getattr(request.state, "request_id", None),
    )


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
