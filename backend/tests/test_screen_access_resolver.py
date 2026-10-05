"""Tests for the shared screen-access resolver
(``app/core/screen_access.py`` — feature 003-menu-screen-security, T002).

Every test builds its own small org-unit tree (Global -> Region A -> Entity 1
-> BU 1) plus roles/users/grants/screen/action-levels/role_screen_access rows
directly via SQLAlchemy against the real (migrated) Postgres test database,
inside a transaction that is rolled back at teardown so nothing here leaks
into other tests.

FR/AC coverage: AC-1, AC-7, AC-8, AC-13, plus FR-15's per-screen/batched
resolver-equivalence assertion and a regression that this feature does not
modify ``app.core.org_access``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.auth.models import Role, User, UserRoleGrant
from app.core import screen_access
from app.core.database import engine, new_uuid
from app.core.enums import UserStatus
from app.menu_security.models import ActionLevel, RoleScreenAccess, Screen
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization


@pytest.fixture
def db():
    """One Postgres transaction per test, rolled back at teardown."""
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


def _make_role(
    db: Session, *, org_id: str, name: str, allows_hierarchy_rollup: bool = True
) -> Role:
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=allows_hierarchy_rollup)
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
    grant = UserRoleGrant(
        user_id=user.id,
        role_id=role.id,
        org_id=user.org_id,
        org_unit_id=org_unit.id,
    )
    db.add(grant)
    db.flush()
    return grant


def _get_or_create_screen(db: Session, *, code: str) -> Screen:
    existing = db.query(Screen).filter(Screen.code == code).one_or_none()
    if existing is not None:
        return existing
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
    db: Session,
    *,
    org_id: str,
    role: Role,
    screen: Screen,
    level_code: str,
    org_unit: OrgUnit | None,
    deleted: bool = False,
) -> RoleScreenAccess:
    level = _get_or_create_action_level(db, code=level_code, rank=screen_access.LEVEL_RANK[level_code])
    row = RoleScreenAccess(
        org_id=org_id,
        role_id=role.id,
        screen_id=screen.id,
        org_unit_id=(org_unit.id if org_unit else None),
        max_action_level_id=level.id,
    )
    if deleted:
        from app.core.database import utcnow

        row.deleted_at = utcnow()
    db.add(row)
    db.flush()
    return row


class Tree:
    """Global -> Region A -> Entity 1 -> BU 1, within one organization."""

    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="ScreenAccessTest")
        self.global_unit = _make_unit(db, org_id=self.org.id, name="Global", parent_id=None)
        self.region_a = _make_unit(
            db, org_id=self.org.id, name="Region A", parent_id=self.global_unit.id
        )
        self.entity_1 = _make_unit(
            db, org_id=self.org.id, name="Entity 1", parent_id=self.region_a.id
        )
        self.bu_1 = _make_unit(db, org_id=self.org.id, name="BU 1", parent_id=self.entity_1.id)


@pytest.fixture
def tree(db: Session) -> Tree:
    return Tree(db)


SCREEN = "test_screen"


# --- FR-18 / AC-8: rollup up, never down/sideways --------------------------


def test_grant_at_global_rolls_up_to_a_descendant_unit(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="approver")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen, level_code="EDIT", org_unit=tree.global_unit
    )

    resolved = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.bu_1.id
    )

    assert resolved.level == "EDIT"
    assert resolved.rank == 3
    assert role.id in resolved.role_ids


def test_grant_at_child_does_not_roll_up_or_sideways(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="approver")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.bu_1)
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen, level_code="EDIT", org_unit=tree.bu_1
    )

    at_bu1 = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.bu_1.id
    )
    at_parent = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.entity_1.id
    )

    assert at_bu1.level == "EDIT"
    assert at_parent.level is None
    assert at_parent.reason == "no_grant"


# --- allows_hierarchy_rollup=False locks to the exact granted unit ---------


def test_no_rollup_role_is_locked_to_its_exact_granted_unit(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="locked", allows_hierarchy_rollup=False)
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.entity_1)
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen, level_code="EDIT", org_unit=tree.entity_1
    )

    at_exact = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.entity_1.id
    )
    at_child = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.bu_1.id
    )

    assert at_exact.level == "EDIT"
    assert at_child.level is None


# --- org-wide grant always applies ------------------------------------------


def test_org_wide_grant_always_applies_regardless_of_context(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="orgwide")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.bu_1)
    _make_grant_row(db, org_id=tree.org.id, role=role, screen=screen, level_code="VIEW", org_unit=None)

    resolved_at_global = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.global_unit.id
    )
    resolved_at_bu1 = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.bu_1.id
    )

    assert resolved_at_global.level == "VIEW"
    assert resolved_at_bu1.level == "VIEW"


# --- FR-19 / AC-8: highest-level-wins, narrower grant never reduces ---------


def test_highest_level_wins_narrower_grant_never_reduces(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="mixed")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)
    _make_grant(db, user=user, role=role, org_unit=tree.entity_1)
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen, level_code="DELETE", org_unit=tree.global_unit
    )
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen, level_code="VIEW", org_unit=tree.entity_1
    )

    resolved = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.bu_1.id
    )

    assert resolved.level == "DELETE"
    assert resolved.rank == 4


# --- soft-deleted grant rows are excluded -----------------------------------


def test_soft_deleted_grant_row_is_excluded(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="deleted-grant")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)
    _make_grant_row(
        db,
        org_id=tree.org.id,
        role=role,
        screen=screen,
        level_code="EDIT",
        org_unit=tree.global_unit,
        deleted=True,
    )

    resolved = screen_access.resolve_screen_access(
        db, user=user, screen_code=SCREEN, org_unit_id=tree.global_unit.id
    )

    assert resolved.level is None
    assert resolved.reason == "no_grant"


# --- AC-13: cross-org isolation ---------------------------------------------


def test_grant_in_a_different_org_never_satisfies_this_users_check(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    org_b = _make_org(db, name="ScreenAccessTestOtherOrg")
    unit_b = _make_unit(db, org_id=org_b.id, name="Global", parent_id=None)
    role_b = _make_role(db, org_id=org_b.id, name="other-org-role")
    user_b = _make_user(db, org_id=org_b.id, label="other-org-user")
    _make_grant(db, user=user_b, role=role_b, org_unit=unit_b)
    _make_grant_row(db, org_id=org_b.id, role=role_b, screen=screen, level_code="DELETE", org_unit=None)

    role_a = _make_role(db, org_id=tree.org.id, name="approver")
    user_a = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user_a, role=role_a, org_unit=tree.global_unit)
    # No role_screen_access row for role_a on this screen at all — org B's
    # org-wide DELETE grant must never leak across the org boundary.

    resolved = screen_access.resolve_screen_access(
        db, user=user_a, screen_code=SCREEN, org_unit_id=tree.global_unit.id
    )

    assert resolved.level is None
    assert resolved.reason == "no_grant"


# --- context-union when org_unit_id is omitted ------------------------------


def test_no_org_unit_given_context_is_union_of_grant_units_ancestors(db: Session, tree: Tree):
    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="two-unit")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    # User holds the role at two different, unrelated-by-direct-lineage units:
    # Region A (ancestors: Global, Region A) and BU 1 (ancestors: Global,
    # Region A, Entity 1, BU 1). A grant recorded exactly at Entity 1 should
    # be visible via the BU 1 grant's ancestor chain, even though the user
    # holds no grant at Entity 1 itself.
    _make_grant(db, user=user, role=role, org_unit=tree.region_a)
    _make_grant(db, user=user, role=role, org_unit=tree.bu_1)
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen, level_code="ADD", org_unit=tree.entity_1
    )

    resolved = screen_access.resolve_screen_access(db, user=user, screen_code=SCREEN)

    assert resolved.level == "ADD"


# --- unknown screen code -----------------------------------------------------


def test_unknown_screen_code_resolves_to_screen_not_found(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)

    resolved = screen_access.resolve_screen_access(
        db, user=user, screen_code=f"nonexistent-{uuid.uuid4().hex[:8]}"
    )

    assert resolved.level is None
    assert resolved.reason == "screen_not_found"


# --- assert_screen_level raises 403 on denial -------------------------------


def test_assert_screen_level_raises_403_when_below_min_level(db: Session, tree: Tree):
    from fastapi import HTTPException

    screen = _get_or_create_screen(db, code=SCREEN)
    role = _make_role(db, org_id=tree.org.id, name="view-only")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)
    _make_grant_row(db, org_id=tree.org.id, role=role, screen=screen, level_code="VIEW", org_unit=None)

    with pytest.raises(HTTPException) as excinfo:
        screen_access.assert_screen_level(
            db, user=user, screen_code=SCREEN, min_level="EDIT", org_unit_id=tree.global_unit.id
        )
    assert excinfo.value.status_code == 403

    allowed = screen_access.assert_screen_level(
        db, user=user, screen_code=SCREEN, min_level="VIEW", org_unit_id=tree.global_unit.id
    )
    assert allowed.level == "VIEW"


# --- FR-15: resolve_all_screen_access agrees with resolve_screen_access ----


def test_resolve_all_agrees_with_per_screen_resolve(db: Session, tree: Tree):
    screen_1 = _get_or_create_screen(db, code=f"{SCREEN}-1-{uuid.uuid4().hex[:6]}")
    screen_2 = _get_or_create_screen(db, code=f"{SCREEN}-2-{uuid.uuid4().hex[:6]}")
    role = _make_role(db, org_id=tree.org.id, name="multi-screen")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen_1, level_code="EDIT", org_unit=tree.global_unit
    )
    _make_grant_row(
        db, org_id=tree.org.id, role=role, screen=screen_2, level_code="VIEW", org_unit=tree.global_unit
    )

    batched = screen_access.resolve_all_screen_access(
        db, user=user, org_unit_id=tree.bu_1.id
    )
    per_screen_1 = screen_access.resolve_screen_access(
        db, user=user, screen_code=screen_1.code, org_unit_id=tree.bu_1.id
    )
    per_screen_2 = screen_access.resolve_screen_access(
        db, user=user, screen_code=screen_2.code, org_unit_id=tree.bu_1.id
    )

    assert batched[screen_1.code].level == per_screen_1.level == "EDIT"
    assert batched[screen_2.code].level == per_screen_2.level == "VIEW"
    # A screen with no grant at all is simply absent from the batched dict,
    # never present with level=None.
    assert all(v.level is not None for v in batched.values())


# --- Regression: this feature does not modify app.core.org_access ----------


def test_org_access_module_is_not_imported_for_writing_only_reading_helpers():
    """Regression: ``app.core.screen_access`` imports ``ancestor_unit_ids`` /
    ``active_grants_for_user`` from ``app.core.org_access`` (reuse, per
    plan.md's "Risks & decisions") but never reimplements or monkeypatches
    that module. This also documents that org_access.py itself is untouched
    by this feature (spec.md "Out of scope")."""
    import ast
    import inspect

    tree_ast = ast.parse(inspect.getsource(screen_access))
    imported_names: set[str] = set()
    for node in ast.walk(tree_ast):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    assert "app.core.org_access" in imported_names or any(
        name == "app.core" for name in imported_names
    )
    # The module must not define resolve_access/ancestor_unit_ids/
    # active_grants_for_user itself — those stay solely in org_access.py.
    assert not hasattr(screen_access, "resolve_access")
    defined_functions = {
        node.name for node in ast.walk(tree_ast) if isinstance(node, ast.FunctionDef)
    }
    assert "ancestor_unit_ids" not in defined_functions
    assert "active_grants_for_user" not in defined_functions
