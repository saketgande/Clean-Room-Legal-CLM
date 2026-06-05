# AEGIS Legal CLM — Refactoring Proposal (Agent 4: The Builder)

**Date:** 2026-06-02
**Scope:** Production-grade rewrites for every Critical + High finding (F-01 .. F-16) from Agent 2 and the five cross-cutting decisions (CC-1 .. CC-5) from Agent 3.

These files are **proposed rewrites**. None of the live source under `backend/app/` was edited. The team merges this into the real source.

## Cross-cutting modules (build first)

| ID | File | What it provides |
|----|------|------------------|
| CC-1 | `cross_cutting/access_policy.py` | `ContractAccessPolicy.scope_query / access_predicate / can_read_session / can_admin_read_session / can_write_session` |
| CC-2 | `cross_cutting/session_state.py` | `allocate_contract_handle`, `allocate_handles_for_search`, `list_session_handles`, per-session advisory lock |
| CC-3 | `cross_cutting/tool_policy.py`   | `AssistantToolPolicy.is_enabled(tool_name, db, org_id)` with 10s cache |
| CC-4 | `cross_cutting/streaming.py`     | `streaming_session`, `StreamingBudget`, `ClientDisconnected`, `CostBudgetExceeded`, `ToolLoopDetected`, `sse_serialize`, `sse_error_event` |
| CC-5 | `cross_cutting/idempotency.py`   | `build_idempotency_key`, `build_debounce_token`, `build_auto_brain_ingestion_key`, `minute_bucket` |
| —    | `cross_cutting/migration_session_handle_unique.py` | Alembic `0009_assistant_contract_handle_unique` — UNIQUE(session_id, handle) + duplicate prune |

## F-01 .. F-16 — per-finding index

