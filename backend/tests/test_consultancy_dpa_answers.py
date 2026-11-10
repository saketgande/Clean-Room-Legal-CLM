"""The Consultancy and DPA request forms are judged the same way however a request is filed or changed.

The wizard (frontend lib/form-checks.ts) reads the SAME cases file as this suite, so what the browser lets
through is exactly what filing accepts, with the same words. Failures this guards:
* a table or answer the wizard accepts that filing refuses, or the reverse, or the same refusal worded
  differently in the two places (FR-21, FR-30);
* a rule that leaks to a request it was not written for: a zero value or bracketed scope refused on an MSA or
  on their paper (FR-12), a Consultancy answer kept on a request that is not a Consultancy, a table kept when
  its question is hidden (advice only, a rate the basis does not use, countries when no data leaves India);
* a number the wizard calls a number only because Python reads it (1e3, +25, full-width digits);
* "nothing ticked" filing as an answer for a required multi-tick (FR-24), the Amendment form's included;
* an older request refused for an answer it never had (FR-41, FR-42), or let off an answer it now depends on
  (a switch to a rate basis, to Yes, or to our paper), or its older "Several" rewritten;
* a multi-tick answer shown to the AI as a Python list.

No database: these call the validator that create and update both call.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.intake.agreement_forms import answer_text, clean_rows, form_fields, rules_match, shown
from app.intake.service import _validate_field_values
from tests.test_intake_form_values_make_sense import complete

CASES = json.loads(Path(__file__).with_name("fixtures").joinpath("consultancy_dpa_cases.json").read_text())
TICK = "There are no separate deliverables (advice only)"
CONTROLLER = "Controller: we decide how the data is used"

CONSULTANCY = {
    "request_form": "new_agreement", "agreement_type": "Consultancy", "paper": "Our template",
    "entity": "Aegis Pharma Ltd", "entity_id": "le-1", "counterparty": "Acme Advisory LLP",
    "counterparty_id": "cp-1", "cp_signer_name": "Maya Chen", "cp_signer_email": "maya.chen@example.com",
    "purpose": "Quality-management process advice", "department": "Quality",
    "value": "1800000", "currency": "INR", "start_date": "2026-11-01", "term": "Fixed end date",
    "end_date": "2027-04-30", "scope": "Advise on the design of the new quality-management process.",
    "consultancy_deliverables": [
        {"name": "Current-state review", "due": "2026-12-15"},
        {"name": "", "due": ""},
        {"name": "Process design report", "due": "2027-03-15"},
    ],
    "payment_terms": "45 days", "fee_basis": "Fixed fee", "fee_rate": "25000",
    "key_individuals": [{"name": "Anita Rao"}, {"name": "Dev Menon"}],
    "company_contact_name": " Ravi  Shah ", "company_contact_role": "Head of Quality",
    "personal_data": "No", "gxp": "No",
}
NEW_KEYS = ("advice_only", "consultancy_deliverables", "fee_basis", "fee_rate", "key_individuals",
            "company_contact_name", "company_contact_role")
DPA = {
    "request_form": "dpa", "parent_contract_id": "c-1", "cp_signer_name": "Maya Chen",
    "cp_signer_email": "maya.chen@example.com", "paper": "Our template", "our_role": CONTROLLER,
    "processing_purpose": "Hosting and support of the clinical-trial site portal.",
    "data_types": ["Contact and identity", "Health or other special category"],
    "data_subjects": ["Customers", "Patients or trial subjects"],
    "transfer": "Yes", "transfer_countries": [{"country": "Singapore"}, {"country": "Germany"}],
    "subprocessors": "Yes",
    "subprocessor_rows": [
        {"name": "CloudHost Pte Ltd", "country": "Singapore", "processing": "Hosting of the portal database"},
        {"name": "SupportDesk GmbH", "country": "Germany", "processing": "Second-line support"}],
}
DPA_TABLES = ("transfer_countries", "subprocessor_rows")


def consultancy(**over):
    return {**CONSULTANCY, **over}


def dpa(**over):
    return {**DPA, **over}


def check(values, *, stored=None):
    return _validate_field_values(form_fields(values["request_form"]), dict(values), stored=stored)


def refused(values, *, stored=None) -> str:
    with pytest.raises(HTTPException) as exc:
        check(values, stored=stored)
    assert exc.value.status_code == 422
    return exc.value.detail


def _question(form: str, key: str):
    return next(f for f in form_fields(form) if f.key == key)


def _day(text) -> date | None:
    try:
        return date.fromisoformat(text) if isinstance(text, str) and len(text) == 10 else None
    except ValueError:
        return None


# --- the shared cases ------------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES["tables"], ids=lambda c: c["id"])
def test_clean_rows_gives_the_shared_answer(case):
    """The same case the wizard is held to: the first message, or the stored rows."""
    labels = {f.key: f.label for f in form_fields(case["form"])}
    table = _question(case["form"], case["question"])
    if case["error"] is None:
        assert clean_rows(table, case["rows"], case["values"], labels) == case["stored"]
        return
    with pytest.raises(HTTPException) as exc:
        clean_rows(table, case["rows"], case["values"], labels)
    assert (exc.value.status_code, exc.value.detail) == (422, case["error"])


def _filing_of(case) -> dict | None:
    """A complete request of the case's form with the case's table and the answers its rules read, or None
    when the case describes answers a whole request cannot hold (no start date at all, an end before the
    start, a blank "Will data leave India?": the form's own checks answer those first)."""
    values = case["values"]
    if case["form"] == "dpa":  # a blank or unknown "Will data leave India?" is the select's own refusal
        return dpa(**values, **{case["question"]: case["rows"]}) if values.get("transfer") in ("Yes", "No") else None
    over = {case["question"]: case["rows"]}
    if case["question"] == "consultancy_deliverables":
        start, end = values.get("start_date"), values.get("end_date")
        if not _day(start) or (_day(end) and _day(end) < _day(start)):
            return None
        over["start_date"] = start
        if _day(end):
            over["end_date"] = end
        else:  # no end date: the term has none, so the end date question is not asked
            over.update(term="No end date (evergreen)", end_date=None)
    return consultancy(**over)


@pytest.mark.parametrize("case", [c for c in CASES["tables"] if _filing_of(c)], ids=lambda c: c["id"])
def test_filing_gives_the_same_answer_as_the_table_rules(case):
    """Through the validator filing really calls, with the rest of the request correct, so the message a
    requester sees is the shared one and a table that passes is stored as the shared cases say."""
    values = _filing_of(case)
    if case["error"] is None:
        assert check(values)[case["question"]] == case["stored"]
    else:
        assert refused(values) == case["error"]


@pytest.mark.parametrize("case", CASES["fields"], ids=lambda c: c["id"])
def test_single_answer_rules_give_the_shared_answer(case):
    """F1 to F4 on one question, after its type check, for a shown question of the real form."""
    f = _question(case["form"], case["question"])
    values = {**case["values"], f.key: case["value"]}
    if case["error"] is None:
        assert _validate_field_values([f], values)[f.key] is not None
        return
    with pytest.raises(HTTPException) as exc:
        _validate_field_values([f], values)
    assert (exc.value.status_code, exc.value.detail) == (422, case["error"])


def test_the_shared_file_is_not_a_short_one():
    """A cases file that lost its entries would pass every test above with nothing to check."""
    assert len(CASES["tables"]) >= 200 and len(CASES["fields"]) >= 70
    assert {(c["form"], c["question"]) for c in CASES["tables"]} == {
        ("new_agreement", "consultancy_deliverables"), ("new_agreement", "key_individuals"),
        ("dpa", "transfer_countries"), ("dpa", "subprocessor_rows")}
    assert {c["question"] for c in CASES["fields"]} == {
        "scope", "value", "fee_rate", "company_contact_name", "company_contact_role", "processing_purpose"}
    for message in ("M9b", "M13", "M14"):  # the three new table rules each have a refusal case
        assert any(c["id"].startswith(message.lower() + "-") for c in CASES["tables"]), message
    assert any(c["id"].startswith("f3-") for c in CASES["fields"])
    assert any(c["id"].startswith("f4-") for c in CASES["fields"])


# --- a complete request files, and what it drops --------------------------------------------------

def test_a_complete_consultancy_request_files_as_the_plan_shows():
    """The empty row goes, the contact is tidied, a rate the basis does not use is dropped, nothing is added
    for 'advice only' that was not ticked, and the value is a number."""
    out = check(consultancy())
    assert out["consultancy_deliverables"] == [{"name": "Current-state review", "due": "2026-12-15"},
                                               {"name": "Process design report", "due": "2027-03-15"}]
    assert out["fee_basis"] == "Fixed fee" and "fee_rate" not in out and "advice_only" not in out
    assert out["key_individuals"] == [{"name": "Anita Rao"}, {"name": "Dev Menon"}]
    assert out["company_contact_name"] == "Ravi Shah" and out["company_contact_role"] == "Head of Quality"
    assert out["value"] == 1800000


def test_a_complete_dpa_request_files_as_sent():
    out = check(dpa())
    assert {k: out[k] for k in ("data_types", "data_subjects", "transfer_countries", "subprocessor_rows")} == {
        k: DPA[k] for k in ("data_types", "data_subjects", "transfer_countries", "subprocessor_rows")}


def test_advice_only_removes_the_deliverables_and_needs_none():
    """FR-15: the ticked answer is kept, the rows are neither required nor stored (even if sent)."""
    out = check(consultancy(advice_only=[TICK]))
    assert out["advice_only"] == [TICK] and "consultancy_deliverables" not in out
    out = check({k: v for k, v in consultancy(advice_only=[TICK]).items() if k != "consultancy_deliverables"})
    assert "consultancy_deliverables" not in out


def test_without_advice_only_a_deliverable_is_required():
    sent = {k: v for k, v in consultancy().items() if k != "consultancy_deliverables"}
    assert refused(sent) == '"Deliverables" needs at least 1 row.'
    assert refused(consultancy(consultancy_deliverables=[])) == '"Deliverables" needs at least 1 row.'
    assert "advice_only" not in check(consultancy(advice_only=[]))  # nothing ticked is no answer


def test_fixed_fee_drops_a_sent_rate_and_a_rate_basis_needs_one():
    """FR-16: the rate belongs to a rate basis only."""
    assert "fee_rate" not in check(consultancy(fee_basis="Fixed fee", fee_rate="not even a number"))
    for basis in ("Day rate", "Hourly rate"):
        no_rate = {k: v for k, v in consultancy(fee_basis=basis).items() if k != "fee_rate"}
        assert refused(no_rate) == 'Field "Rate per day or hour" is required'
        assert check(consultancy(fee_basis=basis, fee_rate="3,500.50"))["fee_rate"] == 3500.5
    assert check(consultancy(fee_basis="Day rate", fee_rate=25000))["fee_rate"] == 25000


def test_a_rate_is_judged_in_the_order_number_floor_decimals():
    """-5 and -0.005 are numbers, so the floor answers; 25.001 is above it, so the decimals answer."""
    for typed, message in (("-0.005", "must be more than 0"), ("-5", "must be more than 0"),
                           ("25.001", "can have at most 2 decimal places"), ("1e3", "must be a number")):
        assert refused(consultancy(fee_basis="Day rate", fee_rate=typed)) == (
            f'Field "Rate per day or hour" {message}')


def test_a_consultancy_answer_sent_with_another_kind_of_agreement_is_dropped():
    """FR-12: the seven new answers belong to a Consultancy on our paper and nothing else. Sent anyway (even
    unusable ones) they are dropped, and the request files exactly as it would without them."""
    junk = {"advice_only": [TICK], "consultancy_deliverables": "not a table", "fee_basis": "Day rate",
            "fee_rate": "banana", "key_individuals": "Anita", "company_contact_name": "[Name]",
            "company_contact_role": "x" * 500}
    kinds = ["NDA", "Services (MSA)", "Buying from a vendor", "Software or SaaS", "Selling to a customer",
             "Something else"]
    for kind in kinds:
        plain = complete("new_agreement", agreement_type=kind, paper="Our template")
        assert check({**plain, **junk}) == check(plain)
        assert not set(NEW_KEYS) & set(check({**plain, **junk}))
    their = complete("new_agreement", agreement_type="Consultancy", paper="Their paper")
    assert check({**their, **junk}) == check(their)


def test_scope_and_value_rules_apply_to_a_consultancy_on_our_paper_only():
    """FR-12 with FR-17 and FR-20: an MSA and a Consultancy on their paper keep today's rules."""
    for kind, paper in (("Services (MSA)", "Our template"), ("Consultancy", "Their paper")):
        loose = complete("new_agreement", agreement_type=kind, paper=paper, value="0", scope="Advise on [TBD]")
        out = check(loose)
        assert out["value"] == 0 and out["scope"] == "Advise on [TBD]"
    assert refused(consultancy(value="0")) == 'Field "Total contract value" must be more than 0'
    assert refused(consultancy(scope="Advise on [TBD]")).startswith('Field "Scope of services" contains [TBD]')


def test_a_dpa_with_no_data_leaving_india_and_no_sub_processors_has_no_tables():
    """FR-29: a table whose question is answered No is neither required nor kept."""
    out = check(dpa(transfer="No", subprocessors="No"))
    assert not set(DPA_TABLES) & set(out)
    for key, answer in (("transfer", "Yes"), ("subprocessors", "Yes")):
        sent = {k: v for k, v in dpa().items() if k not in DPA_TABLES}
        assert refused({**sent, key: answer}).endswith("needs at least 1 row.")


def test_their_paper_dpa_asks_for_no_table_and_keeps_a_bracketed_purpose():
    """A-3: the follow-ups and the purpose rule are for our own paper."""
    sent = {k: v for k, v in dpa(paper="Their paper", processing_purpose="Hosting of [TBD] systems").items()
            if k not in DPA_TABLES}
    out = check(sent)
    assert out["processing_purpose"] == "Hosting of [TBD] systems" and not set(DPA_TABLES) & set(out)
    assert check(dpa(paper="Their paper")).get("subprocessor_rows") is None  # sent rows are dropped too


def test_a_sub_processor_outside_india_is_refused_only_when_no_data_leaves_india():
    row = {"name": "CloudHost Pte Ltd", "country": "Singapore", "processing": "Hosting"}
    assert refused(dpa(transfer="No", subprocessor_rows=[row])) == (
        '"Sub-processors", row 1: "Country" is "Singapore", but the answer to "Will data leave India?" is No. '
        "Enter India, or change that answer.")
    assert check(dpa(transfer="Yes", subprocessor_rows=[row]))["subprocessor_rows"] == [row]
    assert check(dpa(transfer="No", subprocessor_rows=[{**row, "country": " india "}]))


# --- a multi-tick answer, and what counts as no answer ----------------------------------------------

def test_nothing_ticked_for_a_required_multi_tick_is_refused():
    """FR-24: [] is no answer on the server, as the wizard already said."""
    assert refused(dpa(data_subjects=[])) == 'Field "Whose data?" is required'
    assert refused(dpa(data_types=[])) == 'Field "What kind of personal data?" is required'
    assert refused(dpa(data_subjects="  ")) == 'Field "Whose data?" is required'


def test_a_tick_that_is_not_an_option_is_refused_and_several_is_no_longer_one():
    assert 'must be from: Employees, Customers' in refused(dpa(data_subjects=["Everyone"]))
    assert 'must be from: Employees, Customers' in refused(dpa(data_subjects="Several"))
    assert check(dpa(data_subjects="Customers"))["data_subjects"] == ["Customers"]


def test_the_amendment_form_now_refuses_nothing_ticked_for_what_changes():
    """[] used to file for the required 'what changes' multi-tick while the wizard refused it."""
    assert refused(complete("amendment", what_changes=[])) == (
        f'Field "{_question("amendment", "what_changes").label}" is required')
    assert check(complete("amendment", what_changes=["Value"]))["what_changes"] == ["Value"]


def test_an_optional_multi_tick_left_empty_is_absent():
    assert "advice_only" not in check(consultancy(advice_only=[]))


def test_an_empty_table_is_still_a_table_with_too_few_rows():
    assert refused(dpa(transfer_countries=[])) == '"Countries outside India" needs at least 1 row.'
    assert refused(dpa(transfer_countries="Singapore")) == (
        '"Countries outside India" must be a table of rows, not free text.')


# --- requests filed before these questions ----------------------------------------------------------

def _older(values: dict) -> dict:
    """A request as it was filed before the questions existed."""
    return {k: v for k, v in values.items() if k not in NEW_KEYS}


OLD_CONSULTANCY = _older(consultancy())
OLD_DPA = {**{k: v for k, v in DPA.items() if k not in DPA_TABLES},
           "data_types": "Financial", "data_subjects": "Several", "transfer": "No", "subprocessors": "No"}


def test_an_older_consultancy_request_is_updated_without_the_new_answers():
    """FR-42: nothing it never had is asked for by an unrelated update, and none is invented."""
    out = check(OLD_CONSULTANCY, stored=OLD_CONSULTANCY)
    assert not set(NEW_KEYS) & set(out)
    changed = check({**OLD_CONSULTANCY, "department": "Finance"}, stored=OLD_CONSULTANCY)
    assert changed["department"] == "Finance" and not set(NEW_KEYS) & set(changed)


def test_an_older_consultancys_unchanged_value_and_scope_are_not_refused_for_the_new_rules():
    """FR-42: a zero value or a bracketed scope was allowed when it was filed; only a change to it meets the
    new Consultancy rules."""
    stored = {**OLD_CONSULTANCY, "value": 0, "scope": "Advise on [TBD] processes"}
    assert check(stored, stored=stored)["value"] == 0
    assert refused({**stored, "value": -5}, stored=stored) == 'Field "Total contract value" must be more than 0'
    assert refused({**stored, "scope": "Advise on [Name] processes"}, stored=stored).startswith(
        'Field "Scope of services" contains [Name]')


def test_an_older_consultancy_request_that_gets_a_rate_basis_needs_the_rate():
    """The exemption ends the moment the answer it depends on changes (FR-16)."""
    for basis in ("Day rate", "Hourly rate"):
        sent = {**OLD_CONSULTANCY, "fee_basis": basis}
        assert refused(sent, stored=OLD_CONSULTANCY) == 'Field "Rate per day or hour" is required'
        assert check({**sent, "fee_rate": "20000"}, stored=OLD_CONSULTANCY)["fee_rate"] == 20000
    out = check({**OLD_CONSULTANCY, "fee_basis": "Fixed fee"}, stored=OLD_CONSULTANCY)
    assert out["fee_basis"] == "Fixed fee" and "key_individuals" not in out  # the rest may still lack


def test_an_older_consultancy_request_moved_onto_our_paper_needs_every_answer():
    stored = _older(consultancy(paper="Their paper"))
    detail = refused({**stored, "paper": "Our template"}, stored=stored)
    assert detail == 'Field "How is the consultant paid?" is required'
    assert check(stored, stored=stored)["paper"] == "Their paper"


def test_the_answers_an_older_request_does_set_meet_every_rule():
    """FR-42: an old request is not let off a rule for an answer it is now giving."""
    assert refused({**OLD_CONSULTANCY, "company_contact_name": "[Name]"}, stored=OLD_CONSULTANCY).startswith(
        'Field "Company contact: name" contains [Name]')
    assert refused({**OLD_CONSULTANCY, "key_individuals": [{"name": "A"}, {"name": "a"}]},
                   stored=OLD_CONSULTANCY).startswith('"Consultant\'s key individuals", rows 1 and 2')
    done = {**OLD_CONSULTANCY, "fee_basis": "Day rate", "fee_rate": "25000",
            "consultancy_deliverables": [{"name": "Report", "due": "2026-12-15"}],
            "key_individuals": [{"name": "Anita Rao"}], "company_contact_name": "Ravi Shah",
            "company_contact_role": "Head of Quality"}
    assert check(done, stored=OLD_CONSULTANCY)["fee_rate"] == 25000


def test_an_older_dpa_keeps_its_single_choices_and_is_updated_without_tables():
    """FR-41, FR-42: 'Several' and a single choice sent back as they were stay verbatim; No needs no table."""
    out = check(OLD_DPA, stored=OLD_DPA)
    assert out["data_subjects"] == "Several" and out["data_types"] == "Financial"
    assert not set(DPA_TABLES) & set(out)
    assert check({**OLD_DPA, "our_role": CONTROLLER}, stored=OLD_DPA)["data_subjects"] == "Several"


def test_an_older_dpa_that_is_changed_to_yes_needs_the_entries():
    assert refused({**OLD_DPA, "transfer": "Yes"}, stored=OLD_DPA) == '"Countries outside India" needs at least 1 row.'
    assert refused({**OLD_DPA, "subprocessors": "Yes"}, stored=OLD_DPA) == '"Sub-processors" needs at least 1 row.'
    yes = {**OLD_DPA, "transfer": "Yes", "transfer_countries": [{"country": "Singapore"}]}
    assert check(yes, stored=OLD_DPA)["transfer_countries"] == [{"country": "Singapore"}]
    older_yes = {**OLD_DPA, "transfer": "Yes"}  # filed as Yes before there was a table to fill
    assert "transfer_countries" not in check(older_yes, stored=older_yes)


def test_several_is_refused_on_a_new_filing_and_when_it_is_not_the_stored_answer():
    assert 'must be from' in refused(dpa(data_subjects="Several"))
    assert 'must be from' in refused({**OLD_DPA, "data_subjects": "Several"}, stored={**OLD_DPA, "data_subjects": "Customers"})
    assert check({**OLD_DPA, "data_subjects": ["Customers", "Suppliers"]}, stored=OLD_DPA)["data_subjects"] == [
        "Customers", "Suppliers"]
    # a comma-separated string the stored answer does not equal is read as ticks, as a form post always was
    assert check({**OLD_DPA, "data_subjects": "Employees, Customers"}, stored=OLD_DPA)["data_subjects"] == [
        "Employees", "Customers"]


# --- the building blocks ----------------------------------------------------------------------------

def test_a_not_rule_matches_when_none_of_the_options_is_answered():
    """The deliverables table shows unless 'advice only' is ticked; an unanswered question matches."""
    table = _question("new_agreement", "consultancy_deliverables")
    base = {"agreement_type": "Consultancy", "paper": "Our template"}
    assert shown(table, base)
    assert shown(table, {**base, "advice_only": []}) and shown(table, {**base, "advice_only": None})
    assert not shown(table, {**base, "advice_only": [TICK]})
    assert not shown(table, {**base, "paper": "Their paper"})
    assert shown(SimpleNamespace(show=None), {})
    assert rules_match(None, {}) and not rules_match([{"field": "x", "in": ["a"]}], {"x": ["b"]})
    assert rules_match([{"field": "x", "in": ["a"], "not": True}], {"x": "b"})


def test_the_new_attributes_reach_the_validator():
    """form_fields passes what the rules read: a rule that never reached the validator would silently not run."""
    rate = _question("new_agreement", "fee_rate")
    assert (rate.max_decimals, rate.min_exclusive, rate.older_may_lack) == (2, 0, True)
    assert _question("new_agreement", "value").rules_when and _question("dpa", "data_subjects").legacy_single
    assert _question("new_agreement", "company_contact_name").max_length == 120
    assert _question("dpa", "transfer_countries").columns[0].outside == "India"
    assert _question("dpa", "subprocessor_rows").columns[1].must_be["value"] == "India"
    assert _question("new_agreement", "consultancy_deliverables").columns[1].open_end
    assert [f.key for f in form_fields("dpa") if f.fills] == [
        "processing_purpose", "data_types", "data_subjects", "transfer", "transfer_countries", "subprocessors",
        "subprocessor_rows"]
    assert form_fields("sow") and not any(f.fills for f in form_fields("sow"))


def test_a_multi_tick_answer_reads_as_its_choices_to_the_ai():
    assert answer_text("dpa", "data_subjects", ["Customers", "Patients or trial subjects"]) == (
        "Customers, Patients or trial subjects")
    assert answer_text("dpa", "data_subjects", "Several") == "Several"
    assert answer_text("dpa", "subprocessor_rows", DPA["subprocessor_rows"]).startswith(
        "\n    1. Sub-processor name: CloudHost Pte Ltd; Country: Singapore")
    assert answer_text(None, "x", [1]) == "[1]" and answer_text("dpa", "x", []) == "[]"


def test_the_triage_and_workflow_prompts_show_a_dpa_request_as_people_read_it():
    """FR-23, FR-32: the model reads 'Customers, Patients or trial subjects' and numbered sub-processor rows,
    not a Python list of dicts."""
    from app.intake import flow_agent, triage_agent
    from app.intake.models import IntakeRequest

    request = IntakeRequest(type_label="Data Processing Agreement", subject="DPA", description="d", priority="Medium",
                            field_values=check(dpa()))
    flow = {"id": "f1", "name": "DPA", "is_catch_all": True, "criteria": {}, "steps": [], "description": ""}
    for text in (triage_agent._prompt(request, [{**flow, "criteria": {}}]), flow_agent._prompt_for(request, [flow])):
        assert "data_subjects: Customers, Patients or trial subjects" in text
        assert "1. Sub-processor name: CloudHost Pte Ltd; Country: Singapore; What it does: Hosting of the portal" in text
        assert "2. Sub-processor name: SupportDesk GmbH" in text
        assert "[{" not in text and "['Customers'" not in text
