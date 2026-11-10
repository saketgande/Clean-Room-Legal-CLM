# Task Breakdown: Consultancy and DPA drafts with no blank brackets

Feature ID: 008-consultancy-dpa-no-blanks
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

Task numbers are plan.md's own (T001–T007), unchanged. **No migration**: head stays `0074_template_sync`.

**Money rule:** no task makes a paid call. Tests run with `MOCK_*` on. A real check, filing or draft on the
stack happens only at the QA walk with the owner's go-ahead.

**Contract rule:** every task codes against plan.md's Interface freeze — the form JSON shapes, the new
column/show/update rules (`open_end`, `outside`, `must_be`, `not`, `rules_when`, `older_may_lack`,
`legacy_single`), messages M9b/M13/M14/F3/F4 word for word (007's M1–M12/F1–F2 unchanged), the
`field_values` shapes, the 10 PLACEHOLDERS, the `consultancy.py`/`dpa.py` formats, the template Fill
maps, the hold / free-text / Processor strings, the held and skipped step shapes, the audit actions, the TS
types and the `form-checks.ts` exports. A task that needs to change any of those stops as `blocked`.

**007 stays green:** 007's shared cases file is not edited and its tests pass unmodified, except the two
`test_sow_drafting.py` tests plan.md names (T003).

**Done means:** the task's tests pass, plus `ruff check .` (backend) or `npm run typecheck` +
`npm run test` (frontend; never `npm run lint`).

## Waves

### Wave 1 — Form contract ∥ fills, templates and exam freeze
- [ ] T001 [P] (backend-dev) Form JSON: `new_agreement` gains the 7 Consultancy questions (`advice_only`, `consultancy_deliverables`, `fee_basis`, `fee_rate`, `key_individuals`, `company_contact_name`, `company_contact_role`) with their `show` rules (Consultancy, our paper) and `rules_when` on `scope`/`value`; `dpa`: `data_types`/`data_subjects` become multi-tick with no "Several", the `transfer_countries` and `subprocessor_rows` tables, `fills` on the 5 existing questions, `no_blanks` on `processing_purpose`, corrected help text; the sibling shared cases file `consultancy_dpa_cases.json` with every new rule's cases and exact messages (FR-12–FR-20, FR-24–FR-29, FR-21, FR-30) — dependsOn: — — files: backend/app/intake/agreement_forms.json, backend/tests/fixtures/consultancy_dpa_cases.json
- [ ] T003 [P] (backend-dev) Fills and templates: `intake/consultancy.py` and `intake/dpa.py` (fill values, formats, `EXAMPLE_ANSWERS`/`EXAMPLE_VALUES`/markers, shaped like `sow.py`); `sow.py` renames only `_total` → `total_value`; new `consultancy.txt` schedule and `dpa.txt` Annexes 1 and 3 per the Fill maps (no blank bracket); `template_values` spreads the two new dicts; 10 PLACEHOLDERS + the `services`/`fee_amount` "about" updates; `checks.py` `_CODED` and Apply-guard markers; the exam renders consultancy and dpa from byte-for-byte frozen copies of HEAD's templates (owner approved); tests `test_consultancy_dpa_drafting.py` and the two named `test_sow_drafting.py` changes (FR-1–FR-11, FR-33–FR-40, FR-47, FR-48) — dependsOn: — — files: backend/app/intake/consultancy.py, backend/app/intake/dpa.py, backend/app/intake/sow.py, backend/app/drafting_templates/defaults/consultancy.txt, backend/app/drafting_templates/defaults/dpa.txt, backend/app/intake/drafting.py, backend/app/drafting_templates/service.py, backend/app/drafting_templates/checks.py, backend/eval/exam/build.py, backend/eval/exam/consultancy_2026-10-15.txt, backend/eval/exam/dpa_2026-10-15.txt, backend/tests/test_consultancy_dpa_drafting.py, backend/tests/test_sow_drafting.py

### Wave 2 — Server rules ∥ wizard
- [ ] T002 [P] (backend-dev) Filing and update rules: `form_fields`/`_column` pass the new attributes; `shown` reads `not`; `clean_rows` gains M9b/M13/M14; `tidy` public; `answer_text` joins lists; `_validate_field_values` / `_refuse_unsound_answer` / `_check_table` with `rules_when`, `older_may_lack`, `legacy_single` and F3/F4; `[]` counts as no answer on the server; `create_request` audit facts; `update_request` → `_record_answer_changes` (`intake.answers_changed` audit + timeline); tests `test_consultancy_dpa_answers.py` (reads the shared cases file) and `test_consultancy_dpa_request_updates.py`; 007's suites unchanged and green (FR-12–FR-21, FR-24–FR-30, FR-41, FR-42, FR-50–FR-52) — dependsOn: T001 — files: backend/app/intake/agreement_forms.py, backend/app/intake/service.py, backend/tests/test_consultancy_dpa_answers.py, backend/tests/test_consultancy_dpa_request_updates.py
- [ ] T004 [P] (frontend-dev) Wizard and client checks: `types.ts` additions; `form-checks.ts` (`shown` `not`, open-end `dateWindow`, `rowsProblems` M9b/M13/M14, `answerProblem` F3/F4, `rulesFor`, `lacksAnswer`, `needsDraftAnswers`, `PROCESSOR_ROLE`) with tests reading the SAME shared cases file; `RowsTable` (`item` noun, `ref_prefix`, one-column list, open-end hint); `_agreement-forms.tsx` (step placement, corrected help, `DRAFT_OF` → "Consultancy Agreement template", exported `Question` with `earlier`, multi-tick legacy handling, `rulesFor`, `fee_rate` on review); wizard and forms tests (FR-21–FR-23, FR-30–FR-32, FR-45) — dependsOn: T001 — files: frontend/src/lib/types.ts, frontend/src/lib/form-checks.ts, frontend/src/lib/form-checks.test.ts, frontend/src/app/(app)/intake/_deliverables.tsx, frontend/src/app/(app)/intake/_deliverables.test.tsx, frontend/src/app/(app)/intake/_agreement-forms.tsx, frontend/src/app/(app)/intake/agreement-forms.test.ts, frontend/src/app/(app)/intake/_agreement-wizard.test.tsx

### Wave 3 — Hold, Processor skip, workflow ∥ request page
- [ ] T005 [P] (backend-dev) Hold and Processor rule: `DraftHeld(detail)`; `TemplateSkip`/`template_skip` (422 with the D-11 note, checked before the hold, nothing written); generic `draft_hold` (SOW's three lines kept exactly) with the missing-answers and free-text messages; `held_step`; FR-49 record keys; `workflows/service.py`: the `clm_draft` skip branch before the custom fallback (note, then continue; no contract, no bespoke AI draft; `workflow.draft_skipped` audit + `flow.draft_skipped` timeline, actor None), held catch via `held_step`, `_is_held`, `complete_human_step` refusal for answers/form holds; tests `test_consultancy_dpa_hold.py` and the `test_workflow_engine_consistency.py` extension; 007's hold tests unchanged and green (FR-43, FR-44, FR-46, FR-49, FR-53–FR-56) — dependsOn: T002, T003 — files: backend/app/intake/drafting.py, backend/app/workflows/service.py, backend/tests/test_consultancy_dpa_hold.py, backend/tests/test_workflow_engine_consistency.py
- [ ] T006 [P] (frontend-dev) Request page: `_request-answers.tsx` (`RequestAnswers`: SOW keeps 007's block; Consultancy/DPA show the answers, `EarlierAnswer` for a legacy "Several", the hold reason, and an editor when answers are missing that saves then refreshes the run); `_request-overview.tsx` mounts it and treats any `result.held` as held (no "Approve & advance"); tests (FR-23, FR-32, FR-41, FR-43, FR-44) — dependsOn: T004 — files: frontend/src/app/(app)/intake/_request-answers.tsx, frontend/src/app/(app)/intake/_request-answers.test.tsx, frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx

### Wave 4 — Verification
- [ ] T007 (qa-engineer) Verify 008: full CI gates (ruff, `alembic heads` still `0074_template_sync`, full pytest incl. 007 and exam suites green, typecheck, vitest); map AC-1..AC-61 to tests; `find_blanks` zero on the shipped Consultancy and DPA and on drafts for every fee basis / transfer / sub-processor combination and the example readings; Consultancy and DPA exam copies byte-identical to pre-008; live read-only API checks and refused writes only; a browser-walk section for the orchestrator — dependsOn: T005, T006 — files: specs/008-consultancy-dpa-no-blanks/verification.md

## Task detail notes

- **Hotspots (checked):** `intake/drafting.py` T003 → T005 (never together); `agreement_forms.json` T001
  only (T002/T004 wait for it); `agreement_forms.py` + `intake/service.py` T002 only; `workflows/service.py`
  + `test_workflow_engine_consistency.py` T005 only; `test_sow_drafting.py` T003 only; `types.ts` T004
  only; `endpoints.ts`, `ui.tsx`, `models.py`, `jobs/tasks.py` no task; `_agreement-forms.tsx` and
  `_deliverables.tsx` T004 only (T006 imports from them).
- **`backend/eval/exam/**`** assigned to T003 only (owner approved the frozen copies on 2026-10-15).
- **Paid steps:** none in any task. The QA browser walk files and drafts nothing on the stack without the
  owner's go-ahead; the dev DB has no Consultancy/DPA requests, so held/Processor views are proven by tests.
