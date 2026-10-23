"""Obligation actions shared by the API and the assistant."""

import logging
import re
from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.schemas import ObligationExtractionOutput
from app.auth.models import User
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deadlines import Period, _fmt, contract_anchors, date_in_quote, explain, nth, shift
from app.core.enums import ObligationStatus
from app.integrations.resend import EmailSender, resend_client
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.obligations.models import Obligation, ObligationReminder

logger = logging.getLogger(__name__)

DUE_SOON_DAYS = 7

# Leading cadence word -> months between occurrences. Free-text recurrences such
# as "Ongoing during term" describe how long a duty lasts, not a schedule, so
# they never create a next occurrence. The snake_case values are the extraction
# skill's typed recurrence (ObligationOutput.recurrence).
_CADENCE_MONTHS = {
    "monthly": 1, "quarterly": 3, "semi-annually": 6, "semiannually": 6, "semi_annually": 6,
    "half-yearly": 6, "annually": 12, "annual": 12, "yearly": 12,
}


def _cadence_months(recurrence: str | None) -> int | None:
    words = re.findall(r"[a-z_-]+", (recurrence or "").lower())
    return _CADENCE_MONTHS.get(words[0]) if words else None


def next_due_date(recurrence: str | None, due: date | None) -> date | None:
    """The next occurrence's due date for a fixed cadence ("Monthly", "Quarterly",
    "Annually"...), or None when the obligation doesn't repeat on a schedule."""
    months = _cadence_months(recurrence)
    if not months or due is None:
        return None
    return shift(due, Period(months, "months"))


def complete_and_schedule_next(db: Session, *, ob: Obligation, actor_user_id: str) -> Obligation | None:
    """Mark an obligation completed and, when it repeats on a fixed schedule, open
    its next occurrence (linked back to this one) with the due date moved forward.
    Returns that next occurrence, or None.

    Each occurrence is counted from the series' FIRST due date (k periods on), not
    from the previous one, so monthly from 31 Jan runs 28 Feb, 31 Mar, 30 Apr
    instead of drifting to the 28th for good."""
    db.refresh(ob, with_for_update=True)  # two completions at once can't both add a successor
    if ob.status == ObligationStatus.COMPLETED:
        return None
    ob.status = ObligationStatus.COMPLETED
    ob.updated_by_user_id = actor_user_id
    months = _cadence_months(ob.recurrence)
    if not months or ob.due_date is None:
        return None
    meta = ob.metadata_json or {}
    series_start = date.fromisoformat(meta["series_start"]) if meta.get("series_start") else ob.due_date
    occurrence = int(meta.get("occurrence") or 0) + 1
    due = nth(series_start, Period(months, "months"), occurrence)
    successor = Obligation(
        org_id=ob.org_id,
        contract_id=ob.contract_id,
        contract_version_id=ob.contract_version_id,
        owner_user_id=ob.owner_user_id,
        responsible_party=ob.responsible_party,
        obligation_type=ob.obligation_type,
        description=ob.description,
        due_date=due,
        recurrence=ob.recurrence,
        status=ObligationStatus.OPEN,
        source_citation=ob.source_citation,
        metadata_json={
            "parent_obligation_id": ob.id,
            "series_start": series_start.isoformat(),
            "occurrence": occurrence,
        },
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    db.add(successor)
    db.flush()
    set_reminder(db, successor)
    return successor


# Fixed words for the formula: the anchor is named by code, never by model text.
_ANCHOR_LABELS = {
    "effective_date": "effective date",
    "expiration_date": "expiry",
    "signature_date": "signature date",
}
# Event anchors have no date on the contract: a person enters when it happened.
_EVENT_LABELS = {
    "termination": "terminated",
    "notice_received": "notice received",
    "invoice_received": "invoice received",
    "request_received": "request received",
    "disclosure": "disclosed",
    "other": "started",
}
_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_CLOSED = (ObligationStatus.COMPLETED, ObligationStatus.CANCELLED)


def _states_number(number: int, quote: str) -> bool:
    """Whether the cited clause itself states `number` ("15", "fifteen", "a month").
    A period the model brought in from law isn't in the quote, so it gets no date.

    ponytail: number words only up to 99; "one hundred and twenty" passes only with
    its "(120)", which contract drafting nearly always carries."""
    text = quote.lower()
    if re.search(rf"(?<!\d){number}(?!\d)", text):
        return True
    if number < 20:
        words = _ONES[number]
    elif number < 100:
        tens, ones = divmod(number, 10)
        words = _TENS[tens - 2] + (rf"[\s-]{_ONES[ones]}" if ones else "")
    else:
        return False
    if re.search(rf"\b{words}\b", text):
        return True
    return number == 1 and re.search(r"\ban?\s+(\w+\s+)?(business\s+)?(day|week|month|year)", text) is not None


def _as_date(value) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) else value


