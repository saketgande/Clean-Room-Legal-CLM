"""Thin routers for the menu tree, own-screen-access, action-levels/screens
reference data, and screen-access grant admin (feature
003-menu-screen-security, T007). No business logic here — every handler is a
one-line call into ``app.menu_security.service``. Permissions are exactly the
strings frozen in plan.md's API contract table. Registered in
``backend/app/main.py`` (T018, not this task).

There are deliberately NO menu-item or screen CRUD endpoints (FR-24) — the
screen/menu-item catalog is structural application data maintained exclusively
by Alembic migrations; ``GET /screens`` and ``GET /action-levels`` are
read-only pickers only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.menu_security import service
from app.menu_security.schemas import (
    ActionLevelResponse,
    MenuTreeResponse,
    MyScreenAccessResponse,
    ScreenGrantCreate,
    ScreenGrantResponse,
    ScreenGrantUpdate,
    ScreenResponse,
)

menu_router = APIRouter(prefix="/menu-tree", tags=["menu"])
action_levels_router = APIRouter(prefix="/action-levels", tags=["screens"])
screens_router = APIRouter(prefix="/screens", tags=["screens"])
screen_access_router = APIRouter(prefix="/screen-access", tags=["screen-access"])

_READ_MENU = require_permission("menu:read")
_READ_SCREEN_ACCESS = require_permission("screen_access:read")
_MANAGE_SCREEN_ACCESS = require_permission("screen_access:manage")


# ---------------------------------------------------------------------------
# Menu tree (FR-7, FR-8)
# ---------------------------------------------------------------------------


@menu_router.get("", response_model=MenuTreeResponse)
def get_menu_tree_route(
    org_unit_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(_READ_MENU),
):
    return service.get_menu_tree(db, actor=current_user, org_unit_id=org_unit_id)


# ---------------------------------------------------------------------------
# Own screen access (FR-9, FR-14) — mounted under /screen-access below
# ---------------------------------------------------------------------------


@screen_access_router.get("/me", response_model=MyScreenAccessResponse)
def get_my_screen_access_route(
    screen_code: str | None = None,
    org_unit_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(_READ_MENU),
):
    return service.get_my_screen_access(
        db, actor=current_user, screen_code=screen_code, org_unit_id=org_unit_id
    )


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


@action_levels_router.get("", response_model=list[ActionLevelResponse])
def list_action_levels_route(
    db: Session = Depends(get_db),
    current_user=Depends(_READ_MENU),
):
    return service.list_action_levels(db)


@screens_router.get("", response_model=list[ScreenResponse])
def list_screens_route(
    module: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(_READ_SCREEN_ACCESS),
):
    return service.list_screens(db, module=module)


# ---------------------------------------------------------------------------
# Screen-access grant admin (FR-22, FR-23, FR-25, FR-27)
# ---------------------------------------------------------------------------


@screen_access_router.get("/grants", response_model=list[ScreenGrantResponse])
def list_screen_grants_route(
    role_id: str | None = None,
    screen_id: str | None = None,
    org_unit_id: str | None = None,
    include_revoked: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(_READ_SCREEN_ACCESS),
):
    return service.list_screen_grants(
        db,
        actor=current_user,
        role_id=role_id,
        screen_id=screen_id,
        org_unit_id=org_unit_id,
        include_revoked=include_revoked,
    )


@screen_access_router.post(
    "/grants", response_model=ScreenGrantResponse, status_code=status.HTTP_201_CREATED
)
def create_screen_grant_route(
    payload: ScreenGrantCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_SCREEN_ACCESS),
):
    return service.create_screen_grant(db, actor=current_user, payload=payload)


@screen_access_router.patch("/grants/{grant_id}", response_model=ScreenGrantResponse)
def update_screen_grant_route(
    grant_id: str,
    payload: ScreenGrantUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_SCREEN_ACCESS),
):
    return service.update_screen_grant(db, actor=current_user, grant_id=grant_id, payload=payload)


@screen_access_router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_screen_grant_route(
    grant_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE_SCREEN_ACCESS),
):
    service.revoke_screen_grant(db, actor=current_user, grant_id=grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
