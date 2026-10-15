"""The drafting templates: read them, change them, check them against their playbook.

Reading needs ``playbook:read`` and changing needs ``playbook:update``: a template is our
standard paper, the other half of the playbook it must pass. Checking one (or all of them) needs
``playbook:run``, what running the playbook's review needs anywhere else. Nothing here adds a
permission (FR-68).

The static paths (``/options``, ``/check-all``) are registered before ``/{key}``: FastAPI matches
in order, and a type can never be called ``options`` or ``check-all`` (reserved keys).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.drafting_templates import checks
from app.drafting_templates.dependencies import get_drafting_template_service
from app.drafting_templates.schemas import (
    CheckAllResultOut,
    DraftingTemplateOut,
    DraftingTemplateSummaryOut,
    DraftingTemplateVersionOut,
    TemplateCheckOut,
    TemplateCheckRequest,
    TemplateMutationResultOut,
    TemplatePlaybookChange,
    TemplatePreviewOut,
    TemplatePreviewRequest,
    TemplateSaveRequest,
    TemplateTypeOptionsOut,
    TemplateVersionAction,
)
from app.drafting_templates.service import DraftingTemplateService, get_type, render_sample

router = APIRouter(prefix="/drafting-templates", tags=["drafting-templates"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# --- Static paths first -----------------------------------------------------------------------


@router.get("/options", response_model=TemplateTypeOptionsOut)
def template_options(
    user=Depends(require_permission("playbook:read")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    """What the pickers offer: this organisation's published playbooks and the forms a type can draft."""
    return service.options(org_id=user.org_id)


@router.post("/check-all", response_model=CheckAllResultOut, status_code=status.HTTP_202_ACCEPTED)
def check_all_templates(
    user=Depends(require_permission("playbook:run")),
    db: Session = Depends(get_db),
):
    """Queue a check of every active template that is not already being checked and has a
    playbook. One paid AI review each, run in the background."""
    return checks.check_all(db, user=user)


# --- Reading ----------------------------------------------------------------------------------


@router.get("", response_model=list[DraftingTemplateSummaryOut])
def list_templates(
    include_retired: bool = False,
    user=Depends(require_permission("playbook:read")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.list(org_id=user.org_id, include_retired=include_retired)


@router.get("/{key}", response_model=DraftingTemplateOut)
def get_template(
    key: str,
    user=Depends(require_permission("playbook:read")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.get(org_id=user.org_id, key=key)


@router.get("/{key}/versions", response_model=list[DraftingTemplateVersionOut])
def template_versions(
    key: str,
    user=Depends(require_permission("playbook:read")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.versions(org_id=user.org_id, key=key)


@router.post("/{key}/preview", response_model=TemplatePreviewOut)
def preview_template(key: str, payload: TemplatePreviewRequest, _user=Depends(require_permission("playbook:read"))):
    """The template filled with example values — nothing is saved."""
    return {"text": render_sample(payload.body)}


# --- Checks -----------------------------------------------------------------------------------


@router.post("/{key}/checks", response_model=TemplateCheckOut, status_code=status.HTTP_202_ACCEPTED)
def request_template_check(
    key: str,
    payload: TemplateCheckRequest | None = None,
    user=Depends(require_permission("playbook:run")),
    db: Session = Depends(get_db),
):
    """Queue the playbook's AI review of this template, as a contract drafted from it would get it.

    Answers at once with the recorded check; the review runs in the background (one paid AI call).
    ``body`` = the editor's unsaved text; left out, the saved wording is checked."""
    ttype = get_type(db, user.org_id, key)  # 404 before anything else
    check = checks.request_check(
        db, org_id=user.org_id, key=key, trigger="manual", actor_user_id=user.id,
        unsaved_body=payload.body if payload else None,
    )
    if check is None:  # nothing reviews this template (FR-9): no row, no job, no cost
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            checks.no_playbook_detail(db, org_id=user.org_id, ttype=ttype)
            or f"No published playbook covers {ttype.name}, so there is nothing to check it against.",
        )
    return checks.serialize_check(db, check, full=True)


@router.get("/{key}/checks", response_model=list[TemplateCheckOut])
def list_template_checks(
    key: str,
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(require_permission("playbook:read")),
    db: Session = Depends(get_db),
):
    """The checks of this template, newest first, unsaved ones included (flagged)."""
    return checks.list_checks(db, org_id=user.org_id, key=key, limit=limit)


@router.get("/{key}/checks/{check_id}", response_model=TemplateCheckOut)
def get_template_check(
    key: str,
    check_id: str,
    user=Depends(require_permission("playbook:read")),
    db: Session = Depends(get_db),
):
    return checks.get_check(db, org_id=user.org_id, key=key, check_id=check_id)


# --- Changing ---------------------------------------------------------------------------------


@router.put("/{key}", response_model=TemplateMutationResultOut)
def save_template(
    key: str,
    payload: TemplateSaveRequest,
    request: Request,
    user=Depends(require_permission("playbook:update")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.save(
        actor=user, key=key, body=payload.body, note=payload.note, expected_version=payload.expected_version,
        applied_findings=payload.applied_findings,
        acknowledge_missing_parties=payload.acknowledge_missing_parties, request_id=_rid(request),
    )


@router.post("/{key}/reset", response_model=TemplateMutationResultOut)
def reset_template(
    key: str,
    payload: TemplateVersionAction,
    request: Request,
    user=Depends(require_permission("playbook:update")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    return service.reset(
        actor=user, key=key, note=payload.note, expected_version=payload.expected_version,
        request_id=_rid(request),
    )


@router.post("/{key}/versions/{version}/restore", response_model=TemplateMutationResultOut)
def restore_template_version(
    key: str,
    version: int,
    payload: TemplateVersionAction,
    request: Request,
    user=Depends(require_permission("playbook:update")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    """Load an earlier version back as a new latest version; the earlier ones stay as they were."""
    return service.restore(
        actor=user, key=key, version=version, note=payload.note, expected_version=payload.expected_version,
        request_id=_rid(request),
    )


@router.put("/{key}/playbook", response_model=TemplateMutationResultOut)
def change_template_playbook(
    key: str,
    payload: TemplatePlaybookChange,
    request: Request,
    user=Depends(require_permission("playbook:update")),
    service: DraftingTemplateService = Depends(get_drafting_template_service),
):
    """Pin another published playbook to this template. Contracts already drafted keep theirs."""
    return service.set_playbook(
        actor=user, key=key, playbook_id=payload.playbook_id, note=payload.note, request_id=_rid(request),
    )
