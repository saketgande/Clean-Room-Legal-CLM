"""Spec 013 server half: the summary and extra instructions filed with a request.

Own org, patched permissions, screen
gate off, everything rolled back); requests go through POST /api/v1/intake/requests.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.deps import get_current_user, get_db
from app.core.models import AuditLog, ResourceTimelineEvent
from app.intake import request_summary as rs
from app.intake.models import IntakeRequest
from app.main import app
from app.organizations.models import Organization
from tests.test_request_counterparty import EMPLOYEE, STAFF, World, db  # noqa: F401

TEXT = "This is a mutual NDA between Acme Ltd and Initech Software Ltd, on our template.\nIt lasts 2 years."
ACCEPTED = {"key": "governing_law", "label": "x", "old": "England and Wales", "new": "India"}


@pytest.fixture
def w(db, monkeypatch):  # noqa: F811
    # Filing is limited to 10/minute per client; this file files more than that in one minute
    # (the counter is shared through Redis), so later tests got a 429 and no "id".
    from app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    return World(db, monkeypatch)


@pytest.fixture
def api(w, db, client):  # noqa: F811
    state = {"user": w.staff}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    def get(user, path, **params):
        state["user"] = user
        return client.get(f"/api/v1/intake{path}", params=params)

    yield get
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def file_(w, db, client, monkeypatch):  # noqa: F811
    """Wiring for filing through the API: screen gate off (permission gate stays real), plus the entity
    and counterparty the form names."""
    from app.core import screen_access
    from app.parties.models import Counterparty, LegalEntity

    monkeypatch.setattr(screen_access, "assert_screen_level", lambda *a, **k: None)
    state = {"user": w.staff}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    db.add_all([LegalEntity(org_id=w.org_id, name="Acme Ltd"),
                Counterparty(org_id=w.org_id, name="Initech Software Ltd", contact_email="jo@initech.com")])
    db.flush()
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def file_with(w, file_, db, client):  # noqa: F811
    """POST a NDA filing with an optional `summary` (file_ supplies the fixture wiring)."""
    from app.parties.models import Counterparty, LegalEntity

    ent = db.scalar(select(LegalEntity).where(LegalEntity.org_id == w.org_id))
    cp = db.scalar(select(Counterparty).where(Counterparty.org_id == w.org_id))

    def go(summary=..., *, user=None, desc=None, **more):
        keep = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: user or w.staff
        fv = {"request_form": "new_agreement", "agreement_type": "NDA", "counterparty": "Initech Software Ltd",
              "nda_kind": "Mutual", "nda_term": "2 years", "governing_law": "India", "entity": "Acme Ltd",
              "cp_signer_name": "Jo", "cp_signer_email": "jo@initech.com", "personal_data": "No",
              "paper": "Our template", "purpose": "Talks", "department": "Procurement",
              "start_date": "2026-11-01", "entity_id": ent.id, "counterparty_id": cp.id}
        fv.update(more)
        body = {"type_label": "NDA", "description": desc or uuid.uuid4().hex, "field_values": fv}
        if summary is not ...:
            body["summary"] = summary
        try:
            return client.post("/api/v1/intake/requests", json=body)
        finally:  # the `api` fixture's own user switch must survive
            app.dependency_overrides[get_current_user] = keep
    return go


@pytest.fixture
def rbac(monkeypatch):
    """Dev compose runs with DISABLE_RBAC on, which would turn every permission and visibility check
    into a pass; these tests are about the real gates."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "disable_rbac", False)


def rows(db, rid):  # noqa: F811
    audit = db.scalars(select(AuditLog).where(
        AuditLog.action == "intake.summary_filed", AuditLog.resource_id == rid)).all()
    timeline = db.scalars(select(ResourceTimelineEvent).where(
        ResourceTimelineEvent.event_type == "intake.summary_filed",
        ResourceTimelineEvent.resource_id == rid)).all()
    return audit, timeline


