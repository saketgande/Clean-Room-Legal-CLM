"""The extraction agent's job is to save typing, never to be authoritative — a
statutory deadline it gets wrong is worse than one it declines to guess.

These pin the safety properties that hold regardless of what the LLM returns:
the date sanitiser (a malformed model response must never reach a date column),
and the deterministic fallback used under mock mode / API failure — which is
deliberately unwilling to invent a deadline it cannot literally see.
"""

from app.notices.extraction import _clean_date, _clean_str, _heuristic, clean_deadline

# --- date sanitising -------------------------------------------------------

def test_clean_date_accepts_iso():
    assert _clean_date("2026-07-28") == "2026-07-28"


def test_clean_date_trims_a_datetime_to_its_date():
    assert _clean_date("2026-07-28T00:00:00Z") == "2026-07-28"


def test_clean_date_rejects_prose_and_junk():
    # The model is asked for YYYY-MM-DD, but "within 21 days" or "unknown" must
    # never be handed on as if it were a date.
    for bad in ["within 21 days", "unknown", "28/07/2026", "", "   ", None, 20260728]:
        assert _clean_date(bad) is None


def test_clean_date_rejects_an_impossible_date():
    assert _clean_date("2026-02-30") is None


def test_clean_str_trims_and_caps():
    assert _clean_str("  Northwind Traders GmbH  ", 200) == "Northwind Traders GmbH"
    assert len(_clean_str("x" * 500, 120)) == 120
    assert _clean_str("   ", 200) is None
    assert _clean_str(None, 200) is None


# --- deterministic fallback ------------------------------------------------

def test_heuristic_never_guesses_a_deadline():
    """The property that matters most: the regex fallback must not resolve a
    relative period into a date. Guessing a statutory deadline from a pattern
    match is exactly the confident-but-wrong behaviour this must avoid."""
    result = _heuristic(
        "You are called upon to remedy the breach within 21 days of receipt hereof."
    )
    assert result["deadline"] is None
    assert "response_due_date" not in result


def test_heuristic_reads_a_literal_iso_date():
    result = _heuristic("Date: 2026-07-12\nNotice of termination of the agreement.")
    assert result["notice_date"] == "2026-07-12"


def test_heuristic_reads_a_day_first_date():
    result = _heuristic("Dated 28/07/2026 at New Delhi.")
    assert result["notice_date"] == "2026-07-28"


def test_heuristic_ignores_an_impossible_date():
    # 45/13/2026 matches the day-first shape but isn't a real date.
    assert _heuristic("Ref 45/13/2026")["notice_date"] is None


def test_heuristic_categorises_from_distinctive_phrasing():
    assert _heuristic("This is a CEASE AND DESIST notice.")["notice_type"] == "cease_and_desist"
    assert _heuristic("Notice of termination of the agreement")["notice_type"] == "termination"
    assert _heuristic("issued under Section 138 of the Act")["notice_type"] == "statutory"


def test_heuristic_reports_zero_confidence_when_it_finds_nothing():
    result = _heuristic("Dear Sir, please find attached our brochure.")
    assert result["notice_type"] is None
    assert result["notice_date"] is None
    assert result["confidence"] == 0.0


# --- deadline wording --------------------------------------------------------

def test_clean_deadline_keeps_valid_wording():
    raw = {"stated": True, "explicit_date": None, "period_count": 15, "period_unit": "days",
           "clear_days": True, "runs_from": "receipt", "runs_from_text": "of receipt hereof",
           "quote": " within 15 clear days "}
    assert clean_deadline(raw) == {**raw, "quote": "within 15 clear days"}


def test_clear_days_is_only_ever_a_real_true():
    """A missing or non-boolean clear_days must read as plain days, not 'clear'."""
    base = {"stated": True, "period_count": 15, "period_unit": "days", "quote": "within 15 days"}
    assert clean_deadline(base)["clear_days"] is False
    assert clean_deadline({**base, "clear_days": "yes"})["clear_days"] is False


def test_an_explicit_date_not_written_in_the_notice_is_dropped():
    """A date the model counted itself (here from '30 days') was taken as the
    notice's own stated deadline; it must be dropped so the period is counted."""
    raw = {"stated": True, "explicit_date": "2026-11-05", "period_count": 30, "period_unit": "days",
           "runs_from": "receipt", "quote": "pay within 30 days of receipt of this notice"}
    out = clean_deadline(raw, text="Dated 6 October 2026. Pay within 30 days of receipt of this notice.")
    assert out["explicit_date"] is None and out["period_count"] == 30


def test_an_explicit_date_counts_only_when_its_quote_writes_it():
    """A date the notice writes out survives; one found only somewhere else in the notice
    does not: almost any notice has the year and some matching day number, so checking the
    whole text let a date the model counted itself pass as "stated in notice"."""
    raw = {"stated": True, "explicit_date": "2026-11-05", "quote": "reply on or before 05/11/2026"}
    assert clean_deadline(raw)["explicit_date"] == "2026-11-05"
    elsewhere = {**raw, "quote": "reply by the date given above"}
    assert clean_deadline(elsewhere, text="Deadline: 5th November, 2026.")["explicit_date"] is None


def test_not_stated_drops_any_stray_period():
    """A period left beside stated=false would be counted into a deadline the
    notice never gave."""
    out = clean_deadline({"stated": False, "period_count": 30, "period_unit": "days", "runs_from": "receipt"})
    assert out["stated"] is False
    assert out["period_count"] is None and out["runs_from"] is None


def test_clean_deadline_rejects_out_of_vocabulary_values():
    """An unknown unit/anchor or a non-integer count must not reach the date
    arithmetic; it becomes 'missing', which computes no date."""
    out = clean_deadline({"stated": True, "period_count": True, "period_unit": "fortnights",
                          "runs_from": "dispatch", "explicit_date": "within 15 days"})
    assert (out["period_count"], out["period_unit"], out["runs_from"], out["explicit_date"]) == (
        None, None, None, None)


def test_clean_deadline_without_a_reading_is_none():
    """Model output with no deadline object is 'no reading', not 'not stated'."""
    assert clean_deadline(None) is None
    assert clean_deadline("15 days") is None
