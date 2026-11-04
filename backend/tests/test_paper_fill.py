"""Unit tests for app.intake.paper (spec 014): pure fill-ins for our own paper, no database."""

from collections import defaultdict

import pytest

from app.intake import paper
from app.intake.paper import BLANK, DraftContext, paper_values


def pv(doc, fv=None, ctx=None, **kw):
    return paper_values(doc, fv or {}, company="Acme Ltd", counterparty="Globex", context=ctx, **kw)


@pytest.mark.parametrize("answer,expected", [
    ("India", "India"), ("England and Wales", "England and Wales"), ("Singapore", "Singapore"),
    ("New York", "the State of New York"), ("Delaware", "the State of Delaware"),
    ("", "India"), ("Maharashtra, India", "India"), ("France", "India"),
])
def test_nda_law_mapping(answer, expected):
    """A blank, state or unrecognised NDA answer must become India, never Delaware (the old fallback)."""
    assert paper.governing_law("nda", {"governing_law": answer}) == expected


def test_other_templates_are_always_india():
    """A Delaware answer reaching a non-NDA template must not change its law (the form does not ask)."""
    for doc in ("msa", "sow", "vendor", "saas", "consultancy", "dpa"):
        assert paper.governing_law(doc, {"governing_law": "Delaware"}) == "India"


@pytest.mark.parametrize("address,seat", [
    ("Level 5, One BKC, Mumbai 400051, India", "Mumbai"),
    ("12 MG Road, Bangalore 560001", "Bengaluru"),
    ("1 Connaught Place, Delhi", "New Delhi"),
    ("Sector 5, Gurgaon", "Gurugram"),
    ("PUNE campus, India", "Pune"),
])
def test_seat_from_register_address(address, seat):
    """The seat is our registered city, normalised, found in the register address."""
    assert pv("msa", ctx=DraftContext(entity_address=address))["dispute_seat"] == seat


def test_seat_without_known_city_states_it_without_inventing_one():
    """No city in the address must not guess one: the seat is described as the registered office city."""
    for ctx in (None, DraftContext(entity_address="Somewhere 1, Springfield")):
        assert pv("msa", ctx=ctx)["dispute_seat"] == "the city in which Acme Ltd's registered office is situated"


@pytest.mark.parametrize("jur,foreign", [
    (None, False), ("", False), ("India", False), ("Maharashtra", False), ("Tamil Nadu, India", False),
    ("Singapore", True), ("Delaware, United States", True),
])
def test_foreign_provider_detection(jur, foreign):
    """Blank is the domestic default; only text naming no Indian place is foreign."""
    assert paper.is_foreign(jur) is foreign


def test_dispute_route_per_type():
    """MSA/Vendor/SaaS: MCIA arbitration, SIAC for a foreign provider; NDA and every Consultancy: courts."""
    mum = DraftContext(entity_address="Mumbai 400051")
    for doc in ("msa", "vendor", "saas"):
        d = pv(doc, ctx=mum)
        assert "Mumbai Centre for International Arbitration" in d["dispute_resolution"]
        assert "The seat of arbitration is Mumbai" in d["dispute_resolution"]
        f = pv(doc, ctx=DraftContext(entity_address="Mumbai", counterparty_jurisdiction="Singapore"))
        assert "Singapore International Arbitration Centre" in f["dispute_resolution"]
        assert f["dispute_seat"] == "Singapore"
    assert "exclusive jurisdiction of the courts at Mumbai" in pv("nda", ctx=mum)["dispute_resolution"]


def test_consultancy_always_courts_at_our_seat():
    """Decision 2: a foreign consultant gets the same courts-at-the-seat text, never Singapore or arbitration."""
    for jur in ("India", "Germany", None):
        d = pv("consultancy", ctx=DraftContext(entity_address="Pune", counterparty_jurisdiction=jur))
        assert d["dispute_seat"] == "Pune"
        assert "exclusive jurisdiction of the courts at Pune" in d["dispute_resolution"]
        assert "arbitration" not in d["dispute_resolution"]


def test_sow_and_dpa_state_no_dispute_clause():
    """SOW and DPA follow the parent agreement, so printing their own dispute route would contradict it."""
    assert pv("sow")["dispute_resolution"] == pv("dpa")["dispute_resolution"] == ""


