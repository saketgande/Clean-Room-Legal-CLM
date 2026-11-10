"""Spec 014, pair fixes PF-15 to PF-21 after the 64-review AI sample: gaps the INR-only Check all cannot see.

Each test names the failure it guards. The deterministic phrase check cannot read meaning, so these pin the
facts the AI reviewer needs in the text: the figures a rule accepts, the stated place of incorporation, the
governing-law wording, a number where the Fees cap prints, a detailed scope, an Order Form reference, and a
risky counterparty document that reads like a contract."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.core.database import engine
from app.drafting_templates.standard import STANDARD_RULES
from app.intake import drafting, sow
from app.intake.paper import FEE_CAP_HELD, MINIMUMS, DraftContext, paper_values
from app.playbooks.library import PLAYBOOK_LIBRARY
from app.playbooks.service import _phrases
from eval import template_matrix as matrix
from eval.template_alignment import RISKY_SENTENCES, _risky_text
from tests.test_consultancy_dpa_drafting import _consultancy
from tests.test_template_check_jobs import _world
from tests.test_template_drafting_record import _filed

FOREIGN = [c for c in MINIMUMS if c != "INR"]


def _rule(playbook: str, clause_type: str):
    (p,) = [p for p in PLAYBOOK_LIBRARY if p["name"] == playbook]
    return next(r for r in p["rules"] if r["clause_type"] == clause_type)


def _first(key: str, *parts: str) -> str:
    _, answers, ctx, company, cp = matrix._pick(key, *parts) if parts else next(matrix.combinations(key))
    return matrix.render(key, answers, ctx, company=company, counterparty=cp)


def _with(key: str, **wanted) -> str:
    for label, answers, ctx, company, cp in matrix.combinations(key):
        if all(f"{k}={v}" in label for k, v in wanted.items()):
            return matrix.render(key, answers, ctx, company=company, counterparty=cp)
    raise AssertionError(wanted)


# PF-15 -----------------------------------------------------------------------------------------------

FLOOR_RULES = [("Master Services Agreement Playbook", "insurance"), ("Vendor Agreement Playbook", "insurance"),
               ("Software / SaaS Agreement Playbook", "insurance"),
               ("Software / SaaS Agreement Playbook", "limitation_of_liability"),
               ("Consultancy Agreement Playbook", "insurance")]


@pytest.mark.parametrize(("playbook", "clause"), FLOOR_RULES)
def test_rules_with_an_inr_floor_name_the_accepted_foreign_figures(playbook, clause):
    """The reviewer read 'USD 600,000' as possibly below 'INR 5 crore or the equivalent' because no rule said what
    the equivalent is. Every floor names the exact figures our paper writes, taken from paper.MINIMUMS."""
    text = _rule(playbook, clause)["preferred_position"]
    for cur in FOREIGN:
        assert MINIMUMS[cur]["five_crore"] in text, (playbook, clause, cur)


@pytest.mark.parametrize("key", ["msa", "vendor", "saas", "consultancy"])
@pytest.mark.parametrize("cur", FOREIGN)
def test_a_foreign_currency_draft_states_a_figure_its_rule_names(key, cur):
    """Whatever currency the fees are in, the minimum the draft prints is one the playbook rule accepts."""
    text = _with(key, currency=cur)
    assert MINIMUMS[cur]["five_crore"] in text


# PF-16 -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("doc", ["msa", "vendor", "saas"])
def test_foreign_paper_states_the_other_party_is_outside_india(doc):
    """A Singapore seat was chosen for a foreign party but the contract never said the party is foreign, so the
    reviewer read it as an Indian provider on a foreign seat."""
    ctx = DraftContext(counterparty_jurisdiction="United States")
    text = paper_values(doc, {}, company="Acme Ltd", counterparty="Globex", context=ctx)["dispute_resolution"]
    assert text.startswith("Globex is incorporated or established in United States, outside India.")
    assert "Singapore International Arbitration Centre" in text
    indian = paper_values(doc, {}, company="Acme Ltd", counterparty="Globex",
                          context=DraftContext(counterparty_jurisdiction="Karnataka"))["dispute_resolution"]
    assert "outside India" not in indian and "Singapore" not in indian


@pytest.mark.parametrize("name", ["Master Services Agreement Playbook", "Vendor Agreement Playbook",
                                  "Software / SaaS Agreement Playbook"])
def test_the_governing_law_rule_accepts_a_singapore_seat_for_a_stated_foreign_party(name):
    assert "states that the other party is incorporated or established outside India" in \
        _rule(name, "governing_law")["preferred_position"]


# PF-17 -----------------------------------------------------------------------------------------------


def test_nda_rule_says_a_us_state_law_is_not_the_prohibited_phrase():
    """'the laws of the State of Delaware' was read as the prohibited 'courts of the State of'. The rule names
    both wordings paper writes and says the prohibition is about submitting to US state courts."""
    pos = next(r for r in STANDARD_RULES["nda"] if r["clause_type"] == "governing_law_and_jurisdiction")[
        "preferred_position"]
    assert "governed by the laws of the State of Delaware" in pos and "the laws of the State of New York" in pos
    assert "not choosing a US state's law" in pos


# PF-18 -----------------------------------------------------------------------------------------------


def test_every_consultancy_combination_prints_a_fee_cap_number():
    """The fixed-fee combination rendered 'The Fees shall not exceed  except by' because the matrix held no value
    (a required form answer); no combination may print a blank cap."""
    for label, answers, ctx, company, cp in matrix.combinations("consultancy"):
        assert "shall not exceed  except" not in matrix.render("consultancy", answers, ctx, company=company,
                                                                counterparty=cp), label
        break  # the value is in the base answers, so one combination proves it for all
    assert "INR 1,200,000.00" in _first("consultancy")


@pytest.fixture
def db():
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def test_an_older_consultancy_request_without_a_value_is_held_not_drafted_blank(db):
    """A real request that reaches drafting with no contract value would print a blank where the Fees cap and the
    20% advance cap read their number; it is held with the reason, like the SOW's pricing hold."""
    user = _world(db)
    fv = {k: v for k, v in _consultancy("Fixed fee").items() if k != "value"}
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, fv)) == FEE_CAP_HELD
    assert drafting.draft_hold(db, org_id=user.org_id, request=_filed(db, user, _consultancy("Fixed fee"))) is None


