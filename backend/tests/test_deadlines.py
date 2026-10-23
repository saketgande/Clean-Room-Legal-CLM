"""The date engine every deadline is counted with (app/core/deadlines.py). A wrong
day here is diarised as a legal deadline, so each case names the slip it guards."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.schemas import ObligationTiming, RenewalExtractionOutput, RenewalNoticeTerm, TermLength
from app.core.deadlines import (
    AI_EXPIRY_REASON,
    Anchor,
    Period,
    contract_anchors,
    date_in_quote,
    explain,
    nth,
    shift,
)
from app.obligations.service import obligation_deadline
from app.renewals.service import renewal_dates


@pytest.mark.parametrize("anchor, count, unit, direction, clear, expected, guards", [
    (date(2026, 10, 6), 15, "days", "after", False, date(2026, 10, 21), "anchor day counted (s.9)"),
    (date(2026, 10, 6), 0, "days", "after", False, date(2026, 10, 6), "zero period moves the date"),
    (date(2026, 10, 6), 2, "weeks", "after", False, date(2026, 10, 20), "weeks not 7 days each"),
    (date(2027, 1, 31), 1, "months", "after", False, date(2027, 2, 28), "month end overflows into March"),
    (date(2028, 1, 31), 1, "months", "after", False, date(2028, 2, 29), "leap February missed"),
    (date(2027, 3, 31), 1, "months", "before", False, date(2027, 2, 28), "month end going back"),
    (date(2026, 11, 15), 3, "months", "after", False, date(2027, 2, 15), "year rollover"),
    (date(2028, 2, 29), 1, "years", "after", False, date(2029, 2, 28), "29 Feb into a common year"),
    (date(2028, 2, 29), 4, "years", "after", False, date(2032, 2, 29), "29 Feb into the next leap year"),
    (date(2026, 10, 15), 1, "years", "after", False, date(2027, 10, 15), "one-year term is the anniversary"),
    (date(2026, 10, 9), 1, "business_days", "after", False, date(2026, 10, 12), "Fri + 1 business day lands on Sat"),
    (date(2026, 10, 10), 1, "business_days", "after", False, date(2026, 10, 12), "Sat anchor counts the Sat"),
    (date(2026, 10, 9), 1, "business_days", "before", False, date(2026, 10, 8), "business days going back"),
    (date(2026, 10, 7), 5, "business_days", "after", False, date(2026, 10, 14), "weekend counted as working"),
    (date(2027, 11, 30), 90, "days", "before", False, date(2027, 9, 1), "notice before expiry"),
    (date(2028, 9, 28), 30, "days", "before", False, date(2028, 8, 29), "30 days before expiry"),
    (date(2028, 9, 28), 30, "days", "before", True, date(2028, 8, 28), "clear days not taken earlier"),
    (date(2026, 10, 6), 10, "days", "after", True, date(2026, 10, 15), "clear days pushing a deadline later"),
    (date(2026, 10, 9), 1, "business_days", "after", True, date(2026, 10, 9), "clear business day landing on a Sunday"),
])
def test_shift_counts_a_period_from_its_anchor(anchor, count, unit, direction, clear, expected, guards):
    """Guards the slip named in each row's `guards` column."""
    assert shift(anchor, Period(count, unit), direction, clear_days=clear) == expected, guards


def test_a_weekend_deadline_is_never_moved_later():
    """Guards s.10 creeping in: a deadline on a Saturday stays on the Saturday."""
    due = shift(date(2026, 10, 2), Period(15, "days"))
    assert due == date(2026, 10, 17) and due.weekday() == 5


def test_monthly_recurrence_is_counted_from_the_first_date_without_drift():
    """Guards the chained month-end clamp: 31 Jan monthly must reach 31 Mar and
    30 Apr, not get stuck on the 28th after February."""
    monthly = Period(1, "months")
    assert [nth(date(2027, 1, 31), monthly, k) for k in range(1, 4)] == [
        date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30),
    ]


def test_a_negative_period_is_refused():
    """Guards a sign error silently flipping a deadline to the other side of its anchor."""
    with pytest.raises(ValueError):
        shift(date(2026, 1, 1), Period(-1, "days"))


