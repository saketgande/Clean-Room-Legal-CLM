"""Tests for the shared org-hierarchy + delegation access resolver
(``app/core/org_access.py`` — feature 002-org-hierarchy-rbac, T003).

Every test builds its own small org-unit tree (Global -> Region A -> Entity 1
-> BU 1) plus roles/users/grants directly via SQLAlchemy against the real
(migrated) Postgres test database, inside a transaction that is rolled back
at teardown so nothing here leaks into other tests.

FR/AC coverage: AC-3..AC-14, AC-18, AC-19, AC-20 (the wall/authority
non-interference assertion is a static regression check, not a live wall
scenario, since app/walls and app/authority are out of this feature's scope
entirely).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.models import Permission, Role, User, UserRoleGrant
from app.contracts.access import user_can_access_contract
from app.contracts.models import Contract
from app.core import org_access
from app.core.access import is_org_admin
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import UserStatus
from app.org_structure.models import Delegation, OrgUnit
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


def _get_or_create_permission(db: Session, value: str) -> Permission:
    existing = db.query(Permission).filter(Permission.value == value).one_or_none()
    if existing is not None:
        return existing
    perm = Permission(value=value)
    db.add(perm)
    db.flush()
    return perm


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
    db: Session, *, org_id: str, name: str, permission_values: list[str], allows_hierarchy_rollup: bool = True
) -> Role:
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=allows_hierarchy_rollup)
    role.permissions = [_get_or_create_permission(db, v) for v in permission_values]
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


def _make_grant(
    db: Session,
    *,
    user: User,
    role: Role,
    org_unit: OrgUnit,
    valid_from=None,
    valid_to=None,
    deleted_at=None,
) -> UserRoleGrant:
    grant = UserRoleGrant(
        user_id=user.id,
        role_id=role.id,
        org_id=user.org_id,
        org_unit_id=org_unit.id,
        valid_from=valid_from,
        valid_to=valid_to,
        deleted_at=deleted_at,
        deleted_by_user_id=(user.id if deleted_at else None),
    )
    db.add(grant)
    db.flush()
    return grant


def _make_delegation(
    db: Session,
    *,
    delegator: User,
    delegate: User,
    role_id: str | None,
    org_unit_id: str | None,
    start_date,
    end_date,
    status: str = "active",
    deleted_at=None,
) -> Delegation:
    delegation = Delegation(
        org_id=delegator.org_id,
        delegator_user_id=delegator.id,
        delegate_user_id=delegate.id,
        role_id=role_id,
        org_unit_id=org_unit_id,
        start_date=start_date,
        end_date=end_date,
        status=status,
        deleted_at=deleted_at,
        deleted_by_user_id=(delegator.id if deleted_at else None),
    )
    db.add(delegation)
    db.flush()
    return delegation


class Tree:
    """Global -> Region A -> Entity 1 -> BU 1, within one organization."""

    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="OrgAccessTest")
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


APPROVE = "contract:approve"
READ = "contract:read"


# --- FR-8 / AC-3, AC-4: upward-only rollup ---------------------------------


def test_grant_at_global_rolls_up_to_a_descendant_unit(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)

    resolved = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.bu_1.id
    )

    assert resolved.allowed is True
    assert resolved.reason == "native_grant"
    assert role.id in resolved.role_ids


def test_grant_at_child_does_not_roll_up_or_sideways(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.bu_1)

    at_bu1 = org_access.resolve_access(db, user=user, permission=APPROVE, org_unit_id=tree.bu_1.id)
    at_parent = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.entity_1.id
    )
    at_global = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.global_unit.id
    )

    assert at_bu1.allowed is True
    assert at_parent.allowed is False
    assert at_parent.reason == "no_grant"
    assert at_global.allowed is False


# --- FR-7 / AC-5: allows_hierarchy_rollup=False locks to the exact unit ----


def test_no_rollup_role_is_locked_to_its_exact_granted_unit(db: Session, tree: Tree):
    role = _make_role(
        db,
        org_id=tree.org.id,
        name="locked",
        permission_values=[APPROVE],
        allows_hierarchy_rollup=False,
    )
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.entity_1)

    at_exact = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.entity_1.id
    )
    at_child = org_access.resolve_access(db, user=user, permission=APPROVE, org_unit_id=tree.bu_1.id)

    assert at_exact.allowed is True
    assert at_child.allowed is False


# --- FR-9 / AC-6, AC-7: expiry and revocation are independent --------------


def test_expired_grant_is_excluded_even_if_not_revoked(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(
        db,
        user=user,
        role=role,
        org_unit=tree.global_unit,
        valid_to=utcnow() - timedelta(days=1),
    )

    resolved = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.global_unit.id
    )

    assert resolved.allowed is False
    assert resolved.reason == "no_grant"


def test_revoked_grant_is_excluded_even_if_unexpired(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(
        db,
        user=user,
        role=role,
        org_unit=tree.global_unit,
        valid_to=utcnow() + timedelta(days=365),
        deleted_at=utcnow(),
    )

    resolved = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.global_unit.id
    )

    assert resolved.allowed is False
    assert resolved.reason == "no_grant"


def test_zero_grants_resolves_to_no_access_not_an_error(db: Session, tree: Tree):
    user = _make_user(db, org_id=tree.org.id, label="u1")

    resolved = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.global_unit.id
    )

    assert resolved.allowed is False
    assert resolved.reason == "no_grant"


# --- FR-23 / AC-19: the permission-string check is reused, not bypassed ----


def test_grant_at_correct_scope_but_role_lacks_permission_is_denied(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="reader", permission_values=[READ])
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)

    resolved = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=tree.global_unit.id
    )

    assert resolved.allowed is False
    assert resolved.reason == "role_lacks_permission"


# --- AC-18: cross-org isolation ---------------------------------------------


def test_org_unit_in_a_different_organization_is_never_visible(db: Session, tree: Tree):
    org_b = _make_org(db, name="OrgAccessTestOtherOrg")
    other_root = _make_unit(db, org_id=org_b.id, name="Global", parent_id=None)
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)

    resolved = org_access.resolve_access(
        db, user=user, permission=APPROVE, org_unit_id=other_root.id
    )

    assert resolved.allowed is False
    assert resolved.reason == "org_unit_not_found"


def test_grant_and_delegation_in_org_a_never_satisfy_org_b_user(db: Session, tree: Tree):
    org_b = _make_org(db, name="OrgAccessTestOtherOrg2")
    other_root = _make_unit(db, org_id=org_b.id, name="Global", parent_id=None)
    delegator = _make_user(db, org_id=tree.org.id, label="delegator")
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    _make_grant(db, user=delegator, role=role, org_unit=tree.global_unit)

    other_org_user = _make_user(db, org_id=org_b.id, label="other-org-user")
    now = utcnow()
    # A delegation record cannot even be created across orgs in the real
    # service layer (org-scoped validation is a service-layer concern, T008),
    # but the resolver itself must also never honor a delegation whose
    # ``org_id`` doesn't match the delegate's own org — the delegation-pass
    # query is scoped on ``Delegation.org_id == user.org_id``, so a delegation
    # recorded under org A naming a user who actually belongs to org B never
    # resolves for that user.
    _make_delegation(
        db,
        delegator=delegator,
        delegate=other_org_user,
        role_id=None,
        org_unit_id=None,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    resolved = org_access.resolve_access(
        db, user=other_org_user, permission=APPROVE, org_unit_id=other_root.id
    )

    assert resolved.allowed is False
    assert resolved.reason == "no_grant"


# --- FR-13/FR-14 / AC-10, AC-11: delegation intersection -------------------


def test_delegation_narrowed_to_a_descendant_unit_grants_access(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    delegator = _make_user(db, org_id=tree.org.id, label="delegator")
    delegate = _make_user(db, org_id=tree.org.id, label="delegate")
    _make_grant(db, user=delegator, role=role, org_unit=tree.region_a)

    now = utcnow()
    delegation = _make_delegation(
        db,
        delegator=delegator,
        delegate=delegate,
        role_id=None,
        org_unit_id=tree.entity_1.id,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    resolved = org_access.resolve_access(
        db, user=delegate, permission=APPROVE, org_unit_id=tree.entity_1.id
    )

    assert resolved.allowed is True
    assert resolved.reason == "delegated_grant"
    assert resolved.via_delegation_id == delegation.id
    assert resolved.on_behalf_of_user_id == delegator.id


def test_delegation_broader_than_delegators_actual_grant_is_clamped(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    delegator = _make_user(db, org_id=tree.org.id, label="delegator")
    delegate = _make_user(db, org_id=tree.org.id, label="delegate")
    # Delegator only actually holds the role at Entity 1 (not at Global).
    _make_grant(db, user=delegator, role=role, org_unit=tree.entity_1)

    now = utcnow()
    # Delegation record itself does not narrow the org unit at all (broadest
    # possible statement: "every unit I hold at").
    _make_delegation(
        db,
        delegator=delegator,
        delegate=delegate,
        role_id=None,
        org_unit_id=None,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    # The delegate can reach what the delegator actually holds (Entity 1 and
    # its descendant BU 1)...
    at_entity_1 = org_access.resolve_access(
        db, user=delegate, permission=APPROVE, org_unit_id=tree.entity_1.id
    )
    at_bu_1 = org_access.resolve_access(
        db, user=delegate, permission=APPROVE, org_unit_id=tree.bu_1.id
    )
    # ...but never more than that, even though the delegation record itself
    # named no narrower org unit.
    at_global = org_access.resolve_access(
        db, user=delegate, permission=APPROVE, org_unit_id=tree.global_unit.id
    )

    assert at_entity_1.allowed is True
    assert at_bu_1.allowed is True
    assert at_global.allowed is False


# --- FR-15/FR-16 / AC-12, AC-13: revocation and expiry, no caching ---------


def test_revoked_delegation_denies_access_immediately(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    delegator = _make_user(db, org_id=tree.org.id, label="delegator")
    delegate = _make_user(db, org_id=tree.org.id, label="delegate")
    _make_grant(db, user=delegator, role=role, org_unit=tree.global_unit)

    now = utcnow()
    delegation = _make_delegation(
        db,
        delegator=delegator,
        delegate=delegate,
        role_id=None,
        org_unit_id=None,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    before_revoke = org_access.resolve_access(
        db, user=delegate, permission=APPROVE, org_unit_id=tree.global_unit.id
    )
    assert before_revoke.allowed is True

    delegation.status = "revoked"
    delegation.deleted_at = utcnow()
    delegation.deleted_by_user_id = delegator.id
    db.flush()

    after_revoke = org_access.resolve_access(
        db, user=delegate, permission=APPROVE, org_unit_id=tree.global_unit.id
    )
    assert after_revoke.allowed is False
    assert after_revoke.reason == "no_grant"


def test_ended_delegation_denies_access(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    delegator = _make_user(db, org_id=tree.org.id, label="delegator")
    delegate = _make_user(db, org_id=tree.org.id, label="delegate")
    _make_grant(db, user=delegator, role=role, org_unit=tree.global_unit)

    now = utcnow()
    _make_delegation(
        db,
        delegator=delegator,
        delegate=delegate,
        role_id=None,
        org_unit_id=None,
        start_date=now - timedelta(days=10),
        end_date=now - timedelta(days=1),
    )

    resolved = org_access.resolve_access(
        db, user=delegate, permission=APPROVE, org_unit_id=tree.global_unit.id
    )

    assert resolved.allowed is False


# --- FR-17 / AC-14: no delegation chains ------------------------------------


def test_delegate_cannot_have_their_received_delegation_chain_further(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver", permission_values=[APPROVE])
    original_delegator = _make_user(db, org_id=tree.org.id, label="a")
    middle_delegate = _make_user(db, org_id=tree.org.id, label="b")
    final_delegate = _make_user(db, org_id=tree.org.id, label="c")
    _make_grant(db, user=original_delegator, role=role, org_unit=tree.global_unit)

    now = utcnow()
    # A delegates to B.
    _make_delegation(
        db,
        delegator=original_delegator,
        delegate=middle_delegate,
        role_id=None,
        org_unit_id=None,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )
    # B (who holds access only via A's delegation) attempts to "re-delegate"
    # to C by creating a further Delegation record naming themselves as
    # delegator. The resolver must never let this satisfy C's lookup.
    _make_delegation(
        db,
        delegator=middle_delegate,
        delegate=final_delegate,
        role_id=None,
        org_unit_id=None,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    # Sanity: B does get access via A's delegation.
    b_resolved = org_access.resolve_access(
        db, user=middle_delegate, permission=APPROVE, org_unit_id=tree.global_unit.id
    )
    assert b_resolved.allowed is True
    assert b_resolved.reason == "delegated_grant"

    # But C's lookup, which must recurse into B's resolution with
    # allow_delegated=False, must NOT see B's delegated (not native) access.
    c_resolved = org_access.resolve_access(
        db, user=final_delegate, permission=APPROVE, org_unit_id=tree.global_unit.id
    )
    assert c_resolved.allowed is False


# --- Regression: app.authority and app.walls stay untouched (FR-24, FR-25) -


def test_resolver_module_never_imports_authority_or_walls():
    """Regression for FR-24/FR-25: no ``import`` statement in this resolver
    references ``app.authority`` or ``app.walls`` — only prose in the module
    docstring may mention them (explaining why they're out of scope)."""
    import ast
    import inspect

    tree_ast = ast.parse(inspect.getsource(org_access))
    imported_modules: set[str] = set()
    for node in ast.walk(tree_ast):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("app.authority") for m in imported_modules)
    assert not any(m.startswith("app.walls") for m in imported_modules)


