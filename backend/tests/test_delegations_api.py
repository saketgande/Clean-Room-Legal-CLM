"""Tests for the delegation create/revoke service (feature
002-org-hierarchy-rbac, T008 — ``app/org_structure/service.py`` delegation
functions). Same real-Postgres, one-transaction-per-test fixture style as
``test_org_access_resolver.py``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import UserStatus
from app.core.models import AuditLog
from app.org_structure import service
from app.org_structure.models import OrgUnit
from app.org_structure.schemas import DelegationCreate, OrgUnitCreate
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


def _make_role(db: Session, *, org_id: str, name: str, permission_values: list[str]) -> Role:
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    role.permissions = [_get_or_create_permission(db, v) for v in permission_values]
    db.add(role)
    db.flush()
    return role


def _make_user(db: Session, *, org_id: str, label: str = "user") -> User:
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
        org_id=user.org_id,
        user_id=user.id,
        role_id=role.id,
        org_unit_id=org_unit.id,
        created_by_user_id=user.id,
    )
    db.add(grant)
    db.flush()
    return grant


class Scenario:
    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="DelegationApiTest")
        self.root = _make_unit(db, org_id=self.org.id, name="Global", parent_id=None)
        self.region_a = _make_unit(db, org_id=self.org.id, name="Region A", parent_id=self.root.id)

        # A role carrying every permission this domain's endpoints check
        # (admin_panel:access, user:update_role, delegation:manage), so the
        # delegator can act as both "org admin" and "ordinary self-service
        # delegator" as each test needs.
        self.full_role = _make_role(
            db,
            org_id=self.org.id,
            name="admin",
            permission_values=["admin_panel:access", "user:update_role", "delegation:manage"],
        )
        self.delegator = _make_user(db, org_id=self.org.id, label="delegator")
        _make_grant(db, user=self.delegator, role=self.full_role, org_unit=self.root)

        self.delegate = _make_user(db, org_id=self.org.id, label="delegate")
        # The delegate needs delegation:manage themselves to call the
        # delegation endpoints at all (it's self-service) — grant it at root
        # via a narrow role with no other permissions.
        self.self_service_role = _make_role(
            db, org_id=self.org.id, name="member", permission_values=["delegation:manage"]
        )
        _make_grant(db, user=self.delegate, role=self.self_service_role, org_unit=self.root)


@pytest.fixture
def scenario(db: Session) -> Scenario:
    return Scenario(db)


def _window():
    now = utcnow()
    return now - timedelta(days=1), now + timedelta(days=1)


# --- happy path ---------------------------------------------------------------


def test_create_and_revoke_delegation_happy_path(db: Session, scenario: Scenario):
    start, end = _window()
    created = service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id,
            start_date=start,
            end_date=end,
        ),
    )
    assert created["delegator_user_id"] == scenario.delegator.id
    assert created["delegate_user_id"] == scenario.delegate.id
    assert created["status"] == "active"
    assert created["is_active"] is True

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "delegation.created", AuditLog.resource_id == created["id"]
        )
    )
    assert audit is not None

    revoked = service.revoke_delegation(db, actor=scenario.delegator, delegation_id=created["id"])
    assert revoked["status"] == "revoked"
    assert revoked["revoked_by_user_id"] == scenario.delegator.id

    revoke_audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "delegation.revoked", AuditLog.resource_id == created["id"]
        )
    )
    assert revoke_audit is not None


# --- self-delegation 422 -------------------------------------------------------


def test_self_delegation_is_rejected(db: Session, scenario: Scenario):
    start, end = _window()
    with pytest.raises(HTTPException) as exc_info:
        service.create_delegation(
            db,
            actor=scenario.delegator,
            payload=DelegationCreate(
                delegate_user_id=scenario.delegator.id,
                start_date=start,
                end_date=end,
            ),
        )
    assert exc_info.value.status_code == 422


# --- end < start 422 ------------------------------------------------------------


def test_end_before_start_is_rejected(db: Session, scenario: Scenario):
    now = utcnow()
    with pytest.raises(HTTPException) as exc_info:
        service.create_delegation(
            db,
            actor=scenario.delegator,
            payload=DelegationCreate(
                delegate_user_id=scenario.delegate.id,
                start_date=now,
                end_date=now - timedelta(days=1),
            ),
        )
    assert exc_info.value.status_code == 422


# --- FR-18 / AC-15: only the delegator or an admin may revoke ------------------


def test_delegate_cannot_revoke_their_own_received_delegation(db: Session, scenario: Scenario):
    start, end = _window()
    created = service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id, start_date=start, end_date=end
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        service.revoke_delegation(db, actor=scenario.delegate, delegation_id=created["id"])
    assert exc_info.value.status_code == 403


def test_org_admin_can_revoke_someone_elses_delegation(db: Session, scenario: Scenario):
    start, end = _window()
    created = service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id, start_date=start, end_date=end
        ),
    )
    admin = _make_user(db, org_id=scenario.org.id, label="other-admin")
    _make_grant(db, user=admin, role=scenario.full_role, org_unit=scenario.root)

    revoked = service.revoke_delegation(db, actor=admin, delegation_id=created["id"])
    assert revoked["status"] == "revoked"
    assert revoked["revoked_by_user_id"] == admin.id


def test_admin_with_expired_grant_cannot_revoke_someone_elses_delegation(
    db: Session, scenario: Scenario
):
    """FR-9 regression (T017): an admin_panel:access grant that is expired
    (``valid_to`` in the past) but not soft-deleted must not confer
    admin-equivalence on this feature's revoke-on-behalf-of-another check."""
    start, end = _window()
    created = service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id, start_date=start, end_date=end
        ),
    )
    expired_admin = _make_user(db, org_id=scenario.org.id, label="expired-admin")
    grant = _make_grant(db, user=expired_admin, role=scenario.full_role, org_unit=scenario.root)
    grant.valid_to = utcnow() - timedelta(days=1)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        service.revoke_delegation(db, actor=expired_admin, delegation_id=created["id"])
    assert exc_info.value.status_code == 403


