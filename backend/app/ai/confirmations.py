from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIConfirmation
from app.assistant.models import AssistantMessage, AssistantRun, AssistantToolCall
from app.auth.models import User
from app.core.access import is_org_admin
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.enums import AIConfirmationStatus, AssistantRunStatus, AssistantToolCallStatus

DEFAULT_CONFIRMATION_MINUTES = 30


def create_confirmation(
    db: Session,
    *,
    org_id: str,
    user_id: str,
    session_id: str,
    assistant_run_id: str,
    tool_call: AssistantToolCall,
    tool_input: dict[str, Any],
    policy: dict[str, Any],
    provider_state: dict[str, Any] | None = None,
) -> AIConfirmation:
    confirmation = AIConfirmation(
        org_id=org_id,
        session_id=session_id,
        assistant_run_id=assistant_run_id,
        tool_call_id=tool_call.id,
        tool_name=tool_call.tool_name,
        requested_payload={"tool_name": tool_call.tool_name, "arguments": tool_input},
        tool_input=tool_input,
        policy=policy,
        resource_type=tool_call.resource_type,
        resource_id=tool_call.resource_id,
        resource_version=tool_call.resource_version,
        idempotency_key=tool_call.idempotency_key,
        expires_at=utcnow() + timedelta(minutes=DEFAULT_CONFIRMATION_MINUTES),
        provider_state=provider_state or {},
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )
    db.add(confirmation)
    db.flush()
    tool_call.confirmation_id = confirmation.id
    tool_call.status = AssistantToolCallStatus.CONFIRMATION_REQUIRED
    tool_call.confirmation_required = True
    return confirmation


def confirm_confirmation(
    db: Session,
    *,
    confirmation_id: str,
    user: User,
) -> AIConfirmation:
    confirmation = _get_pending_confirmation(db, confirmation_id=confirmation_id, user=user)
    confirmation.status = AIConfirmationStatus.CONFIRMED
    confirmation.decided_at = utcnow()
    confirmation.decided_by_user_id = user.id
    confirmation.updated_by_user_id = user.id
    tool_call = db.get(AssistantToolCall, confirmation.tool_call_id)
    if tool_call is not None:
        tool_call.status = AssistantToolCallStatus.CONFIRMED
        tool_call.confirmed_by_user_id = user.id
        tool_call.updated_by_user_id = user.id
    write_audit_log(
        db,
        action="assistant.confirmation_confirmed",
        resource_type="ai_confirmation",
        resource_id=confirmation.id,
        org_id=confirmation.org_id,
        actor_user_id=user.id,
        after={
            "tool_name": confirmation.tool_name,
            "tool_call_id": confirmation.tool_call_id,
            "assistant_run_id": confirmation.assistant_run_id,
            "requested_by_user_id": confirmation.created_by_user_id,
        },
    )
    return confirmation


def reject_confirmation(
    db: Session,
    *,
    confirmation_id: str,
    user: User,
    reason: str | None = None,
) -> AIConfirmation:
    confirmation = _get_pending_confirmation(db, confirmation_id=confirmation_id, user=user)
    confirmation.status = AIConfirmationStatus.REJECTED
    confirmation.decided_at = utcnow()
    confirmation.decided_by_user_id = user.id
    confirmation.rejection_reason = reason
    confirmation.updated_by_user_id = user.id
    tool_call = db.get(AssistantToolCall, confirmation.tool_call_id)
    if tool_call is not None:
        tool_call.status = AssistantToolCallStatus.REJECTED
        tool_call.error_message = reason
        tool_call.updated_by_user_id = user.id
    write_audit_log(
        db,
        action="assistant.confirmation_rejected",
        resource_type="ai_confirmation",
        resource_id=confirmation.id,
        org_id=confirmation.org_id,
        actor_user_id=user.id,
        after={
            "tool_name": confirmation.tool_name,
            "tool_call_id": confirmation.tool_call_id,
            "assistant_run_id": confirmation.assistant_run_id,
            "requested_by_user_id": confirmation.created_by_user_id,
            "reason": reason,
        },
    )
    return confirmation


def _get_pending_confirmation(db: Session, *, confirmation_id: str, user: User) -> AIConfirmation:
    confirmation = db.scalar(
        select(AIConfirmation)
        .where(AIConfirmation.id == confirmation_id)
        .with_for_update()
    )
    if confirmation is None or confirmation.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Confirmation not found")
    if confirmation.created_by_user_id != user.id and not is_org_admin(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the requesting user or an org admin can decide this confirmation",
        )
    if confirmation.status != AIConfirmationStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirmation is already decided")
    if confirmation.expires_at and confirmation.expires_at < utcnow():
        # Commit the expiry before raising: get_db rolls back on an exception, so
        # setting EXPIRED and then raising used to lose it and leave the run
        # waiting for a decision that can no longer be made.
        expire_confirmation(db, confirmation)
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirmation expired")
    return confirmation


# ---- ending a turn whose action won't run ----------------------------------