def obligation_deadline(timing, anchors: dict, *, quote: str | None = None, event_date=None) -> dict:
    """The stored deadline block for one obligation: the typed facts, the computed
    date with its formula, or the reason there is none.

    Only a period this contract states (and, given `quote`, one the cited clause
    actually contains) gets a date, counted from a contract date we hold or from
    an event date a person entered (`event_date`). A period from law, an event
    nobody logged or a disputed anchor waits for a person. An ongoing duty's end
    goes in `ends_on`, never `due`, so it can never show as overdue.

    A calendar date the clause writes out (`explicit_date`) is the date, but only
    when its day number and year are in the quote; one the model worked out is
    dropped and the period path applies."""
    facts = timing.model_dump(mode="json") if hasattr(timing, "model_dump") else dict(timing)
    event_date = _as_date(event_date)
    block = {"facts": facts, "due": None, "ends_on": None, "formula": None, "reason": None, "anchor": None,
             "anchor_date": event_date.isoformat() if event_date else None,
             "confirmed_by": None, "confirmed_at": None, "needs_reconfirm": False}
    kind = facts.get("kind")
    if kind not in ("deadline", "ongoing"):
        return block
    stated = _as_date(facts.get("explicit_date"))
    if stated is not None and date_in_quote(stated, quote):
        field = "ends_on" if kind == "ongoing" else "due"
        block[field] = stated.isoformat()
        block["formula"] = f"{'ends' if kind == 'ongoing' else 'due'} {_fmt(stated)} = stated in the contract"
        return block
    if kind == "ongoing" and facts.get("period_number") is None:
        return block
    if not facts.get("stated_in_text") or facts.get("period_number") is None or not facts.get("period_unit"):
        block["reason"] = "period not stated in the contract - needs a person"
        return block
    if quote is not None and not _states_number(facts["period_number"], quote):
        block["reason"] = "period not found in the quoted clause - needs a person"
        return block
    name = facts.get("anchor") or ""
    anchor = anchors.get(name)
    if anchor is not None:
        block["anchor"] = {"name": anchor.name, "value": anchor.value.isoformat() if anchor.value else None,
                           "source": anchor.source, "conflict": anchor.conflict}
        if anchor.value is None:
            block["reason"] = (
                f"{_ANCHOR_LABELS[anchor.name]} differs between the form and the contract text - needs a person"
                if anchor.conflict else anchor.reason or f"{_ANCHOR_LABELS[anchor.name]} not set on the contract"
            )
            return block
        start, label = anchor.value, _ANCHOR_LABELS[anchor.name]
    elif event_date is None:
        block["reason"] = f"starts when {facts.get('anchor_text') or 'the trigger happens'} - enter the date"
        return block
    else:
        start, label = event_date, _EVENT_LABELS.get(name, "started")
        block["anchor"] = {"name": name or "other", "value": event_date.isoformat(), "source": "user",
                           "conflict": None}
    period = Period(facts["period_number"], facts["period_unit"])
    direction, clear = facts.get("direction") or "after", bool(facts.get("clear_days"))
    try:
        due = shift(start, period, direction, clear_days=clear)
    except (OverflowError, ValueError):
        # "within 99999999 days" (a misread number) runs past year 9999; the schema
        # caps new readings, this catches stored ones and a negative count.
        block["reason"] = "period too large to count - needs a person"
        return block
    formula = explain(due, anchor_label=label, anchor=start, period=period, direction=direction, clear_days=clear)
    if kind == "ongoing":
        block["ends_on"], block["formula"] = due.isoformat(), "ends" + formula.removeprefix("due")
    else:
        block["due"], block["formula"] = due.isoformat(), formula
    return block