# --- AC-20 / FR-25: an ethical-wall denial overrides a resolver-allowed -----
# decision, exercised through the REAL composed code path.
#
# Investigation finding (see specs/002-org-hierarchy-rbac/verification.md
# section 5.1 and this task's report): there is currently NO single function
# that composes ``org_access.resolve_access`` with the ethical-wall check.
# No contract-facing route calls into this feature's resolver today (it is
# only wired into this feature's own org-unit/role-grant/delegation
# endpoints) — contract access instead goes entirely through
# ``app.contracts.access.user_can_access_contract`` /
# ``accessible_contract_filter``, which check ownership/admin/matter-
# membership/grants themselves (NOT via ``org_access.resolve_access``) and
# call ``app.walls.service.user_is_walled`` / ``wall_block_filter`` FIRST as
# an absolute deny-override (see ``app/contracts/access.py``, "Phase 3
# deny-overrides, evaluated BEFORE any allow layer").
#
# So the test below proves the AC-20 claim with two things held together,
# both against REAL production code (not a hand-rolled "and" of two
# independently invented checks):
#   (a) ``org_access.resolve_access`` — this feature's actual resolver —
#       says the user's grant ALLOWS the permission, at the relevant org
#       unit, via a role that also satisfies ``is_org_admin`` (the same
#       admin bypass ``user_can_access_contract`` would otherwise honor).
#   (b) ``app.contracts.access.user_can_access_contract`` — the REAL,
#       existing composed access-decision function contract-handling code
#       actually calls — still returns False for that same user against a
#       contract an ``EthicalWall`` has sealed them off from, and a control
#       assertion shows the identical user/contract pair (minus the wall)
#       WOULD have been allowed by admin bypass. This demonstrates the wall
#       overriding what would otherwise be an allow, on the one real
#       composed code path this codebase has today.
#
# The wall/principal rows are inserted via raw SQL (not the ``EthicalWall``/
# ``EthicalWallPrincipal`` ORM classes) so this test file itself continues to
# satisfy ``test_this_test_file_never_imports_authority_or_walls`` above —
# i.e. adding real behavioral coverage does not require weakening that
# existing regression.
#
# No production bug found: ``user_can_access_contract`` checks
# ``user_is_walled`` before any allow branch (including admin/owner), so the
# wall is never bypassed on this path.


