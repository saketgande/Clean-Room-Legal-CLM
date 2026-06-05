# F-01 — SSE session lifecycle (Critical)

**Agent 2 finding:** `event_stream` uses the request-scoped `db` Session after the FastAPI `get_db` dependency has closed it. Tool calls and final-state writes go through a session whose lifetime is implicit; client disconnect mid-stream races dependency teardown against the generator's writes.

## Headline change
- Generator opens its own DB session through CC-4 `streaming_session(...)`. The request-scoped `db` is now used only for synchronous pre-stream ownership/access checks.
- `ai_controller.stream_assistant_run` signature takes `request: Request` + `budget: StreamingBudget`. Between iterations, the controller checks `request.is_disconnected()` and the token budget. Disconnect → `ClientDisconnected` → run flagged `CANCELLED` (`error_message="client_disconnected"`).
- All SSE writes now go through the strict `sse_serialize(...)` helper (incidentally cleans up F-22's `default=str` leakage).
- Tool exceptions are logged + timelined + classified into a structured error code before being re-fed to Claude (folds in F-08).

## Cross-cutting dependencies
- **CC-4** `app/core/streaming.py` — `streaming_session`, `StreamingBudget`, `ClientDisconnected`, `CostBudgetExceeded`, `ToolLoopDetected`, `sse_serialize`, `sse_error_event`.

## Agent 3 done-conditions met
- The session passed to `tool_runtime.execute` is the generator-scoped session, not the request-scoped one — verified by `tests/test_F01_sse_session.py::test_session_is_generator_scoped`.
- Simulated disconnect after iteration 2 stops the loop and the run is marked `CANCELLED` with `error_message="client_disconnected"` — `tests/test_F01_sse_session.py::test_client_disconnect_stops_loop`.
- No call site outside the streaming generator imports `streaming_session`.

## Files
- `assistant_routes_event_stream.py` — rewritten `stream_session` route + its `event_stream` generator + helpers (`_finalize_assistant_message`, `_mark_run_terminal`).
- `ai_controller_stream_assistant_run.py` — rewritten `stream_assistant_run` (and the same shape applies to `resume_assistant_run`; the mixin is intended to be folded back into `AIController`).

## Tests
- `proposals/audit_2026-06-02/tests/test_F01_sse_session.py`
