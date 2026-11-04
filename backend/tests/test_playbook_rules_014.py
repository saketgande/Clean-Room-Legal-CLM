"""Spec 014: the playbook rules that go with the standard templates.

Failures this guards:
* a rule whose literal phrases flag the very wording our own paper writes (the AI review then redlines
  our own template), or let a risky counterparty wording through;
* the one-phrase-per-line change altering how an old single-phrase rule is read;
* the Customer Agreement playbook (unchanged by decision) drifting.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.playbooks.library import NDA_STANDARD, PLAYBOOK_LIBRARY
from app.playbooks.service import _phrases, evaluate_rules_against_text

_BY_NAME = {p["name"]: p for p in [*PLAYBOOK_LIBRARY, NDA_STANDARD]}
MSA = "Master Services Agreement Playbook"
CONS = "Consultancy Agreement Playbook"
VENDOR = "Vendor Agreement Playbook"
SAAS = "Software / SaaS Agreement Playbook"
SOW = "Statement of Work Playbook"
DPA = "Data Processing Agreement Playbook"
NDA = NDA_STANDARD["name"]

_DP_PASS = ("The Provider shall comply with the Digital Personal Data Protection Act, 2023 and shall notify the "
            "Customer without undue delay, and in any event within twenty-four (24) hours, after becoming aware of "
            "a personal data breach.")
_DP_FLAG = "The Provider shall notify the Customer within 72 hours of a personal data breach."
_LIAB_PASS = ("13. LIMITATION OF LIABILITY. Each Party's total aggregate liability shall not exceed the greater of "
              "the total Fees paid or payable in the twelve (12) months preceding the claim and the total Fees "
              "payable for the first twelve (12) months.")
_GOV_PASS = ("This Agreement is governed by the laws of India. Any dispute shall be finally settled by arbitration "
             "seated in Mumbai; either Party may seek urgent interim relief from any competent court.")
_GOV_FLAG = ("This Agreement is governed by the laws of the State of Delaware and the parties submit to the "
             "exclusive jurisdiction of the courts of Delaware.")
_TAX_PASS = ("The Provider shall issue compliant tax invoices so that the Customer can claim input tax credit. Where "
             "services are imported, the Customer pays GST under reverse charge and the Fees are not increased.")
_TAX_FLAG = "Fees are exclusive of taxes, which the Customer shall pay."
_PAY_PASS = ("The Customer shall pay each valid, undisputed invoice within 45 days. If the Provider is a micro or "
             "small enterprise registered under the Micro, Small and Medium Enterprises Development Act, 2006, "
             "payment is due within 45 days from acceptance.")
_INS_PASS = ("The Provider shall maintain professional indemnity insurance of not less than INR 5 crore per claim "
             "and cyber liability insurance of not less than INR 5 crore per claim.")
_INS_FLAG = "The Provider shall maintain insurance with limits of cover appropriate to the Services."

# (playbook, clause_type) -> (wording that must give no finding, risky wording that must give one)
WORDINGS = {
    (MSA, "limitation_of_liability"): (
        _LIAB_PASS, ("The Customer's liability shall be unlimited, and the Provider's shall not exceed the fees paid "
                    "in the preceding month.")),
    (MSA, "indemnification"): (
        "The Provider shall indemnify the Customer against third-party claims arising from its breach.",
        "The Customer shall defend the Provider against any claim."),
    (MSA, "intellectual_property"): (
        "Intellectual property in the Deliverables is assigned to the Customer for the full term of those rights.",
        "The Provider assigns the copyright in the Deliverables to the Customer."),
    (MSA, "payment_terms"): (
        _PAY_PASS, "The Customer shall pay invoices within 90 days."),
    (MSA, "insurance"): (_INS_PASS, _INS_FLAG),
    (MSA, "service_levels"): (
        "Total service credits in any month shall not exceed fifteen percent (15%) of the monthly Fees.",
        "Service credits are the Customer's sole and exclusive remedy for any failure to meet a Service Level."),
    (MSA, "data_protection"): (
        _DP_PASS + " The Parties expect the Provider to process personal data under this Agreement. They shall sign "
        "a data processing agreement on the Customer's standard template at the same time as this Agreement; it "
        "applies from the Effective Date, forms part of this Agreement and prevails over it on the processing of "
        "personal data, and no personal data is processed before it is signed.", _DP_FLAG),
    (MSA, "governing_law"): (_GOV_PASS, _GOV_FLAG),
    (MSA, "tax_compliance"): (_TAX_PASS, _TAX_FLAG),

    (CONS, "intellectual_property"): (
        ("All intellectual property in the Work Product vests in the Company; section 19(4) of the Copyright Act, "
        "1957 does not apply."),
        "The Consultant grants the Company a licence to use the reports."),
    (CONS, "independent_contractor"): (
        "The Consultant is an independent contractor and not an employee of the Company.",
        "The Consultant shall work at the Company's offices from 9 to 6 under its manager."),
    (CONS, "payment_terms"): (_PAY_PASS, "The Company shall pay within 120 days."),
    (CONS, "termination"): (
        "Either Party may terminate this Agreement on fifteen (15) days' written notice.",
        "This Agreement may not be ended before its term."),
    (CONS, "limitation_of_liability"): (
        ("LIMITATION OF LIABILITY. Each Party's liability shall not exceed the Fees paid or payable under this "
        "Agreement, except for breach of confidentiality, fraud or wilful misconduct."),
        "The Company shall have unlimited liability for any claim."),
    (CONS, "governing_law"): (
        ("This Agreement is governed by the laws of India. The courts at Mumbai have exclusive jurisdiction, and "
        "either Party may seek urgent interim relief from any competent court."),
        "Disputes shall be heard by the courts of competent jurisdiction under the laws of the State of Delaware."),
    (CONS, "insurance"): (_INS_PASS, "The Consultant shall maintain appropriate insurance."),
    (CONS, "data_protection"): (_DP_PASS, _DP_FLAG),
    (CONS, "tax_compliance"): (_TAX_PASS, _TAX_FLAG),
    (CONS, "non_compete"): (
        "During the engagement the Consultant shall not advise a competitor of the Company on the same matter.",
        "The Company agrees not to compete with the Consultant for two years."),

    (VENDOR, "limitation_of_liability"): (
        ("LIMITATION OF LIABILITY. Each Party's liability shall not exceed the greater of the Fees paid in the "
        "preceding twelve months and the Fees payable in the first twelve (12) months of this Agreement."),
        "The Company shall have unlimited liability under this Agreement."),
    (VENDOR, "indemnification"): (
        "The Vendor shall indemnify the Company against any claim by its personnel for wages or statutory dues.",
        "Each party shall bear its own losses."),
    (VENDOR, "warranties"): (
        "The Vendor warrants that the Deliverables conform to the Specification.",
        "The goods are supplied as is."),
    (VENDOR, "compliance"): (
        "The Vendor shall comply with all anti-bribery, sanctions and labour laws.",
        "The Vendor shall comply with law."),
    (VENDOR, "payment_terms"): (_PAY_PASS, "Payment is due within 120 days of invoice."),
    (VENDOR, "data_protection"): (
        _DP_PASS, "The Vendor shall notify the Company within forty-eight (48) hours of any breach."),
    (VENDOR, "delivery_and_acceptance"): (
        ("A Deliverable is accepted only when the Company confirms acceptance in writing; use or payment is not "
        "acceptance."),
        "Goods are deemed accepted on delivery."),
    (VENDOR, "insurance"): (
        "The Vendor shall maintain product liability insurance of not less than INR 1 crore per claim.",
        "The Vendor shall maintain insurance adequate to cover its liabilities."),
    (VENDOR, "governing_law"): (_GOV_PASS, _GOV_FLAG),
    (VENDOR, "tax_compliance"): (_TAX_PASS, _TAX_FLAG),

    (SAAS, "data_security"): (
        ("The Provider shall notify the Customer of any Security Incident without undue delay, and in any event "
        "within twenty-four (24) hours, after becoming aware of it."),
        "The Provider will notify the Customer of a security incident within 72 hours."),
    (SAAS, "data_location"): (
        ("Customer Data is hosted in India. Any other location needs the written approval of the Customer's "
        "privacy function."),
        "Customer Data may be stored in any country in which the Provider or its sub-processors operate."),
    (SAAS, "renewal"): (
        "The Provider shall send a renewal reminder before the cancellation deadline.",
        "The subscription renews automatically each year."),
    (SAAS, "limitation_of_liability"): (
        ("LIMITATION OF LIABILITY. The General Cap applies, and liability for Data Breach Claims is limited to the "
        "Data Breach Cap."),
        "The Customer shall have unlimited liability for any breach."),
    (SAAS, "payment_terms"): (_PAY_PASS, "Fees are payable annually in advance and are non-refundable."),
    (SAAS, "suspension"): (
        "The Provider may suspend the Service only to address an imminent security threat.",
        "The Provider may suspend the Service at any time without notice."),
    (SAAS, "business_continuity"): (
        "The Provider shall maintain and test a business continuity and disaster recovery plan.",
        "The Provider maintains backups."),
    (SAAS, "subprocessors"): (
        "If the Customer objects to a new sub-processor and the objection is not resolved, it may terminate.",
        "The Provider may engage sub-processors at its discretion."),
    (SAAS, "data_use"): (
        "The Provider shall not use Customer Data to train or improve any artificial intelligence model.",
        "The Provider may use Customer Data to improve its products and models."),
    (SAAS, "governing_law"): (_GOV_PASS, _GOV_FLAG),
    (SAAS, "tax_compliance"): (_TAX_PASS, _TAX_FLAG),
    (SAAS, "insurance"): (_INS_PASS, _INS_FLAG),

    (SOW, "fees_and_pricing"): (
        "Time and materials. Total charges shall not exceed the not-to-exceed amount of INR 40,00,000.",
        "The Provider's rates may be increased by up to 10% each quarter."),
    (SOW, "milestone_payments"): (
        "Payment is tied to the Customer's written acceptance of Deliverables, not to calendar dates.",
        "50% of the fee is payable on signature of this SOW."),
    (SOW, "service_levels"): (
        "No Service Levels apply to this SOW. A Service Level may be added only by a signed Change Order.",
        "The Provider will use reasonable efforts to keep the platform available."),
    (SOW, "payment_terms"): (
        "The Customer shall pay valid invoices within the period set out in the Master Agreement.",
        "The Customer shall pay invoices within 30 days."),
    (SOW, "governing_law"): (
        "Disputes under this SOW are resolved in the manner set out in the Master Agreement.",
        "This SOW is governed by the laws of England."),

    (DPA, "sub_processors"): (
        "The Processor may engage a Sub-processor only with the Controller's prior written authorisation.",
        "The supplier may use other companies to help it."),
    (DPA, "breach_notification"): (
        ("The Processor shall notify the Controller of any personal data breach without undue delay and within "
        "twenty-four (24) hours."),
        "The Processor shall notify the Controller of a personal data breach within 72 hours."),
    (DPA, "international_transfers"): (
        ("The Processor shall not transfer Personal Data outside India without the Controller's prior written "
        "authorisation."),
        "The Processor may transfer Personal Data to any country without notice."),
    (DPA, "audit"): (
        "The Controller may audit the Processor's compliance on reasonable notice.",
        "The Processor will provide a summary of its controls."),
    (DPA, "data_subject_rights"): (
        "The Processor shall forward any Data Principal request to the Controller within two business days.",
        "The Processor will help as needed."),
    (DPA, "return_or_deletion"): (
        "At the Controller's choice the Processor shall return or erase all Personal Data and certify it.",
        "The Processor may retain Personal Data for its own analytics."),
    (DPA, "liability"): (
        "The Processor's total liability shall not exceed the greater of three (3) times the fees and INR 5 crore.",
        "The Processor's liability is limited as set out in the Principal Agreement."),
    (DPA, "term"): (
        ("This Agreement continues until the later of the end of the Principal Agreement and the return or deletion "
        "of all Personal Data."),
        "This Agreement continues for one year."),
    (DPA, "processing_description"): (
        "The subject matter, duration and nature of the Processing are described in Annex 1.",
        "The Processor processes data as it sees fit."),
    (DPA, "no_own_use"): (
        "The Processor shall not process Personal Data for its own purposes.",
        "The Processor processes customer data."),
    (DPA, "confidentiality_of_personnel"): (
        "Personnel with access to Personal Data are bound by confidentiality and trained.",
        "Personnel with access to Personal Data are trained."),
    (DPA, "assistance"): (
        "The Processor shall assist with any data protection impact assessment.",
        "The Processor will cooperate with the Controller."),
    (DPA, "children_data"): (
        "The Processor shall not undertake behavioural monitoring directed at children.",
        "The Processor processes customer data."),
    (DPA, "governing_law"): (
        "This Agreement is governed by the law that governs the Principal Agreement.",
        "This Agreement shall be governed by and construed in accordance with the laws of Delaware."),

    (NDA, "exclusions_from_confidential_information"): (
        "Information is excluded only if the Receiving Party can show, by its written records, that it is public.",
        "Information is excluded if the Receiving Party believes it was already known to it."),
    (NDA, "permitted_use_and_standard_of_care"): (
        ("The Receiving Party shall use Confidential Information solely for the Purpose. It shall not use "
        "Confidential Information to train, fine-tune, test, or otherwise develop any artificial intelligence or "
        "machine learning model, algorithm, or system."),
        "The Recipient may use the Confidential Information for any purpose whatsoever."),
    (NDA, "compelled_disclosure_and_legal_process"): (
        ("The Receiving Party shall: (a) provide prompt written notice to the Disclosing Party prior to any such "
        "disclosure to the extent permitted by law; (b) cooperate fully with the Disclosing Party in seeking a "
        "protective order, confidential treatment, or other appropriate relief; and (c) disclose only that portion "
        "of the Confidential Information that is legally required to be disclosed. No notice is needed for a "
        "routine regulator examination."),
        "Any compelled disclosure is deemed a waiver of all confidentiality obligations under this Agreement."),
    (NDA, "return_or_destruction_of_information"): (
        "Within ten (10) business days the Receiving Party shall return or destroy all Confidential Information.",
        "The Recipient may retain copies for its internal business purposes."),
    (NDA, "term_and_duration_of_confidentiality"): (
        ("This Agreement continues for two (2) years; confidentiality obligations continue for five (5) years after "
        "it ends. A trade secret is protected for so long as it remains a trade secret."),
        "All obligations under this Agreement expire upon termination of this Agreement."),
    (NDA, "governing_law_and_jurisdiction"): (
        ("The courts at Mumbai have exclusive jurisdiction, but either Party may seek urgent interim relief from "
        "any competent court."),
        "Each party submits to the exclusive jurisdiction of the courts of the State of New York."),
    (NDA, "remedies_and_injunctive_relief"): (
        "Either Party may seek injunctive or other equitable relief without posting a bond.",
        "Remedies for breach are limited to monetary damages."),
    (NDA, "non_compete_and_exclusivity"): (
        "Nothing in this Agreement restricts either Party from independently developing similar products.",
        "During the term the Recipient shall not compete with the Discloser in India."),
    (NDA, "indemnity"): (
        "Remedies for breach are damages and injunctive relief.",
        "The Recipient shall indemnify and hold harmless the Discloser against all losses."),
    (NDA, "residuals"): (
        "No right to use information retained in the unaided memory of any person is granted.",
        "Either party may use Residuals, meaning information retained in unaided memory."),
    (NDA, "personal_data"): (
        ("Personal data is handled under the Digital Personal Data Protection Act, 2023 with reasonable safeguards, "
        "and any personal data breach is notified without undue delay."),
        "Personal data shall be handled in accordance with applicable law."),
    (NDA, "assignment"): (
        ("Neither Party may assign this Agreement without consent, except to an Affiliate or a successor to its "
        "business."),
        "Neither party may assign this Agreement without consent."),
}

# Rules copied unchanged from the previous library, or judged by the AI alone (no usable phrase).
_NO_WORDING = {
    (MSA, "termination"), (MSA, "confidentiality"), (CONS, "confidentiality"), (CONS, "conflicts_of_interest"),
    (CONS, "non_solicitation"), (VENDOR, "subcontracting"), (VENDOR, "audit_rights"), (SAAS, "service_levels"),
    (SAAS, "exit_and_data_return"), (SAAS, "price_increases"), (SOW, "scope_and_deliverables"),
    (SOW, "acceptance"), (SOW, "change_control"), (SOW, "order_of_precedence"), (SOW, "key_personnel"),
    (DPA, "processing_instructions"), (DPA, "security_measures"),
    (NDA, "definition_of_confidential_information"),
}


def _findings(playbook: str, clause_type: str, text: str):
    spec = next(r for r in _BY_NAME[playbook]["rules"] if r["clause_type"] == clause_type)
    return evaluate_rules_against_text(rules=[SimpleNamespace(id="r", **spec)], text=text)


@pytest.mark.parametrize(("playbook", "clause_type"), list(WORDINGS))
def test_wording_our_paper_writes_is_not_flagged(playbook, clause_type):
    """A rule that flags the wording our own template writes sends our own paper back as a redline."""
    assert _findings(playbook, clause_type, WORDINGS[playbook, clause_type][0]) == []


@pytest.mark.parametrize(("playbook", "clause_type"), list(WORDINGS))
def test_risky_counterparty_wording_is_flagged(playbook, clause_type):
    """A rule loose enough to pass the counterparty's riskier wording protects nothing."""
    assert _findings(playbook, clause_type, WORDINGS[playbook, clause_type][1]) != []


