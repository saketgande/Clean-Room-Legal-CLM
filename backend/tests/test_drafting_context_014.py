"""Spec 014 / T005: paper.py wired into drafting (context, no Delaware fallback, holds, placeholders, matrix).

The per-template "0 findings" checks belong to the template tasks; this file pins the wiring they rely on.
"""

from __future__ import annotations

import re
import time

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.contracts.models import Contract
from app.core.database import engine
from app.drafting_templates import checks
from app.drafting_templates.service import (
    PLACEHOLDERS,
    SAMPLE_VALUES,
    TEMPLATES,
    check_body,
    render_sample,
)
from app.intake import drafting, paper
from app.parties.models import Counterparty, LegalEntity
from eval import template_matrix as matrix
from tests.test_sow_drafting import SCOPE
from tests.test_template_check_jobs import _save_version, _world
from tests.test_template_drafting_record import _filed, _service

EXPECTED_SIZE = {"nda": 378, "msa": 2112, "sow": 192, "vendor": 2376, "saas": 3168, "consultancy": 2112, "dpa": 200}


@pytest.fixture
def db():
    """One outer transaction per test, so rows made here never outlive it."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _values(doc, fields=None, **kw):
    return drafting.template_values(doc, company="A Ltd", counterparty="B Ltd", effective="2026-10-01",
                                    fields=fields or {}, **kw)


# --- the fill-in values ---------------------------------------------------------------------------------


@pytest.mark.parametrize("doc", sorted(TEMPLATES))
def test_every_template_is_given_all_paper_fields(doc):
    """A wording that uses a paper field must never hit a KeyError for a doc type that doesn't fill it."""
    assert set(paper.FIELDS) <= set(_values(doc))


@pytest.mark.parametrize("answer", ["", "Maharashtra, India", "Mumbai", "Delaware corporation law"])
@pytest.mark.parametrize("doc", ["msa", "vendor", "saas", "sow", "dpa", "consultancy"])
def test_no_template_but_the_nda_ever_names_delaware_or_a_state(doc, answer):
    """FR-12/13: the old fallback wrote 'the State of Delaware' into a draft whose form never asked for it."""
    assert _values(doc, {"governing_law": answer})["governing_law"] == "India"
    assert "Delaware" not in _values(doc)["governing_law"]


def test_the_nda_keeps_its_five_choices_and_anything_else_is_india():
    """FR-12: only the NDA form asks for a law; a blank or unrecognised answer is India, not Delaware."""
    got = {a: _values("nda", {"governing_law": a})["governing_law"]
           for a in ("India", "England and Wales", "Singapore", "New York", "Delaware", "", "Maharashtra, India")}
    assert got == {"India": "India", "England and Wales": "England and Wales", "Singapore": "Singapore",
                   "New York": "the State of New York", "Delaware": "the State of Delaware", "": "India",
                   "Maharashtra, India": "India"}


def test_the_nda_term_is_written_in_words():
    """FR-27: 'the term of 2 years' read as a number in a sentence; the term is now words with the figure."""
    assert [_values("nda", {"nda_term": t})["term_clause"] for t in ("2 years", "3 years", "5 years", "")] == [
        "continues for two (2) years", "continues for three (3) years", "continues for five (5) years",
        "continues for three (3) years"]


def test_payment_defaults_to_45_days_for_every_type():
    """FR-3: consultancy used to default to 30 days, which no playbook accepts any more."""
    assert {_values(d)["payment_days"] for d in TEMPLATES} == {"45"}
    assert _values("msa", {"payment_terms": "60 days"})["payment_days"] == "60"


def test_saas_renewal_notice_is_one_period_of_at_least_sixty_days():
    """FR-44: clauses 6.1 and 6.2 must state the same notice, and a 30-day pick is lifted to sixty."""
    fv = {"end_date": "2027-09-30", "term": "Renews automatically", "renewal_term": "1 year", "notice_days": "30 days"}
    assert "sixty (60) days' written notice" in _values("saas", fv)["agreement_term"]
    assert _values("saas", fv)["renewal_notice_days"] == "sixty (60)"
    assert "90 days' written notice" in _values("msa", {**fv, "notice_days": "90 days"})["agreement_term"]


