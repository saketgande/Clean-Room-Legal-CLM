"""Spec 014 (T009): our Vendor paper passes the Vendor Agreement Playbook.

Failures this guards:
* a Vendor draft, for any answer the form offers, that our own playbook then redlines (a finding on our
  own paper);
* a buying variant losing the wording it needs (goods without delivery / recall, services without the
  acceptance clause) or gaining the other variant's;
* the old Delaware / court-only wording coming back, or a Vendor draft with an unfilled placeholder.
"""

from types import SimpleNamespace

import pytest

from app.playbooks.library import PLAYBOOK_LIBRARY
from app.playbooks.service import evaluate_rules_against_text
from eval.template_matrix import combinations, render

_SPECS = next(p for p in PLAYBOOK_LIBRARY if p["name"] == "Vendor Agreement Playbook")["rules"]
RULES = [SimpleNamespace(id=f"r{i}", **spec) for i, spec in enumerate(_SPECS)]
CASES = list(combinations("vendor"))

GOODS_ONLY = ("3.4 Quantities.", "3.5 Recall.", "public and product liability insurance")
SERVICES_ONLY = ("professional indemnity insurance", "Services are accepted only on the Company's written")
ALWAYS = ("2.4 If a Deliverable is not delivered", "0.5%", "4.6 The Fees exclude goods and services tax",
          "4.7 If the Vendor is a micro or small enterprise", "6.5 The Vendor is solely responsible for the wages",
          "greater of (a)", "twenty-four (24) hours", "Signed for and on behalf of Aegis Pharma Ltd")


def test_matrix_is_the_full_product():
    """A matrix that silently shrank would let a whole variant go unchecked."""
    assert len(CASES) == 2376


def test_vendor_playbook_has_its_twelve_rules():
    """The test must run against every Vendor rule, not a trimmed list."""
    assert len(RULES) == 12


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_every_combination_passes_the_vendor_playbook(case):
    """A finding on our own Vendor paper means the AI review redlines the template we sent."""
    label, fields, ctx, company, counterparty = case
    text = render("vendor", fields, ctx, company=company, counterparty=counterparty)
    found = evaluate_rules_against_text(rules=RULES, text=text)
    assert [(d.rule.clause_type, d.citation) for d in found] == [], label
    assert "Delaware" not in text and "{" not in text and "}" not in text


def _first(buying: str):
    return next(c for c in CASES if f"buying={buying}" in c[0].split("|"))


def _text(case) -> str:
    return render("vendor", case[1], case[2], company=case[3], counterparty=case[4])


@pytest.mark.parametrize("buying", ["Goods", "Services", "Goods and services"])
def test_each_buying_variant_has_its_wording(buying):
    """Goods get delivery and recall, services get acceptance, both get both; none gets the other's."""
    text = _text(_first(buying))
    goods, services = buying != "Services", buying != "Goods"
    for phrase in ALWAYS:
        assert phrase in text
    for phrase in GOODS_ONLY:
        assert (phrase in text) is goods, phrase
    for phrase in SERVICES_ONLY:
        assert (phrase in text) is services, phrase
    assert "certain goods and services" in text if buying == "Goods and services" else True
    assert f"certain {buying.lower()} on" in text
