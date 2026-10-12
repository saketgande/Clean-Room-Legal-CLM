"""Contract date arithmetic: a period counted from an anchor date.

The AI copies a deadline's WORDING (15 days, after receipt, clear days); this
module turns that wording into a date and a formula a person can check. Pure
stdlib, so every awkward case is tested directly (tests/test_deadlines.py).

Counting rules, decided by the owner:

* "within N days of X" is X + N: the day of X is not counted (General Clauses
  Act s.9 style). "N days before Y" is Y - N.
* A deadline that lands on a weekend keeps its date. The formula prints the
  weekday so a person sees a Saturday and acts on the Friday; we never apply
  s.10's move to the next working day, because that only ever makes a date later.
* "Clear days" take one more day toward the safe side, which for a deadline is
  always earlier.
* Months and years clamp to the month end (31 Jan + 1 month = 28/29 Feb), which
  also errs earlier. A recurrence's k-th date is k periods from the FIRST anchor
  (`nth`), never chained from the previous one, so a 31st doesn't drift to the 28th.
* Business days are Mon-Fri. There is no holiday calendar (the same decision
  as intake/business_time.py), so a holiday never pushes a date later and the
  formula asks a person to confirm.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Unit = Literal["days", "business_days", "weeks", "months", "years"]
Direction = Literal["after", "before"]

HOLIDAYS_NOTE = "public holidays not counted - please confirm"


@dataclass(frozen=True)
class Period:
    count: int
    unit: Unit


def add_months(day: date, months: int) -> date:
    """`months` calendar months from `day` (negative goes back), clamped to the
    last day of the target month: 31 Jan + 1 month is 28 Feb (29 in a leap year)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _step_business_days(day: date, count: int, step: int) -> date:
    # The anchor day itself is never counted, so Sat + 1 business day is Mon.
    while count > 0:
        day += timedelta(days=step)
        if day.weekday() < 5:
            count -= 1
    return day


def shift(anchor: date, period: Period, direction: Direction = "after", *, clear_days: bool = False) -> date:
    """The date `period` after (or before) `anchor`, anchor day not counted."""
    if period.count < 0:
        raise ValueError("a period is never negative; use direction='before'")
    sign = 1 if direction == "after" else -1
    if period.unit == "days":
        due = anchor + timedelta(days=sign * period.count)
    elif period.unit == "weeks":
        due = anchor + timedelta(weeks=sign * period.count)
    elif period.unit == "months":
        due = add_months(anchor, sign * period.count)
    elif period.unit == "years":
        due = add_months(anchor, sign * 12 * period.count)
    elif period.unit == "business_days":
        due = _step_business_days(anchor, period.count, sign)
    else:
        raise ValueError(f"unknown period unit {period.unit!r}")
    if clear_days:
        # Earlier is the safe side in both directions: "within 10 clear days of X"
        # leaves a day less to act, "30 clear days before Y" means acting a day sooner.
        due = _step_business_days(due, 1, -1) if period.unit == "business_days" else due - timedelta(days=1)
    return due


def nth(anchor: date, period: Period, k: int) -> date:
    """The k-th recurrence: k periods after the FIRST anchor, clamped to the month
    end. Monthly from 31 Jan gives 28 Feb, 31 Mar, 30 Apr — chaining from the
    previous date would give 28 Feb, 28 Mar, 28 Apr."""
    return shift(anchor, Period(period.count * k, period.unit))


def _fmt(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b %Y}"


def date_in_quote(day: date, quote: str | None) -> bool:
    """Whether the cited wording itself writes `day`: its day number (an optional
    leading zero, any suffix: "05", "5th") and its four-digit year. A date the
    model worked out isn't in the quote, so it is dropped and the period path or
    "needs a person" applies.

    ponytail: checks the day and year, not the month; a quote holding both a
    "5" and a "2026" elsewhere passes. Parse the date out of the quote if that
    ever matters."""
    text = quote or ""
    return bool(re.search(rf"(?<!\d)0?{day.day}(?!\d)", text)) and bool(
        re.search(rf"(?<!\d){day.year}(?!\d)", text))


