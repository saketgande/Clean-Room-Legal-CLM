"""A Statement of Work drafted from a request is filled in, not left to fill in (spec 007).

Failures this guards:
* a bracket, brace or "delete as appropriate" left in the shipped SOW wording or in a drafted SOW;
* a payment that is a cent out, so the payments no longer add up to the total, or that depends on a date
  instead of on written acceptance;
* the D-numbers naming different Deliverables in different lists of the same SOW;
* the Templates page's example (names, deliverables, amounts) leaking into a draft made for a real request,
  or Apply writing it into our own paper;
* an older request (deliverables only as free text) drafted with an empty list, or a hold that pins a
  template, stores a file or writes an audit row;
* any other template drafting differently because the SOW gained nine fields;
* an organisation's own saved SOW wording changing under it.

The pure tests call the fill and the render directly. The rest run against Postgres in one rolled-back
transaction through the real upload pipeline with an in-memory store; the AI answer is a fake, so nothing
is spent.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.ai.schemas import PlaybookReviewOutput
from app.contracts.blanks import find_blanks
from app.contracts.models import Contract
from app.core.config import settings
from app.core.database import engine, utcnow
from app.core.models import AuditLog
from app.drafting_templates import checks
from app.drafting_templates.models import DraftingTemplateCheck, DraftingTemplateType
from app.drafting_templates.service import (
    PLACEHOLDERS,
    SAMPLE_VALUES,
    TEMPLATES,
    DraftingTemplateService,
    check_body,
    default_body,
    field_uses,
    get_type,
    render_sample,
)
from app.intake import drafting, sow
from app.intake import routes as intake_routes
from app.intake.dependencies import get_drafting_service
from app.intake.drafting import DraftHeld
from app.jobs.models import JobRun
from tests.test_template_check_jobs import (
    _ai,
    _ai_runs,
    _audit,
    _deviation,
    _FakeReview,
    _playbook,
    _raw_check,
    _ready,
    _request,
    _run,
    _save_version,
    _world,
)
from tests.test_template_drafting_record import _Disk, _events, _filed, _service

SOW_PB = "Statement of Work Playbook"
OLD_SOW = (Path(__file__).resolve().parents[1] / "eval" / "exam" / "sow_2026-10-15.txt").read_text(
    encoding="utf-8")
COMPANY, COUNTERPARTY = "Aegis Pharma Ltd", "Globex Corporation"
OLDER_TEXT = "Plan by Nov, report by Mar, Ravi signs off"


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


# --- the example request of the acceptance criteria ---------------------------------------------


def _rows(shares: tuple[str, ...] | None = None) -> list[dict]:
    rows = [
        {"name": "Migration plan", "due": "2026-11-30", "their_owner": "Priya Nair", "our_owner": "Ravi Shah"},
        {"name": "Migrated warehouse", "due": "2027-02-15", "their_owner": "Priya Nair", "our_owner": "Ravi Shah"},
        {"name": "Handover pack", "due": "2027-03-31", "their_owner": "Tom Reyes", "our_owner": "Ravi Shah"},
    ]
    return [{**r, "share": s} for r, s in zip(rows, shares, strict=False)] if shares else rows


def _answers(pricing: str = "Fixed fee", **over) -> dict:
    """The request the acceptance criteria describe, as the filing stores it (shares only for Milestones)."""
    fv = {
        "request_form": "sow", "counterparty": COUNTERPARTY, "sow_title": "Data platform migration, phase 2",
        "services_start": "2026-11-01", "services_end": "2027-03-31", "value": 4500000, "currency": "INR",
        "pricing": pricing, "scope": "Migrate the data warehouse to the new platform.",
        "cp_signer_name": "Maya Chen", "cp_signer_email": "maya.chen@example.com",
        "deliverable_rows": _rows(("40.00", "35.00", "25.00") if pricing == "Milestones" else None),
    }
    return {**fv, **over}


def _older_answers(**over) -> dict:
    """An older request: filed before the form asked for a table, so its deliverables are free text only."""
    fv = {k: v for k, v in _answers().items() if k != "deliverable_rows"}
    return {**fv, "deliverables": OLDER_TEXT, **over}


def _sow(fv: dict, *, body: str | None = None, **kw) -> str:
    return drafting.render_document("sow", company=COMPANY, counterparty=COUNTERPARTY, effective="2026-11-01",
                                    fields=fv, body=body, **kw)


def _payments(text: str) -> list[Decimal]:
    """The amount of each milestone payment entry, in order."""
    found = re.findall(r"^M\d+: .*? INR ([\d,]+\.\d{2}), payable", text, re.MULTILINE)
    return [Decimal(n.replace(",", "")) for n in found]


def _clean(text: str) -> None:
    assert find_blanks(text) == []
    assert "{" not in text and "}" not in text
    assert "delete as appropriate" not in text.lower()


# --- the shipped wording (AC-1, AC-28) ---------------------------------------------------------


def test_the_shipped_sow_wording_has_no_blank_bracket():
    """AC-1: a bracket in the template is a blank that reaches the counterparty. The blank check finds none,
    and no bracket of any kind remains, so the count is zero by occurrence as well as by distinct blank."""
    body = default_body("sow")
    assert find_blanks(body) == []
    assert "[" not in body and "]" not in body
    assert "delete as appropriate" not in body.lower()
    assert "deemed accepted" not in body.lower()  # the SOW playbook's prohibited phrase


def test_the_shipped_wording_uses_all_nine_new_fields_and_each_is_in_the_page_list():
    """AC-28: every new field is one the Templates page lists (with a plain name and an example) and the
    shipped wording passes the save rules as written."""
    check_body(default_body("sow"))
    used = {f["name"] for f in field_uses(default_body("sow"))}
    assert set(sow.FIELDS) <= used
    listed = {p["name"]: p for p in PLACEHOLDERS}
    assert [n for n in sow.FIELDS if n not in listed] == []
    assert all(listed[n]["label"] and listed[n]["about"] and listed[n]["sample"] for n in sow.FIELDS)
    assert {n: listed[n]["sample"] for n in sow.FIELDS} == sow.EXAMPLE_VALUES  # one example, shown everywhere


def test_a_saved_wording_may_use_a_new_field_and_a_name_not_in_the_list_is_still_refused():
    """AC-28: the strict save rule stays strict. A near miss of a real field name is refused with the
    same message as any unknown field."""
    check_body("{company} and {counterparty}: {sow_title}\n{deliverables_list}\n{milestone_payments_list}")
    with pytest.raises(HTTPException) as refused:
        check_body("{company} and {counterparty}: {deliverable_table}")
    assert refused.value.status_code == 422 and refused.value.detail.startswith("Unknown placeholder {deliverable_table}")


# --- the money (FR-21, FR-44, AC-24, AC-46, AC-47) ----------------------------------------------


@pytest.mark.parametrize(("total", "n", "shares", "expected"), [
    ("100.00", 3, None, ["33.33", "33.33", "33.34"]),
    ("4500000.00", 3, None, ["1500000.00", "1500000.00", "1500000.00"]),
    ("123.45", 1, None, ["123.45"]),
    ("100.005", 3, None, ["33.34", "33.34", "33.33"]),
    ("4500000.00", 3, ["40", "35", "25"], ["1800000.00", "1575000.00", "1125000.00"]),
    ("100.01", 3, ["33.33", "33.33", "33.34"], ["33.33", "33.33", "33.35"]),
    ("77.77", 1, ["100"], ["77.77"]),
    ("1000.00", 3, ["12.5", "37.5", "50"], ["125.00", "375.00", "500.00"]),
], ids=["equal-100", "equal-4.5m", "one-row", "total-rounds-half-up", "milestones-4.5m", "milestones-100.01",
        "milestones-one-row", "milestones-decimal-shares"])
def test_the_payments_are_exact_and_the_last_one_takes_the_rounding(total, n, shares, expected):
    """AC-47: the payments add up to the total to the cent. A float, or a rounding that is not taken up by the
    last payment, loses a cent here (33.33 + 33.33 + 33.34 on 100.01 is 100.00)."""
    paid = sow.split_payments(Decimal(total), n, None if shares is None else [Decimal(s) for s in shares])
    assert [str(p) for p in paid] == expected
    assert sum(paid) == Decimal(total).quantize(Decimal("0.01"), ROUND_HALF_UP)


def test_payments_add_up_for_any_number_of_rows_and_any_decimals_of_total():
    """FR-21: whatever the rows and the total, the sum is the total rounded to the cent, every payment is
    a whole cent amount, and none is negative."""
    for total in ("5000", "5000.01", "5000.005", "99999.99", "1234567.894", "1234567.895"):
        for n in range(1, 10):
            equal = sow.split_payments(Decimal(total), n)
            shares = [Decimal("11.11")] * (n - 1)
            shares.append(Decimal(100) - sum(shares))
            by_share = sow.split_payments(Decimal(total), n, shares)
            for paid in (equal, by_share):
                assert sum(paid) == Decimal(total).quantize(Decimal("0.01"), ROUND_HALF_UP)
                assert all(isinstance(p, Decimal) and p.as_tuple().exponent == -2 and p >= 0 for p in paid)


def test_money_and_dates_are_written_as_a_contract_writes_them():
    """FR-6: "INR 4,500,000.00" and "30 November 2026". A day with no leading zero, and text that is not a
    date is left as written rather than failing a draft."""
    assert sow.money(Decimal(4500000), "INR") == "INR 4,500,000.00"
    assert sow.money(Decimal("0.5"), "USD") == "USD 0.50"
    assert sow.day("2026-11-30") == "30 November 2026" and sow.day("2027-02-05") == "5 February 2027"
    assert sow.day("sometime") == "sometime"


def test_a_request_without_answers_fills_the_nine_fields_with_nothing():
    """FR-24: an answer that is missing fills with an empty string. A non-SOW request, an email request or a
    SOW with no rows must never get a field's example in its place."""
    assert sow.sow_values({}) == dict.fromkeys(sow.FIELDS, "")
    assert sow.sow_values(None) == dict.fromkeys(sow.FIELDS, "")
    assert set(sow.sow_values({})) == set(sow.FIELDS)


