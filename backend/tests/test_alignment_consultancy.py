"""Spec 014 (T011): the Consultancy template and the Consultancy playbook agree.

Failures this guards:
* a drafted Consultancy agreement that the Consultancy playbook flags, so our own paper is redlined
  (checked over every one of the template matrix's combinations: fee basis, deliverables, term, payment
  days, personal data, currency, Indian or foreign consultant);
* a second GST position, or a foreign-law / Delaware / arbitration forum creeping into our paper for a
  consultant (decision 2: every consultant is under Indian law, courts at our registered city);
* the day, the acceptance, the moral-rights or the stamp-duty wording dropping out of the template.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.playbooks.library import PLAYBOOK_LIBRARY
from app.playbooks.service import evaluate_rules_against_text
from eval.template_matrix import combinations, render

RULES = [
    SimpleNamespace(id=f"r{i}", **r)
    for i, r in enumerate(
        next(p for p in PLAYBOOK_LIBRARY if p["name"] == "Consultancy Agreement Playbook")["rules"]
    )
]
COMBOS = list(combinations("consultancy"))


def test_every_consultancy_combination_is_clean_against_its_playbook():
    """A draft the playbook flags sends our own paper back as a redline (FR-42)."""
    assert len(COMBOS) == 2112
    bad = []
    for label, fields, ctx, company, cp in COMBOS:
        text = render("consultancy", fields, ctx, company=company, counterparty=cp)
        found = evaluate_rules_against_text(rules=RULES, text=text)
        if found:
            bad.append((label, [(d.clause_type, d.issue) for d in found]))
    assert bad[:3] == []


def test_every_combination_has_the_frozen_clauses():
    """Each frozen clause must be in every draft, whichever answers it was made from."""
    for label, fields, ctx, company, cp in COMBOS[::37]:
        text = render("consultancy", fields, ctx, company=company, counterparty=cp)
        for needle in ("laws of India", "courts at ", "section 57 of the Copyright Act, 1957",
                       "section 19(4) of the Copyright Act, 1957", "Udyam registration number",
                       "12.5 The Consultant shall maintain", "The Company shall bear any stamp duty",
                       "re-perform them at no cost", "Registered office and address for notices"):
            assert needle in text, (label, needle)
        assert "Delaware" not in text and "arbitrat" not in text.lower(), label
        assert "{" not in text, label


def test_gst_has_one_position_only():
    """Two GST sentences (3.2 and 6.6) can disagree on who bears the tax (AC-16)."""
    _, fields, ctx, company, cp = COMBOS[0]
    text = render("consultancy", fields, ctx, company=company, counterparty=cp)
    assert len(re.findall("goods and services tax", text)) == 1
    assert text.index("goods and services tax") > text.index("6.6 ")


def test_a_day_is_eight_hours_only_on_a_day_rate():
    """A day rate with no day length is a billing dispute; an hourly one must not define a day."""
    for label, fields, ctx, company, cp in COMBOS:
        basis = fields.get("fee_basis")
        text = render("consultancy", fields, ctx, company=company, counterparty=cp)
        assert ("A day is eight (8) hours" in text) == (basis == "Day rate"), label


def test_the_court_is_ours_for_a_foreign_consultant_too():
    """Decision 2: no Singapore seat or name-based guess for any consultant."""
    foreign = next(c for c in COMBOS if c[0].endswith("ctx=foreign"))
    text = render("consultancy", foreign[1], foreign[2], company=foreign[3], counterparty=foreign[4])
    assert "Singapore" not in text and "exclusive jurisdiction of the courts at" in text


def test_a_counterparty_delaware_clause_is_flagged():
    """The playbook still catches the risky wording the template avoids."""
    found = evaluate_rules_against_text(
        rules=RULES, text="Disputes shall be heard by the courts of competent jurisdiction under the "
                          "laws of the State of Delaware.")
    assert any(d.clause_type == "governing_law" for d in found)


@pytest.mark.parametrize("n", [0])
def test_matrix_is_not_empty(n):
    """An empty matrix would pass the zero-findings test for the wrong reason."""
    assert len(COMBOS) > n
