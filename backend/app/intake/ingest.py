"""Multi-channel intake ingestion: email webhook, M365 mailbox polling, and a
Microsoft Teams outgoing-webhook bot — all funnel into the same
service.create_request pipeline (classify → route → triage), audited, and
idempotent on external_message_id.

Security posture: webhook secret / HMAC compares are constant-time and FAIL
CLOSED in production when unconfigured; the public endpoints are rate-limited
by the shared Redis-backed limiter on the routes themselves; and a sender is
only attributed to the address it claims once the receiving mail server's
Authentication-Results vouch for it (see sender_verdict).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html as html_mod
import io
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.models import AdminSetting
from app.intake import agents
from app.intake.models import IntakeRequest
from app.intake.service import IntakeService

logger = logging.getLogger(__name__)

WATERMARK_KEY = "intake.mailbox_watermark"
_GRAPH = "https://graph.microsoft.com/v1.0"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# ---- auth + rate limit -------------------------------------------------------

def check_webhook_secret(provided: str | None) -> None:
    secret = settings.intake_webhook_secret
    if not secret:
        if settings.environment == "production":
            raise HTTPException(503, "Intake webhook not configured")  # fail closed
        return  # open in dev, like the reference
    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTPException(401, "Bad webhook secret")


# The webhooks are rate-limited by the shared slowapi limiter on the routes
# themselves (@limiter.limit) — Redis-backed, so the budget is shared across
# every worker instead of multiplied by them. The bespoke in-memory window that
# used to live here was per-process, which the systemd unit's `--workers 4`
# quietly turned into 4x the intended limit.


def verify_teams_hmac(raw_body: bytes, auth_header: str | None) -> None:
    secret = settings.intake_teams_secret
    if not secret:
        if settings.environment == "production":
            raise HTTPException(503, "Teams channel not configured")
        return
    if not auth_header or not auth_header.startswith("HMAC "):
        raise HTTPException(401, "Missing HMAC authorization")
    digest = hmac.new(base64.b64decode(secret), raw_body, hashlib.sha256).digest()
    if not hmac.compare_digest(auth_header[5:].strip(), base64.b64encode(digest).decode()):
        raise HTTPException(401, "Bad HMAC signature")


# ---- sender verification ---------------------------------------------------
# A `From:` header is typed by whoever sent the mail. Believing it let anyone
# file a request as the General Counsel, with their department and authority
# driving routing and their name on the audit trail.

_AUTH_METHODS = ("dmarc", "compauth", "dkim", "spf")
_AUTH_RE = re.compile(r"\b(dmarc|compauth|dkim|spf)\s*=\s*([a-z]+)", re.IGNORECASE)


def sender_verdict(auth_results: str | list[str] | None) -> dict:
    """Read the RFC 8601 ``Authentication-Results`` the receiving mail server
    wrote and decide whether the visible ``From:`` address can be trusted.

    Only DMARC binds the *visible* From: header to an authenticated identity.
    SPF authenticates the envelope sender and DKIM the signing domain, and a
    spoofer can pass either while forging From: — so neither is sufficient on
    its own. Microsoft's composite ``compauth`` is accepted alongside it
    because that is what Exchange stamps on intra-tenant mail, where DMARC is
    not evaluated at all.

    No header at all means unverified. Fails closed: not having
    checked is not the same as having passed.
    """
    if isinstance(auth_results, list):
        auth_results = "; ".join(h for h in auth_results if h)
    raw = (auth_results or "").strip()
    found: dict[str, str] = {}
    for method, result in _AUTH_RE.findall(raw):
        # First verdict wins: Exchange prepends its own header, so the newest
        # (outermost) result is the one the receiving server stands behind.
        found.setdefault(method.lower(), result.lower())

    verified = found.get("dmarc") == "pass" or found.get("compauth") == "pass"
    return {
        "verified": verified,
        **{method: found.get(method) for method in _AUTH_METHODS},
        "raw": raw[:500] or None,
    }


# ---- attachments (shared by every channel that carries files) -------------------
# These live here rather than in gmail_sync because the M365 sweep needs them
# too, and gmail_sync already imports from this module — the other direction
# would be a cycle.

def _ocr_to_docx(filename: str, mime_type: str, content: bytes, *, reducto=None) -> tuple[str, bytes] | None:
    """OCRs an attachment that needs it (an image, or a scanned/unreadable
    PDF or DOCX) via the same Reducto OCR provider the main contract-upload
    path uses, and copies the recognized text into a new .docx so it reads
    like any other attached document. Returns None when native extraction was
    already good enough (no OCR needed) or OCR produced nothing — e.g. Reducto
    is mocked (MOCK_REDUCTO=true) in local/dev, so this is a no-op there."""
    from app.contract_files.text_extraction import extract_text as native_extract_text
    from app.integrations.dependencies import get_reducto_client

    reducto = reducto or get_reducto_client()
    native = native_extract_text(content, mime_type=mime_type, filename=filename)
    if not native.needs_ocr:
        return None
    try:
        ocr = asyncio.run(reducto.extract_text(filename=filename, mime_type=mime_type, content=content))
    except Exception:
        return None
    if not ocr.text.strip():
        return None

    from docx import Document

    out = Document()
    out.add_heading(f"OCR: {filename}", level=2)
    for line in ocr.text.splitlines() or [""]:
        out.add_paragraph(line)
    buf = io.BytesIO()
    out.save(buf)
    stem = Path(filename).stem or "attachment"
    return f"{stem}_OCR.docx", buf.getvalue()


# ---- M365 mailbox polling -------------------------------------------------------

def _graph_token() -> str:
    resp = httpx.post(
        f"https://login.microsoftonline.com/{settings.intake_graph_tenant_id}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials",
              "client_id": settings.intake_graph_client_id,
              "client_secret": settings.intake_graph_client_secret,
              "scope": "https://graph.microsoft.com/.default"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _html_to_text(markup: str) -> str:
    """Flatten Outlook's HTML body to readable text.

    Outlook sends `body.contentType == "html"` for almost everything, so
    without this the description is a wall of markup that the triage agents
    and the counterparty regex have to read through."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])\s*>", "\n", text)
    # Strip until stable: a single pass leaves a reassembled tag behind for
    # nestings like "<scr<script>ipt>".
    previous = ""
    while text != previous:
        previous = text
        text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _graph_body_text(message: dict) -> str:
    """The real body, not `bodyPreview`.

    `bodyPreview` is Graph's ~255-character preview. Reading it meant every
    mailbox-filed request carried a truncated description into AI triage,
    classification and counterparty extraction."""
    body = message.get("body") or {}
    content = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        return _html_to_text(content)
    return content.strip()


