"""Tests for ``org_access.users_holding_role`` — the inverse-direction
"who holds this role" query (feature 004-approval-chain-reconciliation,
FR-18) added additively to ``app/core/org_access.py`` alongside the shared
``_grant_validity_clause`` extraction used by both ``active_grants_for_user``
and this new function.

Follows the same fixture/tree conventions as
``test_org_access_resolver.py`` (feature 002) so both suites stay consistent.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core import org_access
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
    db: Session,
    *,
    org_id: str,
    name: str,
    permission_values: list[str] | None = None,
    allows_hierarchy_rollup: bool = True,
) -> Role:
    role = Role(
        org_id=org_id,
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        allows_hierarchy_rollup=allows_hierarchy_rollup,
    )
    role.permissions = [_get_or_create_permission(db, v) for v in (permission_values or [])]
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
        self.org = _make_org(db, name="RoleHoldersTest")
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


# --- rollup up / no reverse rollup ------------------------------------------


def test_role_held_at_global_satisfies_query_scoped_to_a_descendant_unit(
    db: Session, tree: Tree
):
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.global_unit)

    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.bu_1.id
    )

    assert {h.user_id for h in holders} == {user.id}
    assert holders[0].via_delegation_id is None


def test_role_held_at_a_child_does_not_satisfy_a_query_at_the_parent(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.bu_1)

    at_bu1 = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.bu_1.id
    )
    at_parent = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.entity_1.id
    )

    assert {h.user_id for h in at_bu1} == {user.id}
    assert at_parent == []


def test_no_rollup_role_is_locked_to_its_exact_granted_unit(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="locked", allows_hierarchy_rollup=False)
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user, role=role, org_unit=tree.entity_1)

    at_exact = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.entity_1.id
    )
    at_child = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.bu_1.id
    )

    assert {h.user_id for h in at_exact} == {user.id}
    assert at_child == []


# --- expiry / soft-delete ----------------------------------------------------


def test_expired_grant_is_excluded(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(
        db,
        user=user,
        role=role,
        org_unit=tree.global_unit,
        valid_to=utcnow() - timedelta(days=1),
    )

    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.global_unit.id
    )

    assert holders == []


def test_soft_deleted_grant_is_excluded(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="approver")
    user = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(
        db,
        user=user,
        role=role,
        org_unit=tree.global_unit,
        valid_to=utcnow() + timedelta(days=365),
        deleted_at=utcnow(),
    )

    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.global_unit.id
    )

    assert holders == []


# --- delegation inclusion / exclusion ---------------------------------------


def test_delegate_included_when_include_delegates_true(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
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

    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.global_unit.id
    )

    holders_by_id = {h.user_id: h for h in holders}
    assert delegator.id in holders_by_id
    assert delegate.id in holders_by_id
    assert holders_by_id[delegate.id].via_delegation_id == delegation.id
    assert holders_by_id[delegate.id].on_behalf_of_user_id == delegator.id
    assert holders_by_id[delegator.id].via_delegation_id is None


def test_delegate_excluded_when_include_delegates_false(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
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
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    holders = org_access.users_holding_role(
        db,
        org_id=tree.org.id,
        role_id=role.id,
        org_unit_id=tree.global_unit.id,
        include_delegates=False,
    )

    assert {h.user_id for h in holders} == {delegator.id}


def test_native_holder_takes_precedence_over_delegated(db: Session, tree: Tree):
    """A user who is both a native holder AND would qualify via delegation is
    deduplicated to the native entry (native precedence)."""
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
    delegator = _make_user(db, org_id=tree.org.id, label="delegator")
    dual_user = _make_user(db, org_id=tree.org.id, label="dual")
    _make_grant(db, user=delegator, role=role, org_unit=tree.global_unit)
    _make_grant(db, user=dual_user, role=role, org_unit=tree.global_unit)

    now = utcnow()
    _make_delegation(
        db,
        delegator=delegator,
        delegate=dual_user,
        role_id=None,
        org_unit_id=None,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.global_unit.id
    )

    holders_by_id = {h.user_id: h for h in holders}
    assert holders_by_id[dual_user.id].via_delegation_id is None


def test_delegation_narrowed_by_role_id_that_does_not_match_is_excluded(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
    other_role = _make_role(db, org_id=tree.org.id, name="other-role")
    delegator = _make_user(db, org_id=tree.org.id, label="delegator")
    delegate = _make_user(db, org_id=tree.org.id, label="delegate")
    _make_grant(db, user=delegator, role=role, org_unit=tree.global_unit)

    now = utcnow()
    _make_delegation(
        db,
        delegator=delegator,
        delegate=delegate,
        role_id=other_role.id,
        org_unit_id=None,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )

    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.global_unit.id
    )

    assert {h.user_id for h in holders} == {delegator.id}


def test_ended_delegation_excludes_the_delegate(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
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

    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=tree.global_unit.id
    )

    assert {h.user_id for h in holders} == {delegator.id}


# --- cross-org isolation ------------------------------------------------


def test_cross_org_isolation(db: Session, tree: Tree):
    org_b = _make_org(db, name="RoleHoldersTestOtherOrg")
    other_root = _make_unit(db, org_id=org_b.id, name="Global", parent_id=None)
    role_b = _make_role(db, org_id=org_b.id, name="finance-approver")
    role_a = _make_role(db, org_id=tree.org.id, name="finance-approver")
    user_a = _make_user(db, org_id=tree.org.id, label="u1")
    _make_grant(db, user=user_a, role=role_a, org_unit=tree.global_unit)

    # Querying org B for org A's role id must yield nothing (role not found in org B).
    holders = org_access.users_holding_role(
        db, org_id=org_b.id, role_id=role_a.id, org_unit_id=other_root.id
    )
    assert holders == []

    # Querying org B's own role at org B's own unit, with no grants, is empty too.
    holders_b = org_access.users_holding_role(
        db, org_id=org_b.id, role_id=role_b.id, org_unit_id=other_root.id
    )
    assert holders_b == []


def test_unknown_org_unit_returns_empty(db: Session, tree: Tree):
    role = _make_role(db, org_id=tree.org.id, name="finance-approver")
    holders = org_access.users_holding_role(
        db, org_id=tree.org.id, role_id=role.id, org_unit_id=new_uuid()
    )
    assert holders == []
