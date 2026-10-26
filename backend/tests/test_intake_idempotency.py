"""Filing with an idempotency key must be safe to retry (feature 007, T006).

External agents retry on timeouts whose outcome they cannot see. Without a key a
retry past the 2-minute duplicate window files a second legal request; with one,
the platform answers "already filed" and changes nothing. These tests run at the
service level in one outer transaction that is rolled back, so no rows persist.
The HTTP status mapping (201 vs 200) is covered with the routes.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import ApiKey, User
from app.core.auth_context import AuthContext
from app.core.database import engine, new_uuid
from app.core.enums import UserStatus
from app.core.models import AuditLog, ResourceTimelineEvent
from app.intake import service
from app.intake.models import IntakeDocument, IntakeRequest
from app.intake.schemas import RequestCreate
from app.organizations.models import Organization
from tests.test_intake_agent_identity import PREFIX as _PREFIX
from tests.test_intake_agent_identity import World
from tests.test_intake_agent_identity import file_request as _http_file

_TAG = "test-intake-idempotency"
_CONFLICT = (
    "This idempotency_key was already used for a different request — "
    "use a new key to file a new request"
)


@pytest.fixture
def db():
    """One outer transaction, rolled back at the end. create_savepoint makes the
    service's own commit()/rollback() act on a savepoint, so the rollback in the
    race path cannot undo this fixture's users."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _org(db) -> Organization:
    org = Organization(id=new_uuid(), name="IdemTest", slug=f"idem-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db, org, label="filer") -> User:
    user = User(org_id=org.id, email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
                full_name=f"Agent {label}", hashed_password="hash", status=UserStatus.ACTIVE)
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def world(db):
    org = _org(db)
    user = _user(db, org)
    db.commit()  # fixtures survive the savepoint rollbacks the service may do
    return org, user


def _key() -> str:
    return f"test-{uuid.uuid4().hex}"


def _payload(key: str | None, **over) -> RequestCreate:
    body = {"type_label": "Contract Question", "description": _TAG, "idempotency_key": key}
    body.update(over)
    return RequestCreate(**body)


def _file(db, user, key, auth=None, **over):
    return service.create_request(db, actor=user, payload=_payload(key, **over),
                                  defer_triage=True, auth=auth)


def _count(db, org, key) -> int:
    return db.scalar(select(func.count()).select_from(IntakeRequest).where(
        IntakeRequest.org_id == org.id, IntakeRequest.idempotency_key == key))


def _audits(db, resource_id, action) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(
        AuditLog.resource_id == resource_id, AuditLog.action == action)))


def _txt(text="MASTER SERVICES AGREEMENT between Acme and Globex.") -> dict:
    return {"filename": "msa.txt", "mime_type": "text/plain",
            "content_b64": base64.b64encode(text.encode()).decode()}


# --- repeat -----------------------------------------------------------------

def test_same_key_and_body_returns_the_original_and_audits_the_repeat(db, world):
    """The defect: a retried filing created a second request. A repeat must return
    the first, say so, and leave exactly one creation in the audit trail."""
    org, user = world
    key = _key()
    first = _file(db, user, key)
    second = _file(db, user, key)

    assert first["idempotent_repeat"] is False
    assert second["idempotent_repeat"] is True
    assert second["id"] == first["id"] and second["ref"] == first["ref"]
    assert _count(db, org, key) == 1
    assert len(_audits(db, first["id"], "intake.created")) == 1
    repeats = _audits(db, first["id"], "intake.create_repeated")
    assert len(repeats) == 1
    assert repeats[0].metadata_json["idempotency_key"] == key
    assert repeats[0].metadata_json["auth_method"] == "internal"


def test_a_repeat_writes_no_second_creation_timeline_event_or_documents(db, world):
    """A repeat is read-only apart from its audit row: nothing is filed or attached twice."""
    _, user = world
    key = _key()
    first = _file(db, user, key, attachments=[_txt()])
    _file(db, user, key, attachments=[_txt()])

    events = db.scalar(select(func.count()).select_from(ResourceTimelineEvent).where(
        ResourceTimelineEvent.resource_id == first["id"],
        ResourceTimelineEvent.event_type == "intake.created"))
    docs = db.scalar(select(func.count()).select_from(IntakeDocument).where(
        IntakeDocument.request_id == first["id"]))
    assert events == 1 and docs == 1