def _graph_auth_results(message: dict) -> str | None:
    """The receiving server's Authentication-Results header(s), if any.

    Exchange Online stamps these on inbound mail; `internetMessageHeaders`
    requires an explicit `$select`, which is why the sweep asks for it.
    """
    lines = [
        h.get("value") or ""
        for h in (message.get("internetMessageHeaders") or [])
        if (h.get("name") or "").lower() == "authentication-results"
    ]
    return "; ".join(line for line in lines if line) or None


def _graph_attachments(message_id: str, headers: dict) -> list[tuple[str, str, bytes]]:
    """[(filename, mime_type, content)] for every real file on a message."""
    base = f"{_GRAPH}/users/{settings.intake_graph_mailbox}/messages/{message_id}/attachments"
    resp = httpx.get(base, headers=headers, timeout=60)
    resp.raise_for_status()
    found: list[tuple[str, str, bytes]] = []
    for att in resp.json().get("value", []):
        # itemAttachment (a forwarded message) and referenceAttachment (a
        # OneDrive link) carry no file bytes — only fileAttachment does.
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        # Inline parts are signature logos and embedded images, not documents.
        # Ingesting them gives every request a row per corporate logo.
        if att.get("isInline"):
            continue
        raw = att.get("contentBytes")
        if raw:
            content = base64.b64decode(raw)
        else:
            # Large attachments are omitted from the collection response and
            # have to be streamed from their own $value endpoint.
            value = httpx.get(f"{base}/{att['id']}/$value", headers=headers, timeout=120)
            value.raise_for_status()
            content = value.content
        if content:
            found.append((
                att.get("name") or "attachment",
                att.get("contentType") or "application/octet-stream",
                content,
            ))
    return found


# ---- Teams bot -------------------------------------------------------------------

