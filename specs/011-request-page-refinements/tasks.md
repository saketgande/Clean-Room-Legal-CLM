# Task Breakdown: Request page refinements

Feature ID: 011-request-page-refinements
Plan: ./plan.md (APPROVED)
Created: 2026-10-23
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- Every task maps to requirement(s) from spec.md. The plan's traceability tables map every FR/AC to a component and a test; each task covers the FRs of its files (named below where useful).
- No migration and no model change: db-engineer has no tasks, the Alembic head stays `0074_template_sync`.
- Contract-first: the Interface freeze in plan.md (exact JSON, permissions, error codes, the 75-row event-type table, TS interfaces, wrappers) is the contract. Copy it exactly; do not "improve" it.
- Shared hotspots: `frontend/src/lib/types.ts` and `endpoints.ts` are touched by T002 only; `backend/app/intake/routes.py` and `schemas.py` by T001 then T003 (serialized); `lifecycle-view.tsx` by T004 only; `_request-overview.tsx`, its test, and `intake/page.tsx` by T006 only.
- Spec 009/010 edits are in the working tree uncommitted; build on them, never revert them. The unrelated uncommitted `prompt_versions.py` / `agent_catalog.py` edits are not this feature's; the one failing backend test `test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had` is a known, unrelated failure.
- Backend gates: `docker compose exec backend ruff check .`, `alembic upgrade head` (no-op), `python -m pytest -q`. A new module needs `docker compose restart backend`. Frontend gates from `frontend/`: `npm run typecheck` (only the two stale `.next/types/validator.ts` docstudio errors may remain) and `npm run test`; never `npm run lint`.
- Theming: no hardcoded colours, no `bg-white`; palette variables only. Every test carries a docstring naming the failure mode it guards.

## Waves

### Wave 1 — Independent foundations (parallel, disjoint files)
- [ ] T001 [P] (backend-dev) Activity read: new `app/intake/activity.py` (the event-type -> sentence/who/system/tone table for all 75 types, safe generic fallback, raw-code/id guard, newest first, cap 500, contract events only when the viewer has `contract:read` and passes the contract access pre-check without writing an `access.denied` audit row), response models, `GET /intake/requests/{id}/activity` (permission as in the plan, no writes), `tests/test_request_activity.py` including a scan that fails when an event type has no sentence (spec FR-8..FR-13 and the access ACs) — dependsOn: — — files: backend/app/intake/activity.py, backend/app/intake/schemas.py, backend/app/intake/routes.py, backend/tests/test_request_activity.py
- [ ] T002 [P] (frontend-dev) Frontend contract: TS interfaces for the activity and counterparty responses and the intake client wrappers (`intakeApi.activity`, `intakeApi.counterparty`) exactly as frozen — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts
- [ ] T004 [P] (frontend-dev) WorkflowCard replacing `StepsCard` in place: one wide card in the main column, a tile row (done tinted, current highlighted with who it waits on / overdue / Check approval status, upcoming plain), links that open Review result, Approval chain, Agent hand-off trace and Send back inside the card (send-back open state is controlled by the parent), wraps at narrow widths; extract `stepFacts` from `lifecycle-view.tsx` with unchanged `NextStepCard` output; `overdueWords` helper; delete the orphaned side-column steps CSS; tests rewritten (spec Steps requirements, FR-1..FR-7) — dependsOn: — — files: frontend/src/app/(app)/intake/_request-steps.tsx, frontend/src/app/(app)/intake/_request-steps.test.tsx, frontend/src/components/lifecycle-view.tsx, frontend/src/components/lifecycle-view.render.test.tsx, frontend/src/lib/workflow-steps.ts, frontend/src/lib/workflow-steps.test.ts

