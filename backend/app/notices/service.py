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

from datetime import UTC, date, datetime

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.access import is_sealed_off, sealed_off_filter
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.deadlines import Period, explain, shift
from app.core.deadlines import _fmt as fmt_day
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


# --- pure helpers (no db — stay module-level) -------------------------------

def _utcnow() -> datetime:
    return datetime.now(UTC)


def deadline_posture(notice: Notice, *, today: date | None = None) -> tuple[str, int | None]:
    """(posture, days_to_due) for a notice.

    'met' once we've responded — a responded notice shouldn't keep screaming
    overdue. 'none' when the notice carries no deadline at all (informational
    notices are common and shouldn't pollute the overdue count). 'needs_date'
    when there is no date because code could not count one (no period stated,
    a statutory period, an anchor it doesn't know) — a person must set it, and
    that must not read as "no deadline".
    """
    if notice.response_due_date is None:
        live = notice.responded_at is None and notice.status not in TERMINAL_STATUSES
        if live and (notice.deadline_basis or {}).get("needs_person"):
            return "needs_date", None
        return "none", None
    today = today or utcnow().date()
    days = (notice.response_due_date - today).days
    if notice.responded_at is not None or notice.status in TERMINAL_STATUSES:
        return "met", days
    if days < 0:
        return "overdue", days
    if days <= AT_RISK_DAYS:
        return "at_risk", days
    return "on_track", days


# What a period runs from, as the fixed word the formula prints (never the
# model's own text, which is shown separately as the quote).
_ANCHOR_LABELS = {"notice_date": "notice dated", "receipt": "received"}


def compute_due(facts: dict | None, *, notice_date: date | None, received_at: date | None) -> dict:
    """The response deadline code counts from the notice's own wording.

    {"due": date|None, "formula": str|None, "reason": str|None, "needs_person": bool}.
    A period is counted from the notice date or the received date
    (app.core.deadlines: the day itself not counted, a weekend never moved later,
    clear days one day earlier); when the reading has both a period and a date,
    the period wins, because the date is the one the model could have worked out.
    An explicit date alone is taken as written (extraction already checked it is
    in the notice). Anything else — no deadline stated (a statute may supply one),
    a period whose number isn't in the quoted sentence, a period running from
    something code doesn't track, a missing anchor date — gets no date and needs
    a person, because a wrong deadline here is worse than none.
    """
    from app.obligations.service import _states_number
    if not facts:
        return {"due": None, "formula": None, "reason": None, "needs_person": False}

    def needs(reason: str) -> dict:
        return {"due": None, "formula": None, "reason": reason, "needs_person": True}

    if not facts.get("stated"):
        return needs("The notice states no deadline; a statutory period may apply. A person must set the date.")
    count, unit, runs_from = facts.get("period_count"), facts.get("period_unit"), facts.get("runs_from")
    explicit = facts.get("explicit_date")
    if explicit and (count is None or not unit):
        due = explicit if isinstance(explicit, date) else date.fromisoformat(explicit)
        return {"due": due, "formula": f"due {fmt_day(due)} = stated in notice",
                "reason": None, "needs_person": False}
    if count is None or not unit:
        return needs("The notice states a deadline but no date or period could be read. A person must set the date.")
    # Checked here, not only at extraction, so facts a client sends back on
    # create get the same check. A number not in the sentence came from a statute.
    if not _states_number(count, facts.get("quote") or ""):
        return needs(f"The period ({count}) is not in the quoted sentence; it may come from a statute. "
                     "A person must set the date.")
    if runs_from not in _ANCHOR_LABELS:
        wording = facts.get("runs_from_text") or "an event code does not track"
        return needs(f"The period runs from {wording!r}. A person must set the date.")
    anchor = notice_date if runs_from == "notice_date" else received_at
    if anchor is None:
        missing = "date on the notice" if runs_from == "notice_date" else "received date"
        return needs(f"The {missing} is not set, so the period can't be counted. Enter it or set the date.")
    period, clear = Period(count, unit), bool(facts.get("clear_days"))
    due = shift(anchor, period, clear_days=clear)
    formula = explain(due, anchor_label=_ANCHOR_LABELS[runs_from], anchor=anchor, period=period,
                      direction="after", clear_days=clear)
    return {"due": due, "formula": formula, "reason": None, "needs_person": False}


