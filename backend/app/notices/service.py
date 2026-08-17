"""Notice register service.

Two rules shape this module:

1. **Deadline posture is derived, never stored.** A statutory deadline is a
   date; a stored `overdue` flag is only as fresh as the last sweep that wrote
   it. Computing on read means the register can never show "on track" for a
   notice that lapsed overnight.
2. **Every state change writes a NoticeEvent.** The timeline is the evidence of
   how a notice was handled, so it is append-only and written in the same
   transaction as the change it describes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException, status as http_status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.notices.models import (
    REMINDER_STAGE_RANK,
    TERMINAL_STATUSES,
    Notice,
    NoticeEvent,
)

# A notice inside this many days of its deadline is "at risk". Deliberately a
# plain constant: statutory windows are short, and per-org tuning is a product
# decision nobody has asked for yet.
AT_RISK_DAYS = 3


# --- helpers ---------------------------------------------------------------

def _next_ref(db: Session) -> str:
    n = db.execute(select(func.nextval("notice_ref_seq"))).scalar_one()
    return f"NOT-{n}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def deadline_posture(notice: Notice, *, today: date | None = None) -> tuple[str, int | None]:
    """(posture, days_to_due) for a notice.

    'met' once we've responded — a responded notice shouldn't keep screaming
    overdue. 'none' when the notice carries no deadline at all (informational
    notices are common and shouldn't pollute the overdue count).
    """
    if notice.response_due_date is None:
        return "none", None
    today = today or date.today()
    days = (notice.response_due_date - today).days
    if notice.responded_at is not None or notice.status in TERMINAL_STATUSES:
        return "met", days
    if days < 0:
        return "overdue", days
    if days <= AT_RISK_DAYS:
        return "at_risk", days
    return "on_track", days


def _user_name(db: Session, uid: str | None) -> str | None:
    if not uid:
        return None
    return db.scalar(select(User.full_name).where(User.id == uid))


def _contract_title(db: Session, cid: str | None) -> str | None:
    if not cid:
        return None
    return db.scalar(select(Contract.title).where(Contract.id == cid))


def _intake_ref(db: Session, request_id: str | None) -> str | None:
    """Human ref ('REQ-4123') of the escalation ticket, so the UI can link to it
    without a second round trip."""
    if not request_id:
        return None
    from app.intake.models import IntakeRequest

    return db.scalar(select(IntakeRequest.ref).where(IntakeRequest.id == request_id))


def serialize(
    db: Session, notice: Notice, *, with_events: bool = False, with_documents: bool = False
) -> dict:
    posture, days = deadline_posture(notice)
    data = {
        "id": notice.id,
        "ref": notice.ref,
        "direction": notice.direction,
        "notice_type": notice.notice_type,
        "subject": notice.subject,
        "description": notice.description or "",
        "counterparty_name": notice.counterparty_name,
        "counterparty_ref": notice.counterparty_ref,
        "contract_id": notice.contract_id,
        "contract_title": _contract_title(db, notice.contract_id),
        "notice_date": notice.notice_date,
        "received_at": notice.received_at,
        "response_due_date": notice.response_due_date,
        "status": notice.status,
        "priority": notice.priority,
        "owner_user_id": notice.owner_user_id,
        "owner_name": _user_name(db, notice.owner_user_id),
        "responded_at": notice.responded_at,
        "response_summary": notice.response_summary,
        "closed_at": notice.closed_at,
        "created_at": notice.created_at,
        "updated_at": notice.updated_at,
        "deadline_posture": posture,
        "days_to_due": days,
        "last_reminder_stage": notice.last_reminder_stage,
        "last_reminder_at": notice.last_reminder_at,
        "draft_response": notice.draft_response,
        "draft_response_at": notice.draft_response_at,
        "escalated_intake_request_id": notice.escalated_intake_request_id,
        "escalated_intake_ref": _intake_ref(db, notice.escalated_intake_request_id),
    }
    if with_events:
        data["events"] = [
            {
                "id": e.id,
                "kind": e.kind,
                "body": e.body,
                "actor_user_id": e.actor_user_id,
                "actor_name": _user_name(db, e.actor_user_id),
                "created_at": e.created_at,
            }
            for e in notice.events
        ]
    if with_documents:
        # extracted_text is deliberately omitted — it can run to 20k chars and
        # nothing in the UI renders it; the detail payload stays small.
        data["documents"] = [
            {
                "id": d.id,
                "filename": d.filename,
                "mime_type": d.mime_type,
                "size_bytes": d.size_bytes,
                "extraction_quality": d.extraction_quality,
                "has_text": bool(d.extracted_text),
                "created_at": d.created_at,
            }
            for d in notice.documents
        ]
    return data


def _add_event(
    db: Session, notice: Notice, *, kind: str, actor: User, body: str | None = None
) -> NoticeEvent:
    event = NoticeEvent(
        org_id=notice.org_id,
        notice_id=notice.id,
        kind=kind,
        body=body,
        actor_user_id=actor.id,
        created_by_user_id=actor.id,
    )
    db.add(event)
    return event


def _get(db: Session, org_id: str, notice_id: str) -> Notice:
    notice = db.scalar(
        select(Notice).where(Notice.id == notice_id, Notice.org_id == org_id)
    )
    if notice is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Notice not found")
    return notice


def _validate_contract(db: Session, org_id: str, contract_id: str | None) -> None:
    """A notice may only link to a contract in the caller's own org — otherwise
    the contract_id field becomes a cross-tenant existence oracle."""
    if not contract_id:
        return
    exists = db.scalar(
        select(Contract.id).where(Contract.id == contract_id, Contract.org_id == org_id)
    )
    if not exists:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Contract not found")


# --- reads -----------------------------------------------------------------

def list_notices(
    db: Session,
    *,
    org_id: str,
    status_filter: str | None = None,
    direction: str | None = None,
    notice_type: str | None = None,
    owner_user_id: str | None = None,
    contract_id: str | None = None,
    overdue_only: bool = False,
    q: str | None = None,
) -> list[dict]:
    stmt = select(Notice).where(Notice.org_id == org_id)
    if status_filter:
        stmt = stmt.where(Notice.status == status_filter)
    if direction:
        stmt = stmt.where(Notice.direction == direction)
    if notice_type:
        stmt = stmt.where(Notice.notice_type == notice_type)
    if owner_user_id:
        stmt = stmt.where(Notice.owner_user_id == owner_user_id)
    if contract_id:
        stmt = stmt.where(Notice.contract_id == contract_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Notice.subject.ilike(like),
                Notice.counterparty_name.ilike(like),
                Notice.ref.ilike(like),
                Notice.counterparty_ref.ilike(like),
            )
        )
    if overdue_only:
        # Narrow in SQL to what *could* be overdue; posture still decides.
        stmt = stmt.where(
            Notice.response_due_date.is_not(None),
            Notice.response_due_date < date.today(),
            Notice.responded_at.is_(None),
            Notice.status.not_in(TERMINAL_STATUSES),
        )
    # Soonest deadline first; undated notices sort last rather than blocking the
    # top of the register.
    stmt = stmt.order_by(
        Notice.response_due_date.is_(None),
        Notice.response_due_date.asc(),
        Notice.created_at.desc(),
    )
    return [serialize(db, n) for n in db.scalars(stmt).all()]


def get_notice(db: Session, *, org_id: str, notice_id: str) -> dict:
    return serialize(db, _get(db, org_id, notice_id), with_events=True, with_documents=True)


def summary(db: Session, *, org_id: str) -> dict:
    rows = db.execute(
        select(Notice.status, func.count()).where(Notice.org_id == org_id).group_by(Notice.status)
    ).all()
    by_status = {s: c for s, c in rows}
    live = db.scalars(
        select(Notice).where(
            Notice.org_id == org_id,
            Notice.response_due_date.is_not(None),
            Notice.responded_at.is_(None),
            Notice.status.not_in(TERMINAL_STATUSES),
        )
    ).all()
    overdue = at_risk = 0
    for n in live:
        posture, _ = deadline_posture(n)
        if posture == "overdue":
            overdue += 1
        elif posture == "at_risk":
            at_risk += 1
    return {
        "total": sum(by_status.values()),
        "open": by_status.get("open", 0),
        "draft": by_status.get("draft", 0),
        "responded": by_status.get("responded", 0),
        "escalated": by_status.get("escalated", 0),
        "closed": by_status.get("closed", 0),
        "overdue": overdue,
        "at_risk": at_risk,
    }


# --- writes ----------------------------------------------------------------

def create_notice(db: Session, *, actor: User, payload) -> dict:
    _validate_contract(db, actor.org_id, payload.contract_id)
    # Outbound notices are drafted before they go out; inbound ones are live the
    # moment they're filed.
    default_status = "draft" if payload.direction == "sent" else "open"
    notice = Notice(
        org_id=actor.org_id,
        ref=_next_ref(db),
        direction=payload.direction,
        notice_type=payload.notice_type,
        subject=payload.subject.strip(),
        description=payload.description or "",
        counterparty_name=payload.counterparty_name.strip(),
        counterparty_ref=payload.counterparty_ref,
        contract_id=payload.contract_id,
        notice_date=payload.notice_date,
        received_at=payload.received_at,
        response_due_date=payload.response_due_date,
        status=payload.status or default_status,
        priority=payload.priority,
        owner_user_id=payload.owner_user_id,
        created_by_user_id=actor.id,
    )
    db.add(notice)
    db.flush()  # need notice.id for the event's FK
    _add_event(db, notice, kind="filed", actor=actor, body=f"{notice.direction} · {notice.notice_type}")
    if notice.owner_user_id:
        _add_event(
            db, notice, kind="assigned", actor=actor,
            body=_user_name(db, notice.owner_user_id),
        )
    write_audit_log(
        db, action="notice.created", resource_type="notice", resource_id=notice.id,
        org_id=actor.org_id, actor_user_id=actor.id,
        after={"ref": notice.ref, "counterparty": notice.counterparty_name},
    )
    db.commit()
    db.refresh(notice)
    return serialize(db, notice, with_events=True, with_documents=True)


_UPDATABLE = (
    "subject", "counterparty_name", "direction", "notice_type", "description",
    "counterparty_ref", "contract_id", "notice_date", "received_at",
    "response_due_date", "priority", "owner_user_id",
)


def update_notice(db: Session, *, actor: User, notice_id: str, payload) -> dict:
    notice = _get(db, actor.org_id, notice_id)
    if payload.contract_id is not None:
        _validate_contract(db, actor.org_id, payload.contract_id)
    previous_owner = notice.owner_user_id
    changed: list[str] = []
    for attr in _UPDATABLE:
        val = getattr(payload, attr, None)
        if val is not None and getattr(notice, attr) != val:
            setattr(notice, attr, val)
            changed.append(attr)
    if not changed:
        return serialize(db, notice, with_events=True, with_documents=True)
    notice.updated_by_user_id = actor.id
    if "owner_user_id" in changed and notice.owner_user_id != previous_owner:
        _add_event(
            db, notice, kind="assigned", actor=actor,
            body=_user_name(db, notice.owner_user_id) or "Unassigned",
        )
    write_audit_log(
        db, action="notice.updated", resource_type="notice", resource_id=notice.id,
        org_id=actor.org_id, actor_user_id=actor.id, after={"changed": changed},
    )
    db.commit()
    db.refresh(notice)
    return serialize(db, notice, with_events=True, with_documents=True)


def set_status(db: Session, *, actor: User, notice_id: str, payload) -> dict:
    notice = _get(db, actor.org_id, notice_id)
    new_status = payload.status
    if new_status == notice.status and not payload.note:
        return serialize(db, notice, with_events=True, with_documents=True)
    before = notice.status
    notice.status = new_status
    notice.updated_by_user_id = actor.id

    if new_status == "responded":
        # Stamp once: the first response is what the statutory clock is measured
        # against, so re-entering 'responded' must not move the goalposts.
        if notice.responded_at is None:
            notice.responded_at = _utcnow()
        if payload.response_summary:
            notice.response_summary = payload.response_summary
    if new_status in TERMINAL_STATUSES and notice.closed_at is None:
        notice.closed_at = _utcnow()
    if new_status not in TERMINAL_STATUSES:
        # Re-opening clears the terminal stamp so closed_at always means "the
        # time it actually ended".
        notice.closed_at = None

    kind = {
        "responded": "responded",
        "escalated": "escalated",
        "closed": "closed",
    }.get(new_status, "status_changed")
    body = payload.note or f"{before} → {new_status}"
    _add_event(db, notice, kind=kind, actor=actor, body=body)
    write_audit_log(
        db, action=f"notice.{kind}", resource_type="notice", resource_id=notice.id,
        org_id=actor.org_id, actor_user_id=actor.id,
        before={"status": before}, after={"status": new_status},
    )
    db.commit()
    db.refresh(notice)
    return serialize(db, notice, with_events=True, with_documents=True)


def add_note(db: Session, *, actor: User, notice_id: str, payload) -> dict:
    notice = _get(db, actor.org_id, notice_id)
    _add_event(db, notice, kind="note", actor=actor, body=payload.body.strip())
    db.commit()
    db.refresh(notice)
    return serialize(db, notice, with_events=True, with_documents=True)


# --- response drafting & escalation ----------------------------------------

def _document_text(db: Session, notice: Notice) -> str:
    """Concatenated text of the notice's attachments, longest first — the fully
    OCR'd copy is worth more to the drafter than a one-line covering note."""
    from app.notices.models import NoticeDocument

    docs = db.scalars(
        select(NoticeDocument).where(NoticeDocument.notice_id == notice.id)
    ).all()
    texts = sorted(
        (d.extracted_text or "" for d in docs), key=len, reverse=True
    )
    return "\n\n".join(t for t in texts if t.strip())


def draft_response(db: Session, *, actor: User, notice_id: str) -> dict:
    """Generate (or regenerate) a draft reply and store it on the notice.

    Overwrites any previous draft — it's a proposal, not a version history, and
    the sent response is recorded separately in response_summary.
    """
    from app.notices.drafting import draft_notice_response

    notice = _get(db, actor.org_id, notice_id)
    result = draft_notice_response(
        db, org_id=actor.org_id, notice=notice,
        document_text=_document_text(db, notice),
    )
    notice.draft_response = result["draft"]
    notice.draft_response_at = _utcnow()
    notice.updated_by_user_id = actor.id
    _add_event(
        db, notice, kind="note", actor=actor,
        body="Drafted a response" + ("" if result["generated"] else " (template — AI unavailable)"),
    )
    write_audit_log(
        db, action="notice.response_drafted", resource_type="notice", resource_id=notice.id,
        org_id=actor.org_id, actor_user_id=actor.id, after={"generated": result["generated"]},
    )
    db.commit()
    db.refresh(notice)
    return {
        **serialize(db, notice, with_events=True, with_documents=True),
        "draft_generated": result["generated"],
    }


def escalate(db: Session, *, actor: User, notice_id: str, payload) -> dict:
    """Escalate a notice by opening a linked intake ticket.

    Deliberately delegating rather than duplicating: the intake engine already
    owns routing rules, team assignment, the SLA clock and the approval ladder.
    Re-implementing any of that on the notice would give the legal team a second
    queue to watch and two places for a matter to go stale.
    """
    from app.intake import service as intake_service
    from app.intake.schemas import RequestCreate

    notice = _get(db, actor.org_id, notice_id)
    if notice.escalated_intake_request_id:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, "This notice has already been escalated"
        )

    due = f" Response due {notice.response_due_date}." if notice.response_due_date else ""
    description = (
        f"Escalated from notice {notice.ref}.\n\n"
        f"Counterparty: {notice.counterparty_name}\n"
        f"Type: {prettify(notice.notice_type)}\n"
        f"{notice.description or ''}\n\n"
        f"Reason for escalation: {payload.reason.strip()}"
        f"{due}"
    ).strip()

    ticket = intake_service.create_request(
        db, actor=actor,
        payload=RequestCreate(
            type_label=payload.type_label or "Legal Question — General",
            subject=f"{notice.ref}: {notice.subject}"[:200],
            description=description,
            # A notice already past its deadline is not a Medium-priority matter.
            priority=payload.priority or ("Critical" if notice.response_due_date and
                                          deadline_posture(notice)[0] == "overdue" else "High"),
            source="api",
        ),
    )

    notice.escalated_intake_request_id = ticket["id"]
    notice.status = "escalated"
    notice.updated_by_user_id = actor.id
    _add_event(
        db, notice, kind="escalated", actor=actor,
        body=f"Opened {ticket['ref']} — {payload.reason.strip()}",
    )
    write_audit_log(
        db, action="notice.escalated", resource_type="notice", resource_id=notice.id,
        org_id=actor.org_id, actor_user_id=actor.id,
        after={"intake_request_id": ticket["id"], "intake_ref": ticket["ref"]},
    )
    db.commit()
    db.refresh(notice)
    return serialize(db, notice, with_events=True, with_documents=True)


