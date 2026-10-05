"""Filing a request and its attachment must succeed or fail as one.

Before: the browser filed the request, then uploaded the file in a second call.
A file the server refused (too big, wrong type) left the request filed without
it while the screen said "Submit failed", so people retried and duplicated it.
Triage was queued before the file arrived and never read attachments. And the
duplicate guard matched on type + note alone, merging two different requests
that happened to share a quick-phrase note.
"""

import base64

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import service, triage_agent
from app.intake.models import IntakeDocument, IntakeRequest
from app.intake.schemas import RequestCreate

_TAG = "test-intake-submit-atomic"
_TXT = base64.b64encode(b"MASTER SERVICES AGREEMENT between Acme and Globex. Term 3 years.").decode()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for r in s.scalars(select(IntakeRequest).where(IntakeRequest.description == _TAG)):
            s.query(IntakeDocument).filter(IntakeDocument.request_id == r.id).delete()
            s.delete(r)
        s.commit()
        s.close()


@pytest.fixture
def actor(db):
    user = db.scalar(select(User).order_by(User.created_at))
    if user is None:
        pytest.skip("needs a seeded user")
    return user


def _count(db):
    return db.scalar(select(func.count()).select_from(IntakeRequest).where(IntakeRequest.description == _TAG))


def _file(db, actor, *, attachments=(), field_values=None):
    return service.create_request(
        db, actor=actor, defer_triage=True,
        payload=RequestCreate(type_label="Contract Question", description=_TAG,
                              field_values=field_values, attachments=list(attachments)),
    )


def test_the_attachment_is_saved_with_the_request(db, actor):
    out = _file(db, actor, attachments=[{"filename": "msa.txt", "mime_type": "text/plain", "content_b64": _TXT}])
    docs = db.scalars(select(IntakeDocument).where(IntakeDocument.request_id == out["id"])).all()
    assert [d.filename for d in docs] == ["msa.txt"]
    assert "MASTER SERVICES AGREEMENT" in docs[0].extracted_text


def test_a_bad_file_files_nothing_and_names_the_file(db, actor):
    """The defect: the request was filed, then the upload failed."""
    xlsx = base64.b64encode(b"PK\x03\x04 not really a spreadsheet").decode()
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, attachments=[{"filename": "budget.xlsx", "content_b64": xlsx,
                                       "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}])
    assert exc.value.status_code == 415
    assert "budget.xlsx" in exc.value.detail
    assert _count(db) == 0


def test_an_oversized_file_files_nothing(db, actor):
    big = base64.b64encode(b"x" * (25 * 1024 * 1024 + 1)).decode()
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, attachments=[{"filename": "big.txt", "mime_type": "text/plain", "content_b64": big}])
    assert exc.value.status_code == 413 and "big.txt" in exc.value.detail
    assert _count(db) == 0


def test_triage_reads_the_attachment(db, actor):
    """Triage used to run before the file existed and never looked at it."""
    out = _file(db, actor, attachments=[{"filename": "msa.txt", "mime_type": "text/plain", "content_b64": _TXT}])
    r = db.get(IntakeRequest, out["id"])
    docs = db.scalars(select(IntakeDocument).where(IntakeDocument.request_id == r.id)).all()
    prompt = triage_agent._prompt(r, [], docs)
    assert "msa.txt" in prompt and "MASTER SERVICES AGREEMENT" in prompt


def test_different_requests_with_the_same_note_are_not_merged(db, actor):
    """Same type and note, different answers: two requests, not one."""
    a = _file(db, actor, field_values={"counterparty": "Acme"})
    b = _file(db, actor, field_values={"counterparty": "Globex"})
    assert a["id"] != b["id"] and _count(db) == 2


def test_an_identical_resubmission_is_still_treated_as_a_retry(db, actor):
    """Guards over-correction: a double click must not file twice."""
    a = _file(db, actor, field_values={"counterparty": "Acme"})
    b = _file(db, actor, field_values={"counterparty": "Acme"})
    assert a["id"] == b["id"] and _count(db) == 1


def test_an_identical_resubmission_without_answers_is_still_a_retry(db, actor):
    """A request with no structured answers (quick question, Ask Aegis, the general
    legal question form) stores JSON null in field_values; a double click must
    still return the first request instead of filing a second one."""
    a = _file(db, actor)
    b = _file(db, actor)
    assert a["id"] == b["id"] and _count(db) == 1


def test_quick_requests_with_different_subjects_are_not_merged(db, actor):
    """The quick form has no structured answers, so the subject tells them apart."""
    def mk(subj):
        return service.create_request(
            db, actor=actor, defer_triage=True,
            payload=RequestCreate(type_label="Contract Question", subject=subj, description=_TAG))

    assert mk("Acme MSA")["id"] != mk("Globex NDA")["id"]


def test_a_real_sized_legal_file_is_accepted(db, actor):
    """Scanned agreements run to tens of MB; the old 3 MB cap refused them."""
    big = base64.b64encode(b"CLAUSE 1. " * (2 * 1024 * 1024)).decode()  # 20 MB
    out = _file(db, actor, attachments=[{"filename": "large-msa.txt", "mime_type": "text/plain", "content_b64": big}])
    assert db.scalar(select(IntakeDocument.size_bytes).where(IntakeDocument.request_id == out["id"])) == 20 * 1024 * 1024


def test_a_filing_over_40_mb_in_total_files_nothing(db, actor):
    """Each file is under 25 MB but together they would exceed the web server's limit."""
    part = base64.b64encode(b"x" * (21 * 1024 * 1024)).decode()
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, attachments=[{"filename": f"part{i}.txt", "mime_type": "text/plain", "content_b64": part} for i in (1, 2)])
    assert exc.value.status_code == 413 and "40 MB" in exc.value.detail
    assert _count(db) == 0