_UNIT_WORDS = {
    "days": ("day", "days"),
    "business_days": ("business day", "business days"),
    "weeks": ("week", "weeks"),
    "months": ("month", "months"),
    "years": ("year", "years"),
}


def explain(
    due: date,
    *,
    anchor_label: str,
    anchor: date,
    period: Period,
    direction: str,
    clear_days: bool = False,
) -> str:
    """The formula shown next to a computed date, e.g.
    "due Wed 21 Oct 2026 = received Tue 6 Oct 2026 + 15 days". `anchor_label`
    is a fixed word code picks from the anchor kind, never the model's text."""
    singular, plural = _UNIT_WORDS[period.unit]
    unit = singular if period.count == 1 else plural
    if clear_days:
        unit = f"clear {unit}"
    sign = "+" if direction == "after" else "-"
    text = f"due {_fmt(due)} = {anchor_label} {_fmt(anchor)} {sign} {period.count} {unit}"
    if period.unit == "business_days":
        text += f" ({HOLIDAYS_NOTE})"
    return text


# --- the contract's anchor dates ---------------------------------------------

ANCHOR_FIELDS = ("effective_date", "expiration_date")
# Where an expiry may come from to count deadlines from it. "ai" is the metadata
# model's own arithmetic ("two years from 15 Oct" -> a date): counting a notice
# deadline from that would be counting from a date nobody wrote or checked. No
# source recorded means it predates field_sources, and every AI write records "ai".
EXPIRY_SOURCES = {"user", "form", "computed", "change_request", "signature", None}
AI_EXPIRY_REASON = "expiry only from the AI reading - needs a person or the renewal reading"


@dataclass
class Anchor:
    name: str
    value: date | None
    # user / form / ai / computed / change_request (contract field_sources) or "signature"
    source: str | None
    conflict: dict | None = None  # {"form": iso, "text": iso} when they disagree
    reason: str | None = None  # why a date the contract holds is not used


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def contract_anchors(db, contract) -> dict[str, Anchor]:
    """The dates deadlines are counted from, with where each came from.

    When the request form and the contract text (the AI metadata read) disagree
    on an effective/expiry date and no person set it, neither is trusted: the
    anchor has no value and carries both, so the page says "needs a person"
    instead of silently counting from the form (Globex: form 17 Nov, text 15 Oct)."""
    from sqlalchemy import func, select

    from app.core.enums import SignatureStatus
    from app.signatures.models import SignatureRequest

    meta = contract.metadata_json or {}
    sources = meta.get("field_sources") or {}
    text_values = (meta.get("ai_suggestions") or {}).get("metadata") or {}
    anchors: dict[str, Anchor] = {}
    for name in ANCHOR_FIELDS:
        value = getattr(contract, name)
        source = sources.get(name)
        form_iso, text_iso = _iso(value), _iso(text_values.get(name))
        if source == "form" and text_iso and form_iso != text_iso:
            anchors[name] = Anchor(name, None, source, {"form": form_iso, "text": text_iso})
        elif name == "expiration_date" and value is not None and source not in EXPIRY_SOURCES:
            anchors[name] = Anchor(name, None, source, reason=AI_EXPIRY_REASON)
        else:
            anchors[name] = Anchor(name, value, source if value is not None else None)
    signed_at = db.scalar(
        select(func.max(SignatureRequest.completed_at)).where(
            SignatureRequest.org_id == contract.org_id,
            SignatureRequest.contract_id == contract.id,
            SignatureRequest.status == SignatureStatus.COMPLETED,
        )
    )
    anchors["signature_date"] = Anchor(
        "signature_date", signed_at.date() if signed_at else None, "signature" if signed_at else None
    )
    return anchors


def recompute_contract_deadlines(db, contract) -> None:
    """Re-derive every code-computed deadline on a contract after an anchor date
    changed. Runs inside the caller's transaction and never commits."""
    from app.obligations.service import recompute_due_dates
    from app.renewals.service import recompute_renewal_dates

    recompute_due_dates(db, contract)
    recompute_renewal_dates(db, contract)
