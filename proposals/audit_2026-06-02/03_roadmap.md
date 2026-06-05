# AEGIS Legal CLM — Refactoring Roadmap (Agent 3: The Strategist)

**Date:** 2026-06-02
**Inputs:** `01_architecture.md` (Agent 1), `02_findings.md` (Agent 2).
**Scope:** Per-item plan for every Critical + High (F-01..F-16). Medium/Low go to a single appendix. 4 sprints, each ~2 weeks for 2–3 engineers in parallel.

---

## Corrections to prior agents

- **Agent 2, F-02 numbering:** Agent 2 lists two Critical items both numbered "F-02" (`F-02` and `F-02b`). For sequencing, this roadmap treats them as a single combined item **F-02 (token endpoint hardening)** because every concrete change (rate limit, ambiguous error code, org-scoped lookup, audit attribution) lands in the same two functions (`approvals/routes.py:decide_via_token`, `approvals/service.py:redeem_token_decision`). They are not separable in practice.
- **Agent 2, F-04 line ref `controller.py:594-608`:** The line range is approximately correct, but the actual schema-build site is inside `_assistant_tool_schemas`. Agent 4 should re-grep for the function head before editing.
- **Agent 2, F-15 stuck-row TTL claim:** Agent 2 says "the 20-min reaper exists for `JobRun` only." Confirmed via Agent 1 §10: `tabular_review/routes.py` also has a reconciler for stuck `TabularReviewCell` rows (20-min TTL). The pattern exists in two places and can be generalized when F-15 lands.
- **Otherwise:** Agent 1 and Agent 2 are mutually consistent on all material points. No factual claims rejected.

---

## Cross-cutting decisions

These five architectural choices must be locked in BEFORE Sprint 1 starts because multiple findings depend on them. The recommended answer is given in each case so the work is unblocked.

### CC-1. Introduce a real `AccessPolicy` layer; deprecate the bare `accessible_contract_filter` predicate.
- **Question:** Stay with the bare `or_(...)` SQL expression in `contracts/access.py`, or move to a typed `AccessPolicy` abstraction?
- **Recommendation:** **Introduce `app/core/access_policy.py`** exposing `ContractAccessPolicy.scope_query(query, user, *, require_org_match: bool = True)` that returns a query with BOTH `Contract.org_id == user.org_id` AND the membership predicate appended. The admin "shortcut" stays — but only after the explicit org filter. Mark the old `accessible_contract_filter` as deprecated; keep it as a thin shim that calls the policy.
- **Why:** F-03 (Critical) and F-30, F-17, F-28 (Medium) all touch contract-access predicates. A typed policy lets us encode "every Contract-joining query MUST have an org-scoped predicate before any access predicate." Today this is folklore enforced by code review.
- **Unblocks:** F-03 (the primary fix), F-06 (admin override for AssistantSession can route through the same policy), F-19 (`_my_attention_items` can ask the policy for "what this user can see" instead of loading all org contracts), F-28 (citation source lookup goes through the same policy and the "permission denied" case becomes explicit, not swallowed).

### CC-2. Session-scoped assistant state (contract handles, in-progress confirmations) stays in Postgres, with a `(session_id, handle)` unique constraint and a `SELECT ... FOR UPDATE`-based handle allocator.
- **Question:** Move per-session state to Redis, or keep in Postgres with proper locking?
- **Recommendation:** **Keep in Postgres.** Add a UNIQUE constraint on `assistant_contract_handle.(session_id, handle)` (migration). Centralize handle allocation in a single function in a new file `app/ai/session_state.py` that does: `SELECT MAX(handle_index) FROM assistant_contract_handle WHERE session_id=? FOR UPDATE` → next index → insert. Three current call-sites (`controller._handle_for_contract`, `tool_runtime._find_contracts`, `routes._ensure_contract_handle`) all collapse to imports of this one function.
- **Why:** Confirmations, tool calls, citations all already live in Postgres with FK relationships. Splitting state across two stores doubles the consistency surface and the audit story. Postgres advisory locks at the `(session_id)` grain are cheap (`pg_advisory_xact_lock(hash(session_id))`).
- **Unblocks:** F-09 (TOCTOU race), F-18 (three copies of handle logic), F-30 (divergent validation between copies).

