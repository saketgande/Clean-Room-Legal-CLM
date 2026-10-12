"""Dates in drafted contract text are written the way a contract writes them.

The form stores "2026-11-01"; the draft used to print that into the sentence ("entered into as of 2026-11-01",
"continues until 2027-09-30"). A contract says "1 November 2026".
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.deadlines import date_in_quote
from app.intake import drafting


@pytest.mark.parametrize("raw,expected", [
    ("2026-11-01", "1 November 2026"),
    ("2027-09-30", "30 September 2027"),
    (date(2026, 1, 5), "5 January 2026"),
    ("2026-11-01T00:00:00", "1 November 2026"),
])
def test_a_date_is_written_in_long_form(raw, expected):
    assert drafting._long_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [("", ""), (None, ""), ("next Tuesday", "next Tuesday"), ("Nov 1, 2026", "Nov 1, 2026")])
def test_anything_that_is_not_an_iso_date_is_left_as_written(raw, expected):
    assert drafting._long_date(raw) == expected


def test_the_nda_and_the_msa_both_print_long_dates():
    nda = drafting.render_document("nda", company="Us", counterparty="Them", effective="2026-11-01", fields={})
    assert "as of 1 November 2026" in nda
    assert "2026-11-01" not in nda
    msa = drafting.render_document("msa", company="Us", counterparty="Them", effective="2026-10-01",
                                   fields={"end_date": "2027-09-30"})
    assert "continues until 30 September 2027" in msa
    assert "2027-09-30" not in msa


def test_the_deadline_finder_still_recognises_a_long_date_as_the_stated_one():
    # The renewal and obligation readers accept a model's date only if the quoted wording itself writes it.
    assert date_in_quote(date(2027, 9, 30), "This Agreement continues until 30 September 2027.")
    assert date_in_quote(date(2026, 11, 1), "entered into as of 1 November 2026")