@pytest.mark.parametrize(("value", "expected"), [
    (4500000, "INR 4,500,000.00"), ("4,500,000", "INR 4,500,000.00"), ("100.005", "INR 100.01"),
    (0, "INR 0.00"), ("forty", ""), (None, ""), ("", ""),
])
def test_the_fee_is_the_total_value_with_its_currency_or_nothing(value, expected):
    """FR-20: the form's number, as a person or the API wrote it. A request filed with a total of zero before
    the form refused it still drafts; an answer that is not a number fills nothing, and does not fail."""
    assert sow.sow_values({"value": value})["fee_amount"] == expected


def test_the_currency_is_the_requests_own_else_the_default():
    assert sow.sow_values({"value": 10, "currency": "USD"})["fee_amount"] == "USD 10.00"
    assert sow.sow_values({"value": 10})["fee_amount"] == f"{settings.default_currency} 10.00"


# --- the draft of the example request (AC-2 to AC-9, AC-22 to AC-25, AC-46, AC-47) --------------


@pytest.mark.parametrize("pricing", ["Fixed fee", "Time and materials", "Milestones"])
def test_a_drafted_sow_has_no_blank_brace_or_instruction_whatever_the_pricing(pricing):
    """AC-2: the whole point of the feature, for each of the three pricing answers."""
    _clean(_sow(_answers(pricing)))


