from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.core.deps import require_permission
from app.prompt_library.dependencies import get_prompt_library_service
from app.prompt_library.service import PromptLibraryService

router = APIRouter(prefix="/prompt-library", tags=["prompt-library"])


class PromptCreate(BaseModel):
    name: str
    workflow_type: str = "assistant"
    visibility: str = "private"
    description: str | None = None
    definition: dict = Field(default_factory=dict)


class PromptUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    definition: dict | None = None
    visibility: str | None = None
    shared_user_ids: list[str] | None = None
    note: str | None = None


class WorkflowLaunch(BaseModel):
    # How the prompt was used, for analytics: "assistant" | "contract" | "tabular".
    mode: str | None = None
    contract_id: str | None = None


@router.get("")
def list_workflows(
    current_user=Depends(require_permission("workflow:read")),
    service: PromptLibraryService = Depends(get_prompt_library_service),
):
    return service.list_workflows(current_user=current_user)


@router.get("/analytics")
def workflow_analytics(
    current_user=Depends(require_permission("workflow:read")),
    service: PromptLibraryService = Depends(get_prompt_library_service),
):
    return service.workflow_analytics(current_user=current_user)


@router.post("")
def create_workflow(
    payload: PromptCreate,
    current_user=Depends(require_permission("workflow:create")),
    service: PromptLibraryService = Depends(get_prompt_library_service),
):
    return service.create_workflow(payload=payload, current_user=current_user)


@router.patch("/{workflow_id}")
def update_workflow(
    workflow_id: str,
    payload: PromptUpdate,
    current_user=Depends(require_permission("workflow:update")),
    service: PromptLibraryService = Depends(get_prompt_library_service),
):
    return service.update_workflow(workflow_id=workflow_id, payload=payload, current_user=current_user)


@router.get("/{workflow_id}/versions")
def list_workflow_versions(
    workflow_id: str,
    current_user=Depends(require_permission("workflow:read")),
    service: PromptLibraryService = Depends(get_prompt_library_service),
):
    return service.list_workflow_versions(workflow_id=workflow_id, current_user=current_user)


@router.post("/{workflow_id}/versions/{version_id}/revert")
def revert_workflow_version(
    workflow_id: str,
    version_id: str,
    current_user=Depends(require_permission("workflow:update")),
    service: PromptLibraryService = Depends(get_prompt_library_service),
):
    return service.revert_workflow_version(
        workflow_id=workflow_id, version_id=version_id, current_user=current_user
    )


@router.post("/{workflow_id}/launch", status_code=status.HTTP_204_NO_CONTENT)
def launch_workflow(
    workflow_id: str,
    payload: WorkflowLaunch,
    current_user=Depends(require_permission("workflow:read")),
    service: PromptLibraryService = Depends(get_prompt_library_service),
):
    """Best-effort usage ping recorded when a prompt is used. Works for both
    custom and built-in prompts (audit_log.resource_id is a free string)."""
    service.launch_workflow(workflow_id=workflow_id, payload=payload, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