def test_routing_hints_are_not_part_of_the_fingerprint(db, world):
    """Priority/department/requester_name/source are hints; a client that derives
    priority from today's date must still match its own retry after midnight."""
    _, user = world
    key = _key()
    first = _file(db, user, key, priority="Medium", department="Procurement")
    again = _file(db, user, key, priority="High", department="Finance",
                  requester_name="Someone else", source="copilot")
    assert again["idempotent_repeat"] is True and again["id"] == first["id"]
    assert again["priority"] == "Medium"  # the original is returned untouched


# --- conflicts --------------------------------------------------------------

def test_same_key_with_a_different_description_is_a_409_and_changes_nothing(db, world):
    org, user = world
    key = _key()
    first = _file(db, user, key)
    with pytest.raises(HTTPException) as exc:
        _file(db, user, key, description=_TAG + " but different")
    assert exc.value.status_code == 409 and exc.value.detail == _CONFLICT

    row = db.get(IntakeRequest, first["id"])
    assert row.description == _TAG
    assert _count(db, org, key) == 1
    assert _audits(db, first["id"], "intake.create_repeated") == []


def test_same_key_with_a_different_attachment_is_a_409(db, world):
    """The 2-minute check ignores attachments; a key-identified filing must not."""
    _, user = world
    key = _key()
    _file(db, user, key, attachments=[_txt("first body")])
    with pytest.raises(HTTPException) as exc:
        _file(db, user, key, attachments=[_txt("second body")])
    assert exc.value.status_code == 409


def test_another_users_key_gets_the_same_409_and_never_their_ref(db, world):
    """Existence leak: a caller probing keys must not learn another user's request."""
    org, user = world
    other = _user(db, org, "other")
    db.commit()
    key = _key()
    first = _file(db, user, key)
    with pytest.raises(HTTPException) as exc:
        _file(db, other, key)  # identical content, different requester
    assert exc.value.status_code == 409 and exc.value.detail == _CONFLICT
    assert first["ref"] not in exc.value.detail and first["id"] not in exc.value.detail
    assert _count(db, org, key) == 1


def test_the_same_key_in_another_organization_is_a_separate_request(db, world):
    """The key is scoped by org: a stranger's key cannot fold into, or block, ours."""
    org, user = world
    org2 = _org(db)
    user2 = _user(db, org2, "second")
    db.commit()
    key = _key()
    a = _file(db, user, key)
    b = _file(db, user2, key)
    assert a["id"] != b["id"]
    assert b["idempotent_repeat"] is False
    assert _count(db, org, key) == 1 and _count(db, org2, key) == 1


# --- race -------------------------------------------------------------------

def test_a_concurrent_filing_with_the_same_key_is_folded_in_not_raised(db, world, monkeypatch):
    """Two identical calls both miss the lookup, then both insert. The partial
    unique index rejects the loser, which must answer 'already filed' rather than
    surface an IntegrityError as a 500 (pattern: test_intake_ingest_dedup.py).
    The rival here is an earlier committed filing on the same connection: a second
    connection could not see this test's uncommitted org and users."""
    org, user = world
    key = _key()
    original = service.IntakeService._find_by_idempotency_key
    rival = {}

    def miss_once(self, **kwargs):
        monkeypatch.undo()
        # The rival commits after our check "passed"; falling off the end is the miss (None).
        rival["out"] = _file(db, user, key)

    monkeypatch.setattr(service.IntakeService, "_find_by_idempotency_key", miss_once)
    out = _file(db, user, key)

    assert original is service.IntakeService._find_by_idempotency_key
    assert out["idempotent_repeat"] is True
    assert out["id"] == rival["out"]["id"]
    assert _count(db, org, key) == 1
    assert len(_audits(db, out["id"], "intake.created")) == 1


# --- duplicate check --------------------------------------------------------

def test_a_key_skips_the_two_minute_duplicate_check(db, world):
    """If the duplicate check ran first, a key would never be stored on a match and a
    retry after the window would file twice. Two keys, identical content: two requests."""
    _, user = world
    a = _file(db, user, _key())
    b = _file(db, user, _key())
    assert a["id"] != b["id"]


def test_without_a_key_the_duplicate_check_still_applies(db, world):
    _, user = world
    a = _file(db, user, None)
    b = _file(db, user, None)
    assert a["id"] == b["id"]
    assert a["idempotent_repeat"] is False and b["idempotent_repeat"] is False
    assert db.get(IntakeRequest, a["id"]).idempotency_key is None


# --- refusals ---------------------------------------------------------------

