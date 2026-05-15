from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.ai.models import AIConfirmation
from app.assistant.models import AssistantToolCall
from app.auth.models import User
from app.core.access import is_org_admin
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.enums import AIConfirmationStatus, AssistantToolCallStatus


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
    confirmation = db.get(AIConfirmation, confirmation_id)
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
        confirmation.status = AIConfirmationStatus.EXPIRED
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirmation expired")
    return confirmation
