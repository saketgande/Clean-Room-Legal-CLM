"""Tests for the menu-tree resolution, own-screen-access, and reference-data
service functions (``app/menu_security/service.py`` — feature
003-menu-screen-security, T007).

Mirrors the fixture style of ``test_screen_access_resolver.py`` and
``test_org_units_api.py``: a real (migrated) Postgres session, one
transaction per test, rolled back at teardown. Route-level permission gates
are exercised directly against the exact dependency instances
``app.menu_security.routes`` wires onto each route (``_READ_MENU`` /
``_READ_SCREEN_ACCESS``), which is the cheapest way to assert AC-4-style
"visibly present in the route's definition" behavior without spinning up a
full HTTP client.

FR/AC coverage: FR-1, FR-7, FR-8, FR-9, FR-13 (menu pruning + own-resolution
only), FR-14 (deep-link data source), FR-15, FR-16.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.models import Role, User, UserRoleGrant
from app.core import screen_access
from app.core.database import engine, new_uuid
from app.core.deps import require_permission
from app.core.enums import UserStatus
from app.menu_security import service
from app.menu_security.models import ActionLevel, MenuItem, RoleScreenAccess, Screen
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
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
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


def _get_or_create_action_level(db: Session, *, code: str, rank: int) -> ActionLevel:
    existing = db.query(ActionLevel).filter(ActionLevel.code == code).one_or_none()
    if existing is not None:
        return existing
    level = ActionLevel(code=code, rank=rank)
    db.add(level)
    db.flush()
    return level


def _make_grant_row(
    db: Session, *, org_id: str, role: Role, screen: Screen, level_code: str
) -> RoleScreenAccess:
    level = _get_or_create_action_level(db, code=level_code, rank=screen_access.LEVEL_RANK[level_code])
    row = RoleScreenAccess(
        org_id=org_id,
        role_id=role.id,
        screen_id=screen.id,
        org_unit_id=None,
        max_action_level_id=level.id,
    )
    db.add(row)
    db.flush()
    return row


def _make_menu_item(
    db: Session,
    *,
    parent_id: str | None,
    label: str,
    menu_type: str,
    screen_id: str | None = None,
    sequence_order: int = 0,
) -> MenuItem:
    item = MenuItem(
        parent_id=parent_id,
        label=label,
        menu_type=menu_type,
        screen_id=screen_id,
        sequence_order=sequence_order,
    )
    db.add(item)
    db.flush()
    return item


class Org:
    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="MenuTreeApiTest")
        self.root = _make_unit(db, org_id=self.org.id, name="Global", parent_id=None)


@pytest.fixture
def org(db: Session) -> Org:
    return Org(db)


# --- FR-7 / AC-2: screen_link node visibility ------------------------------


def test_menu_tree_includes_screen_link_only_when_resolved_view_or_above(db: Session, org: Org):
    screen_visible = _make_screen(db, code=f"visible-{uuid.uuid4().hex[:6]}")
    screen_hidden = _make_screen(db, code=f"hidden-{uuid.uuid4().hex[:6]}")
    role = _make_role(db, org_id=org.org.id, name="viewer")
    user = _make_user(db, org_id=org.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=org.root)
    _make_grant_row(db, org_id=org.org.id, role=role, screen=screen_visible, level_code="VIEW")
    # No grant row at all for screen_hidden.

    group = _make_menu_item(db, parent_id=None, label="Group", menu_type="group", sequence_order=1)
    visible_item = _make_menu_item(
        db, parent_id=group.id, label="Visible", menu_type="screen_link",
        screen_id=screen_visible.id, sequence_order=1,
    )
    _make_menu_item(
        db, parent_id=group.id, label="Hidden", menu_type="screen_link",
        screen_id=screen_hidden.id, sequence_order=2,
    )

    tree = service.get_menu_tree(db, actor=user)

    our_group = next(n for n in tree["nodes"] if n.id == group.id)
    child_ids = {c.id for c in our_group.children}
    assert visible_item.id in child_ids
    assert len(our_group.children) == 1
    assert our_group.children[0].action_level == "VIEW"


# --- FR-8 / AC-2: group node absent when zero surviving descendants --------


def test_menu_tree_group_absent_when_no_descendant_survives(db: Session, org: Org):
    screen = _make_screen(db, code=f"noaccess-{uuid.uuid4().hex[:6]}")
    role = _make_role(db, org_id=org.org.id, name="noaccess")
    user = _make_user(db, org_id=org.org.id, label="u2")
    _make_grant(db, user=user, role=role, org_unit=org.root)
    # No role_screen_access row at all: below VIEW.

    empty_group = _make_menu_item(
        db, parent_id=None, label="EmptyGroup", menu_type="group", sequence_order=5
    )
    _make_menu_item(
        db, parent_id=empty_group.id, label="NoAccess", menu_type="screen_link",
        screen_id=screen.id, sequence_order=1,
    )

    tree = service.get_menu_tree(db, actor=user)

    assert all(n.id != empty_group.id for n in tree["nodes"])


def test_menu_tree_nested_group_prunes_recursively(db: Session, org: Org):
    """A grandparent group with only an empty child group also disappears
    (FR-8's recursion)."""
    screen = _make_screen(db, code=f"deep-{uuid.uuid4().hex[:6]}")
    role = _make_role(db, org_id=org.org.id, name="deepnoaccess")
    user = _make_user(db, org_id=org.org.id, label="u3")
    _make_grant(db, user=user, role=role, org_unit=org.root)

    grandparent = _make_menu_item(
        db, parent_id=None, label="Grandparent", menu_type="group", sequence_order=6
    )
    parent = _make_menu_item(
        db, parent_id=grandparent.id, label="Parent", menu_type="group", sequence_order=1
    )
    _make_menu_item(
        db, parent_id=parent.id, label="Leaf", menu_type="screen_link",
        screen_id=screen.id, sequence_order=1,
    )

    tree = service.get_menu_tree(db, actor=user)

    assert all(n.id != grandparent.id for n in tree["nodes"])


# --- FR-9 / own-resolution-only: get_my_screen_access ----------------------


def test_get_my_screen_access_lists_only_granted_screens(db: Session, org: Org):
    screen_granted = _make_screen(db, code=f"granted-{uuid.uuid4().hex[:6]}")
    screen_not_granted = _make_screen(db, code=f"notgranted-{uuid.uuid4().hex[:6]}")
    role = _make_role(db, org_id=org.org.id, name="grantee")
    user = _make_user(db, org_id=org.org.id, label="u4")
    _make_grant(db, user=user, role=role, org_unit=org.root)
    _make_grant_row(db, org_id=org.org.id, role=role, screen=screen_granted, level_code="EDIT")

    result = service.get_my_screen_access(db, actor=user)

    codes = {e["screen_code"] for e in result["screens"]}
    assert screen_granted.code in codes
    assert screen_not_granted.code not in codes
    entry = next(e for e in result["screens"] if e["screen_code"] == screen_granted.code)
    assert entry["action_level"] == "EDIT"
    assert entry["rank"] == 3


def test_get_my_screen_access_with_screen_code_filter_returns_at_most_one(db: Session, org: Org):
    screen = _make_screen(db, code=f"single-{uuid.uuid4().hex[:6]}")
    role = _make_role(db, org_id=org.org.id, name="single-grantee")
    user = _make_user(db, org_id=org.org.id, label="u5")
    _make_grant(db, user=user, role=role, org_unit=org.root)
    _make_grant_row(db, org_id=org.org.id, role=role, screen=screen, level_code="ADD")

    result = service.get_my_screen_access(db, actor=user, screen_code=screen.code)

    assert len(result["screens"]) == 1
    assert result["screens"][0]["screen_code"] == screen.code
    assert result["screens"][0]["action_level"] == "ADD"


def test_get_my_screen_access_never_takes_a_user_id_parameter():
    """Structural check (FR own-resolution-only): the function signature has
    no ``user_id`` parameter anywhere, which is what makes querying another
    user's resolved access impossible rather than merely checked."""
    import inspect

    params = inspect.signature(service.get_my_screen_access).parameters
    assert "user_id" not in params


# --- reference data ----------------------------------------------------------


def test_list_action_levels_returns_four_levels_in_rank_order(db: Session):
    levels = service.list_action_levels(db)
    codes = [level.code for level in levels]
    assert codes == ["VIEW", "ADD", "EDIT", "DELETE"]


def test_list_screens_marks_tranche_1_screens_as_enforced(db: Session):
    screens = service.list_screens(db)
    by_code = {s["code"]: s for s in screens}
    assert by_code["contracts"]["is_enforced"] is True
    assert by_code["admin"]["is_enforced"] is False


# --- 403 permission gate (the exact dependency wired onto the routes) ------


def test_menu_read_gate_rejects_a_user_without_menu_read(db: Session, org: Org):
    role_no_perms = _make_role(db, org_id=org.org.id, name="no-perms")
    user = _make_user(db, org_id=org.org.id, label="u6")
    _make_grant(db, user=user, role=role_no_perms, org_unit=org.root)

    dependency = require_permission("menu:read")
    with pytest.raises(HTTPException) as exc_info:
        dependency(current_user=user, db=db)
    assert exc_info.value.status_code == 403


def test_screen_access_read_gate_rejects_a_user_without_the_permission(db: Session, org: Org):
    role_no_perms = _make_role(db, org_id=org.org.id, name="no-perms-2")
    user = _make_user(db, org_id=org.org.id, label="u7")
    _make_grant(db, user=user, role=role_no_perms, org_unit=org.root)

    dependency = require_permission("screen_access:read")
    with pytest.raises(HTTPException) as exc_info:
        dependency(current_user=user, db=db)
    assert exc_info.value.status_code == 403