### Wave 2 — Counterparty read ∥ new cards
- [ ] T003 (backend-dev) Counterparty read and the leak fix: stop storing the org-wide dossier in the screening result, strip it from old rows when the request is serialized (`_public_screening`), drop `prior_contracts` from the screening audit payload (the plan's D5), `GET /intake/requests/{id}/counterparty` computing name, summary, contracts[] and requests[] per viewer (`accessible_contract_filter` + `contract:read`; intake `_accessible` for requests; exclude the current contract and request; name match pushed into SQL with `_name_match` confirming; live first then closed/expired, newest first; lists cap 100; counts cover the whole visible set; no total value); `tests/test_request_counterparty.py` (walled contract excluded from lists AND counts, requester sees only own requests, other org 404, nothing leaked through `GET /intake/requests/{id}` or the list endpoints) and the single assertion update in `test_intake_every_party.py` (spec FR-14..FR-24) — dependsOn: T001 — files: backend/app/intake/screening.py, backend/app/intake/service.py, backend/app/intake/schemas.py, backend/app/intake/routes.py, backend/tests/test_request_counterparty.py, backend/tests/test_intake_every_party.py
- [ ] T005 [P] (frontend-dev) ActivityCard and CounterpartyCard rewritten against the new endpoints: Activity grouped by day newest first, plain sentences with who and time, batches of 20, 'Show system details' toggle shown only when something is hidden, one-line empty strip, hand-offs/comments kept per the plan; Counterparty with the name, summary line, clickable contract items (title, type, stage, value, date) and request items (ref, type, status), 'show more', clicking a request calls `onOpenRequest`; helpers `groupActivityByDay` and `counterpartySummary`; delete `activityTrail`; tests (spec FR-8..FR-24 frontend parts) — dependsOn: T002 — files: frontend/src/app/(app)/intake/_request-cards.tsx, frontend/src/app/(app)/intake/_request-cards.test.tsx, frontend/src/lib/request-facts.ts, frontend/src/lib/request-facts.test.ts

### Wave 3 — Page wiring (the hotspot, one task)
- [ ] T006 (frontend-dev) Wire the page: main-column order decision area, What was asked, AI risk review, Workflow card, Activity, Comments and questions; side column Counterparty, Owner and people, Documents, Ask Aegis; queries for the two new endpoints (separate load states so one failing never blanks the page); send-back open state in the parent so the decision area's 'Send back for rework…' opens and focuses the Workflow card's form; `onOpenRequest` prop and `key={detailId}` on `<RequestOverview>` in `page.tsx` so drafts do not carry over; update the existing tests the plan lists (mocks, column order, `.steps` -> `.wft`, send-back opening, comment-in-Activity, the 'Globex once' count) and add the new AC cases; run the full vitest suite and typecheck — dependsOn: T002, T004, T005 — files: frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx, frontend/src/app/(app)/intake/page.tsx

### Wave 4 — QA
- [ ] T007 (qa-engineer) Verify AC-1..AC-29: all gates (ruff, alembic head still one, pytest incl. the new files with the one known unrelated failure, vitest, typecheck); AC table with test names; live checks against the running stack after `docker compose restart backend` for what browser tools allow (if you have none, say so and mark browser-only items 'not exercised'; the orchestrator measures the live page itself); explicit access checks via the API as users who must NOT see a walled contract and a requester who must see only their own requests; write verification.md — dependsOn: T001, T003, T006 — files: specs/011-request-page-refinements/verification.md

## Workload

| Agent | Tasks |
|---|---|
| backend-dev | T001, then T003 (serial; shared routes/schemas) |
| frontend-dev | T002 and T004 (parallel), then T005, then T006 |
| qa-engineer | T007 |
| db-engineer | none |

After T006 the orchestrator regenerates the repo graph (`python scripts/build_repo_graph.py`) and checks the live page in the browser.

## Task detail notes

- Frontend agents code against mocks of the frozen contract until the backend tasks land; the live endpoints exist after T001/T003.
- The plan's Test strategy lists every existing test that must change and how; follow it, never weaken an assertion silently.
- Access ACs are the crux: a walled or cleared-off contract must never appear in a list, a count, the summary, the stored note, the activity list, or any list/serialize endpoint payload for that viewer.
- Dev data for manual checks: REQ-7705 (counterparty Initech Software Ltd, 6 matching contracts incl. its own, 7 earlier requests); other users must be created through the app's own APIs and cleaned up afterwards, and the cleanup stated in the report.