@pytest.mark.parametrize("due, anchor, period, direction, clear, label, expected", [
    (date(2026, 10, 21), date(2026, 10, 6), Period(15, "days"), "after", False, "received",
     "due Wed 21 Oct 2026 = received Tue 6 Oct 2026 + 15 days"),
    (date(2028, 8, 28), date(2028, 9, 28), Period(30, "days"), "before", True, "expiry",
     "due Mon 28 Aug 2028 = expiry Thu 28 Sep 2028 - 30 clear days"),
    (date(2027, 10, 15), date(2026, 10, 15), Period(1, "years"), "after", False, "effective date",
     "due Fri 15 Oct 2027 = effective date Thu 15 Oct 2026 + 1 year"),
    (date(2026, 10, 14), date(2026, 10, 7), Period(5, "business_days"), "after", False, "received",
     ("due Wed 14 Oct 2026 = received Wed 7 Oct 2026 + 5 business days"
      " (public holidays not counted - please confirm)")),
])
def test_explain_shows_the_weekday_and_the_formula(due, anchor, period, direction, clear, label, expected):
    """Guards a formula a person can't check: the weekday must show (so a weekend
    deadline is visible) and business days must carry the holidays warning."""
    assert explain(due, anchor_label=label, anchor=anchor, period=period,
                   direction=direction, clear_days=clear) == expected


class _DB:
    def __init__(self, signed_at=None):
        self.signed_at = signed_at

    def scalar(self, _stmt):
        return self.signed_at


def _contract(meta, **dates):
    return SimpleNamespace(id="c-1", org_id="org-1", metadata_json=meta,
                           effective_date=dates.get("effective_date"),
                           expiration_date=dates.get("expiration_date"))


def test_form_and_text_disagreeing_leaves_the_anchor_for_a_person():
    """Guards the form silently winning: Globex's form says 17 Nov, its text 15 Oct,
    and no person chose. Neither may be counted from; both are shown."""
    contract = _contract(
        {"field_sources": {"effective_date": "form", "expiration_date": "form"},
         "ai_suggestions": {"metadata": {"effective_date": "2026-10-15", "expiration_date": "2027-10-15"}}},
        effective_date=date(2026, 11, 17), expiration_date=date(2027, 10, 15),
    )
    anchors = contract_anchors(_DB(), contract)
    eff = anchors["effective_date"]
    assert (eff.value, eff.conflict) == (None, {"form": "2026-11-17", "text": "2026-10-15"})
    exp = anchors["expiration_date"]  # the two agree: usable as is
    assert (exp.value, exp.source, exp.conflict) == (date(2027, 10, 15), "form", None)
    assert anchors["signature_date"].value is None


def test_a_person_set_anchor_beats_a_disagreeing_text():
    """Guards a person's date being overruled by the AI's reading of the text."""
    contract = _contract(
        {"field_sources": {"effective_date": "user"},
         "ai_suggestions": {"metadata": {"effective_date": "2026-10-15"}}},
        effective_date=date(2026, 11, 17),
    )
    anchors = contract_anchors(_DB(datetime(2026, 11, 20, 23, 30, tzinfo=UTC)), contract)
    assert (anchors["effective_date"].value, anchors["effective_date"].source) == (date(2026, 11, 17), "user")
    assert (anchors["signature_date"].value, anchors["signature_date"].source) == (date(2026, 11, 20), "signature")


def _anchors(effective=None, expiry=None, expiry_source=None, effective_conflict=None):
    return {
        "effective_date": Anchor("effective_date", effective, "ai" if effective else None, effective_conflict),
        "expiration_date": Anchor("expiration_date", expiry, expiry_source, None),
        "signature_date": Anchor("signature_date", None, None, None),
    }


def test_an_obligation_deadline_is_counted_from_a_contract_date_with_its_formula():
    """Guards the AI's date being stored instead of code's: the due date and the
    formula come from the typed wording and the contract's effective date."""
    timing = ObligationTiming(kind="deadline", period_number=12, period_unit="months",
                              anchor="effective_date", stated_in_text=True)
    block = obligation_deadline(timing, _anchors(effective=date(2026, 10, 15)))
    assert block["due"] == "2027-10-15"
    assert block["formula"] == "due Fri 15 Oct 2027 = effective date Thu 15 Oct 2026 + 12 months"


