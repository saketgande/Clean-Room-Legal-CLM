"""Proves the contracts/ dependency-injection conversion (see
backend/DI_MIGRATION.md): a route's service dependency can be swapped for a
fake via FastAPI's dependency_overrides, without monkeypatching module
internals — the pattern the rest of the codebase's DI conversion follows.

The route's ``get_contract_service`` dependency is faked (so no real
``ContractService``/ethical-wall/clearance logic ever runs), but
``Depends(require_permission("contract:read"))`` at the route layer is real —
it resolves permissions via ``app.core.org_access.effective_permission_values``,
which is deliberately DB-backed (it ignores any static ``user.permission_values``
by design, see that function's docstring) and independent of the faked
service. So the test seeds one real Organization/Role/User/UserRoleGrant row
set and shares its DB session with the app via a ``get_db`` override, the
same real-Postgres-transaction-per-test convention every other RBAC-adjacent
test in this suite (e.g. ``test_role_grants_api.py``) already uses — a bare
duck-typed fake user can't satisfy a real permission check.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

import pytest
from sqlalchemy.orm import Session

from app.auth.models import Permission, Role, User, UserRoleGrant
from app.contracts.dependencies import get_contract_service
from app.core.database import engine
from app.core.deps import get_current_user, get_db
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


def _get_or_create_permission(db: Session, value: str) -> Permission:
    existing = db.query(Permission).filter(Permission.value == value).one_or_none()
    if existing is not None:
        return existing
    perm = Permission(value=value)
    db.add(perm)
    db.flush()
    return perm


def _shared_db_override(db: Session):
    """A get_db override that hands the route the SAME session used to seed
    test data — a plain `lambda: db` wouldn't work here since get_db is a
    yield-based dependency; FastAPI needs an actual generator function to
    resolve it the same way."""
    def _get_db():
        yield db
    return _get_db


def _seed_authorized_user(db: Session, *, user_id: str, org_id: str, permission: str) -> User:
    """A real user, in a real org, holding a real grant of `permission` —
    enough for `require_permission` to actually pass."""
    org = Organization(id=org_id, name="DI Test Org", slug=f"di-test-{uuid.uuid4().hex[:8]}")
    db.add(org)
    root = OrgUnit(org_id=org_id, name="Global", parent_id=None)
    db.add(root)
    db.flush()

    role = Role(org_id=org_id, name=f"di-test-role-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    role.permissions = [_get_or_create_permission(db, permission)]
    db.add(role)
    db.flush()

    user = User(
        id=user_id, org_id=org_id, email=f"{user_id}@example.com",
        full_name="DI Test User", hashed_password="hash", status="active",
    )
    db.add(user)
    db.flush()

    db.add(UserRoleGrant(
        org_id=org_id, user_id=user.id, role_id=role.id, org_unit_id=root.id,
        created_by_user_id=user.id,
    ))
    db.flush()
    return user


class _FakeContract:
    """Enough attributes to satisfy ContractResponse's from_attributes model —
    a real ContractService would return an ORM row with all of these."""

    id = "contract-1"
    org_id = "org-1"
    title = "Fake NDA"
    contract_type = None
    lifecycle_stage = "intake"
    renewal_due = False
    archived = False
    owner_user_id = "user-1"
    counterparty_name = None
    jurisdiction = None
    confidentiality = "internal"
    risk_level = None
    risk_score = None
    risk_band = None
    risk_summary = None
    value_amount = None
    currency = None
    effective_date = None
    expiration_date = None
    current_contract_file_id = None
    current_authoritative_version_id = None
    metadata_json: ClassVar[dict] = {}


class _FakeContractService:
    """Stands in for ContractService — records the call, returns canned data."""

    def __init__(self):
        self.calls: list[dict] = []

    def get_contract_for_user(self, *, contract_id: str, user):
        self.calls.append({"contract_id": contract_id, "user_id": getattr(user, "id", None)})
        return _FakeContract()


def test_get_contract_route_uses_injected_service(client, db):
    """GET /contracts/{id} should call whatever ContractService the DI
    container hands it — proven by overriding get_contract_service with a
    fake and asserting the fake, not a real DB-backed instance, was used."""
    fake_service = _FakeContractService()
    user = _seed_authorized_user(db, user_id="user-1", org_id="org-1", permission="contract:read")

    client.app.dependency_overrides[get_db] = _shared_db_override(db)
    client.app.dependency_overrides[get_contract_service] = lambda: fake_service
    client.app.dependency_overrides[get_current_user] = lambda: user

    response = client.get("/api/v1/contracts/contract-1")

    assert response.status_code == 200
    assert fake_service.calls == [{"contract_id": "contract-1", "user_id": "user-1"}]


def test_get_contract_risk_route_reads_from_injected_contract(client, db):
    """/contracts/{id}/risk falls back to the contract's own risk fields when
    no summary is stored yet — still true after the DI conversion."""
    fake_service = _FakeContractService()
    user = _seed_authorized_user(db, user_id="user-1", org_id="org-1", permission="contract:read")

    client.app.dependency_overrides[get_db] = _shared_db_override(db)
    client.app.dependency_overrides[get_contract_service] = lambda: fake_service
    client.app.dependency_overrides[get_current_user] = lambda: user

    response = client.get("/api/v1/contracts/contract-1/risk")

    assert response.status_code == 200
    body = response.json()
    assert body["note"] == "Not computed yet."
    assert fake_service.calls == [{"contract_id": "contract-1", "user_id": "user-1"}]
