# Task Breakdown: NDA already in force check at intake

Feature ID: 012-nda-in-force-check
Plan: ./plan.md (APPROVED)
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- Every task maps to requirement(s) from spec.md; plan.md's FR and AC traceability tables map each to a component and a test.
- Contract-first: the Interface freeze in plan.md (JSON shapes, permissions, frozen rules, stored filing shape, TS interfaces, wrappers, component props) is the contract. Copy it exactly; do not "improve" it.
- One migration, `0075_nda_check` (T001 only). Its `down_revision` is the tuple `("0074_merge_clearance", "0074_template_sync")`, which merges today's two heads. After it: `alembic heads` shows one head, and `make test-db` re-runs migrations on `legal_clm_test`.
- Shared hotspots: `frontend/src/lib/types.ts` and `endpoints.ts` are touched by T002 only. `backend/app/intake/activity.py` by T003 then T005 (serialized through dependsOn). `backend/app/intake/schemas.py`, `nda_check.py` and `tests/test_nda_check.py` by T004 then T005 (serialized). `_request-overview.tsx` and its test by T008 only. `app/models.py`, `app/jobs/tasks.py` and `src/components/ui.tsx` are untouched.
- Specs 009 to 011 edits are in the working tree, uncommitted; build on them, never revert them. Unrelated uncommitted edits (`prompt_versions.py`, `agent_catalog.py`, …) are not this feature's.
- Backend gates: `docker compose exec backend ruff check .`, `alembic upgrade head` (one head `0075_nda_check`), `python -m pytest -q` against `legal_clm_test`. A new module needs `docker compose restart backend`. Frontend gates from `frontend/`: `npm run typecheck` and `npm run test`; never `npm run lint`.
- Theming: `slate-*` and semantic tokens only, no `bg-white`, no hex. Every test carries a docstring naming the failure mode it guards. Audit rows are written in the request's own transaction (CLAUDE.md audit-lock rule).

## Waves

### Wave 1 — Foundations (parallel, disjoint files)
- [ ] T001 [P] (db-engineer) `IntakeRequest.nda_check` JSON column (nullable, next to `screening`, with the frozen comment) + migration `0075_nda_check` (tuple `down_revision` merging both heads, working downgrade); run `alembic upgrade head`, confirm one head, then `make test-db` (FR-20) — dependsOn: — — files: backend/app/intake/models.py, backend/alembic/versions/0075_nda_check.py
- [ ] T002 [P] (frontend-dev) Frontend contract: every TS interface from the freeze (`NdaCheckItem`, `MsaHintItem`, `NdaCheckResult`, `NdaFilingItem`, `NdaFilingRecord`, `RequestNdaCheck`, `NdaCheckFiling`, `ContractTypeOption`, `TypeNeededItem`, `TypeNeededList`; `ContractResponse` gains `type_needed`, `suggested_type`) and wrappers `intakeApi.ndaCheck`, `intakeApi.requestNdaCheck`, `contractsApi.types`, `contractsApi.typeNeeded`, `contractsApi.setType`; typecheck (FR-1, FR-21, FR-35 to FR-37) — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts
- [ ] T003 [P] (backend-dev) Contract types: new `app/contracts/contract_types.py` (fixed list, recognition table, `canonical_contract_type`, `type_needed` incl. closed/archived → false, `suggested_type`, `label_of`, `contract_status`); `ContractResponse` computed fields; `ContractUpdate` refuses `contract_type`; models `ContractTypeOption`, `ContractTypeSet`, `TypeNeededItem`, `TypeNeededResponse`; service `set_contract_type` (list check, person wins via `field_sources`, one `contract.type_changed` audit + timeline row, nothing else touched) and `list_type_needed` (org + `accessible_contract_filter` + not deleted, not archived, not closed, cap 500 + `truncated`); routes `GET /contracts/types`, `GET /contracts/type-needed` (declared before `/{contract_id}`), `PUT /contracts/{id}/type`; `EVENTS` row `contract.type_changed`; `tests/test_contract_types.py` incl. AC-27 drafting case and `test_closed_or_archived_blank_type_is_not_listed` (FR-8, FR-31, FR-34 to FR-40) — dependsOn: — — files: backend/app/contracts/contract_types.py, backend/app/contracts/schemas.py, backend/app/contracts/service.py, backend/app/contracts/routes.py, backend/app/intake/activity.py, backend/tests/test_contract_types.py

