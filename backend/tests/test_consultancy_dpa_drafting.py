"""A Consultancy agreement or DPA drafted from a request is filled in, not left to fill in (spec 008).

Failures this guards:
* a bracket, brace or "delete as appropriate" left in the shipped Consultancy or DPA wording, or in a draft
  made for any fee basis, with or without deliverables, with or without data leaving India;
* a fee that reads as a choice between bases, is a cent out (a float), or can be paid in advance or above
  the stated total without a signed amendment;
* the Company contact written as someone who directs the Consultant, or a key individual who can be
  replaced without our consent;
* a DPA annex that lets the Processor process other people's data, other kinds of data, or use a
  sub-processor nobody authorised;
* the Templates page's example (names, deliverables, countries, sub-processors) leaking into a draft made
  for a real request, or Apply writing it into our own paper;
* a request that lacks an answer drafted from the example instead of from nothing;
* any other template drafting differently because ten fields were added, or the exam's documents changing
  under it because the shipped wording did.

The pure tests call the fills and the render directly. The few that need Postgres run in one rolled-back
transaction through the real upload pipeline with an in-memory store; the AI answer is a fake.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.ai.schemas import PlaybookReviewOutput
from app.contracts.blanks import find_blanks
from app.core.database import engine
from app.drafting_templates import checks
from app.drafting_templates.service import (
    PLACEHOLDERS,
    SAMPLE_VALUES,
    TEMPLATES,
    DraftingTemplateService,
    check_body,
    default_body,
    field_uses,
    render_sample,
)
from app.intake import consultancy, dpa, drafting, paper, sow
from app.intake.agreement_forms import form_def
from eval.exam import build as exam
from tests.test_template_check_jobs import _ai, _deviation, _FakeReview, _ready, _request, _run
from tests.test_template_drafting_record import _Disk, _filed, _service

CONSULTANCY_PB = "Consultancy Agreement Playbook"
DPA_PB = "Data Processing Agreement Playbook"
COMPANY, COUNTERPARTY = "Aegis Pharma Ltd", "Acme Advisory LLP"
NEW_FIELDS = (*consultancy.FIELDS, *dpa.FIELDS)
EXAM_DIR = Path(__file__).resolve().parents[1] / "eval" / "exam"


@pytest.fixture
def db():
    """Commits inside the drafting pipeline are savepoints of one outer transaction, so they behave as in
    production and still leave nothing behind."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def dispatched(monkeypatch):
    """The AI jobs a draft queues, swallowed: a draft here must not reach a broker."""
    from app.contract_files import service as contract_files_service

    seen: list[str] = []
    monkeypatch.setattr(contract_files_service, "dispatch_job", lambda db, *, job: seen.append(job.job_type) or job)
    return seen


# --- the example requests of the acceptance criteria --------------------------------------------


def _consultancy(basis: str = "Fixed fee", **over) -> dict:
    """The Consultancy request the acceptance criteria describe, as the filing stores it."""
    fv = {
        "request_form": "new_agreement", "agreement_type": "Consultancy", "paper": "Our template",
        "counterparty": COUNTERPARTY, "start_date": "2026-11-01", "term": "Fixed end date",
        "end_date": "2027-04-30", "payment_terms": "30 days", "value": 1800000, "currency": "INR",
        "scope": ("Advise on the design of the new quality-management process, including a review of current "
                  "practice, a gap analysis, and recommendations for the quality team."),
        "consultancy_deliverables": [{"name": "Current-state review", "due": "2026-12-15"},
                                     {"name": "Process design report", "due": "2027-03-15"}],
        "fee_basis": basis, "key_individuals": [{"name": "Anita Rao"}, {"name": "Dev Menon"}],
        "company_contact_name": "Ravi Shah", "company_contact_role": "Head of Quality",
    }
    if basis in ("Day rate", "Hourly rate"):
        fv["fee_rate"] = {"Day rate": 25000, "Hourly rate": 3500}[basis]
    return {**fv, **over}


def _advice_only(**over) -> dict:
    fv = {k: v for k, v in _consultancy("Day rate").items() if k != "consultancy_deliverables"}
    return {**fv, "advice_only": [consultancy.ADVICE_TICK], **over}


def _dpa(**over) -> dict:
    """The DPA request the acceptance criteria describe: health data, data leaving India, two sub-processors."""
    fv = {
        "request_form": "dpa", "paper": "Our template", "counterparty": COUNTERPARTY,
        "our_role": "Controller: we decide how the data is used",
        "processing_purpose": "Hosting and support of the clinical-trial site portal.",
        "data_types": ["Contact and identity", "Health or other special category"],
        "data_subjects": ["Customers", "Patients or trial subjects"],
        "transfer": "Yes", "transfer_countries": [{"country": "Singapore"}, {"country": "Germany"}],
        "subprocessors": "Yes",
        "subprocessor_rows": [
            {"name": "CloudHost Pte Ltd", "country": "Singapore", "processing": "Hosting of the portal database"},
            {"name": "SupportDesk GmbH", "country": "Germany", "processing": "Second-line support"}],
    }
    return {**fv, **over}


def _plain_dpa(**over) -> dict:
    """One kind of people, one kind of data, nothing leaving India, no sub-processors."""
    return _dpa(data_types=["Contact and identity"], data_subjects=["Customers"], transfer="No",
                transfer_countries=None, subprocessors="No", subprocessor_rows=None, **over)


