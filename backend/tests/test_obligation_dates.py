"""An obligation's date state is worked out on read from what is stored (app/obligations/dates.py): dated, waiting for an
event, needs a person, no fixed date, or not read. "Needs a date" is the one alarm number; the others must not flood
it. Pure: every way the deadline is computed is run through the classifier."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

import app.models  # noqa: F401  (register every mapper)
from app.core.deadlines import Anchor
from app.obligations.dates import (
    EVENT_WAIT,
    FILTERS,
    date_counts,
    date_state,
    date_summary,
    matches,
)
from app.obligations.service import RETIRED_REASON, _as_date, obligation_deadline
from tests.test_obligation_deadlines import ANCHORS, _timing


def _row(block: dict | None, *, due=None, status="open") -> SimpleNamespace:
    return SimpleNamespace(due_date=due, status=status, metadata_json={"deadline": block} if block is not None else {})


def _computed(timing, quote, *, anchors=ANCHORS, event=None) -> SimpleNamespace:
    """An obligation as extraction stores it: the block code computed, and the date that block gave."""
    block = obligation_deadline(timing, anchors, quote=quote, event_date=event)
    return _row(block, due=_as_date(block["due"]))


MISSING = {**ANCHORS, "effective_date": Anchor("effective_date", None, None)}
DISPUTED = {**ANCHORS, "effective_date": Anchor("effective_date", None, "form", {"form": "2026-11-17", "text": "2026-10-15"})}
PERIOD = {"period_number": 30, "period_unit": "days", "anchor": "invoice_received", "anchor_text": "receipt of invoice"}


@pytest.mark.parametrize("ob, expected", [
    (_computed(_timing(period_number=12, period_unit="months", anchor="effective_date"),
               "within twelve (12) months of the Effective Date"), "dated"),
    (_computed(_timing(period_number=3, period_unit="months", anchor="effective_date", explicit_date=date(2027, 3, 31)),
               "deliver the report by 31 March 2027"), "dated"),
    (_computed(_timing(**PERIOD), "within 30 days of receipt of invoice", event=date(2026, 11, 2)), "dated"),
    # The clause counts from an event nobody has logged: nothing is missing until it happens.
    (_computed(_timing(**PERIOD), "within 30 days of receipt of invoice"), "waiting_event"),
    # No date by the duty's nature.
    (_computed(_timing(kind="ongoing", period_number=2, period_unit="years", anchor="expiration_date"),
               "for two (2) years after expiry"), "no_fixed_date"),
    (_computed(_timing(kind="ongoing"), "keep the information confidential"), "no_fixed_date"),
    (_computed(_timing(kind="condition", period_number=5, period_unit="days", anchor="effective_date"),
               "if audited, within 5 days"), "no_fixed_date"),
    # Code could not count a date and a person must.
    (_computed(_timing(period_number=60, period_unit="days", anchor="effective_date"),
               "pay all undisputed invoices promptly"), "needs_person"),
    (_computed(_timing(stated_in_text=False, period_number=3, period_unit="days", anchor="effective_date"),
               "within three days"), "needs_person"),
    (_computed(_timing(period_number=12, period_unit="months", anchor="effective_date"), "within a year",
               anchors=MISSING), "needs_person"),
    (_computed(_timing(period_number=12, period_unit="months", anchor="effective_date"),
               "within twelve (12) months of the Effective Date", anchors=DISPUTED), "needs_person"),
    (_computed({"kind": "deadline", "stated_in_text": True, "period_number": 999999999, "period_unit": "days",
                "anchor": "effective_date"}, None), "needs_person"),
])
def test_every_way_a_deadline_is_computed_lands_in_the_right_state(ob, expected):
    """Guards the two failure directions: a dateless-by-nature duty flooding the 'needs a date' queue until nobody
    trusts it, and a duty that really needs a person being hidden among the calm ones."""
    assert date_state(ob) == expected


@pytest.mark.parametrize("ob", [
    # Each is an ongoing duty whose END could not be counted, so obligation_deadline wrote a reason.
    _computed(_timing(kind="ongoing", period_number=3, period_unit="years", anchor="effective_date"),
              "survives three (3) years from the Effective Date", anchors=MISSING),
    _computed(_timing(kind="ongoing", period_number=3, period_unit="years", anchor="effective_date",
                      stated_in_text=False), "retain records as required by law"),
    _computed(_timing(kind="ongoing", period_number=3, period_unit="years", anchor="effective_date"),
              "survives for a period after the Effective Date"),
    _computed(_timing(kind="ongoing", period_number=3, period_unit="years", anchor="effective_date"),
              "survives three (3) years from the Effective Date", anchors=DISPUTED),
    _computed(_timing(kind="ongoing", period_number=3, period_unit="years", anchor="disclosure",
                      anchor_text="the date of disclosure"), "three (3) years from the date of disclosure"),
], ids=["contract date unset", "period from law", "period not in the clause", "contract date disputed", "event not logged"])
def test_an_ongoing_duty_is_never_a_gap_whatever_stopped_its_end_being_counted(ob):
    """Guards a standing duty ('confidentiality survives three years') joining 'Needs a date': it only ever gets an end
    date, so typing a due date, as the page would say, turns it into one that can go overdue."""
    assert ob.metadata_json["deadline"]["reason"], "the case must really carry a reason for this to mean anything"
    assert date_state(ob) == "no_fixed_date"


def test_a_deadline_duty_with_the_same_reasons_still_needs_a_person():
    """Guards the ongoing rule swallowing real gaps: the same missing contract date on a DEADLINE duty is an alarm."""
    ob = _computed(_timing(period_number=3, period_unit="years", anchor="effective_date"),
                   "within three (3) years of the Effective Date", anchors=MISSING)
    assert date_state(ob) == "needs_person"


def test_the_event_wait_text_the_classifier_reads_is_the_text_the_code_writes():
    """Guards the one string coupling: if the reason obligation_deadline writes were reworded without EVENT_WAIT, every
    waiting row would silently become 'needs a date'."""
    block = obligation_deadline(_timing(**PERIOD), ANCHORS, quote="within 30 days of receipt of invoice")
    assert block["reason"].startswith(EVENT_WAIT)


def test_the_retired_ai_date_needs_a_person():
    assert date_state(_row({"facts": None, "due": None, "reason": RETIRED_REASON})) == "needs_person"


def test_a_row_read_before_dates_were_counted_is_not_read_never_conditional():
    """Guards a pre-Step-3d row (no deadline block) passing as 'conditional - no fixed date': its timing is unknown, and
    it has no date. With a date it is simply dated."""
    assert date_state(_row(None)) == "not_read"
    assert date_state(_row({})) == "not_read"
    assert date_state(_row({"facts": None, "reason": None, "due": None})) == "not_read"
    assert date_state(_row(None, due=date(2026, 12, 1))) == "dated"


def test_a_date_a_person_cleared_is_no_fixed_date_never_a_gap():
    """Guards the page nagging for a date someone decided there is none of."""
    cleared = {"facts": {"kind": "deadline"}, "due": None, "reason": "set by a person", "set_by": "u-1"}
    assert date_state(_row(cleared)) == "no_fixed_date"
    assert date_state(_row(cleared, due=date(2026, 12, 1))) == "dated"


def test_only_open_obligations_are_counted_and_needs_a_date_is_needs_person_plus_not_read():
    rows = [
        _row(None), _row(None, status="due_soon"), _row({"reason": "period not stated in the contract - needs a person"}),
        _row({"reason": EVENT_WAIT + "the invoice is received - enter the date"}), _row({"facts": {}, "reason": None}),
        _row(None, due=date(2026, 12, 1), status="overdue"),
        _row(None, status="completed"), _row({"reason": "period not stated in the contract - needs a person"}, status="cancelled"),
    ]
    counts = date_counts(rows)
    assert counts == {"dated": 1, "waiting_event": 1, "needs_person": 1, "no_fixed_date": 1, "not_read": 2, "needs_date": 3}


@pytest.mark.parametrize("wanted, states", [
    ("needs_date", {"needs_person", "not_read"}), ("dated", {"dated"}), ("waiting_event", {"waiting_event"}),
    ("no_fixed_date", {"no_fixed_date"}), ("needs_person", {"needs_person"}), ("not_read", {"not_read"}),
])
def test_a_filter_matches_exactly_its_states(wanted, states):
    """Guards the queue (the filtered list) disagreeing with the tile that counts it."""
    blocks = {
        "dated": _row(None, due=date(2026, 12, 1)), "waiting_event": _row({"reason": EVENT_WAIT + "x - enter the date"}),
        "needs_person": _row({"reason": "period not stated in the contract - needs a person"}),
        "no_fixed_date": _row({"facts": {"kind": "ongoing"}, "reason": None}), "not_read": _row(None),
    }
    assert {name for name, ob in blocks.items() if matches(ob, wanted)} == states
    assert wanted in FILTERS


def test_the_line_leaves_out_zeros_and_says_needs_for_exactly_one():
    """Guards a sentence like '0 dated, 0 waiting' and '1 need a date'."""
    full = {"dated": 1, "waiting_event": 12, "needs_date": 45, "no_fixed_date": 53}
    assert date_summary(full) == "1 dated, 12 waiting for an event, 45 need a date, 53 no fixed date"
    assert date_summary({"dated": 0, "waiting_event": 0, "needs_date": 1, "no_fixed_date": 0}) == "1 needs a date"
    assert date_summary({}) == ""
