from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db, require_permission, require_screen_level
from app.core.rate_limit import limiter
from app.intake import copilot as copilot_mod
from app.intake import drafts as drafts_mod
from app.intake.dependencies import (
    get_drafting_service,
    get_gmail_sync_service,
    get_ingest_service,
    get_intake_service,
    get_screening_service,
    get_team_service,
)
from app.intake.drafting import DraftingService
from app.intake.gmail_sync import GmailSyncService
from app.intake.ingest import IngestService
from app.intake.schemas import (
    AssigneeResponse,
    CopilotFileRequest,
    CopilotTurnRequest,
    CopilotTurnResponse,
    DraftResponse,
    DraftSave,
    HandoffCreate,
    HandoffResponse,
    PartiesUpdate,
    PromoteRequest,
    RequestCreate,
    RequestResponse,
    RequestUpdate,
    TaskCreateReq,
    TaskResponse,
    TaskUpdateReq,
    TeamCreate,
    TeamResponse,
    TeamUpdate,
    TriageActionRequest,
)
from app.intake.screening import ScreeningService
from app.intake.service import IntakeService
from app.intake.service import claim_request as _claim_request
from app.intake.service import list_pool_requests as _list_pool_requests
from app.intake.teams import TeamService

router = APIRouter(prefix="/intake", tags=["intake"])

_CREATE = require_permission("intake:create")   # all employees — file + own tickets
_READ = require_permission("intake:read")        # the staff gate — queue + manage actions
_UPDATE = require_permission("intake:update")     # stage / handoff / tasks
_MANAGE = require_permission("admin_panel:access")  # admin config (teams)

# FR-10/FR-11/FR-17 tranche-1 screen-level gates — additive to the permission
# checks above, declared next to the router per plan.md's frozen shape.
_INTAKE_VIEW = require_screen_level("intake", "VIEW")
_INTAKE_ADD = require_screen_level("intake", "ADD")
_INTAKE_EDIT = require_screen_level("intake", "EDIT")
_INTAKE_DELETE = require_screen_level("intake", "DELETE")


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# ---- request forms ----------------------------------------------------------
# The nine forms' questions, labels, options and show-when rules. The wizard
# renders from these so the browser and filing validation can't disagree.

@router.get("/forms")
def list_forms(current_user=Depends(_CREATE)):
    from app.intake.agreement_forms import form_defs

    return list(form_defs())


# ---- drafts (agreement wizard "Save as Draft") ------------------------------
# Private to whoever saved them; anyone who may file a request may keep drafts.

@router.get("/drafts", response_model=list[DraftResponse])
def list_drafts(db: Session = Depends(get_db), current_user=Depends(_CREATE)):
    return drafts_mod.list_mine(db, actor=current_user)


@router.post("/drafts", response_model=DraftResponse, status_code=status.HTTP_201_CREATED)
def create_draft(payload: DraftSave, db: Session = Depends(get_db), current_user=Depends(_CREATE)):
    return drafts_mod.save(db, actor=current_user, payload=payload)


@router.put("/drafts/{draft_id}", response_model=DraftResponse)
def update_draft(draft_id: str, payload: DraftSave, db: Session = Depends(get_db),
                 current_user=Depends(_CREATE)):
    return drafts_mod.save(db, actor=current_user, payload=payload, draft_id=draft_id)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(draft_id: str, db: Session = Depends(get_db), current_user=Depends(_CREATE)):
    drafts_mod.delete(db, actor=current_user, draft_id=draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- requests -------------------------------------------------------------

@router.post("/requests", response_model=RequestResponse, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: RequestCreate,
    request: Request,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_CREATE),
    _screen=Depends(_INTAKE_ADD),
):
    return intake_service.create_request(
        actor=current_user, payload=payload, request_id=_req_id(request),
        defer_triage=True,  # AI triage, screening and workflow autostart run in a background job
    )


@router.get("/requests", response_model=list[RequestResponse])
def list_requests(
    status_filter: str | None = None,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_READ),
):
    return intake_service.list_requests(user=current_user, status_filter=status_filter)


@router.get("/requests/mine", response_model=list[RequestResponse])
def list_my_requests(
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_CREATE),
):
    return intake_service.list_requests(user=current_user, mine=True)


@router.get("/requests/pool", response_model=list[RequestResponse])
def list_request_pool(
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
):
    return _list_pool_requests(db, user=current_user)


@router.get("/requests/{request_id}", response_model=RequestResponse)
def get_request(
    request_id: str,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_CREATE),
):
    # get_request enforces staff-read OR requester-owns
    r = intake_service.get_request(user=current_user, request_id=request_id)
    return intake_service.serialize_request(r)