def _draft(key: str, fv: dict, **kw) -> str:
    return drafting.render_document(key, company=COMPANY, counterparty=COUNTERPARTY, effective="2026-11-01",
                                    fields=fv, **kw)


def _schedule(text: str) -> str:
    return text[text.index("ENGAGEMENT SCHEDULE"):text.index("IN WITNESS WHEREOF")]


def _annexes(text: str) -> str:
    """Annex 1 and Annex 3, the two places the DPA's answers go (Annex 2 is the security measures)."""
    return text[text.index("ANNEX 1"):text.index("ANNEX 2")] + text[text.index("ANNEX 3"):]


def _clean(text: str) -> None:
    assert find_blanks(text) == []
    assert "{" not in text and "}" not in text
    assert "delete as appropriate" not in text.lower()


# --- the shipped wording (AC-1, AC-7) -----------------------------------------------------------


@pytest.mark.parametrize("key", ["consultancy", "dpa"])
def test_the_shipped_wording_has_no_blank_bracket(key):
    """AC-1 / AC-7: a bracket in the template is a blank that reaches the counterparty and stops the contract
    at the signature gate. None is left, by blank and by occurrence, and no instruction to the reader."""
    body = default_body(key)
    assert find_blanks(body) == []
    assert "[" not in body and "]" not in body
    assert "delete as appropriate" not in body.lower()
    check_body(body)  # the strict save rule accepts it as written


@pytest.mark.parametrize("key", ["consultancy", "dpa"])
def test_a_request_with_no_answers_still_drafts_no_blank_or_brace(key):
    """FR-2 / FR-7: whatever the request left out, a place with no answer is empty, never a bracket or an
    unfilled code. (A held request is never drafted at all; this is the floor under that.)"""
    _clean(_draft(key, {}))


@pytest.mark.parametrize("fv", [_consultancy("Fixed fee"), _consultancy("Day rate"), _consultancy("Hourly rate"),
                                _advice_only()], ids=["fixed", "day rate", "hourly rate", "advice only"])
def test_a_drafted_consultancy_agreement_has_no_blank_whatever_the_fee_basis_or_deliverables(fv):
    """AC-2: each fee basis, with and without deliverables."""
    _clean(_draft("consultancy", fv))


@pytest.mark.parametrize("fv", [_dpa(), _plain_dpa(), _dpa(transfer="No"), _dpa(subprocessors="No"),
                                _dpa(data_types=["Financial"])],
                         ids=["example", "plain", "no transfer", "no sub-processors", "one non-special kind"])
def test_a_drafted_dpa_has_no_blank_whatever_the_answers(fv):
    """AC-8: data leaving India or not, sub-processors or none, special category data or not."""
    _clean(_draft("dpa", fv))


# --- Consultancy: the five places (AC-3, AC-4, AC-5) --------------------------------------------


def test_the_schedule_holds_the_requests_answers_in_the_five_places():
    """AC-3 / AC-4: the scope as written, D1 and D2 in the order entered with long-form dates, the fee, the
    key individuals once each and the contact with the role."""
    schedule = _schedule(_draft("consultancy", _consultancy("Day rate")))
    assert "Services: Advise on the design of the new quality-management process, including" in schedule
    assert ("Deliverables and timetable:\n\n"
            "D1. Current-state review. Due date: 15 December 2026.\n\n"
            "D2. Process design report. Due date: 15 March 2027.\n\n") in schedule
    assert "Consultant's key individual(s): Anita Rao and Dev Menon. The Consultant shall not replace" in schedule
    assert "Company contact: Ravi Shah, Head of Quality. The Company contact is" in schedule


@pytest.mark.parametrize(("basis", "statement", "cap"), [
    ("Fixed fee", "The Fees are a fixed fee of INR 1,800,000.00 for the Services.", "INR 1,800,000.00"),
    ("Day rate", ("The Fees are charged at a day rate of INR 25,000.00 per day for time properly spent on the "
                  "Services. A day is eight (8) hours; part days are charged pro rata. Travel time is not "
                  "chargeable unless agreed in writing."), "INR 1,800,000.00"),
    ("Hourly rate", ("The Fees are charged at an hourly rate of INR 3,500.00 per hour for time properly spent "
                     "on the Services. Part hours are charged pro rata. Travel time is not chargeable unless "
                     "agreed in writing."), "INR 1,800,000.00"),
])
def test_the_fee_is_one_statement_with_the_total_as_its_ceiling(basis, statement, cap):
    """AC-5: the requester's basis, never the bases as alternatives; the total value is the fixed fee or the
    amount a rate may not exceed, and invoicing and payment follow Clauses 6.2 to 6.4."""
    schedule = _schedule(_draft("consultancy", _consultancy(basis)))
    assert (f"Fees: {statement} The Fees shall not exceed {cap} except by a written amendment signed by "
            "both Parties. The Consultant shall invoice the Fees, and the Company shall pay them, in "
            "accordance with Clauses 6.2 to 6.4.") in schedule
    others = {"Fixed fee", "Day rate", "Hourly rate"} - {basis}
    assert not any(word in schedule for word in ("e.g.", " or a daily", "delete")) and not any(
        f"{o.lower()} of" in schedule for o in others)


