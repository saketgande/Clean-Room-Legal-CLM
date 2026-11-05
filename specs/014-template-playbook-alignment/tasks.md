# Task Breakdown: Templates and playbooks aligned, so our own paper passes our own playbook

Feature ID: 014-template-playbook-alignment
Plan: ./plan.md (APPROVED)
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- Every task maps to requirement(s) from spec.md; plan.md's FR traceability and AC → evidence tables map each to a component and a test.
- Contract-first: plan.md's Interface freeze and "Template + rule pairs (the frozen content)" — the new template wording, the corrected and added playbook rules, the fill rules, `paper.MINIMUMS`, the decisions Q1–Q19 and the plan's three user decisions — are the contract. Copy wording and rules exactly; no legal judgement in implementation. If a frozen pair still produces a finding, report it; the fix is a pair change recorded in plan.md, never an ad-hoc wording edit.
- One migration, `0077_std_paper` (T014 only), data-only, on the single head `0076_request_summary`.
- Shared hotspots (serialize): `backend/app/intake/drafting.py`, `backend/app/drafting_templates/service.py`, `checks.py`, `backend/tests/test_drafting_templates.py` — T005 then T013. `backend/tests/test_consultancy_dpa_drafting.py` — T011 then T012. `frontend/src/lib/types.ts`, `endpoints.ts` — T004 only. `app/models.py`, `app/jobs/tasks.py`, `src/components/ui.tsx` untouched.
- Specs 009–013 and the purpose-question removal are uncommitted in the working tree; build on them, never revert or reformat them.
- Backend gates: `docker compose exec backend ruff check .`, `alembic upgrade head`, `python -m pytest -q` against `legal_clm_test` (known unrelated failure: `test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had`). New modules need `docker compose restart backend`. Frontend gates from `frontend/`: `npm run typecheck`, `npm run test`; never `npm run lint`. Never run git stash / checkout / reset.
- The paid AI sample (≈ 57 + 7 runs, ≈ $6) runs ONLY in T017 and ONLY after the user says go. No other task makes real AI calls.

## Waves

### Wave 1 — Foundations (parallel, disjoint files)
- [ ] T001 [P] (backend-dev) New pure fill module `app/intake/paper.py`: governing-law normalisation (blank / Indian state / other text → India; NDA keeps its five choices), seat from the entity's registered address or the descriptive fallback, dispute clause per type (NDA and every Consultancy → courts at the seat; MSA/Vendor/SaaS → MCIA arbitration, SIAC Singapore for a foreign provider), signature blocks (our signer from the register, theirs from the request with title after a comma, clean blanks), notice addresses, MSME line, GST clause, fee currency, `MINIMUMS` (INR 5 crore / 1 crore and USD/EUR/GBP equivalents — the only place amounts live), breach window 24h/6h, personal-data variants, NDA term in words, data-breach floor; unit tests (FR-12 to FR-25, FR-45) — dependsOn: — — files: backend/app/intake/paper.py, backend/tests/test_paper_fill.py
- [ ] T002 [P] (backend-dev) Playbook rules: every rule set in `app/playbooks/library.py` exactly as frozen (changed + added rules per playbook; Customer Agreement unchanged and pinned); `evaluate_rules_against_text` accepts one phrase per line via a `_phrases` helper (single-phrase rules behave as before); one must-pass and one must-flag wording per changed rule, incl. risky counterparty clauses (FR-5 to FR-7, FR-26 to FR-44 rule parts, FR-51, FR-52) — dependsOn: — — files: backend/app/playbooks/library.py, backend/app/playbooks/service.py, backend/tests/test_playbook_rules_014.py, backend/tests/test_playbook_library.py
- [ ] T003 [P] (backend-dev) Payment options per type (FR-3): `options_when` on the payment question in `agreement_forms.json` (MSA and Consultancy 45/60; Vendor and SaaS 45/60/90; Selling / Something else unchanged), resolved server-side in `agreement_forms.py` and the `select` branch of `_coerce_field`; a filed request still holding "30 days" stays editable; tests — dependsOn: — — files: backend/app/intake/agreement_forms.json, backend/app/intake/agreement_forms.py, backend/app/intake/service.py, backend/tests/test_payment_options_014.py, backend/tests/test_intake_agreement_form_validation.py
- [ ] T004 [P] (frontend-dev) Frontend: TS interfaces + `draftingTemplatesApi.adoptStandardPlaybook`; `optionsFor` in `form-checks.ts` (same `options_when` resolution as the server, not-offered problem) used by the wizard's `Question`; Templates page "Older than the standard" badge, the two notices and the "Use the standard rules" button (needs `playbook:publish`), `TRIGGER_LABEL.sample`; tests (FR-3, FR-53, FR-54) — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts, frontend/src/lib/form-checks.ts, frontend/src/lib/form-checks.test.ts, frontend/src/app/(app)/intake/_agreement-forms.tsx, frontend/src/app/(app)/intake/_agreement-wizard.test.tsx, frontend/src/app/(app)/templates/_template-list.tsx, frontend/src/app/(app)/templates/_template-detail.tsx, frontend/src/app/(app)/templates/_check-panel.tsx, frontend/src/app/(app)/templates/page.test.tsx, frontend/src/app/(app)/templates/_check-panel.test.tsx