@router.patch("/requests/{request_id}", response_model=RequestResponse)
def update_request(
    request_id: str,
    payload: RequestUpdate,
    request: Request,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
    _screen=Depends(_INTAKE_EDIT),
):
    return intake_service.update_request(
        actor=current_user, request_id=request_id, payload=payload,
        http_request_id=_req_id(request),
    )


@router.post("/requests/{request_id}/triage", response_model=RequestResponse)
def triage_request(
    request_id: str,
    payload: TriageActionRequest,
    request: Request,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_READ),
    _screen=Depends(_INTAKE_EDIT),
):
    return intake_service.record_triage_action(
        actor=current_user, request_id=request_id, payload=payload,
        http_request_id=_req_id(request),
    )


@router.post("/requests/{request_id}/assign-to-me", response_model=RequestResponse)
def assign_to_me(
    request_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
    _screen=Depends(_INTAKE_EDIT),
):
    return _claim_request(
        db, actor=current_user, request_id=request_id, http_request_id=_req_id(request),
    )


@router.post("/requests/{request_id}/suggest-flow", response_model=RequestResponse)
def suggest_flow(
    request_id: str,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_READ),
    _screen=Depends(_INTAKE_VIEW),
):
    """Re-run the Flow Router agent for this request; the suggestion lands on
    ai_triage.flow_suggestion. Assigning it is a separate one-click flows/start."""
    return intake_service.resuggest_flow(actor=current_user, request_id=request_id)


# ---- approval ladder ------------------------------------------------------

class _ApprovalPreview(BaseModel):
    type_label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20000)
    priority: str = "Medium"
    department: str | None = None
    field_values: dict = Field(default_factory=dict)


# Approvals start only from a workflow's Approval step; there is no manual
# submit route (deleted 2026-09-29).
@router.post("/approval-preview")
def approval_preview(payload: _ApprovalPreview,
                     intake_service: IntakeService = Depends(get_intake_service),
                     current_user=Depends(_CREATE)):
    """Who will approve a request before it is filed. Saves nothing."""
    return intake_service.preview_approvals(actor=current_user, payload=payload)


@router.get("/requests/{request_id}/approval-chain")
def approval_chain(
    request_id: str,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_READ),
):
    return intake_service.get_approval_chain(actor=current_user, request_id=request_id)


# ---- handoff / custody ----------------------------------------------------

@router.post("/requests/{request_id}/handoff", response_model=RequestResponse)
def create_handoff(
    request_id: str,
    payload: HandoffCreate,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
    _screen=Depends(_INTAKE_ADD),
):
    return intake_service.handoff(actor=current_user, request_id=request_id, payload=payload)


@router.get("/requests/{request_id}/handoffs", response_model=list[HandoffResponse])
def list_handoffs(
    request_id: str,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_CREATE),
):
    r = intake_service.get_request(user=current_user, request_id=request_id)
    return intake_service.list_handoffs(request=r)


# ---- tasks ----------------------------------------------------------------

@router.get("/requests/{request_id}/tasks", response_model=list[TaskResponse])
def list_tasks(
    request_id: str,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_CREATE),
):
    return intake_service.list_tasks(actor=current_user, request_id=request_id)


@router.post("/requests/{request_id}/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    request_id: str,
    payload: TaskCreateReq,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
    _screen=Depends(_INTAKE_ADD),
):
    return intake_service.create_task(actor=current_user, request_id=request_id, payload=payload)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    payload: TaskUpdateReq,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
    _screen=Depends(_INTAKE_EDIT),
):
    return intake_service.update_task(actor=current_user, task_id=task_id, payload=payload)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
    _screen=Depends(_INTAKE_DELETE),
):
    intake_service.delete_task(actor=current_user, task_id=task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tasks/{task_id}/effort", response_model=TaskResponse)
def log_effort(
    task_id: str,
    minutes: int,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
    _screen=Depends(_INTAKE_EDIT),
):
    return intake_service.log_effort(actor=current_user, task_id=task_id, minutes=minutes)


# ---- my-work + assignees --------------------------------------------------

@router.get("/my-work")
def my_work(
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_READ),
):
    return intake_service.my_work(user=current_user)


@router.get("/assignees", response_model=list[AssigneeResponse])
def list_assignees(
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
):
    return intake_service.list_assignees(org_id=current_user.org_id)


# ---- promote --------------------------------------------------------------

