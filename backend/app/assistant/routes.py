import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.confirmations import confirm_confirmation, reject_confirmation
from app.ai.controller import ai_controller
from app.ai.tool_registry import tool_registry
from app.assistant.dependencies import get_assistant_service
from app.assistant.models import AssistantMessage, AssistantRun
from app.assistant.service import AssistantService
from app.contracts.service import get_contract_for_user
from app.core.config import settings
from app.core.deps import get_db, require_permission
from app.core.enums import AssistantRunStatus, AssistantSessionType
from app.core.rate_limit import limiter
from app.core.rbac import has_permission
from app.matters.access import get_project_for_user

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantSessionCreate(BaseModel):
    session_type: AssistantSessionType = AssistantSessionType.GENERAL
    title: str | None = None
    matter_id: str | None = None
    contract_id: str | None = None
    tabular_review_id: str | None = None


class AssistantStreamRequest(BaseModel):
    message: str = Field(min_length=1)
    contract_ids: list[str] = Field(default_factory=list)
    matter_id: str | None = None
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
    matter_id: str | None = None,
    contract_id: str | None = None,
    status_filter: str = "active",
    q: str | None = None,
    limit: int = 50,
    current_user=Depends(require_permission("assistant:use")),
    service: AssistantService = Depends(get_assistant_service),
):
    return service.list_sessions(
        current_user=current_user, matter_id=matter_id, contract_id=contract_id,
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
    if payload.matter_id:
        get_project_for_user(db, matter_id=payload.matter_id, user=current_user)
    for contract_id in payload.contract_ids:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)

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
        context_manifest={"contract_ids": payload.contract_ids, "matter_id": payload.matter_id},
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
        blocks: list[dict] = []
        waiting_for_confirmation = False
        client_disconnected = False
        finalized = False
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
                matter_id=payload.matter_id or session.matter_id,
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
                _accumulate_block(blocks, event)
                yield _sse(event["event"], event["payload"])
                if event["event"] == "tool_finished":
                    for extra_event in _events_from_tool_result(event["payload"].get("result")):
                        yield _sse(extra_event["event"], extra_event["payload"])
                # F-01/F-07: best-effort early-out. When the disconnect is observed
                # BETWEEN iterations, break to stop driving the (paid) Claude loop and fall
                # through to finalization. NOTE: on the pinned uvicorn/ASGI-2.3 stack
                # Starlette cancels this generator at the suspended yield on disconnect,
                # which can skip the finalization below and leave the run in RUNNING. The
                # durable fix (terminal-state in a finally + the controller's own session)
                # is the F-01 follow-up; this guard only reduces wasted spend.
                if await request.is_disconnected():
                    client_disconnected = True
                    break
            if waiting_for_confirmation:
                # The controller already set the run to WAITING_CONFIRMATION and
                # committed; just surface the current status to the client.
                db.refresh(assistant_run)
                finalized = True
                yield _sse(
                    "done",
                    {"assistant_run_id": assistant_run.id, "run_status": assistant_run.status},
                )
                return
            if client_disconnected:
                service.persist_assistant_answer(
                    org_id=current_user.org_id,
                    session_id=session.id,
                    run=assistant_run,
                    answer_parts=answer_parts,
                    citations=citations,
                    blocks=blocks,
                    current_user=current_user,
                    extra_metadata={"interrupted": True},
                )
                assistant_run.status = AssistantRunStatus.INTERRUPTED
                assistant_run.error_message = "Assistant stream interrupted before completion"
                db.commit()
                finalized = True
                return
            service.persist_assistant_answer(
                org_id=current_user.org_id,
                session_id=session.id,
                run=assistant_run,
                answer_parts=answer_parts,
                citations=citations,
                blocks=blocks,
                current_user=current_user,
            )
            assistant_run.status = AssistantRunStatus.SUCCEEDED
            db.commit()
            finalized = True
            yield _sse(
                "done",
                {"assistant_run_id": assistant_run.id, "run_status": assistant_run.status},
            )
        except Exception as exc:
            try:
                service.persist_assistant_answer(
                    org_id=current_user.org_id,
                    session_id=session.id,
                    run=assistant_run,
                    answer_parts=answer_parts,
                    citations=citations,
                    blocks=blocks,
                    current_user=current_user,
                    extra_metadata={"interrupted": True},
                )
            except Exception:
                db.rollback()
            assistant_run.status = AssistantRunStatus.FAILED
            assistant_run.error_message = str(exc)
            db.commit()
            finalized = True
            yield _sse("error", {"message": str(exc), "assistant_run_id": assistant_run.id})
            yield _sse("done", {"assistant_run_id": assistant_run.id, "run_status": assistant_run.status})
        finally:
            if not finalized:
                try:
                    db.rollback()
                    current = db.get(AssistantRun, assistant_run.id)
                    if current is not None and current.status == AssistantRunStatus.RUNNING:
                        current.status = AssistantRunStatus.INTERRUPTED
                        current.error_message = "Assistant stream interrupted before completion"
                        db.commit()
                except Exception:
                    db.rollback()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
    _require_ai_tools(current_user)
    run = service.get_run_for_user(assistant_run_id=assistant_run_id, current_user=current_user)

    async def event_stream() -> AsyncIterator[str]:
        answer_parts: list[str] = []
        citations: list[dict] = []
        blocks: list[dict] = []
        client_disconnected = False
        finalized = False
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
                _accumulate_block(blocks, event)
                yield _sse(event["event"], event["payload"])
                if event["event"] == "tool_finished":
                    for extra_event in _events_from_tool_result(event["payload"].get("result")):
                        yield _sse(extra_event["event"], extra_event["payload"])
                if await request.is_disconnected():
                    client_disconnected = True
                    break
            if client_disconnected:
                service.persist_assistant_answer(
                    org_id=current_user.org_id,
                    session_id=run.session_id,
                    run=run,
                    answer_parts=answer_parts,
                    citations=citations,
                    blocks=blocks,
                    current_user=current_user,
                    extra_metadata={"resumed": True, "interrupted": True},
                )
                run.status = AssistantRunStatus.INTERRUPTED
                run.error_message = "Assistant stream interrupted before completion"
                db.commit()
                finalized = True
                return
            service.persist_assistant_answer(
                org_id=current_user.org_id,
                session_id=run.session_id,
                run=run,
                answer_parts=answer_parts,
                citations=citations,
                blocks=blocks,
                current_user=current_user,
                extra_metadata={"resumed": True},
            )
            # Was previously never set on the success path (only the
            # message/tool-call linking was committed), leaving a resumed run
            # stuck at whatever status it had before resuming (typically
            # WAITING_CONFIRMATION) forever.
            run.status = AssistantRunStatus.SUCCEEDED
            db.commit()
            finalized = True
            yield _sse("done", {"assistant_run_id": run.id, "run_status": run.status})
        except Exception as exc:
            try:
                service.persist_assistant_answer(
                    org_id=current_user.org_id,
                    session_id=run.session_id,
                    run=run,
                    answer_parts=answer_parts,
                    citations=citations,
                    blocks=blocks,
                    current_user=current_user,
                    extra_metadata={"resumed": True, "interrupted": True},
                )
            except Exception:
                db.rollback()
            run.status = AssistantRunStatus.FAILED
            run.error_message = str(exc)
            db.commit()
            finalized = True
            yield _sse("error", {"message": str(exc), "assistant_run_id": run.id})
            yield _sse("done", {"assistant_run_id": run.id, "run_status": run.status})
        finally:
            if not finalized:
                try:
                    db.rollback()
                    current = db.get(AssistantRun, run.id)
                    if current is not None and current.status == AssistantRunStatus.RUNNING:
                        current.status = AssistantRunStatus.INTERRUPTED
                        current.error_message = "Assistant stream interrupted before completion"
                        db.commit()
                except Exception:
                    db.rollback()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _citations_from_tool_result(result: dict | None) -> list[dict]:
    if not isinstance(result, dict):
        return []
    citations: list[dict] = []
    if result.get("text_snapshot_id"):
        excerpt = (result.get("text_excerpt") or result.get("text") or "")[:1200]
        citations.append(
            {
                "type": "text_snapshot",
                "contract_id": result.get("contract_id"),
                "text_snapshot_id": result.get("text_snapshot_id"),
                "start_char": 0 if excerpt else None,
                "end_char": len(excerpt) if excerpt else None,
                "excerpt": excerpt,
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
    for cit in result.get("citations") or []:
        if isinstance(cit, dict) and (cit.get("excerpt") or cit.get("quote")):
            citations.append(
                {
                    "type": cit.get("type", "citation"),
                    "contract_id": result.get("contract_id"),
                    "excerpt": cit.get("excerpt") or cit.get("quote"),
                }
            )
    return citations


# Tools whose result is an intake request the user should be able to open. Their
# result carries {id, ref}; we persist those on the tool block so the trace can
# render an "Open REQ-…" link after reload (the live stream is replaced by these
# persisted blocks once the answer lands).
_REQUEST_LINK_TOOLS = {
    "create_intake_request",
    "get_intake_request",
    "start_intake_workflow",
    "advance_intake_workflow",
}


def _accumulate_block(blocks: list[dict], event: dict) -> None:
    """Build an ordered, persistable timeline of the assistant turn (content
    interleaved with tool steps) so the Mike-style trace survives reload."""
    name = event.get("event")
    payload = event.get("payload") or {}
    if name == "message_delta":
        text = payload.get("text", "")
        if not text:
            return
        if blocks and blocks[-1].get("type") == "content":
            blocks[-1]["text"] += text
        else:
            blocks.append({"type": "content", "text": text})
    elif name == "tool_started":
        blocks.append(
            {
                "type": "tool",
                "name": payload.get("tool_name", "tool"),
                "status": "running",
            }
        )
    elif name == "tool_finished":
        result = payload.get("result")
        for b in reversed(blocks):
            if b.get("type") == "tool" and b.get("status") == "running":
                b["status"] = "error" if payload.get("error") else "done"
                if isinstance(result, dict):
                    art = {
                        k: result.get(k)
                        for k in (
                            "artifact_type",
                            "contract_id",
                            "edits",
                            "summary",
                        )
                        if result.get(k) is not None
                    }
                    if (
                        b.get("name") in _REQUEST_LINK_TOOLS
                        and isinstance(result.get("id"), str)
                        and isinstance(result.get("ref"), str)
                    ):
                        art["request_id"] = result["id"]
                        art["request_ref"] = result["ref"]
                    if art:
                        b["artifact"] = art
                break


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
    if artifact_type in {"assistant_edit", "playbook_redline"}:
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
    if result.get("playbooks"):
        events.append(
            {
                "event": "playbooks_offered",
                "payload": {"playbooks": result["playbooks"]},
            }
        )
    return events