@pytest.mark.parametrize("timing, anchors, reason", [
    (ObligationTiming(kind="deadline", anchor="effective_date", stated_in_text=False,
                      anchor_text="statutory period"), _anchors(effective=date(2026, 1, 1)),
     "period not stated in the contract - needs a person"),
    (ObligationTiming(kind="deadline", period_number=30, period_unit="days", anchor="invoice_received",
                      anchor_text="receipt of a valid invoice", stated_in_text=True), _anchors(),
     "starts when receipt of a valid invoice - enter the date"),
    (ObligationTiming(kind="deadline", period_number=30, period_unit="days", anchor="effective_date",
                      stated_in_text=True), _anchors(effective_conflict={"form": "a", "text": "b"}),
     "effective date differs between the form and the contract text - needs a person"),
    (ObligationTiming(kind="deadline", period_number=30, period_unit="days", anchor="signature_date",
                      stated_in_text=True), _anchors(), "signature date not set on the contract"),
    (ObligationTiming(kind="ongoing", period_number=2, period_unit="years", anchor="expiration_date",
                      stated_in_text=True),
     {**_anchors(), "expiration_date": Anchor("expiration_date", None, "ai", reason=AI_EXPIRY_REASON)},
     AI_EXPIRY_REASON),
])
def test_an_obligation_deadline_without_a_usable_anchor_or_period_gets_no_date(timing, anchors, reason):
    """Guards a date computed from law, from an event nobody logged, or from a
    disputed anchor: each must wait for a person, with the reason shown."""
    block = obligation_deadline(timing, anchors)
    assert (block["due"], block["reason"]) == (None, reason)


@pytest.mark.parametrize("source, expected", [
    ("ai", None),
    ("computed", date(2027, 10, 15)),
    ("change_request", date(2027, 10, 15)),
    ("user", date(2027, 10, 15)),
    ("form", date(2027, 10, 15)),
    (None, date(2027, 10, 15)),
])
def test_only_a_decided_expiry_is_an_anchor(source, expected):
    """Guards counting notice and post-term deadlines from the metadata model's own
    arithmetic ("two years from 15 Oct" -> a date nobody wrote): an "ai" expiry is
    no anchor and says why; code's count, a change request, a person and the form are."""
    meta = {"field_sources": {"expiration_date": source}} if source else {}
    expiry = contract_anchors(_DB(), _contract(meta, expiration_date=date(2027, 10, 15)))["expiration_date"]
    assert (expiry.value, expiry.reason) == (expected, None if expected else AI_EXPIRY_REASON)


@pytest.mark.parametrize("day, quote, expected", [
    (date(2027, 3, 31), "shall deliver the report by 31 March 2027", True),
    (date(2027, 3, 5), "on or before 05/03/2027", True),
    (date(2027, 3, 5), "no later than the 5th day of March, 2027", True),
    (date(2027, 3, 31), "by 31 March of each year", False),
    (date(2027, 3, 1), "within 30 days of 11 March 2027", False),
    (date(2027, 3, 31), None, False),
])
def test_an_explicit_date_must_be_written_in_the_quote(day, quote, expected):
    """Guards a date the model worked out being passed off as one the contract
    states: its day number and year must both be in the cited wording."""
    assert date_in_quote(day, quote) is expected


@pytest.mark.parametrize("count, unit", [(99_999_999, "years"), (10**10, "days")])
def test_an_absurd_period_waits_for_a_person_instead_of_crashing(count, unit):
    """Guards a misread number (stored before the schema cap) raising out of date
    arithmetic and failing the whole extraction or anchor recount."""
    facts = {"kind": "deadline", "period_number": count, "period_unit": unit,
             "anchor": "effective_date", "stated_in_text": True}
    block = obligation_deadline(facts, _anchors(effective=date(2026, 1, 1)))
    assert (block["due"], block["reason"]) == (None, "period too large to count - needs a person")


def test_the_schemas_refuse_an_absurd_period():
    """Guards an overflowing number reaching date arithmetic at all: the extraction
    schemas cap every period (and the tool schema shows the model the cap)."""
    with pytest.raises(ValidationError):
        ObligationTiming(period_number=99_999)
    with pytest.raises(ValidationError):
        TermLength(number=99_999, unit="years")
    with pytest.raises(ValidationError):
        RenewalNoticeTerm(kind="non_renewal", number=99_999, unit="days")


