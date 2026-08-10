"""Multi-channel intake ingestion: email webhook, M365 mailbox polling, and a
Microsoft Teams outgoing-webhook bot — all funnel into the same
service.create_request pipeline (classify → route → triage), audited, and
idempotent on external_message_id.

Security posture (mirrors the reference): webhook secret compare is
constant-time and FAILS CLOSED in production when unconfigured; the public
endpoints are rate-limited with a small in-memory sliding window.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.config import settings
from app.core.models import AdminSetting
from app.intake.models import IntakeRequest
from app.intake import agents, service

WATERMARK_KEY = "intake.mailbox_watermark"
_GRAPH = "https://graph.microsoft.com/v1.0"

# ---- auth + rate limit -------------------------------------------------------

def check_webhook_secret(provided: str | None) -> None:
    secret = settings.intake_webhook_secret
    if not secret:
        if settings.environment == "production":
            raise HTTPException(503, "Intake webhook not configured")  # fail closed
        return  # open in dev, like the reference
    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTPException(401, "Bad webhook secret")


# ponytail: in-memory sliding window — per-process only; move to Redis if this
# ever runs multi-worker in production.
_hits: dict[str, list[float]] = {}

def rate_limit(key: str, limit: int = 30, window_s: int = 60) -> None:
    now = time.time()
    bucket = [t for t in _hits.get(key, []) if now - t < window_s]
    if len(bucket) >= limit:
        raise HTTPException(429, "Rate limit exceeded")
    bucket.append(now)
    _hits[key] = bucket


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


# ---- shared ingest core --------------------------------------------------------

def _resolve_requester(db: Session, org_id: str, email: str | None) -> User:
    """Match the sender to a user; unknown senders file under the org admin
    (single-tenant front door) with the raw address preserved on the request."""
    if email:
        u = db.query(User).filter(User.email.ilike(email)).first()
        if u:
            return u
    admin = db.query(User).filter(User.email == settings.dev_seed_admin_email).first()
    if admin:
        return admin
    u = db.query(User).first()
    if not u:
        raise HTTPException(500, "No users exist to attribute the request to")
    return u


def ingest_message(
    db: Session, *, source: str, from_email: str | None, subject: str,
    body: str, external_message_id: str,
) -> dict:
    """Idempotent channel ingest → create_request. Returns the request dict,
    with `deduped: True` when the message was already filed."""
    existing = (db.query(IntakeRequest)
                .filter(IntakeRequest.external_message_id == external_message_id)
                .first())
    if existing:
        return {"id": existing.id, "ref": existing.ref, "deduped": True}

    requester = _resolve_requester(db, "org", from_email)
    fv: dict = {"channel_from": from_email} if from_email else {}
    # Light counterparty extraction so screening can run on channel intake.
    m = re.search(r"counterpart(?:y|ies)[:\s]+([A-Z][\w&.\- ]{2,60})", body or "", re.IGNORECASE)
    if m:
        fv["counterparty"] = m.group(1).strip().rstrip(".")
    fv = fv or None
    payload = SimpleNamespace(
        source=source, requester_name=from_email or requester.email,
        department=None, request_type_id=None,
        # Not the raw subject line (that's `subject`, below) — a channel
        # message's category isn't known yet at ingest time, so it starts in
        # the same generic bucket the New Request form itself offers, and
        # gets refined to a real configured type where a caller re-classifies
        # (see email_triage_agent.classify_email for the Gmail channel).
        type_label=agents.DEFAULT_BUILTIN_EXTRA,
        subject=(subject or "").strip()[:200] or None,
        description=body or "", field_values=fv, priority="Medium",
    )
    out = service.create_request(db, actor=requester, payload=payload)
    r = db.query(IntakeRequest).filter(IntakeRequest.id == out["id"]).first()
    r.external_message_id = external_message_id
    write_audit_log(db, action=f"intake.ingest.{source}", resource_type="intake_request",
                    resource_id=r.id, org_id=r.org_id, actor_user_id=requester.id,
                    after={"from": from_email, "external_message_id": external_message_id})
    db.commit()
    out["deduped"] = False
    return out


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


def _get_watermark(db: Session) -> str | None:
    row = db.query(AdminSetting).filter(AdminSetting.key == WATERMARK_KEY).first()
    return row.value if row else None


def _set_watermark(db: Session, org_id: str, value: str) -> None:
    row = db.query(AdminSetting).filter(AdminSetting.key == WATERMARK_KEY).first()
    if row:
        row.value = value
    else:
        db.add(AdminSetting(org_id=org_id, key=WATERMARK_KEY, value=value))


def poll_mailbox(db: Session) -> dict:
    """Read the delegated legal mailbox since the last watermark and file each
    message as an intake request. Inert until Graph credentials are configured."""
    if not all([settings.intake_graph_tenant_id, settings.intake_graph_client_id,
                settings.intake_graph_client_secret, settings.intake_graph_mailbox]):
        return {"status": "disabled", "note": "Set INTAKE_GRAPH_* env vars to enable mailbox intake."}

    token = _graph_token()
    anchor_user = _resolve_requester(db, "org", None)  # org for the watermark row
    headers = {"Authorization": f"Bearer {token}"}
    params = {"$orderby": "receivedDateTime asc", "$top": "25",
              "$select": "id,subject,bodyPreview,from,receivedDateTime"}
    watermark = _get_watermark(db)
    if watermark:
        params["$filter"] = f"receivedDateTime gt {watermark}"
    resp = httpx.get(f"{_GRAPH}/users/{settings.intake_graph_mailbox}/messages",
                     headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    messages = resp.json().get("value", [])

    filed, latest = [], watermark
    for m in messages:
        sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address")
        out = ingest_message(db, source="email", from_email=sender,
                             subject=m.get("subject") or "", body=m.get("bodyPreview") or "",
                             external_message_id=m["id"])
        latest = m.get("receivedDateTime") or latest
        filed.append(out)
        if settings.intake_mailbox_auto_ack and sender and not out["deduped"]:
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
        _set_watermark(db, anchor_user.org_id, latest)
        db.commit()
    return {"status": "ok", "fetched": len(messages), "filed": filed}


# ---- Teams bot -------------------------------------------------------------------

_MENTION_RE = re.compile(r"<at>.*?</at>", re.DOTALL)


def handle_teams_activity(db: Session, activity: dict) -> dict:
    """Teams outgoing-webhook command grammar: `help`, `status REQ-####`,
    anything else files a ticket. Returns a Bot Framework message reply."""
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

    out = ingest_message(db, source="teams", from_email=None,
                         subject=text[:120], body=f"(via Teams, from {sender})\n\n{text}",
                         external_message_id=f"teams:{activity.get('id') or time.time()}")
    verb = "already filed as" if out["deduped"] else "filed as"
    return {"type": "message", "text": f"Request {verb} **{out['ref']}** — triaged and routed. ✔"}
