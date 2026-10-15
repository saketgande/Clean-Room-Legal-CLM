# Status Board: SOW drafts filled from the request (007-sow-filled-from-request)

Run started: 2026-10-15T11:00:37Z
Orchestrator session: /implement 007

Legend: ⬜ pending · 🔵 running · ✅ done · 🟥 failed · 🟧 blocked

| Task | Agent | Status | Last update | Detail |
|---|---|---|---|---|
| T001 | backend-dev | ✅ done | 2026-10-15T11:50:00Z | M12 "short of 100%" regenerated; 146 rows / 20 field / 54 blank cases, 0 reference mismatches |
| T002 | backend-dev | ✅ done | 2026-10-15T12:15:00Z | 79 new tests + assigned suites green, test_docx_fields[sow] fixed (141 passed with test_sow_drafting), ruff clean |
| T003 | backend-dev | ✅ done | 2026-10-15T11:29:13Z | filing/update rules for the rows kind; 146+143 shared cases, FR-32 exemption, audit intake.deliverables_changed; ruff clean |
| T004 | frontend-dev | ✅ done | 2026-10-15T13:30:00Z | form-checks mirror + shared cases (220 pass), RowsTable/RequestDeliverables, wizard table; typecheck + vitest 1291 green |
| T005 | backend-dev | ✅ done | 2026-10-15T13:05:00Z | held clm_draft step (waiting_human + result.held), resolver re-arm, manual-complete 409; 8 new tests, full suite 3807 passed, ruff clean |
| T006 | frontend-dev | ✅ done | 2026-10-15T13:55:00Z | RequestDeliverables mounted on the request page; held draft step shows its reason and loses Approve & advance; 10 new tests, typecheck clean (2 stale .next errors), vitest 1302 green |
| T007 | qa-engineer | ✅ done | 2026-10-15T12:11:50Z | 48 ACs: 45 Pass / 2 Partial (AC-31 real check not run, AC-43 wall clause) / 1 Browser (AC-20 keyboard + screen reader not walked) / 0 Fail; gates green (pytest 3807 + 1 skipped, vitest 1302); orchestrator browser walk 2026-10-15 recorded in section 9, nothing wrong in 007; 0 P0-P2, 8 P3 (P3-8 pre-existing) |

## Event log

Append-only; newest last. One line per status change:

- 2026-10-15T11:01:21Z orchestrator → wave 1 launched: T001, T002
- 2026-10-15T11:05:00Z backend-dev → T002 running: froze eval/exam/sow_2026-10-15.txt (cmp ok), reading plan/spec/code
- 2026-10-15T11:05:30Z backend-dev → T001 running: read plan/spec, editing agreement_forms.json then the shared cases file
- 2026-10-15T11:25:00Z backend-dev → T002 running: source files written, tests next
- 2026-10-15T11:35:00Z backend-dev → T001 done: agreement_forms.json (sow) + tests/fixtures/deliverable_rows_cases.json (146 rows, 20 fields, 54 blanks); ruff green
- 2026-10-15T11:45:00Z backend-dev → T001 running: M12 correction ('short of 100%') being regenerated in the cases file
- 2026-10-15T11:50:00Z backend-dev → T001 done: M12 'short of 100%' regenerated (6 strings), 0 reference mismatches, node parse ok
- 2026-10-15T11:12:38Z orchestrator → T001 verified (cases file valid, M12 corrected to 'short of'); launched T003, T004; T005 waits for T002
- 2026-10-15T12:00:00Z frontend-dev → T004 running: reading plan/spec/code
- 2026-10-15T11:12:44Z backend-dev → T003 running: reading plan/spec/intake code
- 2026-10-15T11:40:00Z backend-dev → T002 done. FINDING for orchestrator: tests/test_docx_fields.py::test_every_shipped_template_fills_in_word_exactly_as_it_does_as_text[sow] now fails (docx_fields.fill collapses line breaks in values; the SOW list fields are joined by blank lines). Not in T002's files. Fix: build the expected text from SAMPLE_VALUES with re.sub(r"[\r\n\t]+", " ", v) (verified to pass), or Phase 3 paragraph splitting.
- 2026-10-15T12:10:00Z backend-dev → T002 running: follow-up edit to tests/test_docx_fields.py (coordinator-cleared)
- 2026-10-15T11:27:53Z orchestrator → T002 verified (sow.txt 0 brackets, frozen exam copy identical, exam green); follow-up: docx parity test newline-normalised + Phase 3 requirement logged on 006 T023; launched T005
- 2026-10-15T12:15:00Z backend-dev → T002 done: tests/test_docx_fields.py parity test now collapses line breaks in expected text; 141 passed (docx_fields + sow_drafting), ruff clean
- 2026-10-15T11:29:13Z backend-dev → T003 done: agreement_forms.clean_rows/answer_text, _validate_field_values(stored=), intake.deliverables_changed (audit + timeline), triage/flow answer_text; test_sow_deliverable_rows (388), test_sow_request_updates (23); ruff clean
- 2026-10-15T13:30:00Z frontend-dev → T004 done: types.ts, form-checks.ts(+test), _deliverables.tsx(+test), _agreement-forms.tsx, agreement-forms.test.ts, _agreement-wizard.test.tsx; typecheck clean (2 stale .next errors), vitest 64 files / 1291 tests
- 2026-10-15T12:35:00Z backend-dev → T005 running: workflows/service.py edited, writing tests in test_workflow_engine_consistency.py
- 2026-10-15T13:05:00Z backend-dev → T005 done: workflows/service.py + test_workflow_engine_consistency.py (25 tests); suites 254 passed, full suite 3807 passed / 1 skipped; ruff clean
- 2026-10-15T11:40:25Z orchestrator → T003 (435), T004 (vitest 1291), T005 (58 + full suite) verified; launched T006
- 2026-10-15T13:55:00Z frontend-dev → T006 done: _request-overview.tsx + _request-overview.test.tsx (45 tests); typecheck clean (2 stale .next errors), vitest 64 files / 1302 tests
- 2026-10-15T12:01:30Z qa-engineer → T007 running: ruff/heads/typecheck/vitest green, live read-only API + refused writes (7 x 422, no row created), find_blanks + money checks done; full pytest in progress
- 2026-10-15T12:10:28Z qa-engineer → T007 done: specs/007-sow-filled-from-request/verification.md written (gates, AC map, blanks, money, live API, defects, browser-walk checklist)
- 2026-10-15T12:11:50Z qa-engineer → T007 done: section 9 filled with the orchestrator's 2026-10-15 browser walk; AC-10/12/14/21/26/28/30/34/44/45 rows noted; P3-8 (approval-preview not debounced, pre-existing) added