def test_every_rule_has_wordings_or_is_listed_as_unchanged():
    """A new or changed rule added without its pass/flag pair would ship unchecked."""
    have = {(p["name"], r["clause_type"]) for p in _BY_NAME.values() for r in p["rules"]
            if p["name"] != "Customer Agreement Playbook"}
    assert have == set(WORDINGS) | _NO_WORDING


@pytest.mark.parametrize(("name", "count"), [
    (MSA, 11), (CONS, 13), (VENDOR, 12), (SAAS, 15), (SOW, 10), (DPA, 16), (NDA, 13),
])
def test_rule_counts(name, count):
    """A rule dropped or doubled in the library would silently change what every review checks."""
    assert len(_BY_NAME[name]["rules"]) == count
    clauses = [r["clause_type"] for r in _BY_NAME[name]["rules"]]
    assert len(set(clauses)) == len(clauses)


def test_customer_agreement_playbook_is_unchanged():
    """The Customer Agreement playbook was reviewed and kept as is (FR-7); any edit needs a new review."""
    rules = _BY_NAME["Customer Agreement Playbook"]["rules"]
    digest = hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()
    assert digest == "deb3570f1a114a1cbe5ad821a699e017e5e8d5bc73acbab18bf598eec44c6dd3"


def test_nda_standard_is_not_seeded_for_new_organisations():
    """NDAs use the org's own playbook; seeding ours would pick the wrong rules for existing NDA contracts."""
    assert NDA_STANDARD not in PLAYBOOK_LIBRARY


