"""Business logic for the menu_security domain (feature
003-menu-screen-security, T007).

Shapes and behavior are frozen in plan.md's "Interface freeze" and
"Component design > Backend > Service" sections — copy exactly, do not
improvise. Every mutation is org-scoped (``actor.org_id``) and audited in the
same transaction; every read of "my own" resolution (``get_my_screen_access``)
takes no ``user_id`` parameter anywhere, which is what makes "a user can never
query another user's resolved access" structurally impossible rather than
merely checked (FR own-resolution-only rule).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core import screen_access
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.rbac import ADMIN_ROLE_NAME
from app.menu_security.access import (
    get_action_level_or_422,
    get_grant_or_404,
    get_org_role_or_404,
    get_screen_or_404,
)
from app.menu_security.models import ActionLevel, MenuItem, RoleScreenAccess, Screen
from app.menu_security.schemas import MenuNode, ScreenGrantCreate, ScreenGrantUpdate
from app.org_structure.access import get_org_unit_or_404
from app.org_structure.models import OrgUnit

# FR-17 tranche-1 screens — the only ones with the FR-10 API-layer retrofit in
# this feature. Drives ScreenResponse.is_enforced (a derived response field,
# not a column).
TRANCHE_1_SCREEN_CODES: frozenset[str] = frozenset(
    {"contracts", "trademarks", "notices", "intake"}
)


# ---------------------------------------------------------------------------
# Reference data (no org scope, no audit)
# ---------------------------------------------------------------------------


def list_action_levels(db: Session) -> list[ActionLevel]:
    stmt = select(ActionLevel).where(ActionLevel.deleted_at.is_(None)).order_by(ActionLevel.rank)
    return list(db.scalars(stmt).all())


def list_screens(db: Session, *, module: str | None = None) -> list[dict]:
    stmt = select(Screen).where(Screen.deleted_at.is_(None))
    if module is not None:
        stmt = stmt.where(Screen.module == module)
    screens = db.scalars(stmt.order_by(Screen.module, Screen.name)).all()
    return [_serialize_screen(s) for s in screens]


def _serialize_screen(screen: Screen) -> dict:
    return {
        "id": screen.id,
        "code": screen.code,
        "name": screen.name,
        "module": screen.module,
        "route_path": screen.route_path,
        "is_enforced": screen.code in TRANCHE_1_SCREEN_CODES,
    }


# ---------------------------------------------------------------------------
# Menu tree (FR-7, FR-8) and "my screen access" (FR-9, own-resolution only)
# ---------------------------------------------------------------------------


def get_menu_tree(db: Session, *, actor: User, org_unit_id: str | None = None) -> dict:
    """Resolve once (FR-6), load the whole tree, prune bottom-up.

    A ``screen_link`` node survives iff its screen appears in the resolution
    map (FR-7); a ``group`` node survives iff at least one child survives
    after pruning, recursively (FR-8). No audit — a read on every page load.
    """
    resolution = screen_access.resolve_all_screen_access(
        db, user=actor, org_unit_id=org_unit_id
    )

    items = db.scalars(
        select(MenuItem)
        .where(MenuItem.deleted_at.is_(None))
        .order_by(MenuItem.parent_id, MenuItem.sequence_order)
    ).all()

    screens_by_id = {
        s.id: s for s in db.scalars(select(Screen).where(Screen.deleted_at.is_(None))).all()
    }

    children_by_parent: dict[str | None, list[MenuItem]] = {}
    for item in items:
        children_by_parent.setdefault(item.parent_id, []).append(item)

    def build(item: MenuItem) -> MenuNode | None:
        child_nodes: list[MenuNode] = []
        for child in children_by_parent.get(item.id, []):
            built = build(child)
            if built is not None:
                child_nodes.append(built)

        if item.menu_type == "screen_link":
            screen = screens_by_id.get(item.screen_id)
            resolved = resolution.get(screen.code) if screen is not None else None
            if resolved is None:
                return None
            return MenuNode(
                id=item.id,
                parent_id=item.parent_id,
                label=item.label,
                icon=item.icon,
                menu_type=item.menu_type,
                sequence_order=item.sequence_order,
                screen_id=screen.id,
                screen_code=screen.code,
                route_path=screen.route_path,
                action_level=resolved.level,
                children=[],
            )

        # Group node: survives iff >=1 child survived pruning.
        if not child_nodes:
            return None
        return MenuNode(
            id=item.id,
            parent_id=item.parent_id,
            label=item.label,
            icon=item.icon,
            menu_type=item.menu_type,
            sequence_order=item.sequence_order,
            screen_id=None,
            screen_code=None,
            route_path=None,
            action_level=None,
            children=child_nodes,
        )

    roots: list[MenuNode] = []
    for item in children_by_parent.get(None, []):
        built = build(item)
        if built is not None:
            roots.append(built)

    return {"org_unit_id": org_unit_id, "nodes": roots}


def get_my_screen_access(
    db: Session, *, actor: User, screen_code: str | None = None, org_unit_id: str | None = None
) -> dict:
    """Always resolves for ``actor`` only — there is no ``user_id`` parameter
    anywhere in this module."""
    if screen_code is not None:
        resolved = screen_access.resolve_screen_access(
            db, user=actor, screen_code=screen_code, org_unit_id=org_unit_id
        )
        entries = []
        if resolved.level is not None:
            entries.append(
                {
                    "screen_id": resolved.screen_id,
                    "screen_code": resolved.screen_code,
                    "route_path": get_screen_or_404(db, code=screen_code).route_path,
                    "action_level": resolved.level,
                    "rank": resolved.rank,
                }
            )
        return {"org_unit_id": org_unit_id, "screens": entries}

    resolution = screen_access.resolve_all_screen_access(db, user=actor, org_unit_id=org_unit_id)
    screens_by_id = {
        s.id: s for s in db.scalars(select(Screen).where(Screen.deleted_at.is_(None))).all()
    }
    entries = [
        {
            "screen_id": resolved.screen_id,
            "screen_code": resolved.screen_code,
            "route_path": screens_by_id[resolved.screen_id].route_path,
            "action_level": resolved.level,
            "rank": resolved.rank,
        }
        for resolved in resolution.values()
        if resolved.screen_id in screens_by_id
    ]
    return {"org_unit_id": org_unit_id, "screens": entries}


# ---------------------------------------------------------------------------
# Screen-access grant admin (FR-22, FR-23, FR-25, FR-27)
# ---------------------------------------------------------------------------


def _is_bootstrap_grant(db: Session, grant: RoleScreenAccess) -> bool:
    """The FR-25 predicate: the built-in admin role's grant on the
    ``screen_access`` screen. Shared by ``_assert_bootstrap_grant_not_weakened``
    and the response's ``is_locked`` field, so the UI disables exactly what
    the server refuses."""
    screen = db.get(Screen, grant.screen_id)
    return grant.role.name == ADMIN_ROLE_NAME and screen is not None and screen.code == "screen_access"


def _assert_bootstrap_grant_not_weakened(
    db: Session, *, grant: RoleScreenAccess, new_level_rank: int | None
) -> None:
    """The FR-25 lock — the SINGLE code path both PATCH and DELETE funnel
    through. Raises 409 when the grant is the built-in admin role's DELETE
    grant on the ``screen_access`` screen and the update/revoke would reduce
    it below DELETE or remove it (``new_level_rank is None`` == a revoke)."""
    if _is_bootstrap_grant(db, grant) and (new_level_rank is None or new_level_rank < 4):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The built-in admin role's DELETE access to the screen-access "
            "management screen cannot be reduced or revoked",
        )


def _serialize_grant(db: Session, grant: RoleScreenAccess) -> dict:
    screen = db.get(Screen, grant.screen_id)
    unit = db.get(OrgUnit, grant.org_unit_id) if grant.org_unit_id else None
    return {
        "id": grant.id,
        "org_id": grant.org_id,
        "role_id": grant.role_id,
        "role_name": grant.role.name,
        "role_is_builtin_admin": grant.role.name == ADMIN_ROLE_NAME,
        "allows_hierarchy_rollup": grant.role.allows_hierarchy_rollup,
        "screen_id": grant.screen_id,
        "screen_code": screen.code,
        "screen_name": screen.name,
        "org_unit_id": grant.org_unit_id,
        "org_unit_name": unit.name if unit else None,
        "max_action_level_id": grant.max_action_level_id,
        "max_action_level": grant.action_level.code,
        "max_action_rank": grant.action_level.rank,
        "is_locked": _is_bootstrap_grant(db, grant),
        "is_active": grant.deleted_at is None,
        "revoked_at": grant.deleted_at,
        "revoked_by_user_id": grant.deleted_by_user_id,
        "created_at": grant.created_at,
        "created_by_user_id": grant.created_by_user_id,
        "updated_at": grant.updated_at,
        "updated_by_user_id": grant.updated_by_user_id,
    }


def list_screen_grants(
    db: Session,
    *,
    actor: User,
    role_id: str | None = None,
    screen_id: str | None = None,
    org_unit_id: str | None = None,
    include_revoked: bool = False,
) -> list[dict]:
    stmt = select(RoleScreenAccess).where(RoleScreenAccess.org_id == actor.org_id)
    if role_id is not None:
        stmt = stmt.where(RoleScreenAccess.role_id == role_id)
    if screen_id is not None:
        stmt = stmt.where(RoleScreenAccess.screen_id == screen_id)
    if org_unit_id is not None:
        stmt = stmt.where(RoleScreenAccess.org_unit_id == org_unit_id)
    if not include_revoked:
        stmt = stmt.where(RoleScreenAccess.deleted_at.is_(None))
    grants = db.scalars(stmt.order_by(RoleScreenAccess.created_at.desc())).all()
    return [_serialize_grant(db, g) for g in grants]


def create_screen_grant(db: Session, *, actor: User, payload: ScreenGrantCreate) -> dict:
    role = get_org_role_or_404(db, actor.org_id, payload.role_id)
    if payload.org_unit_id is not None:
        get_org_unit_or_404(db, actor.org_id, payload.org_unit_id)
    screen = get_screen_or_404(db, screen_id=payload.screen_id)
    level = get_action_level_or_422(db, payload.action_level)

    existing = db.scalar(
        select(RoleScreenAccess.id).where(
            RoleScreenAccess.org_id == actor.org_id,
            RoleScreenAccess.role_id == role.id,
            RoleScreenAccess.screen_id == screen.id,
            RoleScreenAccess.org_unit_id == payload.org_unit_id,
            RoleScreenAccess.deleted_at.is_(None),
        )
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A live grant already exists for this role/screen/org-unit"
        )

    grant = RoleScreenAccess(
        org_id=actor.org_id,
        role_id=role.id,
        screen_id=screen.id,
        org_unit_id=payload.org_unit_id,
        max_action_level_id=level.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(grant)
    db.flush()
    write_audit_log(
        db,
        action="screen_access.granted",
        resource_type="role_screen_access",
        resource_id=grant.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={
            "role_id": role.id,
            "role_name": role.name,
            "screen_id": screen.id,
            "screen_code": screen.code,
            "org_unit_id": grant.org_unit_id,
            "max_action_level": level.code,
        },
    )
    db.commit()
    db.refresh(grant)
    return _serialize_grant(db, grant)


def update_screen_grant(
    db: Session, *, actor: User, grant_id: str, payload: ScreenGrantUpdate
) -> dict:
    grant = get_grant_or_404(db, actor.org_id, grant_id)
    new_level = get_action_level_or_422(db, payload.action_level)
    _assert_bootstrap_grant_not_weakened(db, grant=grant, new_level_rank=new_level.rank)

    before_level = grant.action_level.code
    screen = db.get(Screen, grant.screen_id)
    grant.max_action_level_id = new_level.id
    grant.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="screen_access.updated",
        resource_type="role_screen_access",
        resource_id=grant.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before={"max_action_level": before_level},
        after={"max_action_level": new_level.code},
        metadata={
            "role_id": grant.role_id,
            "role_name": grant.role.name,
            "screen_id": grant.screen_id,
            "screen_code": screen.code,
            "org_unit_id": grant.org_unit_id,
        },
    )
    db.commit()
    db.refresh(grant)
    return _serialize_grant(db, grant)


def revoke_screen_grant(db: Session, *, actor: User, grant_id: str) -> None:
    grant = get_grant_or_404(db, actor.org_id, grant_id)
    if grant.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Screen-access grant already revoked")
    _assert_bootstrap_grant_not_weakened(db, grant=grant, new_level_rank=None)

    before_level = grant.action_level.code
    role_id = grant.role_id
    role_name = grant.role.name
    screen_id = grant.screen_id
    screen_code = db.get(Screen, grant.screen_id).code
    org_unit_id = grant.org_unit_id

    grant.deleted_at = utcnow()
    grant.deleted_by_user_id = actor.id
    grant.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="screen_access.revoked",
        resource_type="role_screen_access",
        resource_id=grant.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before={
            "role_id": role_id,
            "role_name": role_name,
            "screen_id": screen_id,
            "screen_code": screen_code,
            "org_unit_id": org_unit_id,
            "max_action_level": before_level,
        },
        metadata={"revocation": True},
    )
    db.commit()
