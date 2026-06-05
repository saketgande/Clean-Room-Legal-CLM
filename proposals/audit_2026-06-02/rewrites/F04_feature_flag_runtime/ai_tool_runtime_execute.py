"""F-04 rewrite — ``ToolRuntime.execute`` feature_flag gate at execution.

Replaces the permission-and-validation prologue of
``backend/app/ai/tool_runtime.py:86-157`` (``ToolRuntime.execute``).
Defense-in-depth: even if Claude picks a tool from a stale schema
that disabled the feature mid-stream, the runtime refuses to run it.

This file also reorders the row insertion so a feature-disabled call
returns a structured ``feature_disabled`` result rather than raising
into the streaming generator's free-form-error path.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.ai.confirmations import create_confirmation
from app.ai.tool_policy import AssistantToolPolicy
from app.ai.tool_registry import tool_registry
from app.assistant.models import AssistantToolCall
from app.auth.models import User
from app.core.database import utcnow
from app.core.enums import AssistantToolCallStatus
from app.core.rbac import has_permission

_logger = logging.getLogger(__name__)


async def execute_tool_with_feature_gate(
    self,
    db: Session,
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    user: User,
    session_id: str,
    assistant_run_id: str | None = None,
    provider_tool_use_id: str | None = None,
    provider_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Production-grade ``ToolRuntime.execute`` — feature-flag gate before any DB insert."""
    spec = tool_registry.get(tool_name)
    if not has_permission(user.permission_values, spec.required_permission):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Missing permission: {spec.required_permission}",
        )
    if not AssistantToolPolicy.is_enabled(spec.name, db, user.org_id):
        # Return a structured result the controller can surface to the user
        # without re-feeding the model into a retry loop.
        _logger.info(
            "assistant.tool.feature_disabled",
            extra={
                "tool_name": tool_name,
                "session_id": session_id,
                "assistant_run_id": assistant_run_id,
                "org_id": user.org_id,
            },
        )
        return {
            "status": "feature_disabled",
            "tool_name": tool_name,
            "feature_flag": spec.feature_flag,
            "message": (
                f"Tool '{tool_name}' is disabled by organization policy "
                f"(setting: {spec.feature_flag})."
            ),
        }
    validated_input = spec.input_model.model_validate(tool_input)
    # Idempotency key uses the structured builder from CC-5 in F-10 — for
    # tools that elect to participate. Other tools fall back to the legacy
    # ``_idempotency_key`` helper to avoid an unrelated migration.
    from app.ai.tool_runtime import _idempotency_key  # type: ignore[import-not-found]

    call = AssistantToolCall(
        org_id=user.org_id,
        session_id=session_id,
        assistant_run_id=assistant_run_id,
        provider_tool_use_id=provider_tool_use_id,
        tool_name=tool_name,
        category=spec.category,
        arguments=validated_input.model_dump(mode="json"),
        status=AssistantToolCallStatus.RUNNING,
        confirmation_required=spec.requires_confirmation,
        idempotency_key=_idempotency_key(
            tool_name, session_id, validated_input.model_dump(mode="json")
        ),
        output_schema_name=spec.output_model.__name__,
        started_at=utcnow(),
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    db.add(call)
    db.flush()
    try:
        if spec.requires_confirmation:
            confirmation = create_confirmation(
                db,
                org_id=user.org_id,
                user_id=user.id,
                session_id=session_id,
                assistant_run_id=assistant_run_id or "",
                tool_call=call,
                tool_input=validated_input.model_dump(mode="json"),
                policy={
                    "confirmation_policy": spec.confirmation_policy,
                    "category": spec.category,
                },
                provider_state=provider_state,
            )
            db.flush()
            return {
                "confirmation_required": True,
                "tool_call_id": call.id,
                "confirmation_id": confirmation.id,
            }

        result = await self._execute_validated(
            db,
            tool_name=tool_name,
            payload=validated_input,
            user=user,
            session_id=session_id,
        )
        call.result = result
        call.status = AssistantToolCallStatus.SUCCEEDED
        call.finished_at = utcnow()
        db.flush()
        return result
    except Exception as exc:
        # F-15: every post-insert failure now flips the row to FAILED.
        call.status = AssistantToolCallStatus.FAILED
        call.error_message = str(exc)[:2000]
        call.finished_at = utcnow()
        try:
            db.flush()
        except Exception:  # noqa: BLE001 - flush of error-state must not mask original
            db.rollback()
            _logger.exception(
                "assistant.tool.failure_persist_failed",
                extra={
                    "tool_name": tool_name,
                    "tool_call_id": call.id,
                },
            )
        raise