def test_phrases_one_per_line_and_single_phrase_unchanged():
    """A single-line value must read exactly as before; blank lines and stray spaces must not become phrases."""
    assert _phrases(None) == [] and _phrases("") == [] and _phrases(" \n \n") == []
    assert _phrases("  undisputed   invoices ") == ["undisputed invoices"]
    assert _phrases("a b\n\n c \n") == ["a b", "c"]


def _rule(**kw):
    base = dict(id="r", clause_type="x", risk_level="high", approval_required=False, preferred_position="p",
                fallback_position=None, sample_clause=None, required_language=None, prohibited_language=None)
    return SimpleNamespace(**{**base, **kw})


def test_required_lines_flag_any_missing_one_with_one_finding():
    """'Any line missing' is one finding naming the first missing line, not one per missing line."""
    rule = _rule(required_language="alpha\nbeta\ngamma")
    assert evaluate_rules_against_text(rules=[rule], text="alpha beta gamma") == []
    found = evaluate_rules_against_text(rules=[rule], text="alpha only")
    assert len(found) == 1 and found[0].citation["required_language"] == "beta"


def test_prohibited_lines_flag_the_first_found_with_one_finding():
    """Two prohibited lines both present are still one finding, quoted from the first line that matches."""
    rule = _rule(prohibited_language="first bad\nsecond bad")
    found = evaluate_rules_against_text(rules=[rule], text="x second bad y first bad")
    assert len(found) == 1 and found[0].original_text == "first bad"
    assert evaluate_rules_against_text(rules=[rule], text="all fine") == []


def test_single_phrase_rule_behaves_as_before():
    """A stored rule with one multi-word phrase still matches across whitespace and case, one finding each way."""
    rule = _rule(required_language="Undisputed  Invoices", prohibited_language="payable in advance")
    assert evaluate_rules_against_text(rules=[rule], text="valid undisputed\ninvoices only") == []
    assert len(evaluate_rules_against_text(rules=[rule], text="PAYABLE IN ADVANCE; undisputed invoices")) == 1
    assert len(evaluate_rules_against_text(rules=[rule], text="nothing")) == 1
