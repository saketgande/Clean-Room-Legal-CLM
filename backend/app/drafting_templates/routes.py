"""The drafting templates — read them, preview them, change them.

Reading needs ``playbook:read`` and changing needs ``playbook:update``: a
template is our standard paper, the other half of the playbook it must pass.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.deps import require_permission
from app.drafting_templates.dependencies import get_drafting_template_service
from app.drafting_templates.service import DraftingTemplateService, render_sample

router = APIRouter(prefix="/drafting-templates", tags=["drafting-templates"])


class TemplateSave(BaseModel):
    body: str = Field(max_length=200_000)
    note: str | None = Field(default=None, max_length=300)


class TemplateReset(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class TemplatePreview(BaseModel):
    body: str = Field(max_length=200_000)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("")
def list_templates(
    user=Depends(require_permission("playbook:read")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.list(org_id=user.org_id)


@router.get("/{key}")
def get_template(
    key: str,
    user=Depends(require_permission("playbook:read")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.get(org_id=user.org_id, key=key)


@router.get("/{key}/versions")
def template_versions(
    key: str,
    user=Depends(require_permission("playbook:read")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.versions(org_id=user.org_id, key=key)


@router.post("/{key}/preview")
def preview_template(key: str, payload: TemplatePreview, _user=Depends(require_permission("playbook:read"))):
    """The template filled with example values — nothing is saved."""
    return {"text": render_sample(payload.body)}


@router.put("/{key}")
def save_template(
    key: str,
    payload: TemplateSave,
    request: Request,
    user=Depends(require_permission("playbook:update")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.save(actor=user, key=key, body=payload.body, note=payload.note, request_id=_rid(request))


@router.post("/{key}/reset")
def reset_template(
    key: str,
    payload: TemplateReset,
    request: Request,
    user=Depends(require_permission("playbook:update")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.reset(actor=user, key=key, note=payload.note, request_id=_rid(request))