def prettify(value: str | None) -> str:
    return (value or "").replace("_", " ").strip().capitalize()


# --- deadline reminders ----------------------------------------------------

def reminder_stage_for(days_to_due: int | None) -> str | None:
    """The milestone a notice sits at, or None if it's too far out (or has no
    deadline) to be worth chasing. Mirrors the register's own thresholds so the
    email and the amber badge can never disagree."""
    if days_to_due is None:
        return None
    if days_to_due < 0:
        return "overdue"
    if days_to_due == 0:
        return "due"
    if days_to_due <= AT_RISK_DAYS:
        return "t3"
    if days_to_due <= 7:
        return "t7"
    return None


_REMINDER_COPY = {
    "t7": ("Notice due in a week", "is due for a response in {days} days"),
    "t3": ("Notice due soon", "is due for a response in {days} days"),
    "due": ("Notice due today", "must be answered today"),
    "overdue": ("Notice deadline missed", "was due {days} days ago and has no recorded response"),
}


def run_reminders(db: Session, *, org_id: str | None = None) -> dict:
    """Notify owners about notices approaching or past their deadline.

    Idempotent by construction: a notice is only chased when its current
    milestone is more urgent than the last one stamped on it, so re-running the
    sweep on the same day sends nothing. Answered and closed notices are
    excluded by the query, so the chasing stops the moment the work is done.
    """
    from app.notifications.models import Notification

    stmt = select(Notice).where(
        Notice.response_due_date.is_not(None),
        Notice.responded_at.is_(None),
        Notice.status.not_in(TERMINAL_STATUSES),
    )
    if org_id:
        stmt = stmt.where(Notice.org_id == org_id)

    sent = 0
    skipped_no_owner = 0
    unchanged = 0
    for notice in db.scalars(stmt).all():
        _, days = deadline_posture(notice)
        stage = reminder_stage_for(days)
        if stage is None:
            continue
        if REMINDER_STAGE_RANK[stage] <= REMINDER_STAGE_RANK.get(notice.last_reminder_stage, 0):
            unchanged += 1
            continue

        # Stamp regardless of whether anyone could be notified — otherwise an
        # unassigned notice re-enters this branch on every single run.
        notice.last_reminder_stage = stage
        notice.last_reminder_at = _utcnow()

        if not notice.owner_user_id:
            skipped_no_owner += 1
            continue

        title, phrase = _REMINDER_COPY[stage]
        detail = phrase.format(days=abs(days) if days is not None else 0)
        db.add(
            Notification(
                org_id=notice.org_id,
                user_id=notice.owner_user_id,
                channel="in_app",
                event_type=f"notice.reminder.{stage}",
                subject=f"{title}: {notice.ref}",
                body=(
                    f'"{notice.subject}" from {notice.counterparty_name} {detail} '
                    f"(due {notice.response_due_date})."
                ),
                status="sent",
            )
        )
        # Recorded on the notice too, so the timeline shows what was chased and
        # when — useful evidence if a deadline is later disputed.
        db.add(
            NoticeEvent(
                org_id=notice.org_id, notice_id=notice.id, kind="reminded",
                body=f"{title.lower()} — {detail}",
            )
        )
        sent += 1

    db.commit()
    return {
        "sent": sent,
        "skipped_no_owner": skipped_no_owner,
        "already_reminded": unchanged,
    }