### CC-3. Tool `feature_flag` becomes a runtime gate at BOTH schema-build time and tool-execution time, via a new `AssistantToolPolicy.is_enabled(...)`.
- **Question:** Delete the unenforced `feature_flag` field from `ToolSpec`, or wire it up?
- **Recommendation:** **Wire it up.** Add `app/ai/tool_policy.py` exposing `AssistantToolPolicy.is_enabled(tool_name, db, org_id, user) -> bool` that consults `AdminSetting` (key = the tool's `feature_flag` value, e.g. `feature.ai.edit_suggestions`; truthy boolean expected). Two consumers must call it:
  1. `controller._assistant_tool_schemas` (gates whether the tool is even offered to Claude).
  2. `tool_runtime.execute` (gates whether the tool will run if Claude already chose it from a stale schema — defense-in-depth for in-flight requests at the moment a flag flips).
- **Why:** F-04 (Critical) is a contract violation: admins can disable AI features and the toggle does nothing. Deleting the field would be cleaner code but breaks the customer-facing promise. The dual-gate (schema + execute) handles the toggle-during-streaming case.
- **Unblocks:** F-04 (primary), and implicitly F-15 (the "feature_not_enabled" branch in `_execute_validated` can now reach a real exit path).

### CC-4. SSE generators get a dedicated `StreamingSession` lifecycle with its own DB session, distinct from the request-scoped one.
- **Question:** Reuse the request `Session` inside the SSE generator, or open a generator-scoped session?
- **Recommendation:** **Open a generator-scoped session.** Add `app/core/streaming.py` exposing `@asynccontextmanager streaming_session(*, request, on_disconnect) -> AsyncIterator[Session]` that:
  1. Opens a fresh `SessionLocal()` inside the generator (NOT the one from `Depends(get_db)`).
  2. Owns its own commit/rollback boundaries.
  3. Checks `request.is_disconnected()` between iterations and raises `ClientDisconnected` for the caller to handle.
  4. Closes the session in `finally`, regardless of how the generator exited (early return, exception, disconnect).
- **Why:** F-01 (Critical) is the lifecycle race. F-07 (cost budget) and F-08 (silent tool exceptions) all share the streaming generator. One abstraction fixes the session-leak AND gives the cancellation hook AND gives one bracketed place to wrap exception logging.
- **Unblocks:** F-01 (primary), F-07 (cancellation hook is now real), F-08 (one obvious place to add `logger.exception(...)`), partly F-15 (when the generator exits early, the stuck-row reaper now has clearer ownership).

### CC-5. Introduce `app/jobs/idempotency.py` with structured key builders. Manual extracts get a click-debouncing token, not a wall-clock timestamp.
- **Question:** Keep using `f"...:{utcnow().timestamp()}"` for manual job reruns?
- **Recommendation:** **Replace with a structured library.** New module `app/jobs/idempotency.py` with `build_idempotency_key(job_type, *, version_id, snapshot_id, trigger: Literal["upload","manual","assistant"], debounce_token: str | None = None)`. For manual triggers, the route mints `debounce_token = f"{user.id}:{minute_bucket(utcnow(), window=5)}"` so a user double-clicking inside 5 minutes is deduped, but a deliberate re-extract 10 minutes later is allowed. Same shape for `tool_runtime`.
- **Why:** F-10 (High) is a real money leak. The current `timestamp()` defeats the unique constraint entirely. The 5-minute bucket is a deliberate behavior change with the smallest possible blast radius.
- **Unblocks:** F-10 (primary), F-14 (the `contract_brain_ingestion` enqueue can route through the same builder with `trigger="upload"` and a per-version single key, removing the three-reasons-three-jobs problem).

---

## Critical findings — per-item plan

### F-01 — SSE event_stream uses the request-scoped `db` Session after the FastAPI dependency closed it
- **Severity (from Agent 2):** Critical
- **The exact change**
  - Add new module `backend/app/core/streaming.py` exporting `streaming_session(...)` context manager (see CC-4).
  - Modify `backend/app/assistant/routes.py:250-379` `stream_session`: stop passing `db` into the generator. Inside `event_stream`, open `async with streaming_session(request=request, ...) as gen_db:` and rebind all writes to `gen_db`. The request-scoped `db` is used ONLY for the synchronous pre-stream work (ownership checks, message insertion before `StreamingResponse` returns).
  - Same change for `backend/app/assistant/routes.py:382-458` `resume_run`.
  - Modify `backend/app/ai/controller.py:93-325` `stream_assistant_run` signature: accept a callable `session_factory` (returns a fresh `Session`) instead of a bare `db`. Internally use one short-lived session per "unit of work" (one Claude call + persisted log) rather than holding a single session across the whole loop. OR: accept the gen-scoped `db` from the route and require it be a generator-owned session (documented invariant).
  - Same for `resume_assistant_run` (`:327-592`).
- **Blast radius**
  - Two route handlers: `assistant/routes.py:stream_session`, `assistant/routes.py:resume_run`.
  - Two controller methods: `controller.stream_assistant_run`, `controller.resume_assistant_run`.
  - Every call inside the streaming generator that uses `db`: `db.commit()` (8+ sites), `db.refresh()`, `db.scalar()`, `db.add()`, the per-iteration `_log_assistant_ai_call`, and the exception-path `assistant_run.status = FAILED; db.commit()`.
  - `tool_runtime.execute(db, ...)` is called inside the loop — its `db` argument is the same closing session. Either also rebind to the generator session (preferred) or refactor `tool_runtime` to accept a session factory.
  - Tests: `test_ai_architecture_wiring.py` exercises the controller — must continue to pass. Add a new test that simulates `request.is_disconnected() == True` after iteration 2 and asserts no further Claude calls fire.
- **Dependencies**
  - **Requires CC-4 locked.**
  - Must precede F-07 (cancellation) and F-08 (logging) because they hook into the same generator. F-15 (stuck tool calls) also benefits — easier to add cleanup-on-disconnect once the session lifetime is right.
- **Done condition**
  - New test `tests/test_phase11_sse_lifecycle.py::test_session_is_generator_scoped` asserts the session passed to `tool_runtime.execute` is NOT the request-scoped one (e.g., by stamping the session with a marker attribute).
  - New test `tests/test_phase11_sse_lifecycle.py::test_client_disconnect_stops_loop` asserts the loop exits after disconnect with `AssistantRun.status = FAILED` and `error_message = "client_disconnected"`.
  - No call site outside the streaming generator references `streaming_session` (grep confirms).
- **Risk of doing this fix**
  - Threading a new session through a 1680-LOC controller method touches many call paths. Easy to miss one `db.commit()`. Mitigation: temporarily add an assertion `assert db is gen_db` at controller entry to catch leaks.
  - The behavior change "early abort on disconnect" is user-visible: a refresh-during-stream now stops the AI run rather than silently completing it. Document this in the PR.
  - Performance: opening a fresh session per Claude iteration adds ~1ms; acceptable.

### F-02 — Token-decision endpoint is unauthenticated AND not rate-limited; status-code oracle; cross-tenant hash lookup
- **Severity (from Agent 2):** Critical (combines F-02 + F-02b per the correction above)
- **The exact change**
  - Modify `backend/app/approvals/routes.py:141` `decide_via_token`:
    - Add `@limiter.limit(settings.approval_token_rate_limit)` decorator. Default to `"30/minute;200/hour"` per IP. Add the new setting in `app/core/config.py`.
    - Add a `Field(max_length=4000)` to `TokenDecisionPayload.comment` (also resolves F-25).
  - Modify `backend/app/approvals/service.py:256-292` `redeem_token_decision`:
    - **Collapse 404 / "used" / "expired" into a single uniform 401 `{"detail": "Invalid or expired approval token"}` response.** Keep the distinct internal logging (audit + structured logger) but do not reveal the state in the HTTP response. This eliminates the oracle.
    - Add `org_id_hint: str | None = None` parameter — when present (most call paths know the org from context — sales/legal portals will be org-scoped), constrain the `select(ApprovalToken)` with `ApprovalToken.org_id == org_id_hint` BEFORE the hash compare. Even when caller doesn't know the org (raw URL hit), the audit row records the looked-up `org_id` once found.
    - When `approver` (the User row) is None (e.g., deactivated), use a stable sentinel actor: write `actor_label=f"token:{intended_approver_email} (deactivated_user)"`, `actor_user_id=None`. This is already done but make it explicit so audit reviewers can distinguish a system actor from a deactivated approver.
  - Add a per-token redemption attempt log row (existing `audit_log` is fine — use event_type `approval.token_attempt`) regardless of success/failure. Used by abuse-detection.
- **Blast radius**
  - One route, one service function. No new tables.
  - Tests: `test_phase10_security_hardening.py` — add `test_token_decision_rate_limited`, `test_token_decision_uniform_error`, `test_token_decision_attempt_audited`.
  - Frontend: the approve/reject email URLs (`app_base_url/#approve?token=...`) don't change. The frontend currently maps 404/409 to user-friendly messages — verify it still works when those collapse to 401.
- **Dependencies**
  - None on other F-XX. Can run in parallel with F-01, F-03, F-04 in Sprint 1.
- **Done condition**
  - `tests/test_phase10_security_hardening.py::test_token_decision_rate_limited` exists and passes (sends 31 requests/min, expects 429 on the 31st).
  - `tests/test_phase10_security_hardening.py::test_token_decision_uniform_error` asserts that "no such token", "already used", and "expired" all return status 401 with identical body.
  - `audit_log` carries a row for every token redemption attempt, including failures (verify with new test).
- **Risk of doing this fix**
  - The uniform error message is a UX regression: a real approver clicking an expired link now sees "Invalid or expired" rather than "expired." Mitigation: log a structured event the helpdesk can look up by token-prefix in the audit log.
  - Rate-limit defaults need a sanity check against real approval-email burst patterns. Mitigation: ship at 30/min/IP initially — bumpable per env via setting.

### F-03 — `accessible_contract_filter` returns `true()` for org admins; cross-tenant leak risk on Contract joins that lack explicit `Contract.org_id` filter
- **Severity (from Agent 2):** Critical
- **The exact change**
  - Add `backend/app/core/access_policy.py` (per CC-1) with `class ContractAccessPolicy: scope_query(query, user, *, require_org_match=True)`. This method appends BOTH `Contract.org_id == user.org_id` AND the membership predicate (or `true()` for admins, after the org filter has already run).
  - Modify `backend/app/contracts/access.py:11-13` `accessible_contract_filter` to delegate to the policy. Keep the function signature for back-compat but rewrite the body so that org admins NO LONGER return bare `true()` — they return `Contract.org_id == user.org_id` (the org filter folded in). Other roles return `and_(Contract.org_id == user.org_id, or_(...membership...))`.
  - For every existing caller (see Blast radius), audit that `Contract.org_id == user.org_id` is no longer needed because `accessible_contract_filter` now provides it. Where the caller still writes its own redundant `Contract.org_id == ...`, leave it (defense-in-depth, no harm).
- **Blast radius** (every caller of `accessible_contract_filter`)
  - `backend/app/contract_brain/retrieval.py:44, 230` — `resolve_scope_contract_ids` and a second site.
  - `backend/app/contracts/service.py:45, 118` — `get_contract_for_user`, `list_contracts_for_user`.
  - `backend/app/signatures/routes.py:59` — `list_signatures`.
  - `backend/app/playbooks/routes.py:484` — playbook contracts list.
  - `backend/app/search/routes.py:39, 100, 142, 212` — four search endpoints.
  - `backend/app/renewals/routes.py:45` — `list_renewals`.
  - `backend/app/obligations/routes.py:56` — `list_obligations`.
  - Total: **7 files, 12 call-sites.** Every one must be hand-verified that the new filter still returns the intended rows.
  - Tests: `test_phase10_security_hardening.py` — add `test_org_admin_cannot_see_other_org_obligations` (creates two orgs with a deliberately mis-keyed `Obligation.contract_id`, ensures org A's admin does not see it).
- **Dependencies**
  - **Requires CC-1 locked.**
  - Should land BEFORE F-06 (assistant-session admin override) because the access-policy abstraction is the place where admin powers are codified — F-06's "admin can read any session in their org" hooks into the same policy.
- **Done condition**
  - Grep `Contract.org_id ==` in `backend/app/` shows every join-with-Contract query has either an explicit `Contract.org_id` predicate OR uses `accessible_contract_filter` (now safe).
  - The new cross-tenant test fails before this change and passes after.
  - `accessible_contract_filter` no longer returns bare `true()` in any code path (verified by reading the function).
- **Risk of doing this fix**
  - This is the highest-blast-radius change in Sprint 1: 12 query sites. Easy to break listing in subtle ways (e.g., a test that relied on admin seeing all contracts across some test-fixture mis-org).
  - Mitigation: stage the change behind a feature setting `feature.access_policy.strict_org_admin` defaulting to True in test/staging, toggleable in prod for one deploy cycle so a regression can be rolled back without redeploy.

### F-04 — AI tool `feature_flag` is declared but never enforced at runtime
- **Severity (from Agent 2):** Critical
- **The exact change**
  - Add `backend/app/ai/tool_policy.py` per CC-3 with `class AssistantToolPolicy: is_enabled(tool_name, db, org_id) -> bool`. Caches per-(org_id, tool_name) for 10s within a single request via `request.state` to avoid N+1 lookups when 20 tools are filtered.
  - Modify `backend/app/ai/controller.py:_assistant_tool_schemas` (≈`:594-608`): add `if not AssistantToolPolicy.is_enabled(tool.name, db, user.org_id): continue` to the filter loop, alongside the existing `enabled_by_default` and `has_permission` checks.
  - Modify `backend/app/ai/tool_runtime.py:86-157` `ToolRuntime.execute`: after permission check, add: `if spec.feature_flag and not AssistantToolPolicy.is_enabled(spec.name, db, user.org_id): raise HTTPException(status.HTTP_409_CONFLICT, "Tool disabled by org policy")`.
  - The dead `else: return {"status": "feature_not_enabled"}` at `tool_runtime.py:266` (F-34) becomes reachable as a legitimate exit and can be kept (or surfaced as a structured tool result rather than 409 — agent 4's call). Recommend: 409 from `execute`, caught by the streaming generator and turned into a user-facing "feature disabled" message, NOT re-fed to Claude (so the model doesn't keep retrying).
- **Blast radius**
  - Two call-sites in `controller.py` and `tool_runtime.py`. New file `tool_policy.py`.
  - Admin UI: customers will only be able to flip flags via `AdminSetting` rows. Verify the admin settings UI surface includes `feature.ai.*` keys with a friendly name. If not, add to `app/admin/routes.py` allowlist of writable keys.
  - Tests: `test_ai_architecture_wiring.py` — add `test_disabled_feature_flag_omits_tool_from_schema`, `test_disabled_feature_flag_blocks_inflight_call`.
- **Dependencies**
  - **Requires CC-3 locked.**
  - Independent of F-01/F-02/F-03. Can run in parallel in Sprint 1.
- **Done condition**
  - Both new tests pass.
  - Flipping `feature.ai.edit_suggestions=false` in `admin_setting` causes `edit_contract` to disappear from the next `complete_with_tools` request payload (verified by capturing the request).
  - When a stale Claude response asks for the disabled tool mid-stream, the user sees a "feature disabled" SSE event, NOT a Claude-driven retry loop.
- **Risk of doing this fix**
  - Behavior change visible to users: tools they could use yesterday may vanish today. Mitigation: default all `AdminSetting` rows to `true` (enabled) so the behavior change is opt-in.
  - The 10s cache means a flag-off won't take effect until the cache expires for in-progress sessions. Acceptable.

---

## High findings — per-item plan

### F-05 — `_anchor_suggestions` accepts empty `original_text` and inserts at offset 0 — silent drop on subsequent inserts
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/ai/tool_runtime.py:1589-1622` `_anchor_suggestions`. Behavior change required (this is a bug, per the pipeline rules — "the finding itself is a behavior bug" exception):
    - When `original_text == ""`, instead of forcing `start=end=0`, set `rec["start"]` to the END of the prior insert plus a configurable nudge (or to a model-supplied `insert_after` field if added to the suggestion schema). Failing a supplied position, anchor each "empty original" insert sequentially: insert[i] at offset 0, insert[i+1] at offset (end of insert[i]), etc.
    - Alternatively, keep offset 0 but iterate inserts in order and bump `cursor` to `cursor + len(replacement)` after each apply. Modify the corresponding `_apply_anchored` at `:1635-1649` to advance cursor by `replacement_text length` for empty-original inserts.
  - Add an audit-trail field on the result: `dropped_suggestions: list[{index, reason}]` so the caller can surface "N of M suggestions could not be applied". Currently `applied=False` is computed but never surfaced to the user.
- **Blast radius**
  - One function and its single caller `_apply_anchored`. Both in `tool_runtime.py`.
  - Frontend assistant workspace: must render the new `dropped_suggestions` field (or backend hides it but logs it — minimum bar is the audit row).
  - Tests: new `tests/test_phase11_edit_suggestions.py::test_multiple_empty_original_all_applied`.
- **Dependencies**
  - None. Can run alongside F-23 (Medium, same area).
- **Done condition**
  - Test confirms: given two suggestions with `original_text=""`, both end up in the DOCX with distinct anchor positions.
  - `dropped_suggestions` is populated in result whenever the count of inputs differs from applied.
- **Risk of doing this fix**
  - This is a *behavior* fix: today's silent-drop becomes visible. PRs that depend on the current (broken) behavior break. None are known but verify against integration tests.
  - DOCX output ordering is now stricter — review with a legal SME.

### F-06 — AssistantSession ownership hard-bound to creator; no admin override
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/assistant/routes.py:512-520` `_get_session_for_user`:
    - Replace the inline check with a call to `ContractAccessPolicy.can_admin_read_session(session, user)` from the new policy module.
    - Specifically: allow the read if `session.created_by_user_id == user.id` OR `is_org_admin(user) and session.org_id == user.org_id`. Mutating operations (POST/PATCH/DELETE) still require ownership — only READ paths get the admin bypass.
  - Add separate admin-read functions distinct from the existing `_get_session_for_user`: `_get_session_for_user_read(...)` (allows admin) vs `_get_session_for_user_write(...)` (creator only). Update all callers in `assistant/routes.py` to use the right one.
  - Add audit log entry whenever an admin reads a session not their own: event_type `assistant.session.admin_read`.
- **Blast radius**
  - `assistant/routes.py` — 8 endpoints call `_get_session_for_user` (sessions list, messages, runs, tool-calls, stream, resume, confirm, reject). Split into read/write.
  - Tests: `test_ai_architecture_wiring.py` — add `test_admin_can_read_other_user_session`, `test_admin_cannot_write_other_user_session`.
- **Dependencies**
  - Should follow F-03 (CC-1) so the admin-bypass logic lives in the same `AccessPolicy` module — keeps admin semantics in one file.
- **Done condition**
  - New tests pass.
  - Admin read of another user's session produces an `audit_log` row of type `assistant.session.admin_read`.
- **Risk of doing this fix**
  - Mild compliance question: does an admin reading a user's AI conversation require disclosure? Mitigation: surface the audit-read in the resource timeline, visible to the session owner.

### F-07 — Tool-use loop has no cost budget and no cancellation point
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/ai/controller.py:168-319` `stream_assistant_run` and `:445-585` `resume_assistant_run`:
    - At the top of the iteration loop, check `await request.is_disconnected()` (the route plumbs `request` into the controller). On disconnect, mark `AssistantRun.status = CANCELLED`, mark `AISkillRun.status = CANCELLED`, set `error_message = "client_disconnected"`, and `return` (no exception).
    - Add a per-run token accumulator: `tokens_used += call_log.total_tokens` after each `_log_assistant_ai_call`. If `tokens_used > settings.ai_max_tokens_per_run` (new setting, default 200_000), break with `error_message = "token_budget_exceeded"`.
    - Add a per-tool-name repeat guard: if the same `tool_name` with the same `idempotency_key` has been called `>= 3` times in the same run, break with `error_message = "tool_loop_detected"`.
  - The route's `event_stream` (post-F-01) must wire `request` into the controller — F-01 already changes signatures, fold this in.
- **Blast radius**
  - Two controller methods. One new config setting.
  - SSE consumers (frontend assistant workspace) must handle a `done` event with non-success status. Likely already does (see existing `assistant_run.status = FAILED` path).
  - Tests: `test_ai_architecture_wiring.py::test_loop_exits_on_disconnect`, `::test_loop_exits_on_token_budget`, `::test_loop_exits_on_repeat_tool`.
- **Dependencies**
  - **Requires F-01** to have already plumbed the request and generator-scoped session.
- **Done condition**
  - All three new tests pass.
  - The Claude integration mock layer can simulate "empty tool_use_blocks indefinitely" and the loop exits at ≤2 extra iterations.
- **Risk of doing this fix**
  - Token budget of 200K might be too low for legitimate multi-contract analyses. Mitigation: make it configurable per-org via `AdminSetting`.

### F-08 — SSE generator swallows tool exceptions; only `str(exc)` re-fed to Claude
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/ai/controller.py:289-316` and `:557-583` (both `except Exception` blocks):
    - Add `logger.exception("assistant.tool.failed", extra={...})` with `tool_name`, `assistant_run_id`, `session_id`, `iteration`.
    - Add a `resource_timeline_event` insertion: event_type `assistant.tool.failed`, resource_type/id from the tool input.
    - Add a structured error code derived from the exception class (e.g., `HTTPException -> http_{status}`, `ValueError -> validation`, generic -> `internal`). Re-feed Claude with `{"error_code": "...", "tool_name": ..., "message": <sanitized str(exc)>}` so the model can branch on a stable code rather than a free-form string.
    - Sanitize `str(exc)` before re-feeding: strip file paths and stack traces if present (a safety guard against tool exceptions exposing internal paths to the model context, which then enters the conversation log).
- **Blast radius**
  - Two except blocks. One new logger taxonomy line.
  - Tests: `test_ai_architecture_wiring.py::test_tool_exception_is_logged_and_timelined`.
- **Dependencies**
  - **Requires F-01 (CC-4)** for the generator-scoped session that owns the timeline write.
- **Done condition**
  - When a tool raises, the test asserts: a `resource_timeline_event` row exists, `logger.exception` was called once, and the tool_result fed back to Claude has the structured `error_code` field.
- **Risk of doing this fix**
  - Re-feeding a structured code may change Claude's behavior (model may decide differently than on free-form strings). Mitigation: ship behind a feature flag for one sprint.

### F-09 — Contract-handle generation TOCTOU race
- **Severity (from Agent 2):** High
- **The exact change**
  - Add migration `0009_contract_handle_unique_constraint.py`: `CREATE UNIQUE INDEX ux_assistant_contract_handle_session_handle ON assistant_contract_handle(session_id, handle);`
  - Add `backend/app/ai/session_state.py` with `allocate_contract_handle(db: Session, *, org_id: str, session_id: str, contract_id: str) -> str` per CC-2. Acquires `pg_advisory_xact_lock(hash(session_id))`, queries the next index, inserts, returns the handle.
  - Delete the three duplicated implementations:
    - `controller.py:736-774` `_handle_for_contract` → replace body with `from app.ai.session_state import allocate_contract_handle; return allocate_contract_handle(...)`.
    - `tool_runtime.py:399-431` `_find_contracts` handle path → same.
    - `assistant/routes.py:539-587` `_ensure_contract_handle` → same.
  - The `ensure` semantic for routes (which currently 409s on requested-handle collision per F-30) is preserved via a `requested_handle: str | None = None` arg to `allocate_contract_handle`.
- **Blast radius**
  - One new migration. One new file. Three deletions/replacements.
  - SQLite test posture: advisory locks are a no-op on SQLite (already handled in `audit.py` pattern). Replicate the same `try/except` shim in `session_state.py`. Tests in SQLite remain serial enough that races don't fire.
  - Tests: `test_ai_architecture_wiring.py::test_concurrent_handle_allocation_unique` (use a `ThreadPoolExecutor` to call from two threads).
- **Dependencies**
  - **Requires CC-2 locked.**
  - Migration must deploy before code change (code change relies on the unique constraint catching a duplicate insert as a fallback).
- **Done condition**
  - Concurrent test produces no duplicate handles across 50 parallel calls.
  - Grep `f"contract-{count}"` returns 0 hits in `app/ai/` and `app/assistant/`.
- **Risk of doing this fix**
  - Migration: requires brief table-level lock to create index. Run during low-traffic window. Index creation on `assistant_contract_handle` should be milliseconds for any realistic table size.
  - Cleanup of any duplicates that already exist in production data: write a one-shot data-cleanup script (delete dupes keeping lowest `id`) BEFORE the migration, OR use `CREATE UNIQUE INDEX CONCURRENTLY` with a pre-check.

### F-10 — Manual `obligation_extraction` idempotency key uses `utcnow().timestamp()`
- **Severity (from Agent 2):** High
- **The exact change**
  - Add `backend/app/jobs/idempotency.py` per CC-5 with `build_idempotency_key(job_type, *, version_id, snapshot_id, trigger, debounce_token=None)`.
  - Modify `backend/app/obligations/routes.py:161`: replace the inline f-string with `build_idempotency_key("obligation_extraction", version_id=version.id, snapshot_id=snapshot.id, trigger="manual", debounce_token=f"{user.id}:{minute_bucket(utcnow(), window=5)}")`.
  - Same change in `backend/app/ai/tool_runtime.py:1242` (`trigger="assistant"`).
  - Add `minute_bucket(dt: datetime, *, window: int) -> str` helper in `app/core/timeutil.py` (returns `YYYYMMDDHHMM` truncated to bucket).
- **Blast radius**
  - Two call-sites. One new module. One new helper.
  - Tests: `test_phase6_9_integration.py::test_manual_extract_within_5min_deduped`, `::test_manual_extract_after_5min_allowed`.
- **Dependencies**
  - **Requires CC-5 locked.** Should follow F-14 conceptually (same library, same builder).
- **Done condition**
  - Double-clicking the "Re-extract obligations" button within 5 minutes results in exactly one `JobRun` row.
  - Clicking it again 6 minutes later succeeds.
- **Risk of doing this fix**
  - 5-minute window may surprise users who legitimately want two extracts close together. Mitigation: surface a friendly "Already extracted X seconds ago; please wait Y" message rather than 409ing into the void.

### F-11 — `_persist_obligations` soft-deletes all prior AI obligations every run; human-edited reminders lost
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/ai/controller.py:1446-1542` `_persist_obligations`:
    - Compute a content hash for each new obligation: `sha256(description + due_date + amount + party)`.
    - Compute the same hash for existing obligations.
    - For obligations whose hash matches a prior AI-extracted obligation: UPDATE the existing row's `updated_by_user_id` and skip insertion. Do NOT soft-delete.
    - For obligations not in the new set (and currently active AI-sourced): mark deleted as today.
    - For genuinely new obligations: insert.
    - `ObligationReminder`: only recompute `remind_at` for newly inserted obligations, or for existing obligations whose `due_date` changed.
  - Persist a structured `extraction_diff_log` row (new table OR a single JSON entry in `audit_log.detail`): `{added: N, kept: M, deleted: K, reminder_preserved: M}`.
- **Blast radius**
  - One function in `controller.py`.
  - One new lightweight migration if `extraction_diff_log` is its own table — recommend it goes into `audit_log.detail_json` instead.
  - Tests: new `test_obligation_extraction_diff_preserves_reminders`.
- **Dependencies**
  - None on other F-XX. Can run in parallel with F-13/F-14/F-16.
- **Done condition**
  - Test: extract obligations, hand-edit a reminder's `remind_at`, re-run extraction with identical output, assert the reminder is unchanged.
  - Test: re-run with one removed obligation, assert only that obligation is soft-deleted.
- **Risk of doing this fix**
  - Content-hash schema choice (which fields go in the hash) is a behavior call: if the model returns a typo-corrected description, do we keep or replace? Recommend: hash on `(due_date, amount, party)` only, treating description as mutable. Document the choice.

### F-12 — Audit-log autonomous session swallows Postgres errors silently
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/core/audit.py:54-60` `write_audit_log`:
    - Differentiate "advisory lock not available because backend doesn't support it" (SQLite) from "advisory lock errored on Postgres" (chain integrity event). The current `except Exception:` catches both.
    - Use `if settings.database_url.startswith("sqlite"): pass` to skip the lock; otherwise NOT in a try/except — let the Postgres error propagate.
    - Outer try at `:85-87`: replace the bare `except Exception` with structured catch that:
      1. Logs at ERROR level with structured fields (event_type, actor_id, target_resource).
      2. Increments a counter `audit_log.write_failed_total` (Prometheus-style — even if metrics aren't wired now, the call site exists).
      3. **Raises a new `AuditChainCorrupted` exception** (in `app/core/exceptions.py`). The caller's transaction MUST be rolled back. Today the caller swallows the audit failure and proceeds with the business action — that's exactly the tamper-window F-12 calls out.
  - The behavior change: a business action whose audit row fails now ALSO fails. This is deliberate. Document explicitly.
- **Blast radius**
  - One module rewritten. Every caller of `write_audit_log` (≈30 sites) must handle the new exception OR (preferred) let it propagate to the request handler. Audit at every call-site that no `try/except` swallows it.
  - Tests: `test_phase10_security_hardening.py::test_audit_failure_rolls_back_business_action`.
- **Dependencies**
  - None on other F-XX. But should land BEFORE Sprint 3 perf work that increases audit-write volume.
- **Done condition**
  - Simulated Postgres failure during audit write causes the originating HTTP request to return 500 (or domain-appropriate error) AND no business-row write committed.
  - Logger captures the failure with structured fields.
- **Risk of doing this fix**
  - **High operational risk:** this is the trickiest fix. Today's "silently degrade" becomes "fail the user request." A flaky Postgres connection that used to be invisible now produces visible 500s. Mitigation: only raise on chain-integrity violations (hash mismatch detection, advisory-lock timeout). Connection blips can still retry with a single retry inside `write_audit_log` before raising.

### F-13 — DocuSign webhook HMAC is replayable; no timestamp/nonce window
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/integrations/docusign.py:189-201` `verify_connect_signature`:
    - Add `timestamp_header: str | None` parameter. DocuSign Connect ships `X-DocuSign-Timestamp-1` (verify exact header name in their docs). Reject if `abs(now - timestamp) > 300` seconds.
    - Include the timestamp in the HMAC input: `expected = base64.b64encode(hmac.new(key, body + b"|" + timestamp_header.encode(), hashlib.sha256).digest())`.
  - Modify `backend/app/signatures/routes.py:214-291` `docusign_connect_webhook`:
    - Pass the timestamp header through.
    - Add nonce-style replay protection: extract DocuSign's `envelopeId` + status, keep a `webhook_event_seen` table (new lightweight migration `0010_webhook_event_seen.py`) with `(envelope_id, status, observed_at) UNIQUE`. On duplicate, return 200 with `{"status": "duplicate_ignored"}` instead of re-processing.
    - Reject if `mock_docusign=True` AND `environment != "local"` — defense-in-depth (the boot validator catches this but the webhook route should refuse anyway).
- **Blast radius**
  - One integration function. One route. One new tiny table.
  - Tests: `test_phase10_security_hardening.py::test_docusign_replay_rejected`, `::test_docusign_old_timestamp_rejected`.
  - Customers using DocuSign Connect: verify their Connect configuration sends the timestamp header. Note: if DocuSign Connect doesn't natively ship a timestamp, we synthesize protection from the envelope's existing `eventTimestamp` payload field — there IS one in their schema.
- **Dependencies**
  - None on other F-XX.
- **Done condition**
  - Replay test: same payload+signature submitted twice returns 200 first time, 200-duplicate-ignored second time.
  - Old-timestamp test: payload with `eventTimestamp` 10 minutes ago is rejected.
- **Risk of doing this fix**
  - DocuSign Connect retries on failure — the duplicate table will be touched repeatedly during outages. Mitigation: TTL on the table (drop rows older than 30 days).

### F-14 — Auto-enqueued `contract_brain_ingestion` race: 3 reasons, 3 concurrent ingests
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/jobs/tasks.py:216-251` `_queue_contract_brain_ingestion`:
    - **Remove `:{reason}` from the idempotency key.** Per CC-5, use `build_idempotency_key("contract_brain_ingestion", version_id=..., snapshot_id=..., trigger="auto")`. One key per (version, snapshot) regardless of which upstream job triggered the enqueue.
    - Add a `pg_advisory_xact_lock(hash(f"brain_ingest:{contract_id}"))` at the top of `ingest_contract_brain` (`backend/app/contract_brain/ingestion.py:14`) so even if two jobs are enqueued, they serialize at execution time.
  - Modify `backend/app/contract_brain/ingestion.py` `ingest_contract_brain` to acquire the lock at the start and release on commit.
- **Blast radius**
  - One Celery dispatcher. One ingest function.
  - Tests: `test_phase6_9_integration.py::test_concurrent_brain_ingest_serializes`.
- **Dependencies**
  - **Requires CC-5 locked.**
  - Independent of F-09 (different table, similar pattern).
- **Done condition**
  - Test asserts: trigger clause+obligation+renewal jobs in rapid succession → exactly one `contract_brain_ingestion` `JobRun` row.
  - Concurrent test asserts: two simultaneous ingests for the same contract serialize (second waits, doesn't corrupt graph).
- **Risk of doing this fix**
  - Brain ingestion can be slow (10+ seconds on large contracts). Holding an advisory lock that long is fine — the lock is per-contract, not global.

### F-15 — `tool_runtime.execute` leaves `AssistantToolCall` rows in RUNNING on permission/validation/flush errors
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/ai/tool_runtime.py:86-157` `ToolRuntime.execute`:
    - Reorder: perform permission check + pydantic validation BEFORE inserting the `AssistantToolCall` row. Today validation is before, but the row is still inserted after — if a later step (confirmation creation, flush) raises, the row is left RUNNING. Wrap the entire post-insert flow in a `try/except` that updates the row to FAILED.
    - More importantly: wrap the WHOLE function body in `try/except Exception as exc: call.status = FAILED; call.error_message = str(exc); db.commit(); raise` — same shape the controller uses but inside the runtime.
  - Generalize the stuck-row reaper:
    - Add `backend/app/jobs/reaper.py` with `reap_stuck_rows(*, model, ttl, status_col, status_running, status_failed)`.
    - Use it from `jobs/routes.py:_reap_stuck_jobs` (refactor existing code), from `tabular_review/routes.py:_reconcile_review_status` (refactor existing code), and add a NEW reaper for `AssistantToolCall` with 20-min TTL. Hook into the same call site as `_reap_stuck_jobs` (every list_jobs call, or a periodic hook).
- **Blast radius**
  - One function rewrite (`ToolRuntime.execute`).
  - One new module (`jobs/reaper.py`).
  - Two existing reaper sites refactored to use it.
  - One new reaper for `AssistantToolCall`.
  - Tests: `test_ai_architecture_wiring.py::test_tool_call_failed_on_flush_error`, `::test_stuck_tool_call_reaped_after_ttl`.
- **Dependencies**
  - Independent. Should land in same sprint as F-08 (both improve tool error observability).
- **Done condition**
  - Test asserts: when `_execute_validated` raises mid-flow, the `AssistantToolCall` row has `status=FAILED` and `error_message` populated.
  - Test asserts: a stuck RUNNING row older than 20 minutes is reaped.
- **Risk of doing this fix**
  - Reaper hook point: today's `_reap_stuck_jobs` fires on every list_jobs call (cheap but ad-hoc). For tool calls, that's wrong (no equivalent "list tool calls" call by users). Mitigation: hook into a background scheduler (the request_log_queue daemon thread already exists — bolt on a 60s ticker) OR Celery beat.

### F-16 — Boot validator doesn't check `claude_api_key` is set when `mock_claude=False`
- **Severity (from Agent 2):** High
- **The exact change**
  - Modify `backend/app/core/config.py:142-182` `validate_runtime_settings`:
    - Add: `if not settings.mock_claude and not settings.claude_api_key: problems.append("CLAUDE_API_KEY must be set when MOCK_CLAUDE=false")`.
    - Same shape for `docusign_*` keys (when `mock_docusign=False`, require integration_key/user_id/account_id/private_key_path AND verify the PEM file exists and is readable).
    - Same for `resend_api_key` (when `mock_resend=False`).
    - Same for `reducto_api_key` (when `mock_reducto=False`).
    - Note: `docusign_connect_hmac_key` is intentionally optional and stays optional.
  - Add a positive-side check: when `mock_X=True` AND `X_api_key` IS set, warn (don't fail) — likely misconfiguration where mock was left on by accident.
- **Blast radius**
  - One function. No code outside `config.py`.
  - Deployment runbook: any prod deployment with the bad config now fails to start (was succeed-then-crash-at-request-time before).
  - Tests: `test_phase10_security_hardening.py::test_boot_rejects_missing_claude_key_when_real`, `::test_boot_rejects_missing_docusign_key_when_real`.
- **Dependencies**
  - None. Can run in parallel with F-13 (both touch integration boot integrity).
- **Done condition**
  - Boot with `MOCK_CLAUDE=false` and no `CLAUDE_API_KEY` raises a clear error containing the env-var name.
  - Boot with `MOCK_DOCUSIGN=false` and a missing PEM file raises a clear error.
- **Risk of doing this fix**
  - A deploy that *was* working (because mock was accidentally left on) now refuses to start, exposing what was already a bug. This is desired — flag the change in the deploy notes.

---

## Sprint plan

Sprints are ~2-week chunks for 2–3 engineers in parallel. Items inside a sprint are listed in execution order with parallelism markers `[parallel]` where applicable.

---

### Sprint 1 — Unblockers (critical stability & security)

**Sprint goal:** No critical bug is bleeding. SSE sessions stop racing with FastAPI dependency teardown; cross-tenant leak via admin shortcut is closed; the unauthenticated approval endpoint is rate-limited and oracle-free; AI feature flags actually do something. By end of sprint, all 4 Critical findings have shipped + the two High items most tightly coupled to them (F-07 cancellation, F-09 handle race) ship as a single coherent assistant-runtime patch.

**Items (in execution order within sprint):**
1. **CC-1 thru CC-5 — lock decisions BEFORE any code.** Day 1: write the five short ADRs in `proposals/audit_2026-06-02/adr/`. Sign-off required.
2. **F-04 — Tool feature_flag gate** `[parallel with F-02, F-03]`. New file `app/ai/tool_policy.py`. Lowest blast radius of the Criticals.
3. **F-02 — Token-decision hardening** `[parallel with F-04, F-03]`. Single route + single service function.
4. **F-03 — `accessible_contract_filter` refactor + `AccessPolicy` layer** `[parallel with F-04, F-02]`. Highest blast radius (12 call-sites) — needs careful PR review. Land behind the `feature.access_policy.strict_org_admin` setting so it can roll back if needed.
5. **F-01 — SSE session lifecycle (`StreamingSession`)** — Day 4 onwards, after CC-4 is locked. Owns the assistant runtime; F-07 and F-09 are folded in.
6. **F-07 — Loop budget + cancellation** [folded into F-01 PR].
7. **F-09 — Contract handle TOCTOU** [folded into F-01 PR, ships with migration `0009`].

**Success at end of sprint (observable outcomes):**
- All 4 Critical tests in `tests/test_phase10_security_hardening.py` pass.
- A page-refresh during an assistant stream no longer leaves an `AssistantRun` in RUNNING — it transitions to CANCELLED within 1 iteration.
- An org admin with the `feature.access_policy.strict_org_admin` setting on cannot read another org's obligations (regression test).
- Disabling `feature.ai.edit_suggestions` causes the tool to disappear from the next Claude request payload AND blocks any in-flight call.
- A burst of 31 unauthenticated POSTs to `/token-decision` returns 429 on the 31st.

**Sprint-level risks:**
- **F-03 is the highest-blast-radius single change in the whole roadmap** — 12 query sites, every Contract-joining query. A regression here is a customer-visible "I can't see my contracts" outage. Mitigation: ship behind a setting, stage in test/staging for a week before flipping in prod.
- **F-01 + F-07 + F-09 ship as one PR** — that's a big rewrite of the assistant streaming runtime. Mitigation: break into three commits within one PR for reviewability; require integration test on real Claude with mocks off in staging.
- **Auth/approval UX regression risk on F-02.** A real approver clicking an expired email link now sees "Invalid or expired" rather than the more specific "expired." Mitigation: helpdesk runbook update — direct support staff to the audit log when a real user complains.

---

### Sprint 2 — Architecture corrections (coupling, abstractions, data flow)

**Sprint goal:** The structural problems the Criticals exposed are fixed. Tool errors are observable; tool-call rows can't go stuck; AssistantSessions have a real admin override; webhook integrity is hardened; integration configs fail at boot instead of at first request. Audit log corruption can no longer be silent.

**Items (in execution order within sprint):**
1. **F-12 — Audit-log Postgres error surfacing** — first, because everything else writes audits and we want the new failure mode in place before adding more audit-emitting code.
2. **F-08 — Tool exception logging + timeline + sanitization** `[parallel with F-15]`.
3. **F-15 — `AssistantToolCall` failure on flush error + generic reaper module** `[parallel with F-08]`.
4. **F-06 — AssistantSession admin override (uses `AccessPolicy` from Sprint 1)**.
5. **F-13 — DocuSign webhook timestamp + replay protection (+ migration `0010`)** `[parallel with F-16]`.
6. **F-16 — Boot-time validation of real integration keys** `[parallel with F-13]`.

**Success at end of sprint:**
- Every tool exception produces: a logged stack trace, a `resource_timeline_event` row, a structured error code visible to Claude. No more "tool failed silently."
- `AssistantToolCall` rows older than 20 minutes in RUNNING are reaped to FAILED.
- An org admin investigating an AI conversation can read sessions they didn't create; the read is itself audited.
- A replayed DocuSign webhook returns 200 with `duplicate_ignored` instead of re-transitioning the contract.
- Deploying with `MOCK_CLAUDE=false` and no API key now fails at boot, not at first request.

**Sprint-level risks:**
- **F-12 is operationally scary.** Today's "swallow audit errors" silently degrades; tomorrow's "raise and roll back" surfaces issues that may have been there a long time. Mitigation: deploy with a structured-log alert for `audit_log.write_failed_total` first, before raising, so we see the volume before changing the behavior. Run a 1-week "log only" cycle in staging to confirm no false positives.
- **F-13 requires a coordinated change with the DocuSign Connect configuration.** Customers using Connect must reconfigure their webhook endpoint to include timestamps. Mitigation: make timestamp validation soft-fail (warn only) for one release before flipping to hard-fail.

---

### Sprint 3 — Performance & scalability (queries, async, caching, state)

**Sprint goal:** Remove the cost bombs and the 10× scaling cliffs. AI extraction stops re-creating data on every flaky retry. The brain ingestion no longer triple-runs per contract. Manual button-clicks don't burn money on double-clicks. The "what needs attention" query stops loading the whole org into memory.

**Items (in execution order within sprint):**
1. **F-14 — `contract_brain_ingestion` lock + key consolidation** — first because it depends on CC-5 from Sprint 1 and unblocks scaling tests of the brain.
2. **F-10 — Idempotency key with debounce token for `obligation_extraction`** `[parallel with F-14]`. Uses the same library.
3. **F-11 — `_persist_obligations` content-hash diff** `[parallel with F-10]`.
4. **F-19 (Medium, pulled into Sprint 3) — `_my_attention_items` pagination + scoped queries.** This is the headline scaling cliff and the right home is Sprint 3.

**Success at end of sprint:**
- Double-clicking "Re-extract obligations" inside 5 minutes produces 1 `JobRun`, not 2.
- A contract that's already been brain-ingested gets 1 ingestion job after clause/obligation/renewal extraction, not 3.
- A re-run of obligation extraction that produces identical output does not delete-and-reinsert any rows — reminders survive.
- `_my_attention_items` runs in O(items_visible_to_user), not O(org_total_contracts).

**Sprint-level risks:**
- **F-11 has a subtle semantic risk.** "Identical extraction" via content hash will mask cases where the model improves over time (better description) but the user wanted the improvement. Mitigation: hash on the immutable structural fields only (`due_date`, `amount`, `party`), not description.
- **F-19 may require a query plan that doesn't exist yet.** Loading attention items efficiently across orgs may need new indexes on `(org_id, status, due_date)` for each domain table. Mitigation: review query plans on a 10K-contract staging dataset BEFORE merging.

---

### Sprint 4 — Maintainability & consistency (patterns, typing, error handling, tests)

**Sprint goal:** Clean up the duplication and the cosmetic safety issues. The codebase reads coherently. Tests cover the patterns we want to enforce going forward. Medium/Low backlog is triaged.

**Items (in execution order within sprint):**
1. **F-05 — `_anchor_suggestions` empty-original behavior fix + surfaced dropped-suggestions** `[parallel with the Medium cleanups]`.
2. **Medium/Low cleanups (see appendix)** — F-17, F-18, F-22, F-23, F-24, F-26, F-27, F-28, F-30, F-31, F-32, F-33, F-34, F-35, F-36, F-37 batched into 2-3 PRs by area.
3. **F-29 (Medium) — Add empty-table assertion to migration `0007`** — straightforward defensive PR.
4. **F-21 (Medium) — Convert `status_filter` to enum**.
5. **F-25 (Medium) — `max_length` on TokenDecisionPayload.comment** (likely already done in Sprint 1 with F-02, verify).

**Success at end of sprint:**
- All Medium/Low items either shipped, ticketed, or explicitly deferred with rationale.
- No file has 3 copies of the same logic (F-18, F-30 cleaned by Sprint 1 + Sprint 4).
- The `_redacted_input` substring check is precise (F-31).
- Tests added in earlier sprints continue to pass with the cleaned code.

**Sprint-level risks:**
- **The Medium/Low items are a long tail with low individual risk but cumulative review burden.** Mitigation: bundle by area (3 PRs: "AI cleanups", "auth/security cleanups", "ops cleanups") instead of 1 PR per item.

---

## Medium & Low — backlog

Each item is rolled into a sprint OR deferred. Per the brief, no full per-item plan here — just placement.

| ID | Title (Agent 2) | Severity | Disposition |
|----|------------------|----------|-------------|
| F-17 | INTERNAL_RESULT_KEYS hand-curated denylist | Medium | **Sprint 4** — replace denylist with explicit `to_model_safe(...)` per tool result schema, OR allowlist by-type. |
| F-18 | 3 copies of handle generation logic | Medium | **Sprint 1** — folded into F-09 fix; no separate ticket. |
| F-19 | `_my_attention_items` unbounded org-wide query | Medium | **Sprint 3** — escalated; this is the highest-impact Medium item, treated as a sprint headline. |
| F-20 | Long-lived clients read `settings.*` at request time | Medium | **Sprint 2** — folded into F-13 (read-at-boot pattern is the same fix). |
| F-21 | Obligation `status_filter` free-form | Medium | **Sprint 4** — convert to enum on the Pydantic input. |
| F-22 | SSE `json.dumps(..., default=str)` leaks repr | Medium | **Sprint 4** — replace with explicit serializer in `app/core/streaming.py` (shared with F-01). |
| F-23 | `_apply_anchored` tie-at-0 collapse | Medium | **Sprint 4** — folded into F-05. |
| F-24 | Tools raise `HTTPException(422)` inside tool loop | Medium | **Sprint 4** — introduce `ToolError` exception in `app/ai/errors.py`; convert tool raise sites. |
| F-25 | `decide_via_token` comment unbounded length | Medium | **Sprint 1** — folded into F-02. |
| F-26 | Mock module hardcodes `mock-playbook-id` | Medium | **Sprint 4** — replace with real fixture IDs queried at mock-time. |
| F-27 | `_persist_metadata` auto-overwrites on `confidence=high` | Medium | **Sprint 4** — introduce a `pending_metadata_review` flag, require human accept (behavior change — get sign-off). |
| F-28 | Citation source lookup silently swallows access errors | Medium | **Sprint 4** — replace `except Exception: source_cache[id] = None` with explicit `AccessDenied` handling; mark citation as `validation_status="permission_denied"`. |
| F-29 | Migration 0007 unconditional DROP | Medium | **Sprint 4** — add row-count assertion BEFORE drop; ticket only (migration is already applied). |
| F-30 | Divergent validation between handle implementations | Medium | **Sprint 1** — folded into F-09. |
| F-31 | `"text" in key.lower()` substring match too broad | Low | **Sprint 4** — change to exact-match list. |
| F-32 | `_json_safe` double encode/decode | Low | **Sprint 4** — replace with direct dict walk. |
| F-33 | `allowed_hosts = "*"` default | Low | **Ticket for later, no sprint assignment** — `validate_runtime_settings` already catches in non-local; cost/benefit doesn't justify Sprint 4. |
| F-34 | Dead `feature_not_enabled` branch in `_execute_validated` | Low | **Sprint 1** — folded into F-04 (the branch becomes reachable). |
| F-35 | `from docx import Document` per call | Low | **Ticket for later, no sprint assignment** — 10ms cold-start, not worth disturbing the DOCX renderer. |
| F-36 | Different concurrency patterns in handle queries | Low | **Sprint 1** — folded into F-09. |
| F-37 | Unnecessary `db.refresh(assistant_run)` | Low | **Ticket for later, no sprint assignment** — likely deleted by F-01 anyway. |

---

## End of Agent 3 output
