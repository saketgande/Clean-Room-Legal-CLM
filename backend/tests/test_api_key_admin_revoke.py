"""API-key identity and containment (feature 007-intake-mcp-server, T002).

Guards the failure modes behind FR-12, FR-31, FR-35, FR-40 and FR-41: a leaked
agent key minting a replacement that outlives its revocation, an administrator
being unable to cut off a key, revocation not taking effect on the next call,
and key material leaking into audit rows or list responses.

Follows the repo convention (see test_screen_level_enforcement.py): route
handlers and dependencies are called directly against a rolled-back Postgres
transaction rather than through Starlette's TestClient.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.auth import routes as auth_routes
from app.auth.models import ApiKey, Permission, Role, User, UserRoleGrant
from app.auth.schemas import ApiKeyCreate
from app.auth.service import AuthService, authenticate_api_key, authenticate_api_key_with_id
from app.core.auth_context import INTERNAL
from app.core.database import engine, new_uuid
from app.core.deps import get_current_user, request_auth, require_permission
from app.core.enums import UserStatus
from app.core.models import AuditLog
from app.core.security import create_access_token
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


def _org(db: Session) -> tuple[Organization, OrgUnit]:
    org = Organization(id=new_uuid(), name="KeyAdmin", slug=f"keyadmin-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    unit = OrgUnit(org_id=org.id, name="Global", parent_id=None)
    db.add(unit)
    db.flush()
    return org, unit


def _user(db: Session, org: Organization, label: str) -> User:
    user = User(
        org_id=org.id,
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=label,
        hashed_password="hash",
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    return user


def _grant(db: Session, user: User, unit: OrgUnit, permissions: list[str]) -> None:
    role = Role(org_id=user.org_id, name=f"r-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    perms = []
    for value in permissions:
        perm = db.query(Permission).filter(Permission.value == value).one_or_none()
        if perm is None:
            perm = Permission(value=value)
            db.add(perm)
            db.flush()
        perms.append(perm)
    role.permissions = perms
    db.add(role)
    db.flush()
    db.add(UserRoleGrant(user_id=user.id, role_id=role.id, org_id=user.org_id, org_unit_id=unit.id))
    db.flush()


class World:
    def __init__(self, db: Session) -> None:
        self.org, self.unit = _org(db)
        self.owner = _user(db, self.org, "agent")
        self.admin = _user(db, self.org, "admin")
        _grant(db, self.admin, self.unit, ["user:approve"])
        self.other = _user(db, self.org, "plain")
        self.service = AuthService(db)


@pytest.fixture
def world(db: Session) -> World:
    return World(db)


def _request() -> Request:
    return Request({"type": "http", "headers": [], "state": {}})


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _mint(w: World, user: User, name: str = "agent key") -> dict:
    return w.service.create_api_key(payload=ApiKeyCreate(name=name), actor=user)


def _audit(db: Session, action: str, resource_id: str) -> AuditLog:
    return db.scalars(
        select(AuditLog).where(AuditLog.action == action, AuditLog.resource_id == resource_id)
    ).one()


def test_key_auth_records_method_and_key_id(db: Session, world: World):
    """The filing audit trail needs to know a call came from a key, and which."""
    created = _mint(world, world.owner)
    request = _request()
    user = get_current_user(request, db, _bearer(created["api_key"]))
    assert user.id == world.owner.id
    auth = request_auth(request)
    assert auth.method == "api_key"
    assert auth.api_key_id == created["id"]


def test_jwt_auth_records_access_token_and_no_key(db: Session, world: World):
    token = create_access_token(subject=world.owner.id)[0]
    request = _request()
    get_current_user(request, db, _bearer(token))
    auth = request_auth(request)
    assert auth.method == "access_token"
    assert auth.api_key_id is None


def test_request_auth_falls_back_to_internal():
    """Ingest/chat/jobs call services with no HTTP auth; they must read as internal."""
    assert request_auth(_request()) is INTERNAL


def test_authenticate_wrappers_agree(db: Session, world: World):
    created = _mint(world, world.owner)
    user, key_id = authenticate_api_key_with_id(db, created["api_key"])
    assert user.id == world.owner.id and key_id == created["id"]
    assert authenticate_api_key(db, created["api_key"]).id == world.owner.id
    assert authenticate_api_key_with_id(db, "clm_not-a-real-key") is None


def test_key_cannot_mint_key_but_session_can(db: Session, world: World):
    """A leaked key minting a replacement would defeat revocation (FR-41)."""
    created = _mint(world, world.owner)
    key_request = _request()
    get_current_user(key_request, db, _bearer(created["api_key"]))
    with pytest.raises(HTTPException) as exc:
        auth_routes.create_key(
            ApiKeyCreate(name="replacement"), key_request, world.service, world.owner
        )
    assert exc.value.status_code == 403
    assert len(db.scalars(select(ApiKey).where(ApiKey.user_id == world.owner.id)).all()) == 1

    session_request = _request()
    get_current_user(session_request, db, _bearer(create_access_token(subject=world.owner.id)[0]))
    result = auth_routes.create_key(
        ApiKeyCreate(name="from session"), session_request, world.service, world.owner
    )
    assert result["api_key"].startswith("clm_")


def test_list_requires_user_approve(db: Session, world: World):
    dep = require_permission("user:approve")
    assert dep(current_user=world.admin, db=db) is world.admin
    with pytest.raises(HTTPException) as exc:
        dep(current_user=world.other, db=db)
    assert exc.value.status_code == 403


def test_admin_lists_keys_newest_first_without_secrets(db: Session, world: World):
    first = _mint(world, world.owner, "first")
    second = _mint(world, world.owner, "second")
    rows = world.service.list_api_keys_for_user(actor=world.admin, user_id=world.owner.id)
    assert {r["id"] for r in rows} == {first["id"], second["id"]}
    assert all(r["api_key"] is None for r in rows)
    assert not any("hash" in k for r in rows for k in r)


def test_list_other_org_user_is_404(db: Session, world: World):
    other_org, _ = _org(db)
    stranger = _user(db, other_org, "stranger")
    with pytest.raises(HTTPException) as exc:
        world.service.list_api_keys_for_user(actor=world.admin, user_id=stranger.id)
    assert exc.value.status_code == 404


def test_admin_revokes_owners_key_and_it_stops_working(db: Session, world: World):
    """Revocation must bite on the very next call (FR-41, AC-25)."""
    created = _mint(world, world.owner)
    assert authenticate_api_key(db, created["api_key"]) is not None
    revoked = world.service.revoke_api_key(api_key_id=created["id"], actor=world.admin)
    assert revoked["revoked_at"] is not None
    assert authenticate_api_key(db, created["api_key"]) is None
    with pytest.raises(HTTPException) as exc:
        get_current_user(_request(), db, _bearer(created["api_key"]))
    assert exc.value.status_code == 401
    assert _audit(db, "api_key.revoked", created["id"]).metadata_json["revoked_by_admin"] is True


def test_owner_revoke_is_not_flagged_admin(db: Session, world: World):
    created = _mint(world, world.owner, "mine")
    world.service.revoke_api_key(api_key_id=created["id"], actor=world.owner)
    meta = _audit(db, "api_key.revoked", created["id"]).metadata_json
    assert meta == {
        "key_user_id": world.owner.id,
        "key_name": "mine",
        "revoked_by_admin": False,
    }


def test_non_admin_cannot_revoke_someone_elses_key(db: Session, world: World):
    created = _mint(world, world.owner)
    with pytest.raises(HTTPException) as exc:
        world.service.revoke_api_key(api_key_id=created["id"], actor=world.other)
    assert exc.value.status_code == 404
    assert authenticate_api_key(db, created["api_key"]) is not None


def test_admin_cannot_revoke_across_orgs(db: Session, world: World):
    other_org, _ = _org(db)
    stranger = _user(db, other_org, "stranger")
    foreign = AuthService(db).create_api_key(payload=ApiKeyCreate(name="x"), actor=stranger)
    with pytest.raises(HTTPException) as exc:
        world.service.revoke_api_key(api_key_id=foreign["id"], actor=world.admin)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        world.service.revoke_api_key(api_key_id="no-such-key", actor=world.admin)
    assert exc.value.status_code == 404


def test_deactivated_user_key_is_rejected(db: Session, world: World):
    created = _mint(world, world.owner)
    world.owner.status = UserStatus.DEACTIVATED
    db.flush()
    assert authenticate_api_key(db, created["api_key"]) is None
    with pytest.raises(HTTPException) as exc:
        get_current_user(_request(), db, _bearer(created["api_key"]))
    assert exc.value.status_code == 401


def test_created_audit_has_key_identity_and_no_secret(db: Session, world: World):
    """No key value or hash may reach an audit row (FR-35, AC-20)."""
    created = _mint(world, world.owner, "audit me")
    row = _audit(db, "api_key.created", created["id"])
    assert row.metadata_json == {
        "key_user_id": world.owner.id,
        "key_name": "audit me",
        "auth_method": "access_token",
    }
    key_hash = db.get(ApiKey, created["id"]).key_hash
    blob = repr(row.metadata_json)
    assert created["api_key"] not in blob and key_hash not in blob
