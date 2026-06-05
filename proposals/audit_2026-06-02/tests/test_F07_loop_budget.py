"""F-07 cost-budget / cancellation tests (folded into F-01 streaming.py)."""

from __future__ import annotations


def test_loop_exits_on_token_budget():
    """Token-budget exceeded raises ``CostBudgetExceeded`` from ``check_token_budget``."""
    from app.core.streaming import CostBudgetExceeded, StreamingBudget

    budget = StreamingBudget(max_tokens=1_000)
    budget.record_tokens(800)
    budget.check_token_budget()
    budget.record_tokens(300)
    raised = False
    try:
        budget.check_token_budget()
    except CostBudgetExceeded as exc:
        raised = "token_budget_exceeded" in str(exc)
    assert raised


def test_loop_exits_on_repeat_tool():
    """Same tool + idempotency key, three calls, raises ``ToolLoopDetected``."""
    from app.core.streaming import StreamingBudget, ToolLoopDetected

    budget = StreamingBudget(max_repeat_per_tool=3)
    raised = False
    try:
        for _ in range(4):
            budget.record_tool_call(tool_name="edit_contract", idempotency_key="k-1")
    except ToolLoopDetected:
        raised = True
    assert raised


def test_tool_loop_counts_per_idempotency_key():
    """Different idempotency keys for the same tool don't combine in the counter."""
    from app.core.streaming import StreamingBudget, ToolLoopDetected

    budget = StreamingBudget(max_repeat_per_tool=3)
    # Two different idempotency keys, two calls each — under threshold.
    budget.record_tool_call(tool_name="edit_contract", idempotency_key="k-1")
    budget.record_tool_call(tool_name="edit_contract", idempotency_key="k-1")
    budget.record_tool_call(tool_name="edit_contract", idempotency_key="k-2")
    budget.record_tool_call(tool_name="edit_contract", idempotency_key="k-2")
    # Should NOT have raised; threshold counts the (tool, key) pair.
    assert True
