# Task Breakdown: AI Evaluation Baseline ("the exam")

Feature ID: 005-ai-eval-baseline
Plan: ./plan.md (must be APPROVED)
Created: 2026-10-06
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

**Money rule for every task:** no task may make a paid call. All tests run with the fake
Anthropic client / `MOCK_*` on, exactly as CI does. The only paid steps happen after
`/implement`, when the owner runs the exam commands and gives a go-ahead.

## Waves

### Wave 1 — Foundation (no dependencies)
- [ ] T001 [P] (db-engineer) Add `AICallLog.eval_run_id` (varchar 36, nullable, indexed) and `AICallLog.batched` (bool, NOT NULL, default false) + migration `0071_ai_call_log_eval_run` on head `0070_ai_call_log_cache_tokens`, with working downgrade (FR-34, FR-41) — dependsOn: — — files: backend/app/core/models.py, backend/alembic/versions/0071_ai_call_log_eval_run.py
- [ ] T002 [P] (backend-dev) Ignore rules before any contract touches disk: restore the lost `backend/docstudio_out/` rule under the dangling comment, add `backend/eval/private/`, `backend/eval/contracts/`, `backend/eval/.work/`, `backend/eval/**/*.xlsx`; mirror the eval paths in `backend/.dockerignore` (FR-36) — dependsOn: — — files: .gitignore, backend/.dockerignore
- [ ] T003 [P] (backend-dev) `eval/exam/content.py`: frozen paths, owner-file allowlist (`owner_file_path` refuses any other name), all pydantic models (documents, planted changes, review/extraction/QA/portfolio items, routing + owner keys), loaders, `content_fingerprint`, `app_code_sha256`; empty `__init__.py`; unit tests (FR-1, FR-2, FR-15, FR-20, FR-27, FR-36) — dependsOn: — — files: backend/eval/exam/__init__.py, backend/eval/exam/content.py, backend/tests/test_exam_content.py
- [ ] T004 [P] (backend-dev) Port the 7 branch result files unchanged into `results/superseded/` with a README labelling them "earlier, superseded" and naming the three known defects (FR-29) — dependsOn: — — files: backend/eval/results/superseded/*.json, backend/eval/results/superseded/README.md
- [ ] T005 [P] (frontend-dev) `AiUsageTraffic` type + `AiUsageSummary.traffic`; `aiUsageApi.summary(days, traffic)`; Real usage / Exam traffic toggle on the AI Usage page (default Real); typecheck + vitest (FR-34) — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts, frontend/src/app/(app)/ai-usage/page.tsx

### Wave 2 — Platform hooks + pure exam modules
- [ ] T006 [P] (backend-dev) `integrations/claude.py`: `exam_meter` hook (None in the app; mock mode returns before it), the four batch methods (`create_batch`, `batch_status`, `batch_results`, cancel), `complete_json` for claude-sonnet-5-5 via `output_config.format` (no temperature, no tools, no tool_choice); `ai/gateway/ledger.py`: `AICallContext.eval_run_id` written by `record_call`; shared fake Anthropic client; tests prove the app path is unchanged when the hook is None (FR-16, FR-32, FR-34, FR-41) — dependsOn: T001 — files: backend/app/integrations/claude.py, backend/app/ai/gateway/ledger.py, backend/tests/exam_fake_anthropic.py, backend/tests/test_exam_claude_client.py
- [ ] T007 [P] (backend-dev) `analytics/routes.py`: `traffic=real|exam` filter on both queries (default real, so real figures exclude exam rows), model-aware price table (sonnet-4-6 $3/$15, sonnet-5-5 $2/$10, haiku-4-5 $1/$5, opus-5-5 $4/$20; cache 0.1×/1.25×), public `token_cost(..., batched=)` halving batch rows, group by `batched`; org filter + permission unchanged; tests (FR-14, FR-34, FR-41) — dependsOn: T001 — files: backend/app/analytics/routes.py, backend/tests/test_analytics_ai_usage_traffic.py
- [ ] T008 [P] (backend-dev) `eval/exam/build.py` part 1 — public sets: port the branch downloader/parsers (CUAD, ContractNLI, LegalBench-RAG) with seeded selection: 40 long CUAD contracts preferring labelled passages past 25k (review checklist + extraction keys), 10 ContractNLI NDAs, 100 LegalBench-RAG CUAD questions on those 40; record dataset name/version/licence/sha256; write `content/datasets.json` + public items; extend content tests (FR-2, FR-4) — dependsOn: T002, T003 — files: backend/eval/exam/build.py, backend/eval/exam/content/datasets.json, backend/eval/exam/content/items/extraction.json, backend/eval/exam/content/items/qa.json, backend/eval/exam/content/playbooks/nda_checklist.json, backend/eval/exam/content/playbooks/cuad_checklist.json, backend/tests/test_exam_content.py
- [ ] T009 [P] (backend-dev) `eval/exam/env.py`: `ExamSettings` (private `eval/private/exam.env`), `isolate()` (refuse if the live engine exists or env isn't local/dev/test; dedicated key required and ≠ real key; refuse mock/Voyage/Cohere; repoint settings to `legal_clm_eval`, in-memory Celery, mail/DocuSign mocks, RBAC on, daily cap off), `authenticate_owner` (owner email + AEGIS password re-entry, refusals audited), `audit`, `exam_lock` (FR-31, FR-34, FR-35, FR-37, FR-38, FR-39) — dependsOn: T003 — files: backend/eval/exam/env.py
- [ ] T010 [P] (backend-dev) `eval/exam/sheets.py`: XLSX writer/loader (openpyxl, already installed) for the facts sheet and the routing sheet; protected columns; all-or-nothing load (`SheetRejected` with every problem listed); unmarked rows excluded and counted; only owner-confirmed rows enter a key; tests (FR-3, FR-17, FR-18, FR-19, FR-21) — dependsOn: T003 — files: backend/eval/exam/sheets.py, backend/tests/test_exam_sheets.py
- [ ] T011 [P] (backend-dev) `eval/exam/score.py` (pure): review precision/recall/F1 + missed high/critical by name; per-field extraction with normalisation (dates, amounts, company suffixes, parties as a list) split before/after 25k; Q&A search-found vs answer-cited split before/after 25k; portfolio found/missed/wrongly-included; routing accuracy; coverage, truncation, quote validity, consistency, cost/time; `score_pair` noise bands; `verdict` (main measure beats its band AND no other quality measure worsens beyond its band; truncation lower-is-better; cost/time/coverage/consistency report-only; refuse across content or delivery mode); tests (FR-10..FR-13, FR-22..FR-28, FR-41) — dependsOn: T003 — files: backend/eval/exam/score.py, backend/tests/test_exam_score.py, backend/tests/test_exam_compare.py

### Wave 3 — Constructed contracts, paid-run machinery, report
- [ ] T012 [P] (backend-dev) `eval/exam/build.py` part 2 — constructed sets: Common Paper CSA/DPA (18 incl. 3 controls) + AEGIS-template Indian variants (24 incl. 6 controls: Indian law, INR, Indian entities) + 4 long docs with changes past 25k + 6 scanned twins (pages as images, DocuSign-style stamp as the only text layer, sharing the twin's answer record); severity copied from the platform playbook library rule each change breaks (rule with no severity → no change planted, build says so); 40-contract portfolio (28 reused + 12) with 8 questions and exact answer lists; `cp_msa`/`cp_dpa` playbooks with the frozen library mapping; `manifest.json`, `documents.json`, `sample.json`; extend content tests (FR-5, FR-6, FR-7, FR-8, FR-9, FR-30) — dependsOn: T008 — files: backend/eval/exam/build.py, backend/eval/exam/content/items/extraction.json, backend/eval/exam/content/documents.json, backend/eval/exam/content/manifest.json, backend/eval/exam/content/sample.json, backend/eval/exam/content/items/review.json, backend/eval/exam/content/items/portfolio.json, backend/eval/exam/content/playbooks/cp_msa.json, backend/eval/exam/content/playbooks/cp_dpa.json, backend/tests/test_exam_content.py
- [ ] T013 [P] (backend-dev) `eval/exam/runner.py` part 1 — paid-run machinery: `Estimate` (calls + USD per call type, OCR separately, "set ~$25 to guarantee completion; charged only for what's used"), `Meter` (worst-case admission so spend never passes the ceiling, settle at actual, batch ×0.5), `custom_id`, `worst_cost`, `approve_paid_run` (interactive: type the run id + ceiling, single-use, audited go/no-go), `finish_paid_run` (immutable `eval.run_finished` record), `mirror_values` / `stamp_and_mirror` (copy exam call rows to the live ledger with `eval_run_id` + `batched`, cost/token columns only, text and exam ids blanked), `reconcile_crashed_runs`; tests (FR-31, FR-32, FR-33, FR-34, FR-39, FR-40, FR-41) — dependsOn: T006, T007, T009 — files: backend/eval/exam/runner.py, backend/tests/test_exam_gate.py, backend/tests/test_exam_ledger.py
- [ ] T014 [P] (backend-dev) `eval/exam/report.py`: private `detail.md` (every measure, failed calls, invalid quotes), public numbers-only report in `reports/` with n, plain-language line and noise band per measure, cost/time beside scores, "contains errors"/"incomplete" flags, sample label; `check_pair` (same versions, both full + complete, ≤1% failed calls, same delivery mode); `reports/README.md` (FR-26, FR-27, FR-30, FR-36, FR-40, FR-41) — dependsOn: T011 — files: backend/eval/exam/report.py, backend/eval/exam/reports/README.md

### Wave 3b — Follow-ups from review (added 2026-10-07 by the orchestrator, owner approved "fix and continue")
- [ ] T021 [P] (backend-dev) Hardening of runner/report/score found in review: `stamp_and_mirror` refuses unless its session's database name starts with `legal_clm_eval` (can never tag real usage as exam traffic); `approve_paid_run` validates `kind`; `score_pair` refuses the same run twice; `verdict` derives the target from the target_area recorded on the change runs (refuses a mismatch or a missing record) instead of trusting an argument; amount normalisation drops trailing zeros; `write_detail` writes only under the private runs folder; remove `write_public_report(replace=True)` so a published baseline report is never overwritten; FR-26 "missed high/critical" section gets n + a plain-language line; committed tests for report.py (check_pair, FR-36 guard) (FR-26, FR-27, FR-28, FR-34, FR-36, FR-39) — dependsOn: T011, T013, T014 — files: backend/eval/exam/runner.py, backend/eval/exam/report.py, backend/eval/exam/score.py, backend/tests/test_exam_ledger.py, backend/tests/test_exam_compare.py, backend/tests/test_exam_score.py, backend/tests/test_exam_report.py
- [ ] T022 [P] (backend-dev) Answer-key integrity + privacy-audit fixes found in review: owner.py writes `eval.real_contract_sent` BEFORE each OCR/pre-fill send (and a failure/interrupt row after), pre-fill schema requires all six facts (a missing field is a failed pre-fill, never a silent "not stated"), ocr/prefill run records carry the pre-fill model + versions and count only calls actually made; routing sheets are bound to the exact drafted request texts (a stale sheet from an earlier draft is rejected); sheets.py disambiguates workflow names that differ only by case/spacing; content.py rejects duplicate routing item ids and keys pointing at missing requests/documents; committed tests/test_exam_routing.py (adopt the T016 checks) (FR-3, FR-16, FR-19, FR-21, FR-35, FR-39) — dependsOn: T010, T015, T016 — files: backend/eval/exam/owner.py, backend/eval/exam/routing.py, backend/eval/exam/sheets.py, backend/eval/exam/content.py, backend/tests/test_exam_gate.py, backend/tests/test_exam_sheets.py, backend/tests/test_exam_routing.py
- [ ] T023 [P] (backend-dev) Committed tests for `eval/exam/evaldb.py` (T017's checks were scratch-only and are gone): `reset` refuses any database name not starting `legal_clm_eval`; `migrate` brings a scratch exam database to head; `bootstrap` creates the exam org + runner/portfolio users; `load_playbooks`/`load_workflows` load content; `ingest_document` runs in-process with no Celery dispatch, no stage triggers, no notifications/emails/workflow runs, and writes nothing to the live database; each test uses its own throwaway `legal_clm_eval_test_*` database and drops it (FR-5, FR-24, FR-37, FR-38) — dependsOn: T017 — files: backend/tests/test_exam_evaldb.py
- T012 retry: file list widened to include `backend/eval/exam/content/items/extraction.json` (omitted from the original task list by the orchestrator), so the 58 constructed extraction keys are committed and sample extraction covers constructed docs.

### Wave 4 — Owner commands + routing
- [ ] T015 [P] (backend-dev) `eval/exam/owner.py`: `FrozenOCR` (OCR once via the existing provider, cache by file sha256, frozen text + fingerprint; stale key when file/text changes), `verify_quote`, `read_as_app` (the app's own read path, for the 2 stamp-only scans "as read today"), `ocr` command (estimate in files/pages/credits, `REDUCTO_USD_PER_CREDIT` optional with warning, go-ahead + ceiling, audit each real-contract send), `prefill` command (claude-sonnet-5-5 `complete_json`, refuse if it equals the model under test, quote check → "no supporting sentence found", facts sheet), `load-facts`; extend gate tests (FR-3, FR-15, FR-16, FR-17, FR-20, FR-23, FR-35) — dependsOn: T006, T010, T013 — files: backend/eval/exam/owner.py, backend/tests/test_exam_gate.py
- [ ] T016 [P] (backend-dev) `eval/exam/routing.py`: `snapshot_workflows(live)` (today's enabled workflows), `draft-routing` paid command (~50 free-text requests spread across the workflows, drafting model recorded, not the model under test, coverage per workflow reported), routing sheet out, `load-routing` (owner's picks become the key, stored against the snapshot; stale when the workflow list changes) (FR-3, FR-21, FR-25) — dependsOn: T010, T013 — files: backend/eval/exam/routing.py

### Wave 5 — Throwaway database
- [ ] T017 (backend-dev) `eval/exam/evaldb.py`: `reset` (only names starting `legal_clm_eval`), `migrate` (alembic upgrade head on the exam DB), `bootstrap` (exam org + runner and portfolio users), `load_playbooks`, `load_workflows` (from the routing snapshot), `ingest_document` (the upload pipeline's own read + persist steps and job handler functions in-process — no Celery dispatch, no stage triggers, no notifications/emails/workflow runs) (FR-5, FR-24, FR-37, FR-38) — dependsOn: T009, T012, T015, T016, T022 — files: backend/eval/exam/evaldb.py

### Wave 6 — The exam run
- [ ] T018 (backend-dev) `eval/exam/runner.py` part 2: `execute_exam_run` (sample/full; rebuild exam DB; phase 1 = labelling/metadata/routing triage, brain ingestion between, phase 2 = review/Q&A/portfolio; capture → one batch per phase → replay by `custom_id`; owner-contract units synchronous while batches process; one retry batch for transient errors; unreturned/changed = failed; ceiling via `Meter`; results + detail), `resume_exam_run` / `abandon_exam_run` (batch ids in the private run folder); Q&A and portfolio through the app's own `ask_contract_brain` over the exam DB only; batch tests on the fake client (FR-1, FR-10, FR-11, FR-24, FR-25, FR-30, FR-32, FR-40, FR-41) — dependsOn: T011, T013, T014, T015, T016, T017, T021, T022 — files: backend/eval/exam/runner.py, backend/tests/test_exam_batch.py

### Wave 7 — CLI, isolation proof, operator guide
- [ ] T019 (backend-dev) `eval/exam/__main__.py` (argparse: `build`, `ocr`, `prefill`, `load-facts`, `draft-routing`, `load-routing`, `run --mode sample|full [--change --target]`, `run --resume/--abandon`, `baseline`, `compare`, `status`; `isolate()` before anything imports the database), `README.md` operator guide (setup of `eval/private/exam.env`, step-by-step for the owner, costs), subprocess isolation test proving no live-DB contract rows, no dispatch, no notifications, exam rows only in `legal_clm_eval`, refusal without the dedicated key (FR-27, FR-28, FR-30, FR-31, FR-34, FR-37, FR-38) — dependsOn: T018 — files: backend/eval/exam/__main__.py, backend/eval/exam/README.md, backend/tests/test_exam_isolation.py, backend/tests/exam_isolation_driver.py

### Wave 8 — Verification
- [ ] T020 (qa-engineer) Verify AC-1..AC-41 against the code and the mocked test suite (no paid calls), run `ruff check .`, `alembic upgrade head`, `pytest -q`, frontend typecheck + vitest; write verification.md — dependsOn: T005, T019, T023 — files: specs/005-ai-eval-baseline/verification.md

## Per-agent workload

| Agent | Tasks |
|---|---|
| db-engineer | T001 |
| backend-dev | T002, T003, T004, T006, T007, T008, T009, T010, T011, T012, T013, T014, T015, T016, T017, T018, T019, T021, T022, T023 |
| frontend-dev | T005 |
| qa-engineer | T020 |

## Parallel-safety check

- Shared files are serialized by `dependsOn`: `build.py` (T008 → T012), `runner.py`
  (T013 → T018), `test_exam_content.py` (T003 → T008 → T012), `test_exam_gate.py`
  (T013 → T015). No two `[P]` tasks in the same wave share a file.
- `claude.py` + `ledger.py` in one task (T006); `analytics/routes.py` in one task (T007);
  `types.ts` + `endpoints.ts` in one task (T005); `core/models.py` only in T001.
- One migration (T001); nothing else touches `backend/alembic/versions/`.
- Untouched by design: `backend/app/models.py`, `backend/app/jobs/tasks.py`,
  `backend/app/core/config.py`, `frontend/src/components/ui.tsx`, `.github/workflows/*`.

## Task detail notes

- T002 must land before T008 downloads anything into `backend/eval/contracts/` and before
  any owner contract is copied under `backend/eval/private/` (FR-36).
- T008/T012 download public datasets (free, ~400 MB) into the git-ignored raw folder; only
  the generated `content/**` JSON is committed. `build` makes no AI call.
- T013 `Estimate` text must say plainly: expected ≈ $9.75 for a full run; a ceiling of
  about $25 guarantees completion; the owner is charged only for what is used (hard-limit
  decision, plan "Open questions").
- T015/T016 commands are paid; tests drive them with the fake client only.
- Interfaces are frozen in plan.md "Python interfaces between exam modules"; a task that
  needs a change to another module's interface stops and reports `blocked`.
