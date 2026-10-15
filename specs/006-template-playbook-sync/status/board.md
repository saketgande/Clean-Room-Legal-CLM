# Status Board: Template and Playbook Sync (006-template-playbook-sync)

Run started: 2026-10-15T07:32:38Z
Orchestrator session: /implement — Phase 1 first, pause at T013 for the owner

Legend: ⬜ pending · 🔵 running · ✅ done · 🟥 failed · 🟧 blocked

| Task | Agent | Status | Last update | Detail |
|---|---|---|---|---|
| T001 | db-engineer | ✅ done | 2026-10-15T07:43:06Z | 0074_template_sync: models + migration done; upgrade/downgrade/upgrade OK, ruff clean, tests pass |
| T002 | frontend-dev | ✅ done | 2026-10-15T07:40:10Z | types.ts, endpoints.ts, lib/templates.ts + 36 unit tests done; typecheck errors only in old templates page files (expected); full suite 61 files / 896 tests pass |
| T003 | backend-dev | ✅ done | 2026-10-15T07:36:27Z | schemas.py + test_template_schemas.py; 62 tests pass; ruff check . clean |
| T004 | backend-dev | ✅ done | 2026-10-15T07:50:20Z | docx_fields.py + test_docx_fields.py done: 58 tests pass, ruff clean (whole backend); engine not wired to any route |
| T005 | backend-dev | ✅ done | 2026-10-15T08:11:35Z | strict bare-name field rule in service.check_body and docx_fields; 233 pass in the 4 target files (425 with playbook/exam/intake suites); ruff clean |
| T006 | backend-dev | ✅ done | 2026-10-15T07:54:31Z | follow-up done: getattr dropped (stub has review_playbook_id); risk note names the lost pin; 3 note tests + exact old note kept for unpinned; 69 pass on requested suites; ruff clean |
| T007 | frontend-dev | ✅ done | 2026-10-15T07:56:54Z | _wording-editor.tsx, _check-panel.tsx, _version-history.tsx + 55 component tests (20 editor, 35 check-panel/version-history) pass; typecheck errors only in old page.tsx/page.test.tsx (+2 stale .next/validator); full suite fails only old page.test.tsx (8) |
| T008 | frontend-dev | ✅ done | 2026-10-15T07:43:35Z | _request-overview.tsx keeps template_notice from draft and shows it as a warning MessageBar at the top; 2 tests added (notice / none); vitest 34/34 in file, full suite 61 files / 898 tests pass; typecheck: no errors outside old templates files |
| T009 | backend-dev | ✅ done | 2026-10-15T08:26:42Z | checks.py + tasks.py template_check branch + test_template_check_jobs.py (56) + test_template_status.py (41): 97 pass; 360 pass with related suites; ruff clean; real-session e2e on scratch DB (8-thread single flight OK) |
| T010 | frontend-dev | ✅ done | 2026-10-15T08:17:08Z | page.tsx, _template-list, _template-detail, _check-all, _leave-guard + page.test.tsx (48 tests, mutation-checked) done; typecheck: only the 2 stale .next/validator errors; full suite 63 files / 993 tests pass |
| T011 | backend-dev | ✅ done | 2026-10-15T08:59:16Z | follow-up done: list_checks/get_check moved into checks.py (routes make one call each), check_against_playbook deleted (no leftovers in app/ tests/); ruff clean; 246 pass in 4 template files; restart + smoke OK (login 200, list 200 x7, nda/checks 200) |
| T012 | backend-dev | ✅ done | 2026-10-15T08:51:22Z | drafting.py/routes.py/schemas.py done; test_template_drafting_record.py 26 + test_playbook_pin.py 23 pass (mutation-checked); full suite 3307 passed 1 skipped; ruff clean |
| T013 | qa-engineer | ✅ done | 2026-10-15T09:37:23Z | Phase 1 verified incl. orchestrator browser walk: 45 P1 ACs = 42 Pass / 2 Partial (AC-46, AC-56) / 1 Browser (AC-60) / 0 Fail; 30 later-phase; defects 0 P0/P1, 1 P2 (check panel shows contradictory model summary), 4 P3; gates green (pytest 3307, vitest 993); verification.md section 7 filled |
| T014 | db-engineer | ⬜ pending | — | — |
| T015 | frontend-dev | ⬜ pending | — | — |
| T016 | backend-dev | ⬜ pending | — | — |
| T017 | backend-dev | ⬜ pending | — | — |
| T018 | frontend-dev | ⬜ pending | — | — |
| T019 | backend-dev | ⬜ pending | — | — |
| T020 | frontend-dev | ⬜ pending | — | — |
| T021 | backend-dev | ⬜ pending | — | — |
| T022 | qa-engineer | ⬜ pending | — | — |
| T023 | backend-dev | ⬜ pending | — | — |
| T024 | qa-engineer | ⬜ pending | — | — |
| T025 | backend-dev | ⬜ pending | — | follow-up from T006 (retry wording) |