@router.post("/requests/{request_id}/promote", response_model=RequestResponse)
def promote(
    request_id: str,
    payload: PromoteRequest,
    request: Request,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_READ),
    _screen=Depends(_INTAKE_EDIT),
):
    return intake_service.promote(actor=current_user, request_id=request_id, payload=payload,
                           http_request_id=_req_id(request))


@router.post("/requests/{request_id}/draft-contract", response_model=RequestResponse)
async def draft_contract(
    request_id: str,
    request: Request,
    intake_service: IntakeService = Depends(get_intake_service),
    drafting_service: DraftingService = Depends(get_drafting_service),
    current_user=Depends(_READ),
    _screen=Depends(_INTAKE_EDIT),
):
    """Render a draft contract from an intake request and link it back — the
    intake → contract-lifecycle bridge. Idempotent."""
    r = intake_service.get_request(user=current_user, request_id=request_id)
    await drafting_service.draft_contract_for_request(
        actor=current_user, request=r, http_request_id=_req_id(request)
    )
    r = intake_service.get_request(user=current_user, request_id=request_id)
    return intake_service.serialize_request(r)


@router.post("/requests/{request_id}/ingest-attachment", response_model=RequestResponse)
async def ingest_attachment(
    request_id: str,
    request: Request,
    intake_service: IntakeService = Depends(get_intake_service),
    drafting_service: DraftingService = Depends(get_drafting_service),
    current_user=Depends(_READ),
    _screen=Depends(_INTAKE_EDIT),
):
    """Use the request's attached document as the contract (the 'review an
    existing contract' path) instead of drafting from a template. Idempotent."""
    r = intake_service.get_request(user=current_user, request_id=request_id)
    await drafting_service.ingest_attachment_as_contract(
        actor=current_user, request=r, http_request_id=_req_id(request)
    )
    r = intake_service.get_request(user=current_user, request_id=request_id)
    return intake_service.serialize_request(r)


# ---- SLA ------------------------------------------------------------------

@router.get("/requests/{request_id}/sla")
def sla_legs(
    request_id: str,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_CREATE),
):
    r = intake_service.get_request(user=current_user, request_id=request_id)
    return intake_service.build_sla_legs(request=r)


@router.post("/requests/{request_id}/pause", response_model=RequestResponse)
def set_pause(
    request_id: str,
    paused: bool = True,
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_UPDATE),
    _screen=Depends(_INTAKE_EDIT),
):
    return intake_service.set_pause(actor=current_user, request_id=request_id, paused=paused)


@router.get("/sla-ops")
def sla_ops(
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_READ),
):
    return intake_service.sla_ops_summary(org_id=current_user.org_id)


@router.post("/sla-scan")
def sla_scan(
    intake_service: IntakeService = Depends(get_intake_service),
    current_user=Depends(_MANAGE),
):
    return intake_service.run_sla_sweep(org_id=current_user.org_id)


# ---- teams (admin) ------------------------------------------------

@router.get("/teams", response_model=list[TeamResponse])
def list_teams(team_service: TeamService = Depends(get_team_service), current_user=Depends(_READ)):
    return team_service.list_teams(org_id=current_user.org_id)


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamCreate, team_service: TeamService = Depends(get_team_service), current_user=Depends(_MANAGE)):
    return team_service.create_team(actor=current_user, payload=payload)