def test_advice_only_states_that_no_deliverables_are_specified():
    """AC-4: a tick, not an empty table: the schedule says the output is the Clause 2.2 reports and advice,
    which Clause 8 vests in the Company. No D1 appears even if rows were stored."""
    fv = _advice_only(consultancy_deliverables=[{"name": "Left over", "due": "2026-12-15"}])
    schedule = _schedule(_draft("consultancy", fv))
    assert ("Deliverables and timetable:\n\nNo separate deliverables are specified. The Consultant's output is "
            "the reports and advice it gives under Clause 2.2, all of which are Work Product.\n\n") in schedule
    assert "D1." not in schedule and "Left over" not in schedule


def test_the_schedule_keeps_the_playbook_positions_whatever_the_basis():
    """AC-6 / AC-13: nothing in the five places is payable in advance or unlimited; the only "direct or
    supervise" is the Company contact's negated sentence; a fee above the total needs a signed amendment and
    a key individual is not replaced without prior written consent. Clause 6.3 and the 20% advance cap of
    6.5 are the template's own and unchanged."""
    for basis in ("Fixed fee", "Day rate", "Hourly rate"):
        text = _draft("consultancy", _consultancy(basis))
        schedule = _schedule(text)
        lowered = schedule.lower()
        assert "in advance" not in lowered and "unlimited" not in lowered and "payable in advance" not in lowered
        assert lowered.count("direct or supervise") == 1 and "does not direct or supervise" in lowered
        assert "a written amendment signed by both Parties" in schedule
        assert "shall not replace any key individual named here without the Company's prior written consent" in schedule
        assert "valid, undisputed invoices within 30 days of receipt" in text
        assert "Any advance payment shall not exceed twenty percent (20%) of the total Fees" in text


# --- Consultancy: the fills ---------------------------------------------------------------------


def test_services_keep_their_line_breaks_and_lose_surrounding_space():
    """FR-3 row 1: the scope is the Consultant's performance obligation, verbatim."""
    schedule = _schedule(_draft("consultancy", _consultancy(scope="  Phase one.\r\nPhase two.  ")))
    assert "Services: Phase one.\nPhase two.\n" in schedule


def test_deliverables_are_numbered_in_the_order_entered_not_by_date():
    """FR-11: D-numbers follow the order entered, so a later date entered first is still D1."""
    rows = [{"name": "Final report", "due": "2027-03-15"}, {"name": "Interim note?", "due": "2026-12-15"}]
    assert consultancy.consultancy_values(_consultancy(consultancy_deliverables=rows))[
        "consultancy_deliverables"] == (
            "D1. Final report. Due date: 15 March 2027.\n\nD2. Interim note? Due date: 15 December 2026.")


@pytest.mark.parametrize(("names", "expected"), [
    ([], ""),
    (["Anita Rao"], "Anita Rao."),
    (["Anita Rao", "Dev Menon"], "Anita Rao and Dev Menon."),
    (["Anita Rao", "Dev Menon", "Zoya Khan"], "Anita Rao, Dev Menon and Zoya Khan."),
    (["Anita  Rao ", " Dr. Dev Menon"], "Anita Rao and Dr. Dev Menon."),
    (["Anita Rao", "Dr. Dev M."], "Anita Rao and Dr. Dev M."),
])
def test_key_individuals_are_named_once_in_the_order_entered(names, expected):
    """FR-3 row 4: a list the way a contract writes it; no double full stop after an initial."""
    fv = _consultancy(key_individuals=[{"name": n} for n in names])
    assert consultancy.consultancy_values(fv)["key_individuals"] == expected


@pytest.mark.parametrize(("name", "role", "expected"), [
    ("Ravi Shah", "Head of Quality", "Ravi Shah, Head of Quality."),
    ("  Ravi   Shah ", "Head of Quality (acting)", "Ravi Shah, Head of Quality (acting)."),
    ("Ravi Shah", "Head of Quality.", "Ravi Shah, Head of Quality."),
    ("Ravi Shah", "", "Ravi Shah."),
    ("", "Head of Quality", ""),
])
def test_the_company_contact_is_a_name_and_a_role(name, role, expected):
    """FR-3 row 5: the name and the role; with no name there is no contact to name, whatever the role."""
    fv = _consultancy(company_contact_name=name, company_contact_role=role)
    assert consultancy.consultancy_values(fv)["company_contact"] == expected


@pytest.mark.parametrize(("basis", "rate", "expected"), [
    ("Day rate", 25000, "INR 25,000.00 per day"),
    ("Day rate", 25000.5, "INR 25,000.50 per day"),
    ("Day rate", "25000.505", "INR 25,000.51 per day"),
    ("Day rate", 0.3, "INR 0.30 per day"),
    ("Hourly rate", "1,234,567.891", "INR 1,234,567.89 per hour"),
    ("Hourly rate", 3500, "INR 3,500.00 per hour"),
])
def test_a_rate_is_exact_money_rounded_half_up_to_the_cent(basis, rate, expected):
    """FR-11 / FR-16: a float anywhere would print 0.30 as 0.299999... or lose a cent on 25,000.505."""
    assert expected in consultancy.consultancy_values(_consultancy(basis, fee_rate=rate))["consultancy_fee_basis"]


def test_the_currency_is_the_requests_own_else_the_default():
    from app.core.config import settings

    assert "USD 25,000.00" in consultancy.consultancy_values(
        _consultancy("Day rate", currency="USD"))["consultancy_fee_basis"]
    assert f"{settings.default_currency} 25,000.00" in consultancy.consultancy_values(
        _consultancy("Day rate", currency=None))["consultancy_fee_basis"]