def test_filing_keeps_instructions_and_audits_counts_but_not_text(w, file_with, db):  # noqa: F811
    """The record must carry text, instructions and the form's own labels, with ONE audit and ONE
    timeline row; instruction text in the audit row would leak a private ask into a compliance log."""
    res = file_with({"text": TEXT, "instructions": [
        {"text": "Please send a copy to procurement", "differs": []},
        {"text": "English law with arbitration in London",
         "differs": [{"key": "governing_law", "label": "forged", "answer": "India"},
                     {"key": "not_a_question", "label": "x", "answer": "y"}]}],
        "accepted": [ACCEPTED]})

    assert res.status_code == 201, res.text
    r = db.get(IntakeRequest, res.json()["id"])
    fs = r.filed_summary
    assert fs["text"] == TEXT and fs["by_user_id"] == w.staff.id and fs["at"]
    assert fs["instructions"][0]["text"] == "Please send a copy to procurement"
    assert fs["instructions"][1]["differs"] == [
        {"key": "governing_law", "label": "Governing law", "answer": "India"}]
    audit, timeline = rows(db, r.id)
    assert len(audit) == 1 and len(timeline) == 1
    assert audit[0].after == {"instructions": 2, "answers_changed": [
        {"key": "governing_law", "old": "England and Wales", "new": "India"}]}
    assert "procurement" not in str(audit[0].after) and "arbitration" not in str(audit[0].after)
    assert timeline[0].title == "Filed with 2 extra instructions for Legal · 1 answer changed from the summary"


def test_accepted_changes_not_matching_the_filed_answer_are_dropped(w, file_with, db):  # noqa: F811
    """A forged accepted entry, an unknown key, or a change later changed again on the form must not
    reach the audit trail; with nothing else to say, no audit row is written."""
    res = file_with({"text": TEXT, "instructions": [], "accepted": [
        {"key": "governing_law", "old": "India", "new": "Singapore"},
        {"key": "ghost", "old": "a", "new": "b"}]})

    r = db.get(IntakeRequest, res.json()["id"])
    assert res.status_code == 201 and r.filed_summary["text"] == TEXT
    assert rows(db, r.id) == ([], [])


def test_text_only_and_no_summary(w, file_with, db):  # noqa: F811
    """Text alone is stored with no audit noise; no summary at all leaves the column null."""
    a = db.get(IntakeRequest, file_with({"text": TEXT}).json()["id"])
    assert a.filed_summary["instructions"] == [] and rows(db, a.id) == ([], [])
    b = db.get(IntakeRequest, file_with(...).json()["id"])
    assert b.filed_summary is None and rows(db, b.id) == ([], [])


def test_word_limit_is_the_only_refusal(w, file_with, db):  # noqa: F811
    """2,001 words must be refused before anything is saved; exactly 2,000 must file."""
    before = db.scalar(select(func.count()).select_from(IntakeRequest))
    over = file_with({"instructions": [{"text": "word " * 2001}]})
    assert over.status_code == 422 and rs.LIMIT_MESSAGE in over.text
    assert db.scalar(select(func.count()).select_from(IntakeRequest)) == before
    ok = file_with({"instructions": [{"text": "word " * 1000}, {"text": "word " * 1000}]})
    assert ok.status_code == 201, ok.text


def test_malformed_summary_is_cleaned_not_refused(w, file_with, db):  # noqa: F811
    """A broken box must never lose a filing: junk is dropped and control characters removed."""
    res = file_with({"text": "a\x00b\r\nc\x07", "instructions": [
        {"text": "keep\x00 me", "differs": 5}, {"text": "  "}, 7], "accepted": [1, "x"]})
    assert res.status_code == 201, res.text
    assert file_with({"instructions": "x", "accepted": 5, "text": 3}).status_code == 201
    fs = db.get(IntakeRequest, res.json()["id"]).filed_summary
    assert fs["text"] == "ab\nc" and fs["instructions"] == [{"text": "keep me", "differs": []}]