def test_signature_blocks_split_and_blank():
    """Name/title split at the first comma; a missing part or missing register row is a clean blank line."""
    v = pv("msa", {"cp_signer_name": "Maya Chen, CFO"}, DraftContext(entity_signatory="R. Iyer, Director"))
    assert (v["our_signatory_name"], v["our_signatory_title"]) == ("R. Iyer", "Director")
    assert (v["their_signatory_name"], v["their_signatory_title"]) == ("Maya Chen", "CFO")
    v = pv("msa", {"cp_signer_name": "Maya Chen"})
    assert v["our_signatory_name"] == v["our_signatory_title"] == v["their_signatory_title"] == BLANK
    assert v["their_signatory_name"] == "Maya Chen"


def test_addresses_one_line_or_blank_never_invented():
    """A register address prints on one line; none prints a blank line, not a made-up address."""
    v = pv("msa", ctx=DraftContext(entity_address="1 A Road\n  Mumbai", counterparty_address=" 2 B St,\nPune "))
    assert v["company_address"] == "1 A Road Mumbai" and v["counterparty_address"] == "2 B St, Pune"
    v = pv("msa")
    assert v["company_address"] == v["counterparty_address"] == BLANK


def test_example_context_only_when_asked():
    """A real request must never show the sample signatory or address (FR-24); the example check does."""
    assert not any(m in str(pv("msa").values()) for m in paper.EXAMPLE_MARKERS)
    ex = pv("msa", example=True)
    assert ex["our_signatory_name"] == "Priya Nair" and "One BKC" in ex["company_address"]
    assert set(ex) == set(paper.FIELDS)


def test_minimums_by_currency_single_table():
    """Insurance and the data-breach floor come from MINIMUMS by currency; DPA ignores the request's currency."""
    assert pv("saas", {"currency": "USD"})["insurance_minimum"] == "USD 600,000"
    assert pv("vendor", {"currency": "EUR"})["data_breach_floor"] == "EUR 550,000"
    assert "five crore" in pv("saas")["data_breach_floor"]
    assert pv("dpa", {"currency": "GBP"})["fee_currency"] == "INR"
    assert pv("msa", {"currency": "XXX"})["fee_currency"] == "INR"
    assert paper.MINIMUMS["GBP"] == {"five_crore": "GBP 475,000", "one_crore": "GBP 95,000"}
    v = pv("vendor", {"currency": "USD", "buying": "Goods"})
    assert "USD 120,000" in v["vendor_insurance"] and "professional indemnity" not in v["vendor_insurance"]


def test_dpa_status_variants():
    """No personal data still requires a DPA before any is processed; Yes or blank (safe reading) signs now."""
    no = pv("msa", {"personal_data": "No"})["dpa_status"]
    assert no.startswith("The Parties do not expect the Provider to process Personal Data")
    for fv in ({"personal_data": "Yes"}, {}):
        assert "no personal data is processed before it is signed" in pv("vendor", fv)["dpa_status"]
    assert "Consultant" in pv("consultancy", {})["dpa_status"]
    assert pv("nda", {})["dpa_status"] == ""


def test_nda_personal_data_variants():
    """Yes keeps the protection but not a breach notice (11.1 ends with its own, so it would print twice);
    No/blank states none is expected."""
    yes = pv("nda", {"personal_data": "Yes"})["nda_personal_data"]
    assert "Digital Personal Data Protection Act" in yes and "breach" not in yes
    for fv in ({"personal_data": "No"}, {}):
        text = pv("nda", fv)["nda_personal_data"]
        assert text.startswith("The Parties do not expect") and "Digital Personal Data Protection Act" in text


def test_gxp_only_for_msa_yes():
    """The quality-agreement clause appears only on an MSA that said GxP."""
    assert "quality agreement" in pv("msa", {"gxp": "Yes"})["gxp_clause"]
    assert pv("msa", {"gxp": "No"})["gxp_clause"] == pv("saas", {"gxp": "Yes"})["gxp_clause"] == ""


def test_parent_agreement():
    """Named parent with date, without date, and the generic fallback (DPA says services agreement)."""
    ctx = DraftContext(parent_title="Master Services Agreement", parent_effective="2026-04-01")
    assert pv("sow", ctx=ctx)["parent_agreement"] == (
        'the agreement titled "Master Services Agreement" dated 1 April 2026 between the Parties')
    assert pv("sow", ctx=DraftContext(parent_title="MSA"))["parent_agreement"] == (
        'the agreement titled "MSA" between the Parties')
    assert pv("sow")["parent_agreement"] == "the master agreement between the Parties"
    assert pv("dpa")["parent_agreement"] == "the services agreement between the Parties"