def test_ethical_wall_denial_overrides_an_otherwise_allowed_admin_decision(
    db: Session, tree: Tree
):
    admin_role = _make_role(
        db,
        org_id=tree.org.id,
        name="wall-admin",
        permission_values=["admin_panel:access", "contract:read"],
    )
    user = _make_user(db, org_id=tree.org.id, label="walled-admin")
    _make_grant(db, user=user, role=admin_role, org_unit=tree.global_unit)
    owner = _make_user(db, org_id=tree.org.id, label="owner")

    # (a) This feature's resolver, on its own, says the permission is
    # granted at Global — and the same grant makes the user an org admin.
    resolved = org_access.resolve_access(
        db, user=user, permission="contract:read", org_unit_id=tree.global_unit.id
    )
    assert resolved.allowed is True
    assert is_org_admin(user) is True

    walled_contract = Contract(org_id=tree.org.id, title="Walled NDA", owner_user_id=owner.id)
    control_contract = Contract(org_id=tree.org.id, title="Unwalled NDA", owner_user_id=owner.id)
    db.add_all([walled_contract, control_contract])
    db.flush()

    # Control: without a wall, the real composed contract-access function
    # allows this admin user in (proving the "otherwise allowed" half of the
    # claim against real code, not an assumption).
    assert user_can_access_contract(db, contract=control_contract, user=user) is True

    now = utcnow()
    wall_id = new_uuid()
    db.execute(
        text(
            """
            INSERT INTO ethical_wall
                (id, org_id, name, reason, scope_type, scope_id, active,
                 deactivated_at, created_by_user_id, updated_by_user_id,
                 created_at, updated_at)
            VALUES
                (:id, :org_id, :name, :reason, :scope_type, :scope_id, :active,
                 NULL, :actor_id, :actor_id, :now, :now)
            """
        ),
        {
            "id": wall_id,
            "org_id": tree.org.id,
            "name": "Conflict screen",
            "reason": "resolver-vs-wall regression",
            "scope_type": "contract",
            "scope_id": walled_contract.id,
            "active": True,
            "actor_id": owner.id,
            "now": now,
        },
    )
    db.execute(
        text(
            """
            INSERT INTO ethical_wall_principal
                (id, org_id, wall_id, principal_type, principal_id, created_at, updated_at)
            VALUES
                (:id, :org_id, :wall_id, :principal_type, :principal_id, :now, :now)
            """
        ),
        {
            "id": new_uuid(),
            "org_id": tree.org.id,
            "wall_id": wall_id,
            "principal_type": "user",
            "principal_id": user.id,
            "now": now,
        },
    )
    db.flush()

    # (b) The real composed contract-access check denies the walled contract
    # to this same user, despite (a) — the resolver-allowed, admin-bypass
    # decision is overridden by the wall.
    assert user_can_access_contract(db, contract=walled_contract, user=user) is False


def test_this_test_file_never_imports_authority_or_walls():
    import ast
    import inspect
    import sys

    tree_ast = ast.parse(inspect.getsource(sys.modules[__name__]))
    imported_modules: set[str] = set()
    for node in ast.walk(tree_ast):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("app.authority") for m in imported_modules)
    assert not any(m.startswith("app.walls") for m in imported_modules)
