"""Thin router for the approval_chains domain (feature 004). No business
logic here — every handler is a one-line call into ``app.approval_chains.
service``. Permissions are exactly the strings frozen in plan.md's API
contract table. NOT registered in ``backend/app/main.py`` by this task
(T015's job) — this module only needs to define the router object correctly.

``require_screen_level`` is deliberately NOT added to these routes: feature
003's FR-17 tranche 1 does not include ``/approvals``, and this feature does
not silently extend that tranche.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.approval_chains import service
from app.approval_chains.schemas import (
    ChainBlockedResponse,
    ChainDecisionPayload,
    ChainDefinitionCreate,
    ChainDefinitionResponse,
    ChainDefinitionUpdate,
    ChainHistoryEntry,
    ChainInstanceCreate,
    ChainInstanceDetailResponse,
    ChainInstanceSummary,
    ChainRecalculatePayload,
    ChainStepCreate,
    ChainStepResponse,
    ChainStepRuleCreate,
    ChainStepRuleResponse,
    ChainStepRuleUpdate,
    ChainStepUpdate,
    ConditionFieldCatalogResponse,
)
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/approval-chains", tags=["approval-chains"])

_READ = require_permission("approval_chain:read")
_MANAGE = require_permission("approval_chain:manage")
_DECIDE = require_permission("approval_chain:decide")
_RECALCULATE = require_permission("approval_chain:recalculate")
_CONTRACT_APPROVE = require_permission("contract:approve")


# ---------------------------------------------------------------------------
# Condition field catalog
# ---------------------------------------------------------------------------


@router.get("/fields", response_model=ConditionFieldCatalogResponse)
def get_fields_route(
    module: str = "contract",
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.get_field_catalog(module)


# ---------------------------------------------------------------------------
# Chain definitions
# ---------------------------------------------------------------------------


@router.get("/definitions", response_model=list[ChainDefinitionResponse])
def list_definitions_route(
    module: str | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.list_definitions(db, actor=current_user, module=module, include_inactive=include_inactive)


@router.post("/definitions", response_model=ChainDefinitionResponse, status_code=status.HTTP_201_CREATED)
def create_definition_route(
    payload: ChainDefinitionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.create_definition(db, actor=current_user, payload=payload)


@router.patch("/definitions/{definition_id}", response_model=ChainDefinitionResponse)
def update_definition_route(
    definition_id: str,
    payload: ChainDefinitionUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.update_definition(db, actor=current_user, definition_id=definition_id, payload=payload)


@router.delete("/definitions/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_definition_route(
    definition_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    service.delete_definition(db, actor=current_user, definition_id=definition_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Chain steps
# ---------------------------------------------------------------------------


@router.post(
    "/definitions/{definition_id}/steps",
    response_model=ChainStepResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_step_route(
    definition_id: str,
    payload: ChainStepCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.create_step(db, actor=current_user, definition_id=definition_id, payload=payload)


@router.patch("/steps/{step_id}", response_model=ChainStepResponse)
def update_step_route(
    step_id: str,
    payload: ChainStepUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.update_step(db, actor=current_user, step_id=step_id, payload=payload)


@router.delete("/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_step_route(
    step_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    service.delete_step(db, actor=current_user, step_id=step_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Chain step rules
# ---------------------------------------------------------------------------


@router.post(
    "/steps/{step_id}/rules", response_model=ChainStepRuleResponse, status_code=status.HTTP_201_CREATED
)
def create_rule_route(
    step_id: str,
    payload: ChainStepRuleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.create_rule(db, actor=current_user, step_id=step_id, payload=payload)


@router.patch("/rules/{rule_id}", response_model=ChainStepRuleResponse)
def update_rule_route(
    rule_id: str,
    payload: ChainStepRuleUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.update_rule(db, actor=current_user, rule_id=rule_id, payload=payload)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule_route(
    rule_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    service.delete_rule(db, actor=current_user, rule_id=rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Chain instances
# ---------------------------------------------------------------------------


@router.post("/instances", response_model=ChainInstanceDetailResponse, status_code=status.HTTP_201_CREATED)
def create_instance_route(
    payload: ChainInstanceCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_CONTRACT_APPROVE),
):
    instance = service.create_instance(db, actor=current_user, payload=payload)
    return service.get_instance_detail(db, actor=current_user, instance_id=instance.id)


@router.get("/instances", response_model=list[ChainInstanceSummary])
def list_instances_route(
    module: str | None = None,
    module_record_id: str | None = None,
    status_: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
):
    return service.list_instances(
        db,
        actor=current_user,
        module=module,
        module_record_id=module_record_id,
        status_filter=status_,
        limit=limit,
        offset=offset,
    )


@router.get("/instances/{instance_id}", response_model=ChainInstanceDetailResponse)
def get_instance_route(
    instance_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
):
    return service.get_instance_detail(db, actor=current_user, instance_id=instance_id)


@router.get("/instances/{instance_id}/history", response_model=list[ChainHistoryEntry])
def get_instance_history_route(
    instance_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
):
    return service.get_history(db, actor=current_user, instance_id=instance_id)


@router.get("/instances/{instance_id}/blocked", response_model=ChainBlockedResponse)
def get_instance_blocked_route(
    instance_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.get_blocked(db, actor=current_user, instance_id=instance_id)


@router.post(
    "/instances/{instance_id}/requirements/{requirement_id}/decision",
    response_model=ChainInstanceDetailResponse,
)
def decide_requirement_route(
    instance_id: str,
    requirement_id: str,
    payload: ChainDecisionPayload,
    db: Session = Depends(get_db),
    current_user=Depends(_DECIDE),
):
    instance = service.record_decision(
        db, actor=current_user, instance_id=instance_id, requirement_id=requirement_id, payload=payload
    )
    return service.get_instance_detail(db, actor=current_user, instance_id=instance.id)


@router.post("/instances/{instance_id}/recalculate", response_model=ChainInstanceDetailResponse)
def recalculate_instance_route(
    instance_id: str,
    payload: ChainRecalculatePayload,
    db: Session = Depends(get_db),
    current_user=Depends(_RECALCULATE),
):
    instance = service.recalculate(db, actor=current_user, instance_id=instance_id, payload=payload)
    return service.get_instance_detail(db, actor=current_user, instance_id=instance.id)