_MENTION_RE = re.compile(r"<at>.*?</at>", re.DOTALL)


def _teams_message_key(activity: dict) -> str:
    """The idempotency key for a Teams activity.

    Bot Framework delivers at least once and replays a retried activity
    verbatim, so the key has to be a property of the message. The previous
    fallback was ``time.time()`` — unique on every call, which meant the key
    never matched and each retry filed another ticket.

    The activity id is the right key when Teams sends one. When it doesn't, a
    digest of the fields that identify the message is stable across retries
    while still separating two genuinely different messages — `timestamp` in
    particular, so the same person asking the same thing twice in the same
    chat is two requests, not one silently swallowed.
    """
    activity_id = str(activity.get("id") or "").strip()
    if activity_id:
        return f"teams:{activity_id}"
    sender = activity.get("from") or {}
    material = "|".join(str(part or "") for part in (
        (activity.get("conversation") or {}).get("id"),
        sender.get("aadObjectId") or sender.get("id"),
        activity.get("timestamp") or activity.get("localTimestamp"),
        activity.get("text"),
    ))
    return "teams:d:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class IngestService:
    """Multi-channel intake ingest: email webhook, M365 mailbox, Teams bot.

    Part of the DI migration (see backend/DI_MIGRATION.md). Constructed with
    a ``db`` session; ``intake`` defaults to an ``IntakeService`` built from
    the same session (composition — every channel funnels into
    ``create_request``), and ``reducto`` (the OCR client for scanned
    attachments) to the app provider. Pure helpers (secret/HMAC checks, sender
    verdict, Graph parsing, OCR-to-docx, the Teams dedup key) stay module-level.
    """

    def __init__(self, db: Session, *, intake: IntakeService | None = None, reducto=None):
        self.db = db
        self.intake = intake or IntakeService(db)
        self.reducto = reducto

    def _fallback_user(self) -> User:
        """Who an unattributable request belongs to.

        ponytail: the seeded admin, then any user. A dedicated service account is
        the right answer — add one and point this at it when intake grows an owner
        that isn't a person.
        """
        db = self.db
        admin = db.query(User).filter(User.email == settings.dev_seed_admin_email).first()
        if admin:
            return admin
        u = db.query(User).first()
        if not u:
            raise HTTPException(500, "No users exist to attribute the request to")
        return u

    def _resolve_requester(self, email: str | None, *, verified: bool = False) -> User:
        """Match the sender to a user — but only when the message proved who sent it.

        ``verified`` defaults to False so every caller has to opt in: an
        unauthenticated channel silently binding a claimed address to a real
        account is exactly the bug this guards. Unverified senders file under the
        fallback user with the raw address preserved on the request, so nothing is
        lost — it just isn't elevated to an identity nobody checked.
        """
        db = self.db
        if email and verified:
            # Exact, case-insensitive. The previous `ilike(email)` treated `%` and
            # `_` in an attacker-controlled header as SQL wildcards, so a From: of
            # `%@%` matched whichever user the table returned first.
            u = db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()
            if u:
                return u
        return self._fallback_user()

    def _already_filed(self, org_id: str, external_message_id: str) -> IntakeRequest | None:
        db = self.db
        return (db.query(IntakeRequest)
                .filter(IntakeRequest.org_id == org_id,
                        IntakeRequest.external_message_id == external_message_id)
                .first())

    def ingest_message(
        self, *, source: str, from_email: str | None, subject: str,
        body: str, external_message_id: str, auth_results: str | list[str] | None = None,
    ) -> dict:
        """Idempotent channel ingest → create_request. Returns the request dict,
        with `deduped: True` when the message was already filed.

        ``auth_results`` is the raw ``Authentication-Results`` header from the
        receiving mail server. Without it the sender is treated as unverified and
        the request is NOT attributed to the address it claims to come from.
        """
        db = self.db
        verdict = sender_verdict(auth_results)
        requester = self._resolve_requester(from_email, verified=verdict["verified"])

        # Fast path. Scoped to the org so it matches uq_intake_request_org_extmsg
        # and rides that index — and so it means the same thing the constraint does.
        existing = self._already_filed(requester.org_id, external_message_id)
        if existing is not None:
            return {"id": existing.id, "ref": existing.ref, "deduped": True}

        fv: dict = {"channel_from": from_email} if from_email else {}
        if from_email:
            # Kept on the request so a reviewer can see whose word the address is
            # on — the sender's, or the receiving mail server's.
            fv["channel_sender_verified"] = verdict["verified"]
            fv["channel_sender_auth"] = {
                method: verdict[method] for method in _AUTH_METHODS if verdict[method]
            } or None
        # Light counterparty extraction so screening can run on channel intake.
        m = re.search(r"counterpart(?:y|ies)[:\s]+([A-Z][\w&.\- ]{2,60})", body or "", re.IGNORECASE)
        if m:
            fv["counterparty"] = m.group(1).strip().rstrip(".")
        fv = fv or None
        payload = SimpleNamespace(
            source=source, requester_name=from_email or requester.email,
            department=None,
            # Not the raw subject line (that's `subject`, below) — a channel
            # message's category isn't known yet at ingest time, so it starts in
            # the same generic bucket the New Request form itself offers, and
            # gets refined to a real configured type where a caller re-classifies
            # (see email_triage_agent.classify_email for the Gmail channel).
            type_label=agents.DEFAULT_BUILTIN_EXTRA,
            subject=(subject or "").strip()[:200] or None,
            description=body or "", field_values=fv, priority="Medium",
        )
        try:
            out = self.intake.create_request(actor=requester, payload=payload,
                                             external_message_id=external_message_id)
        except IntegrityError:
            # Lost the race: a concurrent delivery of the same message filed it
            # between our check and this insert. The unique index is what makes
            # that safe — without it both deliveries would have committed, and the
            # loser would have been a duplicate legal matter.
            db.rollback()
            existing = self._already_filed(requester.org_id, external_message_id)
            if existing is None:
                raise
            logger.info("concurrent redelivery of %s folded into %s",
                        external_message_id, existing.ref)
            return {"id": existing.id, "ref": existing.ref, "deduped": True,
                    "sender_verified": verdict["verified"]}

        r = db.query(IntakeRequest).filter(IntakeRequest.id == out["id"]).first()
        write_audit_log(db, action=f"intake.ingest.{source}", resource_type="intake_request",
                        resource_id=r.id, org_id=r.org_id, actor_user_id=requester.id,
                        after={"from": from_email, "external_message_id": external_message_id,
                               "sender_verified": verdict["verified"],
                               "sender_auth": verdict["raw"]})
        if from_email and not verdict["verified"]:
            # Visible on the request, not just in the audit trail: whoever works
            # this ticket needs to know the address on it is only a claim.
            write_timeline_event(
                db, org_id=r.org_id, resource_type="intake_request", resource_id=r.id,
                event_type="intake.sender.unverified", actor_user_id=requester.id,
                title=f"Sender not verified — {from_email} is unconfirmed",
                details={"from": from_email, "authentication_results": verdict["raw"]},
            )
        db.commit()
        out["deduped"] = False
        out["sender_verified"] = verdict["verified"]
        return out

    def _ingest_attachment(self, *, requester, request_id: str,
                            filename: str, mime_type: str, content: bytes) -> str:
        """Attaches `content` to the request and, when it needed OCR, also
        attaches a companion .docx holding the OCR'd text to the same request.
        Returns the combined extracted text for downstream classification."""
        doc = self.intake.add_document(actor=requester, request_id=request_id,
                                    filename=filename, mime_type=mime_type, content=content)
        text = doc.get("extracted_text") or ""
        ocr_result = _ocr_to_docx(filename, mime_type, content, reducto=self.reducto)
        if ocr_result:
            ocr_filename, ocr_bytes = ocr_result
            ocr_doc = self.intake.add_document(actor=requester, request_id=request_id,
                                            filename=ocr_filename, mime_type=_DOCX_MIME, content=ocr_bytes)
            text = "\n\n".join(t for t in (text, ocr_doc.get("extracted_text") or "") if t)
        return text

    def _get_watermark(self) -> str | None:
        db = self.db
        row = db.query(AdminSetting).filter(AdminSetting.key == WATERMARK_KEY).first()
        return row.value if row else None

    def _set_watermark(self, org_id: str, value: str) -> None:
        db = self.db
        row = db.query(AdminSetting).filter(AdminSetting.key == WATERMARK_KEY).first()
        if row:
            row.value = value
        else:
            db.add(AdminSetting(org_id=org_id, key=WATERMARK_KEY, value=value))

    def _attach_graph_files(self, *, message_id: str, headers: dict,
                            request_id: str, sender: str | None,
                            sender_verified: bool = False) -> list[dict]:
        """Attach a message's files to the request it was filed as.

        An emailed contract IS the request — dropping it silently was the bug this
        exists to close. So a file we cannot accept (over the 25 MB attachment cap, or
        a type outside the allowlist) is recorded on the request's timeline rather
        than discarded, and never aborts the rest of the sweep.
        """
        requester = self._resolve_requester(sender, verified=sender_verified)
        try:
            files = _graph_attachments(message_id, headers)
        except Exception as exc:
            logger.warning("could not list attachments for message %s", message_id, exc_info=True)
            self._note_attachment_problem(request_id=request_id, requester=requester,
                                     filename=None, reason=str(exc))
            return [{"status": "error", "reason": str(exc)}]

        results: list[dict] = []
        for filename, mime_type, content in files:
            try:
                self._ingest_attachment(requester=requester, request_id=request_id,
                                   filename=filename, mime_type=mime_type, content=content)
                results.append({"filename": filename, "status": "attached", "bytes": len(content)})
            except HTTPException as exc:
                results.append({"filename": filename, "status": "rejected", "reason": str(exc.detail)})
                self._note_attachment_problem(request_id=request_id, requester=requester,
                                         filename=filename, reason=str(exc.detail))
            except Exception as exc:
                logger.warning("attachment %s failed on request %s", filename, request_id, exc_info=True)
                results.append({"filename": filename, "status": "error", "reason": str(exc)})
                self._note_attachment_problem(request_id=request_id, requester=requester,
                                         filename=filename, reason=str(exc))
        return results

    def _note_attachment_problem(self, *, request_id: str, requester: User,
                                 filename: str | None, reason: str) -> None:
        """Surface a dropped attachment where a human will see it — the request's
        own timeline — instead of only in the worker log."""
        db = self.db
        label = f"Attachment not stored: {filename}" if filename else "Attachments could not be read"
        try:
            write_timeline_event(
                db, org_id=requester.org_id, resource_type="intake_request",
                resource_id=request_id, event_type="intake.attachment.failed",
                title=label, actor_user_id=requester.id,
                details={"filename": filename, "reason": reason[:500]},
            )
            write_audit_log(db, action="intake.attachment.failed", resource_type="intake_request",
                            resource_id=request_id, org_id=requester.org_id,
                            actor_user_id=requester.id,
                            after={"filename": filename, "reason": reason[:500]})
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("could not record attachment failure on %s", request_id, exc_info=True)

    def poll_mailbox(self) -> dict:
        """Read the delegated legal mailbox since the last watermark and file each
        message as an intake request. Inert until Graph credentials are configured."""
        db = self.db
        if not all([settings.intake_graph_tenant_id, settings.intake_graph_client_id,
                    settings.intake_graph_client_secret, settings.intake_graph_mailbox]):
            return {"status": "disabled", "note": "Set INTAKE_GRAPH_* env vars to enable mailbox intake."}

        token = _graph_token()
        anchor_user = self._fallback_user()  # owns the watermark row
        headers = {"Authorization": f"Bearer {token}"}
        params = {"$orderby": "receivedDateTime asc", "$top": "25",
                  "$select": "id,subject,body,from,receivedDateTime,hasAttachments,"
                             "internetMessageHeaders"}
        watermark = self._get_watermark()
        if watermark:
            params["$filter"] = f"receivedDateTime gt {watermark}"
        resp = httpx.get(f"{_GRAPH}/users/{settings.intake_graph_mailbox}/messages",
                         headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        messages = resp.json().get("value", [])

        filed, latest = [], watermark
        for m in messages:
            sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address")
            out = self.ingest_message(source="email", from_email=sender,
                                 subject=m.get("subject") or "", body=_graph_body_text(m),
                                 external_message_id=m["id"],
                                 auth_results=_graph_auth_results(m))
            latest = m.get("receivedDateTime") or latest
            # The attachment is usually the point of the email — the draft NDA, the
            # counterparty's redline, the executed copy. Skipped on a dedupe: the
            # files are already on the request from the first delivery.
            if m.get("hasAttachments") and not out["deduped"]:
                out["attachments"] = self._attach_graph_files(
                    message_id=m["id"], headers=headers, request_id=out["id"],
                    sender=sender, sender_verified=out["sender_verified"],
                )
            filed.append(out)
            # Only acknowledge a sender the receiving server vouched for. Replying
            # to an unverified From: turns the legal mailbox into a backscatter
            # relay — anyone could make it mail a third party — and an auto-reply
            # to a forged address is an unanswered message going somewhere nobody
            # in legal chose to write to.
            if (settings.intake_mailbox_auto_ack and sender and not out["deduped"]
                    and out["sender_verified"]):
                try:  # best-effort acknowledgement; never blocks ingestion
                    httpx.post(f"{_GRAPH}/users/{settings.intake_graph_mailbox}/sendMail",
                               headers=headers, timeout=30,
                               json={"message": {
                                   "subject": f"Re: {m.get('subject') or 'your request'} [{out['ref']}]",
                                   "body": {"contentType": "text",
                                            "content": f"Legal received your request ({out['ref']}). "
                                                       "It has been triaged and routed — we'll follow up."},
                                   "toRecipients": [{"emailAddress": {"address": sender}}]}})
                except httpx.HTTPError:
                    pass
        if latest and latest != watermark:
            self._set_watermark(anchor_user.org_id, latest)
            db.commit()
        return {"status": "ok", "fetched": len(messages), "filed": filed}

    def handle_teams_activity(self, activity: dict) -> dict:
        """Teams outgoing-webhook command grammar: `help`, `status REQ-####`,
        anything else files a ticket. Returns a Bot Framework message reply."""
        db = self.db
        text = _MENTION_RE.sub("", activity.get("text") or "").strip()
        sender = (activity.get("from") or {}).get("name")
        low = text.lower()

        if not text or low in ("help", "hi", "hello"):
            return {"type": "message", "text":
                    "**Aegis Legal Intake**\n\n- `status REQ-1234` — check a request\n"
                    "- anything else — files a new legal request"}

        m = re.match(r"status\s+(req-\d+)", low)
        if m:
            r = (db.query(IntakeRequest)
                 .filter(IntakeRequest.ref.ilike(m.group(1))).first())
            if not r:
                return {"type": "message", "text": f"No request found for `{m.group(1).upper()}`."}
            return {"type": "message", "text":
                    f"**{r.ref}** · {r.type_label}\nStatus: {r.status} · SLA: {r.sla_status} · "
                    f"Priority: {r.priority}"}

        out = self.ingest_message(source="teams", from_email=None,
                             subject=text[:120], body=f"(via Teams, from {sender})\n\n{text}",
                             external_message_id=_teams_message_key(activity))
        verb = "already filed as" if out["deduped"] else "filed as"
        return {"type": "message", "text": f"Request {verb} **{out['ref']}** — triaged and routed. ✔"}


# --- DI-MIGRATION: temporary wrappers ---------------------------------------
# Imported directly by tests and older callers. Tracked in backend/DI_MIGRATION.md.

def _fallback_user(db: Session) -> User:
    return IngestService(db)._fallback_user()


def _resolve_requester(db: Session, email: str | None, *, verified: bool = False) -> User:
    return IngestService(db)._resolve_requester(email, verified=verified)


def ingest_message(
    db: Session, *, source: str, from_email: str | None, subject: str,
    body: str, external_message_id: str, auth_results: str | list[str] | None = None,
) -> dict:
    return IngestService(db).ingest_message(
        source=source, from_email=from_email, subject=subject, body=body,
        external_message_id=external_message_id, auth_results=auth_results,
    )


def _ingest_attachment(db: Session, *, requester, request_id: str,
                       filename: str, mime_type: str, content: bytes) -> str:
    return IngestService(db)._ingest_attachment(
        requester=requester, request_id=request_id,
        filename=filename, mime_type=mime_type, content=content,
    )


def poll_mailbox(db: Session) -> dict:
    return IngestService(db).poll_mailbox()


def handle_teams_activity(db: Session, activity: dict) -> dict:
    return IngestService(db).handle_teams_activity(activity)
