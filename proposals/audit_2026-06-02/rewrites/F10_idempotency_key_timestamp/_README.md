# F-10 — Idempotency key uses `utcnow().timestamp()` (High)

**Agent 2 finding:** Manual obligation-extraction keys at `obligations/routes.py:161` and `tool_runtime.py:1242` end in `:{utcnow().timestamp()}`. Every click — including double-clicks — mints a unique key, defeating `JobRun.idempotency_key UNIQUE`. Real Claude $ leak per double-click.

## Headline change
- New `app/jobs/idempotency.py` (CC-5) — `build_idempotency_key(job_type, *, version_id, snapshot_id, trigger, debounce_token=None)` and `build_debounce_token(user_id, now, window=5)`. Default debounce window is 5 minutes.
- `obligations.routes.trigger_obligation_extraction` and `tool_runtime._extract_obligations` both build their keys through CC-5 with `trigger="manual"` / `trigger="assistant"` and a debounce token of `f"{user_id}:{minute_bucket(now, 5)}"`. Two clicks in the same 5-minute bucket dedupe to one JobRun row.
- The tool result now includes the `idempotency_key` so the model can see the dedupe state if it asks again.

## Cross-cutting dependencies
- **CC-5** `app/jobs/idempotency.py` — `build_idempotency_key`, `build_debounce_token`, `minute_bucket`.

## Agent 3 done-conditions met
- `tests/test_F10_idempotency.py::test_manual_extract_within_5min_deduped` — POST twice inside the same 5-minute bucket returns the same `job_id`.
- `tests/test_F10_idempotency.py::test_manual_extract_after_5min_allowed` — POST again with a clock advance past the bucket boundary yields a new `job_id`.
- `tests/test_cross_cutting.py::test_minute_bucket_truncates` — pure unit test for the bucket helper.

## Files
- `obligations_routes_extract.py` — rewritten obligations route.
- `ai_tool_runtime_idempotency.py` — rewritten tool method.

## Tests
- `proposals/audit_2026-06-02/tests/test_F10_idempotency.py`
- `proposals/audit_2026-06-02/tests/test_cross_cutting.py`
