"""Org-scoped fetch/guard helpers for the menu_security domain.

Mirrors the shape of ``app.org_structure.access`` (feature 002). ``Screen`` is
a platform-wide structural catalog (FR-24) so ``get_screen_or_404`` carries no
org filter; ``RoleScreenAccess`` grants and ``Role`` lookups ARE org-scoped
(AC-13/AC-14 — a role or grant from another organization is never visible).
Org-unit lookups reuse ``app.org_structure.access.get_org_unit_or_404``
directly rather than duplicating that logic here.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Role
from app.menu_security.models import ActionLevel, RoleScreenAccess, Screen

__all__ = [
    "get_action_level_or_422",
    "get_grant_or_404",
    "get_org_role_or_404",
    "get_screen_or_404",
]


def get_screen_or_404(db: Session, *, screen_id: str | None = None, code: str | None = None) -> Screen:
    """Fetch a ``Screen`` by ``screen_id`` or ``code`` (exactly one expected).

    Platform-wide — NO org filter (FR-24: the screen catalog is shared across
    the platform). 404s when missing or soft-deleted.
    """
    if screen_id is None and code is None:
        raise HTTPException(404, "Screen not found")
    stmt = select(Screen).where(Screen.deleted_at.is_(None))
    if screen_id is not None:
        stmt = stmt.where(Screen.id == screen_id)
    if code is not None:
        stmt = stmt.where(Screen.code == code)
    screen = db.execute(stmt).scalar_one_or_none()
    if screen is None:
        raise HTTPException(404, "Screen not found")
    return screen


def get_action_level_or_422(db: Session, code: str) -> ActionLevel:
    """Fetch an ``ActionLevel`` by ``code``. 422s (not 404) when the code is
    outside the four defined levels — this is a validation failure on the
    caller's requested level, per spec's edge case, not a missing-resource
    lookup.
    """
    stmt = select(ActionLevel).where(ActionLevel.code == code, ActionLevel.deleted_at.is_(None))
    action_level = db.execute(stmt).scalar_one_or_none()
    if action_level is None:
        raise HTTPException(422, "Unknown action level")
    return action_level


def get_grant_or_404(db: Session, org_id: str, grant_id: str) -> RoleScreenAccess:
    """Fetch a ``RoleScreenAccess`` grant scoped to ``org_id``. 404s when
    missing or belonging to another organization (AC-14) — including
    already-revoked grants, since callers that need to inspect a revoked
    grant should pass ``include_deleted`` semantics explicitly rather than
    silently succeeding here.
    """
    stmt = select(RoleScreenAccess).where(
        RoleScreenAccess.id == grant_id, RoleScreenAccess.org_id == org_id
    )
    grant = db.execute(stmt).scalar_one_or_none()
    if grant is None:
        raise HTTPException(404, "Screen-access grant not found")
    return grant


def get_org_role_or_404(db: Session, org_id: str, role_id: str) -> Role:
    """Fetch a ``Role`` scoped to ``org_id``. 404s when missing or belonging
    to a different organization — a role from another org is never visible
    or usable here (AC-13/AC-14).
    """
    stmt = select(Role).where(Role.id == role_id, Role.org_id == org_id)
    role = db.execute(stmt).scalar_one_or_none()
    if role is None:
        raise HTTPException(404, "Role not found")
    return role
