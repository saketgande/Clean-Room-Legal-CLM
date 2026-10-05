"""Give an organisation the org unit, role grants and screen access it needs.

Migrations 0042/0043/0045 set these up only for organisations that already
existed when they ran. On a fresh install the organisation is created later
(first-admin setup), so it got none of them: the first admin held no role grant
(``User.roles`` is view-only, so assigning it stores nothing) and no role had
any screen, and Legal Intake, Contracts and every other guarded page stayed
hidden. ``ensure_org_access`` creates what's missing, the same way the
migrations did, and never changes what already exists — so it is safe to run
on every first-admin setup and as a repair (``python -m app.devtools
bootstrap-access``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Role, User, UserRoleGrant
from app.menu_security.catalog import _ALL, SCREEN_CATALOG
from app.menu_security.models import ActionLevel, RoleScreenAccess, Screen
from app.org_structure.models import OrgUnit


def _root_unit(db: Session, org_id: str, actor_user_id: str | None) -> OrgUnit:
    root = db.scalar(select(OrgUnit).where(
        OrgUnit.org_id == org_id, OrgUnit.parent_id.is_(None), OrgUnit.deleted_at.is_(None)))
    if root is None:
        root = OrgUnit(org_id=org_id, name="Global", parent_id=None,
                       created_by_user_id=actor_user_id, updated_by_user_id=actor_user_id)
        db.add(root)
        db.flush()
    return root


def _default_level(perms: set[str], read_perms, write_perms) -> str | None:
    """Migration 0043's rule: write permission (or an ungated screen) → DELETE,
    read permission → VIEW, else no access."""
    if write_perms is _ALL or perms & write_perms:
        return "DELETE"
    if read_perms and perms & read_perms:
        return "VIEW"
    return None


def ensure_org_access(db: Session, *, org_id: str, actor_user_id: str | None = None) -> dict:
    """Create whatever is missing; returns counts of what was added. Caller commits."""
    root = _root_unit(db, org_id, actor_user_id)

    added_grants = 0
    # A user with no live grant gets their assigned role (active_role_id) at the
    # root — what 0042 did for every user_role row that existed then.
    for user in db.scalars(select(User).where(User.org_id == org_id)).all():
        if not user.active_role_id:
            continue
        has_grant = db.scalar(select(UserRoleGrant.id).where(
            UserRoleGrant.user_id == user.id, UserRoleGrant.deleted_at.is_(None)).limit(1))
        if has_grant:
            continue
        db.add(UserRoleGrant(user_id=user.id, role_id=user.active_role_id, org_id=org_id,
                             org_unit_id=root.id, created_by_user_id=actor_user_id,
                             updated_by_user_id=actor_user_id))
        added_grants += 1

    screens = {s.code: s for s in db.scalars(select(Screen).where(Screen.deleted_at.is_(None))).all()}
    levels = {lv.code: lv for lv in db.scalars(select(ActionLevel)).all()}
    added_access = 0
    for role in db.scalars(select(Role).where(Role.org_id == org_id)).all():
        perms = {p.value for p in role.permissions}
        have = set(db.scalars(select(RoleScreenAccess.screen_id).where(
            RoleScreenAccess.org_id == org_id, RoleScreenAccess.role_id == role.id,
            RoleScreenAccess.org_unit_id.is_(None), RoleScreenAccess.deleted_at.is_(None))).all())
        for code, _name, _module, _route, read_perms, write_perms in SCREEN_CATALOG:
            screen = screens.get(code)
            level = _default_level(perms, read_perms, write_perms)
            if screen is None or screen.id in have or level is None or level not in levels:
                continue
            db.add(RoleScreenAccess(org_id=org_id, role_id=role.id, screen_id=screen.id, org_unit_id=None,
                                    max_action_level_id=levels[level].id,
                                    created_by_user_id=actor_user_id, updated_by_user_id=actor_user_id))
            added_access += 1
    db.flush()
    return {"root_unit": root.id, "role_grants_added": added_grants, "screen_access_added": added_access}
