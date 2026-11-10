# Task Breakdown: Live request summary and extra instructions on the agreement form

Feature ID: 013-live-request-summary
Plan: ./plan.md (APPROVED)
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- Every task maps to requirement(s) from spec.md; plan.md's FR traceability and AC evidence tables map each to a component and a test.
- Contract-first: the Interface freeze in plan.md (API shapes, stored shapes, Pydantic models, TS interfaces, the sentence rules per form, the smart-edit rules, component props, copy) is the contract. Copy it exactly; do not "improve" it.
- One migration, `0076_request_summary` (T001 only), on the single head `0075_nda_check`. After it: `alembic heads` shows one head; `make test-db`.
- Shared hotspots: `frontend/src/lib/types.ts` and `endpoints.ts` are touched by T002 only. `backend/app/intake/{schemas,routes,service,drafts,activity}.py` by T003 only. `_agreement-forms.tsx` and `_agreement-wizard.test.tsx` by T006 only. `_request-overview.tsx` and `_request-asked.tsx` by T007 only. `app/models.py`, `app/jobs/tasks.py`, `src/components/ui.tsx` are untouched.
- Plan's T004 is split in two to keep each to one agent session: the sentence rules (T004) and the smart-edit engine (T005), which imports T004's types and rules.
- Specs 009–012 edits are in the working tree, uncommitted (incl. `0075_nda_check.py`, `nda_check.py`, `_nda-check.tsx`); build on them, never revert or reformat them.
- Backend gates: `docker compose exec backend ruff check .`, `alembic upgrade head` (one head `0076_request_summary`), `python -m pytest -q` against `legal_clm_test`. A new module needs `docker compose restart backend`. Frontend gates from `frontend/`: `npm run typecheck` and `npm run test`; never `npm run lint`.
- Theming: `slate-*` and semantic tokens only, no `bg-white`, no hex. Every test carries a docstring/comment naming the failure mode. Audit rows are written in the filing's own transaction (CLAUDE.md audit-lock rule).

## Waves

### Wave 1 — Foundations (parallel, disjoint files)
- [ ] T001 [P] (db-engineer) Columns `IntakeRequest.filed_summary` (JSON, nullable) and `IntakeDraft.summary_box` (JSON, nullable) + migration `0076_request_summary` on `0075_nda_check` (working downgrade dropping both); run `alembic upgrade head`, confirm one head, then `make test-db` (FR-28, FR-36) — dependsOn: — — files: backend/app/intake/models.py, backend/alembic/versions/0076_request_summary.py
- [ ] T002 [P] (frontend-dev) Frontend contract: every TS interface from the freeze (summary payload, filed record, instruction, differs, accepted change, `SummaryBoxState`, request summary response; `IntakeDraft` gains `summary_box`) and wrapper `intakeApi.requestSummary(id)`; typecheck (FR-28, FR-30, FR-36) — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts

