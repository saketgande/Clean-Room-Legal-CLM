"""An external agent's identity: a role holding only ``intake:create`` (feature 007, T008).

The agent identity is an ordinary user whose role grants ``intake:create`` and
nothing else, plus an org-wide ``intake`` screen grant at ADD (plan.md F1: filing
is additionally screen-gated, and a role created after migration 0043 has no such
row). These tests run over HTTP with real ``Bearer clm_`` keys and RBAC enforced
(conftest pins DISABLE_RBAC=false), so a permission or screen gate that quietly
stopped applying would show up here as a 2xx where a 403/404 is expected.

Rows are committed (the TestClient uses its own sessions) under a fresh
organization per test and removed afterwards. Audit rows are immutable by design
and stay behind.
"""

from __future__ import annotations

import uuid
import warnings
from dataclasses import dataclass, field

import pytest
from sqlalchemy import delete, select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.auth.schemas import ApiKeyCreate
from app.auth.service import AuthService
from app.core.config import settings
from app.core.database import Base, SessionLocal, new_uuid
from app.core.enums import UserStatus
from app.core.security import create_access_token
from app.menu_security.models import ActionLevel, RoleScreenAccess, Screen
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization

PREFIX = settings.api_v1_prefix
_TAG = "test-intake-agent-identity"


@dataclass
class Agent:
    user_id: str
    full_name: str
    key: str
    key_id: str
    headers: dict = field(default_factory=dict)

    def __post_init__(self):
        self.headers = {"Authorization": f"Bearer {self.key}"}


class World:
    """One committed organization with helpers to add users. ``cleanup`` removes
    everything org-scoped (children first, via the FK-sorted table order)."""

    def __init__(self) -> None:
        self.db = SessionLocal()
        self.org = Organization(id=new_uuid(), name="AgentIdentityTest",
                                slug=f"agent-{uuid.uuid4().hex[:8]}")
        self.db.add(self.org)
        self.db.flush()
        self.unit = OrgUnit(org_id=self.org.id, name="Global", parent_id=None)
        self.db.add(self.unit)
        self.db.commit()

    def _permission(self, value: str) -> Permission:
        perm = self.db.query(Permission).filter(Permission.value == value).one_or_none()
        if perm is None:
            perm = Permission(value=value)
            self.db.add(perm)
            self.db.flush()
        return perm

    def role(self, permissions: list[str], *, intake_screen: str | None = "ADD") -> Role:
        db = self.db
        role = Role(org_id=self.org.id, name=f"role-{uuid.uuid4().hex[:8]}",
                    allows_hierarchy_rollup=True)
        role.permissions = [self._permission(p) for p in permissions]
        db.add(role)
        db.flush()
        if intake_screen:
            screen = db.query(Screen).filter(Screen.code == "intake").one()
            level = db.query(ActionLevel).filter(ActionLevel.code == intake_screen).one()
            db.add(RoleScreenAccess(org_id=self.org.id, role_id=role.id, screen_id=screen.id,
                                    org_unit_id=None, max_action_level_id=level.id))
        db.commit()
        return role

    def user(self, role: Role, label: str = "agent") -> User:
        db = self.db
        user = User(org_id=self.org.id, email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
                    full_name=f"Agent {label} {uuid.uuid4().hex[:4]}", hashed_password="hash",
                    status=UserStatus.ACTIVE)
        db.add(user)
        db.flush()
        db.add(UserRoleGrant(user_id=user.id, role_id=role.id, org_id=self.org.id,
                             org_unit_id=self.unit.id))
        db.commit()
        return user

    def agent(self, label: str = "agent", *, permissions: list[str] | None = None,
              intake_screen: str | None = "ADD", role: Role | None = None) -> Agent:
        """A user plus a real ``clm_`` key minted through the service."""
        role = role or self.role(permissions if permissions is not None else ["intake:create"],
                                 intake_screen=intake_screen)
        user = self.user(role, label)
        minted = AuthService(self.db).create_api_key(payload=ApiKeyCreate(name=f"{label} key"),
                                                     actor=user)
        return Agent(user_id=user.id, full_name=user.full_name, key=minted["api_key"],
                     key_id=minted["id"])

    def jwt_headers(self, user_id: str) -> dict:
        token, _ = create_access_token(user_id)
        return {"Authorization": f"Bearer {token}"}

    def cleanup(self) -> None:
        db = self.db
        try:
            db.rollback()
            with warnings.catch_warnings():
                # the trademark <-> document_extract FK cycle; irrelevant to intake rows
                warnings.simplefilter("ignore")
                tables = list(reversed(Base.metadata.sorted_tables))
            for table in tables:
                if table.name == "audit_log" or "org_id" not in table.c:
                    continue
                db.execute(delete(table).where(table.c.org_id == self.org.id))
            db.execute(delete(Organization).where(Organization.id == self.org.id))
            db.commit()
        except Exception:
            db.rollback()  # leftovers are harmless: unique slug, no shared state
        finally:
            db.close()


@pytest.fixture
def world():
    w = World()
    try:
        yield w
    finally:
        w.cleanup()


def file_request(client, agent_or_headers, *, description: str = _TAG, key: str | None = None,
                 **over):
    headers = getattr(agent_or_headers, "headers", agent_or_headers)
    body = {"type_label": "Contract Question", "description": description,
            "idempotency_key": key or f"test-{uuid.uuid4().hex}"}
    body.update(over)
    return client.post(f"{PREFIX}/intake/requests", json=body, headers=headers)


