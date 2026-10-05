"""Tests for the org-unit CRUD service (feature 002-org-hierarchy-rbac, T008 —
``app/org_structure/service.py`` org-unit functions + the router's permission
gate). Mirrors the fixture style of ``test_org_access_resolver.py``: a real
(migrated) Postgres session, one transaction per test, rolled back at
teardown.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid
from app.core.enums import UserStatus
from app.core.models import AuditLog
from app.org_structure import service
from app.org_structure.models import OrgUnit
from app.org_structure.schemas import OrgUnitCreate, OrgUnitUpdate
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


def _get_or_create_permission(db: Session, value: str) -> Permission:
    existing = db.query(Permission).filter(Permission.value == value).one_or_none()
    if existing is not None:
        return existing
    perm = Permission(value=value)
    db.add(perm)
    db.flush()
    return perm


def _make_admin(db: Session, *, org_id: str, root: OrgUnit) -> User:
    role = Role(org_id=org_id, name=f"admin-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    role.permissions = [_get_or_create_permission(db, "admin_panel:access")]
    db.add(role)
    db.flush()
    user = User(
        org_id=org_id,
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Org Admin",
        hashed_password="hash",
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    db.add(
        UserRoleGrant(
            org_id=org_id,
            user_id=user.id,
            role_id=role.id,
            org_unit_id=root.id,
            created_by_user_id=user.id,
        )
    )
    db.flush()
    return user


def _make_plain_user(db: Session, *, org_id: str) -> User:
    user = User(
        org_id=org_id,
        email=f"plain-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Plain User",
        hashed_password="hash",
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    return user


class Tree:
    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="OrgUnitApiTest")
        self.global_unit = _make_unit(db, org_id=self.org.id, name="Global", parent_id=None)
        self.region_a = _make_unit(
            db, org_id=self.org.id, name="Region A", parent_id=self.global_unit.id
        )
        self.entity_1 = _make_unit(
            db, org_id=self.org.id, name="Entity 1", parent_id=self.region_a.id
        )
        self.bu_1 = _make_unit(db, org_id=self.org.id, name="BU 1", parent_id=self.entity_1.id)
        self.bu_2 = _make_unit(db, org_id=self.org.id, name="BU 2", parent_id=self.entity_1.id)
        self.admin = _make_admin(db, org_id=self.org.id, root=self.global_unit)


@pytest.fixture
def tree(db: Session) -> Tree:
    return Tree(db)


# --- happy path -------------------------------------------------------------


def test_create_org_unit_under_a_parent(db: Session, tree: Tree):
    result = service.create_org_unit(
        db, actor=tree.admin, payload=OrgUnitCreate(name="Region B", parent_id=tree.global_unit.id)
    )
    assert result["name"] == "Region B"
    assert result["parent_id"] == tree.global_unit.id
    assert result["depth"] == 1
    assert result["path_names"] == ["Global", "Region B"]

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "org_unit.created", AuditLog.resource_id == result["id"]
        )
    )
    assert audit is not None
    assert audit.actor_user_id == tree.admin.id


def test_rename_org_unit(db: Session, tree: Tree):
    result = service.update_org_unit(
        db, actor=tree.admin, org_unit_id=tree.entity_1.id, payload=OrgUnitUpdate(name="Entity One")
    )
    assert result["name"] == "Entity One"
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "org_unit.updated", AuditLog.resource_id == tree.entity_1.id
        )
    )
    assert audit is not None


def test_reparent_org_unit_happy_path(db: Session, tree: Tree):
    result = service.update_org_unit(
        db,
        actor=tree.admin,
        org_unit_id=tree.bu_1.id,
        payload=OrgUnitUpdate(parent_id=tree.global_unit.id),
    )
    assert result["parent_id"] == tree.global_unit.id
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "org_unit.reparented", AuditLog.resource_id == tree.bu_1.id
        )
    )
    assert audit is not None
    assert audit.before == {"parent_id": tree.entity_1.id}
    assert audit.after == {"parent_id": tree.global_unit.id}


# --- FR-3 / AC-1: cycle prevention ------------------------------------------


def test_reparenting_into_own_descendant_is_rejected(db: Session, tree: Tree):
    with pytest.raises(HTTPException) as exc_info:
        service.update_org_unit(
            db,
            actor=tree.admin,
            org_unit_id=tree.region_a.id,
            payload=OrgUnitUpdate(parent_id=tree.bu_1.id),
        )
    assert exc_info.value.status_code == 409

    db.refresh(tree.region_a)
    assert tree.region_a.parent_id == tree.global_unit.id


def test_reparenting_a_unit_to_itself_is_rejected(db: Session, tree: Tree):
    with pytest.raises(HTTPException) as exc_info:
        service.update_org_unit(
            db,
            actor=tree.admin,
            org_unit_id=tree.entity_1.id,
            payload=OrgUnitUpdate(parent_id=tree.entity_1.id),
        )
    assert exc_info.value.status_code == 409


# --- FR-4 / AC-2: single root ------------------------------------------------


def test_creating_a_second_root_is_rejected(db: Session, tree: Tree):
    with pytest.raises(HTTPException) as exc_info:
        service.create_org_unit(
            db, actor=tree.admin, payload=OrgUnitCreate(name="Another Global", parent_id=None)
        )
    assert exc_info.value.status_code == 409


def test_clearing_a_parent_while_a_root_exists_is_rejected(db: Session, tree: Tree):
    with pytest.raises(HTTPException) as exc_info:
        service.update_org_unit(
            db, actor=tree.admin, org_unit_id=tree.region_a.id, payload=OrgUnitUpdate(parent_id=None)
        )
    assert exc_info.value.status_code == 409


# --- root-with-descendants delete 409 ---------------------------------------


def test_deleting_the_root_while_it_has_descendants_is_rejected(db: Session, tree: Tree):
    with pytest.raises(HTTPException) as exc_info:
        service.delete_org_unit(db, actor=tree.admin, org_unit_id=tree.global_unit.id)
    assert exc_info.value.status_code == 409


def test_deleting_an_already_deleted_unit_is_rejected(db: Session, tree: Tree):
    service.delete_org_unit(db, actor=tree.admin, org_unit_id=tree.bu_1.id)
    with pytest.raises(HTTPException) as exc_info:
        service.delete_org_unit(db, actor=tree.admin, org_unit_id=tree.bu_1.id)
    assert exc_info.value.status_code == 409


# --- AC-18: cross-org isolation ----------------------------------------------


def test_referencing_an_org_unit_from_another_org_is_404(db: Session, tree: Tree):
    other_org = _make_org(db, name="OrgUnitApiTestOtherOrg")
    other_root = _make_unit(db, org_id=other_org.id, name="Global", parent_id=None)

    with pytest.raises(HTTPException) as exc_info:
        service.create_org_unit(
            db, actor=tree.admin, payload=OrgUnitCreate(name="Region X", parent_id=other_root.id)
        )
    assert exc_info.value.status_code == 404


# --- missing-permission 403 --------------------------------------------------


def test_actor_without_admin_permission_cannot_manage_org_units(db: Session, tree: Tree):
    plain_user = _make_plain_user(db, org_id=tree.org.id)
    with pytest.raises(HTTPException) as exc_info:
        service.create_org_unit(
            db,
            actor=plain_user,
            payload=OrgUnitCreate(name="Region C", parent_id=tree.global_unit.id),
        )
    assert exc_info.value.status_code == 403


# --- FR-5 / AC-8: auto re-parent on delete -----------------------------------


def test_deleting_a_unit_reparents_its_children_and_audits_each_move(db: Session, tree: Tree):
    result = service.delete_org_unit(db, actor=tree.admin, org_unit_id=tree.entity_1.id)

    assert result["deleted_org_unit_id"] == tree.entity_1.id
    moved_ids = {entry["org_unit_id"] for entry in result["reparented"]}
    assert moved_ids == {tree.bu_1.id, tree.bu_2.id}
    for entry in result["reparented"]:
        assert entry["from_parent_id"] == tree.entity_1.id
        assert entry["to_parent_id"] == tree.region_a.id

    db.refresh(tree.bu_1)
    db.refresh(tree.bu_2)
    assert tree.bu_1.parent_id == tree.region_a.id
    assert tree.bu_2.parent_id == tree.region_a.id

    reparent_audits = db.scalars(
        select(AuditLog).where(AuditLog.action == "org_unit.reparented_on_delete")
    ).all()
    reparented_child_ids = {a.resource_id for a in reparent_audits}
    assert {tree.bu_1.id, tree.bu_2.id} <= reparented_child_ids
    for audit in reparent_audits:
        if audit.resource_id in (tree.bu_1.id, tree.bu_2.id):
            assert audit.metadata_json["consequence_of_org_unit_id"] == tree.entity_1.id

    delete_audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "org_unit.deleted", AuditLog.resource_id == tree.entity_1.id
        )
    )
    assert delete_audit is not None
    assert set(delete_audit.metadata_json["reparented_child_ids"]) == {tree.bu_1.id, tree.bu_2.id}


# --- FR-20: deleted_by_user_id always set -------------------------------------


def test_soft_delete_records_the_deleting_actor(db: Session, tree: Tree):
    service.delete_org_unit(db, actor=tree.admin, org_unit_id=tree.bu_1.id)
    db.refresh(tree.bu_1)
    assert tree.bu_1.deleted_at is not None
    assert tree.bu_1.deleted_by_user_id == tree.admin.id
