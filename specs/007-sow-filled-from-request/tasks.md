# Task Breakdown: SOW drafts filled from the request

Feature ID: 007-sow-filled-from-request
Plan: ./plan.md (must be APPROVED)
Created: 2026-10-15
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- `dependsOn: —` means no dependencies (wave 1).
- Every task maps to requirement(s) from spec.md, noted as `(FR-N)`.
- Two tasks that touch the same file must never carry `[P]` together — watch the
  shared hotspots: `backend/app/models.py`, `backend/app/jobs/tasks.py`,
  `frontend/src/lib/endpoints.ts`, `frontend/src/lib/types.ts`,
  `frontend/src/components/ui.tsx`.
- Migration tasks never run in parallel with each other (single Alembic head).

**No migration** in this feature (plan "Database schema"): head stays `0074_template_sync`.

**Money rule:** no task makes a paid call. Tests run with `MOCK_*` on; drafting tests use the
existing in-memory upload pipeline. A real check or draft on the stack happens only at the QA
walk, with the owner's go-ahead.

**Contract rule:** every task codes against plan.md's Interface freeze — the `rows` question kind
and its JSON, the exact messages M1–M12 / F1–F2, the `field_values` shape, the 9 `PLACEHOLDERS`
entries, the `sow.py` formats, the `sow.txt` Fill map, `DRAFT_HELD` / `HELD_STEP_NOT_MANUAL`, the
`form-checks.ts` exports and the TS types. A task that needs to change any of those stops as
`blocked`.

**Done means:** the task's tests pass, plus `ruff check .` (backend) or `npm run typecheck` +
`npm run test` (frontend; never `npm run lint`).

## Waves

