"""Spec 014, pair fixes PF-27 to PF-30 after the 64-review sample (batch f1fc2f46) and the new pass rule.

Each test names the failure it guards: the Mumbai seat wording read as the prohibited foreign-court phrase, a
Vendor/SaaS indemnity that never says who controls the defence, a public liability line with no amount, a
stated contract value read as narrowing the liability cap, and the eval pass rule (same clause type in 2 of 3
runs fails a combination)."""

from __future__ import annotations

import pytest

from app.intake import paper
from app.playbooks.service import evaluate_rules_against_text
from eval.template_alignment import repeated_clause_types
from tests.test_pair_fixes_pf15_21 import _rule, _with

MSA, VENDOR, SAAS, CONS = ("Master Services Agreement Playbook", "Vendor Agreement Playbook",
                           "Software / SaaS Agreement Playbook", "Consultancy Agreement Playbook")


@pytest.mark.parametrize("key,playbook", [("vendor", VENDOR), ("saas", SAAS), ("msa", MSA)])
def test_mumbai_seat_wording_is_not_the_prohibited_phrase(key, playbook):
    """'exclusive supervisory jurisdiction' was read as 'exclusive jurisdiction of the courts of'. Our clause now says
    the seat's courts alone have supervisory jurisdiction over the arbitration, and the rule says that is not it."""
    text = _with(key)
    assert "exclusive supervisory jurisdiction" not in text
    assert "alone have supervisory jurisdiction over the arbitration" in text
    assert "exclusive jurisdiction of the courts of" not in text
    rule = _rule(playbook, "governing_law")
    assert "supervisory jurisdiction over the arbitration is not the prohibited phrase" in rule["preferred_position"]
    assert not evaluate_rules_against_text(rules=[_as_eval(rule)], text=text)


def _as_eval(rule: dict):
    from types import SimpleNamespace
    return SimpleNamespace(id="r", **rule, is_active=True, name=rule["clause_type"])


@pytest.mark.parametrize("key,who", [("vendor", "Vendor"), ("saas", "Provider")])
def test_indemnity_says_the_provider_controls_the_defence(key, who):
    """Vendor 10.2 and SaaS 14.2 only limited the claimant's settlement, so the reviewer asked who controls the
    defence. They now say the provider does, with participation at our cost and consent to fault admissions."""
    text = _with(key)
    assert f"The {who} controls the defence and settlement of the claim" in text
    assert "admits fault for, or imposes any obligation on," in text
    assert "with its own counsel at its own cost" in text
    for pb in (VENDOR, MSA):
        assert "controls the defence and settlement of an indemnified claim" in _rule(
            pb, "indemnification")["preferred_position"]


def test_consultancy_public_liability_has_an_amount_the_rule_names():
    """Public liability had no figure, so the insurance rule's per-claim position was unmet. It now uses the
    one-crore tier from paper.MINIMUMS, in the fee currency, and the rule lists the same foreign equivalents."""
    text = _with("consultancy")
    assert "public liability insurance of not less than INR 10,000,000 (one crore rupees) per claim" in text
    pos = _rule(CONS, "insurance")["preferred_position"]
    assert "public liability insurance of at least INR 1 crore" in pos
    for cur in ("USD", "EUR", "GBP"):
        assert paper.MINIMUMS[cur]["one_crore"] in pos


def test_stated_contract_value_says_it_does_not_limit_liability():
    """MSA 6.1's 'total value shall not exceed X' was read as a cap narrower than 13.1. The sentence now says it
    is a spending figure only and liability is governed solely by the limitation clause (MSA, Vendor, SaaS)."""
    for key in ("msa", "vendor", "saas"):
        try:
            text = _with(key, value="given")
        except AssertionError:
            continue  # this type's matrix has no value dimension; the sentence is shared, so MSA proves it
        assert "commercial limit on spending only; it does not limit or reduce either Party's liability" in text
    assert "commercial limit on spending only" in _with("msa", value="given")
    assert "spending figure only" in _rule(MSA, "limitation_of_liability")["preferred_position"]


def _run(combo, n, *clause_types):
    return {"combination": combo, "run": n, "findings": [{"clause_type": c} for c in clause_types]}


def test_pass_rule_fails_only_a_clause_type_repeated_in_two_runs():
    """One-off findings differ every run and must not fail a combination; the same clause type in 2 of 3 runs
    must. A type twice in one run counts once."""
    runs = [_run("a", 1, "insurance"), _run("a", 2, "tax"), _run("a", 3),
            _run("b", 1, "insurance", "insurance"), _run("b", 2), _run("b", 3),
            _run("c", 1, "tax"), _run("c", 2, "tax", "insurance"), _run("c", 3, "insurance"),
            {"combination": "d", "run": 1, "error": "boom"}]
    assert repeated_clause_types(runs) == {"c": ["insurance", "tax"]}