def confirmation_details(tool_input: Any) -> dict[str, str]:
    """What the user is being asked to approve — the action's own arguments, so
    the confirmation card says WHICH request, WHO, WHAT decision. Without this the
    card only named the tool, and a prompt-injected action could be approved
    blind. Scalars only, length-capped; the server re-validates on execution."""
    if not isinstance(tool_input, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in tool_input.items():
        if len(out) >= 8:
            break
        if isinstance(value, bool):
            text = "yes" if value else "no"
        elif isinstance(value, (str, int, float)):
            text = str(value).strip()
        else:
            continue
        if text:
            out[str(key)[:60]] = text if len(text) <= 300 else text[:299] + "…"
    return out


def close_turn_for_confirmation(db: Session, confirmation: AIConfirmation, *, outcome: str,
                                actor_user_id: str | None) -> bool:
    """End the assistant turn behind a confirmation that will not run (rejected or
    expired): store a short visible reply and mark the run cancelled, so the chat
    never shows a request with no answer after a reload. No-op unless the run is
    still waiting for this decision. Does not commit."""
    run = db.get(AssistantRun, confirmation.assistant_run_id) if confirmation.assistant_run_id else None
    if run is None or run.org_id != confirmation.org_id or run.status != AssistantRunStatus.WAITING_CONFIRMATION:
        return False
    action = (confirmation.tool_name or "action").replace("_", " ")
    text = (f"Cancelled — you rejected “{action}”, so nothing was changed." if outcome == "rejected"
            else f"The “{action}” action expired before it was approved, so nothing was changed. "
                 "Ask again if you still want it.")
    actor = actor_user_id or confirmation.created_by_user_id
    message = AssistantMessage(
        org_id=run.org_id, session_id=run.session_id, role="assistant", content=text, citations=[],
        metadata_json={"assistant_run_id": run.id, "blocks": [{"type": "content", "text": text}],
                       "confirmation_id": confirmation.id, "confirmation_outcome": outcome},
        created_by_user_id=actor, updated_by_user_id=actor,
    )
    db.add(message)
    db.flush()
    run.assistant_message_id = message.id
    for call in db.scalars(select(AssistantToolCall).where(
            AssistantToolCall.org_id == run.org_id, AssistantToolCall.assistant_run_id == run.id,
            AssistantToolCall.message_id.is_(None))):
        call.message_id = message.id
    run.status = AssistantRunStatus.CANCELLED
    return True


def expire_confirmation(db: Session, confirmation: AIConfirmation) -> None:
    """Mark a pending confirmation expired and close its turn. Does not commit."""
    confirmation.status = AIConfirmationStatus.EXPIRED
    tool_call = db.get(AssistantToolCall, confirmation.tool_call_id) if confirmation.tool_call_id else None
    if tool_call is not None and tool_call.status == AssistantToolCallStatus.CONFIRMATION_REQUIRED:
        tool_call.status = AssistantToolCallStatus.FAILED
        tool_call.error_message = "Confirmation expired before it was approved"
    close_turn_for_confirmation(db, confirmation, outcome="expired", actor_user_id=None)
    write_audit_log(
        db, action="assistant.confirmation_expired", resource_type="ai_confirmation",
        resource_id=confirmation.id, org_id=confirmation.org_id, actor_user_id=None,
        after={"tool_name": confirmation.tool_name, "assistant_run_id": confirmation.assistant_run_id},
    )


def sweep_confirmations(db: Session, *, now=None, limit: int = 200) -> dict:
    """Periodic clean-up (celery beat): expire pending confirmations past their
    deadline, and close runs still "waiting_confirmation" whose confirmation was
    already decided against (rejected/expired) — e.g. rejections made before the
    reject endpoint closed the turn. Commits."""
    now = now or utcnow()
    expired = 0
    for conf in db.scalars(select(AIConfirmation).where(
            AIConfirmation.status == AIConfirmationStatus.PENDING,
            AIConfirmation.expires_at.is_not(None), AIConfirmation.expires_at < now).limit(limit)):
        expire_confirmation(db, conf)
        expired += 1
    closed = 0
    stuck = db.scalars(select(AssistantRun).where(
        AssistantRun.status == AssistantRunStatus.WAITING_CONFIRMATION).limit(limit)).all()
    for run in stuck:
        conf = db.scalar(select(AIConfirmation).where(AIConfirmation.assistant_run_id == run.id)
                         .order_by(AIConfirmation.created_at.desc()).limit(1))
        if conf is not None and conf.status in (AIConfirmationStatus.REJECTED, AIConfirmationStatus.EXPIRED):
            outcome = "rejected" if conf.status == AIConfirmationStatus.REJECTED else "expired"
            if close_turn_for_confirmation(db, conf, outcome=outcome, actor_user_id=conf.decided_by_user_id):
                closed += 1
    db.commit()
    return {"expired": expired, "closed_stuck_runs": closed}


def pending_confirmation_for_session(db: Session, *, session_id: str, org_id: str) -> dict | None:
    """The still-actionable confirmation of this session's latest waiting run, so
    a reloaded chat can show its card again (it only ever came from the live
    stream, so a refresh lost it and the action could never be approved)."""
    run = db.scalar(select(AssistantRun).where(
        AssistantRun.session_id == session_id, AssistantRun.org_id == org_id,
        AssistantRun.status == AssistantRunStatus.WAITING_CONFIRMATION,
    ).order_by(AssistantRun.created_at.desc()).limit(1))
    if run is None:
        return None
    conf = db.scalar(select(AIConfirmation).where(
        AIConfirmation.assistant_run_id == run.id, AIConfirmation.status == AIConfirmationStatus.PENDING,
    ).order_by(AIConfirmation.created_at.desc()).limit(1))
    if conf is None or (conf.expires_at and conf.expires_at < utcnow()):
        return None
    return {
        "confirmation_id": conf.id, "assistant_run_id": run.id, "tool_name": conf.tool_name,
        "details": confirmation_details(conf.tool_input),
        "expires_at": conf.expires_at.isoformat() if conf.expires_at else None,
    }
