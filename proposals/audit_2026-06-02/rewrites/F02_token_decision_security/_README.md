# F-02 — Token-decision endpoint hardening (Critical)

**Agent 2 findings:** F-02 (unauthenticated + un-rate-limited token endpoint, 404/409 state oracle) + F-02b (cross-org hash lookup, deactivated-approver actor attribution). Per Agent 3, treated as one combined item.

## Headline change
- Added `@limiter.limit(settings.approval_token_rate_limit)` to the `POST /approvals/token-decision` route.
- New config setting `approval_token_rate_limit` (default `"30/minute;200/hour"`) — see F-16 file `core_config_validate.py` for the config addition.
- `TokenDecisionPayload.token`/`comment` now have explicit bounds (`min_length=16,max_length=200` / `max_length=4_000`) — folds in F-25.
- `redeem_token_decision` collapses **all** failure modes into a single `401 "Invalid or expired approval token"` response. Caller cannot distinguish "no such token" vs "already used" vs "expired".
- `approval.token_attempt` audit row is written on every redemption call — success or failure — with `outcome` / `reason` / `intended_approver_email` / `decision` / `remote_ip` for abuse detection.
- `actor_label` now explicitly tags `(deactivated_user)` when the original approver row resolves to `None`.

## Cross-cutting dependencies
- None new. Reuses CC-1 indirectly through `_apply_decision`, which we leave untouched.

## Agent 3 done-conditions met
- `test_F02_token_decision.py::test_token_decision_rate_limited` — 31 unauthenticated POSTs returns 429 on the 31st.
- `test_F02_token_decision.py::test_token_decision_uniform_error` — "not found", "used", "expired" all return identical 401 bodies.
- `test_F02_token_decision.py::test_token_decision_attempt_audited` — every attempt produces an `approval.token_attempt` audit row.

## Files
- `approvals_routes_token_decision.py` — rate-limited route + capped Pydantic payload.
- `approvals_service_token_decision.py` — uniform-error + audit-on-attempt service function.

## Tests
- `proposals/audit_2026-06-02/tests/test_F02_token_decision.py`
