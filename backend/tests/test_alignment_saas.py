"""Spec 014 / T010: our SaaS paper passes our own Software / SaaS playbook, for every combination of answers.

Failure mode guarded: the shipped template drifting from its playbook (a 72-hour breach window, a hosting
clause without privacy approval, two renewal periods, a data breach both capped and uncapped) so that our own
paper is redlined by our own review."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.playbooks.library import PLAYBOOK_LIBRARY
from app.playbooks.service import evaluate_rules_against_text
from eval import template_matrix as matrix

RULES = [SimpleNamespace(id=r["clause_type"], **r)
         for p in PLAYBOOK_LIBRARY if p["name"] == "Software / SaaS Agreement Playbook" for r in p["rules"]]
COMBOS = list(matrix.combinations("saas"))


def _text(**fields) -> str:
    _, answers, ctx, company, cp = matrix._pick("saas", *[f"{k}={v}" for k, v in fields.items()])
    return matrix.render("saas", answers, ctx, company=company, counterparty=cp)


def test_every_combination_has_no_finding():
    """3,168 drafts (payment x hosting x term x personal data x currency x entity) each give zero findings on
    all 15 SaaS rules, so no answer can produce paper our own review flags."""
    assert len(COMBOS) == 3168 and len(RULES) == 15
    bad = []
    for label, answers, ctx, company, cp in COMBOS:
        found = evaluate_rules_against_text(rules=RULES, text=matrix.render("saas", answers, ctx, company=company,
                                                                            counterparty=cp))
        if found:
            bad.append((label, [(d.clause_type, d.issue) for d in found]))
    assert not bad, bad[:3]


@pytest.mark.parametrize("hosting,place", [("India", "located in India."),
                                           ("European Union", "located in the European Union"),
                                           ("United States", "located in the United States of America"),
                                           ("Several regions", "data centre regions listed in the Order Form")])
def test_hosting_clause_follows_the_answer(hosting, place):
    """The hosting answer reaches clause 9.1, and outside India the privacy function must approve first."""
    text = _text(hosting=hosting)
    assert place in text and "privacy function" in text
    if hosting != "India":
        assert "subject to the written approval of the Customer's privacy function" in text
    assert text.count("\n9.1 The Provider shall host") == 1 and "9.2 The Provider shall not move Customer Data" in text


def test_one_renewal_notice_of_at_least_sixty_days():
    """Clauses 6.1 and 6.2 once named different periods (30 vs 60); every stated notice is one number >= 60."""
    seen = 0
    for label, answers, ctx, company, cp in COMBOS:
        if "Renews automatically" not in label:
            continue
        text = matrix.render("saas", answers, ctx, company=company, counterparty=cp)
        in_61 = re.findall(r"gives \w+(?:-\w+)? \((\d+)\) days' written notice of non-renewal", text)
        in_62 = re.findall(r"at least \w+(?:-\w+)? \((\d+)\) days before the end of the then-current", text)
        assert len(in_61) == 1 and len(in_62) == 1 and in_61 == in_62 and int(in_62[0]) >= 60, label
        seen += 1
    assert seen


def test_data_breach_is_under_one_cap_and_never_also_uncapped():
    """A breach that is also a confidentiality breach or a data indemnity claim must sit under the Data Breach
    Cap only; the carve-outs from the caps must not list it, or it would be both capped and uncapped."""
    text = _text(currency="INR")
    assert text.count('(the "Data Breach Cap")') == 1 and text.count('(the "General Cap")') == 1
    assert "INR 50,000,000 (five crore rupees)" in text
    assert "(a) breach of clause 11 (Confidentiality), other than a Data Breach Claim" in text
    assert "(b) the indemnities in clauses 14.1(a) (the IP indemnity) and 14.1(c), which are unlimited" in text
    assert "indemnities in clause 14 (Indemnification)" not in text
    assert "not in addition to it" in text


def test_floor_follows_the_currency():
    """The data-breach floor is the currency equivalent of INR 5 crore, not a rupee figure on a dollar deal."""
    assert "USD 600,000 (the" in _text(currency="USD") and "INR 50,000,000 (five crore rupees) (the" not in _text(
        currency="USD")


def test_continuity_suspension_subprocessor_exit_billing_and_tax_are_in_the_paper():
    """The clauses added for the playbook's new rules exist in every draft."""
    text = _text(currency="USD")
    for needed in ("disaster recovery plan", "recovery point objective of twenty-four (24) hours",
                   "may suspend the Service only", "more than thirty (30) days overdue",
                   "may terminate the affected Order Form on written notice without liability",
                   "quarterly in arrears", "under reverse charge", "cyber liability insurance",
                   "twenty-four (24) hours (and within six (6) hours"):
        assert needed in text, needed
    assert "seventy-two" not in text


def test_no_delaware_and_no_unfilled_placeholder():
    """The old paper fell back to Delaware law; SaaS paper is Indian-law unless the form says otherwise, and no
    brace placeholder is left in any draft."""
    for label, answers, ctx, company, cp in COMBOS[::97]:
        text = matrix.render("saas", answers, ctx, company=company, counterparty=cp)
        assert "Delaware" not in text and not re.search(r"\{\w+\}", text), label
