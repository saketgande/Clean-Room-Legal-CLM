import json
import logging
from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.confirmations import (
    close_turn_for_confirmation,
    confirm_confirmation,
    reject_confirmation,
)
from app.ai.tool_registry import tool_registry
from app.assistant import run_events
from app.assistant.dependencies import get_assistant_service
from app.assistant.models import AssistantMessage, AssistantRun, AssistantSession
from app.assistant.runner import (
    accumulate_block as _accumulate_block,  # noqa: F401  (re-exported for callers/tests)
)
from app.assistant.runner import (
    citations_from_tool_result as _citations_from_tool_result,  # noqa: F401
)
from app.assistant.runner import (
    events_from_tool_result as _events_from_tool_result,  # noqa: F401
)
from app.assistant.runner import (
    user_facing_error as _user_facing_error,  # noqa: F401
)
from app.assistant.service import AssistantService
from app.contracts.service import get_contract_for_user
from app.core.config import settings
from app.core.database import SessionLocal, utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import AssistantRunStatus, AssistantSessionType
from app.core.rate_limit import limiter
from app.core.rbac import has_permission

router = APIRouter(prefix="/assistant", tags=["assistant"])
logger = logging.getLogger(__name__)


class AssistantSessionCreate(BaseModel):
    session_type: AssistantSessionType = AssistantSessionType.GENERAL
    title: str | None = None
    contract_id: str | None = None
    tabular_review_id: str | None = None


class AssistantStreamRequest(BaseModel):
    # Same reason as BrainAskRequest.question: this is prompt input.
    message: str = Field(min_length=1, max_length=8000)
    contract_ids: list[str] = Field(default_factory=list)
    resume_run_id: str | None = None
    client_event_id: str | None = None


class AssistantSessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, pattern="^(active|archived)$")


class AssistantContractHandleAdd(BaseModel):
    contract_id: str
    handle: str | None = Field(default=None, min_length=1, max_length=80)


class ConfirmationRejectRequest(BaseModel):
    reason: str | None = None


@router.get("/tools")
def list_tools(current_user=Depends(require_permission("assistant:use"))):
    permissions = current_user.permission_values
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "category": spec.category,
            "permission": spec.required_permission,
            "confirmation_policy": spec.confirmation_policy,
            "feature_flag": spec.feature_flag,
            "enabled_by_default": spec.enabled_by_default,
            "input_schema": spec.input_model.model_json_schema(),
            "output_schema": spec.output_model.model_json_schema(),
        }
        for spec in tool_registry.all()
        if spec.enabled_by_default
        and (spec.required_permission in permissions or "*" in permissions)
    ]


