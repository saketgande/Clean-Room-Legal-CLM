import html
import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.enums import ContractLifecycleStage, RenewalDecision
from app.integrations.resend import EmailSender, resend_client
from app.renewals.models import RenewalEvent

logger = logging.getLogger(__name__)


def serialize_renewal(row: RenewalEvent, *, contract_title: str | None = None) -> dict:
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


class RenewalsService:
    def __init__(self, db: Session, *, resend: EmailSender | None = None):
        self.db = db
        self.resend = resend or resend_client

    def get_recommendation(self, *, row: RenewalEvent, org_id: str) -> dict:
        """Advisory AI suggestion (renew / renegotiate / terminate) + rationale,
        grounded in the contract's facts on file. Never records a decision."""
        db = self.db
        contract = db.get(Contract, row.contract_id)
        if contract is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found for this renewal")
        from app.renewals.recommendation import recommend_renewal

        return recommend_renewal(db, org_id=org_id, renewal=row, contract=contract)

    def decide_renewal(
        self,
        *,
        row: RenewalEvent,
        decision: str,
        note: str | None,
        current_user: User,
        request_id: str | None,
    ) -> RenewalEvent:
        db = self.db
        decision_map = {
            "renew": RenewalDecision.RENEW,
            "terminate": RenewalDecision.TERMINATE,
            "renegotiate": RenewalDecision.RENEGOTIATE,
        }
        row.decision = decision_map[decision]
        row.decision_note = note
        row.updated_by_user_id = current_user.id
        # Deciding clears the renewal-due flag; terminating also closes the contract.
        contract = db.get(Contract, row.contract_id)
        if contract is not None and contract.org_id == current_user.org_id:
            contract.renewal_due = False
            contract.updated_by_user_id = current_user.id
            if (
                decision == "terminate"
                and contract.lifecycle_stage == ContractLifecycleStage.ACTIVE
            ):
                transition_contract_stage(
                    db,
                    contract=contract,
                    to_stage=ContractLifecycleStage.CLOSED,
                    actor_user_id=current_user.id,
                    reason="Renewal decision: terminate",
                    request_id=request_id,
                )
        write_audit_log(
            db,
            action="renewal.decided",
            resource_type="renewal_event",
            resource_id=row.id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            request_id=request_id,
            after={"decision": row.decision, "contract_id": row.contract_id},
        )
        write_timeline_event(
            db,
            org_id=current_user.org_id,
            resource_type="contract",
            resource_id=row.contract_id,
            event_type="renewal.decided",
            title=f"Renewal decision: {decision}",
            actor_user_id=current_user.id,
            request_id=request_id,
            details={"renewal_event_id": row.id, "decision": decision},
        )
        db.commit()
        db.refresh(row)
        return row

    async def run_window_check(self, *, current_user: User, request_id: str | None) -> dict:
        """Detect contracts whose renewal/notice window has opened and move ACTIVE
        contracts to RENEWAL_DUE, notifying the owner."""
        db = self.db
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
                    await self.resend.send_email(
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
            request_id=request_id,
            after={"contracts_moved_to_renewal_due": moved, "notifications_failed": notify_failed},
        )
        db.commit()
        return {"contracts_moved_to_renewal_due": moved, "notifications_failed": notify_failed}
