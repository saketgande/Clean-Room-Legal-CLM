"""Spec 014, pair fixes PF-31 to PF-34 from the code review and the f1fc2f46 sample.

Each test names the failure it guards: an Indian city read as a foreign jurisdiction (false Singapore seat and
recital), the first city of an address taken as ours, a standard update that overwrote a playbook whose
approval or escalation had been edited, and a several-regions SaaS clause that left the location open until an
Order Form existed."""

from __future__ import annotations

import pytest

from app.drafting_templates import standard
from app.drafting_templates.service import current_wording, get_type
from app.intake import paper
from app.playbooks.models import PlaybookRule
from app.playbooks.service import evaluate_rules_against_text
from tests.test_pair_fixes_pf15_21 import _rule, _with
from tests.test_standard_update_014 import (  # noqa: F401  (db is a fixture the tests below take by name)
    PUBLISHER,
    _audit,
    _playbook,
    _user,
    _versions,
    db,
)

SAAS = "Software / SaaS Agreement Playbook"


@pytest.mark.parametrize("place", ["Mumbai", "Bangalore, KA", "Pune, Maharashtra", "Delhi", "India", "Goa", ""])
def test_indian_places_are_not_foreign(place):
    """'Mumbai' alone named no state or 'India', so it was foreign: a Singapore seat and a false recital that
    the party is established in Mumbai, outside India. A city or state is Indian; blank stays domestic."""
    assert paper.is_foreign(place) is False


@pytest.mark.parametrize("place", ["Punjab, Pakistan", "Hyderabad, Pakistan", "Delaware", "Singapore", "London",
                                   "England and Wales", "Dhaka, Bangladesh", "State of New York, USA"])
def test_foreign_places_are_foreign_even_beside_an_indian_place_word(place):
    """'Punjab, Pakistan' matched 'Punjab' and passed as Indian, so a Pakistani party got an Indian seat."""
    assert paper.is_foreign(place) is True


def test_an_indian_city_counterparty_gets_an_indian_seat_and_no_foreign_recital():
    """The end-to-end symptom of the above, on a vendor draft."""
    ctx = paper.DraftContext(entity_address="Level 5, Pune 411001", counterparty_jurisdiction="Mumbai")
    v = paper.paper_values("vendor", {}, company="X Ltd", counterparty="Y Ltd", context=ctx)
    assert v["dispute_seat"] == "Pune" and "outside India" not in v["dispute_resolution"]


@pytest.mark.parametrize("address,city", [
    ("Delhi Public School Road, Pune 411001", "Pune"),
    ("Level 5, One BKC, Bandra Kurla Complex, Mumbai 400051, India", "Mumbai"),
    ("12 Noida Link Road, Sector 3, Gurugram, Haryana 122001", "Gurugram"),
    ("Plot 4, Hyderabad Road, New Delhi 110001", "New Delhi"),
])
def test_the_seat_is_the_city_nearest_the_end_of_our_address(address, city):
    """The first listed city won, so a street named after another city ('Delhi Public School Road, Pune') moved
    the seat to New Delhi. Indian addresses end city, state, PIN, so the last city named is ours."""
    ctx = paper.DraftContext(entity_address=address)
    assert paper._seat("msa", "X Ltd", ctx) == city


def _msa_rules(**edit):
    base = {"clause_type": "payment_terms", "preferred_position": "Pay in arrears.", "rationale": "Cash flow.",
            "approval_required": False, "escalation_role": None}
    return [base, {**base, "clause_type": "insurance", **edit}]


def _baseline_of(monkeypatch, rules):
    row = [{**r, "rule_type": "standard_position", "risk_level": "medium"} for r in rules]
    monkeypatch.setitem(standard.BASELINE_FINGERPRINTS, "msa", frozenset({standard.fingerprint(row)}))


def test_an_edit_to_only_approval_or_escalation_is_not_overwritten_and_is_reported(db, monkeypatch):  # noqa: F811
    """The fingerprint ignored approval_required and escalation_role, so a playbook edited only there matched the
    reviewed baseline and the update replaced it. It must be skipped, audited and read as behind."""
    _baseline_of(monkeypatch, _msa_rules())
    user = _user(db, PUBLISHER)
    pb, _ = _playbook(db, user, _msa_rules(approval_required=True, escalation_role="CFO"), old=True)

    standard.apply_standard_update(db)

    assert len(_versions(db, pb)) == 1
    assert len(_audit(db, user.org_id, "playbook.standard_skipped")) == 1
    ttype = get_type(db, user.org_id, "msa")
    assert standard.status_for(db, user.org_id, ttype, current_wording(db, user.org_id, ttype))[
        "playbook_behind"] is True


def test_the_same_rules_without_that_edit_are_still_updated(db, monkeypatch):  # noqa: F811
    """Control for the test above: the stricter hash must not stop an untouched baseline from being updated."""
    _baseline_of(monkeypatch, _msa_rules())
    user = _user(db, PUBLISHER)
    pb, _ = _playbook(db, user, _msa_rules(), old=True)
    standard.apply_standard_update(db)
    assert len(_versions(db, pb)) == 2


def test_an_organisation_already_on_the_standard_still_reads_as_up_to_date(db):  # noqa: F811
    """The hash now covers four more fields, so STANDARD_FINGERPRINT changed too. Rows equal to the standard
    (an organisation that adopted it) must still not read as behind, even dated before the standard."""
    user = _user(db, PUBLISHER)
    _, v = _playbook(db, user, [], old=True)
    for r in standard.STANDARD_RULES["msa"]:
        db.add(PlaybookRule(org_id=user.org_id, playbook_version_id=v.id, **r))
    db.flush()
    ttype = get_type(db, user.org_id, "msa")
    assert standard.status_for(db, user.org_id, ttype, current_wording(db, user.org_id, ttype))[
        "playbook_behind"] is False


def test_the_shipped_baselines_are_hashed_on_every_editable_field():
    """Guards the regenerated baseline: the library rules of the review era must hash to it on all fields,
    and the NDA alone is compared on the reviewed eight (its extras are the organisation's own)."""
    assert standard.BASELINE_FIELDS == {"nda": standard.CORE_FIELDS}
    assert len(standard.FIELDS) == 12
    a = {"clause_type": "c", "preferred_position": "p"}
    assert standard.row_hash({**a, "approval_required": True}) != standard.row_hash(a)
    assert standard.row_hash({**a, "approval_required": False, "escalation_role": ""}) == standard.row_hash(a)


def test_several_regions_fixes_the_location_at_signature():
    """The reviewer found 9.1 named no location and left it to an Order Form that might not exist yet. The
    wording now ties the regions to the Order Form signed with the Agreement and bars any other region."""
    text = _with("saas", hosting="Several regions")
    assert "regions listed in the Order Form signed with this Agreement, and in no other region" in text
    assert "written approval of the Customer's privacy function, which shall be given before any Customer" in text


def test_the_saas_rule_accepts_signed_order_form_regions_but_not_any_region_we_choose():
    """The rule says signed-with-the-Agreement regions with approval meet the position; the provider's own
    choice of region still trips the prohibited phrase."""
    rule = _rule(SAAS, "data_location")
    assert "Order Form signed together with the Agreement" in rule["preferred_position"]
    assert "and no other region" in rule["preferred_position"]
    from types import SimpleNamespace
    r = SimpleNamespace(id="r", **rule, is_active=True, name="data_location")
    bad = "The Provider may host Customer Data in any location chosen by the Provider."
    assert evaluate_rules_against_text(rules=[r], text=bad)