def test_a_refused_attachment_with_a_key_leaves_no_row_and_no_audit(db, world):
    org, user = world
    key = _key()
    before = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.actor_user_id == user.id))
    xlsx = base64.b64encode(b"PK\x03\x04 not a spreadsheet").decode()
    with pytest.raises(HTTPException) as exc:
        _file(db, user, key, attachments=[{
            "filename": "budget.xlsx", "content_b64": xlsx,
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}])
    assert exc.value.status_code == 415
    assert _count(db, org, key) == 0
    after = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.actor_user_id == user.id))
    assert after == before


def test_a_malformed_key_is_refused_by_the_schema():
    for bad in ("short", "has space in it", "x" * 129, "emoji-✓-key-123"):
        with pytest.raises(ValidationError):
            RequestCreate(type_label="x", idempotency_key=bad)
    assert RequestCreate(type_label="x", idempotency_key="mcp-3f2a.9c0e:4b5d_8f9a").idempotency_key


# --- API-key callers --------------------------------------------------------

def _api_key(db, org, user) -> ApiKey:
    row = ApiKey(org_id=org.id, user_id=user.id, name="idem-test", key_hash="not-a-real-hash")
    db.add(row)
    db.flush()
    db.commit()
    return row


def test_an_api_key_filing_forces_source_ignores_requester_name_and_flags_the_agent(db, world):
    org, user = world
    key_row = _api_key(db, org, user)
    auth = AuthContext(method="api_key", api_key_id=key_row.id)
    key = _key()
    out = _file(db, user, key, auth=auth, source="form", requester_name="The CEO")

    assert out["source"] == "api"
    assert out["requester_name"] == user.full_name  # falls back to the registered name
    assert out["filed_by_agent"] is True and out["filed_by_agent_name"] == user.full_name
    row = db.get(IntakeRequest, out["id"])
    assert row.filed_via_api_key_id == key_row.id and row.requester_name is None

    meta = _audits(db, out["id"], "intake.created")[0].metadata_json
    assert meta["auth_method"] == "api_key" and meta["api_key_id"] == key_row.id
    assert meta["source"] == "api" and meta["idempotency_key"] == key
    assert meta["filed_by_agent"] is True and meta["attachment_count"] == 0

    title = db.scalar(select(ResourceTimelineEvent.title).where(
        ResourceTimelineEvent.resource_id == out["id"],
        ResourceTimelineEvent.event_type == "intake.created"))
    assert title == f"Request filed by agent {user.full_name} — Contract Question"

    repeat = _file(db, user, key, auth=auth)
    meta = _audits(db, out["id"], "intake.create_repeated")[0].metadata_json
    assert repeat["idempotent_repeat"] is True
    assert meta["auth_method"] == "api_key" and meta["api_key_id"] == key_row.id


def test_a_session_filing_keeps_what_the_caller_sent_and_is_not_flagged(db, world):
    _, user = world
    out = _file(db, user, _key(), auth=AuthContext(method="access_token"),
                source="copilot", requester_name="Pat")
    assert out["source"] == "copilot" and out["requester_name"] == "Pat"
    assert out["filed_by_agent"] is False and out["filed_by_agent_name"] is None
    meta = _audits(db, out["id"], "intake.created")[0].metadata_json
    assert meta["auth_method"] == "access_token" and meta["api_key_id"] is None
    assert meta["filed_by_agent"] is False


def test_internal_callers_get_the_internal_audit_marker_and_no_key(db, world):
    """Ingest, chat tools and jobs call without an HTTP auth context."""
    _, user = world
    out = _file(db, user, None)
    meta = _audits(db, out["id"], "intake.created")[0].metadata_json
    assert meta["auth_method"] == "internal" and meta["api_key_id"] is None
    assert meta["idempotency_key"] is None


def test_attachments_and_add_document_audit_the_auth_method(db, world):
    org, user = world
    key_row = _api_key(db, org, user)
    auth = AuthContext(method="api_key", api_key_id=key_row.id)
    out = _file(db, user, _key(), auth=auth, attachments=[_txt()])
    created_with = _audits(db, out["id"], "intake.document.added")
    assert len(created_with) == 1
    assert created_with[0].metadata_json == {"auth_method": "api_key", "api_key_id": key_row.id}

    service.add_document(db, actor=user, request_id=out["id"], filename="more.txt",
                         mime_type="text/plain", content=b"Additional terms apply.", auth=auth)
    docs = _audits(db, out["id"], "intake.document.added")
    assert len(docs) == 2
    assert all(d.metadata_json["api_key_id"] == key_row.id for d in docs)

    service.add_document(db, actor=user, request_id=out["id"], filename="x.txt",
                         mime_type="text/plain", content=b"Internal call.")
    methods = [d.metadata_json["auth_method"]
               for d in _audits(db, out["id"], "intake.document.added")]
    assert sorted(methods) == ["api_key", "api_key", "internal"]