def _quote(ob: Obligation) -> str:
    return (ob.source_citation or {}).get("quote") or ""


def set_reminder(db: Session, ob: Obligation) -> None:
    """Keep exactly one unsent reminder per obligation, at max(due - 7, today).
    Called wherever due_date changes; a computed date nobody confirmed still
    reminds (owner decision), labelled as calculated when it is sent. A deleted,
    closed or dateless obligation is left with none."""
    for reminder in db.scalars(
        select(ObligationReminder).where(
            ObligationReminder.org_id == ob.org_id,
            ObligationReminder.obligation_id == ob.id,
            ObligationReminder.sent_at.is_(None),
        )
    ).all():
        db.delete(reminder)
    if ob.due_date is None or ob.status in _CLOSED or ob.deleted_at is not None:
        return
    db.add(
        ObligationReminder(
            org_id=ob.org_id,
            obligation_id=ob.id,
            remind_at=max(ob.due_date - timedelta(days=DUE_SOON_DAYS), utcnow().date()),
            channel="email",
            created_by_user_id=ob.updated_by_user_id,
            updated_by_user_id=ob.updated_by_user_id,
        )
    )


def is_confirmed(ob: Obligation) -> bool:
    return bool(((getattr(ob, "metadata_json", None) or {}).get("deadline") or {}).get("confirmed_by"))


def reminder_email(ob: Obligation) -> tuple[str, str]:
    """Subject and body of a reminder. Never the obligation's text (a contract
    excerpt, possibly PII): type and date only, the detail stays behind login."""
    label = ob.obligation_type or "contract obligation"
    when = f"due on {ob.due_date}"
    if not is_confirmed(ob):
        when += " (calculated - not yet confirmed)"
    return (
        f"Obligation due: {label}",
        f"<p>You have a <b>{label}</b> {when}.</p><p>Open the contract workspace to review the details.</p>",
    )


def _refresh_deadline(db: Session, ob: Obligation, anchors: dict) -> None:
    """Re-count one obligation's date from the current anchors. A date a person
    typed is left alone; a confirmed one is never overwritten, only flagged
    needs_reconfirm with the new count beside it when its anchor moved it."""
    meta = dict(ob.metadata_json or {})
    old = meta.get("deadline") or {}
    if not old.get("facts") or old.get("set_by"):
        return
    new = obligation_deadline(old["facts"], anchors, quote=_quote(ob), event_date=old.get("anchor_date"))
    if old.get("confirmed_by"):
        # The re-count always reflects today's anchors: an anchor that moved and
        # moved back lands on the confirmed date again, and the stale flag goes.
        if (new["due"], new["ends_on"]) == (old.get("due"), old.get("ends_on")):
            meta["deadline"] = {**old, "needs_reconfirm": False, "recomputed": None}
        else:
            recomputed = {k: new[k] for k in ("due", "ends_on", "formula", "reason", "anchor")}
            meta["deadline"] = {**old, "needs_reconfirm": True, "recomputed": recomputed}
    else:
        meta["deadline"] = new
        due = _as_date(new["due"])
        if ob.due_date != due:
            ob.due_date = due
            set_reminder(db, ob)
    ob.metadata_json = meta


def _kept_by_a_person(ob: Obligation) -> bool:
    meta = ob.metadata_json or {}
    return bool(meta.get("edited_by") or (meta.get("deadline") or {}).get("confirmed_by"))


