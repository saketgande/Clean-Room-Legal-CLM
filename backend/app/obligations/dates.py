"""Does this obligation have a date, and if it has none, does anyone owe one?

Worked out when it is read, from what is already stored (the way Notices works out its posture): nothing is saved,
so it cannot go stale and needs no migration. Pure: no database, no clock, no network.

The states, checked in this order:

  dated          a due date (counted by code, confirmed, or typed by a person)
  not_read       no date and no deadline block at all: read before code counted dates, so its timing is unknown
  no_fixed_date  no date and none is expected: an ongoing duty (with or without an end date, and even when that end
                 cannot be counted yet: it only ever gets an end date, never a due date), an "if X happens" duty, or a
                 date a person chose to clear
  waiting_event  the contract gives a period but it runs from an event nobody has logged ("starts when ... - enter
                 the date"). Nothing is missing until the event happens
  needs_person   code could not count a date and a person must: period not stated, or not in the quoted clause, a
                 contract date missing or in dispute, a period too large to count, the retired AI date

"Needs a date" everywhere is needs_person + not_read. Waiting for an event and no fixed date are NOT alarms (nothing
is missing yet, or by the duty's nature): a queue that is mostly false alarms stops being read. Completed and
cancelled obligations are not counted.
"""

from __future__ import annotations

from collections import Counter

# The one piece of reason text the classifier reads: obligation_deadline (service.py) writes a reason starting with
# this when the clause counts from an event nobody has logged. Both places use this constant, and a test runs every
# way a deadline is computed through date_state, so rewording it cannot quietly turn waiting rows into alarms.
EVENT_WAIT = "starts when "

STATES = ("dated", "waiting_event", "needs_person", "no_fixed_date", "not_read")
OPEN_STATUSES = ("open", "due_soon", "overdue")


def date_state(ob) -> str:
    """The date state of one obligation (an ORM row or any object with `due_date` and `metadata_json`)."""
    if ob.due_date is not None:
        return "dated"
    block = (ob.metadata_json or {}).get("deadline") or {}
    reason = block.get("reason")
    if not block or (block.get("facts") is None and not reason and not block.get("set_by")):
        return "not_read"
    if block.get("set_by"):  # a person typed a date and then cleared it: they decided there is none
        return "no_fixed_date"
    # An ongoing duty ("confidentiality survives three years") never gets a due date, only an end date, so it can never
    # be a gap in a deadline, whatever stopped its end being counted (a contract date unset, the period read from the
    # law, an event not logged). Without this it joined "Needs a date" and the page told someone to type a due date
    # that would turn a standing duty into one that can go overdue.
    if (block.get("facts") or {}).get("kind") == "ongoing":
        return "no_fixed_date"
    if isinstance(reason, str) and reason.startswith(EVENT_WAIT):
        return "waiting_event"
    if reason:
        return "needs_person"
    return "no_fixed_date"


def date_counts(rows) -> dict:
    """How many OPEN obligations are in each state, and `needs_date` (needs_person + not_read)."""
    seen = Counter(date_state(r) for r in rows if r.status in OPEN_STATUSES)
    counts = {state: seen.get(state, 0) for state in STATES}
    counts["needs_date"] = counts["needs_person"] + counts["not_read"]
    return counts


def matches(ob, wanted: str) -> bool:
    """Whether an obligation belongs under a `date_state` filter: one state, or `needs_date`."""
    state = date_state(ob)
    return state in ("needs_person", "not_read") if wanted == "needs_date" else state == wanted


FILTERS = (*STATES, "needs_date")


def date_summary(counts: dict) -> str:
    """One plain line: "1 dated, 12 waiting for an event, 45 need a date, 53 no fixed date". Zeros are left out,
    "needs" for exactly one, and nothing at all gives an empty string."""
    needs = counts.get("needs_date", 0)
    parts = [
        (counts.get("dated", 0), "{n} dated"),
        (counts.get("waiting_event", 0), "{n} waiting for an event"),
        (needs, "{n} needs a date" if needs == 1 else "{n} need a date"),
        (counts.get("no_fixed_date", 0), "{n} no fixed date"),
    ]
    return ", ".join(text.format(n=n) for n, text in parts if n)