### Wave 2 — Drafting wiring
- [ ] T005 (backend-dev) Wire `paper.py` into `drafting.py` (`template_values` context, Delaware fallback deleted, holds for missing SOW pricing and test-text scope), new placeholders in the `PLACEHOLDERS` catalogue (save validation still requires every placeholder fillable), `_CODED` / markers in `checks.py`; `eval/template_matrix.py` (the answer-combination generator, ~10,500 combinations, fast); tests; existing suite green (FR-2, FR-4, FR-12 to FR-25) — dependsOn: T001, T003 — files: backend/app/intake/drafting.py, backend/app/drafting_templates/service.py, backend/app/drafting_templates/checks.py, backend/eval/template_matrix.py, backend/tests/test_drafting_context_014.py, backend/tests/test_drafting_templates.py

### Wave 3 — One template each ∥ the rollout logic
- [ ] T006 [P] (backend-dev) NDA: `defaults/nda.txt` exactly as frozen; matrix test → 0 phrase-check findings for every combination (FR-26 to FR-30) — dependsOn: T002, T005 — files: backend/app/drafting_templates/defaults/nda.txt, backend/tests/test_alignment_nda.py, backend/tests/test_nda_recital_purpose.py
- [ ] T007 [P] (backend-dev) MSA: `defaults/msa.txt` as frozen; matrix → 0 (FR-31, FR-32) — dependsOn: T002, T005 — files: backend/app/drafting_templates/defaults/msa.txt, backend/tests/test_alignment_msa.py
- [ ] T008 [P] (backend-dev) SOW: `defaults/sow.txt` + `sow.py` (one pricing clause for the chosen model, master agreement named, rates/payment/law follow the MSA, service levels "none" by default); matrix → 0 (FR-33 to FR-36) — dependsOn: T002, T005 — files: backend/app/drafting_templates/defaults/sow.txt, backend/app/intake/sow.py, backend/tests/test_alignment_sow.py, backend/tests/test_sow_drafting.py, backend/tests/test_sow_deliverable_rows.py
- [ ] T009 [P] (backend-dev) Vendor: `defaults/vendor.txt` as frozen (goods / services / both variants); matrix → 0 (FR-37, FR-38) — dependsOn: T002, T005 — files: backend/app/drafting_templates/defaults/vendor.txt, backend/tests/test_alignment_vendor.py
- [ ] T010 [P] (backend-dev) SaaS: `defaults/saas.txt` as frozen (hosting variants, one renewal notice, single data-breach cap with floor); matrix → 0 (FR-39 to FR-41) — dependsOn: T002, T005 — files: backend/app/drafting_templates/defaults/saas.txt, backend/tests/test_alignment_saas.py
- [ ] T011 [P] (backend-dev) Consultancy: `defaults/consultancy.txt` + `consultancy.py` as frozen (courts at the seat for every consultant); matrix → 0 (FR-42) — dependsOn: T002, T005 — files: backend/app/drafting_templates/defaults/consultancy.txt, backend/app/intake/consultancy.py, backend/tests/test_alignment_consultancy.py, backend/tests/test_consultancy_dpa_drafting.py, backend/tests/test_consultancy_dpa_answers.py
- [ ] T013 [P] (backend-dev) Rollout: `drafting_templates/standard.py` (shipped text versions, playbook adopt/apply/revert only when the org's rules still match what the review read, "older than the standard" status), adopt-standard endpoint (`playbook:publish`), schemas, `_summary`, `_MANUAL` / `_results_vary` in `checks.py`; tests (FR-8, FR-9, FR-53 to FR-55) — dependsOn: T002, T005 — files: backend/app/drafting_templates/standard.py, backend/app/drafting_templates/routes.py, backend/app/drafting_templates/schemas.py, backend/app/drafting_templates/service.py, backend/app/drafting_templates/checks.py, backend/tests/test_standard_update_014.py, backend/tests/test_drafting_templates.py, backend/tests/test_template_status.py, backend/tests/test_template_check.py

### Wave 4 — DPA ∥ migration
- [ ] T012 [P] (backend-dev) DPA: `defaults/dpa.txt` + `dpa.py` as frozen (principal agreement named, term and law follow it, DPDP vs EU/UK transfers, children's data, 3× limit incl. fines with floor); matrix → 0 (FR-43, FR-44) — dependsOn: T002, T005, T011 — files: backend/app/drafting_templates/defaults/dpa.txt, backend/app/intake/dpa.py, backend/tests/test_alignment_dpa.py, backend/tests/test_consultancy_dpa_drafting.py
- [ ] T014 [P] (db-engineer) Migration `0077_std_paper` (data-only, no schema change): applies the standard playbook versions and template version notes per plan; no-op with no organisations (CI, test DB); downgrade archives the new playbook versions and repoints to the previous ones; `alembic heads` = one; `make test-db` (FR-53, FR-55) — dependsOn: T013 — files: backend/alembic/versions/0077_std_paper.py

### Wave 5 — AI script ∥ untouched-contracts + full suite
- [ ] T015 [P] (backend-dev) `eval/template_alignment.py`: the deliberate AI sample (19 main combinations × 3 + 7 risky counterparty-paper runs), refuses under `MOCK_CLAUDE`, needs `--yes`, dry plan without it, records each run in the template check history and a dated report file; verify `--help`, dry run and refusal only — NO paid run (FR-1A, AC-1A) — dependsOn: T005, T006, T007, T008, T009, T010, T011, T012, T013 — files: backend/eval/template_alignment.py
- [ ] T016 [P] (backend-dev) Existing contracts untouched: test that contracts, requests, findings and suggested changes made before the change are unchanged (FR-10, FR-11); then full `ruff check .` + `pytest -q` green (only the known unrelated failure) — dependsOn: T006, T007, T008, T009, T010, T011, T012, T013, T014 — files: backend/tests/test_existing_contracts_untouched_014.py

### Wave 6 — Apply and prove (orchestrator + user)
- [ ] T017 (orchestrator) `make migrate` on dev; `docker compose restart backend`; run "Check all" on the Templates page so every template's check is current (the stale SOW result is replaced, FR-8/FR-9); then **only after the user's explicit go-ahead (≈ $6)** run `eval/template_alignment.py --yes`; any finding goes back as a pair fix to the owning template task's agent, then re-run (AC-1, AC-1A) — dependsOn: T004, T014, T015, T016 — files: (none — runs only)

### Wave 7 — QA
- [ ] T018 (qa-engineer) Verify every AC with evidence (tests, matrix counts, AI-sample report, Templates page checks, browser check of the badge/notices/button and the payment options), write verification.md — dependsOn: T017 — files: specs/014-template-playbook-alignment/verification.md

## Workload

| Agent | Tasks |
|---|---|
| backend-dev | T001, T002, T003, T005, T006, T007, T008, T009, T010, T011, T012, T013, T015, T016 |
| frontend-dev | T004 |
| db-engineer | T014 |
| orchestrator (+ your go-ahead) | T017 |
| qa-engineer | T018 |

## Task detail notes

- **T012 waits for T011** because both edit `tests/test_consultancy_dpa_drafting.py`; it is placed in wave 4 beside the migration.
- **T013 runs beside the template tasks** because it touches only files T005 has finished with and no template task touches.
- **T014 after T013** — the migration applies what `standard.py` defines.
- **T017 is the only paid step.** I'll ask you before running it, with the exact cost estimate.
- After T013 lands, the orchestrator regenerates the repo graph.