@router.get("/sessions")
def list_sessions(
    contract_id: str | None = None,
    status_filter: str = "active",
    q: str | None = None,
    limit: int = 50,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.list_sessions(
        current_user=current_user, contract_id=contract_id,
        status_filter=status_filter, q=q, limit=limit,
    )


@router.post("/sessions")
def create_session(
    payload: AssistantSessionCreate,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.create_session(payload=payload, current_user=current_user)


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.get_session(session_id=session_id, current_user=current_user)


@router.patch("/sessions/{session_id}")
def update_session(
    session_id: str,
    payload: AssistantSessionUpdate,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.update_session(session_id=session_id, payload=payload, current_user=current_user)


@router.post("/sessions/{session_id}/contracts")
def add_contract_handle(
    session_id: str,
    payload: AssistantContractHandleAdd,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.add_contract_handle(session_id=session_id, payload=payload, current_user=current_user)


@router.get("/sessions/{session_id}/messages")
def list_session_messages(
    session_id: str,
    limit: int = 100,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.list_session_messages(session_id=session_id, limit=limit, current_user=current_user)


@router.get("/sessions/{session_id}/runs")
def list_session_runs(
    session_id: str,
    limit: int = 50,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.list_session_runs(session_id=session_id, limit=limit, current_user=current_user)


@router.get("/runs/{assistant_run_id}")
def get_run(
    assistant_run_id: str,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.get_run(assistant_run_id=assistant_run_id, current_user=current_user)


@router.post("/sessions/{session_id}/stream")
@limiter.limit(settings.rate_limit_assistant_stream)
async def stream_session(
    session_id: str,
    payload: AssistantStreamRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    _require_ai_tools(current_user)
    session = service.get_session_for_user(session_id=session_id, current_user=current_user)
    for contract_id in payload.contract_ids:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)

    # One answer at a time per chat: a second one would interleave with the
    # first in the conversation history. The row lock makes two simultaneous
    # sends (two tabs) take turns at this check.
    db.execute(select(AssistantSession.id).where(AssistantSession.id == session.id).with_for_update())
    if db.scalar(
        select(AssistantRun.id).where(
            AssistantRun.session_id == session.id, AssistantRun.status == AssistantRunStatus.RUNNING
        )
    ):
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Aegis is still answering your last message in this chat. Wait for it, or press Stop.",
        )
    for contract_id in payload.contract_ids:
        service.ensure_contract_handle(session=session, contract_id=contract_id, current_user=current_user)
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
        context_manifest={"contract_ids": payload.contract_ids},
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(assistant_run)
    db.commit()
    db.refresh(assistant_run)

    request_id = getattr(request.state, "request_id", None)
    failure = _enqueue_turn(db, assistant_run, mode="start", request_id=request_id)
    first = _sse("session_started", {"session_id": session.id, "assistant_run_id": assistant_run.id})
    return _watch_response(assistant_run.id, after=None, first=first, failure=failure)


@router.post("/runs/{assistant_run_id}/resume")
@limiter.limit(settings.rate_limit_assistant_stream)
async def resume_run(
    assistant_run_id: str,
    request: Request,
    confirmation_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    """Continue a run after its action was confirmed. The worker does the work;
    this response only watches it (leaving the page does not stop it)."""
    _require_ai_tools(current_user)
    run = service.get_run_for_user(assistant_run_id=assistant_run_id, current_user=current_user)
    # Atomic: of two clicks (or two tabs) only one may continue the run — the
    # confirmed action must never execute twice.
    moved = db.execute(
        update(AssistantRun)
        .where(
            AssistantRun.id == run.id,
            AssistantRun.status == AssistantRunStatus.WAITING_CONFIRMATION,
        )
        .values(status=AssistantRunStatus.RUNNING, updated_at=utcnow())
    ).rowcount
    db.commit()
    if not moved:
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer isn't waiting for a confirmation")
    db.refresh(run)
    # The old stream ends with the "done" that paused the run; start a new one.
    run_events.reset_events(run.id)
    failure = _enqueue_turn(
        db, run, mode="resume", confirmation_id=confirmation_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return _watch_response(run.id, after=None, first=None, failure=failure)


@router.get("/runs/{assistant_run_id}/events")
async def watch_run_events(
    assistant_run_id: str,
    after: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    """Watch an answer that is being (or was) produced: replays its events from
    the start, or after ``after`` / ``Last-Event-ID`` when reconnecting. Ends
    with ``done``. Only the chat's owner may watch."""
    run = service.get_run_for_user(assistant_run_id=assistant_run_id, current_user=current_user)
    return _watch_response(run.id, after=after or last_event_id, first=None, failure=None)


@router.post("/runs/{assistant_run_id}/cancel")
def cancel_run(
    assistant_run_id: str,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    """The Stop button. The worker stops at its next check (within about half a
    second, or when the tool in progress finishes) and saves what it has."""
    run = service.get_run_for_user(assistant_run_id=assistant_run_id, current_user=current_user)
    if run.status != AssistantRunStatus.RUNNING:
        return {"assistant_run_id": run.id, "run_status": run.status, "cancel_requested": False}
    if not run_events.request_cancel(run.id):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Couldn't stop the answer right now. Try again.")
    return {"assistant_run_id": run.id, "run_status": run.status, "cancel_requested": True}


ENQUEUE_FAILED_MESSAGE = "Aegis couldn't start this answer right now. Please try again in a moment."


def _enqueue_turn(
    db: Session,
    run: AssistantRun,
    *,
    mode: str,
    request_id: str | None,
    confirmation_id: str | None = None,
) -> str | None:
    """Queue the answer for a worker. Returns a user-facing message if that
    failed (the run is then marked failed so it doesn't look stuck)."""
    from app.jobs.tasks import run_assistant_turn

    try:
        run_assistant_turn.apply_async(
            kwargs={
                "run_id": run.id,
                "mode": mode,
                "confirmation_id": confirmation_id,
                "request_id": request_id,
            }
        )
        return None
    except Exception:
        logger.exception("could not queue assistant run %s (request %s)", run.id, request_id)
        run.status = AssistantRunStatus.FAILED
        run.error_message = ENQUEUE_FAILED_MESSAGE
        run.completed_at = utcnow()
        db.commit()
        return ENQUEUE_FAILED_MESSAGE


def _run_status_reader(run_id: str):
    """Fresh read of a run's status, off the event loop (used by the watcher
    only when no events arrive for a while)."""

    def read() -> str | None:
        with SessionLocal() as db:
            run = db.get(AssistantRun, run_id)
            return run.status if run is not None else None

    async def status_now() -> str | None:
        return await anyio.to_thread.run_sync(read)

    return status_now


def _watch_response(run_id: str, *, after: str | None, first: str | None, failure: str | None) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        if first:
            yield first
        if failure:
            yield _sse("error", {"message": failure, "assistant_run_id": run_id})
            yield _sse("done", {"assistant_run_id": run_id, "run_status": AssistantRunStatus.FAILED})
            return
        # Disconnecting simply ends this generator: the answer keeps going in
        # the worker and can be watched again from GET /runs/{id}/events.
        async for event_id, event, payload in run_events.tail(
            run_id, after=after, run_status=_run_status_reader(run_id)
        ):
            if event == "ping":
                yield ": keep-alive\n\n"
                continue
            yield _sse(event, payload, event_id=event_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        # Don't let a proxy (nginx) buffer the live answer.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/confirmations/{confirmation_id}/confirm")
def confirm_assistant_action(
    confirmation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    _require_ai_tools(current_user)
    confirmation = confirm_confirmation(db, confirmation_id=confirmation_id, user=current_user)
    db.commit()
    return {
        "confirmation_id": confirmation.id,
        "status": confirmation.status,
        "assistant_run_id": confirmation.assistant_run_id,
        "resume_required": True,
    }


@router.post("/confirmations/{confirmation_id}/reject")
def reject_assistant_action(
    confirmation_id: str,
    payload: ConfirmationRejectRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    _require_ai_tools(current_user)
    confirmation = reject_confirmation(
        db,
        confirmation_id=confirmation_id,
        user=current_user,
        reason=payload.reason,
    )
    # Close the turn: the run used to stay "waiting_confirmation" forever with no
    # reply saved, so after a reload the chat showed the request with no answer.
    close_turn_for_confirmation(db, confirmation, outcome="rejected", actor_user_id=current_user.id)
    db.commit()
    return {
        "confirmation_id": confirmation.id,
        "status": confirmation.status,
        "assistant_run_id": confirmation.assistant_run_id,
        "resume_required": False,
    }


def _require_ai_tools(current_user) -> None:
    if not has_permission(current_user.permission_values, "assistant:use_ai_tools"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Missing permission: assistant:use_ai_tools"
        )


def _sse(event: str, payload: dict, *, event_id: str | None = None) -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"