# --- what the identity may do ------------------------------------------------

def test_a_create_only_identity_can_file_and_read_its_own_request(client, world):
    """FR-44: with only intake:create (+ the screen grant) the agent files, lists,
    reads, sees the SLA and the documents of its own request, and adds a document."""
    agent = world.agent()
    filed = file_request(client, agent)
    assert filed.status_code == 201, filed.text
    rid = filed.json()["id"]

    mine = client.get(f"{PREFIX}/intake/requests/mine", headers=agent.headers)
    assert mine.status_code == 200 and [r["id"] for r in mine.json()] == [rid]
    assert client.get(f"{PREFIX}/intake/forms", headers=agent.headers).status_code == 200
    assert client.get(f"{PREFIX}/intake/requests/{rid}", headers=agent.headers).status_code == 200
    sla = client.get(f"{PREFIX}/intake/requests/{rid}/sla", headers=agent.headers)
    assert sla.status_code == 200 and "legs" in sla.json()
    docs = client.get(f"{PREFIX}/intake/requests/{rid}/documents", headers=agent.headers)
    assert docs.status_code == 200 and docs.json() == []
    added = client.post(
        f"{PREFIX}/intake/requests/{rid}/documents", headers=agent.headers,
        json={"filename": "terms.txt", "mime_type": "text/plain",
              "content_b64": "TWFzdGVyIHNlcnZpY2VzIHRlcm1zLg=="})
    assert added.status_code == 201, added.text
    docs = client.get(f"{PREFIX}/intake/requests/{rid}/documents", headers=agent.headers)
    assert [d["filename"] for d in docs.json()] == ["terms.txt"]


def test_another_identitys_request_is_a_404_not_a_403(client, world):
    """AC-21: the own-request rule answers 404 so an agent cannot tell whether
    someone else's request exists."""
    owner, other = world.agent("owner"), world.agent("other")
    rid = file_request(client, owner).json()["id"]
    for path in ("", "/sla", "/documents"):
        res = client.get(f"{PREFIX}/intake/requests/{rid}{path}", headers=other.headers)
        assert res.status_code == 404, (path, res.status_code)
    res = client.post(f"{PREFIX}/intake/requests/{rid}/documents", headers=other.headers,
                      json={"filename": "x.txt", "mime_type": "text/plain",
                            "content_b64": "SGVsbG8="})
    assert res.status_code == 404
    docs = client.get(f"{PREFIX}/intake/requests/{rid}/documents", headers=owner.headers)
    assert docs.json() == []


def test_two_identities_each_list_only_their_own_requests(client, world):
    """AC-35: concurrent agents never see each other's filings."""
    a, b = world.agent("a"), world.agent("b")
    ra = file_request(client, a).json()["id"]
    rb1 = file_request(client, b).json()["id"]
    rb2 = file_request(client, b).json()["id"]
    assert [r["id"] for r in client.get(f"{PREFIX}/intake/requests/mine",
                                        headers=a.headers).json()] == [ra]
    assert sorted(r["id"] for r in client.get(f"{PREFIX}/intake/requests/mine",
                                              headers=b.headers).json()) == sorted([rb1, rb2])


# --- what the identity may not do --------------------------------------------

def test_staff_routes_and_other_modules_are_403(client, world):
    """AC-21/AC-23: the role has no intake:read/update and no contract:read, so
    the queue, pool, triage, claiming, promotion and contracts are closed."""
    agent = world.agent()
    rid = file_request(client, agent).json()["id"]
    h = agent.headers
    assert client.get(f"{PREFIX}/intake/requests", headers=h).status_code == 403
    assert client.get(f"{PREFIX}/intake/requests/pool", headers=h).status_code == 403
    assert client.get(f"{PREFIX}/intake/my-work", headers=h).status_code == 403
    assert client.post(f"{PREFIX}/intake/requests/{rid}/triage", headers=h,
                       json={"action": "start"}).status_code == 403
    assert client.post(f"{PREFIX}/intake/requests/{rid}/assign-to-me",
                       headers=h).status_code == 403
    assert client.post(f"{PREFIX}/intake/requests/{rid}/promote", headers=h,
                       json={}).status_code == 403
    assert client.get(f"{PREFIX}/contracts", headers=h).status_code == 403


def test_filing_without_the_intake_screen_grant_is_403(client, world):
    """Why the documented procedure needs the screen grant (plan F1): the
    permission alone is not enough to file, and nothing is stored."""
    agent = world.agent(intake_screen=None)
    res = file_request(client, agent)
    assert res.status_code == 403
    assert "intake" in res.json()["detail"]
    assert client.get(f"{PREFIX}/intake/requests/mine", headers=agent.headers).json() == []


def test_a_role_without_intake_create_cannot_file(client, world):
    """AC-22: no permission, no row (even with the screen grant)."""
    agent = world.agent(permissions=["contract:read"])
    res = file_request(client, agent)
    assert res.status_code == 403
    assert "intake:create" in res.json()["detail"]
    for path in ("/intake/requests/mine", "/intake/forms"):
        assert client.get(f"{PREFIX}{path}", headers=agent.headers).status_code == 403
    with SessionLocal() as db:
        from app.intake.models import IntakeRequest
        assert db.scalar(select(IntakeRequest.id).where(
            IntakeRequest.org_id == world.org.id)) is None


def test_a_bad_key_is_401(client):
    res = client.get(f"{PREFIX}/intake/requests/mine",
                     headers={"Authorization": "Bearer clm_not-a-real-key-at-all"})
    assert res.status_code == 401
