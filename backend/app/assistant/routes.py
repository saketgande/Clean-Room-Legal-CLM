import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.confirmations import confirm_confirmation, reject_confirmation
from app.ai.controller import ai_controller
from app.ai.tool_registry import tool_registry
from app.assistant.models import (
    AssistantContractHandle,
    AssistantMessage,
    AssistantRun,
    AssistantSession,
    AssistantToolCall,
)
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.core.enums import AssistantRunStatus
from app.projects.access import get_project_for_user

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantSessionCreate(BaseModel):
    session_type: str = "general"
    title: str | None = None
    project_id: str | None = None
    contract_id: str | None = None
    tabular_review_id: str | None = None


class AssistantStreamRequest(BaseModel):
    message: str = Field(min_length=1)
    contract_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None
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
    project_id: str | None = None,
    contract_id: str | None = None,
    status_filter: str = "active",
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    query = select(AssistantSession).where(
        AssistantSession.org_id == current_user.org_id,
        AssistantSession.created_by_user_id == current_user.id,
    )
    if status_filter:
        query = query.where(AssistantSession.status == status_filter)
    if project_id:
        get_project_for_user(db, project_id=project_id, user=current_user)
        query = query.where(AssistantSession.project_id == project_id)
    if contract_id:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
        query = query.where(AssistantSession.contract_id == contract_id)
    return db.scalars(query.order_by(AssistantSession.updated_at.desc()).limit(min(limit, 100))).all()


