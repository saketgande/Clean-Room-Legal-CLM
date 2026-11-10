"""Contract types (012 T003): the fixed list, reading a stored type against it, and the one way a
person sets it (PUT /contracts/{id}/type).

Failures this guards: an NDA stored as "Mutual Non-Disclosure Agreement (MNDA)" read as unknown (or a
"Services Agreement" read as an NDA because of its title); a closed or archived contract nagging for a
type; a PUT that moves the stage, writes twice or lets the AI overwrite the person; a PATCH that sets the
type around the list check; a walled or foreign contract showing up in the to-do list. Everything runs in
one rolled-back transaction in an organisation of its own.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.contracts.contract_types as ct
import app.contracts.service as contract_service
import app.models  # noqa: F401  (register every mapper)
from app.ai.controller import ai_controller
from app.ai.schemas import CitationInput, ContractMetadataOutput
from app.auth.models import User
from app.contracts.models import Contract
from app.core import org_access, screen_access
from app.core.database import engine, utcnow
from app.core.deps import get_current_user, get_db
from app.core.models import AuditLog, ResourceTimelineEvent
from app.organizations.models import Organization
from app.walls.models import EthicalWall, EthicalWallPrincipal

READER = {"contract:read"}
EDITOR = {"contract:read", "contract:update"}


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


class World:
    def __init__(self, db: Session, monkeypatch):
        self.db = db
        org = Organization(name="Type Co", slug=f"ty-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org_id = org.id
        self.perms: dict[str, set[str]] = {}
        monkeypatch.setattr(User, "permission_values", property(lambda u: self.perms.get(u.id, set())))
        monkeypatch.setattr(
            org_access, "effective_permission_values",
            lambda db, *, user, at=None: self.perms.get(user.id, set()),
        )
        # The screen grant is its own layer (tested elsewhere); here only the permission string gates.
        monkeypatch.setattr(
            screen_access, "resolve_screen_access",
            lambda db, *, user, screen_code, org_unit_id=None, at=None: screen_access.ResolvedScreenAccess(
                screen_code, None, None, "DELETE", 4, (), "test"),
        )
        self.editor = self.user(EDITOR)

    def user(self, perms: set[str], org_id: str | None = None) -> User:
        u = User(org_id=org_id or self.org_id, email=f"{uuid.uuid4().hex[:8]}@example.com",
                 full_name="Tess Tester", hashed_password="hash", status="active")
        self.db.add(u)
        self.db.flush()
        self.perms[u.id] = set(perms)
        return u

    def contract(self, contract_type: str | None = None, *, title: str | None = None,
                 stage: str = "active", org_id: str | None = None, **kw) -> Contract:
        c = Contract(org_id=org_id or self.org_id, title=title or f"Deal {uuid.uuid4().hex[:5]}",
                     counterparty_name="Umbrella Corp", lifecycle_stage=stage,
                     contract_type=contract_type, owner_user_id=self.editor.id,
                     created_at=utcnow(), **kw)
        self.db.add(c)
        self.db.flush()
        return c

    def wall(self, contract: Contract, user: User) -> None:
        w = EthicalWall(org_id=self.org_id, name="w", scope_type="contract", scope_id=contract.id)
        self.db.add(w)
        self.db.flush()
        self.db.add(EthicalWallPrincipal(org_id=self.org_id, wall_id=w.id,
                                         principal_type="user", principal_id=user.id))
        self.db.flush()
        self.db.expire_all()

    def rows(self, contract: Contract) -> tuple[list[AuditLog], list[ResourceTimelineEvent]]:
        audit = list(self.db.scalars(select(AuditLog).where(
            AuditLog.action == "contract.type_changed", AuditLog.resource_id == contract.id)))
        timeline = list(self.db.scalars(select(ResourceTimelineEvent).where(
            ResourceTimelineEvent.event_type == "contract.type_changed",
            ResourceTimelineEvent.resource_id == contract.id)))
        return audit, timeline


@pytest.fixture
def w(db, monkeypatch):
    return World(db, monkeypatch)


@pytest.fixture
def api(w, db, client):
    from app.main import app

    state = {"user": w.editor}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    def call(method: str, path: str, *, user: User | None = None, **kw):
        state["user"] = user or w.editor
        return client.request(method, f"/api/v1/contracts{path}", **kw)

    return call


# --- reading a stored type ----------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [
    ("NDA", "NDA"),
    ("Mutual Non-Disclosure Agreement", "NDA"),
    ("Mutual Non-Disclosure Agreement (MNDA)", "NDA"),
    ("non_disclosure_agreement", "NDA"),
    ("Master Services Agreement", "MSA"),
    ("MSA", "MSA"),
    ("Services Agreement", None),
    ("DPA", "DPA"),
    ("Statement of Work", "Statement of Work"),
    ("SaaS subscription", "Software / SaaS Agreement"),
    ("Other", "other"),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_canonical_contract_type_reads_what_is_stored(raw, expected):
    """A spelling of NDA the AI or an upload wrote must be read as NDA, and a vague one must not be
    guessed at: the NDA check lists by this answer."""
    assert ct.canonical_contract_type(raw) == expected


@pytest.mark.parametrize(("ctype", "title", "meta", "stage", "archived", "needed"), [
    (None, "Deal", {}, "active", False, True),
    ("", "Deal", {}, "active", False, True),
    ("Services Agreement", "Deal", {}, "active", False, True),
    ("other", "Mutual NDA - Umbrella", {}, "active", False, True),
    ("other", "Mutual NDA - Umbrella", {"field_sources": {"contract_type": "user"}}, "active", False, False),
    ("other", "Deal", {}, "active", False, False),
    ("NDA", "Mutual NDA - Umbrella", {}, "active", False, False),
    (None, "Deal", {}, "closed", False, False),
    (None, "Deal", {}, "active", True, False),
    ("other", "Mutual NDA - Umbrella", {}, "closed", False, False),
], ids=["blank", "empty", "unrecognised", "other-nda-title", "other-chosen", "other-plain", "known",
        "closed", "archived", "closed-other"])
def test_type_needed(ctype, title, meta, stage, archived, needed):
    """A person who chose Other is never asked again, a closed or archived contract is never asked,
    and an unrecognised or blank type always is."""
    assert ct.type_needed(ctype, title, meta, stage, archived) is needed


def test_suggested_type_is_the_document_reading_only_while_needed():
    """The suggestion comes from the document reading, never the title, and only while a type is needed."""
    meta = {"ai_suggestions": {"metadata": {"contract_type": "Mutual Non-Disclosure Agreement"}}}
    assert ct.suggested_type(None, "Whatever", meta, "active", False) == "NDA"
    assert ct.suggested_type(None, "Mutual NDA", {}, "active", False) is None
    assert ct.suggested_type("MSA", "Deal", meta, "active", False) is None
    assert ct.suggested_type(None, "Deal", meta, "closed", False) is None


def test_contract_status():
    """Active and in date is in force; earlier stages are in progress; closed, archived or ended is neither."""
    today = utcnow().date()

    def c(**kw):
        base = {"archived": False, "lifecycle_stage": "active", "expiration_date": None}
        return SimpleNamespace(**{**base, **kw})
    assert ct.contract_status(c(), today) == "in_force"
    assert ct.contract_status(c(expiration_date=today), today) == "in_force"
    assert ct.contract_status(c(expiration_date=today - timedelta(days=1)), today) is None
    assert ct.contract_status(c(lifecycle_stage="review"), today) == "in_progress"
    assert ct.contract_status(c(lifecycle_stage="review", expiration_date=today - timedelta(days=1)), today) is None
    assert ct.contract_status(c(lifecycle_stage="closed"), today) is None
    assert ct.contract_status(c(archived=True), today) is None


# --- the endpoints ------------------------------------------------------------------------------


def test_types_endpoint_returns_the_frozen_list_in_order(w, api):
    """The picker and the drafting code must share spelling and order."""
    res = api("GET", "/types")
    assert res.status_code == 200
    assert [o["value"] for o in res.json()] == [
        "NDA", "MSA", "Statement of Work", "Software / SaaS Agreement", "Vendor Agreement",
        "Consultancy Agreement", "DPA", "Customer Agreement", "other"]
    assert res.json()[1] == {"value": "MSA", "label": "MSA (master services)"}


def test_types_and_type_needed_need_contract_read(w, api):
    """Both reads are permission-gated."""
    nobody = w.user(set())
    assert api("GET", "/types", user=nobody).status_code == 403
    assert api("GET", "/type-needed", user=nobody).status_code == 403


def test_closed_or_archived_blank_type_is_not_listed(w, api):
    """AC-29 / DEC-2: a closed or archived contract with no type is neither listed nor prompted on its
    page, while an open one beside them is."""
    open_ = w.contract(None, title="Open one")
    closed = w.contract(None, title="Closed one", stage="closed")
    archived = w.contract("", title="Archived one", archived=True)

    ids = [i["id"] for i in api("GET", "/type-needed").json()["items"]]

    assert ids == [open_.id]
    assert api("GET", f"/{open_.id}").json()["type_needed"] is True
    for c in (closed, archived):
        assert api("GET", f"/{c.id}").json()["type_needed"] is False


def test_type_needed_lists_blank_and_other_titled_nda_but_not_walled_or_foreign(w, api, db):
    """AC-29: only contracts the viewer may open, in their own organisation, and only the ones in doubt."""
    blank = w.contract(None, title="No type")
    other = w.contract("other", title="Mutual NDA - Umbrella",
                       metadata_json={"ai_suggestions": {"metadata": {"contract_type": "NDA"}}})
    w.contract("NDA", title="Known")
    w.contract("other", title="Plain deal")
    walled = w.contract(None, title="Walled")
    w.wall(walled, w.user(READER))  # the wall names someone else; the editor is not behind it
    hidden = w.contract(None, title="Hidden from the reader")
    w.wall(hidden, w.editor)
    foreign_org = Organization(name="Other Co", slug=f"ot-{uuid.uuid4().hex[:8]}")
    db.add(foreign_org)
    db.flush()
    w.contract(None, title="Foreign", org_id=foreign_org.id)

    body = api("GET", "/type-needed").json()

    got = {i["id"]: i for i in body["items"]}
    assert set(got) == {blank.id, other.id, walled.id} | ({hidden.id} & set(got))
    assert hidden.id not in got and body["truncated"] is False
    assert got[other.id]["suggested_type"] == "NDA" and got[blank.id]["suggested_type"] is None
    assert got[other.id]["stage"] == "active" and got[other.id]["counterparty_name"] == "Umbrella Corp"


def test_type_needed_is_truncated_past_the_cap(w, api, monkeypatch):
    """A long to-do list says so rather than silently dropping rows."""
    monkeypatch.setattr(contract_service, "TYPE_NEEDED_CAP", 2)
    for _ in range(3):
        w.contract(None)
    body = api("GET", "/type-needed").json()
    assert len(body["items"]) == 2 and body["truncated"] is True


def test_put_sets_the_type_with_one_audit_and_one_timeline_row_and_touches_nothing_else(w, api, db):
    """AC-30: choosing a type changes the type and who chose it, nothing else, and is recorded once."""
    c = w.contract(None, stage="review", value_amount=1200, currency="INR")
    before = {k: getattr(c, k) for k in (
        "lifecycle_stage", "expiration_date", "effective_date", "value_amount", "currency",
        "owner_user_id", "archived", "review_playbook_id", "counterparty_name", "title")}

    res = api("PUT", f"/{c.id}/type", json={"contract_type": "NDA"})

    assert res.status_code == 200
    assert res.json()["contract_type"] == "NDA" and res.json()["type_needed"] is False
    db.refresh(c)
    assert {k: getattr(c, k) for k in before} == before
    assert c.metadata_json["field_sources"]["contract_type"] == "user"
    audit, timeline = w.rows(c)
    assert len(audit) == 1 and len(timeline) == 1
    assert audit[0].before == {"contract_type": None} and audit[0].after == {"contract_type": "NDA"}
    assert audit[0].actor_user_id == w.editor.id
    assert timeline[0].title == "Contract type set to NDA (was not set)"
    assert timeline[0].details == {"from": None, "to": "NDA"}


def test_put_titles_for_a_change_and_for_a_confirmation(w, api):
    """The timeline sentence tells a change from a first confirmation of the same value."""
    c = w.contract("Services Agreement")
    api("PUT", f"/{c.id}/type", json={"contract_type": "MSA"})
    d = w.contract("NDA")
    api("PUT", f"/{d.id}/type", json={"contract_type": "NDA"})
    assert w.rows(c)[1][0].title == "Contract type changed from Services Agreement to MSA (master services)"
    assert w.rows(d)[1][0].title == "Contract type confirmed as NDA"


def test_put_repeating_the_same_value_writes_nothing_new(w, api):
    """A double click must not add a second audit or timeline row."""
    c = w.contract(None)
    assert api("PUT", f"/{c.id}/type", json={"contract_type": "NDA"}).status_code == 200
    assert api("PUT", f"/{c.id}/type", json={"contract_type": "NDA"}).status_code == 200
    audit, timeline = w.rows(c)
    assert len(audit) == 1 and len(timeline) == 1


def test_put_refuses_a_value_outside_the_list(w, api):
    """The list is the only vocabulary; free text would defeat the NDA check."""
    c = w.contract(None)
    res = api("PUT", f"/{c.id}/type", json={"contract_type": "Mutual NDA"})
    assert res.status_code == 422 and res.json()["detail"] == "Pick a contract type from the list"
    assert w.rows(c) == ([], [])


def test_put_is_404_for_a_walled_or_foreign_contract_and_403_without_update(w, api, db):
    """The PUT can't be used to probe or edit what the viewer cannot open or may not change."""
    c = w.contract(None)
    other_editor = w.user(EDITOR)
    w.wall(c, w.editor)
    assert api("PUT", f"/{c.id}/type", user=other_editor, json={"contract_type": "NDA"}).status_code == 404
    foreign_org = Organization(name="Other Co", slug=f"ot-{uuid.uuid4().hex[:8]}")
    db.add(foreign_org)
    db.flush()
    foreign = w.contract(None, org_id=foreign_org.id)
    assert api("PUT", f"/{foreign.id}/type", json={"contract_type": "NDA"}).status_code == 404
    d = w.contract(None)
    assert api("PUT", f"/{d.id}/type", user=w.user(READER), json={"contract_type": "NDA"}).status_code == 403