def test_vendor_supply_variants():
    """Goods, services and both print different clauses; services alone is numbered 3.3, after goods 3.6."""
    goods, svc, both = (pv("vendor", {"buying": b}) for b in ("Goods", "Services", "Goods and services"))
    assert goods["supply_description"] == "goods" and svc["supply_description"] == "services"
    assert both["supply_description"] == "goods and services" and pv("vendor")["supply_description"] == "goods and services"
    assert "3.5 Recall" in goods["vendor_supply_terms"] and "Services." not in goods["vendor_supply_terms"]
    assert svc["vendor_supply_terms"].startswith("\n\n3.3 Services.") and "Recall" not in svc["vendor_supply_terms"]
    assert "3.5 Recall" in both["vendor_supply_terms"] and "3.6 Services." in both["vendor_supply_terms"]
    assert "product liability" in goods["vendor_insurance"] and "professional indemnity" in svc["vendor_insurance"]
    assert "product liability" in both["vendor_insurance"] and "professional indemnity" in both["vendor_insurance"]
    assert pv("msa")["vendor_supply_terms"] == pv("msa")["vendor_insurance"] == ""


@pytest.mark.parametrize("where,needle", [
    ("India", "located in India."), ("European Union", "located in the European Union. That location"),
    ("United States", "the United States of America. That location"),
    ("Several regions", "regions listed in the Order Form signed with this Agreement"), ("", "located in India."),
])
def test_hosting_variants(where, needle):
    """Each hosting answer prints its own 9.1 and every variant keeps the 9.2 transfer limit."""
    text = pv("saas", {"hosting": where})["hosting_clause"]
    assert needle in text and "9.2 The Provider shall not move Customer Data" in text


def test_renewal_notice_floor_and_words():
    """A 30-day notice is raised to 60 so 6.1 and 6.2 agree; 90 stays; words render with the number."""
    assert paper.renewal_notice_days({"notice_days": "30 days"}) == 60
    assert paper.renewal_notice_days({"notice_days": "90 days"}) == 90
    assert paper.renewal_notice_days({}) == 60
    assert [paper.number_words(n) for n in (6, 24, 30, 45, 60, 90)] == [
        "six (6)", "twenty-four (24)", "thirty (30)", "forty-five (45)", "sixty (60)", "ninety (90)"]
    assert pv("saas", {"notice_days": "30 days"})["renewal_notice_days"] == "sixty (60)"


def test_consultancy_invoicing_variants():
    """Invoicing follows the fee basis: instalments on accepted deliverables, monthly pro rata, or time."""
    assert "Engagement Schedule" in pv("consultancy", {"fee_basis": "Fixed fee",
                                                       "consultancy_deliverables": [{"name": "x"}]})["consultancy_invoicing"]
    assert "in proportion" in pv("consultancy", {"fee_basis": "Fixed fee"})["consultancy_invoicing"]
    assert "time properly spent" in pv("consultancy", {"fee_basis": "Day rate"})["consultancy_invoicing"]
    assert pv("consultancy", {})["consultancy_invoicing"] == "The Consultant shall invoice the Company monthly in arrears."


@pytest.mark.parametrize("text,held", [
    ("This is a test", True), ("TBD", True), ("lorem ipsum dolor sit amet", True), ("testing", True),
    (None, True), ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen", True),
    (("Build and test the customer portal, including research, design, development, user acceptance "
      "testing and launch support for all regions."), False),
])
def test_placeholder_scope(text, held):
    """Placeholder scope (under 15 words or test/TBD text) is held; a real scope mentioning 'testing' is not."""
    assert paper.placeholder_scope(text) is held


def test_nda_yes_prints_breach_notice_once():
    """personal_data=Yes must not say the breach notice twice: the template's 11.1 sentence is the only one."""
    from app.drafting_templates.service import default_body

    sentence = "notify the other without undue delay of any personal data breach"
    values = pv("nda", {"personal_data": "Yes"})
    text = default_body("nda").format_map(defaultdict(str, values))
    assert text.count(sentence) == 1
    assert text.count("personal data breach") == 1