@router.patch("/teams/{team_id}", response_model=TeamResponse)
def update_team(team_id: str, payload: TeamUpdate, team_service: TeamService = Depends(get_team_service), current_user=Depends(_MANAGE)):
    return team_service.update_team(actor=current_user, team_id=team_id, payload=payload)


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: str, team_service: TeamService = Depends(get_team_service), current_user=Depends(_MANAGE)):
    team_service.delete_team(actor=current_user, team_id=team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- copilot (conversational filing) --------------------------------------

@router.post("/copilot/turn", response_model=CopilotTurnResponse)
def copilot_turn(payload: CopilotTurnRequest, current_user=Depends(_CREATE),
                  _screen=Depends(_INTAKE_VIEW)):
    return copilot_mod.turn(payload.messages, payload.user_message)


@router.post("/copilot/file", response_model=RequestResponse, status_code=status.HTTP_201_CREATED)
def copilot_file(payload: CopilotFileRequest, request: Request,
                 intake_service: IntakeService = Depends(get_intake_service), current_user=Depends(_CREATE), _screen=Depends(_INTAKE_ADD)):
    return intake_service.file_from_copilot(actor=current_user, payload=payload,
                                     request_id=_req_id(request))


# ---- gap-fill: channels, screening, documents, agent ops -------------------
# Public webhooks authenticate via shared secret / HMAC (fail-closed in prod)
# and are rate-limited; everything else uses the normal permission gates.

import base64 as _b64

from fastapi import Body, Header

from app.intake import ingest as ingest_mod  # pure webhook-auth helpers only


@router.post("/email-webhook", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.rate_limit_intake_webhook)
def email_webhook(
    request: Request,
    payload: dict = Body(...),
    x_intake_secret: str | None = Header(default=None),
    ingest_service: IngestService = Depends(get_ingest_service),
):
    """Inbound email → intake request. curl-demoable:
    {from_email, subject, body, external_message_id}.

    The shared secret authenticates the *relay*, not the sender: `from_email`
    is still whatever the message claimed. A relay that wants the request
    attributed to that address must forward the receiving server's
    `authentication_results` header with it; without one the sender is treated
    as unverified and the request files under the fallback owner.
    """
    ingest_mod.check_webhook_secret(x_intake_secret)
    ext = str(payload.get("external_message_id") or "").strip()
    if not ext:
        from fastapi import HTTPException
        raise HTTPException(422, "external_message_id is required (idempotency key)")
    return ingest_service.ingest_message(
        source="email",
        from_email=(payload.get("from_email") or None),
        subject=str(payload.get("subject") or ""),
        body=str(payload.get("body") or ""),
        external_message_id=ext,
        auth_results=(payload.get("authentication_results") or None),
    )


@router.post("/teams-webhook")
@limiter.limit(settings.rate_limit_intake_webhook)
async def teams_webhook(request: Request, ingest_service: IngestService = Depends(get_ingest_service)):
    """Microsoft Teams outgoing-webhook bot (HMAC-verified)."""
    raw = await request.body()
    ingest_mod.verify_teams_hmac(raw, request.headers.get("authorization"))
    import json as _json
    activity = _json.loads(raw or b"{}")
    # Handling the activity files a request (AI triage): blocking work, so off the event loop.
    return await run_in_threadpool(ingest_service.handle_teams_activity, activity)


@router.post("/mailbox/poll")
def mailbox_poll(ingest_service: IngestService = Depends(get_ingest_service), current_user=Depends(_MANAGE)):
    """Manually trigger the M365 mailbox sweep (inert until INTAKE_GRAPH_* set)."""
    return ingest_service.poll_mailbox()


@router.post("/gmail-sync")
def gmail_sync(gmail_sync_service: GmailSyncService = Depends(get_gmail_sync_service), current_user=Depends(_MANAGE)):
    """Manually trigger a Gmail inbox sync (inert until INTAKE_GMAIL_* set)."""
    return gmail_sync_service.sync_gmail_inbox()


@router.post("/requests/{request_id}/screen")
def rescreen_request(request_id: str,
                     db: Session = Depends(get_db),
                     intake_service: IntakeService = Depends(get_intake_service),
                     screening_service: ScreeningService = Depends(get_screening_service),
                     current_user=Depends(_READ),_screen=Depends(_INTAKE_EDIT)):
    r = intake_service.get_request(user=current_user, request_id=request_id)
    result = screening_service.run_screening(r, actor_user_id=current_user.id)
    db.commit()
    return result


@router.post("/requests/{request_id}/documents", status_code=status.HTTP_201_CREATED)
def upload_document(request_id: str, payload: dict = Body(...),
                    intake_service: IntakeService = Depends(get_intake_service),
                    current_user=Depends(_CREATE)):
    """Attach a document ({filename, mime_type, content_b64}); text is extracted
    and folded into the request so agents read it."""
    try:
        content = _b64.b64decode(str(payload.get("content_b64") or ""), validate=True)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(422, "content_b64 is not valid base64") from None
    return intake_service.add_document(
        actor=current_user, request_id=request_id,
        filename=str(payload.get("filename") or "attachment"),
        mime_type=str(payload.get("mime_type") or "application/octet-stream"),
        content=content,
    )


@router.get("/requests/{request_id}/documents")
def list_request_documents(request_id: str, intake_service: IntakeService = Depends(get_intake_service),
                           current_user=Depends(_CREATE)):
    return intake_service.list_documents(actor=current_user, request_id=request_id)


@router.put("/requests/{request_id}/parties", response_model=RequestResponse)
def set_request_parties(request_id: str, payload: PartiesUpdate,
                        intake_service: IntakeService = Depends(get_intake_service),
                        current_user=Depends(_READ),_screen=Depends(_INTAKE_EDIT)):
    """Replace the request's parties (counterparty + adverse/related) and refresh the relationship note."""
    return intake_service.set_parties(actor=current_user, request_id=request_id, parties=payload.parties)
