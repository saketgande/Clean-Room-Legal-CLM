"""Thin routers for org units, role grants, and delegations (feature
002-org-hierarchy-rbac, T008). No business logic here — every handler is a
one-line call into ``app.org_structure.service``. Permissions are exactly the
strings frozen in plan.md's API contract table. Registered in
``backend/app/main.py`` alongside ``walls_router`` (T014, not this task).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.org_structure import service
from app.org_structure.schemas import (
    DelegationCreate,
    DelegationEligibilityEntry,
    DelegationResponse,
    OrgUnitCreate,
    OrgUnitDeleteResponse,
    OrgUnitResponse,
    OrgUnitUpdate,
    RoleGrantCreate,
    RoleGrantResponse,
)

org_units_router = APIRouter(prefix="/org-units", tags=["org-units"])
role_grants_router = APIRouter(prefix="/role-grants", tags=["role-grants"])
delegations_router = APIRouter(prefix="/delegations", tags=["delegations"])

_READ_ORG_UNITS = require_permission("org_unit:read")
_MANAGE_ORG_UNITS = require_permission("admin_panel:access")
_READ_ROLE_GRANTS = require_permission("user:read")
_MANAGE_ROLE_GRANTS = require_permission("user:update_role")
_MANAGE_DELEGATIONS = require_permission("delegation:manage")


# ---------------------------------------------------------------------------
# Org units
# ---------------------------------------------------------------------------


@org_units_router.get("", response_model=list[OrgUnitResponse])
def list_org_units_route(
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(_READ_ORG_UNITS),
):
    return service.list_org_units(db, actor=current_user, include_deleted=include_deleted)


@org_units_router.post("", response_model=OrgUnitResponse, status_code=status.HTTP_201_CREATED)
def create_org_unit_route(
    payload: OrgUnitCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_ORG_UNITS),
):
    return service.create_org_unit(db, actor=current_user, payload=payload)


@org_units_router.patch("/{org_unit_id}", response_model=OrgUnitResponse)
def update_org_unit_route(
    org_unit_id: str,
    payload: OrgUnitUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_ORG_UNITS),
):
    return service.update_org_unit(db, actor=current_user, org_unit_id=org_unit_id, payload=payload)


@org_units_router.delete("/{org_unit_id}", response_model=OrgUnitDeleteResponse)
def delete_org_unit_route(
    org_unit_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_ORG_UNITS),
):
    return service.delete_org_unit(db, actor=current_user, org_unit_id=org_unit_id)


# ---------------------------------------------------------------------------
# Role grants
# ---------------------------------------------------------------------------


@role_grants_router.get("", response_model=list[RoleGrantResponse])
def list_role_grants_route(
    user_id: str | None = None,
    org_unit_id: str | None = None,
    include_revoked: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(_READ_ROLE_GRANTS),
):
    return service.list_role_grants(
        db,
        actor=current_user,
        user_id=user_id,
        org_unit_id=org_unit_id,
        include_revoked=include_revoked,
    )


@role_grants_router.post(
    "", response_model=RoleGrantResponse, status_code=status.HTTP_201_CREATED
)
def create_role_grant_route(
    payload: RoleGrantCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_ROLE_GRANTS),
):
    return service.create_role_grant(db, actor=current_user, payload=payload)


@role_grants_router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_role_grant_route(
    grant_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_ROLE_GRANTS),
):
    service.revoke_role_grant(db, actor=current_user, grant_id=grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Delegations
# ---------------------------------------------------------------------------


@delegations_router.get("", response_model=list[DelegationResponse])
def list_delegations_route(
    direction: str = "mine",
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_DELEGATIONS),
):
    return service.list_delegations(db, actor=current_user, direction=direction)


@delegations_router.get("/eligibility", response_model=list[DelegationEligibilityEntry])
def delegation_eligibility_route(
    delegator_user_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_DELEGATIONS),
):
    return service.delegation_eligibility(
        db, actor=current_user, delegator_user_id=delegator_user_id
    )


@delegations_router.post("", response_model=DelegationResponse, status_code=status.HTTP_201_CREATED)
def create_delegation_route(
    payload: DelegationCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_DELEGATIONS),
):
    return service.create_delegation(db, actor=current_user, payload=payload)


@delegations_router.post("/{delegation_id}/revoke", response_model=DelegationResponse)
def revoke_delegation_route(
    delegation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_DELEGATIONS),
):
    return service.revoke_delegation(db, actor=current_user, delegation_id=delegation_id)
