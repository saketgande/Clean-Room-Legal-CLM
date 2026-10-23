"""Save as Draft must keep the form, and only for the person who saved it.

The wizard's "Save as Draft" button called the back action and threw every
answer away, while the header claimed "Saved automatically". Drafts now live in
their own table: private to the saver, never a filed request (no triage, no SLA
clock, no board count), and never validated — a draft is incomplete by design.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import drafts
from app.intake.models import IntakeDraft, IntakeRequest
from app.intake.schemas import DraftSave

_TITLE = "test-intake-drafts"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for d in s.scalars(select(IntakeDraft).where(IntakeDraft.title == _TITLE)):
            s.delete(d)
        s.commit()
        s.close()


@pytest.fixture
def users(db):
    rows = db.scalars(select(User).order_by(User.created_at).limit(2)).all()
    if len(rows) < 2:
        pytest.skip("needs two seeded users")
    return rows


def _payload(**kw):
    base = dict(form_key="sow", title=_TITLE, values={"sow_title": "Phase 2"}, page_index=4, visited=5)
    base.update(kw)
    return DraftSave(**base)


def test_a_saved_draft_comes_back_exactly(db, users):
    """The defect: everything entered was lost. Values, step and parent all return."""
    me = users[0]
    saved = drafts.save(db, actor=me, payload=_payload(parent_contract_id="c-123"))
    mine = [d for d in drafts.list_mine(db, actor=me) if d["id"] == saved["id"]]
    assert mine and mine[0]["values"] == {"sow_title": "Phase 2"}
    assert (mine[0]["page_index"], mine[0]["visited"], mine[0]["parent_contract_id"]) == (4, 5, "c-123")


def test_saving_again_updates_rather_than_duplicates(db, users):
    me = users[0]
    first = drafts.save(db, actor=me, payload=_payload())
    drafts.save(db, actor=me, payload=_payload(values={"sow_title": "Phase 3"}), draft_id=first["id"])
    mine = [d for d in drafts.list_mine(db, actor=me) if d["title"] == _TITLE]
    assert len(mine) == 1 and mine[0]["values"]["sow_title"] == "Phase 3"


def test_a_draft_is_private_to_its_owner(db, users):
    """Someone else's half-written request must not be readable or deletable."""
    me, other = users
    saved = drafts.save(db, actor=me, payload=_payload())
    assert all(d["id"] != saved["id"] for d in drafts.list_mine(db, actor=other))
    with pytest.raises(HTTPException) as exc:
        drafts.delete(db, actor=other, draft_id=saved["id"])
    assert exc.value.status_code == 404


def test_a_draft_is_not_a_filed_request(db, users):
    """Saving must not start triage, the SLA clock or appear on the board."""
    before = db.scalar(select(IntakeRequest.id).order_by(IntakeRequest.created_at.desc()).limit(1))
    drafts.save(db, actor=users[0], payload=_payload())
    after = db.scalar(select(IntakeRequest.id).order_by(IntakeRequest.created_at.desc()).limit(1))
    assert before == after


def test_an_incomplete_draft_is_accepted(db, users):
    """Drafts are unvalidated by design — required fields come at submit."""
    saved = drafts.save(db, actor=users[0], payload=_payload(values={}))
    assert saved["values"] == {}


def test_unknown_form_is_refused(db, users):
    with pytest.raises(HTTPException) as exc:
        drafts.save(db, actor=users[0], payload=_payload(form_key="not_a_form"))
    assert exc.value.status_code == 422
