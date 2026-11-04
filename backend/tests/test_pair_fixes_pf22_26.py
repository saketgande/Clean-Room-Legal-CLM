"""Spec 014, pair fixes PF-22 to PF-26 after the second 64-review AI sample (batch 847a2aea).

Each test names the failure it guards. PF-22..25 pin the wording the AI reviewer read as a gap (the cap sentence,
the no-personal-data DPA, professional indemnity on goods paper, SaaS hosting approval and the uncapped IP
indemnity); PF-26 pins the recovery of review output whose JSON text carries unescaped defined-term quotes."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ai.schemas import PlaybookReviewOutput, _list_from_json_text
from tests.test_pair_fixes_pf15_21 import _rule, _with

MSA, VENDOR, SAAS = ("Master Services Agreement Playbook", "Vendor Agreement Playbook",
                     "Software / SaaS Agreement Playbook")


def test_msa_cap_sentence_in_template_is_the_rule_wording():
    """The reviewer read 'under this Agreement' as narrower than the rule's 'in the preceding 12 months'. The rule
    now quotes the template's own words and says a stated contract value does not narrow the cap."""
    phrase = "fees paid or payable under this Agreement in the twelve (12) months immediately preceding the event"
    assert phrase in _rule(MSA, "limitation_of_liability")["preferred_position"]
    assert phrase in _with("msa", gxp="No").replace("Fees", "fees")
    assert "does not narrow" in _rule(MSA, "limitation_of_liability")["preferred_position"]


@pytest.mark.parametrize("key", ["vendor", "msa"])
def test_no_personal_data_paper_still_signs_the_dpa_with_the_agreement(key):
    """Paper saying 'DPA signed before any processing' was read as missing the rule's 'signed with the agreement,
    applying from the Effective Date'. With no personal data expected the DPA is still signed now."""
    text = _with(key, personal_data="No")
    assert "do not expect" in text
    assert "at the same time as this Agreement" in text and "applies from the Effective Date" in text
    assert "Before any" not in text.split("do not expect", 1)[1][:300]
    assert "signed with this Agreement, applying from the" in _rule(
        VENDOR, "data_protection")["preferred_position"]


def test_goods_paper_has_no_service_wording_and_the_rule_excludes_goods_remedies():
    """Goods-only paper said 'goods or services invoiced' and 're-perform the services', so the reviewer asked for
    professional indemnity. The paper says Deliverables and the rule limits PI to services under an Order."""
    text = _with("vendor", buying="Goods")
    assert "professional indemnity" not in text
    assert "goods or services" not in text and "re-perform the services" not in text
    pos = _rule(VENDOR, "insurance")["preferred_position"]
    assert "services under an Order" in pos and "does not make a goods supplier a services supplier" in pos


@pytest.mark.parametrize("where", ["European Union", "United States", "Several regions"])
def test_saas_hosting_uses_the_rule_term_and_rule_calls_it_preferred(where):
    """EU/US hosting with Order Form + approval was read as fallback and 'given after review by' as not the
    'privacy function'. 9.1 and 9.2 name the Customer's privacy function as approver; the rule says it IS preferred."""
    text = _with("saas", hosting=where)
    assert "prior written approval of the Customer's privacy function" in text
    assert "given after review" not in text
    assert "IS the preferred position" in _rule(SAAS, "data_location")["preferred_position"]


def test_saas_ip_indemnity_is_expressly_uncapped():
    """15.4(b) only left the IP indemnity out of the cap; the reviewer wanted it stated as uncapped, as the rule says."""
    text = _with("saas", hosting="India")
    assert "(the IP indemnity) and 14.1(c), which are unlimited and not subject to the General Cap" in text
    assert "outside every cap" in _rule(SAAS, "limitation_of_liability")["preferred_position"]


# PF-26: fragments copied from the three ai_unavailable reviews (a_i_call_log, 2026-10-27), trimmed.
GST = ('[{"quote": "Opening.", "label": "Rule 11", "page_number": null}, {"quote": "The Fees exclude goods and '
       'services tax ("GST"). Where GST is chargeable on the Fees, each invoice shall show the GSTIN.", '
       '"label": "Tax", "page_number": null}]')
MSMED = ('[\n  {\n    "rule_index": 6,\n    "clause_type": "payment_terms",\n    "issue": "Cites the MSMED Act by its '
         'short name.",\n    "suggested_fix": "If the Provider is a micro or small enterprise registered under the '
         'Micro, Small and Medium Enterprises Development Act, 2006 ("MSMED Act"), it shall state its Udyam number.",'
         '\n    "severity": "low"\n  },\n  {\n    "rule_index": 3,\n    "clause_type": "data_location",\n    '
         '"issue": "Other."\n  }\n]')


def test_unescaped_defined_term_quotes_are_recovered_in_citations():
    """The model sent citations as JSON text whose contract wording had ("GST") unescaped, json.loads failed, and
    the whole review became ai_unavailable. Two items come back and the quotes stay in the text."""
    items = _list_from_json_text(GST)
    assert len(items) == 2 and 'goods and services tax ("GST").' in items[1]["quote"]
    assert len(PlaybookReviewOutput(citations=GST).citations) == 2


def test_unescaped_defined_term_quotes_are_recovered_in_deviations():
    """Same failure in `deviations` with ("MSMED Act") inside suggested_fix: both deviations come back."""
    out = PlaybookReviewOutput(deviations=MSMED)
    assert [d.clause_type for d in out.deviations] == ["payment_terms", "data_location"]
    assert '("MSMED Act")' in out.deviations[0].suggested_fix


def test_well_formed_json_text_is_untouched():
    """The repair only runs after a normal parse fails; escaped quotes must not be double-escaped."""
    good = json.dumps([{"quote": 'a ("GST") b'}])
    assert _list_from_json_text(good) == [{"quote": 'a ("GST") b'}]


@pytest.mark.parametrize("bad", ["[{not json at all", '[{"quote": "unterminated', "plain text", '{"a": "b"}'])
def test_garbage_still_fails_as_before(bad):
    """Repair must not invent a list: unparseable or non-list text goes on unchanged and validation rejects it."""
    assert _list_from_json_text(bad) == bad
    with pytest.raises(ValidationError):
        PlaybookReviewOutput(deviations=bad)