| Finding | Severity | One-line change | Files |
|---------|----------|------------------|-------|
| **F-01** SSE lifecycle | Critical | Generator opens its own DB session via CC-4; loop checks `is_disconnected()` and budget between iterations | `F01_sse_session_lifecycle/assistant_routes_event_stream.py`, `F01_sse_session_lifecycle/ai_controller_stream_assistant_run.py`, `F01_sse_session_lifecycle/_README.md` |
| **F-02** Token decision | Critical | Rate-limited route, uniform 401 (no oracle), per-attempt audit, cap `comment` at 4_000 chars, cap `token` length | `F02_token_decision_security/approvals_routes_token_decision.py`, `F02_token_decision_security/approvals_service_token_decision.py`, `F02_token_decision_security/_README.md` |
| **F-03** Cross-tenant leak | Critical | `accessible_contract_filter` no longer returns bare `true()` for admins; CC-1 policy folds org-id into every predicate | `F03_access_filter_tenant_leak/contracts_access.py`, `F03_access_filter_tenant_leak/obligations_routes_list.py`, `F03_access_filter_tenant_leak/renewals_routes_list.py`, `F03_access_filter_tenant_leak/_README.md` |
| **F-04** Feature flag | Critical | `AssistantToolPolicy.is_enabled` gates BOTH schema-build (controller) and execute (runtime); returns `feature_disabled` shape | `F04_feature_flag_runtime/ai_controller_assistant_tool_schemas.py`, `F04_feature_flag_runtime/ai_tool_runtime_execute.py`, `F04_feature_flag_runtime/_README.md` |
| **F-05** Empty original | High | Sort-stable `(start, end, sort_index)`; empty-original inserts retain order; `dropped_suggestions` surfaced | `F05_anchor_suggestions_empty_original/ai_tool_runtime_anchor.py`, `F05_anchor_suggestions_empty_original/_README.md` |
| **F-06** Admin session | High | Read/write split; admins can read same-org sessions, with audit log; writes still creator-only | `F06_assistant_session_admin_override/assistant_routes_session_access.py`, `F06_assistant_session_admin_override/_README.md` |
| **F-07** Loop budget | High | Folded into F-01 via CC-4 `StreamingBudget` — disconnect / token budget / repeat-tool guards | `F07_tool_loop_cost_budget/_README.md` (pointer) |
| **F-08** Tool exceptions | High | `logger.exception` + `resource_timeline_event` + structured `error_code`; `str(exc)` sanitized (paths stripped) | `F08_tool_exception_logging/ai_controller_tool_exception.py`, `F08_tool_exception_logging/_README.md` |
| **F-09** Handle TOCTOU | High | All three call sites delegate to CC-2 `allocate_contract_handle` (advisory lock + UNIQUE backstop) | `F09_contract_handle_toctou/ai_controller_handles.py`, `F09_contract_handle_toctou/ai_tool_runtime_handles.py`, `F09_contract_handle_toctou/assistant_routes_handles.py`, `F09_contract_handle_toctou/_README.md` |
| **F-10** Idempotency timestamp | High | Manual + assistant triggers use 5-minute debounce token via CC-5 instead of `utcnow().timestamp()` | `F10_idempotency_key_timestamp/obligations_routes_extract.py`, `F10_idempotency_key_timestamp/ai_tool_runtime_idempotency.py`, `F10_idempotency_key_timestamp/_README.md` |
| **F-11** Obligations diff | High | Content-hash-aware `_persist_obligations`; identical extraction = no-op, reminders preserved | `F11_persist_obligations_diff/ai_controller_persist_obligations.py`, `F11_persist_obligations_diff/_README.md` |
| **F-12** Audit Postgres error | High | Backend-aware lock; `AuditChainCorrupted` exception raised on any unexpected failure (no more silent skip) | `F12_audit_log_postgres_error/core_audit_record.py`, `F12_audit_log_postgres_error/_README.md` |
| **F-13** DocuSign HMAC | High | Timestamp window (5 min) folded into HMAC; `WebhookEventSeen` table for (envelope_id, status) replay protection | `F13_docusign_hmac_replay/integrations_docusign_verify.py`, `F13_docusign_hmac_replay/_README.md` |
| **F-14** Brain ingest race | High | Single canonical idempotency key via CC-5; per-contract `pg_advisory_xact_lock` around graph mutation | `F14_brain_ingestion_lock/jobs_tasks_brain_ingestion.py`, `F14_brain_ingestion_lock/_README.md` |
| **F-15** Stuck tool calls | High | Generic `reap_stuck_rows` + concrete reaper for `AssistantToolCall`; runtime now ALWAYS flips to FAILED on flush error | `F15_stuck_tool_calls_reaper/ai_tool_runtime_stuck_reaper.py`, `F15_stuck_tool_calls_reaper/_README.md` (runtime change lives in F-04's file) |
| **F-16** Boot integration keys | High | `validate_runtime_settings` requires real key/PEM when `MOCK_X=false`; warns on mock-with-key set | `F16_boot_integration_key_check/core_config_validate.py`, `F16_boot_integration_key_check/_README.md` |

## Test layout

Each finding has a dedicated test file under `proposals/audit_2026-06-02/tests/`:
- `test_cross_cutting.py` — covers CC-1 .. CC-5.
- `test_F01_sse_session.py` through `test_F16_boot_validate.py` — one per finding.

Tests use pytest, follow the existing `backend/tests/` style (`SimpleNamespace` for lightweight fixtures, `MagicMock` where DB session interactions are mocked, no monkey-patching where dependency-injection would do).

## Folded medium / low findings

Per Agent 3's roadmap, several Medium / Low items are picked up by the Critical / High rewrites — recorded here so reviewers can check them off:

- **F-18** (3 copies of handle logic) — folded into F-09 via CC-2.
- **F-21** (free-form `status_filter`) — folded into F-03 via `ObligationStatus` enum in `obligations_routes_list.py`.
- **F-22** (`json.dumps(..., default=str)`) — folded into F-01 via CC-4 `sse_serialize`.
- **F-23** (sort-tie collapse) — folded into F-05 via deterministic `(start, end, sort_index)` key.
- **F-25** (unbounded `comment`) — folded into F-02 via `max_length=4_000`.
- **F-30** (divergent handle validation) — folded into F-09.
- **F-34** (dead `feature_not_enabled` branch) — becomes reachable via F-04.
- **F-36** (different concurrency patterns in handle queries) — folded into F-09.

## Things this proposal intentionally does NOT do

- Does NOT modify any file under `backend/app/`. Every rewrite lives under `proposals/audit_2026-06-02/rewrites/`.
- Does NOT auto-apply the Alembic migration. The `cross_cutting/migration_session_handle_unique.py` file is a proposal; the integration team renames it to `backend/alembic/versions/0009_*.py` on merge.
- Does NOT write the F-13 companion model `WebhookEventSeen` (the schema sketch is in the verify file as a comment block). That model + its migration `0010_webhook_event_seen.py` belong to the merge PR.
- Does NOT touch the Medium / Low items reserved for Sprint 4 (F-17, F-19, F-24, F-26, F-27, F-28, F-29, F-31, F-32, F-33, F-35, F-37).

## Open notes for reviewers

- The `assistant_routes_event_stream.py` file lifts only the lifecycle section. The other endpoints in `backend/app/assistant/routes.py` remain unchanged — F-06's `assistant_routes_session_access.py` shows the new access helpers separately so reviewers don't have to diff the full module.
- `F01_sse_session_lifecycle/ai_controller_stream_assistant_run.py` is structured as a mixin (`AIControllerStreamingMixin`) so reviewers can see exactly which methods change without needing the entire `AIController` source. On merge, the mixin's methods replace the corresponding methods on `AIController`. The same pattern (`resume_assistant_run`) follows the identical structure — the file shows the more involved one.
- The F-04 `ai_tool_runtime_execute.py` file replaces the WHOLE `ToolRuntime.execute` body — picking up F-15's "always flip to FAILED on exception" guarantee at the same time.