def test_the_draft_holds_the_requests_title_scope_and_deliverables_exactly():
    """AC-3: the Background names the title, the Services hold the scope as written, and D1 to D3 hold the
    name, the date as "30 November 2026" and both owners, in the order entered."""
    text = _sow(_answers())
    assert 'for the work titled "Data platform migration, phase 2".' in text
    assert "perform the following Services:\n\nMigrate the data warehouse to the new platform.\n\n4.3" in text
    assert ("D1. Migration plan. Due date: 30 November 2026. Provider owner: Priya Nair. Customer owner: Ravi Shah.\n\n"
            "D2. Migrated warehouse. Due date: 15 February 2027. Provider owner: Priya Nair. Customer owner: Ravi Shah."
            "\n\nD3. Handover pack. Due date: 31 March 2027. Provider owner: Tom Reyes. Customer owner: Ravi Shah."
            ) in text


def test_the_services_keep_their_line_breaks_and_lose_surrounding_space():
    """AC-3: the scope is the Provider's performance obligation, verbatim."""
    text = _sow(_answers(scope="  Phase 1: assess.\r\nPhase 2: build.\n\nPhase 3: launch.  "))
    assert "Services:\n\nPhase 1: assess.\nPhase 2: build.\n\nPhase 3: launch.\n\n4.3" in text


def test_each_provider_owner_is_one_key_person_however_it_is_typed():
    """AC-4: Priya Nair owns D1 and D2 and is listed once, even when the second row types "priya  nair";
    the acceptance clause lists one entry for each of D1, D2 and D3."""
    rows = _rows()
    rows[1] = {**rows[1], "their_owner": "priya  nair"}
    text = _sow(_answers(deliverable_rows=rows))
    assert ("5.1 The following individuals are key personnel for this SOW (the \"Key Personnel\"):\n\n"
            "Priya Nair - owner of D1 and D2 - delivery of those Deliverables\n\n"
            "Tom Reyes - owner of D3 - delivery of that Deliverable\n\n5.2") in text
    assert text.count("Priya Nair - owner of") == 1 and "priya  nair" not in text
    assert "D1: Migration plan.\n\nD2: Migrated warehouse.\n\nD3: Handover pack.\n\n6.2" in text


def test_three_owners_of_the_same_person_read_as_a_list_in_d_order():
    rows = [{"name": n, "due": "2026-12-01", "their_owner": "Priya Nair", "our_owner": "Ravi Shah"}
            for n in ("One", "Two", "Three")]
    assert "Priya Nair - owner of D1, D2 and D3 - delivery of those Deliverables" in _sow(
        _answers(deliverable_rows=rows))


def test_the_standard_wording_is_in_the_template_and_so_in_the_draft():
    """AC-5: out of scope, customer dependencies, location and reporting say what the playbook wants by
    their legal effect, and they are the template's own words (so a person can read and edit them)."""
    text = _sow(_answers())
    for words in (
        "No further exclusions have been agreed at the SOW Effective Date.",
        "No Customer dependency is listed at the SOW Effective Date.",
        "only where that dependency is recorded in a signed Change Order",
        "at its own premises or remotely, during its normal business hours",
        "only where the Customer asks in writing",
        "Customer owners named in Clause 4.3 weekly",
    ):
        assert words in text and words in default_body("sow")


def test_both_sides_have_a_representative():
    """AC-6: the Provider's is their signer as authorised signatory; the Customer's is the owner of each
    Deliverable, with a way to name an overall representative."""
    text = _sow(_answers())
    assert "Provider: Maya Chen (maya.chen@example.com), authorised signatory." in text
    assert "the Customer owner named for each Deliverable in Clause 4.3 is the Customer's representative" in text
    assert sow.sow_values(_answers(cp_signer_email=""))["provider_representative"] == "Maya Chen, authorised signatory"
    assert sow.sow_values(_answers(cp_signer_name=""))["provider_representative"] == ""


