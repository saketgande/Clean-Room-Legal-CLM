from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_permission
from app.intake import copilot as copilot_mod
from app.intake import routing as routing_mod
from app.intake import service
from app.intake import teams as teams_mod
from app.intake.schemas import (
    AssigneeResponse,
    BulkTriageRequest,
    CopilotFileRequest,
    CopilotTurnRequest,
    CopilotTurnResponse,
    HandoffCreate,
    HandoffResponse,
    KbCreate,
    KbResponse,
    KbUpdate,
    PartiesUpdate,
    PromoteRequest,
    RecommendationResponse,
    RequestCreate,
    RequestResponse,
    RequestTypeCreate,
    RequestTypeResponse,
    RequestTypeUpdate,
    RequestUpdate,
    RuleCreate,
    RuleResponse,
    RuleUpdate,
    TaskCreateReq,
    TaskResponse,
    TaskUpdateReq,
    TeamCreate,
    TeamResponse,
    TeamUpdate,
    TriageActionRequest,
)

router = APIRouter(prefix="/intake", tags=["intake"])

_CREATE = require_permission("intake:create")   # all employees — file + own tickets
_READ = require_permission("intake:read")        # staff-wide list/detail
_TRIAGE = require_permission("intake:triage")     # verdicts / cockpit / my-work
_UPDATE = require_permission("intake:update")     # stage / handoff / tasks
_MANAGE = require_permission("admin_panel:access")  # admin config (types, teams, rules)


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# ---- request types --------------------------------------------------------
# Reading types is needed to render the New Request form, so any filer may list.

