import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.citations import validate_citation
from app.ai.confirmations import confirm_confirmation, reject_confirmation
from app.ai.controller import ai_controller
from app.ai.models import AICitation
from app.ai.schemas import CitationInput
from app.ai.tool_registry import tool_registry
from app.assistant.models import (
    AssistantContractHandle,
    AssistantMessage,
    AssistantRun,
    AssistantSession,
    AssistantToolCall,
)
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.service import get_contract_for_user
from app.core.config import settings
from app.core.deps import get_db, require_permission
from app.core.enums import AssistantRunStatus, AssistantSessionType
from app.core.rate_limit import limiter
from app.core.rbac import has_permission
from app.projects.access import get_project_for_user

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantSessionCreate(BaseModel):
    session_type: AssistantSessionType = AssistantSessionType.GENERAL
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
    q: str | None = None,
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
    if q and q.strip():
        # Title-only search would be useless here: many sessions share an
        # identical auto-generated title (e.g. every "Edit · <contract>"
        # chat opened against the same contract), so also match on the
        # actual conversation content.
        needle = f"%{q.strip()}%"
        matching_session_ids = select(AssistantMessage.session_id).where(
            AssistantMessage.org_id == current_user.org_id,
            AssistantMessage.content.ilike(needle),
        )
        query = query.where(
            or_(AssistantSession.title.ilike(needle), AssistantSession.id.in_(matching_session_ids))
        )
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


@router.post("/sessions/{session_id}/stream")
@limiter.limit(settings.rate_limit_assistant_stream)
async def stream_session(
    session_id: str,
    payload: AssistantStreamRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    _require_ai_tools(current_user)
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
                _persist_assistant_answer(
                    db,
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
            _persist_assistant_answer(
                db,
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
                _persist_assistant_answer(
                    db,
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
):
    _require_ai_tools(current_user)
    run = _get_run_for_user(db, assistant_run_id=assistant_run_id, current_user=current_user)

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
                _persist_assistant_answer(
                    db,
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
            _persist_assistant_answer(
                db,
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
                _persist_assistant_answer(
                    db,
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
        excerpt = (result.get("text_excerpt") or "")[:1200]
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


def _validate_and_store_assistant_citations(
    db: Session,
    *,
    org_id: str,
    assistant_run_id: str,
    current_user,
    raw_citations: list[dict],
) -> list[dict]:
    """Validate assistant citation excerpts against source contract text using the
    shared fuzzy validator, persist AICitation rows, and return enriched citations."""
    source_cache: dict[str, tuple[str, str | None, bool]] = {}

    def _source_for(contract_id: str) -> tuple[str, str | None, bool] | None:
        if contract_id in source_cache:
            return source_cache[contract_id]
        try:
            contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
        except Exception:
            source_cache[contract_id] = None
            return None
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
        entry = (
            (snapshot.text or "", snapshot.id, bool(snapshot.ocr_provider))
            if snapshot is not None
            else ("", None, False)
        )
        source_cache[contract_id] = entry
        return entry

    enriched: list[dict] = []
    for citation in raw_citations:
        quote = citation.get("excerpt")
        contract_id = citation.get("contract_id")
        if not quote or not contract_id:
            enriched.append({**citation, "validation_status": "not_applicable"})
            continue
        source = _source_for(contract_id)
        if not source or not source[0]:
            enriched.append({**citation, "validation_status": "unverified"})
            continue
        source_text, snapshot_id, is_ocr = source
        result = validate_citation(
            CitationInput(quote=quote),
            source_text,
            is_ocr=is_ocr,
        )
        db.add(
            AICitation(
                org_id=org_id,
                assistant_run_id=assistant_run_id,
                resource_type="contract",
                resource_id=contract_id,
                contract_id=contract_id,
                text_snapshot_id=snapshot_id,
                quote=quote,
                normalized_quote=result.normalized_quote,
                start_char=citation.get("start_char"),
                end_char=citation.get("end_char"),
                validation_status=result.validation_status,
                similarity_score=result.similarity_score,
                metadata_json={"message": result.message, "source": "assistant_tool_result"},
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
        )
        enriched.append(
            {
                **citation,
                "validation_status": result.validation_status,
                "similarity_score": result.similarity_score,
            }
        )
    return enriched


def _persist_assistant_answer(
    db: Session,
    *,
    org_id: str,
    session_id: str,
    run: AssistantRun,
    answer_parts: list[str],
    citations: list[dict],
    blocks: list[dict],
    current_user,
    extra_metadata: dict | None = None,
) -> None:
    """Persist whatever answer text was generated as an AssistantMessage, linking
    it to the run and any tool calls made during it.

    Called on success AND on interruption/failure: a stream that gets cut off
    after Claude already generated a real, useful partial answer must not throw
    that content away — the previous behavior silently discarded answer_parts
    whenever client_disconnected fired, leaving the user's question in history
    with no reply and no way to tell whether the assistant had said anything at
    all. Does not commit or touch run.status — callers set those to reflect
    why persistence happened (success vs. interrupted vs. failed).
    """
    answer = "".join(answer_parts)
    if not answer:
        return
    citations = _validate_and_store_assistant_citations(
        db,
        org_id=org_id,
        assistant_run_id=run.id,
        current_user=current_user,
        raw_citations=citations,
    )
    assistant_message = AssistantMessage(
        org_id=org_id,
        session_id=session_id,
        role="assistant",
        content=answer,
        citations=citations,
        metadata_json={
            "assistant_run_id": run.id,
            "blocks": blocks,
            **(extra_metadata or {}),
        },
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(assistant_message)
    db.flush()
    run.assistant_message_id = assistant_message.id
    for call in db.scalars(
        select(AssistantToolCall).where(
            AssistantToolCall.org_id == org_id,
            AssistantToolCall.assistant_run_id == run.id,
            AssistantToolCall.message_id.is_(None),
        )
    ):
        call.message_id = assistant_message.id


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