# --- documents & extraction ------------------------------------------------

# Same inline ceiling the intake attachments use. Notices are short documents;
# anything larger is a bundle that belongs in the contract record instead.
DOC_MAX_BYTES = 3 * 1024 * 1024


def _extract_document_text(content: bytes, *, mime_type: str, filename: str) -> tuple[str, float | None]:
    """Text + quality for an uploaded document, or ('', None) if extraction
    fails. Never raises: a notice whose PDF won't parse must still be filable."""
    from app.contract_files.text_extraction import extract_text

    try:
        result = extract_text(content, mime_type=mime_type, filename=filename)
        # Postgres TEXT can't hold a NUL byte, and some PDF font encodings emit
        # them — stripping here keeps the insert from failing on a valid file.
        return (result.text or "").replace("\x00", "")[:20000], result.quality_score
    except Exception:
        return "", None


def add_document(
    db: Session, *, actor: User, notice_id: str, filename: str, mime_type: str, content: bytes
) -> dict:
    from app.contract_files.service import validate_upload_mime
    from app.notices.models import NoticeDocument

    if len(content) > DOC_MAX_BYTES:
        raise HTTPException(413, "Attachment over the 3 MB limit")
    # The declared content-type is untrusted — this re-checks it against the
    # shared allowlist and the file's own magic bytes.
    mime_type = validate_upload_mime(content, mime_type)
    notice = _get(db, actor.org_id, notice_id)

    text, quality = _extract_document_text(content, mime_type=mime_type, filename=filename)
    doc = NoticeDocument(
        org_id=actor.org_id, notice_id=notice.id, filename=filename[:300],
        mime_type=mime_type[:120], size_bytes=len(content),
        extracted_text=text or None, extraction_quality=quality,
        created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    db.add(doc)
    _add_event(db, notice, kind="note", actor=actor, body=f"Attached {filename[:120]}")
    write_audit_log(
        db, action="notice.document.added", resource_type="notice", resource_id=notice.id,
        org_id=actor.org_id, actor_user_id=actor.id, after={"filename": filename[:300]},
    )
    db.commit()
    db.refresh(notice)
    return serialize(db, notice, with_events=True, with_documents=True)


def delete_document(db: Session, *, actor: User, notice_id: str, document_id: str) -> dict:
    from app.notices.models import NoticeDocument

    notice = _get(db, actor.org_id, notice_id)
    doc = db.scalar(
        select(NoticeDocument).where(
            NoticeDocument.id == document_id,
            NoticeDocument.notice_id == notice.id,
            NoticeDocument.org_id == actor.org_id,
        )
    )
    if doc is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Document not found")
    db.delete(doc)
    db.commit()
    db.refresh(notice)
    return serialize(db, notice, with_events=True, with_documents=True)


def extract_from_upload(
    db: Session, *, actor: User, filename: str, mime_type: str, content: bytes
) -> dict:
    """Read an uploaded notice and propose register fields — WITHOUT creating
    anything. The filer reviews the suggestions in the form and saves normally,
    so a mis-read deadline can never reach the register unreviewed."""
    from app.contract_files.service import validate_upload_mime
    from app.notices.extraction import extract_notice_fields

    if len(content) > DOC_MAX_BYTES:
        raise HTTPException(413, "Attachment over the 3 MB limit")
    mime_type = validate_upload_mime(content, mime_type)
    text, quality = _extract_document_text(content, mime_type=mime_type, filename=filename)
    if not text.strip():
        return {
            "suggestions": {},
            "confidence": 0.0,
            "source": "empty",
            "extraction_quality": quality,
            "message": "No readable text — the file may be a scan without OCR.",
        }
    result = extract_notice_fields(db, org_id=actor.org_id, text=text)
    source = result.pop("source", "llm")
    confidence = result.pop("confidence", 0.0)
    return {
        "suggestions": {k: v for k, v in result.items() if v is not None},
        "confidence": confidence,
        "source": source,
        "extraction_quality": quality,
        "message": None,
    }


def delete_notice(db: Session, *, actor: User, notice_id: str) -> None:
    notice = _get(db, actor.org_id, notice_id)
    write_audit_log(
        db, action="notice.deleted", resource_type="notice", resource_id=notice.id,
        org_id=actor.org_id, actor_user_id=actor.id,
        before={"ref": notice.ref, "counterparty": notice.counterparty_name},
    )
    db.delete(notice)  # events cascade
    db.commit()