### Wave 2 — NDA check read path ∥ frontend surfaces
- [ ] T004 (backend-dev) NDA check read path: new `app/intake/nda_check.py` with `nda_check` (register/name matching, per-viewer visibility, status, NDA vs MSA by type only, kind/direction for template-drafted NDAs only, sort, ending soon) and `request_nda_check` (`applies`, register-id sanity check, own contract excluded, stored `listed` re-filtered per viewer, current titles); intake response models; routes `GET /intake/nda-check` and `GET /intake/requests/{id}/nda-check` (writing nothing); `visible_counterparty` note "N NDA in force" via `contract_status`; read-path cases in `tests/test_nda_check.py` (status, recognition, order, kind, matching, byte-identical walled results for NDA and MSA, other org, no `contract:read`, links open, MSA cases, request-page cases, no writes on read, 403/404) and the counterparty note update + ended-NDA case in `tests/test_request_counterparty.py` (FR-1 to FR-16, FR-21, FR-22, FR-24 to FR-33) — dependsOn: T001, T003 — files: backend/app/intake/nda_check.py, backend/app/intake/schemas.py, backend/app/intake/routes.py, backend/app/intake/screening.py, backend/tests/test_nda_check.py, backend/tests/test_request_counterparty.py
- [ ] T006 [P] (frontend-dev) Wizard panel + shared NDA components: `src/lib/nda-check.ts` helpers (+test); `intake/_nda-check.tsx` with `NdaCheckList`, `NdaCheckPanel`, `NdaCheckCard` exactly as frozen (+test); wizard `_agreement-forms.tsx`: `ndaOn` rule, `ndaQ` query (`retry:false`, not part of `body`), panel as first aside block on every step, Cancel → `onBack` (no request call), Continue + optional reason, `nda_check` added to the create payload only when a result was on screen; wizard test cases from the plan (FR-1, FR-2, FR-4, FR-11 to FR-19, FR-29, FR-30, FR-33) — dependsOn: T002 — files: frontend/src/lib/nda-check.ts, frontend/src/lib/nda-check.test.ts, frontend/src/app/(app)/intake/_nda-check.tsx, frontend/src/app/(app)/intake/_nda-check.test.tsx, frontend/src/app/(app)/intake/_agreement-forms.tsx, frontend/src/app/(app)/intake/_agreement-wizard.test.tsx
- [ ] T007 [P] (frontend-dev) Contract types UI: `components/contract-type-picker.tsx` (+test); `contracts/_type-needed.tsx` collapsible panel (+test) rendered on `contracts/page.tsx` between the briefing and the toolbar; contract page `.ctx` prompt row in `_clm-workspace.tsx` when `type_needed` and the viewer may edit (+test) (FR-35 to FR-37) — dependsOn: T002 — files: frontend/src/components/contract-type-picker.tsx, frontend/src/components/contract-type-picker.test.tsx, frontend/src/app/(app)/contracts/_type-needed.tsx, frontend/src/app/(app)/contracts/_type-needed.test.tsx, frontend/src/app/(app)/contracts/page.tsx, frontend/src/app/(app)/contracts/[id]/_clm-workspace.tsx, frontend/src/app/(app)/contracts/[id]/_clm-workspace.test.tsx

### Wave 3 — Filing record ∥ request page card
- [ ] T005 [P] (backend-dev) Filing record: `NdaCheckFiling` (validators that never raise) + `RequestCreate.nda_check`; `record_filing` in `nda_check.py` (recompute for the filer ∩ `shown_ids`, store the frozen shape, one `intake.nda_check_continued` audit + one timeline row in the filing transaction, try/except so it never breaks filing, nothing on a duplicate retry); one call in `create_request`; `EVENTS` row `intake.nda_check_continued`; filing cases in `tests/test_nda_check.py` (stored record, one audit/timeline row, forged or walled id dropped, no `nda_check` → nothing, oversized input → 201, nothing matched → nothing stored, lawyer behind a wall sees choice and reason with `listed == []`) (FR-17 to FR-20, FR-23, audit section) — dependsOn: T004 — files: backend/app/intake/nda_check.py, backend/app/intake/schemas.py, backend/app/intake/service.py, backend/app/intake/activity.py, backend/tests/test_nda_check.py
- [ ] T008 [P] (frontend-dev) Request page card: `ndaQ` query on `intakeApi.requestNdaCheck`, `<NdaCheckCard>` directly above `CounterpartyCard` in the side column, its own load state so a failure never blanks the page; update the existing layout assertion and add the plan's cases to `_request-overview.test.tsx`; run the full vitest suite and typecheck (FR-21 to FR-23) — dependsOn: T006 — files: frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx

### Wave 4 — QA
- [ ] T009 (qa-engineer) Verify AC-1 to AC-31: all gates (ruff, one Alembic head `0075_nda_check`, full pytest, vitest, typecheck); AC table with test names per plan.md's evidence table; live checks against the running stack after `docker compose restart backend` (wizard panel, cancel leaves no rows, continue stores one audit row, request page as the assigned lawyer and as a walled user, `/contracts` type-needed count vs `psql`, set one type and see it in the check); light + dark screenshots where browser tools allow, otherwise mark browser-only items "not exercised"; write verification.md — dependsOn: T005, T007, T008 — files: specs/012-nda-in-force-check/verification.md

### Follow-up (added 2026-10-27 after the live check, user-approved; plan DEC-3)
- [x] T010 (backend-dev) NDA kind rule: treat an NDA as drafted from our template when the contract has the `drafted_from_template` marker OR its linked request's `field_values.paper == "Our template"`; "Their paper" and uploaded NDAs still give `kind: null`; tests in `tests/test_nda_check.py` (FR-12, FR-13) — dependsOn: T005 — files: backend/app/intake/nda_check.py, backend/tests/test_nda_check.py

## Workload

| Agent | Tasks |
|---|---|
| db-engineer | T001 |
| backend-dev | T003, T004, T005 |
| frontend-dev | T002, T006, T007, T008 |
| qa-engineer | T009 |

## Task detail notes

- **T004/T005 split.** The plan's single "NDA check" backend task is split into the read path (T004) and the filing record (T005) to keep each to one agent session. They share `nda_check.py`, `schemas.py` and `test_nda_check.py`, so T005 depends on T004 and never runs beside it.
- **T003 before T004.** `nda_check.py` and `screening.py` import `contract_status` / `canonical_contract_type` from T003's module.
- **T001 before T004.** The request-page read path reads `IntakeRequest.nda_check`, and the tests need the column on `legal_clm_test` (`make test-db`).
- **T008 after T006.** The request page imports `NdaCheckCard` from `_nda-check.tsx`.
- After T008 lands, the orchestrator regenerates the repo graph (`python scripts/build_repo_graph.py`).