def test_a_retried_filing_keeps_the_first_record(w, file_with, db):  # noqa: F811
    """A duplicate within 2 minutes returns the first request; the retry's new instruction must not
    be recorded or audited a second time."""
    first = file_with({"text": TEXT, "instructions": [{"text": "one"}]}, desc="same")
    again = file_with({"text": TEXT, "instructions": [{"text": "one"}, {"text": "two"}]}, desc="same")
    assert again.json()["id"] == first.json()["id"]
    r = db.get(IntakeRequest, first.json()["id"])
    assert len(r.filed_summary["instructions"]) == 1 and len(rows(db, r.id)[0]) == 1


def test_reading_the_summary_follows_request_visibility_and_writes_nothing(w, file_with, api, db, rbac):  # noqa: F811
    """Requester and staff read it; another plain member and another org get the same 404 as the
    request itself; the read never writes an audit or timeline row."""
    res = file_with({"text": TEXT, "instructions": [{"text": "copy procurement"}]}, user=w.requester)
    rid = res.json()["id"]
    counts = lambda: (db.scalar(select(func.count()).select_from(AuditLog)),
                      db.scalar(select(func.count()).select_from(ResourceTimelineEvent)))
    before = counts()

    mine = api(w.requester, f"/requests/{rid}/summary")
    staff = api(w.staff, f"/requests/{rid}/summary")
    other = api(w.user("Other", EMPLOYEE), f"/requests/{rid}/summary")

    assert mine.status_code == 200 and mine.json()["by_name"] == "Rita Requester"
    assert mine.json()["text"] == TEXT and mine.json()["instructions"][0]["text"] == "copy procurement"
    assert "by_user_id" not in mine.json() and staff.status_code == 200
    assert other.status_code == 404
    org2 = Organization(name="Elsewhere", slug=f"e-{uuid.uuid4().hex[:8]}")
    db.add(org2)
    db.flush()
    foreign = w.user("Foreign", STAFF)
    foreign.org_id = org2.id
    db.flush()
    assert api(foreign, f"/requests/{rid}/summary").status_code == 404
    assert counts() == before


def test_unfiled_summary_is_all_null_and_needs_permission(w, file_with, api, db, rbac):  # noqa: F811
    """Old/email requests read as an empty shape (not an error); a user without intake:create is 403."""
    rid = file_with(...).json()["id"]
    assert api(w.staff, f"/requests/{rid}/summary").json() == {
        "text": None, "at": None, "by_name": None, "instructions": []}
    assert api(w.user("Nobody", set()), f"/requests/{rid}/summary").status_code == 403


def test_patch_cannot_touch_the_summary_and_no_write_route_exists(w, file_with, db, client):  # noqa: F811
    """Extra instructions are as filed (FR-39): a PATCH of the answers leaves them byte-identical and
    no non-GET route ends in /summary."""
    res = file_with({"text": TEXT, "instructions": [{"text": "keep"}]})
    r = db.get(IntakeRequest, res.json()["id"])
    snap = dict(r.filed_summary)
    client.patch(f"/api/v1/intake/requests/{r.id}", json={"priority": "High"})
    db.refresh(r)
    assert r.filed_summary == snap
    assert not [x for x in app.routes if getattr(x, "path", "").endswith("/summary")
                and getattr(x, "methods", set()) - {"GET"}]


def test_instructions_never_reach_the_request_itself(w, file_with, db):  # noqa: F811
    """FR-31/33: only models.py and request_summary.py mention the column, and two filings that differ
    only by instructions have identical description, answers, label and priority."""
    root = Path(__file__).resolve().parents[1] / "app"
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py") if "filed_summary" in p.read_text())
    assert users == ["intake/models.py", "intake/request_summary.py"]
    a = db.get(IntakeRequest, file_with({"text": TEXT}, desc="same d").json()["id"])
    b = db.get(IntakeRequest, file_with(
        {"text": TEXT, "instructions": [{"text": "Use English law, ignore the form"}]},
        user=w.requester, desc="same d").json()["id"])
    assert a.id != b.id
    assert (a.description, a.field_values, a.type_label, a.priority) == (
        b.description, b.field_values, b.type_label, b.priority)