def test_admin_with_expired_grant_cannot_view_all_delegations(db: Session, scenario: Scenario):
    """FR-9 regression (T017): expired admin_panel:access must not pass the
    view-all-delegations admin-equivalence check either."""
    expired_admin = _make_user(db, org_id=scenario.org.id, label="expired-admin-viewer")
    grant = _make_grant(db, user=expired_admin, role=scenario.full_role, org_unit=scenario.root)
    grant.valid_to = utcnow() - timedelta(days=1)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        service.list_delegations(db, actor=expired_admin, direction="all")
    assert exc_info.value.status_code == 403


def test_admin_with_expired_grant_cannot_create_delegation_on_behalf_of_another(
    db: Session, scenario: Scenario
):
    """FR-9 regression (T017): expired admin_panel:access must not pass the
    create-delegation-on-behalf-of-another admin-equivalence check either."""
    start, end = _window()
    expired_admin = _make_user(db, org_id=scenario.org.id, label="expired-admin-creator")
    grant = _make_grant(db, user=expired_admin, role=scenario.full_role, org_unit=scenario.root)
    grant.valid_to = utcnow() - timedelta(days=1)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        service.create_delegation(
            db,
            actor=expired_admin,
            payload=DelegationCreate(
                delegator_user_id=scenario.delegator.id,
                delegate_user_id=scenario.delegate.id,
                start_date=start,
                end_date=end,
            ),
        )
    assert exc_info.value.status_code == 403


def test_revoking_an_already_revoked_delegation_is_rejected(db: Session, scenario: Scenario):
    start, end = _window()
    created = service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id, start_date=start, end_date=end
        ),
    )
    service.revoke_delegation(db, actor=scenario.delegator, delegation_id=created["id"])
    with pytest.raises(HTTPException) as exc_info:
        service.revoke_delegation(db, actor=scenario.delegator, delegation_id=created["id"])
    assert exc_info.value.status_code == 409


# --- FR-17 / AC-14: no delegation chains ---------------------------------------


def test_a_delegate_cannot_re_delegate_access_held_only_via_delegation(
    db: Session, scenario: Scenario
):
    start, end = _window()
    # The delegator's `full_role` (admin_panel:access, user:update_role,
    # delegation:manage) reaches the delegate ONLY through this delegation —
    # the delegate holds no UserRoleGrant of that role themselves.
    service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id, start_date=start, end_date=end
        ),
    )
    third_party = _make_user(db, org_id=scenario.org.id, label="third-party")

    with pytest.raises(HTTPException) as exc_info:
        service.create_delegation(
            db,
            actor=scenario.delegate,
            payload=DelegationCreate(
                delegator_user_id=scenario.delegate.id,
                delegate_user_id=third_party.id,
                role_id=scenario.full_role.id,
                start_date=start,
                end_date=end,
            ),
        )
    assert exc_info.value.status_code == 403


# --- AC-23: can_revoke is false on a received delegation -----------------------


def test_can_revoke_is_false_for_the_delegate_true_for_the_delegator(
    db: Session, scenario: Scenario
):
    start, end = _window()
    service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id, start_date=start, end_date=end
        ),
    )

    mine = service.list_delegations(db, actor=scenario.delegator, direction="mine")
    received = service.list_delegations(db, actor=scenario.delegate, direction="received")

    assert mine[0]["can_revoke"] is True
    assert received[0]["can_revoke"] is False


# --- FR-21 / AC-16: delegated-action audit attribution -------------------------


def test_action_performed_under_delegation_records_both_actor_and_delegator(
    db: Session, scenario: Scenario
):
    start, end = _window()
    delegation = service.create_delegation(
        db,
        actor=scenario.delegator,
        payload=DelegationCreate(
            delegate_user_id=scenario.delegate.id, start_date=start, end_date=end
        ),
    )

    # The delegate holds admin_panel:access only via the delegator's
    # delegation (no native grant of their own) — creating an org unit
    # exercises assert_access's delegated pass end to end.
    created_unit = service.create_org_unit(
        db,
        actor=scenario.delegate,
        payload=OrgUnitCreate(name="Delegated Region", parent_id=scenario.region_a.id),
    )

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "org_unit.created", AuditLog.resource_id == created_unit["id"]
        )
    )
    assert audit is not None
    assert audit.actor_user_id == scenario.delegate.id
    assert audit.metadata_json["acting_user_id"] == scenario.delegate.id
    assert audit.metadata_json["on_behalf_of_user_id"] == scenario.delegator.id
    assert audit.metadata_json["delegation_id"] == delegation["id"]
