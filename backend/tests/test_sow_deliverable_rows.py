"""The SOW request form's deliverables table is judged the same way however a request is filed or changed.

The wizard (frontend lib/form-checks.ts) reads the SAME cases file as this suite, so what the browser lets
through is exactly what filing accepts, with the same words. Failures this guards:
* a table the wizard accepts that filing refuses, or the reverse (FR-16), or a message worded differently
  in the two places;
* rows kept or dropped differently: an empty row stored, a hidden share filed, a cell not tidied, row
  numbers that skip an ignored row;
* free-text deliverables filed on a new request, or a table that quietly replaces them on an older one;
* an older request refused for an answer it was filed with before the rule existed (FR-32), or its old
  free text changed by an update (FR-31);
* a digit that only Python reads (full-width, Arabic-Indic) getting through.

No database: these call the validator that create and update both call.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.contracts.blanks import find_blanks
from app.intake.agreement_forms import answer_text, clean_rows, form_fields
from app.intake.service import _validate_field_values

CASES = json.loads(Path(__file__).with_name("fixtures").joinpath("deliverable_rows_cases.json").read_text())
FIELDS = form_fields("sow")
TABLE = next(f for f in FIELDS if f.kind == "rows")
LABELS = {f.key: f.label for f in FIELDS}

ROWS = [
    {"name": "Migration plan", "due": "2026-11-30", "their_owner": "Priya Nair", "our_owner": "Ravi Shah"},
    {"name": "Migrated warehouse", "due": "2027-02-15", "their_owner": "Priya Nair", "our_owner": "Ravi Shah"},
    {"name": "Handover pack", "due": "2027-03-31", "their_owner": "Tom Reyes", "our_owner": "Ravi Shah"},
]
SOW = {
    "request_form": "sow", "parent_contract_id": "c-1", "cp_signer_name": "Maya Chen",
    "cp_signer_email": "maya.chen@example.com", "department": "Procurement",
    "sow_title": "Data platform migration, phase 2", "services_start": "2026-11-01",
    "services_end": "2027-03-31", "value": "4500000", "currency": "INR", "pricing": "Fixed fee",
    "scope": "Migrate the data warehouse to the new platform.", "deliverable_rows": ROWS,
    "personal_data": "No", "gxp": "No",
}
OLD_TEXT = "Plan by Nov, report by Mar, Ravi signs off"


def sow(**over):
    return {**SOW, **over}


def older(**over):
    """A request filed before the table existed: the free text and no rows."""
    return {k: v for k, v in sow(**{"deliverables": OLD_TEXT, **over}).items() if k != "deliverable_rows"}


def refused(values, *, stored=None):
    with pytest.raises(HTTPException) as exc:
        _validate_field_values(FIELDS, dict(values), stored=stored)
    assert exc.value.status_code == 422
    return exc.value.detail


def _both_days(values):
    try:
        start, end = (date.fromisoformat(values[k]) for k in ("services_start", "services_end"))
    except (KeyError, ValueError):
        return False
    return start <= end and "pricing" in values


# --- the shared cases ------------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES["rows"], ids=lambda c: c["id"])
def test_clean_rows_gives_the_shared_answer(case):
    """The same case the wizard is held to: the first message, or the stored rows."""
    if case["error"] is None:
        assert clean_rows(TABLE, case["rows"], case["values"], LABELS) == case["stored"]
        return
    with pytest.raises(HTTPException) as exc:
        clean_rows(TABLE, case["rows"], case["values"], LABELS)
    assert (exc.value.status_code, exc.value.detail) == (422, case["error"])


@pytest.mark.parametrize("case", [c for c in CASES["rows"] if _both_days(c["values"])], ids=lambda c: c["id"])
def test_filing_gives_the_same_answer_as_the_table_rules(case):
    """Through the validator filing really calls, with the rest of the request correct, so the message
    a requester sees is the shared one and a table that passes is stored as the shared cases say."""
    values = sow(**case["values"], deliverable_rows=case["rows"])
    if case["error"] is None:
        assert _validate_field_values(FIELDS, values)["deliverable_rows"] == case["stored"]
    else:
        assert refused(values) == case["error"]


@pytest.mark.parametrize("case", CASES["fields"], ids=lambda c: c["id"])
def test_single_answer_rules_give_the_shared_answer(case):
    """F1 (no blank brackets) and F2 (a number above a floor) on one question, after its type check."""
    f = SimpleNamespace(required=True, options=None, show=None, not_before=None, **case["field"])
    values = {f.key: case["value"]}
    if case["error"] is None:
        assert _validate_field_values([f], values)[f.key] is not None
        return
    with pytest.raises(HTTPException) as exc:
        _validate_field_values([f], values)
    assert (exc.value.status_code, exc.value.detail) == (422, case["error"])


@pytest.mark.parametrize("case", CASES["blanks"], ids=lambda c: c["text"][:40] or "empty")
def test_the_blank_detector_gives_the_shared_list(case):
    """The reference the TypeScript port is held to, so a bracket one side calls a blank is one the other does too."""
    assert find_blanks(case["text"]) == case["blanks"]


def test_the_shared_file_is_not_a_short_one():
    """A cases file that lost its entries would pass every test above with nothing to check."""
    assert (len(CASES["rows"]), len(CASES["fields"]), len(CASES["blanks"])) >= (140, 20, 50)


# --- how the table fits the rest of the form --------------------------------------------------------

def test_a_complete_request_files_with_the_rows_as_sent():
    assert _validate_field_values(FIELDS, sow())["deliverable_rows"] == ROWS


def test_an_end_date_before_the_start_date_is_reported_before_any_row():
    """The window is only known once the two dates are: the date-order message must come first."""
    detail = refused(sow(services_start="2027-04-01", services_end="2027-03-01"))
    assert detail == '"Services end" cannot be before "Services start"'


def test_a_missing_required_answer_is_reported_before_the_table():
    values = sow(deliverable_rows=[{"name": "x"}])
    del values["sow_title"]
    assert refused(values) == 'Field "SoW title" is required'


def test_the_window_is_checked_again_when_services_end_moves():
    """AC-14: rows entered for one window are refused, naming the first row now outside it."""
    assert refused(sow(services_end="2027-02-01")) == (
        '"Deliverables", row 2: "Due date" must be between 1 November 2026 and 1 February 2027 '
        "(Services start to Services end).")


def test_a_hidden_pricing_answer_drops_the_shares_and_milestones_need_them():
    shared = [{**r, "share": s} for r, s in zip(ROWS, ("40", "35", "25"), strict=True)]
    fixed = _validate_field_values(FIELDS, sow(deliverable_rows=shared))
    assert all("share" not in r for r in fixed["deliverable_rows"])
    milestones = _validate_field_values(FIELDS, sow(pricing="Milestones", deliverable_rows=shared))
    assert [r["share"] for r in milestones["deliverable_rows"]] == ["40.00", "35.00", "25.00"]
    assert refused(sow(pricing="Milestones")) == (
        '"Deliverables", row 1: "Share of the fee (%)" is required.')


def test_empty_rows_are_not_kept_and_the_rest_keep_their_order():
    rows = [ROWS[0], {"name": " ", "due": "", "their_owner": None, "our_owner": ""}, ROWS[1]]
    assert _validate_field_values(FIELDS, sow(deliverable_rows=rows))["deliverable_rows"] == ROWS[:2]


def test_a_new_filing_cannot_carry_free_text_deliverables():
    """FR-16: free text where the table belongs is refused with a message that says a table is needed."""
    want = '"Deliverables" must be a table of rows, not free text.'
    assert refused(sow(deliverables=OLD_TEXT)) == want
    assert refused(sow(deliverable_rows=OLD_TEXT)) == want
    assert refused(older()) == want


def test_a_new_filing_with_no_table_is_refused_as_needing_a_row():
    values = sow()
    del values["deliverable_rows"]
    assert refused(values) == '"Deliverables" needs at least 1 row.'
    assert refused(sow(deliverable_rows="")) == '"Deliverables" needs at least 1 row.'
    assert refused(sow(deliverable_rows=None)) == '"Deliverables" needs at least 1 row.'


def test_text_that_reaches_the_sow_may_not_hold_a_blank_bracket():
    """FR-14: the title, scope and signer name; a round bracket is ordinary writing."""
    assert 'contains [TBD]' in refused(sow(scope="Migrate [TBD] systems"))
    assert 'Field "SoW title" contains [Insert title]' in refused(sow(sow_title="[Insert title]"))
    assert 'Field "Their signer: name" contains [Name]' in refused(sow(cp_signer_name="[Name]"))
    assert _validate_field_values(FIELDS, sow(scope="Migrate (phase 1) systems"))["scope"] == "Migrate (phase 1) systems"


@pytest.mark.parametrize("value", [0, "0", -5, "-5"])
def test_a_total_value_of_zero_or_less_is_refused(value):
    assert refused(sow(value=value)) == 'Field "Total value of this SoW" must be more than 0'


def test_a_total_value_of_one_is_accepted():
    assert _validate_field_values(FIELDS, sow(value=1))["value"] == 1


# --- older requests (FR-31, FR-32) -----------------------------------------------------------------

def test_an_older_request_passes_an_update_that_has_no_rows():
    """FR-32: the free-text answer is not a reason to refuse an unrelated change; no rows are made up."""
    stored = older()
    out = _validate_field_values(FIELDS, older(department="Finance"), stored=stored)
    assert out["deliverables"] == OLD_TEXT and "deliverable_rows" not in out
    assert out["department"] == "Finance"


def test_the_free_text_is_carried_over_whatever_the_update_sends():
    """FR-31: the stored words cannot be changed or removed through a field update."""
    stored = older()
    changed = _validate_field_values(FIELDS, older(deliverables="something else"), stored=stored)
    omitted = _validate_field_values(FIELDS, {k: v for k, v in older().items() if k != "deliverables"},
                                     stored=stored)
    assert changed["deliverables"] == omitted["deliverables"] == OLD_TEXT


def test_setting_rows_on_an_older_request_runs_every_table_rule():
    """AC-35: an update that sets the deliverables is judged like a filing, and keeps the old text."""
    stored = older()
    out = _validate_field_values(FIELDS, {**older(), "deliverable_rows": ROWS}, stored=stored)
    assert out["deliverable_rows"] == ROWS and out["deliverables"] == OLD_TEXT
    late = [*ROWS[:2], {**ROWS[2], "due": "2031-01-01"}]
    assert refused({**older(), "deliverable_rows": late}, stored=stored).startswith('"Deliverables", row 3:')
    assert refused({**older(pricing="Milestones"), "deliverable_rows": ROWS}, stored=stored) == (
        '"Deliverables", row 1: "Share of the fee (%)" is required.')


def test_free_text_cannot_be_added_to_a_request_that_never_had_it():
    stored = sow()
    assert refused(sow(deliverables=OLD_TEXT), stored=stored) == (
        '"Deliverables" must be a table of rows, not free text.')


def test_a_request_with_rows_still_needs_them_on_update():
    stored = sow()
    values = sow()
    del values["deliverable_rows"]
    assert refused(values, stored=stored) == '"Deliverables" needs at least 1 row.'


def test_an_answer_filed_before_the_rules_is_not_refused_when_the_update_leaves_it_alone():
    """A total value of 0 and a bracketed scope were fine when they were filed; changing the priority
    must not fail for them. Changing the answer itself is judged by the new rule."""
    stored = older(value=0, scope="Migrate [TBD] systems")
    kept = _validate_field_values(FIELDS, dict(stored, department="Finance"), stored=stored)
    assert kept["value"] == 0 and kept["scope"] == "Migrate [TBD] systems"
    same_value_as_text = _validate_field_values(FIELDS, dict(stored, value="0"), stored=stored)
    assert same_value_as_text["value"] == 0
    assert 'contains [Later]' in refused(dict(stored, scope="Migrate [Later] systems"), stored=stored)
    assert 'must be more than 0' in refused(dict(stored, value=-5), stored=stored)


def test_the_same_answers_are_refused_on_a_new_filing():
    assert 'must be more than 0' in refused(older(value=0))


# --- how the AI prompts see an answer ---------------------------------------------------------------

def test_a_table_answer_reads_as_numbered_rows_with_the_column_names():
    text = answer_text("sow", "deliverable_rows", ROWS[:2])
    assert text == (
        "\n    1. Deliverable name: Migration plan; Due date: 2026-11-30; Their owner: Priya Nair; "
        "Our owner: Ravi Shah"
        "\n    2. Deliverable name: Migrated warehouse; Due date: 2027-02-15; Their owner: Priya Nair; "
        "Our owner: Ravi Shah")


def test_other_answers_read_as_they_always_did():
    assert answer_text("sow", "sow_title", "Phase 2") == "Phase 2"
    assert answer_text("sow", "deliverables", OLD_TEXT) == OLD_TEXT
    assert answer_text(None, "deliverable_rows", ROWS) == str(ROWS)
    assert answer_text("nda", "deliverable_rows", [1]) == "[1]"


# --- the form definition ----------------------------------------------------------------------------

def test_the_loader_passes_the_table_and_its_rules_through():
    assert (TABLE.min_rows, TABLE.max_rows, TABLE.legacy_key) == (1, 20, "deliverables")
    assert [c.key for c in TABLE.columns] == ["name", "due", "their_owner", "our_owner", "share"]
    share = TABLE.columns[-1]
    assert share.kind == "percent" and share.total == 100 and share.show
    assert TABLE.columns[1].within == ["services_start", "services_end"]
    assert {f.key for f in FIELDS if f.no_blanks} == {"sow_title", "scope", "cp_signer_name", "cp_signer_email"}
    assert next(f for f in FIELDS if f.key == "value").min_exclusive == 0
