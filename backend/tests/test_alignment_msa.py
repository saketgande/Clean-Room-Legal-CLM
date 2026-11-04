"""Our own MSA paper must pass our own MSA playbook (spec 014, FR-31, FR-32).

Failure mode guarded: a template that disagrees with its playbook gets our own paper redlined by the
automatic review. Every answer combination the form can draft is rendered and judged by the playbook rules.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.playbooks.library import PLAYBOOK_LIBRARY
from app.playbooks.service import evaluate_rules_against_text
from eval.template_matrix import combinations, render

_RULES = [SimpleNamespace(id=r["clause_type"], **r) for r in
          next(p for p in PLAYBOOK_LIBRARY if p["name"] == "Master Services Agreement Playbook")["rules"]]
_COMBOS = list(combinations("msa"))


def _draft(*parts: str) -> str:
    _, fields, ctx, company, cp = next(c for c in _COMBOS if set(parts) <= set(c[0].split("|")))
    return render("msa", fields, ctx, company=company, counterparty=cp)


def test_matrix_size_is_the_planned_one():
    """A silently shrunken matrix would make the zero-findings check below prove less than it claims."""
    assert len(_COMBOS) == 2112


def test_every_combination_passes_the_msa_playbook():
    """Any finding means our own paper would be redlined; report every offending combination at once."""
    bad = []
    for label, fields, ctx, company, cp in _COMBOS:
        text = render("msa", fields, ctx, company=company, counterparty=cp)
        found = evaluate_rules_against_text(rules=_RULES, text=text)
        if found:
            bad.append((label, [f.clause_type if hasattr(f, "clause_type") else str(f) for f in found]))
    assert not bad, f"{len(bad)} combinations flagged; first: {bad[:3]}"


def test_foreign_paper_uses_indian_law_and_siac_arbitration():
    """Foreign counterparty paper must keep Indian law and name the SIAC route, not a foreign court."""
    text = _draft("ctx=foreign")
    assert "laws of India" in text and "Singapore International Arbitration Centre" in text and "Delaware" not in text


def test_every_combination_has_the_fixed_protections():
    """Fixed wording the playbook relies on must survive every combination of answers."""
    for _, fields, ctx, company, cp in _COMBOS[::50]:
        t = render("msa", fields, ctx, company=company, counterparty=cp)
        assert "five percent (5%)" in t and "fifteen percent (15%)" in t
        assert "Micro, Small and Medium Enterprises Development Act, 2006" in t
        assert "input tax credit" in t and "reverse charge" in t
        assert "twenty-four (24) hours" in t and "six (6) hours" in t
        assert "Signed for and on behalf of" in t and "Delaware" not in t
        assert "{" not in t


@pytest.mark.parametrize("cur", ["INR", "USD"])
def test_insurance_is_stated_in_the_contract_currency(cur):
    """Insurance without a stated amount in the fee currency leaves the indemnities unbacked."""
    text = _draft(f"currency={cur}")
    line = next(ln for ln in text.splitlines() if ln.startswith("14.1"))
    assert "per claim" in line and "{" not in line
    assert (("INR" in line) if cur == "INR" else ("INR" not in line))