@router.get("/request-types", response_model=list[RequestTypeResponse])
def list_request_types(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    return service.list_types(
        db, org_id=current_user.org_id, include_inactive=include_inactive
    )


@router.post("/request-types", response_model=RequestTypeResponse, status_code=status.HTTP_201_CREATED)
def create_request_type(
    payload: RequestTypeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.create_type(db, actor=current_user, payload=payload)


@router.patch("/request-types/{type_id}", response_model=RequestTypeResponse)
def update_request_type(
    type_id: str,
    payload: RequestTypeUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.update_type(db, actor=current_user, type_id=type_id, payload=payload)


@router.delete("/request-types/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request_type(
    type_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    service.delete_type(db, actor=current_user, type_id=type_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- requests -------------------------------------------------------------

@router.post("/requests", response_model=RequestResponse, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: RequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    return service.create_request(
        db, actor=current_user, payload=payload, request_id=_req_id(request)
    )


@router.get("/requests", response_model=list[RequestResponse])
def list_requests(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
):
    return service.list_requests(db, user=current_user, status_filter=status_filter)


@router.get("/requests/mine", response_model=list[RequestResponse])
def list_my_requests(
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    return service.list_requests(db, user=current_user, mine=True)


@router.get("/requests/{request_id}", response_model=RequestResponse)
def get_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    # get_request enforces staff-read OR requester-owns
    r = service.get_request(db, user=current_user, request_id=request_id)
    return service.serialize_request(db, r)


@router.patch("/requests/{request_id}", response_model=RequestResponse)
def update_request(
    request_id: str,
    payload: RequestUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.update_request(
        db, actor=current_user, request_id=request_id, payload=payload,
        http_request_id=_req_id(request),
    )


@router.post("/requests/{request_id}/triage", response_model=RequestResponse)
def triage_request(
    request_id: str,
    payload: TriageActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_TRIAGE),
):
    return service.record_triage_action(
        db, actor=current_user, request_id=request_id, payload=payload,
        http_request_id=_req_id(request),
    )


# ---- handoff / custody ----------------------------------------------------

@router.post("/requests/{request_id}/handoff", response_model=RequestResponse)
def create_handoff(
    request_id: str,
    payload: HandoffCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.handoff(db, actor=current_user, request_id=request_id, payload=payload)


@router.get("/requests/{request_id}/handoffs", response_model=list[HandoffResponse])
def list_handoffs(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    r = service.get_request(db, user=current_user, request_id=request_id)
    return service.list_handoffs(db, request=r)


# ---- tasks ----------------------------------------------------------------

@router.get("/requests/{request_id}/tasks", response_model=list[TaskResponse])
def list_tasks(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    return service.list_tasks(db, actor=current_user, request_id=request_id)


@router.post("/requests/{request_id}/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    request_id: str,
    payload: TaskCreateReq,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.create_task(db, actor=current_user, request_id=request_id, payload=payload)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    payload: TaskUpdateReq,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.update_task(db, actor=current_user, task_id=task_id, payload=payload)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    service.delete_task(db, actor=current_user, task_id=task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tasks/{task_id}/effort", response_model=TaskResponse)
def log_effort(
    task_id: str,
    minutes: int,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.log_effort(db, actor=current_user, task_id=task_id, minutes=minutes)


# ---- my-work + assignees --------------------------------------------------

@router.get("/my-work")
def my_work(
    db: Session = Depends(get_db),
    current_user=Depends(_TRIAGE),
):
    return service.my_work(db, user=current_user)


@router.get("/assignees", response_model=list[AssigneeResponse])
def list_assignees(
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.list_assignees(db, org_id=current_user.org_id)


# ---- recommendation / verdicts / promote ----------------------------------

@router.get("/requests/{request_id}/recommendation")
def get_recommendation(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    return service.get_recommendation(db, user=current_user, request_id=request_id)


@router.post("/requests/bulk-triage")
def bulk_triage(
    payload: BulkTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_TRIAGE),
):
    return {"results": service.bulk_triage(
        db, actor=current_user, ids=payload.ids, action=payload.action,
        http_request_id=_req_id(request))}


@router.post("/requests/{request_id}/promote", response_model=RequestResponse)
def promote(
    request_id: str,
    payload: PromoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_TRIAGE),
):
    return service.promote(db, actor=current_user, request_id=request_id, payload=payload,
                           http_request_id=_req_id(request))


@router.post("/requests/{request_id}/draft-contract", response_model=RequestResponse)
async def draft_contract(
    request_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_TRIAGE),
):
    """Render a draft contract from an intake request and link it back — the
    intake → contract-lifecycle bridge. Idempotent."""
    from app.intake.drafting import draft_contract_for_request

    r = service.get_request(db, user=current_user, request_id=request_id)
    await draft_contract_for_request(
        db, actor=current_user, request=r, http_request_id=_req_id(request)
    )
    r = service.get_request(db, user=current_user, request_id=request_id)
    return service.serialize_request(db, r)


# ---- SLA ------------------------------------------------------------------

@router.get("/requests/{request_id}/sla")
def sla_legs(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    r = service.get_request(db, user=current_user, request_id=request_id)
    return service.build_sla_legs(db, request=r)


@router.post("/requests/{request_id}/pause", response_model=RequestResponse)
def set_pause(
    request_id: str,
    paused: bool = True,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.set_pause(db, actor=current_user, request_id=request_id, paused=paused)


@router.get("/sla-ops")
def sla_ops(
    db: Session = Depends(get_db),
    current_user=Depends(_TRIAGE),
):
    return service.sla_ops_summary(db, org_id=current_user.org_id)


@router.post("/sla-scan")
def sla_scan(
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.run_sla_sweep(db, org_id=current_user.org_id)


# ---- teams / pools (admin) ------------------------------------------------

@router.get("/teams", response_model=list[TeamResponse])
def list_teams(db: Session = Depends(get_db), current_user=Depends(_TRIAGE)):
    return teams_mod.list_teams(db, org_id=current_user.org_id)


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamCreate, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return teams_mod.create_team(db, actor=current_user, payload=payload)


@router.patch("/teams/{team_id}", response_model=TeamResponse)
def update_team(team_id: str, payload: TeamUpdate, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return teams_mod.update_team(db, actor=current_user, team_id=team_id, payload=payload)


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: str, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    teams_mod.delete_team(db, actor=current_user, team_id=team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- routing rules (read=triage, write=admin per Part 0.14) ---------------

@router.get("/routing-rules", response_model=list[RuleResponse])
def list_rules(db: Session = Depends(get_db), current_user=Depends(_TRIAGE)):
    return routing_mod.list_rules(db, org_id=current_user.org_id)


@router.post("/routing-rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return routing_mod.create_rule(db, actor=current_user, payload=payload)


@router.patch("/routing-rules/{rule_id}", response_model=RuleResponse)
def update_rule(rule_id: str, payload: RuleUpdate, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return routing_mod.update_rule(db, actor=current_user, rule_id=rule_id, payload=payload)


@router.delete("/routing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: str, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    routing_mod.delete_rule(db, actor=current_user, rule_id=rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- knowledge base (read = any filer; write = admin) ---------------------

@router.get("/kb", response_model=list[KbResponse])
def list_kb(include_inactive: bool = False, db: Session = Depends(get_db), current_user=Depends(_CREATE)):
    return service.list_kb(db, org_id=current_user.org_id,
                           include_inactive=include_inactive and False)  # non-admins never see inactive


@router.get("/kb/all", response_model=list[KbResponse])
def list_kb_admin(db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return service.list_kb(db, org_id=current_user.org_id, include_inactive=True)


@router.post("/kb", response_model=KbResponse, status_code=status.HTTP_201_CREATED)
def create_kb(payload: KbCreate, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return service.create_kb(db, actor=current_user, payload=payload)


@router.patch("/kb/{kb_id}", response_model=KbResponse)
def update_kb(kb_id: str, payload: KbUpdate, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return service.update_kb(db, actor=current_user, kb_id=kb_id, payload=payload)


@router.delete("/kb/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kb(kb_id: str, db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    service.delete_kb(db, actor=current_user, kb_id=kb_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- pool ops -------------------------------------------------------------

@router.get("/pool-ops")
def pool_ops(days: int = 30, db: Session = Depends(get_db), current_user=Depends(_TRIAGE)):
    return service.pool_ops_summary(db, org_id=current_user.org_id, days=days)


# ---- copilot (conversational filing) --------------------------------------

@router.post("/copilot/turn", response_model=CopilotTurnResponse)
def copilot_turn(payload: CopilotTurnRequest, current_user=Depends(_CREATE)):
    return copilot_mod.turn(payload.messages, payload.user_message)


@router.post("/copilot/file", response_model=RequestResponse, status_code=status.HTTP_201_CREATED)
def copilot_file(payload: CopilotFileRequest, request: Request,
                 db: Session = Depends(get_db), current_user=Depends(_CREATE)):
    return service.file_from_copilot(db, actor=current_user, payload=payload,
                                     request_id=_req_id(request))


# ---- gap-fill: channels, screening, documents, agent ops -------------------
# Public webhooks authenticate via shared secret / HMAC (fail-closed in prod)
# and are rate-limited; everything else uses the normal permission gates.

import base64 as _b64

from fastapi import Body, Header

from app.intake import gmail_sync as gmail_sync_mod
from app.intake import ingest as ingest_mod
from app.intake import screening as screening_mod


@router.post("/email-webhook", status_code=status.HTTP_202_ACCEPTED)
def email_webhook(
    request: Request,
    payload: dict = Body(...),
    x_intake_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Inbound email → intake request. curl-demoable:
    {from_email, subject, body, external_message_id}."""
    ingest_mod.rate_limit(f"email:{request.client.host if request.client else 'x'}")
    ingest_mod.check_webhook_secret(x_intake_secret)
    ext = str(payload.get("external_message_id") or "").strip()
    if not ext:
        from fastapi import HTTPException
        raise HTTPException(422, "external_message_id is required (idempotency key)")
    return ingest_mod.ingest_message(
        db, source="email",
        from_email=(payload.get("from_email") or None),
        subject=str(payload.get("subject") or ""),
        body=str(payload.get("body") or ""),
        external_message_id=ext,
    )


@router.post("/teams-webhook")
async def teams_webhook(request: Request, db: Session = Depends(get_db)):
    """Microsoft Teams outgoing-webhook bot (HMAC-verified)."""
    raw = await request.body()
    ingest_mod.rate_limit(f"teams:{request.client.host if request.client else 'x'}")
    ingest_mod.verify_teams_hmac(raw, request.headers.get("authorization"))
    import json as _json
    activity = _json.loads(raw or b"{}")
    return ingest_mod.handle_teams_activity(db, activity)


@router.post("/mailbox/poll")
def mailbox_poll(db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    """Manually trigger the M365 mailbox sweep (inert until INTAKE_GRAPH_* set)."""
    return ingest_mod.poll_mailbox(db)


@router.post("/gmail-sync")
def gmail_sync(db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    """Manually trigger a Gmail inbox sync (inert until INTAKE_GMAIL_* set)."""
    return gmail_sync_mod.sync_gmail_inbox(db)


@router.post("/requests/{request_id}/screen")
def rescreen_request(request_id: str, db: Session = Depends(get_db),
                     current_user=Depends(_TRIAGE)):
    r = service.get_request(db, user=current_user, request_id=request_id)
    result = screening_mod.run_screening(db, r, actor_user_id=current_user.id)
    db.commit()
    return result


@router.post("/sanctions/refresh")
def sanctions_refresh(db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    """Pull the live OFAC SDN list (Treasury CSV) into the screening table."""
    return screening_mod.refresh_ofac(db, current_user.org_id)


@router.post("/requests/{request_id}/documents", status_code=status.HTTP_201_CREATED)
def upload_document(request_id: str, payload: dict = Body(...),
                    db: Session = Depends(get_db), current_user=Depends(_CREATE)):
    """Attach a document ({filename, mime_type, content_b64}); text is extracted
    and folded into the request so agents read it."""
    try:
        content = _b64.b64decode(str(payload.get("content_b64") or ""), validate=True)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(422, "content_b64 is not valid base64")
    return service.add_document(
        db, actor=current_user, request_id=request_id,
        filename=str(payload.get("filename") or "attachment"),
        mime_type=str(payload.get("mime_type") or "application/octet-stream"),
        content=content,
    )


@router.get("/requests/{request_id}/documents")
def list_request_documents(request_id: str, db: Session = Depends(get_db),
                           current_user=Depends(_CREATE)):
    return service.list_documents(db, actor=current_user, request_id=request_id)


@router.put("/requests/{request_id}/parties", response_model=RequestResponse)
def set_request_parties(request_id: str, payload: PartiesUpdate,
                        db: Session = Depends(get_db), current_user=Depends(_TRIAGE)):
    """Replace the request's parties (counterparty + adverse/related) and re-screen."""
    return service.set_parties(db, actor=current_user, request_id=request_id, parties=payload.parties)


@router.get("/agent-metrics")
def get_agent_metrics(db: Session = Depends(get_db), current_user=Depends(_TRIAGE)):
    return service.agent_metrics(db, current_user.org_id)