def apply_extracted_obligations(
    db: Session,
    *,
    contract: Contract,
    version,
    output,
    skill_run,
    created_by_user_id: str | None,
    request_id: str | None,
) -> int:
    """Write an obligation_extraction result: the AI's wording, with every due
    date computed here from the contract's anchors. Returns the number created.
    Also the path a same-text replay takes (carry_forward_job), so a reused
    answer is re-counted from today's anchors. Never commits."""
    extracted = (
        output if isinstance(output, ObligationExtractionOutput)
        else ObligationExtractionOutput.model_validate(output)
    )
    # Replace prior AI-extracted, still-open obligations so re-extraction on a new
    # authoritative version does not duplicate, but keep any a person confirmed or
    # edited: a re-run must never undo that. A new item quoting the same clause as
    # a kept row is skipped.
    existing = db.scalars(
        select(Obligation).where(
            Obligation.org_id == contract.org_id,
            Obligation.contract_id == contract.id,
            Obligation.deleted_at.is_(None),
        )
    ).all()
    anchors = contract_anchors(db, contract)
    kept_quotes: set[str] = set()
    for ob in existing:
        if (ob.metadata_json or {}).get("source") != "ai_extraction" or ob.status in _CLOSED:
            continue
        if _kept_by_a_person(ob):
            # An empty quote would match every new item that has no quote either.
            if quote := _quote(ob).strip():
                kept_quotes.add(quote)
            _refresh_deadline(db, ob, anchors)
            continue
        ob.deleted_at = utcnow()
        ob.deleted_by_user_id = created_by_user_id
        ob.updated_by_user_id = created_by_user_id
        set_reminder(db, ob)  # deleted: drops its unsent reminder, so nobody is emailed about it
    created = 0
    for item in extracted.obligations:
        citation = item.citations[0].model_dump(mode="json") if item.citations else None
        quote = (citation or {}).get("quote") or ""
        if quote.strip() in kept_quotes:
            continue
        deadline = obligation_deadline(item.timing, anchors, quote=quote)
        obligation = Obligation(
            org_id=contract.org_id,
            contract_id=contract.id,
            contract_version_id=version.id if version else None,
            owner_user_id=contract.owner_user_id,
            responsible_party=item.responsible_party,
            obligation_type=item.obligation_type,
            description=item.description,
            due_date=_as_date(deadline["due"]),
            recurrence=None if item.recurrence == "none" else item.recurrence,
            status="open",
            source_citation=citation,
            metadata_json={
                "source": "ai_extraction",
                "confidence": item.confidence,
                "source_clause_type": item.source_clause_type,
                "skill_run_id": skill_run.id,
                "recurrence_note": item.recurrence_note,
                "deadline": deadline,
            },
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(obligation)
        db.flush()
        created += 1
        if obligation.due_date is not None:
            set_reminder(db, obligation)
    write_audit_log(
        db,
        action="contract.obligations_extracted",
        resource_type="contract",
        resource_id=contract.id,
        org_id=contract.org_id,
        actor_user_id=created_by_user_id,
        request_id=request_id,
        after={"obligation_count": created, "skill_run_id": skill_run.id},
    )
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.obligations_extracted",
        title=f"Obligations extracted ({created})",
        actor_user_id=created_by_user_id,
        request_id=request_id,
        skill_run_id=skill_run.id,
        details={"obligation_count": created},
    )
    return created


def recompute_due_dates(db: Session, contract: Contract) -> None:
    """Re-derive code-computed due dates after one of the contract's anchor dates
    changed (core/deadlines.recompute_contract_deadlines). Person-set dates stay;
    confirmed ones are flagged needs_reconfirm. Never commits."""
    anchors = contract_anchors(db, contract)
    for ob in db.scalars(
        select(Obligation).where(
            Obligation.org_id == contract.org_id,
            Obligation.contract_id == contract.id,
            Obligation.deleted_at.is_(None),
            Obligation.status.not_in(_CLOSED),
        )
    ).all():
        _refresh_deadline(db, ob, anchors)


RETIRED_REASON = "dated by the AI before Step 3d - re-run extraction or set the date"


