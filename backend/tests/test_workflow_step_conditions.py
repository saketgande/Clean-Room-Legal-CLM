"""WF-02: step rules evaluate as documented: typed values, numeric operators, the
legacy skip_if key, and a rule that can't be evaluated never skips a step."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.workflows import service
from app.workflows.builtin import BUILTIN_FLOWS


def _req(**fields):
    return SimpleNamespace(type_label="", description="", priority=None, department=None, field_values=fields)


def _step(flow_name, step_name):
    flow = next(f for f in BUILTIN_FLOWS if f["name"] == flow_name)
    return next(s for s in flow["steps"] if s["name"] == step_name)


def test_every_builtin_rule_passes_save_time_validation():
    for flow in BUILTIN_FLOWS:
        service._clean_steps(flow["steps"])


def test_finance_review_is_skipped_only_for_small_contracts():
    step = _step("Contract Approval Ladder", "Finance Review")
    assert service._skip_reason(step, _req(contract_value="5,000")) == "Skipped by rule"
    assert service._skip_reason(step, _req(contract_value="$25,000")) is None
    assert service._skip_reason(step, _req(value=2500)) == "Skipped by rule"  # read via the approval bridge keys
    assert service._skip_reason(step, _req()) is None  # value unknown: the review runs


def test_msa_finance_approval_runs_for_high_or_unknown_value():
    step = _step("Master Services Agreement", "Finance approval")
    assert service._skip_reason(step, _req(contract_value=50000)) is None
    assert service._skip_reason(step, _req(contract_value="2000")) == "Condition not met"
    assert service._skip_reason(step, _req()) is None


def test_yes_no_answers_match_whether_stored_as_text_or_bool():
    step = _step("Master Services Agreement", "Quality review")
    assert service._skip_reason(step, _req(gxp=True)) is None
    assert service._skip_reason(step, _req(gxp="true")) is None
    assert service._skip_reason(step, _req(gxp="No")) == "Condition not met"


def test_flows_seeded_with_the_legacy_skip_if_key_still_skip():
    step = {"name": "Outside Counsel Engagement", "type": "human_task",
            "config": {"skip_if": {"field": "handled_inhouse", "op": "eq", "value": True}}}
    assert service._skip_reason(step, _req(handled_inhouse="yes")) == "Skipped by rule"
    assert service._skip_reason(step, _req(handled_inhouse=False)) is None


def test_unsupported_operator_never_skips_and_is_rejected_on_save():
    step = {"name": "Odd rule", "type": "human_task",
            "config": {"skip_when": {"field": "region", "op": "contains", "value": "eu"}}}
    assert service._skip_reason(step, _req(region="eu-west")) is None
    with pytest.raises(HTTPException):
        service._clean_steps([step])


def test_numeric_operator_without_a_number_is_rejected_on_save():
    with pytest.raises(HTTPException):
        service._clean_steps([{"type": "approval", "name": "Finance",
                               "cond": {"field": "contract_value", "op": "gte", "value": "lots"}}])