### Wave 1 — Form contract ∥ SOW fill and template (no shared files)
- [ ] T001 [P] (backend-dev) SOW form JSON: replace `deliverables` with the `deliverable_rows` question (`kind: "rows"`, `min_rows: 1`, `max_rows: 20`, `legacy_key`/`legacy_label`, five columns with the share column's `show` rule) exactly as frozen; `no_blanks` on `sow_title`, `scope`, `cp_signer_name`, `cp_signer_email`; `min_exclusive: 0` on `value`; the shared rule-cases file with every M1–M12 / F1–F2 case and its exact message (FR-7, FR-9, FR-14, FR-15, FR-16, FR-41, FR-42) — dependsOn: — — files: backend/app/intake/agreement_forms.json, backend/tests/fixtures/deliverable_rows_cases.json
- [ ] T002 [P] (backend-dev) SOW fill and template: `intake/sow.py` (the nine fill values, `day`, `money`, `split_payments` with exact decimals and the remainder-to-last rule, `EXAMPLE_VALUES` / `EXAMPLE_MARKERS`, hold messages); new `defaults/sow.txt` per the clause-by-clause Fill map (no blank bracket); `intake/drafting.py` (`example=` on `template_values`/`render_document`, `DraftHeld` + `draft_hold` before any pin/render/record, FR-37 record keys); 9 `PLACEHOLDERS` + the `value_sentence` sample; `checks.py` (`example=True` in `check_values`/`_review`, `_CODED` += 9, the Apply guard); the exam renders the SOW from a byte-for-byte frozen copy of today's `sow.txt` (owner decision); tests `test_sow_drafting.py` (every AC-47 money example, AC-1 no blanks via `find_blanks`, example content never in a real draft) and the `_bare_draft` updates (FR-1–FR-6, FR-19–FR-30, FR-33, FR-36, FR-37, FR-44) — dependsOn: — — files: backend/app/intake/sow.py, backend/app/drafting_templates/defaults/sow.txt, backend/app/intake/drafting.py, backend/app/drafting_templates/service.py, backend/app/drafting_templates/checks.py, backend/eval/exam/build.py, backend/eval/exam/sow_2026-10-15.txt, backend/tests/test_sow_drafting.py, backend/tests/test_template_check.py, backend/tests/test_template_check_jobs.py

### Wave 2 — Filing rules ∥ wizard ∥ workflow hold
- [ ] T003 [P] (backend-dev) Filing and update rules: `agreement_forms.form_fields` passes the new attributes, `clean_rows` (tidy, row numbering as sent, uniqueness, due within the services window, share column show/strip, total 100), `answer_text`; `intake/service.py` `_validate_field_values(..., stored=)` with `no_blanks`, `min_exclusive`, the rows post-pass and the unchanged-answer exemption (FR-32); `create_request` audit facts; `update_request` passes `stored`, carries the legacy key over, writes `intake.deliverables_changed` (rows before/after) to audit + timeline; triage and flow prompts read `answer_text`; tests `test_sow_deliverable_rows.py` (reads the shared cases file), `test_sow_request_updates.py`, and `complete()` in `test_intake_form_values_make_sense.py` learns `rows` (FR-8–FR-11, FR-13–FR-16, FR-18, FR-31, FR-32, FR-38–FR-43) — dependsOn: T001 — files: backend/app/intake/agreement_forms.py, backend/app/intake/service.py, backend/app/intake/triage_agent.py, backend/app/intake/flow_agent.py, backend/tests/test_sow_deliverable_rows.py, backend/tests/test_sow_request_updates.py, backend/tests/test_intake_form_values_make_sense.py
- [ ] T004 [P] (frontend-dev) Wizard: TS types (`RequestFormColumn`, `rows` kind, `FormRow`, `FormValue`, `IntakeDraft.values`); `lib/form-checks.ts` mirror (`findBlanks`, `tidyCell`, `rowsProblems`, `shareTotal`, `pastDueRows`, `longDate`, `answerProblem` rules) with tests that read the SAME shared cases file; `_deliverables.tsx` (`RowsTable`: labelled cells, add/remove, 20-row cap, per-cell errors, past-date note, running share total; `RequestDeliverables`: read-only rows, earlier free-text answer, editor for older requests that saves then refreshes the run); `_agreement-forms.tsx` (table in the SOW step, problems in the banner, review step shows rows and says "Statement of Work template", filing body drops the legacy key, saved draft shows old text beside the table, LoadError with Retry); `agreement-forms.test.ts` and wizard tests (FR-7–FR-12, FR-14, FR-16–FR-18, FR-31, FR-34, FR-41–FR-43) — dependsOn: T001 — files: frontend/src/lib/types.ts, frontend/src/lib/form-checks.ts, frontend/src/lib/form-checks.test.ts, frontend/src/app/(app)/intake/_deliverables.tsx, frontend/src/app/(app)/intake/_deliverables.test.tsx, frontend/src/app/(app)/intake/_agreement-forms.tsx, frontend/src/app/(app)/intake/agreement-forms.test.ts, frontend/src/app/(app)/intake/_agreement-wizard.test.tsx
- [ ] T005 [P] (backend-dev) Workflow hold: the `clm_draft` step catches `DraftHeld`, waits for a person with the hold recorded (not skipped, not complete, no contract); `_resolve_waiting_group` re-arms a held step once rows exist so refresh or the existing resume beat drafts it; `complete_human_step` refuses a held step with `HELD_STEP_NOT_MANUAL`; extend `test_workflow_engine_consistency.py` (stops with the reason, not skipped, continues after rows are entered, manual complete refused) (FR-33, FR-45) — dependsOn: T002 — files: backend/app/workflows/service.py, backend/tests/test_workflow_engine_consistency.py

### Wave 3 — Request page
- [ ] T006 (frontend-dev) Request overview: mount `RequestDeliverables`; on a held draft step hide "Approve & advance" and show the hold reason; tests (FR-18, FR-31, FR-33, FR-45) — dependsOn: T004, T005 — files: frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx

### Wave 4 — Verification
- [ ] T007 (qa-engineer) Verify 007: full CI gates (`ruff`, `alembic heads` still `0074_template_sync`, full `pytest`, typecheck, vitest incl. `test_exam_content.py` green on the frozen copy); map AC-1..AC-48 to test ids; check the shipped `sow.txt` with `find_blanks` (zero); live API read-only checks; leave a browser-walk section for the orchestrator (wizard table, Milestones %, filing errors, request page rows, held older request) — dependsOn: T003, T006 — files: specs/007-sow-filled-from-request/verification.md

## Task detail notes

- **Hotspots (checked):** `frontend/src/lib/types.ts` only T004; `endpoints.ts`, `ui.tsx`,
  `models.py`, `jobs/tasks.py` no task. `agreement_forms.json` only T001 (T003 and T004 read it, so
  they wait for T001). `intake/drafting.py` and `checks.py` only T002; `intake/service.py` only T003;
  `workflows/service.py` only T005; `_agreement-forms.tsx` and `_deliverables.tsx` only T004
  (T006 imports `RequestDeliverables`).
- **Renumbering vs plan.md:** plan.md's path mapping calls the SOW fill task T003 and the filing
  rules T002; here they are T002 and T003 so wave 1 reads in order. Files and content are exactly
  plan.md's.
- **`backend/eval/exam/**`** is assigned to T002 only (owner approved the frozen copy on
  2026-10-15) so the AI exam's content and baseline stay reproducible.
- **Paid steps:** none in any task. The QA browser walk may run one real Templates-page check of
  the new SOW wording only with the owner's go-ahead.