def test_the_register_rows_reach_the_values_and_a_bare_request_gets_blank_lines():
    """FR-18: a real draft prints the register's address and signer, else a line to write on, never the
    example person or an invented address."""
    ctx = paper.DraftContext(entity_address="3 Nariman Point, Mumbai 400021", entity_signatory="R. Iyer, Director",
                             counterparty_address="9 Park Street, Kolkata")
    got = _values("msa", {"cp_signer_name": "Jordan Lee, Delivery Lead"}, context=ctx)
    assert (got["our_signatory_name"], got["our_signatory_title"]) == ("R. Iyer", "Director")
    assert (got["their_signatory_name"], got["their_signatory_title"]) == ("Jordan Lee", "Delivery Lead")
    assert got["company_address"] == "3 Nariman Point, Mumbai 400021" and got["dispute_seat"] == "Mumbai"
    bare = _values("msa")
    assert bare["our_signatory_name"] == paper.BLANK and bare["company_address"] == paper.BLANK
    assert paper.EXAMPLE_MARKERS[0] not in str(bare)


def test_example_context_is_used_only_when_a_check_asks_for_it():
    """FR-24: the Templates-page check shows the example office; a draft for a request must never."""
    assert "One BKC" in _values("msa", example=True)["company_address"]
    assert not any(m in str(_values("msa")) for m in paper.EXAMPLE_MARKERS)


def test_a_wording_can_use_a_paper_field_and_render_document_fills_it():
    """The new fields are real placeholders end to end: save validation accepts them and drafting fills them."""
    body = "Seat {dispute_seat}; sign {our_signatory_name}; {company} and {counterparty}."
    check_body(body)
    ctx = paper.DraftContext(entity_address="1 Marine Drive, Mumbai", entity_signatory="A. Rao, Director")
    assert drafting.render_document("msa", company="A", counterparty="B", effective="2026-10-01", body=body,
                                    context=ctx) == "Seat Mumbai; sign A. Rao; A and B."


# --- the placeholder catalogue and the check --------------------------------------------------------------


def test_the_catalogue_lists_every_paper_field_with_its_own_name():
    """FR-55: a field drafting fills but the catalogue lacks would make a saved wording using it refuse."""
    listed = {p["name"]: p for p in PLACEHOLDERS}
    assert [n for n in paper.FIELDS if n not in listed] == []
    assert all(listed[n]["label"] and listed[n]["about"] for n in paper.FIELDS)
    assert len({p["label"] for p in PLACEHOLDERS}) == len(PLACEHOLDERS)


def test_the_samples_come_from_the_example_reading_and_the_preview_fills_them():
    """The editor preview prints these samples, so they must be the real wording, not blanks."""
    assert "One BKC" in SAMPLE_VALUES["company_address"] and SAMPLE_VALUES["fee_currency"] == "INR"
    assert SAMPLE_VALUES["gxp_clause"] and SAMPLE_VALUES["hosting_clause"] and SAMPLE_VALUES["vendor_insurance"]
    assert "Priya Nair" in render_sample("{our_signatory_name}")


def test_the_check_turns_paper_values_back_into_fields_and_never_applies_example_content():
    """A suggested fix quoting the sample address must become {company_address} or be advice only."""
    values = checks.check_values("msa")
    assert set(paper.FIELDS) <= set(checks._CODED) and set(paper.EXAMPLE_MARKERS) <= set(checks._EXAMPLE_MARKERS)
    assert "{company_address}" in checks.template_form_of(f"Office: {values['company_address']}.", values)


# --- the context a request is drafted with ----------------------------------------------------------------


def test_the_draft_context_reads_the_register_rows_and_the_parent_of_this_organisation(db):
    """FR-14 to FR-18: signatory, offices and the parent come from the register rows named on the request;
    a parent contract of another organisation is ignored (same guard as apply_change_request)."""
    user = _world(db)
    other = _world(db, "Other")
    entity = LegalEntity(org_id=user.org_id, name="Aegis Pharma Ltd", registered_address="Level 5, Mumbai 400051",
                         authorised_signatory="R. Iyer, Director")
    cp = Counterparty(org_id=user.org_id, name="Globex Corporation", jurisdiction="United States",
                      address="1 Main St, Austin")
    mine = Contract(org_id=user.org_id, title="Master Services Agreement", owner_user_id=user.id,
                    created_by_user_id=user.id)
    theirs = Contract(org_id=other.org_id, title="Someone else's contract", owner_user_id=other.id,
                      created_by_user_id=other.id)
    db.add_all([entity, cp, mine, theirs])
    db.flush()
    req = _filed(db, user, {"request_form": "sow", "parent_contract_id": mine.id})
    req.legal_entity_id, req.counterparty_id = entity.id, cp.id
    ctx = _service(db)._draft_context(req)
    assert (ctx.entity_address, ctx.entity_signatory) == ("Level 5, Mumbai 400051", "R. Iyer, Director")
    assert (ctx.counterparty_jurisdiction, ctx.counterparty_address) == ("United States", "1 Main St, Austin")
    assert ctx.parent_title == "Master Services Agreement"
    req.field_values = {"request_form": "sow", "parent_contract_id": theirs.id}
    assert _service(db)._draft_context(req).parent_title is None
    assert _service(db)._draft_context(_filed(db, user, {"request_form": "sow"})) == paper.DraftContext()