def test_the_fee_and_the_dates_read_as_the_contract_writes_them():
    """AC-9: "INR 4,500,000.00" and "30 November 2026"; D1 to D3 mean the same Deliverables in all four lists."""
    values = sow.sow_values(_answers("Milestones"))
    assert values["fee_amount"] == "INR 4,500,000.00"
    for i, name in enumerate(("Migration plan", "Migrated warehouse", "Handover pack"), 1):
        assert f"D{i}. {name}." in values["deliverables_list"]
        assert f"D{i}: {name}." in values["acceptance_list"]
        assert f"Acceptance of D{i} ({name})." in values["milestone_payments_list"]
    assert "owner of D1 and D2" in values["key_personnel_list"] and "owner of D3" in values["key_personnel_list"]


@pytest.mark.parametrize(("pricing", "statement"), [
    ("Fixed fee", "8.1 Pricing model: Fixed fee. The Fees for this SOW are the fixed fee stated in Clause 8.2."),
    ("Time and materials", ("8.1 Pricing model: Time and materials. The Fees for this SOW are charged on a "
                            "time-and-materials basis under Clause 8.3 and may not exceed the not-to-exceed amount "
                            "stated there.")),
    ("Milestones", ("8.1 Pricing model: Fixed fee payable in instalments. The Fees for this SOW are the fixed fee "
                    "stated in Clause 8.2, payable in the instalments set out in Clause 9.1, each only after the "
                    "Customer has accepted in writing the Deliverable it is linked to.")),
])
def test_the_pricing_is_stated_once_never_as_a_choice(pricing, statement):
    """AC-22: one model is stated, and the two models never appear as alternatives to choose from."""
    text = _sow(_answers(pricing))
    assert statement in text
    assert sum(text.count(s) for s in ("8.1 Pricing model: Fixed fee", "8.1 Pricing model: Time and")) == 1
    assert "/" not in next(line for line in text.splitlines() if line.startswith("8.1"))


@pytest.mark.parametrize("pricing", ["Fixed fee", "Time and materials", "Milestones"])
def test_the_total_is_the_fixed_fee_and_the_not_to_exceed_amount_with_the_other_clause_conditional(pricing):
    """AC-23: the clause for the model that was not chosen stays, opens by saying it applies only where the SOW
    is priced that way, and states the same total, so no clause contradicts another."""
    text = _sow(_answers(pricing))
    assert ("Where this SOW is priced as a fixed fee (including a fixed fee payable in instalments), the total fee "
            "for the Services and Deliverables is INR 4,500,000.00,") in text
    assert "Where this SOW is priced on a time-and-materials basis" in text
    assert "not-to-exceed amount of INR 4,500,000.00." in text


def test_a_fixed_fee_is_paid_in_equal_instalments_that_add_up():
    """AC-24: 100.00 over three rows is 33.33, 33.33 and 33.34; one row is the whole total."""
    text = _sow(_answers(value=100))
    assert _payments(text) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    one = _sow(_answers(value=100, deliverable_rows=_rows()[:1]))
    assert _payments(one) == [Decimal("100.00")] and "M2:" not in one


def test_an_equal_split_of_the_example_total_is_exact():
    """AC-46: 4,500,000.00 over three rows is 1,500,000.00 each."""
    assert _payments(_sow(_answers())) == [Decimal("1500000.00")] * 3


def test_time_and_materials_lists_no_payment_amount_and_keeps_the_holdback():
    """AC-24: no per-Deliverable amount; the section says why, and clause 9.2's hold-back until written
    acceptance is still there."""
    text = _sow(_answers("Time and materials"))
    assert sow.TM_NO_MILESTONES in text and _payments(text) == []
    assert "M1:" not in text
    assert "may hold back ten percent (10%) of each invoice until it has accepted all Deliverables in writing" in text


def test_milestone_payments_state_the_share_and_the_amount_and_add_up():
    """AC-47: "40% of the fixed fee: INR 1,800,000.00" and so on, each payable after written acceptance of its
    own Deliverable."""
    text = _sow(_answers("Milestones"))
    for i, line in enumerate((
        "40% of the fixed fee: INR 1,800,000.00", "35% of the fixed fee: INR 1,575,000.00",
        "25% of the fixed fee: INR 1,125,000.00"), 1):
        assert f"Payment: {line}, payable only after the Customer has accepted D{i} in writing." in text
    assert sum(_payments(text)) == Decimal("4500000.00")


def test_milestone_shares_that_do_not_divide_evenly_still_add_up():
    """AC-47: 100.01 at 33.33 / 33.33 / 33.34 is 33.33, 33.33 and 33.35; a decimal share shows without its
    trailing zeros; one row at 100 is the whole total."""
    rows = _rows(("33.33", "33.33", "33.34"))
    text = _sow(_answers("Milestones", value="100.01", deliverable_rows=rows))
    assert _payments(text) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.35")]
    assert "33.34% of the fixed fee: INR 33.35" in text
    decimals = _sow(_answers("Milestones", deliverable_rows=_rows(("12.50", "37.50", "50.00"))))
    assert "12.5% of the fixed fee: INR 562,500.00" in decimals and "50% of the fixed fee" in decimals
    single = _sow(_answers("Milestones", deliverable_rows=_rows(("100.00",))[:1]))
    assert _payments(single) == [Decimal("4500000.00")]


