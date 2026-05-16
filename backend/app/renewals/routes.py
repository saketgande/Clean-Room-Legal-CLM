from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import ContractLifecycleStage, RenewalDecision
from app.integrations.resend import resend_client
from app.renewals.models import RenewalEvent

router = APIRouter(prefix="/renewals", tags=["renewals"])


class RenewalDecisionPayload(BaseModel):
    decision: str = Field(pattern="^(renew|terminate|renegotiate)$")
    note: str | None = None


def _get_renewal(db: Session, *, renewal_id: str, current_user: User) -> RenewalEvent:
    row = db.get(RenewalEvent, renewal_id)
    if row is None or row.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Renewal event not found")
    return row


@router.get("")
def list_renewals(
    contract_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    query = select(RenewalEvent).where(RenewalEvent.org_id == current_user.org_id)
    if contract_id:
        query = query.where(RenewalEvent.contract_id == contract_id)
    return db.scalars(query.order_by(RenewalEvent.notice_date.asc())).all()


@router.get("/{renewal_id}")
def get_renewal(
    renewal_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    return _get_renewal(db, renewal_id=renewal_id, current_user=current_user)


@router.post("/{renewal_id}/decision")
def decide_renewal(
    renewal_id: str,
    payload: RenewalDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:renew")),
):
    row = _get_renewal(db, renewal_id=renewal_id, current_user=current_user)
    decision_map = {
        "renew": RenewalDecision.RENEW,
        "terminate": RenewalDecision.TERMINATE,
        "renegotiate": RenewalDecision.RENEGOTIATE,
    }
    row.decision = decision_map[payload.decision]
    row.decision_note = payload.note
    row.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="renewal.decided",
        resource_type="renewal_event",
        resource_id=row.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        request_id=getattr(request.state, "request_id", None),
        after={"decision": row.decision, "contract_id": row.contract_id},
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="contract",
        resource_id=row.contract_id,
        event_type="renewal.decided",
        title=f"Renewal decision: {payload.decision}",
        actor_user_id=current_user.id,
        request_id=getattr(request.state, "request_id", None),
        details={"renewal_event_id": row.id, "decision": payload.decision},
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/run-window-check")
async def run_renewal_window_check(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:renew")),
):
    """Detect contracts whose renewal/notice window has opened and move ACTIVE
    contracts to RENEWAL_DUE, notifying the owner."""
    today = utcnow().date()
    events = db.scalars(
        select(RenewalEvent).where(RenewalEvent.org_id == current_user.org_id)
    ).all()
    moved = 0
    for event in events:
        window = event.renewal_window_starts_at or event.notice_date
        if window is None or window > today:
            continue
        contract = db.get(Contract, event.contract_id)
        if (
            contract is None
            or contract.org_id != current_user.org_id
            or contract.lifecycle_stage != ContractLifecycleStage.ACTIVE
        ):
            continue
        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.RENEWAL_DUE,
            actor_user_id=current_user.id,
            reason="Renewal/notice window opened",
            request_id=getattr(request.state, "request_id", None),
        )
        owner = db.get(User, event.owner_user_id) if event.owner_user_id else None
        if owner is not None:
            await resend_client.send_email(
                to=owner.email,
                subject=f"Renewal window open: {contract.title}",
                html=(
                    f"<p><b>{contract.title}</b> has entered its renewal window "
                    f"(notice date {event.notice_date}, expires {event.expiration_date}).</p>"
                ),
            )
        moved += 1
    write_audit_log(
        db,
        action="renewal.window_check_run",
        resource_type="org",
        resource_id=current_user.org_id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        request_id=getattr(request.state, "request_id", None),
        after={"contracts_moved_to_renewal_due": moved},
    )
    db.commit()
    return {"contracts_moved_to_renewal_due": moved}