@pytest.mark.parametrize("fv", [
    _consultancy("Day rate", fee_rate=None), _consultancy("Hourly rate", fee_rate="abc"),
    _consultancy("Fixed fee", value=None), _consultancy("Weekly rate"), _consultancy(fee_basis=None),
], ids=["no rate", "rate not a number", "no total", "unknown basis", "no basis"])
def test_a_fee_the_answers_cannot_state_is_empty_never_invented(fv):
    """FR-43 / FR-34: a missing figure fills with nothing (the hold stops this draft in practice); it is
    never replaced by the example's rate or by a guess."""
    assert consultancy.consultancy_values(fv)["consultancy_fee_basis"] == ""


def test_a_fixed_fee_ignores_a_stale_rate():
    """FR-16: a rate that is still stored after the basis became Fixed fee must not reach the fee."""
    statement = consultancy.consultancy_values(_consultancy("Fixed fee", fee_rate=99999))["consultancy_fee_basis"]
    assert statement == "The Fees are a fixed fee of INR 1,800,000.00 for the Services."


def test_the_consultancy_record_counts_what_the_draft_was_filled_from():
    """FR-49: rows (0 for advice only), the fee basis (null when unanswered) and the key individuals."""
    assert consultancy.record_facts(_consultancy("Fixed fee")) == {
        "deliverable_rows": 2, "fee_basis": "Fixed fee", "key_individuals": 2}
    assert consultancy.record_facts(_advice_only())["deliverable_rows"] == 0
    assert consultancy.record_facts({}) == {"deliverable_rows": 0, "fee_basis": None, "key_individuals": 0}


def test_the_hold_message_names_the_missing_answers_in_form_order():
    """FR-43: each label in double quotes, joined the way a sentence lists them."""
    labels = ["Deliverables", "How is the consultant paid?", "Consultant's key individuals",
              "Company contact: name", "Company contact: role"]
    assert consultancy.held_message(labels) == (
        'Enter these answers on the request first: "Deliverables", "How is the consultant paid?", '
        '"Consultant\'s key individuals", "Company contact: name" and "Company contact: role". The Engagement '
        "Schedule of the Consultancy draft is made from them, and this request does not have them yet.")
    assert consultancy.held_message(["A"]).startswith('Enter these answers on the request first: "A". The')
    assert consultancy.held_message(["A", "B"]).count('first: "A" and "B". The') == 1
    assert dpa.held_message(["A", "B", "C"]) == (
        'Enter these answers on the request first: "A", "B" and "C". Annex 1 and Annex 3 of the DPA draft are '
        "made from them, and this request does not have them yet.")


# --- DPA: the six places (AC-9 to AC-12) --------------------------------------------------------


def test_annex_1_and_3_hold_the_requests_answers():
    """AC-9 to AC-12: the purpose as written, the ticked people and kinds in the form's order and no other, the
    special category statement, India then the listed countries, and each sub-processor in order."""
    text = _annexes(_draft("dpa", _dpa()))
    assert ("\n\nHosting and support of the clinical-trial site portal.\n\n"
            "Categories of Data Subjects: Customers; Patients or trial subjects. The Processor shall not process "
            "Personal Data of any other category of Data Subject.") in text
    assert ("Types of Personal Data: names, contact details and identification data; health data and other "
            "special category data. The Processor shall not process any other type of Personal Data.") in text
    assert f"Sensitive or special category Personal Data (if any): {dpa.SPECIAL_YES}\n" in text
    assert "Processing locations: India, Singapore and Germany. Personal Data is processed" in text
    assert ("(name, location, and Processing performed):\n\n"
            "CloudHost Pte Ltd (Singapore): Hosting of the portal database.\n\n"
            "SupportDesk GmbH (Germany): Second-line support.\n\n"
            "No Sub-processor other than any listed above is authorised") in text


def test_a_dpa_with_nothing_leaving_india_and_no_sub_processors_says_so():
    """AC-10 / AC-12: India only, no special category data, and "None." with the authorisation sentence still
    in force: a Sub-processor may be engaged later only with prior specific written authorisation."""
    text = _annexes(_draft("dpa", _plain_dpa()))
    assert "Categories of Data Subjects: Customers. The Processor" in text
    assert "Types of Personal Data: names, contact details and identification data. The Processor" in text
    assert f"Sensitive or special category Personal Data (if any): {dpa.SPECIAL_NO}\n" in text
    assert "Processing locations: India. Personal Data is processed and accessed only in the countries" in text
    assert "(name, location, and Processing performed):\n\nNone.\n\nNo Sub-processor other than" in text
    assert "only with the Controller's prior specific written authorisation under Section 6.1" in text


def test_the_annexes_keep_the_playbook_limits_and_the_section_9_and_6_1_sentences():
    """AC-9 to AC-13: processing only on documented instructions and for the stated purpose, only the listed
    people, data, countries and sub-processors, and any transfer outside India only on Section 9's conditions,
    whatever the answers."""
    for fv in (_dpa(), _plain_dpa()):
        text = _annexes(_draft("dpa", fv))
        for words in (
            ("shall process Personal Data only on the Controller's documented instructions and only for the "
             "purpose stated below"),
            "consists only of the operations described in Section 1.3",
            "shall not process Personal Data of any other category of Data Subject",
            "shall not process any other type of Personal Data",
            "processed and accessed only in the countries listed in this entry",
            "in any country outside India is permitted only on the conditions of Section 9",
            "only with the Controller's prior specific written authorisation under Section 6.1",
            "A Sub-processor located outside India may process Personal Data only on the conditions of Section 9",
        ):
            assert words in text, words
            assert words in default_body("dpa")  # the template's own wording: visible, editable, checked


