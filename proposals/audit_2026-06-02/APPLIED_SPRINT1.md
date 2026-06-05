# Sprint 1 — Applied fixes (branch `fix/sprint1-criticals`)

Applied to **live source**, verified against ground truth, suite green (76 passed).
Not committed — left on the branch for review.

| Finding | Status | What landed | Files | Test |
|---|---|---|---|---|
| **F-03** tenant leak | ✅ Fixed | `accessible_contract_filter` is now always org-scoped; admins escalate *within* their org (no more `true()`). Fixes all 10 call sites at the source. | `contracts/access.py` | `test_f03_*` |
| **F-02** token oracle + no rate limit | ✅ Fixed | All 4 token-failure paths collapse to one uniform `401` (reason logged server-side only); added per-IP `@limiter.limit(rate_limit_token_decision)` (5/min). | `approvals/service.py`, `approvals/routes.py`, `core/config.py` | `test_f02_*` |
| **F-04** feature flags not enforced | ✅ Fixed | New `ai/tool_policy.is_tool_enabled()` (mirrors the existing skill gate). Gates both schema-build (Claude never sees disabled tools) and `execute()` (defense-in-depth, no RUNNING row). | `ai/tool_policy.py` (new), `ai/controller.py`, `ai/tool_runtime.py` | `test_f04_*` |
| **F-01** SSE session lifecycle | ◑ Partial + ticketed | Landed: client-disconnect guard stops the paid Claude loop when the user leaves. | `assistant/routes.py` | — (needs streaming harness) |

## F-01 — correction to Agent 2's severity

Agent 2 rated this Critical on the premise that the SSE generator writes to an **already-closed** DB session. **Verified against the pinned stack (FastAPI 0.136.1 / Starlette 1.0.0): `yield` dependencies are held open until the `StreamingResponse` finishes, so that does not happen on the happy path.** The real, version-independent risks that remain:

1. **Connection-pool hold** — one pooled DB connection is pinned for the entire multi-second, 8-iteration Claude interaction. Under concurrent assistant load this exhausts the pool → outage. (Scaling-Critical, real.)
2. **No disconnect cancellation** — fixed now (guard added).

Reclassify F-01 from "data corruption, Critical-now" to "connection-pool exhaustion, Critical-at-scale." The full fix (isolate the ~500-line controller onto its own session, re-attach ORM objects, release the connection between iterations) is **task #10** — it cannot be verified without an integration test through uvicorn's StreamingResponse and must not land blind.

## Deferred (Sprints 2–4)
The 12 High findings (F-05–F-16) per `03_roadmap.md`. Agent 4's proposal rewrites in `rewrites/` remain the reference for those.

## Verification round (2026-06-03) — 4 parallel adversarial reviewers

Re-audited each fix against live code (not summaries). Result + follow-on:

- **F-03** — verdict FIXED. All 11 `accessible_contract_filter` call sites confirmed tenant-safe; no over-restriction (project shares are intra-org by construction).
- **F-02** — verdict FIXED (core). Oracle gone + rate limit live. Residuals (do not reopen the brute-force vector): X-Forwarded-For IP rotation defeats per-IP limit behind a proxy with no trusted-hops config; timing asymmetry; a valid-token-holder-only 422/409 oracle in `_apply_decision`. Test now covers all 4 failure branches. → task #15.
- **F-04** — review found TWO real holes; **both now fixed**: (1) fail-open coercion `bool("false") == True` → replaced with `flag_value_is_enabled()` (shared; also fixed the same latent bug in the skills gate `_ensure_skill_enabled`); (2) `execute_confirmed` resume path didn't re-check the flag → gate added (marks the call FAILED). New tests: string-`"false"` fail-open guard + coercion matrix.
- **F-01** — severity correction confirmed ACCURATE (FastAPI 0.136/Starlette 1.0 hold the session open during streaming). But the disconnect guard's comment OVERCLAIMED: on pinned uvicorn 0.47 (ASGI 2.3 cancel path) a mid-stream disconnect cancels the generator before finalization, so a RUNNING row can still be left. Comment corrected to be honest; durable fix (terminal-state in `finally` + own session) sharpened in task #10. **F-01 remains not-fully-fixed by design.**

Suite after this round: **78 passed, 0 failures.**
