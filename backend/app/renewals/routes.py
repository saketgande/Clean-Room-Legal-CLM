import logging
import html

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.access import accessible_contract_filter
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import ContractLifecycleStage, RenewalDecision
from app.integrations.resend import resend_client
from app.renewals.models import RenewalEvent

router = APIRouter(prefix="/renewals", tags=["renewals"])

logger = logging.getLogger(__name__)


class RenewalDecisionPayload(BaseModel):
    decision: str = Field(pattern="^(renew|terminate|renegotiate)$")
    note: str | None = None


def _serialize(row: RenewalEvent, *, contract_title: str | None = None) -> dict:
    """Renewal JSON enriched with the contract title — so the portfolio list
    never has to show a bare UUID or re-fetch a separately-paginated contract
    list to resolve it (that list caps at 100 by default, so a renewal whose
    contract falls outside that page silently lost its title before)."""
    return {
        "id": row.id,
        "org_id": row.org_id,
        "contract_id": row.contract_id,
        "contract_title": contract_title,
        "contract_version_id": row.contract_version_id,
        "expiration_date": row.expiration_date.isoformat() if row.expiration_date else None,
        "notice_date": row.notice_date.isoformat() if row.notice_date else None,
        "renewal_window_starts_at": (
            row.renewal_window_starts_at.isoformat() if row.renewal_window_starts_at else None
        ),
        "owner_user_id": row.owner_user_id,
        "decision": row.decision,
        "decision_note": row.decision_note,
        "metadata_json": row.metadata_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


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
    return [_serialize(row, contract_title=title) for row, title in rows]


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
    # Deciding clears the renewal-due flag; terminating also closes the contract.
    contract = db.get(Contract, row.contract_id)
    if contract is not None and contract.org_id == current_user.org_id:
        contract.renewal_due = False
        contract.updated_by_user_id = current_user.id
        if (
            payload.decision == "terminate"
            and contract.lifecycle_stage == ContractLifecycleStage.ACTIVE
        ):
            transition_contract_stage(
                db,
                contract=contract,
                to_stage=ContractLifecycleStage.CLOSED,
                actor_user_id=current_user.id,
                reason="Renewal decision: terminate",
                request_id=getattr(request.state, "request_id", None),
            )
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
    current_user=Depends(require_permission("admin_panel:access")),
):
    """Detect contracts whose renewal/notice window has opened and move ACTIVE
    contracts to RENEWAL_DUE, notifying the owner."""
    today = utcnow().date()
    events = db.scalars(
        select(RenewalEvent).where(RenewalEvent.org_id == current_user.org_id)
    ).all()
    moved = 0
    notify_failed = 0
    for event in events:
        window = event.renewal_window_starts_at or event.notice_date
        if window is None or window > today:
            continue
        contract = db.get(Contract, event.contract_id)
        if (
            contract is None
            or contract.org_id != current_user.org_id
            or contract.lifecycle_stage != ContractLifecycleStage.ACTIVE
            or contract.renewal_due  # already flagged → don't re-notify
        ):
            continue
        # Renewal-due is a flag on the (still ACTIVE) contract, not a stage.
        contract.renewal_due = True
        contract.updated_by_user_id = current_user.id
        owner = db.get(User, event.owner_user_id) if event.owner_user_id else None
        if owner is not None:
            safe_title = html.escape(contract.title or "Untitled contract")
            # The stage transition above is the durable outcome; a notification
            # failure must not roll it back or abort the rest of the sweep.
            try:
                await resend_client.send_email(
                    to=owner.email,
                    subject=f"Renewal window open: {contract.title}",
                    html=(
                        f"<p><b>{safe_title}</b> has entered its renewal window "
                        f"(notice date {event.notice_date}, expires {event.expiration_date}).</p>"
                    ),
                )
            except Exception:
                logger.exception(
                    "renewal window notification email failed",
                    extra={"renewal_event_id": event.id, "contract_id": contract.id},
                )
                notify_failed += 1
        moved += 1
    write_audit_log(
        db,
        action="renewal.window_check_run",
        resource_type="org",
        resource_id=current_user.org_id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        request_id=getattr(request.state, "request_id", None),
        after={"contracts_moved_to_renewal_due": moved, "notifications_failed": notify_failed},
    )
    db.commit()
    return {"contracts_moved_to_renewal_due": moved, "notifications_failed": notify_failed}