@pytest.mark.parametrize(("kinds", "yes"), [
    (["Contact and identity"], False), (["Employee / HR", "Financial"], False),
    (["Health or other special category"], True), (["Financial", "Health or other special category"], True),
])
def test_special_category_data_follows_the_kinds_ticked_and_nothing_else(kinds, yes):
    """A-7 / AC-11: stated as processed only when the health / special category kind is ticked, however the
    purpose reads; stated as none when other kinds are ticked."""
    values = dpa.dpa_values(_dpa(data_types=kinds, processing_purpose="Anything, including health records."))
    assert values["special_category_data"] == (dpa.SPECIAL_YES if yes else dpa.SPECIAL_NO)
    assert dpa.record_facts(_dpa(data_types=kinds))["special_category_data"] is yes


def test_each_kind_of_data_is_described_in_its_own_words_in_the_order_stored():
    values = dpa.dpa_values(_dpa(data_types=["Financial", "Employee / HR", "Financial", "Contact and identity"]))
    assert values["personal_data_types"] == (
        "financial and transaction data; employment and HR records; names, contact details and identification data.")


@pytest.mark.parametrize(("subjects", "kinds", "expected"), [
    ("Customers", "Employee / HR", ("Customers.", "employment and HR records.")),
    ("Several", "Several of these", ("", "")),
    (["Employees", "Suppliers", "Employees"], ["Financial"], ("Employees; Suppliers.", "financial and transaction data.")),
    (None, None, ("", "")),
], ids=["an older single choice", "an older Several", "ticks listed once", "unanswered"])
def test_an_older_single_choice_is_one_tick_and_several_says_nothing(subjects, kinds, expected):
    """FR-41: a request filed before the form let people tick several keeps its answer; "Several" names no
    category, so it fills nothing (and the hold, not this fill, asks for the answer)."""
    values = dpa.dpa_values(_dpa(data_subjects=subjects, data_types=kinds))
    assert (values["data_subject_categories"], values["personal_data_types"]) == expected
    if expected == ("", ""):
        assert values["special_category_data"] == ""


def test_a_follow_up_answer_that_no_longer_applies_is_not_drafted():
    """FR-29: countries still stored after "Will data leave India?" became No, or sub-processors after the
    answer became No, are not in the draft: the annex says India only and None."""
    values = dpa.dpa_values(_dpa(transfer="No", subprocessors="No"))
    assert (values["processing_locations"], values["authorised_subprocessors"]) == ("India.", "None.")
    assert dpa.record_facts(_dpa(transfer="No", subprocessors="No"))["countries_outside_india"] == 0
    # Yes with nothing entered fills nothing: it is never "India." or "None." by default.
    empty = dpa.dpa_values(_dpa(transfer_countries=[], subprocessor_rows=[]))
    assert (empty["processing_locations"], empty["authorised_subprocessors"]) == ("", "")


def test_the_purpose_keeps_its_line_breaks_and_loses_surrounding_space():
    values = dpa.dpa_values(_dpa(processing_purpose="\n  Hosting.\r\nSupport.  "))
    assert values["processing_purpose"] == "Hosting.\nSupport."


def test_the_dpa_record_counts_what_the_draft_was_filled_from():
    """FR-49: countries outside India, sub-processors and whether special category data is stated."""
    assert dpa.record_facts(_dpa()) == {
        "countries_outside_india": 2, "subprocessors": 2, "special_category_data": True}
    assert dpa.record_facts(_plain_dpa()) == {
        "countries_outside_india": 0, "subprocessors": 0, "special_category_data": False}


def test_the_processor_strings_are_the_forms_option_and_say_nothing_about_who_prepares_the_paper():
    """D-11 / FR-53: the role the refusal keys on is the form's own option text (a reworded option would
    silently stop the rule), and the note names no one as the author of the other paper."""
    (role,) = [f for f in form_def("dpa")["fields"] if f["key"] == "our_role"]
    assert dpa.PROCESSOR_ROLE in role["options"]
    assert dpa.PROCESSOR_NOTE == (
        "No draft was made: this request says we are the Processor, and our DPA template is written for the "
        "Controller. Attach the other side's DPA (or the paper to use) before the review.")
    assert "prepare" not in dpa.PROCESSOR_NOTE.lower() and "counterparty" not in dpa.PROCESSOR_NOTE.lower()


def test_the_frozen_sentences_a_draft_and_a_hold_use_are_the_ones_the_plan_fixed():
    """FR-8 row 4 / FR-43: the special category sentences go into a contract and the free-text message goes
    to a person; a reworded constant would change a signed paper or a message the tests of later tasks pin."""
    assert dpa.SPECIAL_YES == (
        "Special category Personal Data is processed: health data and other special category data. The "
        "Processor shall process it only for the purpose stated in this Annex 1 and shall apply the measures "
        "in Annex 2 to it.")
    assert dpa.SPECIAL_NO == "None. No special category Personal Data is processed under this Agreement."
    assert dpa.PROCESSOR_TITLE == "No draft made: the request says we are the Processor"
    assert dpa.FREE_TEXT_HELD == (
        "File this request on the Data Processing Agreement form first. It came in by email or chat, so it has "
        "none of the form's answers, and Annex 1 and Annex 3 of the DPA draft are made from them: whose data it "
        "is, what kinds of data, where it is processed and which sub-processors are used.")
    assert consultancy.ADVICE_ONLY == (
        "No separate deliverables are specified. The Consultant's output is the reports and advice it gives "
        "under Clause 2.2, all of which are Work Product.")