@pytest.mark.parametrize("pricing", ["Fixed fee", "Milestones"])
def test_every_payment_waits_for_written_acceptance_and_no_date(pricing):
    """AC-25 / FR-22: each payment names written acceptance of its own Deliverable, none is blank, and none
    is tied to a calendar date."""
    text = _sow(_answers(pricing))
    entries = [line for line in text.splitlines() if re.match(r"M\d+:", line)]
    assert len(entries) == 3
    for i, line in enumerate(entries, 1):
        assert line.endswith(f"payable only after the Customer has accepted D{i} in writing.")
        assert not re.search(r"\b(19|20)\d\d\b|January|February|March|April|May|June|July|August|"
                             r"September|October|November|December", line)
    assert all(p > 0 for p in _payments(text))


@pytest.mark.parametrize("pricing", ["Fixed fee", "Time and materials", "Milestones"])
def test_nothing_in_the_draft_deems_accepts_by_use_or_pays_by_date(pricing):
    """AC-8 / FR-5: the SOW playbook prohibits deemed acceptance; acceptance is in writing only, and a
    payment follows acceptance, never a date."""
    text = _sow(_answers(pricing)).lower()
    for phrase in ("deemed accepted", "deemed to be accepted", "deemed acceptance", "by use", "on the date",
                   "automatically accepted", "accepted automatically"):
        assert phrase not in text, phrase
    assert "a deliverable is accepted only when the customer confirms acceptance in writing" in text
    assert "no change to the scope, deliverables, due dates, key personnel or fees of this sow is effective" in text


# --- example content stays on the Templates page (FR-23, FR-24, AC-26, AC-27) --------------------


def test_the_example_reading_is_a_complete_blank_free_sow_that_adds_up():
    """AC-26: at least one Deliverable with name, date and both owners, matching acceptance, key personnel and
    payments, an amount, both representatives, every date inside the term, payments summing to the total."""
    text = _sow({}, example=True)
    _clean(text)
    assert _payments(text) == [Decimal("300000.00"), Decimal("600000.00"), Decimal("300000.00")]
    assert sum(_payments(text)) == Decimal("1200000.00") and "INR 1,200,000.00" in text
    answers = sow.EXAMPLE_ANSWERS
    for row in sow.rows_of(answers):
        assert date.fromisoformat(answers["services_start"]) <= date.fromisoformat(row["due"]) <= date.fromisoformat(
            answers["services_end"])
        assert row["name"] in text and row["their_owner"] in text and row["our_owner"] in text
    assert "Jordan Lee (jordan.lee@globex.example), authorised signatory" in text
    assert len(re.findall(r"^D\d\. ", text, re.MULTILINE)) == len(re.findall(r"^M\d: ", text, re.MULTILINE)) == 3


def test_the_preview_and_the_check_read_the_same_example():
    """FR-23: the preview fills from the field samples, the check from the example reading. If they drifted a
    person would preview one SOW and have another checked."""
    preview = render_sample(default_body("sow"))
    _clean(preview)
    assert all(SAMPLE_VALUES[n] == sow.EXAMPLE_VALUES[n] for n in sow.FIELDS)
    assert all(checks.check_values("sow")[n] == sow.EXAMPLE_VALUES[n] for n in sow.FIELDS)
    for text in (preview, _sow({}, example=True)):
        assert sum(_payments(text)) == Decimal("1200000.00")
    assert sow.EXAMPLE_VALUES["fee_amount"] in SAMPLE_VALUES["value_sentence"]


def test_the_example_is_distinct_from_every_request_in_the_acceptance_criteria():
    """AC-27: the example's names, deliverables and amounts are in no request the tests draft, so finding one
    in a draft can only mean it leaked."""
    drafts = [_sow(_answers(p)) for p in ("Fixed fee", "Time and materials", "Milestones")] + [_sow({})]
    for text in drafts:
        assert [m for m in sow.EXAMPLE_MARKERS if m in text] == []
    assert [m for m in sow.EXAMPLE_MARKERS if m in _sow({}, example=True)] != []  # the markers do find it


def test_a_request_with_one_row_drafts_exactly_that_deliverable():
    """AC-27: no leftover D2 or M2 from a longer example or template."""
    text = _sow(_answers(deliverable_rows=_rows()[:1]))
    assert "D1. Migration plan." in text and "D2" not in text and "M2" not in text
    assert "Tom Reyes" not in text and "Handover pack" not in text


def test_an_answer_the_request_left_out_is_not_filled_from_the_example():
    """FR-24: a SOW request with no rows, scope, price or signer drafts those places empty (the hold stops this
    in practice); it never drafts the example in their place."""
    text = _sow({"request_form": "sow", "sow_title": "Only a title"})
    assert 'titled "Only a title".' in text
    assert [m for m in sow.EXAMPLE_MARKERS if m in text] == []
    assert "{" not in text


