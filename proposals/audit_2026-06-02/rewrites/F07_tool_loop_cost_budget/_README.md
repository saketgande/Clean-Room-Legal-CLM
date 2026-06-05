# F-07 — Tool-use loop has no cost budget / cancellation (High) — folded into F-01

**Agent 2 finding:** The tool loop in `stream_assistant_run` and `resume_assistant_run` runs up to `ai_max_tool_iterations = 8` Claude calls regardless of upstream client state. No `request.is_disconnected()` check, no token-budget check, no max-tokens-per-run accumulator, no early-abort on a repeated tool. A page refresh during streaming continues to pay 8 Claude calls.

## Disposition
Per Agent 3 roadmap, F-07 is **folded into F-01** because both depend on the same CC-4 `streaming_session(...)` plumbing — the request/budget plumbing is the same change, the only difference is what each pass checks.

## Where the changes live
- `proposals/audit_2026-06-02/rewrites/cross_cutting/streaming.py` — `StreamingBudget` with `max_tokens` / `max_repeat_per_tool`, the `CostBudgetExceeded` and `ToolLoopDetected` exceptions, and `record_tokens(...)`, `check_token_budget(...)`, `record_tool_call(...)`.
- `proposals/audit_2026-06-02/rewrites/F01_sse_session_lifecycle/ai_controller_stream_assistant_run.py`:
  - `_check_streaming_budget(request, budget)` is called BEFORE each Claude call.
  - `budget.record_tokens(call_log.total_tokens)` after each `_log_assistant_ai_call`.
  - `budget.record_tool_call(tool_name, idempotency_key)` after each tool run.
- `proposals/audit_2026-06-02/rewrites/F01_sse_session_lifecycle/assistant_routes_event_stream.py`:
  - The outer `try/except` distinguishes `ClientDisconnected` (mark `CANCELLED`, no further yields), `CostBudgetExceeded` (yield `token_budget_exceeded` error then `done`), `ToolLoopDetected` (yield `tool_loop_detected` error then `done`).

## Agent 3 done-conditions met
- `tests/test_F01_sse_session.py::test_client_disconnect_stops_loop` — disconnect after iteration 2 stops the loop.
- `tests/test_F07_loop_budget.py::test_loop_exits_on_token_budget` — set a low budget, verify exit at ≤2 iterations.
- `tests/test_F07_loop_budget.py::test_loop_exits_on_repeat_tool` — same tool/idempotency-key called 3× breaks the loop.

## Cross-cutting dependencies
- **CC-4** `streaming.py` — `StreamingBudget`, `ClientDisconnected`, `CostBudgetExceeded`, `ToolLoopDetected`.

## Files
This finding has no file unique to itself. See the F-01 PR for the implementation; see `tests/test_F07_loop_budget.py` for the F-07-specific tests.
