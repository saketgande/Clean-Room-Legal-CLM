"""The shipped Statement of Work wording agrees with the Statement of Work Playbook (spec 014, T008).

Failures this guards:
* any of the 192 answer combinations drafting wording that its own playbook flags (our paper sent back
  as a redline), or two pricing models printed in one SOW;
* a SOW stating its own payment period or governing law instead of following the master agreement;
* a request with no pricing / total, or only placeholder scope, being printed instead of held;
* the master agreement unnamed, or a parent-less request printing a dangling placeholder.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.intake import paper, sow
from app.playbooks.library import PLAYBOOK_LIBRARY
from app.playbooks.service import evaluate_rules_against_text
from eval.template_matrix import combinations, render

_RULES = [SimpleNamespace(id=r["clause_type"], **r)
          for p in PLAYBOOK_LIBRARY if p["name"] == "Statement of Work Playbook" for r in p["rules"]]
_COMBOS = list(combinations("sow"))
_ONE = {"Fixed fee": "8.1 Fixed fee.", "Milestones": "8.1 Fixed fee payable in instalments.",
        "Time and materials": "8.1 Time and materials."}


def test_matrix_is_the_192_the_plan_freezes():
    """A form option added or dropped silently would shrink what the alignment tests cover."""
    assert len(_COMBOS) == 192


@pytest.mark.parametrize("combo", _COMBOS, ids=[c[0] for c in _COMBOS])
def test_every_combination_passes_the_playbook_and_prints_one_model(combo):
    """Zero findings for every combination, and exactly the chosen pricing model printed."""
    _, fields, ctx, _, _ = combo
    text = render("sow", fields, ctx)
    assert evaluate_rules_against_text(rules=_RULES, text=text) == []
    assert [m for m in _ONE.values() if m in text] == [_ONE[fields["pricing"]]]
    assert "{" not in text
    assert ("Master Agreement" in text) and "No Service Levels apply" in text
    if ctx.parent_title:
        assert ctx.parent_title in text


def test_rates_follow_the_master_agreement_and_fixed_fee_has_none():
    """Q19: rates only in the master agreement or a signed change order; a fixed fee prints no rates."""
    tm = sow.pricing_terms({"pricing": "Time and materials", "value": "100", "currency": "INR"})
    assert "set out in the Master Agreement" in tm and "signed Change Order" in tm
    assert "No rates apply" in sow.pricing_terms({"pricing": "Fixed fee", "value": "100"})


@pytest.mark.parametrize("fv", [{}, {"pricing": "Fixed fee"}, {"value": "5"}, {"pricing": "Milestones", "value": "x"}])
def test_no_model_or_no_total_prints_nothing(fv):
    """A SOW without its pricing must be held (empty fields), never printed with a guessed model."""
    assert sow.pricing_terms(fv) == "" and sow.sow_payment(fv) == ""


def test_held_drafts_name_their_reason():
    """The hold wording exists for the two holds the SOW draws (no pricing, placeholder scope)."""
    assert paper.placeholder_scope("This is a test")
    assert not paper.placeholder_scope(
        "Redesign and rebuild the customer self-service portal, including user research, visual design, "
        "build, testing and launch support.")
    assert paper.SOW_PRICING_HELD and paper.SCOPE_HELD