## Event log

Append-only; newest last. One line per status change:

- 2026-10-15T07:34:06Z orchestrator → wave 1 launched: T001, T002, T003, T004
- 2026-10-15T07:34:40Z T002 frontend-dev → running: reading plan.md contract
- 2026-10-15T07:34:30Z T004 backend-dev → running: starting: read plan/spec, designing docx_fields.py
- 2026-10-15T07:34:31Z T003 backend-dev → running: read plan contract; writing schemas.py
- 2026-10-15T07:36:27Z T003 backend-dev → done: schemas.py + test_template_schemas.py; 62 tests pass; ruff check . clean
- 2026-10-15T07:38:00Z T002 frontend-dev → running: types.ts and endpoints.ts done; writing templates.ts helpers + tests
- 2026-10-15T07:40:10Z T002 frontend-dev → done: types.ts, endpoints.ts, lib/templates.ts + 36 unit tests done; typecheck errors only in old templates page files (expected); full suite 61 files / 896 tests pass
- 2026-10-15T07:41:50Z orchestrator → T002, T003 verified done; launched T007, T008
- 2026-10-15T07:42:30Z T007 frontend-dev → running: starting: reading plan/contract
- 2026-10-15T07:43:06Z T001 db-engineer → done: 0074_template_sync: models + migration done; upgrade/downgrade/upgrade OK, ruff clean, tests pass
- 2026-10-15T07:43:26Z T004 backend-dev → running: docx_fields.py written, lint clean; writing test_docx_fields.py
- 2026-10-15T07:43:35Z T008 frontend-dev → done: _request-overview.tsx keeps template_notice from draft and shows it as a warning MessageBar at the top; 2 tests added (notice / none); vitest 34/34 in file, full suite 61 files / 898 tests pass; typecheck: no errors outside old templates files
- 2026-10-15T07:44:33Z T006 backend-dev → running: starting: reading plan FR-51/D-11 and call sites
- 2026-10-15T07:44:50Z T005 backend-dev → running: starting: reading plan, existing service, schemas
- 2026-10-15T07:44:40Z orchestrator → T001, T008 verified done; launched T005, T006
- 2026-10-15T07:46:31Z T006 backend-dev → running: playbook_for_contract added; auto_review_contract + risk._review_runs swapped; running regression suites then writing test_playbook_pin.py
- 2026-10-15T07:50:00Z T007 frontend-dev → running: read plan/contract; writing _wording-editor.tsx, _check-panel.tsx, _version-history.tsx + tests
- 2026-10-15T07:50:20Z T004 backend-dev → done: docx_fields.py + test_docx_fields.py done: 58 tests pass, ruff clean (whole backend); engine not wired to any route
- 2026-10-15T08:05:00Z T007 frontend-dev → running: three components written and type-clean; writing tests
- 2026-10-15T07:52:27Z T005 backend-dev → running: service.py written and lint clean; writing tests in test_drafting_templates.py
- 2026-10-15T07:52:37Z T006 backend-dev → done: playbook_for_contract + 2 call sites (auto_review_contract, risk._review_runs); test_playbook_pin.py 14 pass; 9 regression suites + test_template_check unmodified, 244 pass; ruff clean on touched files
- 2026-10-15T07:53:32Z T006 backend-dev → running: follow-up: drop getattr (stub gets the column), name the lost pin in the risk note
- 2026-10-15T07:54:31Z T006 backend-dev → done: follow-up done: getattr dropped (stub has review_playbook_id); risk note names the lost pin; 3 note tests + exact old note kept for unpinned; 69 pass on requested suites; ruff clean
- 2026-10-15T07:55:07Z orchestrator → T006 follow-up verified (19 passed); added T025 (retry-review wording) after T021
- 2026-10-15T07:56:54Z T007 frontend-dev → done: _wording-editor.tsx, _check-panel.tsx, _version-history.tsx + 55 component tests (20 editor, 35 check-panel/version-history) pass; typecheck errors only in old page.tsx/page.test.tsx (+2 stale .next/validator); full suite fails only old page.test.tsx (8)
- 2026-10-15T07:58:14Z orchestrator → T007 verified done (55 tests); launched T010
- 2026-10-15T07:58:27Z T010 frontend-dev → running: starting: reading plan/contract
- 2026-10-15T08:04:15Z T010 frontend-dev → running: read plan/contract; writing _leave-guard.ts, _template-list.tsx, _template-detail.tsx, _check-all.tsx, page.tsx, then page.test.tsx
- 2026-10-15T08:07:27Z T010 frontend-dev → running: 5 component files written, typecheck clean for src except old page.test.tsx; writing page.test.tsx
- 2026-10-15T08:07:48Z T005 backend-dev → done: service.py Phase 1 + 81 tests in test_drafting_templates.py (168 with test_template_check/schemas); ruff clean; full suite 3131 passed. Note: list/get 500 until T009 adds checks.py
- 2026-10-15T08:09:26Z orchestrator → T005 verified (226 passed); follow-up sent: strict bare {name} rule on text + Word paths; launched T009
- 2026-10-15T08:09:26Z T005 backend-dev → running: follow-up: strict bare-name rule in service.check_body and docx_fields (text and Word paths together)
- 2026-10-15T08:09:37Z T009 backend-dev → running: starting: reading plan/spec/service
- 2026-10-15T08:11:35Z T005 backend-dev → done: strict bare-name field rule in service.check_body and docx_fields; 233 pass in the 4 target files (425 with playbook/exam/intake suites); ruff clean
- 2026-10-15T08:14:51Z T010 frontend-dev → running: 5 components + page.test.tsx written and passing; mutation-checking the tests, then full gates
- 2026-10-15T08:14:54Z T009 backend-dev → running: read plan/spec/service/jobs; writing checks.py
- 2026-10-15T08:17:08Z T010 frontend-dev → done: page.tsx, _template-list, _template-detail, _check-all, _leave-guard + page.test.tsx (48 tests, mutation-checked) done; typecheck: only the 2 stale .next/validator errors; full suite 63 files / 993 tests pass
- 2026-10-15T08:18:22Z orchestrator → T005 follow-up verified (strict bare {name}, 146 passed); T010 verified (typecheck clean bar 2 stale .next, vitest 63 files / 993 passed)
- 2026-10-15T08:22:02Z T009 backend-dev → running: checks.py + tasks.py branch + test_template_check_jobs.py (54 pass) done; writing test_template_status.py
- 2026-10-15T08:26:42Z T009 backend-dev → done: checks.py + tasks.py template_check branch + test_template_check_jobs.py (56) + test_template_status.py (41): 97 pass; 360 pass with related suites; ruff clean; real-session e2e on scratch DB (8-thread single flight OK)
- 2026-10-15T08:28:24Z orchestrator → T009 verified (206 passed, ruff clean); launched T011, T012
- 2026-10-15T08:28:27Z T012 backend-dev → running: starting: reading plan and drafting.py
- 2026-10-15T08:28:37Z T012 backend-dev → running: reading plan and drafting.py
- 2026-10-15T08:32:59Z T012 backend-dev → running: drafting.py, routes.py, schemas.py written; writing test_template_drafting_record.py and the end-to-end cases
- 2026-10-15T08:39:08Z T012 backend-dev → running: code + tests written: test_template_drafting_record 25 pass, test_playbook_pin 23 pass; running regression suites and mutation checks
- 2026-10-15T08:41:30Z T011 backend-dev → running: routes.py + playbooks hooks + endpoint tests written (43 + 106 pass, ruff clean); related suites, restart + smoke, full suite next
- 2026-10-15T08:51:22Z T012 backend-dev → done: drafting.py/routes.py/schemas.py done; test_template_drafting_record.py 26 + test_playbook_pin.py 23 pass (mutation-checked); full suite 3307 passed 1 skipped; ruff clean
- 2026-10-15T08:56:51Z T011 backend-dev → done: routes.py (13 routes) + playbooks publish/seed hooks; test_template_check 43 + test_drafting_templates 106 pass; 332 pass on related suites; full suite 3307 passed 1 skipped; ruff clean; restart + smoke OK (login 200, list 200 x7)
- 2026-10-15T08:58:11Z T011 backend-dev → running: follow-up: moving the two check reads into checks.py, deleting dead check_against_playbook
- 2026-10-15T08:59:16Z T011 backend-dev → done: follow-up done: list_checks/get_check moved into checks.py (routes make one call each), check_against_playbook deleted (no leftovers in app/ tests/); ruff clean; 246 pass in 4 template files; restart + smoke OK (login 200, list 200 x7, nda/checks 200)
- 2026-10-15T09:18:34Z T013 qa-engineer → running: starting: reading spec/plan, then CI gates
- 2026-10-15T09:34:17Z T013 qa-engineer → done: Phase 1 verified: ruff clean, head 0074_template_sync, pytest 3307 passed 1 skipped, vitest 993, typecheck only 2 stale; 45 P1 ACs = 41 Pass / 1 Partial (AC-46) / 3 Browser (17, 56, 60) / 0 Fail; 30 later-phase; 0 blocking defects, 3 P3; live API + RBAC-enforced 403s checked; verification.md written, section 7 left for the orchestrator
- 2026-10-15T09:37:23Z T013 qa-engineer → done: browser walk folded into verification.md; counts and defects updated