def test_renewal_tracks_the_earliest_non_renewal_deadline():
    """Guards keeping only one of several notice terms (Hooli: either party 90
    days, the customer 60 days): the earliest deadline is the one to diarise."""
    facts = RenewalExtractionOutput.model_validate({
        "term_kind": "fixed", "auto_renews": True,
        "initial_term": {"number": 1, "unit": "years", "counted_from": "effective_date"},
        "citations": [{"quote": "an initial term of one (1) year from the Effective Date"}],
        "notice_terms": [
            {"kind": "non_renewal", "number": 60, "unit": "days", "quote": "at least sixty (60) days"},
            {"kind": "non_renewal", "number": 90, "unit": "days", "quote": "ninety (90) days"},
            {"kind": "termination_for_convenience", "number": 120, "unit": "days", "quote": "120 days"},
        ],
    })
    block = renewal_dates(facts, _anchors(effective=date(2026, 11, 30)))
    assert (block["expiration_date"], block["notice_date"]) == ("2027-11-30", "2027-09-01")
    assert block["formulas"]["notice_date"] == "notice by Wed 1 Sep 2027 = expiry Tue 30 Nov 2027 - 90 days"


def test_renewal_with_a_form_end_date_on_an_evergreen_text_needs_a_person():
    """Guards the form's end date silently winning over text that says the
    contract continues until terminated (MSA 5d255b21)."""
    facts = RenewalExtractionOutput(term_kind="evergreen")
    block = renewal_dates(facts, _anchors(expiry=date(2027, 9, 30), expiry_source="form"))
    assert block["expiration_date"] is None
    assert block["conflict"] == {"form": "2027-09-30", "text": "continues until terminated"}


def test_a_non_renewing_contract_gets_no_notice_deadline_from_its_termination_notice():
    """Guards the old bug where an early-termination notice period built a renewal
    window on NDAs that never renew."""
    facts = RenewalExtractionOutput.model_validate({
        "term_kind": "fixed", "auto_renews": False,
        "initial_term": {"number": 2, "unit": "years", "counted_from": "effective_date"},
        "citations": [{"quote": "for a period of two (2) years from the Effective Date"}],
        "notice_terms": [{"kind": "termination_for_convenience", "number": 30, "unit": "days"}],
    })
    block = renewal_dates(facts, _anchors(effective=date(2026, 9, 28)))
    assert (block["expiration_date"], block["notice_date"]) == ("2028-09-28", None)


def test_re_extraction_never_overwrites_a_confirmed_renewal_date(monkeypatch):
    """Guards a re-run (or a carried-forward replay) silently undoing a person's
    confirmation: the confirmed dates stay and the row is flagged needs_reconfirm."""
    from app.renewals import service as renewals_service
    from app.renewals.models import RenewalEvent

    monkeypatch.setattr(renewals_service, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(renewals_service, "write_timeline_event", lambda db, **kw: None)
    event = RenewalEvent(org_id="org-1", contract_id="c-1", expiration_date=date(2027, 12, 31),
                         notice_date=date(2027, 10, 1), renewal_window_starts_at=date(2027, 10, 1),
                         metadata_json={"dates": {"expiration_date": "2027-12-31", "notice_date": "2027-10-01",
                                                  "confirmed_by": "u-1", "confirmed_at": "2026-10-01"}})

    class DB:
        def scalar(self, stmt):
            return event if "renewal_event" in str(stmt) else None

        def flush(self):
            pass

    contract = _contract({}, effective_date=date(2026, 11, 30))
    contract.owner_user_id = None
    output = {"term_kind": "fixed", "auto_renews": True,
              "initial_term": {"number": 1, "unit": "years", "counted_from": "effective_date"},
              "notice_terms": [{"kind": "non_renewal", "number": 90, "unit": "days"}]}
    renewals_service.apply_extracted_renewal(
        DB(), contract=contract, version=None, output=output,
        skill_run=SimpleNamespace(id="run-1"), created_by_user_id=None, request_id=None)
    assert (event.expiration_date, event.notice_date) == (date(2027, 12, 31), date(2027, 10, 1))
    dates = event.metadata_json["dates"]
    assert (dates["confirmed_by"], dates["needs_reconfirm"], dates["expiration_date"]) == ("u-1", True, "2027-12-31")