def _preview(result: dict) -> dict:
    """compute_due's result with the date as ISO, for JSON (basis column, API)."""
    return {**result, "due": result["due"].isoformat() if result["due"] else None}


def deadline_text(notice: Notice) -> str:
    """One line for timeline events and reminders: the formula (or why there is
    no date), labelled when nobody has confirmed a calculated date."""
    basis = notice.deadline_basis or {}
    if notice.response_due_date is None:
        return basis.get("reason") or "no deadline"
    text = basis.get("formula") or f"due {fmt_day(notice.response_due_date)}"
    if deadline_unconfirmed(notice):
        text += " (calculated - not yet confirmed)"
    recount = basis.get("recomputed")
    if recount:
        text += f"; recount after a date change: {recount.get('formula') or recount.get('reason')}"
    return text


def deadline_unconfirmed(notice: Notice) -> bool:
    """A code-computed date nobody has confirmed (or whose anchor changed since).
    Reminders still go out for it, labelled, so the clock is never silent."""
    basis = notice.deadline_basis or {}
    return basis.get("set_by") == "document" and (
        not basis.get("confirmed_by") or bool(basis.get("needs_reconfirm"))
    )


def prettify(value: str | None) -> str:
    return (value or "").replace("_", " ").strip().capitalize()


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


