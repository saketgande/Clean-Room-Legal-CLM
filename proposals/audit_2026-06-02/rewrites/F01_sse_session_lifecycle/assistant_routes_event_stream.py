"""F-01 rewrite — assistant_routes ``event_stream`` / ``resume_run`` (lifecycle fix).

Replaces the inline generators at
``backend/app/assistant/routes.py:292-379`` (``stream_session``) and
``:382-458`` (``resume_run``).

Originally the SSE generator captured the request-scoped ``db`` from
``Depends(get_db)`` and called ``db.commit()``/``db.refresh()`` after
FastAPI's dependency was already past its ``finally`` block on
disconnect. The replacement opens its own generator-scoped session via
CC-4 ``streaming_session(...)``, threads ``request`` and ``StreamingBudget``
into the controller (which F-07 uses for cost+cancellation), and uses
the strict serializer from CC-4 to render every SSE event (F-22).

Sketch only — see ``_README.md`` for the change ledger. This file is
the lifecycle-critical section; the rest of ``routes.py`` is untouched.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.controller import ai_controller
from app.assistant.models import (
    AssistantContractHandle,
    AssistantMessage,
    AssistantRun,
    AssistantSession,
    AssistantToolCall,
)
from app.assistant.routes import (  # type: ignore[import-not-found]
    AssistantStreamRequest,
    _accumulate_block,
    _citations_from_tool_result,
    _ensure_contract_handle,
    _events_from_tool_result,
    _get_run_for_user,
    _get_session_for_user,
    _require_ai_tools,
    _validate_and_store_assistant_citations,
)
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.core.enums import AssistantRunStatus
from app.core.streaming import (
    ClientDisconnected,
    CostBudgetExceeded,
    StreamingBudget,
    ToolLoopDetected,
    sse_error_event,
    sse_serialize,
    streaming_session,
)
from app.projects.access import get_project_for_user

router = APIRouter(prefix="/assistant", tags=["assistant"])
_logger = logging.getLogger(__name__)


@router.post("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    payload: AssistantStreamRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
) -> StreamingResponse:
    """Stream an assistant run; lifecycle-isolated DB session inside the generator."""
    _require_ai_tools(current_user)
    session = _get_session_for_user(db, session_id=session_id, current_user=current_user)
    if payload.project_id:
        get_project_for_user(db, project_id=payload.project_id, user=current_user)
    for contract_id in payload.contract_ids:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
    for contract_id in payload.contract_ids:
        _ensure_contract_handle(
            db, session=session, contract_id=contract_id, current_user=current_user
        )
    user_message = AssistantMessage(
        org_id=current_user.org_id,
        session_id=session.id,
        role="user",
        content=payload.message,
        metadata_json={"client_event_id": payload.client_event_id},
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(user_message)
    db.flush()
    assistant_run = AssistantRun(
        org_id=current_user.org_id,
        session_id=session.id,
        status=AssistantRunStatus.RUNNING,
        user_message_id=user_message.id,
        provider_state={"schema_version": 1, "resume_run_id": payload.resume_run_id},
        context_manifest={
            "contract_ids": payload.contract_ids,
            "project_id": payload.project_id,
        },
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(assistant_run)
    db.commit()
    db.refresh(assistant_run)
    org_id: str = current_user.org_id
    user_id: str = current_user.id
    session_pk: str = session.id
    session_project_id: str | None = session.project_id
    session_contract_id: str | None = session.contract_id
    assistant_run_id: str = assistant_run.id

    async def event_stream() -> AsyncIterator[str]:
        budget = StreamingBudget()
        try:
            async with streaming_session(request=request, budget=budget) as ctx:
                gen_db = ctx.db
                yield sse_serialize(
                    "session_started",
                    {"session_id": session_pk, "assistant_run_id": assistant_run_id},
                )
                answer_parts: list[str] = []
                citations: list[dict] = []
                blocks: list[dict] = []
                waiting_for_confirmation = False
                async for event in ai_controller.stream_assistant_run(
                    gen_db,
                    user=current_user,
                    org_id=org_id,
                    created_by_user_id=user_id,
                    session_id=session_pk,
                    assistant_run_id=assistant_run_id,
                    message=payload.message,
                    request_id=getattr(request.state, "request_id", None),
                    project_id=payload.project_id or session_project_id,
                    contract_id=session_contract_id
                    or (payload.contract_ids[0] if payload.contract_ids else None),
                    contract_ids=payload.contract_ids,
                    request=request,
                    budget=budget,
                ):
                    if event["event"] == "message_delta":
                        answer_parts.append(event["payload"].get("text", ""))
                    if event["event"] == "confirmation_required":
                        waiting_for_confirmation = True
                    if event["event"] == "tool_finished":
                        result = event["payload"].get("result")
                        citations.extend(_citations_from_tool_result(result))
                    _accumulate_block(blocks, event)
                    yield sse_serialize(event["event"], event["payload"])
                    if event["event"] == "tool_finished":
                        for extra_event in _events_from_tool_result(
                            event["payload"].get("result")
                        ):
                            yield sse_serialize(
                                extra_event["event"], extra_event["payload"]
                            )
                if waiting_for_confirmation:
                    gen_run = gen_db.get(AssistantRun, assistant_run_id)
                    if gen_run is not None:
                        gen_db.refresh(gen_run)
                        yield sse_serialize(
                            "done",
                            {
                                "assistant_run_id": assistant_run_id,
                                "run_status": gen_run.status,
                            },
                        )
                    return
                _finalize_assistant_message(
                    gen_db,
                    assistant_run_id=assistant_run_id,
                    org_id=org_id,
                    session_id=session_pk,
                    current_user=current_user,
                    answer_parts=answer_parts,
                    citations=citations,
                    blocks=blocks,
                )
                yield sse_serialize(
                    "done",
                    {
                        "assistant_run_id": assistant_run_id,
                        "run_status": AssistantRunStatus.SUCCEEDED.value,
                    },
                )
        except ClientDisconnected:
            _mark_run_terminal(
                assistant_run_id=assistant_run_id,
                status_value=AssistantRunStatus.CANCELLED,
                error_message="client_disconnected",
            )
            # No further yields — client is gone.
            return
        except CostBudgetExceeded as exc:
            _mark_run_terminal(
                assistant_run_id=assistant_run_id,
                status_value=AssistantRunStatus.FAILED,
                error_message=str(exc),
            )
            yield sse_error_event(
                assistant_run_id=assistant_run_id,
                error_code="token_budget_exceeded",
                message=str(exc),
            )
            yield sse_serialize(
                "done",
                {
                    "assistant_run_id": assistant_run_id,
                    "run_status": AssistantRunStatus.FAILED.value,
                },
            )
        except ToolLoopDetected as exc:
            _mark_run_terminal(
                assistant_run_id=assistant_run_id,
                status_value=AssistantRunStatus.FAILED,
                error_message=str(exc),
            )
            yield sse_error_event(
                assistant_run_id=assistant_run_id,
                error_code="tool_loop_detected",
                message=str(exc),
            )
            yield sse_serialize(
                "done",
                {
                    "assistant_run_id": assistant_run_id,
                    "run_status": AssistantRunStatus.FAILED.value,
                },
            )
        except Exception as exc:
            _logger.exception(
                "assistant.stream.failed",
                extra={
                    "assistant_run_id": assistant_run_id,
                    "session_id": session_pk,
                    "org_id": org_id,
                    "error_class": type(exc).__name__,
                },
            )
            _mark_run_terminal(
                assistant_run_id=assistant_run_id,
                status_value=AssistantRunStatus.FAILED,
                error_message=str(exc),
            )
            yield sse_error_event(
                assistant_run_id=assistant_run_id,
                error_code="stream_failed",
                message=str(exc),
            )
            yield sse_serialize(
                "done",
                {
                    "assistant_run_id": assistant_run_id,
                    "run_status": AssistantRunStatus.FAILED.value,
                },
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _finalize_assistant_message(
    gen_db: Session,
    *,
    assistant_run_id: str,
    org_id: str,
    session_id: str,
    current_user,
    answer_parts: list[str],
    citations: list[dict],
    blocks: list[dict],
) -> None:
    """Persist the assistant message + commit. Generator-session only."""
    assistant_run = gen_db.get(AssistantRun, assistant_run_id)
    if assistant_run is None:
        return
    answer = "".join(answer_parts)
    if answer:
        validated = _validate_and_store_assistant_citations(
            gen_db,
            org_id=org_id,
            assistant_run_id=assistant_run_id,
            current_user=current_user,
            raw_citations=citations,
        )
        assistant_message = AssistantMessage(
            org_id=org_id,
            session_id=session_id,
            role="assistant",
            content=answer,
            citations=validated,
            metadata_json={
                "assistant_run_id": assistant_run_id,
                "blocks": blocks,
            },
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        gen_db.add(assistant_message)
        gen_db.flush()
        assistant_run.assistant_message_id = assistant_message.id
        for call in gen_db.scalars(
            select(AssistantToolCall).where(
                AssistantToolCall.org_id == org_id,
                AssistantToolCall.assistant_run_id == assistant_run_id,
                AssistantToolCall.message_id.is_(None),
            )
        ):
            call.message_id = assistant_message.id
    assistant_run.status = AssistantRunStatus.SUCCEEDED
    gen_db.commit()


def _mark_run_terminal(
    *,
    assistant_run_id: str,
    status_value: AssistantRunStatus,
    error_message: str,
) -> None:
    """Open a fresh session to record terminal status; outer session may be dead."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        run = db.get(AssistantRun, assistant_run_id)
        if run is None:
            return
        run.status = status_value
        run.error_message = error_message
        db.commit()
    except Exception:  # noqa: BLE001 - terminal-state write must not raise further
        db.rollback()
        _logger.exception(
            "assistant.stream.terminal_write_failed",
            extra={"assistant_run_id": assistant_run_id},
        )
    finally:
        db.close()
