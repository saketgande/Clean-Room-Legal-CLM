"""HTTP surface for the workflow engine: flow definitions (CRUD + builder) and
flow runs (start / advance / human-step / refresh)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.intake.models import IntakeRequest
from app.workflows import service
from app.workflows.builtin import seed_builtin_flows
from app.workflows.models import Workflow, WorkflowRun

router = APIRouter(prefix="/workflows", tags=["workflows"])


class FlowPayload(BaseModel):
    name: str
    description: str | None = None
    enabled: bool | None = None
    eval_order: int | None = None
    criteria: dict | None = None
    steps: list[dict] | None = None


class StartPayload(BaseModel):
    request_id: str
    flow_id: str | None = None


class CompleteStepPayload(BaseModel):
    note: str | None = None
    step_idx: int | None = None  # which step (for parallel groups); default = current


class ReturnPayload(BaseModel):
    to_idx: int | None = None  # None = the step's configured return_to target
    note: str | None = None


class CommentPayload(BaseModel):
    text: str
    idx: int | None = None


def _request_id(http_request: Request) -> str | None:
    return getattr(http_request.state, "request_id", None)


def _get_flow(db: Session, org_id: str, flow_id: str) -> Workflow:
    f = db.get(Workflow, flow_id)
    if f is None or f.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return f


def _authorize_run(db: Session, run: WorkflowRun, user) -> WorkflowRun:
    """Org scope plus the linked contract's row-level gate (ethical walls + MAC
    clearance), so every run route enforces what /runs/by-contract does (M1)."""
    if run.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    if run.contract_id:
        get_contract_for_user(db, contract_id=run.contract_id, user=user)
    return run


def _get_run(db: Session, user, run_id: str) -> WorkflowRun:
    # Lock the run row FOR UPDATE (M3): the frontend pump and a manual
    # refresh/complete-step can fire near-simultaneously; without this both read
    # the same current_index and execute the step twice (e.g. two contracts
    # drafted). The second caller blocks until the first commits, then sees the
    # advanced state. Only the mutating routes (complete-step, refresh) use this.
    r = db.get(WorkflowRun, run_id, with_for_update=True)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return _authorize_run(db, r, user)


# ---- flow definitions -----------------------------------------------------

@router.get("")
def list_flows(db: Session = Depends(get_db), current_user=Depends(require_permission("workflow:read"))):
    return [service.serialize_flow(f) for f in service.list_flows(db, org_id=current_user.org_id)]


@router.get("/lifecycle")
def lifecycle(request_id: str | None = None, contract_id: str | None = None, db: Session = Depends(get_db),
              current_user=Depends(require_permission("intake:read"))):
    """The lifecycle rows that aren't workflow steps (request events, signers,
    obligations, renewal, expiry), keyed by stage."""
    from sqlalchemy import select

    from app.intake.service import get_request
    from app.workflows.lifecycle_rows import lifecycle_rows

    request = get_request(db, user=current_user, request_id=request_id) if request_id else None
    contract_id = contract_id or (request.contract_id if request is not None else None)
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user) if contract_id else None
    if request is None and contract is not None:
        request = db.scalars(select(IntakeRequest).where(
            IntakeRequest.org_id == current_user.org_id, IntakeRequest.contract_id == contract.id,
        ).order_by(IntakeRequest.submitted_at.desc())).first()
    run = (service.get_run_for_request(db, request_id=request.id, org_id=current_user.org_id)
           if request is not None else None)
    return lifecycle_rows(db, request=request, contract=contract, run=run)


@router.get("/{flow_id}")
def get_flow(flow_id: str, db: Session = Depends(get_db),
             current_user=Depends(require_permission("workflow:read"))):
    return service.serialize_flow(_get_flow(db, current_user.org_id, flow_id))


@router.post("/seed")
def seed(db: Session = Depends(get_db), current_user=Depends(require_permission("workflow:create"))):
    added = seed_builtin_flows(db, org_id=current_user.org_id, actor_id=current_user.id)
    return {"added": added, "flows": [service.serialize_flow(f) for f in service.list_flows(db, org_id=current_user.org_id)]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_flow(payload: FlowPayload, db: Session = Depends(get_db),
                current_user=Depends(require_permission("workflow:create"))):
    f = service.create_flow(db, actor=current_user, payload=payload.model_dump(exclude_none=True))
    return service.serialize_flow(f)


@router.patch("/{flow_id}")
def update_flow(flow_id: str, payload: FlowPayload, http_request: Request, db: Session = Depends(get_db),
                current_user=Depends(require_permission("workflow:update"))):
    f = _get_flow(db, current_user.org_id, flow_id)
    f = service.update_flow(db, actor=current_user, flow=f, payload=payload.model_dump(exclude_none=True),
                            request_id=_request_id(http_request))
    return service.serialize_flow(f)


# ---- flow runs ------------------------------------------------------------

@router.post("/start")
async def start(payload: StartPayload, http_request: Request, db: Session = Depends(get_db),
                current_user=Depends(require_permission("intake:read"))):
    request = db.get(IntakeRequest, payload.request_id)
    if request is None or request.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if request.contract_id:
        get_contract_for_user(db, contract_id=request.contract_id, user=current_user)
    flow = _get_flow(db, current_user.org_id, payload.flow_id) if payload.flow_id else None
    run = await service.start_flow(db, actor=current_user, request=request, flow=flow,
                                   request_id=_request_id(http_request))
    return service.serialize_run(db, _authorize_run(db, run, current_user))


class StartForContractPayload(BaseModel):
    contract_id: str
    flow_id: str | None = None


@router.post("/start-for-contract")
async def start_for_contract(payload: StartForContractPayload, http_request: Request,
                             db: Session = Depends(get_db),
                             current_user=Depends(require_permission("intake:read"))):
    contract = get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    flow = _get_flow(db, current_user.org_id, payload.flow_id) if payload.flow_id else None
    run = await service.start_flow_for_contract(db, actor=current_user, contract=contract, flow=flow,
                                                request_id=_request_id(http_request))
    return service.serialize_run(db, _authorize_run(db, run, current_user))


@router.get("/runs/by-request/{request_id}")
def run_for_request(request_id: str, db: Session = Depends(get_db),
                    current_user=Depends(require_permission("intake:read"))):
    run = service.get_run_for_request(db, request_id=request_id, org_id=current_user.org_id)
    return service.serialize_run(db, _authorize_run(db, run, current_user)) if run else None


@router.get("/runs/by-contract/{contract_id}")
def run_for_contract(contract_id: str, db: Session = Depends(get_db),
                     current_user=Depends(require_permission("contract:read"))):
    # Row-level access gate (M1): ethical walls + MAC clearance, parity with every
    # other contract-derived read — the capability + org scope alone let a walled/
    # under-cleared user read a matter's governance run. Raises 404 if barred.
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    run = service.get_run_for_contract(db, contract_id=contract_id, org_id=current_user.org_id)
    return service.serialize_run(db, run) if run else None


@router.post("/runs/{run_id}/complete-step")
async def complete_step(run_id: str, payload: CompleteStepPayload, http_request: Request,
                        db: Session = Depends(get_db),
                        current_user=Depends(require_permission("intake:read"))):
    run = _get_run(db, current_user, run_id)
    service.complete_human_step(db, run=run, actor=current_user, note=payload.note, step_idx=payload.step_idx,
                                request_id=_request_id(http_request))
    run = await service.advance_run(db, run=run, actor=current_user)
    return service.serialize_run(db, run)


@router.post("/runs/{run_id}/refresh")
async def refresh(run_id: str, db: Session = Depends(get_db),
                  current_user=Depends(require_permission("intake:read"))):
    run = _get_run(db, current_user, run_id)
    run = await service.refresh_run(db, run=run, actor=current_user)
    return service.serialize_run(db, run)


@router.post("/runs/{run_id}/return")
async def return_step(run_id: str, payload: ReturnPayload, http_request: Request,
                      db: Session = Depends(get_db),
                      current_user=Depends(require_permission("intake:read"))):
    run = _get_run(db, current_user, run_id)
    run = await service.return_run(db, run=run, actor=current_user, to_idx=payload.to_idx, note=payload.note,
                                   request_id=_request_id(http_request))
    return service.serialize_run(db, run)


@router.post("/runs/{run_id}/comment")
def comment(run_id: str, payload: CommentPayload, db: Session = Depends(get_db),
            current_user=Depends(require_permission("intake:read"))):
    run = _get_run(db, current_user, run_id)
    service.add_run_comment(db, run=run, actor=current_user, text=payload.text, idx=payload.idx)
    db.commit()
    db.refresh(run)
    return service.serialize_run(db, run)
