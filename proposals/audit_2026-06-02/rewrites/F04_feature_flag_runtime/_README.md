# F-04 — Tool feature_flag enforcement (Critical)

**Agent 2 finding:** `ToolSpec.feature_flag` is declared (e.g. `feature.ai.edit_suggestions`, `feature.ai.docx_generation`) at `backend/app/ai/tool_registry.py:127,211,229` but never enforced. Schema-build (`controller._assistant_tool_schemas`) and execute (`tool_runtime.execute`) both ignore it. Customer toggles silently do nothing.

## Headline change
- New `app/ai/tool_policy.py` (CC-3) — `AssistantToolPolicy.is_enabled(tool_name, db, org_id)` with a 10s in-process cache.
- `_assistant_tool_schemas` now consults the policy at schema-build time. Disabled tools are not offered to Claude.
- `ToolRuntime.execute` now consults the policy at execute time too, BEFORE inserting the `AssistantToolCall` row. Defense-in-depth for the case where a tool was disabled while a stream was already in flight.
- A feature-disabled call returns `{"status": "feature_disabled", "tool_name": ..., "feature_flag": ..., "message": "..."}` (no HTTPException), so the streaming generator surfaces a typed event to the user instead of throwing into Claude.
- The previously-dead `else: {"status": "feature_not_enabled", ...}` branch at `tool_runtime.py:266` becomes reachable as a legitimate exit (folds in F-34).

## Cross-cutting dependencies
- **CC-3** `app/ai/tool_policy.py` — `AssistantToolPolicy`.

## Agent 3 done-conditions met
- `tests/test_F04_feature_flag.py::test_disabled_feature_flag_omits_tool_from_schema` — flipping `feature.ai.edit_suggestions` to `false` removes `edit_contract` from the next Claude request payload.
- `tests/test_F04_feature_flag.py::test_disabled_feature_flag_blocks_inflight_call` — direct call to `ToolRuntime.execute` for a disabled tool returns `feature_disabled` and does NOT insert an `AssistantToolCall` row.
- Default behavior preserved when no `AdminSetting` row exists — `_coerce_bool(..., default=True)`.

## Files
- `ai_controller_assistant_tool_schemas.py` — rewritten schema builder.
- `ai_tool_runtime_execute.py` — rewritten `execute`, also picks up F-15 (always-FAILED on exception).

## Tests
- `proposals/audit_2026-06-02/tests/test_F04_feature_flag.py`
- `proposals/audit_2026-06-02/tests/test_cross_cutting.py` — `AssistantToolPolicy.is_enabled` unit tests.
