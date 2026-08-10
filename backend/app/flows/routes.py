"""HTTP surface for the workflow engine: flow definitions (CRUD + builder) and
flow runs (start / advance / human-step / refresh)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.flows import service
from app.flows.builtin import seed_builtin_flows
from app.flows.models import Flow, FlowRun
from app.contracts.service import get_contract_for_user
from app.intake.models import IntakeRequest

router = APIRouter(prefix="/flows", tags=["flows"])


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


def _get_flow(db: Session, org_id: str, flow_id: str) -> Flow:
    f = db.get(Flow, flow_id)
    if f is None or f.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return f


def _get_run(db: Session, org_id: str, run_id: str) -> FlowRun:
    # Lock the run row FOR UPDATE (M3): the frontend pump and a manual
    # refresh/complete-step can fire near-simultaneously; without this both read
    # the same current_index and execute the step twice (e.g. two contracts
    # drafted). The second caller blocks until the first commits, then sees the
    # advanced state. Only the mutating routes (complete-step, refresh) use this.
    r = db.get(FlowRun, run_id, with_for_update=True)
    if r is None or r.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return r


# ---- flow definitions -----------------------------------------------------

@router.get("")
def list_flows(db: Session = Depends(get_db), current_user=Depends(require_permission("workflow:read"))):
    return [service.serialize_flow(f) for f in service.list_flows(db, org_id=current_user.org_id)]


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
def update_flow(flow_id: str, payload: FlowPayload, db: Session = Depends(get_db),
                current_user=Depends(require_permission("workflow:update"))):
    f = _get_flow(db, current_user.org_id, flow_id)
    f = service.update_flow(db, actor=current_user, flow=f, payload=payload.model_dump(exclude_none=True))
    return service.serialize_flow(f)


# ---- flow runs ------------------------------------------------------------

@router.post("/start")
async def start(payload: StartPayload, db: Session = Depends(get_db),
                current_user=Depends(require_permission("intake:read"))):
    request = db.get(IntakeRequest, payload.request_id)
    if request is None or request.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    flow = _get_flow(db, current_user.org_id, payload.flow_id) if payload.flow_id else None
    run = await service.start_flow(db, actor=current_user, request=request, flow=flow)
    return service.serialize_run(db, run)


@router.get("/runs/by-request/{request_id}")
def run_for_request(request_id: str, db: Session = Depends(get_db),
                    current_user=Depends(require_permission("intake:read"))):
    run = service.get_run_for_request(db, request_id=request_id, org_id=current_user.org_id)
    return service.serialize_run(db, run) if run else None


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
async def complete_step(run_id: str, payload: CompleteStepPayload, db: Session = Depends(get_db),
                        current_user=Depends(require_permission("intake:read"))):
    run = _get_run(db, current_user.org_id, run_id)
    service.complete_human_step(db, run=run, actor=current_user, note=payload.note)
    run = await service.advance_run(db, run=run, actor=current_user)
    return service.serialize_run(db, run)


@router.post("/runs/{run_id}/refresh")
async def refresh(run_id: str, db: Session = Depends(get_db),
                  current_user=Depends(require_permission("intake:read"))):
    run = _get_run(db, current_user.org_id, run_id)
    run = await service.refresh_run(db, run=run, actor=current_user)
    return service.serialize_run(db, run)