def retire_ai_computed_obligation_dates(db: Session, *, org_id: str | None = None) -> int:
    """Clear the due dates the model itself worked out before Step 3d (an
    ai_extraction row with a date but no `deadline` block), with their unsent
    reminders, so no one is emailed about a date code never counted. Returns
    how many were cleared. Never commits; run once, on purpose. `org_id` None
    means every org (the app is single-tenant).

    A date a person typed back then left no marker on the row, only an
    `obligation.updated` audit entry carrying due_date, so those rows are kept."""
    from app.core.models import AuditLog

    query = select(Obligation).where(
        Obligation.deleted_at.is_(None),
        Obligation.due_date.is_not(None),
        Obligation.status.not_in(_CLOSED),
    )
    if org_id is not None:
        query = query.where(Obligation.org_id == org_id)
    rows = [
        ob for ob in db.scalars(query).all()
        if (ob.metadata_json or {}).get("source") == "ai_extraction" and not (ob.metadata_json or {}).get("deadline")
    ]
    if not rows:
        return 0
    typed = {
        log.resource_id for log in db.scalars(
            select(AuditLog).where(
                AuditLog.action == "obligation.updated",
                AuditLog.resource_type == "obligation",
                AuditLog.resource_id.in_([ob.id for ob in rows]),
            )
        ).all()
        if "due_date" in (log.after or {})
    }
    retired = 0
    for ob in rows:
        if ob.id in typed:
            continue
        before = ob.due_date.isoformat()
        ob.due_date = None
        ob.metadata_json = {**(ob.metadata_json or {}), "deadline": {
            "facts": None, "due": None, "ends_on": None, "formula": None, "reason": RETIRED_REASON,
            "anchor": None, "anchor_date": None, "confirmed_by": None, "confirmed_at": None,
            "needs_reconfirm": False,
        }}
        set_reminder(db, ob)
        write_audit_log(
            db,
            action="obligation.ai_date_retired",
            resource_type="obligation",
            resource_id=ob.id,
            org_id=ob.org_id,
            actor_user_id=None,
            before={"due_date": before},
            after={"due_date": None, "reason": RETIRED_REASON},
        )
        retired += 1
    return retired


def _deadline_view(ob) -> dict | None:
    """What the page shows beside the date: how it was counted (formula) or why
    there is none (reason), and whether a person has confirmed it."""
    block = (ob.metadata_json or {}).get("deadline")
    if not block:
        return None
    return {
        "formula": block.get("formula"),
        "reason": block.get("reason"),
        "ends_on": block.get("ends_on"),
        "anchor": block.get("anchor"),
        "anchor_date": block.get("anchor_date"),
        "set_by_person": bool(block.get("set_by")),
        "confirmed": bool(block.get("confirmed_by")),
        "confirmed_by": block.get("confirmed_by"),
        "confirmed_at": block.get("confirmed_at"),
        "needs_reconfirm": bool(block.get("needs_reconfirm")),
        "recomputed": block.get("recomputed"),
    }


def serialize_obligation(ob, *, contract_title=None, counterparty_name=None, owner_name=None) -> dict:
    """Obligation JSON enriched with the context the UI needs — which contract,
    which counterparty, and the internal owner's name — so the page never has
    to show a bare UUID or re-fetch the contract list to resolve titles."""
    return {
        "id": ob.id,
        "org_id": ob.org_id,
        "contract_id": ob.contract_id,
        "contract_version_id": ob.contract_version_id,
        "contract_title": contract_title,
        "counterparty_name": counterparty_name,
        "owner_user_id": ob.owner_user_id,
        "owner_name": owner_name,
        "responsible_party": ob.responsible_party,
        "obligation_type": ob.obligation_type,
        "description": ob.description,
        "due_date": ob.due_date.isoformat() if ob.due_date else None,
        "recurrence": ob.recurrence,
        "status": ob.status,
        "source_citation": ob.source_citation,
        "metadata_json": ob.metadata_json or {},
        "deadline": _deadline_view(ob),
        "created_at": ob.created_at.isoformat() if ob.created_at else None,
        "updated_at": ob.updated_at.isoformat() if ob.updated_at else None,
    }


