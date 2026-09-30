"""The starter playbooks are picked for the right contracts and don't flag our own paper.

Two failures this guards:
* a contract type matching no playbook, or another type's (the review then
  runs the wrong rules or doesn't run at all — the audit found "no published
  playbook" on every drafted contract);
* a contract drafted from our own template being flagged, when the AI can't
  run, for "missing" required language it plainly contains, or for
  prohibited language the template itself uses.
"""

from types import SimpleNamespace

import pytest

from app.contract_files.blocks import find_phrase
from app.intake import drafting
from app.playbooks.library import CONTRACT_TYPE_OF_FORM, CONTRACT_TYPE_OF_KIND, PLAYBOOK_LIBRARY
from app.playbooks.service import _type_tokens

_BY_NAME = {p["name"]: p for p in PLAYBOOK_LIBRARY}


def _picked(contract_type: str) -> str | None:
    """pick_playbook_for_contract's choice among the library, by name overlap."""
    wanted = _type_tokens(contract_type)
    scored = [(len(wanted & _type_tokens(n)), n) for n in _BY_NAME]
    best = max(scored)
    return best[1] if best[0] else None


@pytest.mark.parametrize(("contract_type", "playbook"), [
    (CONTRACT_TYPE_OF_KIND["Services (MSA)"], "Master Services Agreement Playbook"),
    (CONTRACT_TYPE_OF_KIND["Consultancy"], "Consultancy Agreement Playbook"),
    (CONTRACT_TYPE_OF_KIND["Buying from a vendor"], "Vendor Agreement Playbook"),
    (CONTRACT_TYPE_OF_KIND["Software or SaaS"], "Software / SaaS Agreement Playbook"),
    (CONTRACT_TYPE_OF_KIND["Selling to a customer"], "Customer Agreement Playbook"),
    (CONTRACT_TYPE_OF_FORM["sow"], "Statement of Work Playbook"),
    (CONTRACT_TYPE_OF_FORM["dpa"], "Data Processing Agreement Playbook"),
])
def test_each_contract_type_gets_its_own_playbook(contract_type, playbook):
    assert _picked(contract_type) == playbook


def test_ndas_are_left_to_the_orgs_own_nda_playbook():
    assert _picked("NDA") is None


_FIELDS = {"value": "4500000", "currency": "INR", "start_date": "2026-10-01", "term": "Fixed end date",
           "end_date": "2027-09-30", "payment_terms": "45 days"}


@pytest.mark.parametrize(("playbook", "doc_type"), [
    ("Master Services Agreement Playbook", "msa"), ("Consultancy Agreement Playbook", "msa"),
    ("Vendor Agreement Playbook", "vendor"), ("Software / SaaS Agreement Playbook", "vendor"),
    ("Statement of Work Playbook", "msa"), ("Data Processing Agreement Playbook", "dpa"),
])
def test_our_own_template_passes_the_literal_check(playbook, doc_type):
    text = drafting.render_document(doc_type, company="Aegis Pharma Ltd", counterparty="Globex", effective="2026-10-01",
                                    fields=_FIELDS)
    for r in _BY_NAME[playbook]["rules"]:
        rule = SimpleNamespace(**r)
        if rule.required_language:
            assert find_phrase(text, rule.required_language), f"{rule.clause_type}: '{rule.required_language}' missing"
        if rule.prohibited_language:
            assert not find_phrase(text, rule.prohibited_language), f"{rule.clause_type}: template says '{rule.prohibited_language}'"


def test_every_rule_is_complete():
    """A rule the AI can't reason about (no position or no reason) is noise."""
    for p in PLAYBOOK_LIBRARY:
        assert len(p["rules"]) >= 7, p["name"]
        for r in p["rules"]:
            assert r["preferred_position"] and r["rationale"], (p["name"], r["clause_type"])
            assert r["risk_level"] in {"low", "medium", "high", "critical"}