# --- the other templates draft as before (FR-36, AC-39, AC-38) ----------------------------------


_ALL_ANSWERS = {"value": "4500000", "currency": "INR", "start_date": "2026-10-01", "term": "Fixed end date",
                "end_date": "2027-09-30", "payment_terms": "45 days", "nda_kind": "One-way",
                "nda_direction": "They share", "governing_law": "India"}


@pytest.mark.parametrize("key", sorted(set(TEMPLATES) - {"sow"}))
@pytest.mark.parametrize("fields", [{}, _ALL_ANSWERS], ids=["bare request", "full form"])
def test_no_other_template_changes_for_the_sow_fields(key, fields):
    """AC-39: the nine SOW fields are given to every template, and a template that does not use one ignores
    it (str.format), so its draft is exactly what it is filled with when the nine do not exist. Consultancy
    uses `services` and `fee_amount` (spec 008 FR-35: one field for one thing, so its schedule does not get
    a second field for the scope and the total); every other template uses none of the nine."""
    values = drafting.template_values(key, company=COMPANY, counterparty=COUNTERPARTY, effective="2026-10-01",
                                      fields=fields)
    assert set(sow.FIELDS) <= set(values)
    used = {f["name"] for f in field_uses(default_body(key))} & set(sow.FIELDS)
    assert used == ({"services", "fee_amount"} if key == "consultancy" else set())
    without = {k: v for k, v in values.items() if k not in set(sow.FIELDS) - used}
    drafted = drafting.render_document(key, company=COMPANY, counterparty=COUNTERPARTY, effective="2026-10-01",
                                       fields=fields)
    assert drafted == default_body(key).format(**without)


def test_the_blanks_the_other_templates_already_had_are_gone():
    """AC-39, then spec 008 AC-1 and AC-7: NDA, MSA, SaaS and Vendor never had a blank bracket; the SOW lost
    its own in 007, and Consultancy (5) and the DPA (6) lose theirs now, in the same way. A blank left in
    any shipped template reaches the counterparty and stops the contract at the signature gate."""
    blanks = {k: len(find_blanks(drafting.render_document(
        k, company=COMPANY, counterparty=COUNTERPARTY, effective="2026-10-01", fields={}))) for k in TEMPLATES}
    assert blanks == {"nda": 0, "msa": 0, "consultancy": 0, "sow": 0, "vendor": 0, "saas": 0, "dpa": 0}


def test_a_free_text_request_that_says_sow_is_still_drafted_from_the_msa_template():
    """AC-38 / FR-35: only a request filed on the Statement of Work form drafts from the SOW template; an email
    that says "SOW" resolves as it did before this feature."""
    free = SimpleNamespace(type_label="Contract Review", description="please draft a SOW", field_values={})
    assert drafting.resolve_doc_type(free) == "msa"
    filed = SimpleNamespace(type_label="Statement of Work Request", description="x", field_values={"request_form": "sow"})
    assert drafting.resolve_doc_type(filed) == "sow"


# --- the check and Apply (FR-23, FR-24, AC-26) --------------------------------------------------


def test_the_check_reads_the_example_and_a_suggestion_that_quotes_it_is_never_appliable():
    """FR-24: Apply writes a suggestion into our own paper. One that still quotes the example request (a
    deliverable, a person, an amount) would put "Pilot release" into every SOW we draft, so it is advice only.
    A suggestion that is plain wording, or that is a whole field's text, stays appliable."""
    values = checks.check_values("sow")

    def finding(fix):
        return {"rule_index": 1, "clause_type": "scope_and_deliverables", "severity": "high", "issue": "x",
                "quote": None, "suggested_fix": fix}

    out = checks._enrich([
        finding("Add the deliverable D4. Pilot release. Due date: 31 March 2027."),
        finding("Each Deliverable shall have a named owner on both sides."),
        finding(values["services"]),
        finding("Anika Rao shall remain Key Personnel."),
        finding("The Provider shall be paid INR 600,000.00 on acceptance."),
        finding("Include a [placeholder] here."),
    ], values)

    assert [f["suggestion_is_wording"] for f in out] == [False, True, True, False, False, False]
    assert out[2]["suggested_fix_template"] == "{services}"


def test_the_check_of_the_shipped_sow_sends_the_example_reading_with_no_blank(db, monkeypatch):
    """AC-26 / FR-28: the text the AI reviews is the example reading of the shipped wording: complete, with no
    blank bracket and no unfilled field, and a finding whose fix quotes the example is not appliable."""
    user, _, _ = _ready(db, "sow", SOW_PB)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(
        summary="One issue.", deviations=[_deviation(suggested_fix="Add D4. Pilot release.")])))
    check = _request(db, user, "sow")

    _run(db, check)

    (call,) = fake.calls
    sent = call["contract_context"].text
    _clean(sent)
    assert sent == drafting.render_document(
        "sow", company=SAMPLE_VALUES["company"], counterparty=SAMPLE_VALUES["counterparty"],
        effective=SAMPLE_VALUES["effective_date"], fields={}, example=True)
    assert [m for m in sow.EXAMPLE_MARKERS if m in sent] != []
    db.refresh(check)
    (finding,) = check.findings
    assert finding["suggestion_is_wording"] is False