@router.post("/sessions")
def create_session(
    payload: AssistantSessionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    if payload.project_id:
        get_project_for_user(db, project_id=payload.project_id, user=current_user)
    if payload.contract_id:
        get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    session = AssistantSession(
        org_id=current_user.org_id,
        session_type=payload.session_type,
        title=payload.title,
        project_id=payload.project_id,
        contract_id=payload.contract_id,
        tabular_review_id=payload.tabular_review_id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(session)
    db.flush()
    if payload.contract_id:
        db.add(
            AssistantContractHandle(
                org_id=current_user.org_id,
                session_id=session.id,
                contract_id=payload.contract_id,
                handle="contract-0",
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
        )
    db.commit()
    db.refresh(session)
    return session


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    session = _get_session_for_user(db, session_id=session_id, current_user=current_user)
    handles = db.scalars(
        select(AssistantContractHandle).where(AssistantContractHandle.session_id == session.id)
    ).all()
    return {"session": session, "contract_handles": handles}


@router.patch("/sessions/{session_id}")
def update_session(
    session_id: str,
    payload: AssistantSessionUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    session = _get_session_for_user(db, session_id=session_id, current_user=current_user)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(session, key, value)
    session.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(session)
    return session


@router.post("/sessions/{session_id}/contracts")
def add_contract_handle(
    session_id: str,
    payload: AssistantContractHandleAdd,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    session = _get_session_for_user(db, session_id=session_id, current_user=current_user)
    get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    handle = _ensure_contract_handle(
        db,
        session=session,
        contract_id=payload.contract_id,
        current_user=current_user,
        requested_handle=payload.handle,
    )
    db.commit()
    db.refresh(handle)
    return handle


@router.get("/sessions/{session_id}/messages")
def list_session_messages(
    session_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    session = _get_session_for_user(db, session_id=session_id, current_user=current_user)
    return db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.org_id == current_user.org_id, AssistantMessage.session_id == session.id)
        .order_by(AssistantMessage.created_at.asc())
        .limit(min(limit, 200))
    ).all()


@router.get("/sessions/{session_id}/runs")
def list_session_runs(
    session_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    session = _get_session_for_user(db, session_id=session_id, current_user=current_user)
    return db.scalars(
        select(AssistantRun)
        .where(AssistantRun.org_id == current_user.org_id, AssistantRun.session_id == session.id)
        .order_by(AssistantRun.created_at.desc())
        .limit(min(limit, 100))
    ).all()


@router.get("/runs/{assistant_run_id}")
def get_run(
    assistant_run_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    run = _get_run_for_user(db, assistant_run_id=assistant_run_id, current_user=current_user)
    tool_calls = _tool_calls_for_run(db, assistant_run_id=run.id, org_id=current_user.org_id)
    return {"assistant_run": run, "tool_calls": tool_calls}


@router.get("/runs/{assistant_run_id}/tool-calls")
def list_run_tool_calls(
    assistant_run_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    run = _get_run_for_user(db, assistant_run_id=assistant_run_id, current_user=current_user)
    return _tool_calls_for_run(db, assistant_run_id=run.id, org_id=current_user.org_id)


@router.post("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    payload: AssistantStreamRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    session = _get_session_for_user(db, session_id=session_id, current_user=current_user)
    if payload.project_id:
        get_project_for_user(db, project_id=payload.project_id, user=current_user)
    for contract_id in payload.contract_ids:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)

    for contract_id in payload.contract_ids:
        _ensure_contract_handle(db, session=session, contract_id=contract_id, current_user=current_user)
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
        context_manifest={"contract_ids": payload.contract_ids, "project_id": payload.project_id},
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(assistant_run)
    db.commit()
    db.refresh(assistant_run)

    async def event_stream() -> AsyncIterator[str]:
        yield _sse("session_started", {"session_id": session.id, "assistant_run_id": assistant_run.id})
        answer_parts: list[str] = []
        citations: list[dict] = []
        waiting_for_confirmation = False
        try:
            async for event in ai_controller.stream_assistant_run(
                db,
                user=current_user,
                org_id=current_user.org_id,
                created_by_user_id=current_user.id,
                session_id=session.id,
                assistant_run_id=assistant_run.id,
                message=payload.message,
                request_id=getattr(request.state, "request_id", None),
                project_id=payload.project_id or session.project_id,
                contract_id=session.contract_id or (payload.contract_ids[0] if payload.contract_ids else None),
                contract_ids=payload.contract_ids,
            ):
                if event["event"] == "message_delta":
                    answer_parts.append(event["payload"].get("text", ""))
                if event["event"] == "confirmation_required":
                    waiting_for_confirmation = True
                if event["event"] == "tool_finished":
                    result = event["payload"].get("result")
                    citations.extend(_citations_from_tool_result(result))
                yield _sse(event["event"], event["payload"])
                if event["event"] == "tool_finished":
                    for extra_event in _events_from_tool_result(event["payload"].get("result")):
                        yield _sse(extra_event["event"], extra_event["payload"])
            if waiting_for_confirmation:
                assistant_run.status = AssistantRunStatus.WAITING_CONFIRMATION
                db.commit()
                yield _sse(
                    "done",
                    {"assistant_run_id": assistant_run.id, "run_status": assistant_run.status},
                )
                return
            answer = "".join(answer_parts)
            if answer:
                assistant_message = AssistantMessage(
                    org_id=current_user.org_id,
                    session_id=session.id,
                    role="assistant",
                    content=answer,
                    citations=citations,
                    metadata_json={"assistant_run_id": assistant_run.id},
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
                db.add(assistant_message)
                db.flush()
                assistant_run.assistant_message_id = assistant_message.id
                for call in db.scalars(
                    select(AssistantToolCall).where(
                        AssistantToolCall.org_id == current_user.org_id,
                        AssistantToolCall.assistant_run_id == assistant_run.id,
                        AssistantToolCall.message_id.is_(None),
                    )
                ):
                    call.message_id = assistant_message.id
            assistant_run.status = AssistantRunStatus.SUCCEEDED
            db.commit()
            yield _sse(
                "done",
                {"assistant_run_id": assistant_run.id, "run_status": assistant_run.status},
            )
        except Exception as exc:
            assistant_run.status = AssistantRunStatus.FAILED
            assistant_run.error_message = str(exc)
            db.commit()
            yield _sse("error", {"message": str(exc), "assistant_run_id": assistant_run.id})
            yield _sse("done", {"assistant_run_id": assistant_run.id, "run_status": assistant_run.status})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/runs/{assistant_run_id}/resume")
async def resume_run(
    assistant_run_id: str,
    request: Request,
    confirmation_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    run = _get_run_for_user(db, assistant_run_id=assistant_run_id, current_user=current_user)

    async def event_stream() -> AsyncIterator[str]:
        answer_parts: list[str] = []
        citations: list[dict] = []
        try:
            async for event in ai_controller.resume_assistant_run(
                db,
                user=current_user,
                assistant_run_id=assistant_run_id,
                confirmation_id=confirmation_id,
                request_id=getattr(request.state, "request_id", None),
            ):
                if event["event"] == "message_delta":
                    answer_parts.append(event["payload"].get("text", ""))
                if event["event"] == "tool_finished":
                    result = event["payload"].get("result")
                    citations.extend(_citations_from_tool_result(result))
                yield _sse(event["event"], event["payload"])
                if event["event"] == "tool_finished":
                    for extra_event in _events_from_tool_result(event["payload"].get("result")):
                        yield _sse(extra_event["event"], extra_event["payload"])
            answer = "".join(answer_parts)
            if answer:
                assistant_message = AssistantMessage(
                    org_id=current_user.org_id,
                    session_id=run.session_id,
                    role="assistant",
                    content=answer,
                    citations=citations,
                    metadata_json={"assistant_run_id": run.id, "resumed": True},
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
                db.add(assistant_message)
                db.flush()
                run.assistant_message_id = assistant_message.id
                for call in db.scalars(
                    select(AssistantToolCall).where(
                        AssistantToolCall.org_id == current_user.org_id,
                        AssistantToolCall.assistant_run_id == run.id,
                        AssistantToolCall.message_id.is_(None),
                    )
                ):
                    call.message_id = assistant_message.id
                db.commit()
            yield _sse("done", {"assistant_run_id": run.id, "run_status": run.status})
        except Exception as exc:
            run.status = AssistantRunStatus.FAILED
            run.error_message = str(exc)
            db.commit()
            yield _sse("error", {"message": str(exc), "assistant_run_id": run.id})
            yield _sse("done", {"assistant_run_id": run.id, "run_status": run.status})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/confirmations/{confirmation_id}/confirm")
def confirm_assistant_action(
    confirmation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
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
    confirmation = reject_confirmation(
        db,
        confirmation_id=confirmation_id,
        user=current_user,
        reason=payload.reason,
    )
    db.commit()
    return {
        "confirmation_id": confirmation.id,
        "status": confirmation.status,
        "assistant_run_id": confirmation.assistant_run_id,
        "resume_required": False,
    }


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _get_session_for_user(db: Session, *, session_id: str, current_user) -> AssistantSession:
    session = db.get(AssistantSession, session_id)
    if (
        session is None
        or session.org_id != current_user.org_id
        or session.created_by_user_id != current_user.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")
    return session


def _get_run_for_user(db: Session, *, assistant_run_id: str, current_user) -> AssistantRun:
    run = db.get(AssistantRun, assistant_run_id)
    if run is None or run.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant run not found")
    _get_session_for_user(db, session_id=run.session_id, current_user=current_user)
    return run


def _tool_calls_for_run(db: Session, *, assistant_run_id: str, org_id: str) -> list[AssistantToolCall]:
    return db.scalars(
        select(AssistantToolCall)
        .where(AssistantToolCall.org_id == org_id, AssistantToolCall.assistant_run_id == assistant_run_id)
        .order_by(AssistantToolCall.created_at.asc())
    ).all()


def _ensure_contract_handle(
    db: Session,
    *,
    session: AssistantSession,
    contract_id: str,
    current_user,
    requested_handle: str | None = None,
) -> AssistantContractHandle:
    existing = db.scalar(
        select(AssistantContractHandle).where(
            AssistantContractHandle.org_id == current_user.org_id,
            AssistantContractHandle.session_id == session.id,
            AssistantContractHandle.contract_id == contract_id,
        )
    )
    if existing is not None:
        return existing
    if requested_handle:
        duplicate = db.scalar(
            select(AssistantContractHandle).where(
                AssistantContractHandle.org_id == current_user.org_id,
                AssistantContractHandle.session_id == session.id,
                AssistantContractHandle.handle == requested_handle,
            )
        )
        if duplicate is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Contract handle already exists")
        handle_value = requested_handle
    else:
        handle_count = len(
            db.scalars(
                select(AssistantContractHandle).where(
                    AssistantContractHandle.org_id == current_user.org_id,
                    AssistantContractHandle.session_id == session.id,
                )
            ).all()
        )
        handle_value = f"contract-{handle_count}"
    handle = AssistantContractHandle(
        org_id=current_user.org_id,
        session_id=session.id,
        contract_id=contract_id,
        handle=handle_value,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(handle)
    db.flush()
    return handle


def _citations_from_tool_result(result: dict | None) -> list[dict]:
    if not isinstance(result, dict):
        return []
    citations: list[dict] = []
    if result.get("text_snapshot_id"):
        citations.append(
            {
                "type": "text_snapshot",
                "contract_id": result.get("contract_id"),
                "text_snapshot_id": result.get("text_snapshot_id"),
            }
        )
    for match in result.get("matches") or []:
        citations.append(
            {
                "type": "match",
                "contract_id": result.get("contract_id"),
                "start_char": match.get("start_char"),
                "end_char": match.get("end_char"),
                "excerpt": match.get("excerpt"),
            }
        )
    return citations


def _events_from_tool_result(result: dict | None) -> list[dict]:
    if not isinstance(result, dict):
        return []
    events = [
        {"event": "citation", "payload": citation}
        for citation in _citations_from_tool_result(result)
    ]
    artifact_type = result.get("artifact_type")
    if artifact_type == "generated_contract":
        events.append(
            {
                "event": "contract_generated",
                "payload": {
                    "contract_id": result.get("contract_id"),
                    "contract_file_id": result.get("contract_file_id"),
                    "contract_version_id": result.get("contract_version_id"),
                },
            }
        )
    if artifact_type == "assistant_edit":
        events.append(
            {
                "event": "tracked_change_created",
                "payload": {
                    "contract_id": result.get("contract_id"),
                    "base_version_id": result.get("base_version_id"),
                    "contract_version_id": result.get("contract_version_id"),
                    "contract_edit_id": result.get("contract_edit_id"),
                },
            }
        )
    return events
