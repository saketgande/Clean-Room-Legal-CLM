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
from app.assistant.models import AssistantContractHandle, AssistantMessage, AssistantRun, AssistantSession
from app.core.deps import get_db, require_permission
from app.core.enums import AssistantRunStatus

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


@router.post("/sessions")
def create_session(
    payload: AssistantSessionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
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
    session = db.get(AssistantSession, session_id)
    if session is None or session.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")
    handles = db.scalars(
        select(AssistantContractHandle).where(AssistantContractHandle.session_id == session.id)
    ).all()
    return {"session": session, "contract_handles": handles}


@router.post("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    payload: AssistantStreamRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    session = db.get(AssistantSession, session_id)
    if session is None or session.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")

    for contract_id in payload.contract_ids:
        existing = db.scalar(
            select(AssistantContractHandle).where(
                AssistantContractHandle.org_id == current_user.org_id,
                AssistantContractHandle.session_id == session.id,
                AssistantContractHandle.contract_id == contract_id,
            )
        )
        if existing is None:
            handle_count = len(
                db.scalars(
                    select(AssistantContractHandle).where(AssistantContractHandle.session_id == session.id)
                ).all()
            )
            db.add(
                AssistantContractHandle(
                    org_id=current_user.org_id,
                    session_id=session.id,
                    contract_id=contract_id,
                    handle=f"contract-{handle_count}",
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
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
                yield _sse(event["event"], event["payload"])
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
                    citations=[],
                    metadata_json={"assistant_run_id": assistant_run.id},
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
                db.add(assistant_message)
                db.flush()
                assistant_run.assistant_message_id = assistant_message.id
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