# --- the release and the organisation's own wording (FR-26, FR-27, AC-30, AC-7) ------------------


def test_the_release_makes_a_check_of_the_old_wording_out_of_date_and_starts_nothing(db):
    """AC-30: a template last checked on the old shipped wording reads Out of date, saying the wording changed.
    Reading it starts no check, no job and no AI call: every review is paid for."""
    user, playbook, version = _ready(db, "sow", SOW_PB)
    _raw_check(db, user, "sow", state="completed", body=OLD_SOW, findings=[], finished_at=utcnow(),
               playbook_id=playbook.id, playbook_name=playbook.name, playbook_version_id=version.id,
               playbook_version_number=version.version_number, text_was_truncated=False)
    rows = [db.scalar(select(func.count()).select_from(m)) for m in (DraftingTemplateCheck, JobRun)]
    ai = _ai_runs(db, user.org_id)

    status = checks.template_status(db, org_id=user.org_id, ttype=get_type(db, user.org_id, "sow"))

    assert (status["state"], status["changed"]) == ("out_of_date", "wording")
    assert [db.scalar(select(func.count()).select_from(m)) for m in (DraftingTemplateCheck, JobRun)] == rows
    assert _ai_runs(db, user.org_id) == ai


def test_a_template_never_checked_stays_never_checked(db):
    user, _, _ = _ready(db, "sow", SOW_PB)
    status = checks.template_status(db, org_id=user.org_id, ttype=get_type(db, user.org_id, "sow"))
    assert status["state"] == "never_checked"


async def test_an_organisations_own_wording_keeps_drafting_as_before_with_no_hold(db, dispatched):
    """FR-27 / AC-30: a saved SOW wording with brackets and none of the new fields is not touched by the
    release; it drafts from it, even for an older request, and the rows are not asked for. Resetting then
    gives the new blank-free wording as a new version."""
    user, _, _ = _ready(db, "sow", SOW_PB)
    _save_version(db, user, "sow", OLD_SOW)
    req = _filed(db, user, _older_answers())
    assert drafting.draft_hold(db, org_id=user.org_id, request=req) is None
    disk = _Disk()

    contract = await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    (stored,) = disk.files.values()
    text = stored.decode("utf-8")
    assert contract.id and "D1. [Deliverable name and description]." in text and find_blanks(text) != []

    service = DraftingTemplateService(db)
    back = service.reset(actor=user, key="sow", note=None)
    assert back["changed"] is True and back["template"]["version"] == 2 and back["template"]["source"] == "default"
    assert find_blanks(service.body_for(org_id=user.org_id, key="sow")) == []


async def test_a_saved_change_to_the_standard_wording_is_in_the_next_draft(db, dispatched):
    """AC-7: the reporting frequency is the template's own wording, so "weekly" edited to "fortnightly" and
    saved is what the next draft says. (If it were supplied by code the edit would do nothing.)"""
    user, _, _ = _ready(db, "sow", SOW_PB)
    service = DraftingTemplateService(db)
    edited = default_body("sow").replace("named in Clause 4.3 weekly", "named in Clause 4.3 fortnightly")
    assert edited != default_body("sow")
    assert service.save(actor=user, key="sow", body=edited, note=None)["changed"] is True
    disk = _Disk()

    await _service(db, disk).draft_contract_for_request(actor=user, request=_filed(db, user, _answers()))

    (stored,) = disk.files.values()
    assert "Customer owners named in Clause 4.3 fortnightly, including" in stored.decode("utf-8")


# --- an older request is held (FR-33, AC-36, AC-48) ---------------------------------------------


def _count(db, model, org_id: str) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.org_id == org_id))


async def test_an_older_request_is_held_and_nothing_is_written(db, dispatched):
    """AC-36: no contract, no pin, no audit row, no stored file and no job; the free text is unchanged. Then,
    once rows are entered, the draft holds exactly those rows and none of the old text."""
    user = _world(db)
    _playbook(db, user, SOW_PB)  # published and matching by name, so a draft would pin it
    req = _filed(db, user, _older_answers())
    disk = _Disk()
    audits = _count(db, AuditLog, user.org_id)

    with pytest.raises(DraftHeld) as held:
        await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    assert held.value.status_code == 409 and held.value.detail == sow.DRAFT_HELD
    assert req.contract_id is None and disk.files == {} and dispatched == []
    assert _count(db, Contract, user.org_id) == 0 and _count(db, DraftingTemplateType, user.org_id) == 0
    assert _count(db, AuditLog, user.org_id) == audits
    assert req.field_values["deliverables"] == OLDER_TEXT and "deliverable_rows" not in req.field_values

    req.field_values = {**req.field_values, "deliverable_rows": _rows()}
    contract = await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    assert req.contract_id == contract.id
    (stored,) = disk.files.values()
    text = stored.decode("utf-8")
    assert "D1. Migration plan." in text and "D3. Handover pack." in text and "D4" not in text
    assert OLDER_TEXT not in text and [m for m in sow.EXAMPLE_MARKERS if m in text] == []
    assert req.field_values["deliverables"] == OLDER_TEXT  # nothing converted or removed it


