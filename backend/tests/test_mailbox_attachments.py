"""The M365 mailbox sweep must keep the email's body and its attachments.

Two defects this file guards, both silent:

* the sweep read `bodyPreview` — Graph's ~255-character preview — so every
  mailbox-filed request carried a truncated description into AI triage,
  classification and counterparty extraction;
* it never called `/attachments`, so an emailed contract produced a request
  with no document. The thing the channel exists to receive was the thing
  being discarded.
"""

import base64
from io import BytesIO

import pytest
from docx import Document

# Registers every mapper: intake_request carries an FK to `contract`, so the
# full registry has to be loaded before these models can be queried.
import app.models  # noqa: F401
from app.core.database import SessionLocal
from app.intake import ingest
from app.intake.models import IntakeDocument, IntakeRequest

GRAPH_MAILBOX = "legal@example.com"
_MSG_PREFIX = "test-mbox-attach-"


@pytest.fixture
def db_session():
    """A real session against the shared dev database.

    The suite writes into a database that already holds seed rows, so nothing
    here may assert on a global count. Each test files messages under its own
    `external_message_id` and looks its request up by that; the fixture deletes
    exactly those rows afterwards so the sweep's own dedupe cannot leak between
    tests.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        made = db.query(IntakeRequest).filter(
            IntakeRequest.external_message_id.like(f"{_MSG_PREFIX}%")
        ).all()
        for request in made:
            db.query(IntakeDocument).filter(
                IntakeDocument.request_id == request.id
            ).delete(synchronize_session=False)
            db.delete(request)
        db.commit()
        db.close()


def _filed(db, message_id: str) -> IntakeRequest:
    """The request this sweep filed, found by the id we sent — never by
    assuming ours is the only row in the table."""
    return db.query(IntakeRequest).filter(
        IntakeRequest.external_message_id == message_id
    ).one()


def _documents(db, request: IntakeRequest) -> list[IntakeDocument]:
    return db.query(IntakeDocument).filter(
        IntakeDocument.request_id == request.id
    ).order_by(IntakeDocument.created_at).all()


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class _Resp:
    def __init__(self, payload=None, content=b""):
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def graph(monkeypatch):
    """Stub Graph: one message with one real attachment, one inline logo, and
    one OneDrive reference attachment."""
    monkeypatch.setattr(ingest.settings, "intake_graph_tenant_id", "t", raising=False)
    monkeypatch.setattr(ingest.settings, "intake_graph_client_id", "c", raising=False)
    monkeypatch.setattr(ingest.settings, "intake_graph_client_secret", "s", raising=False)
    monkeypatch.setattr(ingest.settings, "intake_graph_mailbox", GRAPH_MAILBOX, raising=False)
    monkeypatch.setattr(ingest.settings, "intake_mailbox_auto_ack", False, raising=False)
    monkeypatch.setattr(ingest, "_graph_token", lambda: "token")

    state = {"messages": [], "attachments": [], "attachment_calls": 0}

    def fake_get(url, **kwargs):
        if url.endswith("/attachments"):
            state["attachment_calls"] += 1
            return _Resp({"value": state["attachments"]})
        return _Resp({"value": state["messages"]})

    monkeypatch.setattr(ingest.httpx, "get", fake_get)
    monkeypatch.setattr(ingest.httpx, "post", lambda *a, **k: _Resp({}))
    return state


def _message(*, body_html: str, has_attachments: bool, message_id: str) -> dict:
    return {
        "id": _MSG_PREFIX + message_id,
        "subject": "Acme MSA for review",
        "from": {"emailAddress": {"address": "admin@example.com"}},
        "receivedDateTime": "2026-09-17T09:00:00Z",
        "hasAttachments": has_attachments,
        "body": {"contentType": "html", "content": body_html},
    }


def test_the_emailed_contract_is_actually_attached(db_session, graph):
    """The defect this file exists for: the sweep filed the request and threw
    the document away, with no error anywhere."""
    graph["messages"] = [_message(body_html="<p>Signed copy attached.</p>", has_attachments=True, message_id="contract")]
    graph["attachments"] = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-1",
            "name": "Acme_MSA.docx",
            "contentType": "application/vnd.openxmlformats-officedocument."
                           "wordprocessingml.document",
            "isInline": False,
            "size": 4096,
            "contentBytes": base64.b64encode(
                _docx_bytes("3. FEES. Customer shall pay USD 2,400,000.")
            ).decode(),
        }
    ]

    result = ingest.poll_mailbox(db_session)

    assert result["status"] == "ok"
    request = _filed(db_session, _MSG_PREFIX + "contract")
    documents = _documents(db_session, request)

    assert [d.filename for d in documents] == ["Acme_MSA.docx"]
    # And its text was extracted, so triage can actually read the contract.
    assert "2,400,000" in (documents[0].extracted_text or "")


def test_inline_logos_and_reference_attachments_are_not_ingested(db_session, graph):
    """Guards a row per corporate signature logo on every request, and a
    zero-byte 'document' for every OneDrive link — neither carries a file the
    system can read."""
    graph["messages"] = [_message(body_html="<p>See attached.</p>", has_attachments=True, message_id="inline")]
    graph["attachments"] = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-logo", "name": "logo.png", "contentType": "image/png",
            "isInline": True, "size": 120,
            "contentBytes": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 64).decode(),
        },
        {
            "@odata.type": "#microsoft.graph.referenceAttachment",
            "id": "att-link", "name": "Shared doc", "isInline": False, "size": 0,
        },
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-real", "name": "NDA.docx",
            "contentType": "application/vnd.openxmlformats-officedocument."
                           "wordprocessingml.document",
            "isInline": False, "size": 4096,
            "contentBytes": base64.b64encode(_docx_bytes("Mutual NDA")).decode(),
        },
    ]

    ingest.poll_mailbox(db_session)

    request = _filed(db_session, _MSG_PREFIX + "inline")
    assert [d.filename for d in _documents(db_session, request)] == ["NDA.docx"]


def test_the_full_body_is_kept_not_the_preview(db_session, graph):
    """Guards the 255-character truncation: everything downstream — AI triage,
    the counterparty regex, the litigation read — works off this text."""
    long_body = "<p>" + ("Counterparty: Acme Industries. " * 40) + "</p>"
    graph["messages"] = [_message(body_html=long_body, has_attachments=False, message_id="body")]

    ingest.poll_mailbox(db_session)

    request = _filed(db_session, _MSG_PREFIX + "body")
    assert len(request.description) > 500
    assert "<p>" not in request.description  # markup flattened, not stored raw


def test_an_attachment_we_cannot_accept_is_recorded_not_dropped(db_session, graph):
    """Guards the silent-loss failure returning by the back door: a file over
    the inline cap, or of a type outside the allowlist, must leave a trace a
    human can act on rather than vanishing."""
    graph["messages"] = [_message(body_html="<p>Attached.</p>", has_attachments=True, message_id="rejected")]
    graph["attachments"] = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-exe", "name": "installer.exe",
            "contentType": "application/octet-stream", "isInline": False, "size": 512,
            "contentBytes": base64.b64encode(b"MZ\x90\x00" + b"\x00" * 128).decode(),
        }
    ]

    result = ingest.poll_mailbox(db_session)

    outcome = result["filed"][0]["attachments"][0]
    assert outcome["status"] == "rejected"
    # The request itself still exists — one bad file never loses the request.
    request = _filed(db_session, _MSG_PREFIX + "rejected")
    assert _documents(db_session, request) == []


def test_a_redelivered_message_does_not_duplicate_its_attachments(db_session, graph):
    """Graph and every mail relay deliver at least once. Re-attaching on each
    delivery would give the request N copies of the same contract."""
    graph["messages"] = [_message(body_html="<p>Attached.</p>", has_attachments=True, message_id="redeliver")]
    graph["attachments"] = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-1", "name": "NDA.docx",
            "contentType": "application/vnd.openxmlformats-officedocument."
                           "wordprocessingml.document",
            "isInline": False, "size": 4096,
            "contentBytes": base64.b64encode(_docx_bytes("Mutual NDA")).decode(),
        }
    ]

    ingest.poll_mailbox(db_session)
    calls_after_first = graph["attachment_calls"]
    ingest.poll_mailbox(db_session)

    request = _filed(db_session, _MSG_PREFIX + "redeliver")
    assert len(_documents(db_session, request)) == 1
    # And the second sweep did not even fetch them again.
    assert graph["attachment_calls"] == calls_after_first