class ObligationsService:
    def __init__(self, db: Session, *, resend: EmailSender | None = None):
        self.db = db
        self.resend = resend or resend_client

    def serialize_one(self, ob) -> dict:
        """Single-obligation enrichment (get/update/complete) — one extra lookup."""
        db = self.db
        contract = db.get(Contract, ob.contract_id)
        owner_name = None
        if ob.owner_user_id:
            owner = db.get(User, ob.owner_user_id)
            owner_name = owner.full_name if owner else None
        return serialize_obligation(
            ob,
            contract_title=contract.title if contract else None,
            counterparty_name=contract.counterparty_name if contract else None,
            owner_name=owner_name,
        )

    def update_obligation(self, *, ob: Obligation, payload, current_user: User) -> dict:
        db = self.db
        updates = payload.model_dump(exclude_unset=True)
        if "owner_user_id" in updates and updates["owner_user_id"] is not None:
            owner = db.get(User, updates["owner_user_id"])
            if owner is None or owner.org_id != current_user.org_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Owner user must belong to this organization",
                )
        anchor_date_set = "anchor_date" in updates
        anchor_date = updates.pop("anchor_date", None)
        confirm = updates.pop("confirm", None)
        due_before = ob.due_date
        for key, value in updates.items():
            setattr(ob, key, value)
        ob.updated_by_user_id = current_user.id
        self._apply_deadline_edits(
            ob, current_user=current_user, due_typed="due_date" in updates,
            anchor_date_set=anchor_date_set, anchor_date=anchor_date, confirm=bool(confirm),
        )
        if ob.due_date != due_before:
            set_reminder(db, ob)
        write_audit_log(
            db,
            action="obligation.updated",
            resource_type="obligation",
            resource_id=ob.id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            after=payload.model_dump(exclude_unset=True, mode="json"),
        )
        db.commit()
        db.refresh(ob)
        return self.serialize_one(ob)

    def _apply_deadline_edits(
        self, ob: Obligation, *, current_user: User, due_typed: bool, anchor_date_set: bool,
        anchor_date: date | None, confirm: bool,
    ) -> None:
        """Record what a person did to the date, so re-extraction and anchor
        recomputes leave it alone (edited_by / set_by / confirmed_by)."""
        meta = dict(ob.metadata_json or {})
        block = dict(meta.get("deadline") or {})
        now = utcnow().isoformat()
        if anchor_date_set:
            if not block.get("facts"):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "This obligation has no period to count from an event"
                )
            contract = self.db.get(Contract, ob.contract_id)
            # The event date gives a fresh computed date: not confirmed until a person says so.
            block = obligation_deadline(
                block["facts"], contract_anchors(self.db, contract), quote=_quote(ob), event_date=anchor_date
            )
            ob.due_date = _as_date(block["due"])
        if due_typed:
            block.update(due=ob.due_date.isoformat() if ob.due_date else None, formula=None,
                         reason="set by a person", set_by=current_user.id, recomputed=None,
                         confirmed_by=current_user.id, confirmed_at=now, needs_reconfirm=False)
        if confirm:
            recomputed = block.get("recomputed")
            target = recomputed or block
            if not (target.get("due") or target.get("ends_on") or (not recomputed and ob.due_date)):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "There is no deadline date to confirm. Set the date instead."
                )
            if recomputed:
                # Confirming after an anchor moved accepts the re-count: the old date
                # was counted from an anchor that no longer holds.
                block.update({k: recomputed.get(k) for k in ("due", "ends_on", "formula", "reason", "anchor")})
                ob.due_date = _as_date(block["due"])
            block.update(confirmed_by=current_user.id, confirmed_at=now, needs_reconfirm=False, recomputed=None)
        if block:
            meta["deadline"] = block
        meta["edited_by"] = current_user.id
        ob.metadata_json = meta

    def complete_obligation(self, *, ob: Obligation, current_user: User) -> dict:
        db = self.db
        successor = complete_and_schedule_next(db, ob=ob, actor_user_id=current_user.id)
        write_audit_log(
            db,
            action="obligation.completed",
            resource_type="obligation",
            resource_id=ob.id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            after={"contract_id": ob.contract_id, "next_obligation_id": successor.id if successor else None},
        )
        db.commit()
        db.refresh(ob)
        return self.serialize_one(ob)

    def trigger_extraction(self, *, contract_id: str, current_user: User) -> dict:
        db = self.db
        contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
        from app.contract_files.models import ContractTextSnapshot, ContractVersion

        version = (
            db.get(ContractVersion, contract.current_authoritative_version_id)
            if contract.current_authoritative_version_id
            else None
        )
        snapshot = (
            db.get(ContractTextSnapshot, version.text_snapshot_id)
            if version and version.text_snapshot_id
            else None
        )
        if version is None or snapshot is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Contract has no extractable authoritative version")
        job = create_job(
            db,
            org_id=current_user.org_id,
            job_type="obligation_extraction",
            resource_type="contract",
            resource_id=contract.id,
            created_by_user_id=current_user.id,
            idempotency_key=f"obligation_extraction:{version.id}:{snapshot.id}:manual",
            metadata={"contract_version_id": version.id, "text_snapshot_id": snapshot.id},
        )
        db.commit()
        job = db.get(JobRun, job.id)
        dispatch_job(db, job=job)
        db.commit()
        return {"job_id": job.id, "status": job.status}

    async def run_reminders(self, *, current_user: User, request_id: str | None) -> dict:
        db = self.db
        today = utcnow().date()
        # Recompute overdue / due-soon statuses for open obligations.
        open_obligations = db.scalars(
            select(Obligation).where(
                Obligation.org_id == current_user.org_id,
                Obligation.deleted_at.is_(None),
                Obligation.status.in_(["open", "due_soon", "overdue"]),
                Obligation.due_date.is_not(None),
            )
        ).all()
        overdue = due_soon = 0
        for ob in open_obligations:
            if ob.due_date < today:
                ob.status = "overdue"
                overdue += 1
            elif ob.due_date <= today + timedelta(days=DUE_SOON_DAYS):
                ob.status = "due_soon"
                due_soon += 1
            else:
                ob.status = "open"

        due_reminders = db.scalars(
            select(ObligationReminder).where(
                ObligationReminder.org_id == current_user.org_id,
                ObligationReminder.sent_at.is_(None),
                ObligationReminder.remind_at <= today,
            )
        ).all()
        sent = 0
        failed = 0
        for reminder in due_reminders:
            ob = db.get(Obligation, reminder.obligation_id)
            if ob is None or ob.deleted_at is not None or ob.status in {"completed", "cancelled"}:
                continue
            owner = db.get(User, ob.owner_user_id) if ob.owner_user_id else None
            if owner is not None:
                # Don't leak the raw obligation text (an extracted contract excerpt /
                # possible PII) into the email body. Send a minimal notice — type +
                # due date — and point the owner to the app to read the detail behind
                # the auth boundary.
                subject, html = reminder_email(ob)
                try:
                    await self.resend.send_email(to=owner.email, subject=subject, html=html)
                except Exception:
                    # A single failed send must not abort the whole reminder sweep or
                    # roll back the status recomputation above. Skip marking this one
                    # as sent so it is retried on the next run.
                    logger.exception(
                        "obligation reminder email failed",
                        extra={"obligation_id": ob.id, "reminder_id": reminder.id},
                    )
                    failed += 1
                    continue
            reminder.sent_at = today
            reminder.updated_by_user_id = current_user.id
            sent += 1
        write_audit_log(
            db,
            action="obligation.reminders_run",
            resource_type="org",
            resource_id=current_user.org_id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            request_id=request_id,
            after={
                "reminders_sent": sent,
                "reminders_failed": failed,
                "overdue": overdue,
                "due_soon": due_soon,
            },
        )
        db.commit()
        return {
            "reminders_sent": sent,
            "reminders_failed": failed,
            "marked_overdue": overdue,
            "marked_due_soon": due_soon,
        }