# --- HTTP status mapping (T008) ---------------------------------------------
# These drive the real route with a Bearer key and committed rows.


@pytest.fixture
def http_world():
    w = World()
    try:
        yield w
    finally:
        w.cleanup()


def test_http_first_filing_is_201_a_repeat_is_200_and_a_conflict_is_409(client, http_world):
    """AC-5/AC-6/AC-36 at the wire: 201 new, 200 + idempotent_repeat for the same
    key and body, 409 with the frozen message for the same key and other content;
    one row throughout."""
    agent = http_world.agent()
    key = _key()
    first = _http_file(client, agent, key=key)
    assert first.status_code == 201 and first.json()["idempotent_repeat"] is False

    again = _http_file(client, agent, key=key)
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["idempotent_repeat"] is True

    other = _http_file(client, agent, key=key, description=_TAG + " but different")
    assert other.status_code == 409
    assert other.json()["detail"] == _CONFLICT

    mine = client.get(f"{_PREFIX}/intake/requests/mine", headers=agent.headers).json()
    assert [r["id"] for r in mine] == [first.json()["id"]]


def test_http_another_identity_reusing_a_key_gets_the_same_409(client, http_world):
    """No existence oracle: the other user's ref never appears in the refusal."""
    a, b = http_world.agent("a"), http_world.agent("b")
    key = _key()
    first = _http_file(client, a, key=key)
    res = _http_file(client, b, key=key)
    assert res.status_code == 409 and res.json()["detail"] == _CONFLICT
    assert first.json()["ref"] not in res.text


def test_http_a_malformed_key_is_422(client, http_world):
    agent = http_world.agent()
    res = _http_file(client, agent, key="short")
    assert res.status_code == 422


# --- extraction cost (T015 / m1) ---------------------------------------------

@pytest.fixture
def extractions(monkeypatch):
    """Count text extractions (OCR may be paid for on each)."""
    import app.contract_files.text_extraction as te

    real = te.extract_text
    calls: list[str] = []

    def counting(*args, **kwargs):
        calls.append(kwargs.get("filename", ""))
        return real(*args, **kwargs)

    monkeypatch.setattr(te, "extract_text", counting)
    return calls


def test_a_repeat_does_not_extract_the_attachment_again(db, world, extractions):
    """A retry must not pay for OCR a second time, nor risk a changed OCR outcome failing it."""
    _, user = world
    key = _key()
    first = _file(db, user, key, attachments=[_txt()])
    assert len(extractions) == 1
    again = _file(db, user, key, attachments=[_txt()])
    assert again["idempotent_repeat"] is True and again["id"] == first["id"]
    assert len(extractions) == 1


def test_a_conflict_does_not_extract(db, world, extractions):
    _, user = world
    key = _key()
    _file(db, user, key, attachments=[_txt()])
    with pytest.raises(HTTPException) as exc:
        _file(db, user, key, description=_TAG + " changed", attachments=[_txt()])
    assert exc.value.status_code == 409
    assert len(extractions) == 1


def test_a_refused_attachment_with_a_key_still_files_nothing_and_never_extracts(
    db, world, extractions
):
    org, user = world
    key = _key()
    bad = base64.b64encode(b"PK\x03\x04 not a spreadsheet").decode()
    with pytest.raises(HTTPException) as exc:
        _file(db, user, key, attachments=[{
            "filename": "b.xlsx", "content_b64": bad,
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}])
    assert exc.value.status_code == 415
    assert _count(db, org, key) == 0 and extractions == []


def test_the_losing_side_of_a_race_does_not_extract(db, world, monkeypatch, extractions):
    """Only the rival's filing reads the text: the loser fails at the insert, before extraction."""
    _, user = world
    key = _key()
    original = service.IntakeService._find_by_idempotency_key
    state = {"first": True}

    def miss_once(self, **kwargs):
        if state["first"]:
            state["first"] = False
            _file(db, user, key, attachments=[_txt()])  # the rival; extracts once
            return None
        return original(self, **kwargs)

    monkeypatch.setattr(service.IntakeService, "_find_by_idempotency_key", miss_once)
    out = _file(db, user, key, attachments=[_txt()])
    assert out["idempotent_repeat"] is True
    assert len(extractions) == 1