# PF-19 -----------------------------------------------------------------------------------------------


def test_sow_matrix_scope_is_the_detailed_example_and_tax_names_gst():
    """The matrix fed a one-sentence scope, which the reviewer rightly called high level; and 'exclusive only of
    applicable taxes' named no tax. The fixed fee now names GST and where it is dealt with."""
    assert matrix._SCOPE == sow.EXAMPLE_ANSWERS["scope"] and "Out of scope" in matrix._SCOPE
    text = _with("sow", pricing="Fixed fee")
    assert "goods and services tax (GST)" in text and "applicable taxes" not in text


# PF-20 -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("hosting", ["European Union", "United States"])
def test_saas_hosting_outside_india_refers_to_the_order_form_and_the_rule_accepts_it(hosting):
    """The reviewer wanted an Order Form reference and the privacy function's approval; the clause carries both
    and the rule says such a location meets the position."""
    text = _with("saas", hosting=hosting)
    assert "shall be stated in the Order Form and is subject to the written approval of the Customer's privacy function" in text
    assert "stated in the Order Form" in _rule("Software / SaaS Agreement Playbook", "data_location")[
        "preferred_position"]


# PF-21 -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(STANDARD_RULES))
def test_risky_counterparty_text_is_full_sentences_with_the_prohibited_wording(key):
    """Bare fragments ('indemnify', 'residuals') under 'Additional terms' were judged a test artefact and the NDA
    risky run found nothing. Each risky term is a hand-written operative sentence containing the prohibited
    phrase, introduced as Special Conditions that prevail; the expected clause types are still recorded."""
    text, types = _risky_text(key, "BASE")
    assert "form part of this Agreement and prevail" in text
    rules = [r for r in STANDARD_RULES[key] if _phrases(r.get("prohibited_language"))]
    assert types == [r["clause_type"] for r in rules] and types
    for r in rules:
        sentence = RISKY_SENTENCES[(key, r["clause_type"])]  # a missing entry would fall back to a fragment
        assert sentence.endswith(".") and len(sentence.split()) >= 8
        phrase = _phrases(r["prohibited_language"])[0]
        if (key, r["clause_type"]) != ("nda", "definition_of_confidential_information"):  # a description, not text
            assert phrase.lower() in sentence.lower(), (key, r["clause_type"])
        assert sentence in text