# --- example content stays on the Templates page (FR-33, FR-34, AC-37, AC-38) -------------------


@pytest.mark.parametrize("key", ["consultancy", "dpa"])
def test_the_example_reading_and_the_preview_are_complete_and_blank_free(key):
    """AC-37: the check reads the example reading and the page previews the field samples; both are whole
    agreements with no blank, brace or instruction."""
    _clean(_draft(key, {}, example=True))
    _clean(render_sample(default_body(key)))


def test_the_consultancy_example_is_a_consistent_engagement():
    """FR-33: at least one deliverable with a name and a due date inside the example term, a rate with its
    ceiling, a key individual and a contact. The dates sit inside both sample terms (the check's from
    2026-10-01 until terminated, the preview's until 2027-09-30)."""
    answers = consultancy.EXAMPLE_ANSWERS
    end = date.fromisoformat(SAMPLE_VALUES["agreement_term"].rsplit(" ", 1)[1])
    for row in answers["consultancy_deliverables"]:
        assert date.fromisoformat(answers["start_date"]) <= date.fromisoformat(row["due"]) <= end
    text = _schedule(_draft("consultancy", {}, example=True))
    assert "D1. Portal discovery report. Due date: 15 December 2026." in text
    assert "D2. Launch readiness assessment. Due date: 30 June 2027." in text
    assert "a day rate of INR 20,000.00 per day" in text and "shall not exceed INR 1,200,000.00" in text
    assert "Lena Fischer and Omar Haddad." in text and "Kavita Joshi, Head of Digital Products." in text
    assert Decimal(20000) < sow.total_value(sow.EXAMPLE_ANSWERS)  # the rate fits under the ceiling


def test_the_dpa_example_is_a_consistent_processing():
    """FR-33: a purpose, two categories, two kinds including special category data with its statement, a
    country outside India with the Section 9 sentence and a sub-processor with its name, country and role."""
    text = _annexes(_draft("dpa", {}, example=True))
    assert "Running the occupational-health clinic booking system for staff and study volunteers." in text
    assert "Employees; Patients or trial subjects." in text
    assert "employment and HR records; health data and other special category data." in text
    assert dpa.SPECIAL_YES in text and "India and Netherlands." in text
    assert "Northwind Hosting Ltd (Netherlands): Hosting of the clinic booking database." in text
    assert "conditions of Section 9" in text


def test_the_preview_and_the_check_read_the_same_example_as_the_field_list():
    """FR-33 / FR-35: the list's samples, the preview, and the check's values are one example. If they
    drifted a person would preview one agreement and have another checked."""
    for module in (consultancy, dpa):
        for name in module.FIELDS:
            assert SAMPLE_VALUES[name] == module.EXAMPLE_VALUES[name] != ""
            assert checks.check_values("consultancy" if module is consultancy else "dpa")[name] == (
                module.EXAMPLE_VALUES[name])


def test_the_examples_share_nothing_with_a_real_acceptance_request_and_the_markers_find_them():
    """AC-38: finding a marker in a draft means the example leaked; the markers do find the example."""
    drafts = {"consultancy": [_draft("consultancy", _consultancy(b)) for b in ("Fixed fee", "Day rate",
                                                                               "Hourly rate")]
              + [_draft("consultancy", _advice_only()), _draft("consultancy", {})],
              "dpa": [_draft("dpa", _dpa()), _draft("dpa", _plain_dpa()), _draft("dpa", {})]}
    for key, module in (("consultancy", consultancy), ("dpa", dpa)):
        for text in drafts[key]:
            assert [m for m in module.EXAMPLE_MARKERS if m in text] == []
        assert [m for m in module.EXAMPLE_MARKERS if m in _draft(key, {}, example=True)] != []


@pytest.mark.parametrize("fv", [{}, {"request_form": "new_agreement", "agreement_type": "Services (MSA)"},
                                {"request_form": "sow"}, {"request_form": "nda"}])
def test_a_request_without_the_answers_fills_every_new_field_with_nothing(fv):
    """FR-34: example content is returned only for example=True. A request of another kind, a held one or a
    bare one gets empty strings, never the Templates page's example."""
    assert consultancy.consultancy_values(fv) == {n: "" for n in consultancy.FIELDS}
    assert dpa.dpa_values(fv) == {n: "" for n in dpa.FIELDS}
    assert consultancy.consultancy_values(None) == {n: "" for n in consultancy.FIELDS}
    assert dpa.dpa_values(None) == {n: "" for n in dpa.FIELDS}
    assert consultancy.consultancy_values(fv, example=True) == consultancy.EXAMPLE_VALUES


# --- the ten fields on the Templates page (AC-39) -----------------------------------------------


