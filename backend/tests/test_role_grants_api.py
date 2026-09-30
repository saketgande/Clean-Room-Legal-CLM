"""Tests for the role-grant assign/revoke service (feature
002-org-hierarchy-rbac, T008 — ``app/org_structure/service.py`` role-grant
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
from app.org_structure.schemas import RoleGrantCreate
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
        self.org = _make_org(db, name="RoleGrantApiTest")
        self.root = _make_unit(db, org_id=self.org.id, name="Global", parent_id=None)
        self.region_a = _make_unit(db, org_id=self.org.id, name="Region A", parent_id=self.root.id)

        # Actor holds admin_panel:access + user:update_role at the root, so
        # they can grant anything they themselves hold (escalation guard) and
        # the org-unit-scoped resolver check passes at any descendant unit.
        self.admin_role = _make_role(
            db,
            org_id=self.org.id,
            name="admin",
            permission_values=["admin_panel:access", "user:update_role", "contract:read"],
        )
        self.actor = _make_user(db, org_id=self.org.id, label="actor")
        _make_grant(db, user=self.actor, role=self.admin_role, org_unit=self.root)

        self.target = _make_user(db, org_id=self.org.id, label="target")
        self.grantable_role = _make_role(
            db, org_id=self.org.id, name="reader", permission_values=["contract:read"]
        )


@pytest.fixture
def scenario(db: Session) -> Scenario:
    return Scenario(db)


# --- AC-22: assign/revoke happy path -----------------------------------------


def test_assign_role_grant_happy_path(db: Session, scenario: Scenario):
    result = service.create_role_grant(
        db,
        actor=scenario.actor,
        payload=RoleGrantCreate(
            user_id=scenario.target.id,
            role_id=scenario.grantable_role.id,
            org_unit_id=scenario.region_a.id,
        ),
    )
    assert result["user_id"] == scenario.target.id
    assert result["role_id"] == scenario.grantable_role.id
    assert result["org_unit_id"] == scenario.region_a.id
    assert result["is_active"] is True

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "role_grant.created", AuditLog.resource_id == result["id"]
        )
    )
    assert audit is not None
    assert audit.actor_user_id == scenario.actor.id


def test_revoke_role_grant_happy_path_and_audit(db: Session, scenario: Scenario):
    created = service.create_role_grant(
        db,
        actor=scenario.actor,
        payload=RoleGrantCreate(
            user_id=scenario.target.id,
            role_id=scenario.grantable_role.id,
            org_unit_id=scenario.region_a.id,
        ),
    )

    service.revoke_role_grant(db, actor=scenario.actor, grant_id=created["id"])

    grant = db.get(UserRoleGrant, created["id"])
    assert grant.deleted_at is not None
    assert grant.deleted_by_user_id == scenario.actor.id

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "role_grant.revoked", AuditLog.resource_id == created["id"]
        )
    )
    assert audit is not None
    assert audit.actor_user_id == scenario.actor.id
    assert audit.metadata_json["revocation"] is True


# --- duplicate active grant 409 ----------------------------------------------


def test_duplicate_active_grant_is_rejected(db: Session, scenario: Scenario):
    service.create_role_grant(
        db,
        actor=scenario.actor,
        payload=RoleGrantCreate(
            user_id=scenario.target.id,
            role_id=scenario.grantable_role.id,
            org_unit_id=scenario.region_a.id,
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        service.create_role_grant(
            db,
            actor=scenario.actor,
            payload=RoleGrantCreate(
                user_id=scenario.target.id,
                role_id=scenario.grantable_role.id,
                org_unit_id=scenario.region_a.id,
            ),
        )
    assert exc_info.value.status_code == 409


def test_revoking_an_already_revoked_grant_is_rejected(db: Session, scenario: Scenario):
    created = service.create_role_grant(
        db,
        actor=scenario.actor,
        payload=RoleGrantCreate(
            user_id=scenario.target.id,
            role_id=scenario.grantable_role.id,
            org_unit_id=scenario.region_a.id,
        ),
    )
    service.revoke_role_grant(db, actor=scenario.actor, grant_id=created["id"])
    with pytest.raises(HTTPException) as exc_info:
        service.revoke_role_grant(db, actor=scenario.actor, grant_id=created["id"])
    assert exc_info.value.status_code == 409


# --- valid_to < valid_from 422 -----------------------------------------------


def test_valid_to_before_valid_from_is_rejected(db: Session, scenario: Scenario):
    now = utcnow()
    with pytest.raises(HTTPException) as exc_info:
        service.create_role_grant(
            db,
            actor=scenario.actor,
            payload=RoleGrantCreate(
                user_id=scenario.target.id,
                role_id=scenario.grantable_role.id,
                org_unit_id=scenario.region_a.id,
                valid_from=now,
                valid_to=now - timedelta(days=1),
            ),
        )
    assert exc_info.value.status_code == 422


# --- soft-deleted org unit 422 ------------------------------------------------


def test_granting_at_a_soft_deleted_org_unit_is_rejected(db: Session, scenario: Scenario):
    scenario.region_a.deleted_at = utcnow()
    scenario.region_a.deleted_by_user_id = scenario.actor.id
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        service.create_role_grant(
            db,
            actor=scenario.actor,
            payload=RoleGrantCreate(
                user_id=scenario.target.id,
                role_id=scenario.grantable_role.id,
                org_unit_id=scenario.region_a.id,
            ),
        )
    assert exc_info.value.status_code == 422


# --- cross-org 404 ------------------------------------------------------------


def test_granting_to_a_user_in_another_org_is_404(db: Session, scenario: Scenario):
    other_org = _make_org(db, name="RoleGrantApiTestOtherOrg")
    other_user = _make_user(db, org_id=other_org.id, label="other-org-user")

    with pytest.raises(HTTPException) as exc_info:
        service.create_role_grant(
            db,
            actor=scenario.actor,
            payload=RoleGrantCreate(
                user_id=other_user.id,
                role_id=scenario.grantable_role.id,
                org_unit_id=scenario.region_a.id,
            ),
        )
    assert exc_info.value.status_code == 404


# --- 403 without user:update_role --------------------------------------------


def test_actor_without_the_permission_cannot_grant(db: Session, scenario: Scenario):
    plain_user = _make_user(db, org_id=scenario.org.id, label="plain")
    with pytest.raises(HTTPException) as exc_info:
        service.create_role_grant(
            db,
            actor=plain_user,
            payload=RoleGrantCreate(
                user_id=scenario.target.id,
                role_id=scenario.grantable_role.id,
                org_unit_id=scenario.region_a.id,
            ),
        )
    assert exc_info.value.status_code == 403


# --- escalation guard 403 -----------------------------------------------------


def test_actor_cannot_grant_a_role_broader_than_their_own_permissions(
    db: Session, scenario: Scenario
):
    # Actor holds only user:update_role (narrower than admin_panel:access) at
    # the root — enough to pass the router gate but not enough to grant a
    # role carrying a permission they don't themselves hold.
    narrow_role = _make_role(
        db, org_id=scenario.org.id, name="assigner", permission_values=["user:update_role"]
    )
    narrow_actor = _make_user(db, org_id=scenario.org.id, label="narrow-actor")
    _make_grant(db, user=narrow_actor, role=narrow_role, org_unit=scenario.root)

    escalating_role = _make_role(
        db, org_id=scenario.org.id, name="escalating", permission_values=["admin_panel:access"]
    )

    with pytest.raises(HTTPException) as exc_info:
        service.create_role_grant(
            db,
            actor=narrow_actor,
            payload=RoleGrantCreate(
                user_id=scenario.target.id,
                role_id=escalating_role.id,
                org_unit_id=scenario.region_a.id,
            ),
        )
    assert exc_info.value.status_code == 403