# --- the holds ------------------------------------------------------------------------------------------


def _sow_answers(**over) -> dict:
    return {"request_form": "sow", "sow_title": "T", "scope": SCOPE, "value": "1200000", "pricing": "Fixed fee",
            "deliverable_rows": [{"name": "A", "due": "2026-12-01", "their_owner": "X", "our_owner": "Y"}], **over}


def test_a_sow_without_pricing_or_value_is_held_only_when_the_wording_prints_the_pricing(db):
    """FR-35: the draft prints only the pricing chosen, so a wording built from it waits for the model and the
    total; a wording that does not use those fields is never held (an org's own paper keeps drafting), so
    the shipped wording holds and an organisation's older wording without a pricing field does not."""
    user = _world(db)

    def held(fv: dict):
        return drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, fv))

    assert held(_sow_answers(pricing="", value="")) == paper.SOW_PRICING_HELD  # the shipped wording prints it
    _save_version(db, user, "sow", "{company} {counterparty} {services}")
    assert held(_sow_answers(pricing="", value="")) is None  # an org wording with no pricing field
    _save_version(db, user, "sow", "{company} {counterparty} {pricing_terms} {services}")
    assert held(_sow_answers(pricing="")) == paper.SOW_PRICING_HELD
    assert held(_sow_answers(value="")) == paper.SOW_PRICING_HELD
    assert held(_sow_answers(value="not a number")) == paper.SOW_PRICING_HELD
    assert held(_sow_answers()) is None


@pytest.mark.parametrize("scope", ["TBD", "This is a test", "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor",
                                   "Short scope of only a few words"])
def test_placeholder_scope_holds_a_sow_and_a_consultancy_on_our_paper(db, scope):
    """FR-25: test text must not be printed as the services the other side must perform."""
    user = _world(db)
    sow_req = _filed(db, user, _sow_answers(scope=scope))
    assert drafting.draft_hold(db, org_id=user.org_id, request=sow_req) == paper.SCOPE_HELD
    consultancy = {"request_form": "new_agreement", "agreement_type": "Consultancy", "paper": "Our template",
                   "scope": scope, "fee_basis": "Fixed fee"}
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, consultancy)) == paper.SCOPE_HELD
    theirs = {**consultancy, "paper": "Their paper"}
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, theirs)) is None


def test_the_new_holds_use_the_answers_branch_and_resume_on_their_own():
    """Both messages go through the existing 'answers' step note, so a held workflow step carries on by itself."""
    for message in (paper.SOW_PRICING_HELD, paper.SCOPE_HELD):
        note, result = drafting.held_step(message)
        assert result == {"held": "answers"} and note.startswith(message) and note.endswith(drafting.ANSWERS_STEP_SUFFIX)


# --- the combination matrix -------------------------------------------------------------------------------


def test_the_matrix_has_the_planned_size_and_reads_its_choices_from_the_form():
    """FR-2: a matrix that silently shrank (a form option removed) would stop proving what it claims to."""
    assert {k: sum(1 for _ in matrix.combinations(k)) for k in matrix.KEYS} == EXPECTED_SIZE
    assert not {"30 days"} & {f for c in matrix.combinations("msa") for f in [c[1]["payment_terms"]]}
    assert not {"30 days"} & {c[1]["payment_terms"] for c in matrix.combinations("consultancy")}
    assert len(matrix.MAIN) == 19


def test_every_combination_fills_completely_with_no_delaware_and_the_right_currency():
    """FR-1A groundwork: whatever the answers, no unfilled brace or 'Delaware' (unless an NDA picked it), and
    the labels are unique so a failure names its combination. Runs on today's wording; the template tasks
    add the playbook assertions. Also guards the 20-second budget the plan sets for the whole matrix."""
    start = time.perf_counter()
    problems: list[str] = []
    for key in matrix.KEYS:
        labels = set()
        for label, fields, ctx, company, cp in matrix.combinations(key):
            assert label not in labels, label
            labels.add(label)
            text = matrix.render(key, fields, ctx, company=company, counterparty=cp)
            if re.search(r"[{}]", text) or ("Delaware" in text and "governing_law=Delaware" not in label):
                problems.append(label)
    assert not problems, problems[:5]
    assert time.perf_counter() - start < 20
