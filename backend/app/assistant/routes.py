from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.models import AssistantContractHandle, AssistantSession
from app.assistant.tools import ASSISTANT_TOOLS
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantSessionCreate(BaseModel):
    session_type: str = "general"
    title: str | None = None
    project_id: str | None = None
    contract_id: str | None = None
    tabular_review_id: str | None = None


@router.get("/tools")
def list_tools(current_user=Depends(require_permission("assistant:use"))):
    permissions = current_user.permission_values
    return {
        name: details
        for name, details in ASSISTANT_TOOLS.items()
        if details["permission"] in permissions
    }


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