### Wave 2 — Backend ∥ sentence rules ∥ request page
- [ ] T003 [P] (backend-dev) Backend: new `app/intake/request_summary.py` (constants incl. word limit + `LIMIT_MESSAGE`, `clean_text`, `record_summary` — keeps only accepted changes equal to the filed answer, labels from the form, plain text, one `intake.summary_filed` audit row with counts and old/new values but NOT instruction text + one timeline row, only when instructions or accepted changes exist, never raises; `read`); schemas (`RequestCreate.summary`, `DraftSave.summary_box`, `DraftResponse.summary_box`, response models, 422 over the limit before anything is saved); `GET /intake/requests/{id}/summary` behind `get_request` (writes nothing); one call in `create_request` after `record_filing` and after the duplicate-retry return; drafts carry `summary_box`; `EVENTS` row `intake.summary_filed`; `tests/test_request_summary.py` and 2 tests in `tests/test_intake_drafts.py` as in the plan's test strategy (FR-9, FR-28, FR-31 to FR-33, FR-35 to FR-39) — dependsOn: T001 — files: backend/app/intake/request_summary.py, backend/app/intake/schemas.py, backend/app/intake/routes.py, backend/app/intake/service.py, backend/app/intake/drafts.py, backend/app/intake/activity.py, backend/tests/test_request_summary.py, backend/tests/test_intake_drafts.py
- [ ] T004 [P] (frontend-dev) Sentence rules: new `src/lib/request-summary.ts` exactly as frozen (`SENTENCE_RULES` for all nine forms, shared rules, `NAMES`, `SYNONYMS`, `buildSummary`, `summaryText`, `writtenDate`, `moneyText` moved from the wizard's `money()` with identical output, `answerText`); `request-summary.test.ts` with the guard against `agreement_forms.json` (every question has a sentence, rules name real questions/options, variants cover every option, banned advice words) and golden lines for one filled fixture per form (FR-1 to FR-8, FR-38) — dependsOn: T002 — files: frontend/src/lib/request-summary.ts, frontend/src/lib/request-summary.test.ts
- [ ] T007 [P] (frontend-dev) Request page section: new `intake/_request-summary.tsx` `RequestSummarySection` (+test) — text "as filed", extra instructions with notes, writer and time, null when empty/loading, error + Retry, no edit controls; `WhatWasAsked` gains optional `summary` prop rendered after the answers; `_request-overview.tsx` query `["intake-summary", id]` + `SUMMARY_CSS`; update `_request-overview.test.tsx` mocks and add its case (FR-30, FR-35, FR-39) — dependsOn: T002 — files: frontend/src/app/(app)/intake/_request-summary.tsx, frontend/src/app/(app)/intake/_request-summary.test.tsx, frontend/src/app/(app)/intake/_request-asked.tsx, frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx

### Wave 3 — Smart-edit engine
- [ ] T005 (frontend-dev) Smart-edit engine: new `src/lib/summary-edits.ts` exactly as frozen (`WORD_LIMIT`, `WORD_WARN`, `EMPTY_BOX`, `parseDate`, `parseMoney`, `matchChoice` + durations, `checkLine`, `acceptOffer` (the only function returning an answer patch), `declineOffer`, `undoEdits`, `reconcile`, `finalize`, `wordCount`, `differsNote`; register picks never patched; table lines never offered; note line read-only; deleted line → clear offer, required → line back); `summary-edits.test.ts` with the pinned money/date tables and the AC cases listed in the plan (FR-13 to FR-27, FR-32, FR-34) — dependsOn: T004 — files: frontend/src/lib/summary-edits.ts, frontend/src/lib/summary-edits.test.ts

### Wave 4 — Wizard wiring
- [ ] T006 (frontend-dev) Wizard: new `intake/_summary-box.tsx` with `LiveSummary`, `SummaryEditor`, `ExtraInstructions` exactly as frozen (+test: roles, labels, live region, Enter/blur, offers, warning/over-limit bars, gap words); `_agreement-forms.tsx`: `lines` via `buildSummary` in try/catch, `box` state from draft or `EMPTY_BOX`, `reconcile` on answer change, aside order NDA check → live box → "What these answers do" → routing, small-screen link, review step order (name → editor → answers list → note to approvers → extra instructions), `money()` replaced by `moneyText`, submit adds `summary` (refused in the browser over the limit, nothing cut), save-as-draft adds `summary_box`; wizard test cases from the plan (FR-1, FR-2, FR-9 to FR-13, FR-20, FR-29, FR-32, FR-34 to FR-36, FR-38) — dependsOn: T005 — files: frontend/src/app/(app)/intake/_summary-box.tsx, frontend/src/app/(app)/intake/_summary-box.test.tsx, frontend/src/app/(app)/intake/_agreement-forms.tsx, frontend/src/app/(app)/intake/_agreement-wizard.test.tsx

### Wave 5 — QA
- [ ] T008 (qa-engineer) Verify AC-1 to AC-32: all gates (ruff, one Alembic head `0076_request_summary`, full pytest, vitest, typecheck); AC table with test names per plan.md's evidence table; live API checks against the running stack after `docker compose restart backend` (GET summary visibility as requester / staff / plain employee; GET writes nothing); write paths proven by tests unless clearly reversible; browser-only items marked "not exercised" when no browser tools (the orchestrator checks them); write verification.md — dependsOn: T003, T006, T007 — files: specs/013-live-request-summary/verification.md

## Workload

| Agent | Tasks |
|---|---|
| db-engineer | T001 |
| backend-dev | T003 |
| frontend-dev | T002, T004, T005, T006, T007 |
| qa-engineer | T008 |

## Task detail notes

- **T003 needs T001** (both new columns, and `make test-db` for the tests).
- **T004 and T007 need T002** (types). T007 does not need T004–T006: the request page only shows what was filed.
- **T005 needs T004** (it imports `SummaryLine`, `SENTENCE_RULES`, `SYNONYMS`, `NAMES`, `answerText`).
- **T006 needs T005** (and through it T004). It is the only task touching the wizard files, which also hold spec 012's NDA panel wiring — keep that intact.
- Frontend work is the long pole (five tasks, mostly sequential); backend T003 runs beside it in wave 2.
- After T006 lands, the orchestrator regenerates the repo graph (`python scripts/build_repo_graph.py`).
