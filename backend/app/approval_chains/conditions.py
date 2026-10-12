"""The restricted, non-`eval()` condition evaluator (feature 004).

Pure, DB-free, import-free of any model. Evaluates EXACTLY the five
comparison operators fixed by FR-2 (``gt``, ``lt``, ``eq``, ``in``,
``contains``) against a single named fact. There is no boolean combinator, no
nesting, and no sixth operator — a compound business rule is expressed as
multiple single-condition rules (FR-3), never as a richer expression
language.

Security invariant (FR-4, AC-3, AC-4): this module contains NO ``eval``,
``exec``, ``compile``, ``__import__``, and no ``getattr``/``setattr`` on any
attacker-controlled name. Dispatch is a lookup in a frozen dict of five
ordinary Python callables; an unknown key is a miss, not a fallback. The
condition definition (``expression``) is always treated as untrusted
structured data to be parsed and matched, never as logic to execute. Every
public function in this module is designed to never raise on malformed or
adversarial input — malformed input yields a "not satisfied, malformed"
result (or, for ``validate_expression``, a well-formed ``HTTPException``),
never an unhandled exception.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Number
from typing import Any

from fastapi import HTTPException, status

OPERATORS: frozenset[str] = frozenset({"gt", "lt", "eq", "in", "contains"})
OPERATOR_SYMBOLS: dict[str, str] = {
    "gt": ">",
    "lt": "<",
    "eq": "==",
    "in": "is one of",
    "contains": "contains",
}

# Hard caps — an "extremely large value" payload (AC-4) is malformed, never a
# DoS or a memory event. Conservative, documented choices (plan.md does not
# specify tighter caps for the condition evaluator itself).
MAX_FIELD_LEN = 120
MAX_STRING_LEN = 500
MAX_LIST_ITEMS = 100
MAX_ABS_NUMBER = 1e15

REASONS = (
    "satisfied",
    "not_satisfied",
    "malformed_expression",
    "unknown_operator",
    "unknown_field",
    "field_missing",
    "type_mismatch",
    "value_out_of_bounds",
)

_ALLOWED_KEYS = frozenset({"field", "operator", "value"})


@dataclass(frozen=True)
class ConditionResult:
    """The outcome of evaluating one condition against one facts dict."""

    satisfied: bool  # ALWAYS False unless the comparison is well-formed AND true
    malformed: bool  # True => the rule definition itself is invalid (FR-4 fail-closed)
    reason: str  # one of REASONS
    field: str | None
    operator: str | None
    value: Any  # the threshold/comparison value from the definition
    actual: Any  # the item's value at evaluation time (FR-7)
    text: str  # human-readable, "" when malformed


def _miss(reason: str, *, field: Any = None, operator: Any = None, value: Any = None, actual: Any = None) -> ConditionResult:
    return ConditionResult(
        satisfied=False,
        malformed=True,
        reason=reason,
        field=field if isinstance(field, str) else None,
        operator=operator if isinstance(operator, str) else None,
        value=value,
        actual=actual,
        text="",
    )


def _not_satisfied(
    *, field: str, operator: str, value: Any, actual: Any, reason: str = "not_satisfied"
) -> ConditionResult:
    return ConditionResult(
        satisfied=False,
        malformed=False,
        reason=reason,
        field=field,
        operator=operator,
        value=value,
        actual=actual,
        text="",
    )


def _satisfied(*, field: str, operator: str, value: Any, actual: Any) -> ConditionResult:
    return ConditionResult(
        satisfied=True,
        malformed=False,
        reason="satisfied",
        field=field,
        operator=operator,
        value=value,
        actual=actual,
        text=_format_explanation(field=field, operator=operator, value=value, actual=actual),
    )


def _is_finite_number(x: Any) -> bool:
    # bool is a Number subclass in Python but must never be treated as a
    # numeric comparison value for gt/lt (FR-2 semantics table).
    if isinstance(x, bool):
        return False
    if not isinstance(x, (int, float)):
        return False
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return False
    return -MAX_ABS_NUMBER <= x <= MAX_ABS_NUMBER


def _values_equal(actual: Any, value: Any) -> bool:
    """The ``eq`` rule: numbers by numeric equality, strings by
    strip().lower() equality (matching this codebase's existing
    ``_matches``/``_grant_covers`` convention), booleans by identity, and
    ``None`` actual only equal to ``None`` value.
    """
    if actual is None or value is None:
        return actual is None and value is None
    if isinstance(actual, bool) or isinstance(value, bool):
        return actual is value
    if isinstance(actual, Number) and isinstance(value, Number):
        return float(actual) == float(value)
    if isinstance(actual, str) and isinstance(value, str):
        return actual.strip().lower() == value.strip().lower()
    return False


def _leaf_bounds_ok(x: Any) -> bool:
    """A scalar leaf (no nested list/dict) within the caps — used both for a
    bare ``value`` and for each item of a list ``value``."""
    if x is None or isinstance(x, bool):
        return True
    if isinstance(x, Number):
        return _is_finite_number(x)
    if isinstance(x, str):
        return len(x) <= MAX_STRING_LEN
    return False


def _value_bounds_ok(value: Any) -> bool:
    """Structural/size gate on the condition definition's ``value`` — applies
    to EVERY operator equally, independent of operator-specific type
    semantics. A dict is never a valid shape; a list must be a flat list (no
    nested list/dict items) of ≤ MAX_LIST_ITEMS bounded scalars (this also
    rejects a self-referential/recursive list, since the recursive element
    itself is a list, not a scalar leaf); a string/number must be within the
    documented caps. Failing this gate means the expression itself is
    malformed (FR-4), never a mere fact-vs-value type mismatch.
    """
    if isinstance(value, list):
        return len(value) <= MAX_LIST_ITEMS and all(_leaf_bounds_ok(v) for v in value)
    return _leaf_bounds_ok(value)


def _scalar_ok(x: Any) -> bool:
    """Type gate for a single scalar used inside a comparison against a fact
    (``actual``) — distinct from ``_leaf_bounds_ok`` in that this is checked
    at comparison time and drives ``type_mismatch``, not ``malformed``."""
    if x is None or isinstance(x, bool):
        return True
    if isinstance(x, Number):
        return _is_finite_number(x)
    if isinstance(x, str):
        return len(x) <= MAX_STRING_LEN
    return False


def _op_gt(actual: Any, value: Any) -> bool | None:
    if not _is_finite_number(value) or not _is_finite_number(actual):
        return None
    return actual > value


def _op_lt(actual: Any, value: Any) -> bool | None:
    if not _is_finite_number(value) or not _is_finite_number(actual):
        return None
    return actual < value


def _op_eq(actual: Any, value: Any) -> bool | None:
    if not _scalar_ok(value) or not _scalar_ok(actual):
        return None
    return _values_equal(actual, value)


def _op_in(actual: Any, value: Any) -> bool | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(_scalar_ok(v) for v in value):
        return None
    if not _scalar_ok(actual):
        return None
    return any(_values_equal(actual, v) for v in value)


def _op_contains(actual: Any, value: Any) -> bool | None:
    if not _scalar_ok(value):
        return None
    if isinstance(actual, str) and isinstance(value, str):
        return value.strip().lower() in actual.lower()
    if isinstance(actual, list):
        if len(actual) > MAX_LIST_ITEMS or not all(_scalar_ok(v) for v in actual):
            return None
        return any(_values_equal(v, value) for v in actual)
    return None


# Frozen dispatch table — five tokens, five ordinary callables. No dynamic
# lookup by attacker-controlled name is ever performed against this or
# anything else (no getattr/eval/exec/compile/__import__ anywhere below).
_OPERATOR_FUNCS: dict[str, Any] = {
    "gt": _op_gt,
    "lt": _op_lt,
    "eq": _op_eq,
    "in": _op_in,
    "contains": _op_contains,
}


def _shape_check(expression: Any) -> tuple[str, str, Any] | None:
    """Validate the exact `{field, operator, value}` shape. Returns
    (field, operator, value) on success, or None if malformed."""
    if not isinstance(expression, dict):
        return None
    if set(expression.keys()) != _ALLOWED_KEYS:
        return None
    field = expression.get("field")
    operator = expression.get("operator")
    value = expression.get("value")
    if not isinstance(field, str) or not field or len(field) > MAX_FIELD_LEN:
        return None
    if not isinstance(operator, str) or operator not in OPERATORS:
        return None
    return field, operator, value


def evaluate_condition(
    expression: Any,
    facts: Mapping[str, Any],
    *,
    allowed_fields: frozenset[str] | None = None,
) -> ConditionResult:
    """Evaluate ONE comparison against ONE named fact. Never raises.

    ``expression`` is UNTRUSTED structured data read straight out of a JSONB
    column. Accepted shape, exactly: a dict with EXACTLY the keys
    {"field", "operator", "value"}. Anything else — a non-dict, a missing
    key, an extra key (including "and"/"or"/"not"/"expr"/"code"), a
    non-string field or operator, an operator outside OPERATORS, a nested
    dict/list where a scalar is required, a string longer than
    MAX_STRING_LEN, a list longer than MAX_LIST_ITEMS or containing a
    non-scalar, a non-finite or out-of-bounds number — returns
    ``satisfied=False, malformed=True``. A field absent from ``facts`` (or
    from ``allowed_fields`` when given) returns ``satisfied=False`` with
    reason "field_missing"/"unknown_field" — NOT an exception, so the step's
    other rules still evaluate (FR-4, AC-3, AC-4). The whole body is
    additionally wrapped in ``try/except Exception`` whose handler returns a
    malformed result — belt and braces, so no unforeseen input can raise out
    of this function.
    """
    try:
        shape = _shape_check(expression)
        if shape is None:
            return _miss("malformed_expression")
        field, operator, value = shape

        if allowed_fields is not None and field not in allowed_fields:
            return _miss("unknown_field", field=field, operator=operator, value=value)

        if not _value_bounds_ok(value):
            return _miss("malformed_expression", field=field, operator=operator, value=value)
        if operator == "in" and not isinstance(value, list):
            return _miss("malformed_expression", field=field, operator=operator, value=value)

        if not isinstance(facts, Mapping) or field not in facts:
            return ConditionResult(
                satisfied=False,
                malformed=False,
                reason="field_missing",
                field=field,
                operator=operator,
                value=value,
                actual=None,
                text="",
            )
        actual = facts[field]

        func = _OPERATOR_FUNCS.get(operator)
        if func is None:
            return _miss("unknown_operator", field=field, operator=operator, value=value)

        outcome = func(actual, value)
        if outcome is None:
            return _not_satisfied(
                field=field, operator=operator, value=value, actual=actual, reason="type_mismatch"
            )
        if outcome is True:
            return _satisfied(field=field, operator=operator, value=value, actual=actual)
        return _not_satisfied(field=field, operator=operator, value=value, actual=actual)
    except Exception:  # fail-closed by design (FR-4) - BLE001 is intentionally allowed here
        return _miss("malformed_expression")


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Number):
        try:
            if float(value) == int(value):
                return f"{int(value):,}"
        except (OverflowError, ValueError):
            pass
        return f"{value:,}"
    if isinstance(value, list):
        return ", ".join(_format_value(v) for v in value)
    if value is None:
        return "null"
    return str(value)


def _format_explanation(*, field: str, operator: str, value: Any, actual: Any) -> str:
    symbol = OPERATOR_SYMBOLS.get(operator, operator)
    return f"{field} ({_format_value(actual)}) {symbol} {_format_value(value)}"


def render_condition_text(expression: Any) -> str:
    """'contract_value > 1,000,000' — the config-time label (no actual
    value). Returns '(invalid condition)' for a malformed expression."""
    try:
        shape = _shape_check(expression)
        if shape is None:
            return "(invalid condition)"
        field, operator, value = shape
        symbol = OPERATOR_SYMBOLS.get(operator, operator)
        return f"{field} {symbol} {_format_value(value)}"
    except Exception:  # fail-closed - BLE001 is intentionally allowed here
        return "(invalid condition)"


def render_explanation(result: ConditionResult) -> str:
    """'contract_value (1,200,000) > 1,000,000' — the FR-7/AC-6 string, using
    the item's ACTUAL value at evaluation time. Numbers are rendered with
    thousands separators; strings verbatim; lists as 'a, b, c'."""
    try:
        if result.malformed or result.field is None or result.operator is None:
            return ""
        return _format_explanation(
            field=result.field, operator=result.operator, value=result.value, actual=result.actual
        )
    except Exception:  # fail-closed - BLE001 is intentionally allowed here
        return ""


def validate_expression(expression: Any, *, allowed_fields: frozenset[str]) -> None:
    """Config-time gate used by the rule create/update endpoints: raises
    HTTPException(422, "<specific reason>") for anything evaluate_condition
    would call malformed. A malformed rule therefore normally cannot be
    stored at all; evaluate_condition's fail-closed path is the defence for
    rows written before this feature, by a future migration, or directly in
    SQL.
    """
    if not isinstance(expression, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Condition must be an object")
    if set(expression.keys()) != _ALLOWED_KEYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Condition must have exactly the keys 'field', 'operator', 'value' "
            "(no combinators such as 'and'/'or' are supported)",
        )
    field = expression.get("field")
    operator = expression.get("operator")
    value = expression.get("value")

    if not isinstance(field, str) or not field or len(field) > MAX_FIELD_LEN:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Condition 'field' is invalid")
    if field not in allowed_fields:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown condition field '{field}'"
        )
    if not isinstance(operator, str) or operator not in OPERATORS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Condition 'operator' must be one of: {', '.join(sorted(OPERATORS))}",
        )

    if operator in ("gt", "lt"):
        if not _is_finite_number(value):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Condition 'value' must be a finite number for operator '{operator}'",
            )
    elif operator == "in":
        if not isinstance(value, list) or not value:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Condition 'value' must be a non-empty list for 'in'"
            )
        if len(value) > MAX_LIST_ITEMS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Condition 'value' list exceeds {MAX_LIST_ITEMS} items"
            )
        if not all(_scalar_ok(v) for v in value):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Condition 'value' list items must be simple scalars"
            )
    elif operator in ("eq", "contains") and not _scalar_ok(value):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Condition 'value' must be a simple scalar within size limits for '{operator}'",
        )
