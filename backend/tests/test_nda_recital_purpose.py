"""The NDA's first recital reads as a sentence whatever the purpose was typed as (round 2, bug 49).

The template says "The Parties wish to {purpose_phrase} (the "Purpose")." and the form's purpose is a free-text
answer to "What is this agreement for?", so "Managed hosting services." came out as
"The Parties wish to Managed hosting services.".
"""

from __future__ import annotations

import pytest

from app.intake.drafting import render_document


def recital(**fields):
    text = render_document("nda", company="Us Ltd", counterparty="Them Ltd", effective="1 October 2026", fields=fields)
    return next(line for line in text.splitlines() if line.startswith("A. "))


def test_a_purpose_typed_as_a_sentence_is_introduced_not_spliced_into_the_sentence():
    assert recital(purpose="Managed hosting services for the data platform.") == (
        'A. The Parties wish to exchange confidential information for the following purpose: '
        'Managed hosting services for the data platform (the "Purpose").'
    )


def test_no_purpose_keeps_the_ordinary_wording():
    assert recital() == 'A. The Parties wish to explore a potential business relationship (the "Purpose").'


@pytest.mark.parametrize("typed", ["", "   ", ".", " ;. "])
def test_a_purpose_with_nothing_in_it_is_treated_as_no_purpose(typed):
    assert recital(purpose=typed) == 'A. The Parties wish to explore a potential business relationship (the "Purpose").'


def test_a_purpose_typed_over_several_lines_stays_on_one_line():
    line = recital(purpose="Pilot of the new scanner,\n\n  phase 2 only.\n")
    assert line.endswith('purpose: Pilot of the new scanner, phase 2 only (the "Purpose").')


def test_the_recital_is_still_a_single_paragraph_after_it():
    text = render_document("nda", company="Us Ltd", counterparty="Them Ltd", effective="x", fields={"purpose": "A\nB"})
    lines = text.splitlines()
    assert lines[lines.index(next(line for line in lines if line.startswith("A. "))) + 1] == ""