def test_the_ten_new_fields_are_listed_with_a_plain_name_a_use_and_an_example():
    """AC-39 / FR-35: each is in the page's list once, with a label, what it fills in and the example; the
    labels are unique so the page never offers two fields that read as one; the shipped wording uses all ten
    and passes the save rules as written."""
    listed = {p["name"]: p for p in PLACEHOLDERS}
    assert len(NEW_FIELDS) == 10 and [n for n in NEW_FIELDS if n not in listed] == []
    assert all(listed[n]["label"] and listed[n]["about"] and listed[n]["sample"] for n in NEW_FIELDS)
    assert len({p["label"] for p in PLACEHOLDERS}) == len(PLACEHOLDERS)
    assert len({p["name"] for p in PLACEHOLDERS}) == len(PLACEHOLDERS)
    for module, key in ((consultancy, "consultancy"), (dpa, "dpa")):
        assert all(listed[n]["sample"] == module.EXAMPLE_VALUES[n] for n in module.FIELDS)
        assert {f["name"] for f in field_uses(default_body(key))} >= set(module.FIELDS)
    # The scope and the total are the SOW's fields, reused (FR-35): same samples, "about" now says both.
    for name in ("services", "fee_amount"):
        assert "Consultancy" in listed[name]["about"] and listed[name]["sample"] == sow.EXAMPLE_VALUES[name]
    assert {f["name"] for f in field_uses(default_body("consultancy"))} >= {"services", "fee_amount"}


def test_a_saved_wording_may_use_the_new_fields_and_an_unknown_name_is_still_refused():
    """AC-39 / FR-35: the strict save rule stays strict."""
    check_body("{company} and {counterparty}: {" + "}\n{".join(NEW_FIELDS) + "}")
    with pytest.raises(HTTPException) as refused:
        check_body("{company} and {counterparty}: {deliverable_table}")
    assert refused.value.status_code == 422 and refused.value.detail.startswith("Unknown placeholder {deliverable_table}")


# --- the check and Apply (FR-34, AC-42) ---------------------------------------------------------


def _finding(fix):
    return {"rule_index": 1, "clause_type": "scope_and_deliverables", "severity": "high", "issue": "x",
            "quote": None, "suggested_fix": fix}


_QUOTING = {
    "consultancy": ["Add the deliverable D3. Portal discovery report.",
                    "Lena Fischer shall remain a key individual.",
                    "The Company shall pay INR 20,000.00 per day.",
                    "Kavita Joshi, Head of Digital Products shall be the contact."],
    "dpa": ["Add Northwind Hosting Ltd as a sub-processor.",
            "Hosting of the clinic booking database is authorised.",
            "Processing in the Netherlands is permitted."],
}


@pytest.mark.parametrize(("key", "whole"), [("consultancy", "key_individuals"), ("dpa", "processing_purpose")])
def test_a_suggestion_that_quotes_the_example_is_never_appliable(key, whole):
    """FR-34: Apply writes a suggestion into our own paper. One that still holds the example's name, person,
    rate, country or sub-processor would put it into every agreement we draft, so it is advice only. Plain
    wording, and a whole field's text (turned back into its code), stay appliable."""
    values = checks.check_values(key)
    plain = "The Consultant shall not replace any key individual without prior written consent."

    out = checks._enrich([_finding(f) for f in [*_QUOTING[key], plain, values[whole]]], values)

    assert [f["suggestion_is_wording"] for f in out] == [False] * len(_QUOTING[key]) + [True, True]
    assert out[-1]["suggested_fix_template"] == "{" + whole + "}"


@pytest.mark.parametrize(("key", "playbook", "marker"), [
    ("consultancy", CONSULTANCY_PB, "Portal discovery report"), ("dpa", DPA_PB, "Northwind Hosting Ltd")])
def test_the_check_sends_the_example_reading_with_no_blank(db, monkeypatch, key, playbook, marker):
    """AC-42 (text half): the text the AI reviews is the example reading of the shipped wording: complete,
    with no blank bracket and no unfilled field, so no finding can be caused by one; a finding whose fix
    quotes the example is not appliable."""
    user, _, _ = _ready(db, key, playbook)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(
        summary="One issue.", deviations=[_deviation(suggested_fix=f"Add {marker}.")])))
    check = _request(db, user, key)

    _run(db, check)

    (call,) = fake.calls
    sent = call["contract_context"].text
    _clean(sent)
    assert sent == drafting.render_document(
        key, company=SAMPLE_VALUES["company"], counterparty=SAMPLE_VALUES["counterparty"],
        effective=SAMPLE_VALUES["effective_date"], fields={}, example=True)
    assert marker in sent
    db.refresh(check)
    (finding,) = check.findings
    assert finding["suggestion_is_wording"] is False


# --- a saved wording is the template's own (AC-14, AC-41) ---------------------------------------


async def test_a_saved_change_to_a_standard_sentence_is_in_the_next_draft(db, dispatched):
    """AC-14 / FR-10: the consent to replace a key individual is the template's own wording, so "prior
    written consent" edited and saved is what the next draft says. (If code supplied it the edit would do
    nothing.) The draft also holds the request's answers and no example content."""
    user, _, _ = _ready(db, "consultancy", CONSULTANCY_PB)
    service = DraftingTemplateService(db)
    edited = default_body("consultancy").replace(
        "named here without the Company's prior written consent.", "named here without the Company's prior consent "
        "in writing.")
    assert edited != default_body("consultancy")
    assert service.save(actor=user, key="consultancy", body=edited, note=None)["changed"] is True
    disk = _Disk()

    await _service(db, disk).draft_contract_for_request(
        actor=user, request=_filed(db, user, _consultancy("Day rate")))

    (stored,) = disk.files.values()
    schedule = _schedule(stored.decode("utf-8"))
    assert "named here without the Company's prior consent in writing." in schedule
    assert "Anita Rao and Dev Menon." in schedule and "INR 25,000.00 per day" in schedule
    assert [m for m in consultancy.EXAMPLE_MARKERS if m in schedule] == []


