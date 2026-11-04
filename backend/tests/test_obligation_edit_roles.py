"""Who may change an obligation: the role key (obligation:update) handed to member and legal_reviewer, and the
contract key (owner, creator, admin or an update-level share) that still decides which contracts. Real rows in
one rolled-back transaction, RBAC on (dev compose runs with it off, which makes every user an admin)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.auth.service import AuthService
from app.contracts.models import Contract
from app.contracts.service import ContractService
from app.core import org_access
from app.core.config import settings
from app.core.database import engine, new_uuid
from app.core.enums import UserStatus
from app.core.rbac import DEFAULT_ROLE_PERMISSIONS
from app.grants.models import ResourceGrant
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization

MIGRATION = Path(__file__).parents[1] / "alembic" / "versions" / "0077_obligation_edit_roles.py"


def _migration():
    spec = importlib.util.spec_from_file_location("m0077_obligation_edit_roles", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "disable_rbac", False)
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def org(db):
    row = Organization(id=new_uuid(), name="Roles org", slug=f"roles-{uuid.uuid4().hex[:8]}")
    db.add(row)
    db.flush()
    return row


def _person(db: Session, org: Organization, role: Role) -> User:
    unit = db.query(OrgUnit).filter_by(org_id=org.id).first()
    if unit is None:
        unit = OrgUnit(org_id=org.id, name="HQ", parent_id=None)
        db.add(unit)
        db.flush()
    user = User(org_id=org.id, email=f"u-{uuid.uuid4().hex[:8]}@example.com", full_name="Person",
                hashed_password="hash", status=UserStatus.ACTIVE)
    db.add(user)
    db.flush()
    db.add(UserRoleGrant(user_id=user.id, role_id=role.id, org_id=org.id, org_unit_id=unit.id))
    db.flush()
    return user


def _can(db: Session, user: User, permission: str) -> bool:
    return permission in org_access.effective_permission_values(db, user=user)


def test_the_defaults_give_obligation_update_to_member_and_legal_reviewer_only():
    """Guards the new organisations: the role key is handed to the two roles whose people own contracts, not
    to approvers (who cannot even open the page)."""
    assert "obligation:update" in DEFAULT_ROLE_PERMISSIONS["member"]
    assert "obligation:update" in DEFAULT_ROLE_PERMISSIONS["legal_reviewer"]
    assert "obligation:update" not in DEFAULT_ROLE_PERMISSIONS["approver"]
    assert "obligation:update" in DEFAULT_ROLE_PERMISSIONS["admin"]


def test_a_new_organisations_owner_roles_can_change_obligations_and_an_approver_cannot(db, org):
    """Guards the reminder owner being stranded: they got the emails and could not tick anything off."""
    roles = AuthService(db).bootstrap_roles(org.id)
    db.flush()
    member = _person(db, org, roles["member"])
    reviewer = _person(db, org, roles["legal_reviewer"])
    approver = _person(db, org, roles["approver"])

    assert _can(db, member, "obligation:update") and _can(db, reviewer, "obligation:update")
    assert not _can(db, approver, "obligation:update")


def test_the_contract_rule_still_confines_a_member_to_their_own_contracts(db, org):
    """Guards the role key becoming a master key: a member with obligation:update still cannot change what
    hangs off a contract they only hold a read share on, or cannot open at all."""
    roles = AuthService(db).bootstrap_roles(org.id)
    db.flush()
    member = _person(db, org, roles["member"])
    other = _person(db, org, roles["member"])
    mine = Contract(org_id=org.id, title="Mine", owner_user_id=member.id)
    theirs = Contract(org_id=org.id, title="Theirs", owner_user_id=other.id)
    db.add_all([mine, theirs])
    db.flush()
    service = ContractService(db)

    assert service.get_contract_for_user(contract_id=mine.id, user=member, edit=True).id == mine.id
    with pytest.raises(HTTPException) as unseen:
        service.get_contract_for_user(contract_id=theirs.id, user=member, edit=True)
    assert unseen.value.status_code == 404

    db.add(ResourceGrant(org_id=org.id, principal_type="user", principal_id=member.id, resource_type="contract",
                         resource_id=theirs.id, access_level="read"))
    db.flush()
    with pytest.raises(HTTPException) as reader:
        service.get_contract_for_user(contract_id=theirs.id, user=member, edit=True)
    assert reader.value.status_code == 403


def _roles_without_the_permission(db: Session, org: Organization) -> dict[str, Role]:
    """The state an existing organisation is in: built-in roles that hold only obligation:read."""
    read = db.query(Permission).filter_by(value="obligation:read").one_or_none() or Permission(value="obligation:read")
    update = db.query(Permission).filter_by(value="obligation:update").one_or_none() or Permission(
        value="obligation:update")
    db.add_all([read, update])
    roles = {}
    for name in ("member", "legal_reviewer", "approver", "outside_counsel"):
        roles[name] = Role(org_id=org.id, name=name, description=name)
        roles[name].permissions = [read]
        db.add(roles[name])
    admin = Role(org_id=org.id, name="admin", description="admin")
    admin.permissions = [read, update]
    roles["admin"] = admin
    db.add(admin)
    db.flush()
    return roles


def _values(db: Session, role: Role) -> set[str]:
    db.refresh(role)
    return {p.value for p in role.permissions}


def test_the_migration_gives_existing_member_and_legal_reviewer_roles_the_permission(db, org):
    """Guards an existing organisation staying admin-only: the new defaults only reach organisations created
    after them, so this migration adds the same permission, to those two roles and no others."""
    roles = _roles_without_the_permission(db, org)

    added = _migration().grant_obligation_update(db.connection())

    assert added >= 2
    assert "obligation:update" in _values(db, roles["member"])
    assert "obligation:update" in _values(db, roles["legal_reviewer"])
    assert "obligation:update" not in _values(db, roles["approver"])
    assert "obligation:update" not in _values(db, roles["outside_counsel"])  # a custom role is the org's own
    assert "obligation:read" in _values(db, roles["member"])  # nothing is removed


def test_the_migration_is_additive_and_safe_to_run_twice(db, org):
    """Guards a second run, or a role an admin already gave the permission to, ending in duplicates or an error."""
    roles = _roles_without_the_permission(db, org)
    migration = _migration()
    migration.grant_obligation_update(db.connection())
    before = _values(db, roles["member"])

    assert migration.grant_obligation_update(db.connection()) == 0
    assert _values(db, roles["member"]) == before


def test_the_downgrade_takes_the_permission_back_from_those_two_roles_only(db, org):
    """Guards the reverse touching anything else: approvers, custom roles and the admin keep what they had."""
    roles = _roles_without_the_permission(db, org)
    migration = _migration()
    migration.grant_obligation_update(db.connection())

    migration.revoke_obligation_update(db.connection())

    assert "obligation:update" not in _values(db, roles["member"])
    assert "obligation:update" not in _values(db, roles["legal_reviewer"])
    assert "obligation:update" in _values(db, roles["admin"])
    assert "obligation:read" in _values(db, roles["member"])
