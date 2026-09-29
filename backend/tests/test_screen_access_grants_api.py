"""Tests for the screen-access grant admin service functions
(``app/menu_security/service.py`` — feature 003-menu-screen-security, T007).

Mirrors the fixture style of ``test_org_units_api.py``. FR/AC coverage:
FR-22, FR-23, FR-25, FR-27, AC-11, AC-13, AC-14, AC-15.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Role, User, UserRoleGrant
from app.core import screen_access
from app.core.database import engine, new_uuid
from app.core.deps import require_permission
from app.core.enums import UserStatus
from app.core.models import AuditLog
from app.core.rbac import ADMIN_ROLE_NAME
from app.menu_security import service
from app.menu_security.models import ActionLevel, RoleScreenAccess, Screen
from app.menu_security.schemas import ScreenGrantCreate, ScreenGrantUpdate
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _make_org(db: Session, *, name: str) -> Organization:
    org = Organization(id=new_uuid(), name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _make_unit(db: Session, *, org_id: str, name: str, parent_id: str | None) -> OrgUnit:
    unit = OrgUnit(org_id=org_id, name=name, parent_id=parent_id)
    db.add(unit)
    db.flush()
    return unit


def _make_role(db: Session, *, org_id: str, name: str) -> Role:
    role = Role(org_id=org_id, name=name, allows_hierarchy_rollup=True)
    db.add(role)
    db.flush()
    return role


def _make_user(db: Session, *, org_id: str, label: str) -> User:
    user = User(
        org_id=org_id,
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=label,
        hashed_password="hash",
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    return user


def _make_grant(db: Session, *, user: User, role: Role, org_unit: OrgUnit) -> UserRoleGrant:
    grant = UserRoleGrant(user_id=user.id, role_id=role.id, org_id=user.org_id, org_unit_id=org_unit.id)
    db.add(grant)
    db.flush()
    return grant


def _make_screen(db: Session, *, code: str) -> Screen:
    screen = Screen(code=code, name=code, module="test", route_path=f"/{code}-{uuid.uuid4().hex[:8]}")
    db.add(screen)
    db.flush()
    return screen


def _get_or_create_action_level(db: Session, *, code: str) -> ActionLevel:
    existing = db.query(ActionLevel).filter(ActionLevel.code == code).one_or_none()
    if existing is not None:
        return existing
    level = ActionLevel(code=code, rank=screen_access.LEVEL_RANK[code])
    db.add(level)
    db.flush()
    return level


class Org:
    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="ScreenGrantApiTest")
        self.root = _make_unit(db, org_id=self.org.id, name="Global", parent_id=None)
        self.child_unit = _make_unit(db, org_id=self.org.id, name="Child", parent_id=self.root.id)
        self.role = _make_role(db, org_id=self.org.id, name=f"reviewer-{uuid.uuid4().hex[:8]}")
        self.admin_user = _make_user(db, org_id=self.org.id, label="admin")
        _make_grant(db, user=self.admin_user, role=self.role, org_unit=self.root)
        self.screen = _make_screen(db, code=f"screen-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def org(db: Session) -> Org:
    return Org(db)


# --- FR-22 / happy path: create ----------------------------------------------


def test_create_screen_grant_happy_path(db: Session, org: Org):
    result = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="EDIT"),
    )
    assert result["role_id"] == org.role.id
    assert result["screen_id"] == org.screen.id
    assert result["max_action_level"] == "EDIT"
    assert result["max_action_rank"] == 3
    assert result["is_active"] is True
    assert result["is_locked"] is False

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "screen_access.granted", AuditLog.resource_id == result["id"]
        )
    )
    assert audit is not None
    assert audit.actor_user_id == org.admin_user.id


def test_create_screen_grant_scoped_to_org_unit(db: Session, org: Org):
    result = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(
            role_id=org.role.id,
            screen_id=org.screen.id,
            org_unit_id=org.child_unit.id,
            action_level="VIEW",
        ),
    )
    assert result["org_unit_id"] == org.child_unit.id
    assert result["org_unit_name"] == "Child"


# --- FR-22: 404 for role/org-unit outside actor's org ------------------------


def test_create_screen_grant_rejects_role_from_another_org(db: Session, org: Org):
    other_org = _make_org(db, name="ScreenGrantApiTestOther")
    other_role = _make_role(db, org_id=other_org.id, name="other-role")

    with pytest.raises(HTTPException) as exc_info:
        service.create_screen_grant(
            db,
            actor=org.admin_user,
            payload=ScreenGrantCreate(
                role_id=other_role.id, screen_id=org.screen.id, action_level="VIEW"
            ),
        )
    assert exc_info.value.status_code == 404


def test_create_screen_grant_rejects_org_unit_from_another_org(db: Session, org: Org):
    other_org = _make_org(db, name="ScreenGrantApiTestOtherUnit")
    other_unit = _make_unit(db, org_id=other_org.id, name="Global", parent_id=None)

    with pytest.raises(HTTPException) as exc_info:
        service.create_screen_grant(
            db,
            actor=org.admin_user,
            payload=ScreenGrantCreate(
                role_id=org.role.id,
                screen_id=org.screen.id,
                org_unit_id=other_unit.id,
                action_level="VIEW",
            ),
        )
    assert exc_info.value.status_code == 404


def test_create_screen_grant_rejects_unknown_screen(db: Session, org: Org):
    with pytest.raises(HTTPException) as exc_info:
        service.create_screen_grant(
            db,
            actor=org.admin_user,
            payload=ScreenGrantCreate(
                role_id=org.role.id, screen_id=new_uuid(), action_level="VIEW"
            ),
        )
    assert exc_info.value.status_code == 404


# --- FR-5 / edge case: invalid action level is 422 ---------------------------


def test_create_screen_grant_rejects_invalid_action_level_at_the_schema_boundary():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScreenGrantCreate(role_id=new_uuid(), screen_id=new_uuid(), action_level="SUPERDELETE")


def test_get_action_level_or_422_rejects_a_level_the_schema_layer_lets_through(db: Session, org: Org):
    from app.menu_security.access import get_action_level_or_422

    with pytest.raises(HTTPException) as exc_info:
        get_action_level_or_422(db, "NOT_A_LEVEL")
    assert exc_info.value.status_code == 422


# --- 409: duplicate live grant ------------------------------------------------


def test_create_screen_grant_rejects_a_duplicate_live_grant(db: Session, org: Org):
    service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="VIEW"),
    )
    with pytest.raises(HTTPException) as exc_info:
        service.create_screen_grant(
            db,
            actor=org.admin_user,
            payload=ScreenGrantCreate(
                role_id=org.role.id, screen_id=org.screen.id, action_level="EDIT"
            ),
        )
    assert exc_info.value.status_code == 409


# --- FR-22 / AC-11: update raises/lowers the level, audited ------------------


def test_update_screen_grant_raises_the_level_and_audits_before_after(db: Session, org: Org):
    created = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="VIEW"),
    )
    updated = service.update_screen_grant(
        db, actor=org.admin_user, grant_id=created["id"], payload=ScreenGrantUpdate(action_level="EDIT")
    )
    assert updated["max_action_level"] == "EDIT"

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "screen_access.updated", AuditLog.resource_id == created["id"]
        )
    )
    assert audit is not None
    assert audit.before == {"max_action_level": "VIEW"}
    assert audit.after == {"max_action_level": "EDIT"}


def test_update_screen_grant_unknown_grant_is_404(db: Session, org: Org):
    with pytest.raises(HTTPException) as exc_info:
        service.update_screen_grant(
            db, actor=org.admin_user, grant_id=new_uuid(), payload=ScreenGrantUpdate(action_level="EDIT")
        )
    assert exc_info.value.status_code == 404


# --- FR-22: org-filtered fetch (a grant from another org is 404) ------------


def test_update_screen_grant_from_another_org_is_404(db: Session, org: Org):
    created = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="VIEW"),
    )
    other_org = _make_org(db, name="ScreenGrantApiTestUpdateOther")
    other_root = _make_unit(db, org_id=other_org.id, name="Global", parent_id=None)
    other_role = _make_role(db, org_id=other_org.id, name="other-admin-role")
    other_admin = _make_user(db, org_id=other_org.id, label="other-admin")
    _make_grant(db, user=other_admin, role=other_role, org_unit=other_root)

    with pytest.raises(HTTPException) as exc_info:
        service.update_screen_grant(
            db, actor=other_admin, grant_id=created["id"], payload=ScreenGrantUpdate(action_level="EDIT")
        )
    assert exc_info.value.status_code == 404


# --- FR-22: revoke happy path + already-revoked 409 --------------------------


def test_revoke_screen_grant_happy_path_sets_deleted_fields_and_audits(db: Session, org: Org):
    created = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="EDIT"),
    )
    service.revoke_screen_grant(db, actor=org.admin_user, grant_id=created["id"])

    grant = db.get(RoleScreenAccess, created["id"])
    assert grant.deleted_at is not None
    assert grant.deleted_by_user_id == org.admin_user.id

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "screen_access.revoked", AuditLog.resource_id == created["id"]
        )
    )
    assert audit is not None
    assert audit.metadata_json["revocation"] is True


def test_revoke_already_revoked_grant_is_409(db: Session, org: Org):
    created = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="VIEW"),
    )
    service.revoke_screen_grant(db, actor=org.admin_user, grant_id=created["id"])
    with pytest.raises(HTTPException) as exc_info:
        service.revoke_screen_grant(db, actor=org.admin_user, grant_id=created["id"])
    assert exc_info.value.status_code == 409


# --- FR-25 / AC-15: bootstrap lock, single shared code path ------------------


def _make_bootstrap_grant(db: Session, *, org: Org) -> RoleScreenAccess:
    """This feature's cutover (T001's FR-26 migration) seeds every
    *pre-existing* org's built-in ``admin`` role with a DELETE grant on the
    ``screen_access`` screen. A test org created fresh here post-migration
    has no such row automatically, so we recreate the same shape directly:
    a role literally named ``ADMIN_ROLE_NAME`` holding a live DELETE grant on
    the platform's (already-seeded, platform-wide) ``screen_access`` screen —
    exactly the row ``_is_bootstrap_grant`` matches on."""
    admin_role = _make_role(db, org_id=org.org.id, name=ADMIN_ROLE_NAME)
    _make_grant(db, user=org.admin_user, role=admin_role, org_unit=org.root)
    screen_access_screen = db.scalar(select(Screen).where(Screen.code == "screen_access"))
    assert screen_access_screen is not None, "screen catalog seed (T001) must include 'screen_access'"
    created = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(
            role_id=admin_role.id, screen_id=screen_access_screen.id, action_level="DELETE"
        ),
    )
    return db.get(RoleScreenAccess, created["id"])


def test_bootstrap_admin_grant_cannot_be_lowered_below_delete_via_patch(db: Session, org: Org):
    grant = _make_bootstrap_grant(db, org=org)

    with pytest.raises(HTTPException) as exc_info:
        service.update_screen_grant(
            db, actor=org.admin_user, grant_id=grant.id, payload=ScreenGrantUpdate(action_level="EDIT")
        )
    assert exc_info.value.status_code == 409

    db.refresh(grant)
    assert grant.action_level.code == "DELETE"


def test_bootstrap_admin_grant_cannot_be_revoked_via_delete(db: Session, org: Org):
    grant = _make_bootstrap_grant(db, org=org)

    with pytest.raises(HTTPException) as exc_info:
        service.revoke_screen_grant(db, actor=org.admin_user, grant_id=grant.id)
    assert exc_info.value.status_code == 409

    db.refresh(grant)
    assert grant.deleted_at is None


def test_bootstrap_lock_is_a_single_shared_predicate_for_patch_and_delete():
    """Structural check (AC-15's 'through which surface'): both the PATCH
    and DELETE service functions must funnel through the exact same
    ``_assert_bootstrap_grant_not_weakened`` helper — not two independent
    copies of the FR-25 rule."""
    import ast
    import inspect

    update_source = inspect.getsource(service.update_screen_grant)
    revoke_source = inspect.getsource(service.revoke_screen_grant)
    assert "_assert_bootstrap_grant_not_weakened" in update_source
    assert "_assert_bootstrap_grant_not_weakened" in revoke_source

    # And there is exactly one definition of that helper in the module.
    tree = ast.parse(inspect.getsource(service))
    defs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_bootstrap_grant_not_weakened"
    ]
    assert len(defs) == 1


def test_non_bootstrap_grant_can_be_lowered_and_revoked_freely(db: Session, org: Org):
    """Sanity: the lock is specific to (admin role, screen_access screen) —
    an ordinary grant is unaffected."""
    created = service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="DELETE"),
    )
    lowered = service.update_screen_grant(
        db, actor=org.admin_user, grant_id=created["id"], payload=ScreenGrantUpdate(action_level="VIEW")
    )
    assert lowered["max_action_level"] == "VIEW"
    service.revoke_screen_grant(db, actor=org.admin_user, grant_id=created["id"])
    grant = db.get(RoleScreenAccess, created["id"])
    assert grant.deleted_at is not None


# --- AC-13/AC-14: org-filtering on list ---------------------------------------


def test_list_screen_grants_is_org_filtered(db: Session, org: Org):
    service.create_screen_grant(
        db,
        actor=org.admin_user,
        payload=ScreenGrantCreate(role_id=org.role.id, screen_id=org.screen.id, action_level="VIEW"),
    )
    other_org = _make_org(db, name="ScreenGrantApiTestListOther")
    other_root = _make_unit(db, org_id=other_org.id, name="Global", parent_id=None)
    other_role = _make_role(db, org_id=other_org.id, name="other-role-list")
    other_admin = _make_user(db, org_id=other_org.id, label="other-admin-list")
    _make_grant(db, user=other_admin, role=other_role, org_unit=other_root)
    service.create_screen_grant(
        db,
        actor=other_admin,
        payload=ScreenGrantCreate(role_id=other_role.id, screen_id=org.screen.id, action_level="DELETE"),
    )

    org_a_grants = service.list_screen_grants(db, actor=org.admin_user)
    assert all(g["org_id"] == org.org.id for g in org_a_grants)
    assert org.role.id in {g["role_id"] for g in org_a_grants}
    assert other_role.id not in {g["role_id"] for g in org_a_grants}


# --- 403 permission gate (the exact dependency wired onto the routes) -------


def test_screen_access_manage_gate_rejects_a_user_without_the_permission(db: Session, org: Org):
    role_no_perms = _make_role(db, org_id=org.org.id, name=f"no-perms-{uuid.uuid4().hex[:8]}")
    plain_user = _make_user(db, org_id=org.org.id, label="plain")
    _make_grant(db, user=plain_user, role=role_no_perms, org_unit=org.root)

    dependency = require_permission("screen_access:manage")
    with pytest.raises(HTTPException) as exc_info:
        dependency(current_user=plain_user, db=db)
    assert exc_info.value.status_code == 403
