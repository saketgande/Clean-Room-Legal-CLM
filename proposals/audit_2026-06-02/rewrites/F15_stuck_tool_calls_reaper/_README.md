# F-15 — `AssistantToolCall` rows left RUNNING on flush/confirmation error (High)

**Agent 2 finding:** `tool_runtime.execute` inserts an `AssistantToolCall(status=RUNNING)`, flushes, and only flips to FAILED if `_execute_validated` raises. A `db.flush()` failure mid-tool-call OR a `create_confirmation` failure leaves the row stuck in RUNNING with no `error_message`. The 20-minute reaper at `jobs/routes.py` only handles `JobRun`. Tool calls accumulate.

## Headline change
- The body of `ToolRuntime.execute` (rewritten in F-04's `ai_tool_runtime_execute.py`) now wraps EVERYTHING post-insert in a `try/except` that updates `call.status = FAILED` and `call.error_message = str(exc)[:2000]` before re-raising. Even a flush failure now flips the row.
- New generic `reap_stuck_rows(...)` helper plus two concrete callers:
  - `reap_stuck_assistant_tool_calls(db, ttl_minutes=20)` — new (this finding).
  - `reap_stuck_jobs(db, ttl_minutes=20)` — refactored from `_reap_stuck_jobs` in `jobs/routes.py` so the two implementations share code.
- The tabular-review reconciler can use the same `reap_stuck_rows` shape — left as a follow-up since it currently does more than flip a status (it also computes `error_message` from the cell's last AI run).

## Cross-cutting dependencies
- None.

## Agent 3 done-conditions met
- `tests/test_F15_stuck_tool_calls.py::test_tool_call_failed_on_flush_error` — when the post-insert flow raises, the `AssistantToolCall` row has `status=FAILED` and `error_message` populated.
- `tests/test_F15_stuck_tool_calls.py::test_stuck_tool_call_reaped_after_ttl` — a row with `started_at` more than 20 minutes ago and status RUNNING gets flipped to FAILED.

## Files
- `ai_tool_runtime_stuck_reaper.py` — generic reaper + concrete callers.
- The runtime-side "always flip to FAILED" change is in F-04's `ai_tool_runtime_execute.py`.

## Tests
- `proposals/audit_2026-06-02/tests/test_F15_stuck_tool_calls.py`
