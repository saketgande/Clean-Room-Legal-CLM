import logging
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.integrations.resend import EmailSender, resend_client
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.obligations.models import Obligation, ObligationReminder

logger = logging.getLogger(__name__)

DUE_SOON_DAYS = 7


def serialize_obligation(ob, *, contract_title=None, counterparty_name=None, owner_name=None) -> dict:
    """Obligation JSON enriched with the context the UI needs — which contract,
    which counterparty, and the internal owner's name — so the page never has
    to show a bare UUID or re-fetch the contract list to resolve titles."""
    return {
        "id": ob.id,
        "org_id": ob.org_id,
        "contract_id": ob.contract_id,
        "contract_version_id": ob.contract_version_id,
        "contract_title": contract_title,
        "counterparty_name": counterparty_name,
        "owner_user_id": ob.owner_user_id,
        "owner_name": owner_name,
        "responsible_party": ob.responsible_party,
        "obligation_type": ob.obligation_type,
        "description": ob.description,
        "due_date": ob.due_date.isoformat() if ob.due_date else None,
        "recurrence": ob.recurrence,
        "status": ob.status,
        "source_citation": ob.source_citation,
        "metadata_json": ob.metadata_json or {},
        "created_at": ob.created_at.isoformat() if ob.created_at else None,
        "updated_at": ob.updated_at.isoformat() if ob.updated_at else None,
    }


class ObligationsService:
    def __init__(self, db: Session, *, resend: EmailSender | None = None):
        self.db = db
        self.resend = resend or resend_client

    def serialize_one(self, ob) -> dict:
        """Single-obligation enrichment (get/update/complete) — one extra lookup."""
        db = self.db
        contract = db.get(Contract, ob.contract_id)
        owner_name = None
        if ob.owner_user_id:
            owner = db.get(User, ob.owner_user_id)
            owner_name = owner.full_name if owner else None
        return serialize_obligation(
            ob,
            contract_title=contract.title if contract else None,
            counterparty_name=contract.counterparty_name if contract else None,
            owner_name=owner_name,
        )

    def update_obligation(self, *, ob: Obligation, payload, current_user: User) -> dict:
        db = self.db
        updates = payload.model_dump(exclude_unset=True)
        if "owner_user_id" in updates and updates["owner_user_id"] is not None:
            owner = db.get(User, updates["owner_user_id"])
            if owner is None or owner.org_id != current_user.org_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Owner user must belong to this organization",
                )
        for key, value in updates.items():
            setattr(ob, key, value)
        ob.updated_by_user_id = current_user.id
        write_audit_log(
            db,
            action="obligation.updated",
            resource_type="obligation",
            resource_id=ob.id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            after=payload.model_dump(exclude_unset=True, mode="json"),
        )
        db.commit()
        db.refresh(ob)
        return self.serialize_one(ob)

    def complete_obligation(self, *, ob: Obligation, current_user: User) -> dict:
        db = self.db
        ob.status = "completed"
        ob.updated_by_user_id = current_user.id
        write_audit_log(
            db,
            action="obligation.completed",
            resource_type="obligation",
            resource_id=ob.id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            after={"contract_id": ob.contract_id},
        )
        db.commit()
        db.refresh(ob)
        return self.serialize_one(ob)

    def trigger_extraction(self, *, contract_id: str, current_user: User) -> dict:
        db = self.db
        contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
        from app.contract_files.models import ContractTextSnapshot, ContractVersion

        version = (
            db.get(ContractVersion, contract.current_authoritative_version_id)
            if contract.current_authoritative_version_id
            else None
        )
        snapshot = (
            db.get(ContractTextSnapshot, version.text_snapshot_id)
            if version and version.text_snapshot_id
            else None
        )
        if version is None or snapshot is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Contract has no extractable authoritative version")
        job = create_job(
            db,
            org_id=current_user.org_id,
            job_type="obligation_extraction",
            resource_type="contract",
            resource_id=contract.id,
            created_by_user_id=current_user.id,
            idempotency_key=f"obligation_extraction:{version.id}:{snapshot.id}:manual",
            metadata={"contract_version_id": version.id, "text_snapshot_id": snapshot.id},
        )
        db.commit()
        job = db.get(JobRun, job.id)
        dispatch_job(db, job=job)
        db.commit()
        return {"job_id": job.id, "status": job.status}

    async def run_reminders(self, *, current_user: User, request_id: str | None) -> dict:
        db = self.db
        today = utcnow().date()
        # Recompute overdue / due-soon statuses for open obligations.
        open_obligations = db.scalars(
            select(Obligation).where(
                Obligation.org_id == current_user.org_id,
                Obligation.deleted_at.is_(None),
                Obligation.status.in_(["open", "due_soon", "overdue"]),
                Obligation.due_date.is_not(None),
            )
        ).all()
        overdue = due_soon = 0
        for ob in open_obligations:
            if ob.due_date < today:
                ob.status = "overdue"
                overdue += 1
            elif ob.due_date <= today + timedelta(days=DUE_SOON_DAYS):
                ob.status = "due_soon"
                due_soon += 1
            else:
                ob.status = "open"

        due_reminders = db.scalars(
            select(ObligationReminder).where(
                ObligationReminder.org_id == current_user.org_id,
                ObligationReminder.sent_at.is_(None),
                ObligationReminder.remind_at <= today,
            )
        ).all()
        sent = 0
        failed = 0
        for reminder in due_reminders:
            ob = db.get(Obligation, reminder.obligation_id)
            if ob is None or ob.deleted_at is not None or ob.status in {"completed", "cancelled"}:
                continue
            owner = db.get(User, ob.owner_user_id) if ob.owner_user_id else None
            if owner is not None:
                # Don't leak the raw obligation text (an extracted contract excerpt /
                # possible PII) into the email body. Send a minimal notice — type +
                # due date — and point the owner to the app to read the detail behind
                # the auth boundary.
                obligation_label = ob.obligation_type or "contract obligation"
                try:
                    await self.resend.send_email(
                        to=owner.email,
                        subject=f"Obligation due: {obligation_label}",
                        html=(
                            f"<p>You have a <b>{obligation_label}</b> due on {ob.due_date}.</p>"
                            f"<p>Open the contract workspace to review the details.</p>"
                        ),
                    )
                except Exception:
                    # A single failed send must not abort the whole reminder sweep or
                    # roll back the status recomputation above. Skip marking this one
                    # as sent so it is retried on the next run.
                    logger.exception(
                        "obligation reminder email failed",
                        extra={"obligation_id": ob.id, "reminder_id": reminder.id},
                    )
                    failed += 1
                    continue
            reminder.sent_at = today
            reminder.updated_by_user_id = current_user.id
            sent += 1
        write_audit_log(
            db,
            action="obligation.reminders_run",
            resource_type="org",
            resource_id=current_user.org_id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            request_id=request_id,
            after={
                "reminders_sent": sent,
                "reminders_failed": failed,
                "overdue": overdue,
                "due_soon": due_soon,
            },
        )
        db.commit()
        return {
            "reminders_sent": sent,
            "reminders_failed": failed,
            "marked_overdue": overdue,
            "marked_due_soon": due_soon,
        }