async def test_an_organisations_own_wording_with_brackets_and_none_of_the_new_fields_drafts_as_before(
        db, dispatched):
    """FR-37: a wording saved before this feature (the old shipped DPA, brackets and all) is not changed by
    the release: it drafts from itself, and the new answers simply are not used."""
    from tests.test_template_check_jobs import _save_version

    user, _, _ = _ready(db, "dpa", DPA_PB)
    old = (EXAM_DIR / "dpa_2026-10-15.txt").read_text(encoding="utf-8")
    _save_version(db, user, "dpa", old)
    disk = _Disk()

    await _service(db, disk).draft_contract_for_request(actor=user, request=_filed(db, user, _dpa()))

    (stored,) = disk.files.values()
    text = stored.decode("utf-8")
    assert "Types of Personal Data: [for example name" in text and len(find_blanks(text)) == 6
    assert "Hosting and support of the clinical-trial site portal" not in text


# --- the other templates draft as before (FR-47, AC-52) -----------------------------------------


_FULL = {"value": "4500000", "currency": "INR", "start_date": "2026-10-01", "term": "Fixed end date",
         "end_date": "2027-09-30", "payment_terms": "45 days", "nda_kind": "One-way", "nda_direction": "They share",
         "governing_law": "India"}


@pytest.mark.parametrize("key", ["nda", "msa", "saas", "vendor", "sow"])
@pytest.mark.parametrize("fields", [{}, _FULL, _consultancy(), _dpa()], ids=["bare", "full form", "consultancy answers", "dpa answers"])
def test_no_other_template_changes_for_the_ten_new_fields(key, fields):
    """AC-52 / FR-47: every template is given the ten new fields and one that does not use them ignores them
    (str.format), so its draft is exactly what it is filled with when they do not exist."""
    values = drafting.template_values(key, company=COMPANY, counterparty=COUNTERPARTY, effective="2026-11-01",
                                      fields=fields)
    assert set(NEW_FIELDS) <= set(values)
    without = {k: v for k, v in values.items() if k not in NEW_FIELDS}
    assert _draft(key, fields) == default_body(key).format(**without)
    assert not {f["name"] for f in field_uses(default_body(key))} & set(NEW_FIELDS)


def test_only_consultancy_and_the_dpa_use_the_new_fields():
    used = {key: {f["name"] for f in field_uses(default_body(key))} & set(NEW_FIELDS) for key in TEMPLATES}
    assert used == {key: (set(consultancy.FIELDS) if key == "consultancy" else set(dpa.FIELDS)
                          if key == "dpa" else set()) for key in TEMPLATES}


# --- the exam keeps drafting from the wording of 2026-10-15 (FR-48, AC-53) ----------------------


def test_the_exam_renders_consultancy_and_the_dpa_from_frozen_copies_that_match_the_manifest():
    """AC-53 / D-3: the exam's committed documents, planted-change offsets, hashes and baseline depend on the
    old wording. The frozen copies hash to the committed manifest, still hold the old blanks (they were not
    "fixed"), and every template the exam renders from a copy maps to it."""
    manifest = json.loads((EXAM_DIR / "content" / "manifest.json").read_text(encoding="utf-8"))["templates"]
    assert manifest["consultancy"].startswith("9b8ea85d") and manifest["dpa"].startswith("4f2c4c57")
    for key, blanks in (("consultancy", 5), ("dpa", 6), ("sow", None)):
        body = exam._template_body(key)
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == manifest[key]
        assert blanks is None or len(find_blanks(body)) == blanks  # the SOW's copy is 007's, tested there
    # Spec 014 rewrote the other four templates too, so the exam holds a copy of each of them as well.
    assert all(exam._template_body(k) for k in ("nda", "msa", "saas", "vendor"))
    # The live templates are no longer those texts, which is why the copies exist.
    assert default_body("consultancy") != exam._template_body("consultancy")
    assert default_body("dpa") != exam._template_body("dpa")


def test_the_exam_documents_drafted_from_the_frozen_wording_are_byte_for_byte_what_was_committed():
    """FR-48: documents.json holds the sha256 of every constructed file. A Consultancy or DPA deal the exam
    renders without planting a change must still hash to it, which it can only do if the exam draws on the
    wording of 2026-10-15 and the ten new fields change nothing it reads. Every deal's draft is also exactly
    its frozen wording filled with the values the app had before the ten fields existed."""
    committed = {d["doc_id"]: d["file_sha256"] for d in json.loads(
        (EXAM_DIR / "content" / "documents.json").read_text(encoding="utf-8"))}
    unplanted = {"consultancy": 0, "dpa": 0}
    for doc_id, deal in exam.make_deals().items():
        if deal.kind in unplanted and hashlib.sha256(exam._render(deal).encode("utf-8")).hexdigest() == (
                committed[doc_id]):
            unplanted[deal.kind] += 1
        body = exam._template_body(deal.kind)
        if body is not None:
            values = drafting.template_values(deal.kind, company=deal.company, counterparty=deal.counterparty,
                                              effective=deal.written, fields=deal.fields())
            assert drafting.render_document(
                deal.kind, company=deal.company, counterparty=deal.counterparty, effective=deal.written,
                fields=deal.fields(), body=body) == body.format(
                    **{k: v for k, v in values.items() if k not in (*NEW_FIELDS, *paper.FIELDS)})
    assert all(n >= 1 for n in unplanted.values()), unplanted
