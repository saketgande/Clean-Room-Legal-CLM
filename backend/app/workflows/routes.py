from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.core.enums import Visibility, WorkflowType
from app.workflows.builtin import builtin_workflows
from app.workflows.models import Workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowCreate(BaseModel):
    name: str
    workflow_type: str = "assistant"
    visibility: str = "private"
    description: str | None = None
    definition: dict = Field(default_factory=dict)


@router.get("")
def list_workflows(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow:read")),
):
    rows = db.scalars(
        select(Workflow).where(Workflow.org_id == current_user.org_id, Workflow.deleted_at.is_(None))
    ).all()
    # Built-ins are read-only and shown to every org, after the user's own.
    return [*rows, *builtin_workflows()]


@router.post("")
def create_workflow(
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow:create")),
):
    if payload.visibility == Visibility.SYSTEM_BUILTIN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "system_builtin workflows are reserved for built-in templates",
        )
    if payload.workflow_type not in {item.value for item in WorkflowType}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported workflow type")
    if payload.visibility not in {
        Visibility.PRIVATE,
        Visibility.SHARED_WITH_USERS,
        Visibility.ORG_WIDE,
    }:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported workflow visibility")
    workflow = Workflow(
        org_id=current_user.org_id,
        name=payload.name,
        workflow_type=payload.workflow_type,
        visibility=payload.visibility,
        description=payload.description,
        definition=payload.definition,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    return workflow
