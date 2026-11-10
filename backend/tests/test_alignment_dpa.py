"""Spec 014 (T012): the DPA template and the Data Processing Agreement playbook agree.

Failures this guards:
* a drafted DPA that its own playbook flags, so our own paper is redlined (checked over every
  combination of the template matrix);
* the DPA naming no principal agreement, or running "until terminated" / for its own fixed term while
  the principal agreement says otherwise;
* SCCs demanded for DPDP-only data, or the EU/UK transfer safeguards dropped (Q7);
* a foreign-law (Delaware) forum, a second liability limit, or the INR 5 crore floor going missing.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.playbooks.library import PLAYBOOK_LIBRARY
from app.playbooks.service import evaluate_rules_against_text
from eval.template_matrix import combinations, render

RULES = [
    SimpleNamespace(id=f"r{i}", **r)
    for i, r in enumerate(
        next(p for p in PLAYBOOK_LIBRARY if p["name"] == "Data Processing Agreement Playbook")["rules"]
    )
]
COMBOS = list(combinations("dpa"))


def _text(combo) -> str:
    _, fields, ctx, company, cp = combo
    return render("dpa", fields, ctx, company=company, counterparty=cp)


def test_every_dpa_combination_is_clean_against_its_playbook():
    """A draft the playbook flags sends our own paper back as a redline (FR-43)."""
    assert len(COMBOS) == 200
    bad = []
    for combo in COMBOS:
        found = evaluate_rules_against_text(rules=RULES, text=_text(combo))
        if found:
            bad.append((combo[0], [(d.clause_type, d.issue) for d in found]))
    assert bad[:3] == []


def test_every_combination_has_the_frozen_clauses():
    """Each frozen clause must be in every draft, whichever answers it was made from."""
    for combo in COMBOS[::7]:
        text = _text(combo)
        for needle in ("(the \"Principal Agreement\")", "continues until the later of the termination",
                       "governed by the law that governs the Principal Agreement",
                       "section 16 of the DPDP Act", "Module Two", "International Data Transfer Addendum",
                       "law of Ireland", "twenty-four (24) hours", "six (6) hours",
                       "(g) its cause and, where known, the person responsible",
                       "3.5 The Processor shall not undertake tracking", "ten (10) business days",
                       "three (3) times the fees", "never lower than the general limit",
                       "Registered office and address for notices"):
            assert needle in text, (combo[0], needle)
        assert "{" not in text, combo[0]


def test_the_principal_agreement_is_named_and_not_the_old_generic_phrase():
    """Recital A said "the services agreement between the Parties", naming nothing (D1)."""
    text = _text(COMBOS[0])
    assert "services agreement between the Parties" not in text
    assert "under " in text.split("A. The Processor provides services to the Controller")[1][:20]


def test_term_has_no_contradiction_with_the_principal_agreement():
    """A DPA that "continues until terminated" or has its own term outlives or undercuts the contract (D2)."""
    for combo in COMBOS[::11]:
        text = _text(combo).lower()
        assert "continues until terminated" not in text
        assert "commences on the effective date and" not in text


def test_dpdp_data_needs_no_scc_while_eu_uk_data_does():
    """Q7: restricted-country rule only for DPDP data; SCCs only for GDPR / UK GDPR data."""
    text = _text(COMBOS[0])
    a, b = text.split("(b) for Personal Data subject to the GDPR")
    clause_a = a.split("(a) for Personal Data subject to the DPDP Act")[1]
    assert "no other safeguard is required" in clause_a
    assert "Standard Contractual Clauses" not in clause_a
    assert "Standard Contractual Clauses (Module Two" in b


def test_no_delaware_and_one_liability_limit():
    """Decision on law: it follows the Principal Agreement; Q8: one 3x limit with the INR 5 crore floor."""
    for combo in COMBOS[::13]:
        text = _text(combo)
        assert "Delaware" not in text
        assert text.count("shall not exceed the greater of") == 1
        assert "(b) INR 50,000,000 (five crore rupees)" in text


def test_a_counterparty_72_hour_breach_clause_is_flagged():
    """The playbook still catches the old long window (FR-52)."""
    found = evaluate_rules_against_text(
        rules=RULES,
        text="The Processor shall notify the Controller of a personal data breach within 72 hours.",
    )
    assert any(d.clause_type == "breach_notification" for d in found)
