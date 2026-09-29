"""Adversarial-input battery for the condition evaluator (feature 004, T002).

Covers FR-2/FR-3/FR-4 and AC-3/AC-4/AC-6: the five fixed operators, fail-closed
behaviour on every kind of malformed/adversarial input, and source-level
confirmation that no dynamic-code-execution primitive is used anywhere in the
evaluator module.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.approval_chains import conditions as cond

ALLOWED = frozenset({"contract_value", "jurisdiction", "tags", "title", "risk_band"})


# --- happy path: each operator true/false -----------------------------------


def test_gt_true_and_false():
    expr = {"field": "contract_value", "operator": "gt", "value": 1_000_000}
    true_result = cond.evaluate_condition(expr, {"contract_value": 1_200_000}, allowed_fields=ALLOWED)
    assert true_result.satisfied is True
    assert true_result.malformed is False

    false_result = cond.evaluate_condition(expr, {"contract_value": 500_000}, allowed_fields=ALLOWED)
    assert false_result.satisfied is False
    assert false_result.malformed is False


def test_lt_true_and_false():
    expr = {"field": "contract_value", "operator": "lt", "value": 1_000_000}
    assert cond.evaluate_condition(expr, {"contract_value": 500_000}, allowed_fields=ALLOWED).satisfied is True
    assert cond.evaluate_condition(expr, {"contract_value": 1_500_000}, allowed_fields=ALLOWED).satisfied is False


def test_eq_true_and_false():
    expr = {"field": "jurisdiction", "operator": "eq", "value": "EU"}
    assert cond.evaluate_condition(expr, {"jurisdiction": " eu "}, allowed_fields=ALLOWED).satisfied is True
    assert cond.evaluate_condition(expr, {"jurisdiction": "US"}, allowed_fields=ALLOWED).satisfied is False


def test_in_true_and_false():
    expr = {"field": "jurisdiction", "operator": "in", "value": ["EU", "UK"]}
    assert cond.evaluate_condition(expr, {"jurisdiction": "UK"}, allowed_fields=ALLOWED).satisfied is True
    assert cond.evaluate_condition(expr, {"jurisdiction": "US"}, allowed_fields=ALLOWED).satisfied is False


def test_contains_true_and_false():
    expr = {"field": "title", "operator": "contains", "value": "Master"}
    assert cond.evaluate_condition(expr, {"title": "Master Services Agreement"}, allowed_fields=ALLOWED).satisfied is True
    assert cond.evaluate_condition(expr, {"title": "NDA"}, allowed_fields=ALLOWED).satisfied is False

    list_expr = {"field": "tags", "operator": "contains", "value": "urgent"}
    assert cond.evaluate_condition(list_expr, {"tags": ["urgent", "legal"]}, allowed_fields=ALLOWED).satisfied is True
    assert cond.evaluate_condition(list_expr, {"tags": ["legal"]}, allowed_fields=ALLOWED).satisfied is False


# --- adversarial / fail-closed inputs ----------------------------------------


def test_and_or_combinator_is_malformed_never_raises():
    expr = {"and": [{"field": "a", "operator": "gt", "value": 1}, {"field": "b", "operator": "lt", "value": 2}]}
    result = cond.evaluate_condition(expr, {"a": 5, "b": 1}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True

    expr_extra_key = {"field": "contract_value", "operator": "gt", "value": 1, "or": []}
    result2 = cond.evaluate_condition(expr_extra_key, {"contract_value": 5}, allowed_fields=ALLOWED)
    assert result2.satisfied is False
    assert result2.malformed is True


@pytest.mark.parametrize("bad_op", ["exec", "$where", "__import__", "eval", ">", "=="])
def test_unknown_operator_is_malformed_never_raises(bad_op):
    expr = {"field": "contract_value", "operator": bad_op, "value": 1}
    result = cond.evaluate_condition(expr, {"contract_value": 5}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True


def test_field_not_in_allowed_fields_is_rejected_never_raises():
    expr = {"field": "ssn", "operator": "eq", "value": "123-45-6789"}
    result = cond.evaluate_condition(expr, {"ssn": "123-45-6789"}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True
    assert result.reason == "unknown_field"


def test_field_missing_from_facts_is_not_satisfied_never_raises():
    expr = {"field": "contract_value", "operator": "gt", "value": 1000}
    result = cond.evaluate_condition(expr, {}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is False
    assert result.reason == "field_missing"


def test_oversized_string_value_is_rejected_never_raises():
    huge_string = "x" * 1_000_000
    expr = {"field": "title", "operator": "contains", "value": huge_string}
    result = cond.evaluate_condition(expr, {"title": "whatever"}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True


def test_oversized_list_value_is_rejected_never_raises():
    huge_list = list(range(50_000))
    expr = {"field": "jurisdiction", "operator": "in", "value": huge_list}
    result = cond.evaluate_condition(expr, {"jurisdiction": 5}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True


def test_deeply_nested_dict_value_is_rejected_never_raises():
    nested = {"a": {"b": {"c": {"d": [1, 2, {"e": "f"}]}}}}
    expr = {"field": "contract_value", "operator": "eq", "value": nested}
    result = cond.evaluate_condition(expr, {"contract_value": nested}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True


def test_recursive_self_referential_list_never_hangs_or_raises():
    recursive: list = [1, 2, 3]
    recursive.append(recursive)  # self-reference
    expr = {"field": "contract_value", "operator": "in", "value": recursive}
    result = cond.evaluate_condition(expr, {"contract_value": 1}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True


@pytest.mark.parametrize(
    "bad_expression",
    [
        None,
        "not a dict",
        123,
        [],
        {},
        {"field": "contract_value"},
        {"field": "contract_value", "operator": "gt"},
        {"operator": "gt", "value": 1},
        {"field": 123, "operator": "gt", "value": 1},
        {"field": "contract_value", "operator": 123, "value": 1},
        {"field": "", "operator": "gt", "value": 1},
    ],
)
def test_completely_malformed_expression_never_raises(bad_expression):
    result = cond.evaluate_condition(bad_expression, {"contract_value": 5}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is True


def test_type_mismatch_between_condition_value_and_fact_never_raises():
    expr = {"field": "contract_value", "operator": "gt", "value": 1000}
    result = cond.evaluate_condition(expr, {"contract_value": "not a number"}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is False
    assert result.reason == "type_mismatch"


def test_non_mapping_facts_never_raises():
    expr = {"field": "contract_value", "operator": "gt", "value": 1000}
    result = cond.evaluate_condition(expr, None, allowed_fields=ALLOWED)  # type: ignore[arg-type]
    assert result.satisfied is False


def test_bool_excluded_from_gt_lt_numeric_comparison():
    expr = {"field": "contract_value", "operator": "gt", "value": True}
    result = cond.evaluate_condition(expr, {"contract_value": 5}, allowed_fields=ALLOWED)
    assert result.satisfied is False
    assert result.malformed is False
    assert result.reason == "type_mismatch"


# --- source-level non-execution regression -----------------------------------


def _code_lines_without_comments_or_docstrings(source: str) -> str:
    """Strip full-line/trailing comments and triple-quoted docstring bodies,
    which legitimately mention forbidden tokens in PROSE describing what this
    module deliberately does NOT do — leaving only executable code to scan."""
    import re

    no_comments = re.sub(r"#.*", "", source)
    no_docstrings = re.sub(r'"""[\s\S]*?"""', "", no_comments)
    return no_docstrings


def test_evaluator_module_never_uses_dynamic_execution_primitives():
    source = inspect.getsource(cond)
    code_only = _code_lines_without_comments_or_docstrings(source)
    forbidden = ["eval(", "exec(", "compile(", "__import__", "getattr(", "setattr("]
    for token in forbidden:
        assert token not in code_only, f"forbidden token {token!r} found in conditions.py"


def test_validate_expression_never_uses_dynamic_execution_primitives():
    source = inspect.getsource(cond.validate_expression)
    forbidden = ["eval(", "exec(", "compile(", "__import__", "getattr(", "setattr("]
    for token in forbidden:
        assert token not in source


# --- validate_expression (config-time 422 gate) ------------------------------


def test_validate_expression_accepts_well_formed_rule():
    cond.validate_expression(
        {"field": "contract_value", "operator": "gt", "value": 1_000_000}, allowed_fields=ALLOWED
    )


def test_validate_expression_rejects_combinator():
    with pytest.raises(HTTPException) as exc_info:
        cond.validate_expression(
            {"field": "contract_value", "operator": "gt", "value": 1, "and": []}, allowed_fields=ALLOWED
        )
    assert exc_info.value.status_code == 422


def test_validate_expression_rejects_unknown_field():
    with pytest.raises(HTTPException) as exc_info:
        cond.validate_expression({"field": "ssn", "operator": "eq", "value": "x"}, allowed_fields=ALLOWED)
    assert exc_info.value.status_code == 422


def test_validate_expression_rejects_unknown_operator():
    with pytest.raises(HTTPException) as exc_info:
        cond.validate_expression(
            {"field": "contract_value", "operator": "exec", "value": 1}, allowed_fields=ALLOWED
        )
    assert exc_info.value.status_code == 422


def test_validate_expression_rejects_oversized_value():
    with pytest.raises(HTTPException):
        cond.validate_expression(
            {"field": "title", "operator": "contains", "value": "x" * 1_000_000}, allowed_fields=ALLOWED
        )


# --- render_explanation exact FR-7 format ------------------------------------


def test_render_explanation_matches_fr7_example_format():
    expr = {"field": "contract_value", "operator": "gt", "value": 1_000_000}
    result = cond.evaluate_condition(expr, {"contract_value": 1_200_000}, allowed_fields=ALLOWED)
    assert result.satisfied is True
    assert cond.render_explanation(result) == "contract_value (1,200,000) > 1,000,000"


def test_render_explanation_empty_for_malformed_result():
    expr = {"and": []}
    result = cond.evaluate_condition(expr, {}, allowed_fields=ALLOWED)
    assert cond.render_explanation(result) == ""


def test_render_condition_text_config_time_label():
    expr = {"field": "contract_value", "operator": "gt", "value": 1_000_000}
    assert cond.render_condition_text(expr) == "contract_value > 1,000,000"
    assert cond.render_condition_text({"and": []}) == "(invalid condition)"
