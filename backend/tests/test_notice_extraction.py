"""The extraction agent's job is to save typing, never to be authoritative — a
statutory deadline it gets wrong is worse than one it declines to guess.

These pin the safety properties that hold regardless of what the LLM returns:
the date sanitiser (a malformed model response must never reach a date column),
and the deterministic fallback used under mock mode / API failure — which is
deliberately unwilling to invent a deadline it cannot literally see.
"""

from app.notices.extraction import _clean_date, _clean_str, _heuristic


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
    assert result["response_due_date"] is None


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
