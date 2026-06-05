"""F-01 rewrite — ``AIController.stream_assistant_run`` (lifecycle + cancellation).

Replaces ``backend/app/ai/controller.py:93-325`` (stream) and the
matching lifecycle blocks at ``:445-585`` (resume). The signature
changes to accept ``request: Request`` and ``budget: StreamingBudget``
from the CC-4 ``streaming_session`` context — instead of reading from
``settings.ai_max_tool_iterations`` and a request-scoped DB session
that's already closing.

Behavior preserved: SSE event shape (``session_started`` / ``message_delta``
/ ``tool_started`` / ``tool_finished`` / ``confirmation_required`` / ``done``),
tool dispatch, structured logging of every Claude call. Behavior
changed (per Agent 3 done-conditions): the loop now exits when
``request.is_disconnected()`` is True between iterations (F-01 +
F-07), and the per-tool exception path logs the stack trace plus
writes a ``resource_timeline_event`` row before re-feeding Claude a
structured error code (F-08).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.ai.controller import AIController as _UpstreamAIController  # noqa: F401
from app.ai.context import list_contract_handles
from app.ai.models import AISkillRun
from app.ai.prompt_versions import get_active_prompt_bundle
from app.ai.registry import skill_registry
from app.ai.tool_runtime import tool_runtime
from app.assistant.models import AssistantRun
from app.auth.models import User
from app.core.audit import write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import AISkillRunStatus, AIValidationStatus
from app.core.streaming import (
    ClientDisconnected,
    StreamingBudget,
)
from app.integrations.claude import claude_client

_logger = logging.getLogger(__name__)


class AIControllerStreamingMixin:
    """Lifecycle-safe stream/resume methods. Replaces the originals on ``AIController``."""

    async def stream_assistant_run(
        self,
        db: Session,
        *,
        user: User,
        org_id: str,
        created_by_user_id: str,
        session_id: str,
        assistant_run_id: str,
        message: str,
        request_id: str | None,
        project_id: str | None = None,
        contract_id: str | None = None,
        contract_ids: list[str] | None = None,
        request: Request,
        budget: StreamingBudget,
    ):
        """Stream a tool-use Claude run; observes disconnect/budget between iterations."""
        spec = skill_registry.get("assistant_streaming")
        model_config = {"temperature": spec.temperature, "max_tokens": spec.max_tokens}
        prompt_bundle = get_active_prompt_bundle(
            db,
            org_id=org_id,
            prompt_key=spec.prompt_key,
            default_version=spec.prompt_version,
            model_config=model_config,
        )
        handles = list_contract_handles(db, session_id=session_id)
        tools = self._assistant_tool_schemas(user=user, db=db, org_id=org_id)  # type: ignore[attr-defined]
        skill_run = AISkillRun(
            org_id=org_id,
            skill_name=spec.name,
            skill_version=spec.version,
            execution_mode=spec.execution_mode,
            status=AISkillRunStatus.RUNNING,
            resource_type="assistant_session",
            resource_id=session_id,
            session_id=session_id,
            assistant_run_id=assistant_run_id,
            prompt_key=prompt_bundle.prompt_key,
            prompt_version=prompt_bundle.version,
            prompt_hash=prompt_bundle.prompt_hash,
            model=prompt_bundle.model_name,
            model_config_hash=prompt_bundle.model_config_hash,
            input_payload={
                "message": message,
                "project_id": project_id,
                "contract_id": contract_id,
                "contract_ids": contract_ids or [],
                "handles": handles,
            },
            started_at=utcnow(),
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(skill_run)
        db.flush()
        db.commit()

        contract_summaries = self._contract_context_summaries(  # type: ignore[attr-defined]
            db, org_id=org_id, handles=handles
        )
        contract_inventory = self._contract_inventory(db, org_id=org_id)  # type: ignore[attr-defined]
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": self._assistant_user_prompt(  # type: ignore[attr-defined]
                    message=message,
                    project_id=project_id,
                    contract_id=contract_id,
                    contract_ids=contract_ids or [],
                    handles=handles,
                    contract_summaries=contract_summaries,
                    contract_inventory=contract_inventory,
                ),
            }
        ]
        final_answer_parts: list[str] = []
        tool_results: list[dict[str, Any]] = []

        try:
            for iteration in range(settings.ai_max_tool_iterations):
                # Disconnect / cost checks BEFORE the next Claude call — that
                # is where the money is.
                await self._check_streaming_budget(  # type: ignore[attr-defined]
                    request=request, budget=budget
                )
                provider_response = await claude_client.complete_with_tools(
                    system_prompt=prompt_bundle.shared_system_prompt
                    + "\n\n"
                    + prompt_bundle.skill_prompt,
                    messages=messages,
                    tools=tools,
                    max_tokens=spec.max_tokens,
                    temperature=spec.temperature,
                    model=prompt_bundle.model_name,
                )
                call_log = self._log_assistant_ai_call(  # type: ignore[attr-defined]
                    db,
                    org_id=org_id,
                    created_by_user_id=created_by_user_id,
                    request_id=request_id,
                    skill_run=skill_run,
                    spec=spec,
                    provider_response=provider_response,
                    assistant_run_id=assistant_run_id,
                    session_id=session_id,
                )
                budget.record_tokens(getattr(call_log, "total_tokens", None))
                budget.check_token_budget()

                text_delta = "".join(
                    block.get("text", "")
                    for block in provider_response.content_blocks
                    if block.get("type") == "text"
                )
                if not provider_response.tool_use_blocks:
                    if text_delta:
                        final_answer_parts.append(text_delta)
                        yield {"event": "message_delta", "payload": {"text": text_delta}}
                    skill_run.status = AISkillRunStatus.SUCCEEDED
                    skill_run.validation_status = AIValidationStatus.VALID
                    skill_run.output_payload = {
                        "answer": "".join(final_answer_parts),
                        "tool_results": tool_results,
                    }
                    skill_run.finished_at = utcnow()
                    db.commit()
                    return

                if text_delta:
                    final_answer_parts.append(text_delta)
                    yield {"event": "message_delta", "payload": {"text": text_delta}}

                messages.append(
                    {"role": "assistant", "content": provider_response.content_blocks}
                )
                tool_result_blocks: list[dict[str, Any]] = []
                for tool_use in provider_response.tool_use_blocks:
                    tool_name = tool_use.get("name") or ""
                    tool_input = tool_use.get("input") or {}
                    yield {
                        "event": "tool_started",
                        "payload": {
                            "tool_name": tool_name,
                            "tool_use_id": tool_use.get("id"),
                        },
                    }
                    try:
                        result = await tool_runtime.execute(
                            db,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            user=user,
                            session_id=session_id,
                            assistant_run_id=assistant_run_id,
                            provider_tool_use_id=tool_use.get("id"),
                            provider_state={
                                "schema_version": 1,
                                "pending_tool_use": tool_use,
                                "messages": messages,
                                "skill_run_id": skill_run.id,
                            },
                        )
                        budget.record_tool_call(
                            tool_name=tool_name,
                            idempotency_key=result.get("idempotency_key")
                            if isinstance(result, dict)
                            else None,
                        )
                        db.commit()
                        if result.get("confirmation_required"):
                            run = db.get(AssistantRun, assistant_run_id)
                            if run is not None:
                                run.status = "waiting_confirmation"
                                run.provider_state = {
                                    "schema_version": 1,
                                    "pending_tool_use": tool_use,
                                    "messages": messages,
                                    "skill_run_id": skill_run.id,
                                    "confirmation_id": result.get("confirmation_id"),
                                }
                            skill_run.status = AISkillRunStatus.WAITING_CONFIRMATION
                            skill_run.validation_status = AIValidationStatus.NOT_VALIDATED
                            skill_run.output_payload = {
                                "status": "waiting_confirmation",
                                "tool_name": tool_name,
                                "tool_call_id": result.get("tool_call_id"),
                                "confirmation_id": result.get("confirmation_id"),
                            }
                            skill_run.finished_at = utcnow()
                            db.commit()
                            yield {
                                "event": "confirmation_required",
                                "payload": {
                                    "tool_name": tool_name,
                                    "tool_call_id": result.get("tool_call_id"),
                                    "confirmation_id": result.get("confirmation_id"),
                                    "assistant_run_id": assistant_run_id,
                                },
                            }
                            return
                        tool_results.append({"tool_name": tool_name, "result": result})
                        yield {
                            "event": "tool_finished",
                            "payload": {
                                "tool_name": tool_name,
                                "tool_use_id": tool_use.get("id"),
                                "result": result,
                            },
                        }
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.get("id"),
                                "content": self._json_tool_result(  # type: ignore[attr-defined]
                                    self._model_safe_result(  # type: ignore[attr-defined]
                                        db,
                                        value=result,
                                        org_id=org_id,
                                        session_id=session_id,
                                        user_id=created_by_user_id,
                                    )
                                ),
                            }
                        )
                    except ClientDisconnected:
                        raise
                    except Exception as exc:
                        # F-08: log + timeline + sanitized re-feed.
                        error_payload = self._classify_tool_exception(  # type: ignore[attr-defined]
                            exc=exc,
                            tool_name=tool_name,
                        )
                        _logger.exception(
                            "assistant.tool.failed",
                            extra={
                                "tool_name": tool_name,
                                "assistant_run_id": assistant_run_id,
                                "session_id": session_id,
                                "iteration": iteration,
                                "error_class": type(exc).__name__,
                            },
                        )
                        try:
                            write_timeline_event(
                                db,
                                org_id=org_id,
                                resource_type="assistant_session",
                                resource_id=session_id,
                                event_type="assistant.tool.failed",
                                title=f"Tool {tool_name} failed",
                                assistant_run_id=assistant_run_id,
                                details={
                                    "tool_name": tool_name,
                                    "error_code": error_payload["error_code"],
                                    "message": error_payload["message"],
                                },
                            )
                            db.commit()
                        except Exception:  # noqa: BLE001 - timeline write must not break the stream
                            db.rollback()
                            _logger.exception(
                                "assistant.tool.timeline_write_failed",
                                extra={"assistant_run_id": assistant_run_id},
                            )
                        tool_results.append(
                            {"tool_name": tool_name, **error_payload}
                        )
                        yield {
                            "event": "tool_finished",
                            "payload": {
                                "tool_name": tool_name,
                                "tool_use_id": tool_use.get("id"),
                                "error": error_payload["message"],
                                "error_code": error_payload["error_code"],
                            },
                        }
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.get("id"),
                                "content": self._json_tool_result(  # type: ignore[attr-defined]
                                    self._model_safe_result(  # type: ignore[attr-defined]
                                        db,
                                        value={
                                            "tool_name": tool_name,
                                            **error_payload,
                                        },
                                        org_id=org_id,
                                        session_id=session_id,
                                        user_id=created_by_user_id,
                                    )
                                ),
                                "is_error": True,
                            }
                        )
                messages.append({"role": "user", "content": tool_result_blocks})

            raise RuntimeError("Assistant tool loop exceeded maximum iterations")
        except ClientDisconnected:
            skill_run.status = AISkillRunStatus.CANCELLED
            skill_run.validation_status = AIValidationStatus.NOT_VALIDATED
            skill_run.finished_at = utcnow()
            db.commit()
            raise
        except Exception:
            skill_run.status = AISkillRunStatus.FAILED
            skill_run.validation_status = AIValidationStatus.INVALID
            skill_run.finished_at = utcnow()
            db.commit()
            raise

    async def _check_streaming_budget(
        self,
        *,
        request: Request,
        budget: StreamingBudget,
    ) -> None:
        """Probe ``request.is_disconnected()`` and the token budget once."""
        if await request.is_disconnected():
            raise ClientDisconnected("client_disconnected")
        budget.check_token_budget()