def test_a_person_chosen_type_survives_a_later_ai_reading(w, api, db):
    """The AI document reading runs after upload; it must not overwrite what a person picked."""
    c = w.contract(None)
    api("PUT", f"/{c.id}/type", json={"contract_type": "NDA"})
    db.refresh(c)
    output = ContractMetadataOutput(confidence="high", citations=[CitationInput(quote="by and between")],
                                    contract_type="Master Services Agreement")
    ai_controller._persist_metadata(
        db, output=output, context=SimpleNamespace(contract=c), created_by_user_id=w.editor.id,
        request_id=None, skill_run=SimpleNamespace(id="run-1"))
    assert c.contract_type == "NDA"


def test_patch_refuses_contract_type_but_other_fields_still_work(w, api):
    """The type has one door (PUT); a PATCH carrying it would skip the list check and the audit row."""
    c = w.contract("NDA", title="Old title")
    res = api("PATCH", f"/{c.id}", json={"contract_type": "MSA"})
    assert res.status_code == 422 and "PUT /contracts/{id}/type" in res.text
    ok = api("PATCH", f"/{c.id}", json={"title": "New title"})
    assert ok.status_code == 200 and ok.json()["title"] == "New title" and ok.json()["contract_type"] == "NDA"


# --- AC-27: a drafted NDA already carries the listed spelling ----------------------------------


async def test_drafting_an_nda_request_stores_the_listed_nda_type(db, monkeypatch):
    """AC-27: an NDA drafted from a new-agreement request must store exactly "NDA", or the NDA check
    would never find the contract it just helped create."""
    from tests.test_template_drafting_record import _draft, _ready

    monkeypatch.setattr("app.contract_files.service.dispatch_job", lambda db, *, job: job)
    user, _playbook, _pv = _ready(db)
    contract = await _draft(db, user, {"request_form": "new_agreement", "agreement_type": "NDA",
                                       "counterparty": "Globex Corporation"})
    assert contract.contract_type == "NDA" and ct.canonical_contract_type(contract.contract_type) == "NDA"
