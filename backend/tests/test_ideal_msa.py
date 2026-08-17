"""The MSA — the workflow that broke the engine, and what it forced.

An NDA is the same shape every time, so the first engine handled it. An MSA is
not: Quality reviews GxP scope only, Privacy reviews personal data only, and
which approver may sign depends on the value. None of that could be expressed —
every step in a workflow always ran.

That gap is what ``Step.when`` closes. These tests are the proof, and the guard
against it regressing.
"""

import pytest

from app.ideal.library import CR, MSA
from app.ideal.workflow import (
    APPROVE,
    REQUEST_CHANGES,
    advance,
    matches,
    start,
    waiting_on,
)

SMALL_DOMESTIC = {"gxp": False, "personal_data": False, "systems_access": False,
                  "cross_border": False, "value_inr": 40_00_000}
CDMO = {"gxp": True, "personal_data": False, "systems_access": False,
        "cross_border": True, "value_inr": 8 * CR}
CLINICAL_VENDOR = {"gxp": True, "personal_data": True, "systems_access": True,
                   "cross_border": True, "value_inr": 2 * CR}


def _to_reviews(context):
    s = start(MSA, context=context)
    s = advance(s, MSA, step_id="prepare", outcome=APPROVE)
    s = advance(s, MSA, step_id="ai_review", outcome=APPROVE)
    return advance(s, MSA, step_id="legal_review", outcome=APPROVE, actor="A. Sharma")


def _open_steps(state):
    return {w["step"] for w in waiting_on(state, MSA)}


# ==========================================================================
# Only the reviewers this deal actually needs
# ==========================================================================

def test_a_simple_domestic_msa_pulls_in_two_reviewers():
    """No GxP, no personal data, no systems access, domestic — Finance and
    Procurement only. Asking Quality to review an IT services contract is how
    reviewers learn to rubber-stamp."""
    assert _open_steps(_to_reviews(SMALL_DOMESTIC)) == {"Finance review", "Procurement review"}


def test_a_gxp_cross_border_msa_pulls_in_quality_and_tax():
    assert _open_steps(_to_reviews(CDMO)) == {
        "Finance review", "Tax review", "Procurement review", "Quality & regulatory review",
    }


def test_a_clinical_data_vendor_pulls_in_everyone():
    assert _open_steps(_to_reviews(CLINICAL_VENDOR)) == {
        "Finance review", "Tax review", "Procurement review",
        "Quality & regulatory review", "Data privacy review", "IT security review",
    }


def test_what_was_NOT_required_is_recorded_not_merely_absent():
    """'Quality did not review' and 'Quality was never required' are different
    facts. An inspector wants the second one stated."""
    s = _to_reviews(SMALL_DOMESTIC)
    skipped = {e.step_id for e in s.events if e.kind == "not_applicable"}
    assert skipped == {"tax", "quality", "privacy", "security"}
    reason = next(e.reason for e in s.events
                  if e.kind == "not_applicable" and e.step_id == "quality")
    assert "gxp" in reason


# ==========================================================================
# Who may sign depends on the money
# ==========================================================================

@pytest.mark.parametrize(
    "value,expected",
    [
        (40_00_000, "Head of Legal approval"),
        (1 * CR, "General Counsel approval"),
        (4 * CR, "General Counsel approval"),
        (5 * CR, "Board approval"),
        (80 * CR, "Board approval"),
    ],
)
def test_the_signing_authority_follows_the_value(value, expected):
    ctx = {**SMALL_DOMESTIC, "value_inr": value}
    s = _to_reviews(ctx)
    for step in ("finance", "procurement"):
        s = advance(s, MSA, step_id=step, outcome=APPROVE)
    assert _open_steps(s) == {expected}


def test_exactly_one_authority_band_ever_applies():
    """The three bands must not overlap — two approvers for one contract would
    be a genuine governance defect."""
    bands = [MSA.step(i).when for i in ("approve_head", "approve_gc", "approve_board")]
    for value in (0, 99_99_999, 1 * CR, 4_99_99_999, 5 * CR, 100 * CR):
        hits = [w for w in bands if matches(w, {"value_inr": value})]
        assert len(hits) == 1, f"{value} matched {len(hits)} bands"


# ==========================================================================
# The condition matcher
# ==========================================================================

def test_a_missing_fact_fails_closed():
    """If we do not know whether the scope is GxP, Quality is NOT silently
    skipped. Unknown must never mean 'not required'."""
    assert matches({"gxp": True}, {}) is False
    assert matches({"value_inr": {"gte": CR}}, {}) is False


def test_no_condition_always_applies():
    assert matches(None, {}) is True
    assert matches({}, {"anything": 1}) is True


# ==========================================================================
# Everything the NDA proved still holds on the bigger workflow
# ==========================================================================

def test_a_specialist_can_still_send_the_whole_thing_back():
    s = _to_reviews(CDMO)
    s = advance(s, MSA, step_id="quality", outcome=REQUEST_CHANGES,
                actor="R. Iyer", comment="needs a quality agreement")
    for step in ("finance", "tax", "procurement"):
        s = advance(s, MSA, step_id=step, outcome=APPROVE)
    assert _open_steps(s) == {"Prepare the working document"}


def test_a_counterparty_markup_rejoins_the_review_loop():
    s = _to_reviews(SMALL_DOMESTIC)
    for step in ("finance", "procurement"):
        s = advance(s, MSA, step_id=step, outcome=APPROVE)
    s = advance(s, MSA, step_id="approve_head", outcome=APPROVE, actor="Head of Legal")
    assert _open_steps(s) == {"With the counterparty"}
    s = advance(s, MSA, step_id="counterparty", outcome=REQUEST_CHANGES, comment="markup")
    assert _open_steps(s) == {"Automated review"}


def test_the_nda_is_unaffected_by_conditions_it_does_not_use():
    """Adding `when` must not change a workflow that declares none."""
    from app.ideal.library import NDA

    assert all(s.when is None for s in NDA.steps.values())