def test_the_hold_needs_all_of_an_sow_request_no_rows_and_a_wording_that_uses_the_rows(db):
    """FR-33: only a SOW request with no rows is held, only while the wording in effect is built from the rows.
    Rows present, another form, or an organisation's wording without the row fields all draft."""
    user = _world(db)
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, _older_answers())) == sow.DRAFT_HELD
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, _answers())) is None
    msa = _filed(db, user, {"request_form": "new_agreement", "agreement_type": "Services (MSA)"})
    assert drafting.draft_hold(db, org_id=user.org_id, request=msa) is None
    _save_version(db, user, "sow", "SOW between {company} and {counterparty}: {sow_title}. {fee_amount}")
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, _older_answers())) is None
    _save_version(db, user, "sow", "SOW between {company} and {counterparty}:\n{acceptance_list}")
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, _older_answers())) == sow.DRAFT_HELD
    assert _count(db, DraftingTemplateType, user.org_id) == 0  # asking never pins


@pytest.fixture
def as_user(client, db):
    """Calls the draft-contract route as ``user`` on the test session, with an in-memory store."""
    from app.core.deps import get_db
    from app.main import app

    def act(user):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[intake_routes._READ] = lambda: user
        app.dependency_overrides[intake_routes._INTAKE_EDIT] = lambda: None
        app.dependency_overrides[get_drafting_service] = lambda: _service(db)
        return client

    return act


def test_the_route_answers_409_with_the_exact_message_and_writes_nothing(db, dispatched, as_user):
    """FR-33: the page shows this message, so it is the status and words the contract froze."""
    user = _world(db)
    _playbook(db, user, SOW_PB)
    req = _filed(db, user, _older_answers())
    audits = _count(db, AuditLog, user.org_id)

    res = as_user(user).post(f"/api/v1/intake/requests/{req.id}/draft-contract")

    assert res.status_code == 409 and res.json()["detail"] == sow.DRAFT_HELD
    assert req.contract_id is None and dispatched == []
    assert _count(db, AuditLog, user.org_id) == audits and _count(db, DraftingTemplateType, user.org_id) == 0


# --- what a draft records, and what example content never reaches (FR-37, FR-24, AC-40) ---------


async def test_a_sow_draft_records_its_rows_and_pricing_and_another_template_does_not(db, dispatched):
    """AC-40: the contract's metadata, its history and the audit trail all say three rows and "Fixed fee"
    beside the template record 006 writes; a draft of any other template carries neither key."""
    user, _, _ = _ready(db, "sow", SOW_PB)
    req = _filed(db, user, _answers())

    contract = await _service(db).draft_contract_for_request(actor=user, request=req)

    record = contract.metadata_json["drafted_from_template"]
    assert record["key"] == "sow" and record["deliverable_rows"] == 3 and record["pricing"] == "Fixed fee"
    (event,) = _events(db, contract, "contract.drafted_from_template")
    assert event.details["deliverable_rows"] == 3 and event.details["pricing"] == "Fixed fee"
    assert event.details["template_key"] == "sow" and event.details["template_version"] == 0
    (audit,) = [a for a in _audit(db, user.org_id, "intake.contract_drafted") if a.resource_id == req.id]
    assert audit.after["deliverable_rows"] == 3 and audit.after["pricing"] == "Fixed fee"
    assert audit.after["template_key"] == "sow"

    other, _, _ = _ready(db)
    msa = await _service(db).draft_contract_for_request(actor=other, request=_filed(db, other))
    assert not {"deliverable_rows", "pricing"} & set(msa.metadata_json["drafted_from_template"])
    (msa_event,) = _events(db, msa, "contract.drafted_from_template")
    assert not {"deliverable_rows", "pricing"} & set(msa_event.details)


async def test_the_record_keeps_a_missing_pricing_as_null(db, dispatched):
    """FR-37: a SOW request that never answered pricing records null, not the word "None"."""
    user, _, _ = _ready(db, "sow", SOW_PB)
    contract = await _service(db).draft_contract_for_request(
        actor=user, request=_filed(db, user, _answers(pricing=None)))
    assert contract.metadata_json["drafted_from_template"]["pricing"] is None


async def test_example_content_never_reaches_a_draft_made_for_a_real_request(db, dispatched):
    """FR-24, the other direction: a template that uses the SOW fields, drafted for a request that answered
    nothing, fills them with nothing. Only the check passes example=True."""
    user, _, _ = _ready(db)
    _save_version(db, user, "msa", "Agreement between {company} and {counterparty}.\n"
                  + "\n".join("{" + n + "}" for n in sow.FIELDS))
    disk = _Disk()

    await _service(db, disk).draft_contract_for_request(actor=user, request=_filed(db, user))

    (stored,) = disk.files.values()
    text = stored.decode("utf-8")
    assert text.startswith("Agreement between ") and text.strip().endswith("Globex Corporation.")
    assert [m for m in sow.EXAMPLE_MARKERS if m in text] == []
