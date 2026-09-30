"""Workflow "Send to counterparty" step: issue a 7-day share link by email,
let the counterparty comment and Submit (which expires the link), and show the
reviewer the state plus every comment the counterparty wrote.

The step itself stays ``waiting_human``; the reviewer decides afterwards with
the existing complete-step (approve) / return (request changes) endpoints.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractShare
from app.contracts.comments_service import list_shared_comments
from app.contracts.models import Contract, ContractParty
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import ShareAccessMode
from app.intake.models import IntakeTeamMember
from app.integrations.sendgrid import sendgrid_client
from app.notifications.models import Notification
from app.workflows.models import WorkflowRun, WorkflowStepRun

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash_token(value: str) -> str:
    # Must match app.contract_files.routes._hash_secret — the public routes
    # look shares up by this exact digest.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _current_counterparty_step(db: Session, run: WorkflowRun) -> WorkflowStepRun:
    from app.workflows.service import WorkflowService

    steps = list(run.steps or [])
    idx = run.current_index
    if not (0 <= idx < len(steps)) or steps[idx].get("type") != "counterparty":
        raise HTTPException(status.HTTP_409_CONFLICT, "The workflow is not at a counterparty step")
    sr = WorkflowService(db)._sr_at(run, idx)
    if sr is None or sr.status != "waiting_human":
        raise HTTPException(status.HTTP_409_CONFLICT, "This step is not waiting for the counterparty")
    return sr


def _authorize(db: Session, *, run: WorkflowRun, sr: WorkflowStepRun, actor, contract: Contract) -> None:
    """The step's assignee / team members act on it even without direct
    contract access (same reasoning as the approval-chain engine fix);
    anyone else needs normal contract access."""
    if sr.assignee_user_id == actor.id:
        return
    if sr.team_id and db.scalar(
        select(IntakeTeamMember.id).where(
            IntakeTeamMember.team_id == sr.team_id,
            IntakeTeamMember.user_id == actor.id,
            IntakeTeamMember.active.is_(True),
        )
    ):
        return
    get_contract_for_user(db, contract_id=contract.id, user=actor)  # 404s when barred


def _contract_for_run(db: Session, run: WorkflowRun) -> Contract:
    if not run.contract_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This request has no contract to send yet")
    contract = db.get(Contract, run.contract_id)
    if contract is None or contract.org_id != run.org_id or contract.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    return contract


def _latest_share(db: Session, sr: WorkflowStepRun) -> ContractShare | None:
    return db.scalar(
        select(ContractShare)
        .where(ContractShare.workflow_step_run_id == sr.id, ContractShare.deleted_at.is_(None))
        .order_by(ContractShare.created_at.desc())
    )


def _state(share: ContractShare | None) -> str:
    if share is None:
        return "not_sent"
    if share.submitted_at is not None:
        return "submitted"
    now = utcnow()
    if share.revoked_at is not None or (share.expires_at is not None and share.expires_at < now):
        return "expired"
    return "sent"


def get_state(db: Session, *, run: WorkflowRun, actor) -> dict:
    contract = _contract_for_run(db, run)
    sr = _current_counterparty_step(db, run)
    _authorize(db, run=run, sr=sr, actor=actor, contract=contract)
    share = _latest_share(db, sr)
    meta = sr.result or {}
    comments = [c for c in list_shared_comments(db, contract=contract) if c["author_kind"] == "counterparty"]
    return {
        "state": _state(share),
        "recipient_email": meta.get("recipient_email"),
        "sent_at": meta.get("sent_at"),
        "expires_at": share.expires_at if share else None,
        "submitted_at": share.submitted_at if share else None,
        "suggested_email": _suggested_email(db, contract),
        "expiry_days": settings.counterparty_link_expiry_days,
        "comments": comments,
    }


def _suggested_email(db: Session, contract: Contract) -> str | None:
    party = db.scalar(
        select(ContractParty)
        .where(
            ContractParty.contract_id == contract.id,
            ContractParty.contact_email.is_not(None),
        )
        .order_by(ContractParty.created_at.desc())
    )
    return party.contact_email if party else None


async def send_to_counterparty(
    db: Session, *, run: WorkflowRun, actor, recipient_email: str, recipient_name: str | None,
    message: str | None, request_id: str | None = None,
) -> dict:
    email = (recipient_email or "").strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A valid recipient email is required")
    contract = _contract_for_run(db, run)
    sr = _current_counterparty_step(db, run)
    _authorize(db, run=run, sr=sr, actor=actor, contract=contract)

    now = utcnow()
    # Re-sending issues a fresh link; any earlier live one stops working.
    for old in db.scalars(
        select(ContractShare).where(
            ContractShare.workflow_step_run_id == sr.id,
            ContractShare.revoked_at.is_(None),
            ContractShare.deleted_at.is_(None),
        )
    ).all():
        old.revoked_at = now

    party = db.scalar(
        select(ContractParty).where(
            ContractParty.contract_id == contract.id,
            ContractParty.party_type == "counterparty",
        )
    )
    display_name = (recipient_name or "").strip() or contract.counterparty_name or email
    if party is None:
        db.add(ContractParty(
            org_id=contract.org_id, contract_id=contract.id, name=display_name,
            party_type="counterparty", contact_email=email,
            created_by_user_id=actor.id, updated_by_user_id=actor.id,
        ))
    else:
        party.contact_email = email

    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=settings.counterparty_link_expiry_days)
    share = ContractShare(
        org_id=contract.org_id, contract_id=contract.id,
        contract_version_id=contract.current_authoritative_version_id,
        token_hash=_hash_token(token), access_mode=ShareAccessMode.VIEW_ONLY,
        expires_at=expires_at, download_allowed=False, workflow_step_run_id=sr.id,
        created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    db.add(share)
    db.flush()

    link = f"{settings.app_base_url.rstrip('/')}/s/{token}"
    safe_title = html.escape(contract.title or "Contract")
    note = f"<p>{html.escape(message.strip())}</p>" if message and message.strip() else ""
    subject = f"Contract for your review: {contract.title or 'Contract'}"
    body = (
        f"<p>Hello {html.escape(display_name)},</p>"
        f"<p>{html.escape(actor.full_name or 'A colleague')} has shared <strong>{safe_title}</strong> "
        f"with you for review.</p>{note}"
        f'<p><a href="{link}">Open the contract</a></p>'
        f"<p>You can read it and leave comments, then click <strong>Submit</strong> when you are done. "
        f"This link expires in {settings.counterparty_link_expiry_days} days, or as soon as you submit.</p>"
    )
    try:
        # No internal CC here: the configured CC address must not be exposed
        # to an external party.
        result = await sendgrid_client.send_email(to=email, subject=subject, html=body, include_cc=False)
    except Exception as exc:
        db.rollback()
        logger.warning("counterparty email failed for run %s: %s", run.id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not send the email — nothing was shared") from exc

    sr.result = {
        **(sr.result or {}), "share_id": share.id, "recipient_email": email,
        "sent_at": now.isoformat(), "expires_at": expires_at.isoformat(),
    }
    sr.note = f"Sent to {email}; awaiting their response."
    db.add(Notification(
        org_id=contract.org_id, user_id=actor.id, channel="email",
        event_type="workflow.counterparty_sent", subject=subject, body=f"Sent to {email}",
        status="sent" if result.status == "mocked" else result.status,
        provider_message_id=result.provider_message_id,
    ))
    write_audit_log(
        db, action="workflow.counterparty_sent", resource_type="contract_share", resource_id=share.id,
        org_id=contract.org_id, actor_user_id=actor.id, request_id=request_id,
        after={"contract_id": contract.id, "recipient_email": email, "expires_at": expires_at.isoformat()},
    )
    write_timeline_event(
        db, org_id=contract.org_id, resource_type="contract", resource_id=contract.id,
        event_type="workflow.counterparty_sent", title=f"Sent to counterparty ({email})",
        actor_user_id=actor.id, request_id=request_id,
    )
    db.commit()
    return get_state(db, run=run, actor=actor)


def submit_share(db: Session, *, share: ContractShare, request_id: str | None = None) -> dict:
    """Called by the public Submit route once the share has been validated as
    live: expire the link, stamp the step, and queue the reviewer notice."""
    if not share.workflow_step_run_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This link does not support submitting")
    now = utcnow()
    share.submitted_at = now
    share.revoked_at = now
    sr = db.get(WorkflowStepRun, share.workflow_step_run_id)
    if sr is not None:
        sr.result = {**(sr.result or {}), "submitted_at": now.isoformat()}
        sr.note = "Counterparty submitted their comments — review and decide."
    write_audit_log(
        db, action="workflow.counterparty_submitted", resource_type="contract_share",
        resource_id=share.id, org_id=share.org_id, actor_user_id=None, request_id=request_id,
        after={"contract_id": share.contract_id},
    )
    write_timeline_event(
        db, org_id=share.org_id, resource_type="contract", resource_id=share.contract_id,
        event_type="workflow.counterparty_submitted", title="Counterparty submitted their comments",
        actor_user_id=None, request_id=request_id,
    )
    db.commit()
    if sr is not None:
        # Same post-commit race as the other step notifications: give the
        # commit a moment to land before the worker reads the row.
        from app.jobs.tasks import notify_counterparty_submitted

        notify_counterparty_submitted.apply_async(args=[sr.id], countdown=4)
    return {"submitted": True}
