"""Gmail inbox sync for Legal Intake ("Sync With Email"): reads a Gmail inbox
over IMAP (App Password auth, no OAuth). The Email Intake Triage Agent
(email_triage_agent.py) decides whether each message is a Legal/CLM matter —
non-CLM mail (newsletters, personal mail, etc.) is left in the inbox
untouched and never filed. Matching messages go through the same
classify → route → triage pipeline the other channels use (ingest_message),
get any attachments attached (service.add_document, which extracts text),
and are re-tagged — both the ai_triage category and the Inbox "TYPE" column
(type_label) — using the attachment text once available.

Thread continuity: Gmail's IMAP extension exposes a stable X-GM-THRID per
conversation. The first message of a thread creates a request and stamps its
thread id onto field_values.gmail_thread_id; any later message in the same
thread (e.g. a reply with a revised document version) is folded into that
same request as an additional attachment instead of filing a new one.

Inert until INTAKE_GMAIL_ADDRESS / INTAKE_GMAIL_APP_PASSWORD are configured —
mirrors the poll_mailbox (M365/Graph) inert-until-configured pattern below.
No mocking: when configured, this always talks to the real Gmail account.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import re
from email.header import decode_header
from email.message import Message

from sqlalchemy.orm import Session

from app.core.audit import write_audit_log
from app.core.config import settings
from app.intake import email_triage_agent, service
from app.intake.ingest import _resolve_requester, ingest_message
from app.intake.models import IntakeRequest

_IMAP_HOST = "imap.gmail.com"
_THRID_RE = re.compile(rb"X-GM-THRID\s+(\d+)")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _plain_text_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition") or "")
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                try:
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        return ""
    if msg.get_content_type() == "text/plain":
        try:
            payload = msg.get_payload(decode=True) or b""
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def _attachments(msg: Message) -> list[tuple[str, str, bytes]]:
    """Returns [(filename, mime_type, content)] for every named part."""
    found: list[tuple[str, str, bytes]] = []
    if not msg.is_multipart():
        return found
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        try:
            content = part.get_payload(decode=True) or b""
        except Exception:
            continue
        if not content:
            continue
        found.append((_decode(filename), part.get_content_type() or "application/octet-stream", content))
    return found


def sync_gmail_inbox(db: Session) -> dict:
    if not settings.intake_gmail_address or not settings.intake_gmail_app_password:
        return {"status": "disabled",
                "note": "Set INTAKE_GMAIL_ADDRESS / INTAKE_GMAIL_APP_PASSWORD to enable Gmail sync."}

    imap = imaplib.IMAP4_SSL(_IMAP_HOST)
    try:
        try:
            imap.login(settings.intake_gmail_address, settings.intake_gmail_app_password)
        except imaplib.IMAP4.error as exc:
            return {"status": "error",
                    "note": f"Gmail login failed — check INTAKE_GMAIL_ADDRESS / INTAKE_GMAIL_APP_PASSWORD "
                            f"(must be a Google App Password, not the account password): {exc}"}
        except OSError as exc:
            return {"status": "error", "note": f"Could not reach {_IMAP_HOST}: {exc}"}

        status, _ = imap.select(settings.intake_gmail_folder, readonly=True)
        if status != "OK":
            return {"status": "error", "note": f"Could not open folder {settings.intake_gmail_folder!r}"}

        status, data = imap.uid("search", None, "ALL")
        if status != "OK":
            return {"status": "error", "note": "IMAP search failed"}
        uids = (data[0] or b"").split()
        uids = uids[-settings.intake_gmail_max_messages:]

        filed, skipped = [], []
        for uid in uids:
            try:
                status, msg_data = imap.uid("fetch", uid, "(X-GM-THRID BODY.PEEK[])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    skipped.append({"uid": uid.decode(), "reason": "fetch failed"})
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                thrid_match = _THRID_RE.search(msg_data[0][0] or b"")
                thread_id = thrid_match.group(1).decode() if thrid_match else None

                message_id = (msg.get("Message-ID") or "").strip() or None
                external_message_id = f"gmail:{message_id or uid.decode()}"
                subject = _decode(msg.get("Subject")) or "(no subject)"
                from_header = _decode(msg.get("From"))
                from_email = email.utils.parseaddr(from_header)[1] or None
                body = _plain_text_body(msg)
                attachments = _attachments(msg)

                # This exact message already filed (e.g. re-polled before the
                # watermark caught up) — nothing new to do.
                if db.query(IntakeRequest).filter(
                    IntakeRequest.external_message_id == external_message_id
                ).first():
                    skipped.append({"uid": uid.decode(), "reason": "already processed"})
                    continue

                thread_request = None
                if thread_id:
                    thread_request = (
                        db.query(IntakeRequest)
                        .filter(IntakeRequest.source == "gmail_email",
                                IntakeRequest.field_values.op("->>")("gmail_thread_id") == thread_id)
                        .first()
                    )

                requester = _resolve_requester(db, "org", from_email)

                if thread_request:
                    # Same Gmail conversation as an already-filed request (e.g.
                    # a reply with a revised document version) — always pull
                    # the latest document into that request rather than filing
                    # a new one. Only an exact re-fetch of a file already on
                    # this request (same filename + size) is skipped — a
                    # genuinely new/changed attachment is never dropped just
                    # because we've seen this thread before.
                    existing_docs = {
                        (d["filename"], d["size_bytes"])
                        for d in service.list_documents(db, actor=requester, request_id=thread_request.id)
                    }
                    excerpts, added = [], []
                    for filename, mime_type, content in attachments:
                        if (filename, len(content)) in existing_docs:
                            continue
                        try:
                            doc = service.add_document(
                                db, actor=requester, request_id=thread_request.id,
                                filename=filename, mime_type=mime_type, content=content,
                            )
                            excerpts.append(doc.get("extracted_text") or "")
                            added.append(filename)
                        except Exception as exc:
                            db.rollback()
                            skipped.append({"uid": uid.decode(), "reason": f"attachment {filename}: {exc}"})

                    if excerpts:
                        combined = "\n\n".join(e for e in [body, *excerpts] if e)[:20000]
                        triage = email_triage_agent.classify_email(subject, combined)
                        if triage.get("category") != "General":
                            thread_request.ai_triage = {
                                **(thread_request.ai_triage or {}),
                                **{k: v for k, v in triage.items() if k != "type_label"},
                            }
                        if triage.get("type_label"):
                            thread_request.type_label = triage["type_label"]
                        write_audit_log(
                            db, action="intake.ingest.gmail_email.thread_followup",
                            resource_type="intake_request", resource_id=thread_request.id,
                            org_id=thread_request.org_id, actor_user_id=requester.id,
                            after={"from": from_email, "message_id": message_id, "attachments": added},
                        )
                        db.commit()
                    filed.append({"id": thread_request.id, "ref": thread_request.ref,
                                  "deduped": True, "thread_followup": True, "documents_added": added})
                    continue

                # Email Intake Triage Agent: only file messages that actually
                # look like a Legal/CLM matter — everything else (newsletters,
                # personal mail, receipts…) is left in the inbox, untouched.
                if not email_triage_agent.is_clm_related(subject, body, [a[0] for a in attachments]):
                    skipped.append({"uid": uid.decode(), "subject": subject, "reason": "not CLM-related"})
                    continue

                out = ingest_message(
                    db, source="gmail_email", from_email=from_email, subject=subject,
                    body=body, external_message_id=external_message_id,
                )

                if not out["deduped"]:
                    excerpts = [body]
                    for filename, mime_type, content in attachments:
                        try:
                            doc = service.add_document(
                                db, actor=requester, request_id=out["id"],
                                filename=filename, mime_type=mime_type, content=content,
                            )
                            excerpts.append(doc.get("extracted_text") or "")
                        except Exception as exc:
                            # A failed insert leaves the session's transaction
                            # unusable until rolled back — without this, one
                            # bad attachment cascades into failures for every
                            # later message/attachment sharing this session.
                            db.rollback()
                            skipped.append({"uid": uid.decode(), "reason": f"attachment {filename}: {exc}"})

                    combined = "\n\n".join(e for e in excerpts if e)[:20000]
                    triage = email_triage_agent.classify_email(subject, combined)
                    r = db.query(IntakeRequest).filter(IntakeRequest.id == out["id"]).first()
                    if r:
                        if triage.get("category") != "General":
                            r.ai_triage = {**(r.ai_triage or {}),
                                           **{k: v for k, v in triage.items() if k != "type_label"}}
                        if triage.get("type_label"):
                            r.type_label = triage["type_label"]
                        if thread_id:
                            r.field_values = {**(r.field_values or {}), "gmail_thread_id": thread_id}
                        db.commit()

                filed.append(out)
            except Exception as exc:
                db.rollback()
                skipped.append({"uid": uid.decode(), "reason": str(exc)})

        return {"status": "ok", "fetched": len(uids), "filed": filed, "skipped": skipped}
    finally:
        try:
            imap.logout()
        except Exception:
            pass