class NoticesService:
    def __init__(self, db: Session):
        self.db = db

    # --- helpers -------------------------------------------------------

    def _next_ref(self) -> str:
        n = self.db.execute(select(func.nextval("notice_ref_seq"))).scalar_one()
        return f"NOT-{n}"

    def _user_name(self, uid: str | None) -> str | None:
        if not uid:
            return None
        return self.db.scalar(select(User.full_name).where(User.id == uid))

    def _contract_title(self, cid: str | None) -> str | None:
        if not cid:
            return None
        return self.db.scalar(select(Contract.title).where(Contract.id == cid))

    def _intake_ref(self, request_id: str | None) -> str | None:
        """Human ref ('REQ-4123') of the escalation ticket, so the UI can link to it
        without a second round trip."""
        if not request_id:
            return None
        from app.intake.models import IntakeRequest

        return self.db.scalar(select(IntakeRequest.ref).where(IntakeRequest.id == request_id))

    def serialize(
        self, notice: Notice, *, with_events: bool = False, with_documents: bool = False
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
            "contract_title": self._contract_title(notice.contract_id),
            "notice_date": notice.notice_date,
            "received_at": notice.received_at,
            "response_due_date": notice.response_due_date,
            "status": notice.status,
            "priority": notice.priority,
            "owner_user_id": notice.owner_user_id,
            "owner_name": self._user_name(notice.owner_user_id),
            "responded_at": notice.responded_at,
            "response_summary": notice.response_summary,
            "closed_at": notice.closed_at,
            "created_at": notice.created_at,
            "updated_at": notice.updated_at,
            "deadline_posture": posture,
            "deadline_basis": notice.deadline_basis,
            "days_to_due": days,
            "last_reminder_stage": notice.last_reminder_stage,
            "last_reminder_at": notice.last_reminder_at,
            "draft_response": notice.draft_response,
            "draft_response_at": notice.draft_response_at,
            "escalated_intake_request_id": notice.escalated_intake_request_id,
            "escalated_intake_ref": self._intake_ref(notice.escalated_intake_request_id),
        }
        if with_events:
            data["events"] = [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "body": e.body,
                    "actor_user_id": e.actor_user_id,
                    "actor_name": self._user_name(e.actor_user_id),
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
        self, notice: Notice, *, kind: str, actor: User, body: str | None = None
    ) -> NoticeEvent:
        event = NoticeEvent(
            org_id=notice.org_id,
            notice_id=notice.id,
            kind=kind,
            body=body,
            actor_user_id=actor.id,
            created_by_user_id=actor.id,
        )
        self.db.add(event)
        return event

    def _get(self, user: User, notice_id: str) -> Notice:
        notice = self.db.scalar(
            select(Notice).where(Notice.id == notice_id, Notice.org_id == user.org_id)
        )
        if notice is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Notice not found")
        if notice.contract_id:
            contract = self.db.get(Contract, notice.contract_id)
            # A notice inherits its contract's ethical wall and clearance: it quotes and
            # argues about that contract. Not its ownership rule: legal staff work every
            # notice. "Notice not found" (not 403) so the id is no existence oracle.
            if contract is not None and is_sealed_off(self.db, user=user, contract=contract):
                raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Notice not found")
        return notice

    def _visible(self, user: User):
        """The notices this user may see: the org's, minus those whose contract is sealed off
        from them (same rule as _get). A notice with no contract is visible org-wide."""
        return and_(
            Notice.org_id == user.org_id,
            or_(
                Notice.contract_id.is_(None),
                Notice.contract_id.in_(
                    select(Contract.id).where(Contract.org_id == user.org_id, sealed_off_filter(user))
                ),
            ),
        )

    def _validate_contract(self, actor: User, contract_id: str | None) -> None:
        """A notice may only link to a contract in the caller's own org that the caller is not
        sealed off from -- otherwise contract_id becomes an existence oracle, and the caller
        would lock themselves out of the notice they just filed."""
        if not contract_id:
            return
        contract = self.db.scalar(
            select(Contract).where(Contract.id == contract_id, Contract.org_id == actor.org_id)
        )
        if contract is None or is_sealed_off(self.db, user=actor, contract=contract):
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Contract not found")

    # --- the response deadline -----------------------------------------------

    def _deadline_from_document(
        self, notice: Notice, facts: dict, *, actor: User, confirmed: bool
    ) -> None:
        """Count the date from the notice's own wording and the notice's dates."""
        result = compute_due(facts, notice_date=notice.notice_date, received_at=notice.received_at)
        confirmed = confirmed and result["due"] is not None
        notice.response_due_date = result["due"]
        notice.deadline_basis = {
            "facts": facts,
            "set_by": "document",
            **_preview(result),
            "confirmed_by": actor.id if confirmed else None,
            "confirmed_at": _utcnow().isoformat() if confirmed else None,
            "needs_reconfirm": False,
        }

    def _deadline_from_person(
        self, notice: Notice, due: date | None, *, actor: User, facts: dict | None
    ) -> None:
        """A person's date (or "no deadline applies") — never recounted by code.
        Setting it is confirming it. The document's wording is kept for reference."""
        notice.response_due_date = due
        notice.deadline_basis = {
            "facts": facts,
            "set_by": "person",
            "due": due.isoformat() if due else None,
            "formula": f"due {fmt_day(due)} = set by a person" if due else None,
            "reason": None if due else "A person decided no response deadline applies.",
            "needs_person": False,
            "confirmed_by": actor.id,
            "confirmed_at": _utcnow().isoformat(),
            "needs_reconfirm": False,
        }

    def _recount_after_anchor_change(self, notice: Notice, *, actor: User) -> None:
        """notice_date/received_at moved: recount a document date. A confirmed date
        is never overwritten: it stays the deadline, the recount sits beside it
        under `recomputed` and it needs re-confirming (as obligations and renewals
        do). A recount that lands back on the confirmed date clears both."""
        basis = notice.deadline_basis or {}
        if basis.get("set_by") != "document" or not basis.get("facts"):
            return
        if not basis.get("confirmed_by"):
            self._deadline_from_document(notice, basis["facts"], actor=actor, confirmed=False)
            return
        fresh = _preview(compute_due(basis["facts"], notice_date=notice.notice_date, received_at=notice.received_at))
        moved = fresh["due"] != basis.get("due")
        notice.deadline_basis = {**basis, "needs_reconfirm": moved, "recomputed": fresh if moved else None}

    def _log_deadline_change(
        self, notice: Notice, *, actor: User, before_due: date | None, before_text: str,
        before_basis: dict,
    ) -> None:
        """Evidence of who moved a legal deadline, from what, to what, and why."""
        after_text = deadline_text(notice)
        self._add_event(
            notice, kind="deadline_changed", actor=actor,
            body=f"Response deadline: {before_text} → {after_text}"
            if before_basis or before_due else f"Response deadline: {after_text}",
        )
        after_basis = notice.deadline_basis or {}
        write_audit_log(
            self.db, action="notice.deadline_changed", resource_type="notice", resource_id=notice.id,
            org_id=notice.org_id, actor_user_id=actor.id,
            before={
                "response_due_date": before_due.isoformat() if before_due else None,
                "formula": before_basis.get("formula"), "set_by": before_basis.get("set_by"),
            },
            after={
                "response_due_date": after_basis.get("due"),
                "formula": after_basis.get("formula"), "reason": after_basis.get("reason"),
                "set_by": after_basis.get("set_by"), "recomputed": after_basis.get("recomputed"),
            },
        )

    # --- reads -----------------------------------------------------------

    def list_notices(
        self,
        *,
        user: User,
        status_filter: str | None = None,
        direction: str | None = None,
        notice_type: str | None = None,
        owner_user_id: str | None = None,
        contract_id: str | None = None,
        overdue_only: bool = False,
        q: str | None = None,
    ) -> list[dict]:
        db = self.db
        stmt = select(Notice).where(self._visible(user))
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
                Notice.response_due_date < utcnow().date(),
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
        return [self.serialize(n) for n in db.scalars(stmt).all()]

    def get_notice(self, *, user: User, notice_id: str) -> dict:
        return self.serialize(self._get(user, notice_id), with_events=True, with_documents=True)

    def summary(self, *, user: User) -> dict:
        db = self.db
        # Counted over what the list shows, so a walled notice is neither listed nor counted.
        rows = db.execute(
            select(Notice.status, func.count()).where(self._visible(user)).group_by(Notice.status)
        ).all()
        by_status = {s: c for s, c in rows}
        live = db.scalars(
            select(Notice).where(
                self._visible(user),
                Notice.response_due_date.is_not(None),
                Notice.responded_at.is_(None),
                Notice.status.not_in(TERMINAL_STATUSES),
            )
        ).all()
        undated = db.scalars(
            select(Notice).where(
                self._visible(user),
                Notice.response_due_date.is_(None),
                Notice.deadline_basis.is_not(None),
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
            "needs_date": sum(1 for n in undated if deadline_posture(n)[0] == "needs_date"),
        }

    # --- writes ------------------------------------------------------------

    def create_notice(self, *, actor: User, payload) -> dict:
        db = self.db
        self._validate_contract(actor, payload.contract_id)
        # Outbound notices are drafted before they go out; inbound ones are live the
        # moment they're filed.
        default_status = "draft" if payload.direction == "sent" else "open"
        # A received notice nobody owns is never chased (run_reminders skips it),
        # so the filer owns it until they hand it on.
        owner = payload.owner_user_id or (actor.id if payload.direction == "received" else None)
        notice = Notice(
            org_id=actor.org_id,
            ref=self._next_ref(),
            direction=payload.direction,
            notice_type=payload.notice_type,
            subject=payload.subject.strip(),
            description=payload.description or "",
            counterparty_name=payload.counterparty_name.strip(),
            counterparty_ref=payload.counterparty_ref,
            contract_id=payload.contract_id,
            notice_date=payload.notice_date,
            received_at=payload.received_at,
            status=payload.status or default_status,
            priority=payload.priority,
            owner_user_id=owner,
            created_by_user_id=actor.id,
        )
        facts = payload.deadline_basis.model_dump(mode="json") if payload.deadline_basis else None
        if facts is not None:
            counted = compute_due(facts, notice_date=notice.notice_date, received_at=notice.received_at)
            if payload.response_due_date is not None and payload.response_due_date != counted["due"]:
                self._deadline_from_person(notice, payload.response_due_date, actor=actor, facts=facts)
            else:
                self._deadline_from_document(notice, facts, actor=actor, confirmed=payload.deadline_confirmed)
        elif payload.response_due_date is not None:
            self._deadline_from_person(notice, payload.response_due_date, actor=actor, facts=None)
        db.add(notice)
        db.flush()  # need notice.id for the event's FK
        self._add_event(notice, kind="filed", actor=actor, body=f"{notice.direction} · {notice.notice_type}")
        if notice.owner_user_id:
            self._add_event(
                notice, kind="assigned", actor=actor,
                body=self._user_name(notice.owner_user_id),
            )
        basis = notice.deadline_basis or {}
        if basis:
            self._add_event(
                notice, kind="deadline_changed", actor=actor,
                body=f"Response deadline: {deadline_text(notice)}",
            )
        write_audit_log(
            db, action="notice.created", resource_type="notice", resource_id=notice.id,
            org_id=actor.org_id, actor_user_id=actor.id,
            after={
                "ref": notice.ref, "counterparty": notice.counterparty_name,
                "response_due_date": basis.get("due"), "deadline_formula": basis.get("formula"),
                "deadline_reason": basis.get("reason"), "deadline_set_by": basis.get("set_by"),
            },
        )
        db.commit()
        db.refresh(notice)
        return self.serialize(notice, with_events=True, with_documents=True)

    # response_due_date is not here: it goes through the deadline helpers so its
    # provenance is recorded.
    _UPDATABLE = (
        "subject", "counterparty_name", "direction", "notice_type", "description",
        "counterparty_ref", "contract_id", "notice_date", "received_at",
        "priority", "owner_user_id",
    )
    # Nullable columns a PATCH clears by sending null; a null for any other
    # field is ignored (those columns are NOT NULL).
    _CLEARABLE = frozenset(
        {"counterparty_ref", "contract_id", "notice_date", "received_at", "owner_user_id"}
    )

    def update_notice(self, *, actor: User, notice_id: str, payload) -> dict:
        db = self.db
        notice = self._get(actor, notice_id)
        if payload.contract_id is not None:
            self._validate_contract(actor, payload.contract_id)
        sent = payload.model_fields_set
        previous_owner = notice.owner_user_id
        before_due, before_basis = notice.response_due_date, dict(notice.deadline_basis or {})
        before_text = deadline_text(notice)
        changed: list[str] = []
        for attr in self._UPDATABLE:
            if attr not in sent:
                continue
            val = getattr(payload, attr)
            if val is None and attr not in self._CLEARABLE:
                continue
            if getattr(notice, attr) != val:
                setattr(notice, attr, val)
                changed.append(attr)

        if "response_due_date" in sent:
            due = payload.response_due_date
            if before_basis.get("set_by") != "person" or due != before_due:
                self._deadline_from_person(notice, due, actor=actor, facts=before_basis.get("facts"))
        elif {"notice_date", "received_at"} & set(changed):
            self._recount_after_anchor_change(notice, actor=actor)
        deadline_moved = (notice.deadline_basis or {}) != before_basis
        if deadline_moved:
            changed.append("response_due_date")
            self._log_deadline_change(
                notice, actor=actor, before_due=before_due, before_text=before_text,
                before_basis=before_basis,
            )

        if payload.confirm_deadline:
            basis = notice.deadline_basis or {}
            if notice.response_due_date is None:
                raise HTTPException(
                    http_status.HTTP_409_CONFLICT,
                    "There is no deadline date to confirm. Set the date instead.",
                )
            # A person-set date was confirmed by setting it; nothing to do.
            if deadline_unconfirmed(notice):
                recount = basis.get("recomputed")
                if recount:
                    # Confirming after a date moved accepts the recount: the old date
                    # was counted from a date that no longer holds.
                    if recount.get("due") is None:
                        raise HTTPException(
                            http_status.HTTP_409_CONFLICT,
                            f"The recount has no date ({recount.get('reason')}). Set the date instead.",
                        )
                    basis = {**basis, **recount}
                    notice.response_due_date = date.fromisoformat(recount["due"])
                notice.deadline_basis = {
                    **basis, "confirmed_by": actor.id, "recomputed": None,
                    "confirmed_at": _utcnow().isoformat(), "needs_reconfirm": False,
                }
                changed.append("deadline_confirmed")
                self._add_event(
                    notice, kind="deadline_confirmed", actor=actor,
                    body=f"Confirmed {basis.get('formula') or notice.response_due_date}",
                )
                write_audit_log(
                    db, action="notice.deadline_confirmed", resource_type="notice",
                    resource_id=notice.id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"response_due_date": basis.get("due"), "formula": basis.get("formula")},
                )
        if notice.response_due_date != before_due:
            # The reminder stamp belongs to the old date: left in place, an "overdue"
            # sent for a wrong date would silence every reminder for the right one.
            notice.last_reminder_stage = None
            notice.last_reminder_at = None
        if not changed:
            return self.serialize(notice, with_events=True, with_documents=True)
        notice.updated_by_user_id = actor.id
        if "owner_user_id" in changed and notice.owner_user_id != previous_owner:
            self._add_event(
                notice, kind="assigned", actor=actor,
                body=self._user_name(notice.owner_user_id) or "Unassigned",
            )
        write_audit_log(
            db, action="notice.updated", resource_type="notice", resource_id=notice.id,
            org_id=actor.org_id, actor_user_id=actor.id, after={"changed": changed},
        )
        db.commit()
        db.refresh(notice)
        return self.serialize(notice, with_events=True, with_documents=True)

    def set_status(self, *, actor: User, notice_id: str, payload) -> dict:
        db = self.db
        notice = self._get(actor, notice_id)
        new_status = payload.status
        if new_status == notice.status and not payload.note:
            return self.serialize(notice, with_events=True, with_documents=True)
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
        self._add_event(notice, kind=kind, actor=actor, body=body)
        write_audit_log(
            db, action=f"notice.{kind}", resource_type="notice", resource_id=notice.id,
            org_id=actor.org_id, actor_user_id=actor.id,
            before={"status": before}, after={"status": new_status},
        )
        db.commit()
        db.refresh(notice)
        return self.serialize(notice, with_events=True, with_documents=True)

    def add_note(self, *, actor: User, notice_id: str, payload) -> dict:
        db = self.db
        notice = self._get(actor, notice_id)
        self._add_event(notice, kind="note", actor=actor, body=payload.body.strip())
        db.commit()
        db.refresh(notice)
        return self.serialize(notice, with_events=True, with_documents=True)

    # --- response drafting & escalation --------------------------------

    def _document_text(self, notice: Notice) -> str:
        """Concatenated text of the notice's attachments, longest first — the fully
        OCR'd copy is worth more to the drafter than a one-line covering note."""
        from app.notices.models import NoticeDocument

        docs = self.db.scalars(
            select(NoticeDocument).where(NoticeDocument.notice_id == notice.id)
        ).all()
        texts = sorted(
            (d.extracted_text or "" for d in docs), key=len, reverse=True
        )
        return "\n\n".join(t for t in texts if t.strip())

    def draft_response(self, *, actor: User, notice_id: str) -> dict:
        """Generate (or regenerate) a draft reply and store it on the notice.

        Overwrites any previous draft — it's a proposal, not a version history, and
        the sent response is recorded separately in response_summary.
        """
        from app.notices.drafting import draft_notice_response

        db = self.db
        notice = self._get(actor, notice_id)
        result = draft_notice_response(
            db, org_id=actor.org_id, notice=notice,
            document_text=self._document_text(notice),
        )
        notice.draft_response = result["draft"]
        notice.draft_response_at = _utcnow()
        notice.updated_by_user_id = actor.id
        self._add_event(
            notice, kind="note", actor=actor,
            body="Drafted a response" + ("" if result["generated"] else " (template — AI unavailable)"),
        )
        write_audit_log(
            db, action="notice.response_drafted", resource_type="notice", resource_id=notice.id,
            org_id=actor.org_id, actor_user_id=actor.id, after={"generated": result["generated"]},
        )
        db.commit()
        db.refresh(notice)
        return {
            **self.serialize(notice, with_events=True, with_documents=True),
            "draft_generated": result["generated"],
        }

    def escalate(self, *, actor: User, notice_id: str, payload) -> dict:
        """Escalate a notice by opening a linked intake ticket.

        Deliberately delegating rather than duplicating: the intake engine already
        owns routing rules, team assignment, the SLA clock and the approval ladder.
        Re-implementing any of that on the notice would give the legal team a second
        queue to watch and two places for a matter to go stale.
        """
        from app.intake import service as intake_service
        from app.intake.schemas import RequestCreate

        db = self.db
        notice = self._get(actor, notice_id)
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
        self._add_event(
            notice, kind="escalated", actor=actor,
            body=f"Opened {ticket['ref']} — {payload.reason.strip()}",
        )
        write_audit_log(
            db, action="notice.escalated", resource_type="notice", resource_id=notice.id,
            org_id=actor.org_id, actor_user_id=actor.id,
            after={"intake_request_id": ticket["id"], "intake_ref": ticket["ref"]},
        )
        db.commit()
        db.refresh(notice)
        return self.serialize(notice, with_events=True, with_documents=True)

    # --- deadline reminders ----------------------------------------------

    def run_reminders(self, *, org_id: str | None = None) -> dict:
        """Notify owners about notices approaching or past their deadline.

        Idempotent by construction: a notice is only chased when its current
        milestone is more urgent than the last one stamped on it, so re-running the
        sweep on the same day sends nothing. Answered and closed notices are
        excluded by the query, so the chasing stops the moment the work is done.
        """
        from app.notifications.models import Notification

        db = self.db
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
            # A code-computed date nobody confirmed still reminds (a silent clock is
            # the worse failure), but says so and shows the sum to check.
            if deadline_unconfirmed(notice):
                formula = (notice.deadline_basis or {}).get("formula")
                detail += f" (calculated - not yet confirmed: {formula})"
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

    # --- documents & extraction ---------------------------------------------

    def add_document(
        self, *, actor: User, notice_id: str, filename: str, mime_type: str, content: bytes
    ) -> dict:
        from app.contract_files.service import validate_upload_mime
        from app.notices.models import NoticeDocument

        db = self.db
        if len(content) > DOC_MAX_BYTES:
            raise HTTPException(413, "Attachment over the 3 MB limit")
        # The declared content-type is untrusted — this re-checks it against the
        # shared allowlist and the file's own magic bytes.
        mime_type = validate_upload_mime(content, mime_type)
        notice = self._get(actor, notice_id)

        text, quality = _extract_document_text(content, mime_type=mime_type, filename=filename)
        doc = NoticeDocument(
            org_id=actor.org_id, notice_id=notice.id, filename=filename[:300],
            mime_type=mime_type[:120], size_bytes=len(content),
            extracted_text=text or None, extraction_quality=quality,
            created_by_user_id=actor.id, updated_by_user_id=actor.id,
        )
        db.add(doc)
        self._add_event(notice, kind="note", actor=actor, body=f"Attached {filename[:120]}")
        write_audit_log(
            db, action="notice.document.added", resource_type="notice", resource_id=notice.id,
            org_id=actor.org_id, actor_user_id=actor.id, after={"filename": filename[:300]},
        )
        db.commit()
        db.refresh(notice)
        return self.serialize(notice, with_events=True, with_documents=True)

    def delete_document(self, *, actor: User, notice_id: str, document_id: str) -> dict:
        from app.notices.models import NoticeDocument

        db = self.db
        notice = self._get(actor, notice_id)
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
        return self.serialize(notice, with_events=True, with_documents=True)

    def extract_from_upload(
        self, *, actor: User, filename: str, mime_type: str, content: bytes
    ) -> dict:
        """Read an uploaded notice and propose register fields — WITHOUT creating
        anything. The filer reviews the suggestions in the form and saves normally,
        so a mis-read deadline can never reach the register unreviewed."""
        from app.contract_files.service import validate_upload_mime
        from app.notices.extraction import extract_notice_fields

        db = self.db
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
        facts = result.pop("deadline", None)
        preview = None
        if facts is not None:
            # Today stands in for the received date until the filer enters theirs;
            # create_notice recounts from the dates actually saved.
            received = utcnow().date()
            notice_date = result.get("notice_date")
            preview = {
                **_preview(compute_due(
                    facts, received_at=received,
                    notice_date=date.fromisoformat(notice_date) if notice_date else None,
                )),
                "assumed_received_at": received.isoformat(),
            }
        return {
            "suggestions": {k: v for k, v in result.items() if v is not None},
            "deadline_basis": facts,
            "deadline_preview": preview,
            "confidence": confidence,
            "source": source,
            "extraction_quality": quality,
            "message": None,
        }

    def delete_notice(self, *, actor: User, notice_id: str) -> None:
        db = self.db
        notice = self._get(actor, notice_id)
        write_audit_log(
            db, action="notice.deleted", resource_type="notice", resource_id=notice.id,
            org_id=actor.org_id, actor_user_id=actor.id,
            before={"ref": notice.ref, "counterparty": notice.counterparty_name},
        )
        db.delete(notice)  # events cascade
        db.commit()


