"""Our own NDA must pass our own NDA playbook for every way the form can draft it (spec 014, FR-26 to FR-30).

Failure mode: a template or rule edit that makes a drafted NDA trip the playbook that reviews it, so
our own paper comes back as a redline. Every combination of choices is rendered and checked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.playbooks.library import NDA_STANDARD
from app.playbooks.service import evaluate_rules_against_text
from eval.template_matrix import combinations, render

RULES = [SimpleNamespace(id=r["clause_type"], **r) for r in NDA_STANDARD["rules"]]
COMBOS = list(combinations("nda"))


def test_the_matrix_covers_every_combination():
    """A dimension silently dropped from the matrix would let a variant go unchecked."""
    assert len(COMBOS) == 378


@pytest.mark.parametrize("combo", COMBOS, ids=[c[0] for c in COMBOS])
def test_every_drafted_nda_has_no_playbook_finding(combo):
    _, fields, ctx, company, counterparty = combo
    text = render("nda", fields, ctx, company=company, counterparty=counterparty)
    found = evaluate_rules_against_text(rules=RULES, text=text)
    assert [(d.clause_type, d.issue) for d in found] == []


def _text(**answers):
    base = next(c for c in COMBOS if c[0].startswith("nda|mutual|nda_term=3 years|governing_law=India|"))
    fields = {**base[1], **answers}
    return render("nda", fields, base[2], company=base[3], counterparty=base[4])


def test_default_nda_is_under_indian_law_with_courts_at_the_seat():
    """The old paper named the whole country's courts as 'courts of {law}'; it must name a city."""
    text = _text()
    assert "laws of India" in text and "courts at" in text and "Delaware" not in text


def test_delaware_appears_only_when_the_form_chose_it():
    assert "Delaware" in _text(governing_law="Delaware")


def test_signature_blocks_and_term_in_words():
    text = _text()
    assert "Signed for and on behalf of Aegis Pharma Ltd" in text
    assert "Signed for and on behalf of Globex Corporation" in text
    assert text.count("Address for notices:") >= 1 and "{" not in text
    assert "three (3) years" in text or "five (5) years" in text
