# Status Board: AI Evaluation Baseline ("the exam") (005-ai-eval-baseline)

Run started: 2026-10-06T17:18:35Z
Orchestrator session: /implement via Workflow — 8 waves, each task built by its owning agent then independently reviewed

Legend: ⬜ pending · 🔵 running · ✅ done · 🟥 failed · 🟧 blocked

| Task | Agent | Status | Last update | Detail |
|---|---|---|---|---|
| T001 | db-engineer | ✅ done | 2026-10-06T17:22:26Z | 0071_ai_call_log_eval_run applied, single head, ruff clean |
| T002 | backend-dev | ✅ done | 2026-10-06T17:22:00Z | .gitignore + backend/.dockerignore eval/docstudio_out rules; verified with git check-ignore |
| T003 | backend-dev | ✅ done | 2026-10-06T17:26:36Z | content.py + empty __init__.py + test_exam_content.py done; pytest tests/test_exam_content.py 50 passed; ruff clean |
| T004 | backend-dev | ✅ done | 2026-10-06T17:22:01Z | 7 result files + README in results/superseded/ |
| T005 | frontend-dev | ✅ done | 2026-10-06T17:22:01Z | toggle + types + endpoint; vitest 69/69; tsc clean in src/ |
| T006 | backend-dev | ✅ done | 2026-10-06T17:28:08Z | exam_meter hook, complete_json, 4 batch methods, eval_run_id, FakeAnthropic; 25 tests pass, ruff clean |
| T007 | backend-dev | ✅ done | 2026-10-06T17:26:28Z | traffic filter + model-aware token_cost(batched=); 28 tests pass, ruff clean |
| T008 | backend-dev | ✅ done | 2026-10-06T18:00:50Z | review fixes: notice_period key left out where a termination/renewal clause exists (21 of 29 None keys), 'Party B' role labels dropped, bad/empty archive no longer stuck; extraction.json regenerated (275 -> 254 fields); 114 passed, ruff clean |
| T009 | backend-dev | ✅ done | 2026-10-06T17:36:32Z | env.py: isolate/authenticate_owner/audit/exam_lock; ruff clean; 49 scratch checks pass |
| T010 | backend-dev | ✅ done | 2026-10-06T17:36:44Z | sheets.py + test_exam_sheets.py done; pytest tests/test_exam_sheets.py 48 passed; ruff clean |
| T011 | backend-dev | ✅ done | 2026-10-06T17:56:47Z | review fixes: term parse (words+digits), pair private nesting, failure reason code, delta direction-adjusted, content-fingerprint checks; 88 passed, ruff clean |
| T012 | backend-dev | ✅ done | 2026-10-06T19:20:06Z | retry: extraction.json = 40 CUAD + 58 constructed keys, sample extraction covers constructed docs, one full-build report; 170 passed, ruff clean |
| T013 | backend-dev | ✅ done | 2026-10-06T17:55:55Z | runner.py part 1 + test_exam_gate/test_exam_ledger: 80 passed, ruff clean |
| T014 | backend-dev | ✅ done | 2026-10-06T18:13:52Z | report.py + reports/README.md; check_pair/write_detail/write_public_report/build_report; ruff clean; no test file in task list (scratch checks 13 pass) |
| T015 | backend-dev | ✅ done | 2026-10-06T18:21:20Z | owner.py (FrozenOCR, read_as_app, owner-import, ocr, prefill, load-facts, reconfirm sheet) + gate tests; 90 passed, ruff clean |
| T016 | backend-dev | ✅ done | 2026-10-06T18:10:36Z | routing.py: snapshot/draft_routing/load_routing/routing_fresh; ruff clean; 16 scratch tests pass (no test file assigned) |
| T017 | backend-dev | ✅ done | 2026-10-06T19:52:00Z | evaldb.py (reset/migrate/bootstrap/load_playbooks/load_workflows/ingest_document); 77 scratch checks on a real scratch db pass, all test_exam_* 531 passed, ruff clean; no repo test file in task list |
| T018 | backend-dev | ✅ done | 2026-10-07T04:45:00Z | runner part 2 + reviewer fixes: resume ceiling hold, refused-row race, abandon spend, free-stage capture, FR-11 test; test_exam_batch 53 passed, all test_exam_* 626 passed, ruff clean |
| T019 | backend-dev | ✅ done | 2026-10-07T07:34:04Z | review fixes: driver stubs the local reranker (cold-cache hermetic), baseline set removes its report if the designation audit fails, 2 new tests (status unfinished/waiting lines, no orphan baseline report); test_exam_isolation 43 passed, all test_exam_* + analytics 697 passed, ruff clean |
| T021 | backend-dev | ✅ done | 2026-10-06T19:22:40Z | runner/report/score hardening + tests/test_exam_report.py; 485 exam tests pass, ruff clean |
| T022 | backend-dev | ✅ done | 2026-10-06T19:30:50Z | owner.py audit-before-send + strict 6-fact pre-fill + run versions; routing sheets bound to request texts; sheets label disambiguation; content.py key/id consistency; test_exam_routing.py; pytest gate/sheets/routing 184 passed (all test_exam_* 509 passed); ruff clean |
| T023 | backend-dev | ✅ done | 2026-10-07T03:03:33Z | tests/test_exam_evaldb.py: 42 passed (all test_exam_* 573 passed), ruff clean; throwaway legal_clm_eval_test_* dbs, none left behind |
| T020 | qa-engineer | ✅ done | 2026-10-07T07:59:01Z | 41/41 AC PASS (live paid proof NOT-RUN by rule); ruff clean, one head 0071, pytest 1945 passed, vitest 69, typecheck 0 errors outside .next; 0 blocking defects, 2 low (D-1, D-2) |

## Event log

Append-only; newest last. One line per status change:

- 2026-10-06T17:18:35Z orchestrator → run started: 20 tasks pending
- 2026-10-06T17:20:59Z backend-dev → T003 running
- 2026-10-06T17:21:05Z db-engineer T001 → running
- 2026-10-06T17:22:00Z backend-dev → T002 done: ignore rules for eval/private, contracts, .work, xlsx (+ restored docstudio_out) in .gitignore and .dockerignore
- 2026-10-06T17:22:01Z backend-dev → T004 done: 7 branch result files ported unchanged + superseded README
- 2026-10-06T17:22:01Z frontend-dev → T005 done: Real usage / Exam traffic toggle, AiUsageTraffic type, aiUsageApi.summary(days, traffic)
- 2026-10-06T17:22:26Z db-engineer T001 → done (migration 0071_ai_call_log_eval_run)
- 2026-10-06T17:23:11Z backend-dev → T006 running
- 2026-10-06T17:23:46Z backend-dev T007 → running
- 2026-10-06T17:26:28Z backend-dev T007 → done: analytics traffic=real|exam, model-aware rates, public token_cost(batched=); _cost kept as alias for tests/test_ai_gateway.py
- 2026-10-06T17:26:36Z backend-dev → T003 done (content.py + empty __init__.py + test_exam_content.py done; pytest tests/test_exam_content.py 50 passed; ruff clean)
- 2026-10-06T17:28:08Z backend-dev → T006 done: claude.py hook/complete_json/batch methods + ledger eval_run_id + tests/exam_fake_anthropic.py; test_exam_claude_client 25 passed
- 2026-10-06T17:29:49Z backend-dev T008 → running
- 2026-10-06T17:31:41Z backend-dev → T010 running
- 2026-10-06T17:31:40Z backend-dev T009 → running
- 2026-10-06T17:36:32Z backend-dev T009 → done: eval/exam/env.py (ExamSettings, isolate, authenticate_owner, audit, exam_lock)
- 2026-10-06T17:36:44Z backend-dev → T010 done: eval/exam/sheets.py (facts + routing XLSX writer/loader) + tests/test_exam_sheets.py; 48 passed, ruff clean
- 2026-10-06T17:43:15Z backend-dev T013 → running
- 2026-10-06T17:47:27Z backend-dev T011 → done: eval/exam/score.py (score_run, score_pair, verdict, load_texts) + test_exam_score.py + test_exam_compare.py; 71 passed, ruff clean
- 2026-10-06T17:51:30Z backend-dev T008 → done: build.py part 1 (download, CUAD 40/NDA 10/QA 100, registry, playbooks) + datasets/extraction/qa/2 playbooks committed; pytest tests/test_exam_content.py 106 passed; ruff clean
- 2026-10-06T17:55:55Z backend-dev T013 → done: eval/exam/runner.py part 1 (paid-run machinery) + tests/test_exam_gate.py + tests/test_exam_ledger.py; 80 passed, ruff clean
- 2026-10-06T17:56:47Z backend-dev T011 → done (review fixes): review fixes: term parse (words+digits), pair private nesting, failure reason code, delta direction-adjusted, content-fingerprint checks; 88 passed, ruff clean
- 2026-10-06T18:00:50Z backend-dev T008 → done (review fixes): review fixes: notice_period key left out where a termination/renewal clause exists (21 of 29 None keys), 'Party B' role labels dropped, bad/empty archive no longer stuck; extraction.json regenerated (275 -> 254 fields); 114 passed, ruff clean
- 2026-10-06T18:07:42Z backend-dev T014 → running
- 2026-10-06T18:10:36Z backend-dev T016 → done: eval/exam/routing.py (snapshot_workflows, routing_fresh, draft_routing, load_routing); ruff clean; 16 scratch tests pass (no repo test file assigned)
- 2026-10-06T18:13:52Z backend-dev T014 → done: eval/exam/report.py (detail.md, public report + owner-text guard, check_pair) + reports/README.md; ruff clean
- 2026-10-06T18:20:00Z backend-dev T015 → running
- 2026-10-06T18:21:20Z backend-dev T015 → done: eval/exam/owner.py + owner-command tests in tests/test_exam_gate.py; pytest tests/test_exam_gate.py 90 passed; ruff clean
- 2026-10-06T18:41:17Z backend-dev T012 → done: eval/exam/build.py part 2 (constructed sets) + committed content (documents, review, portfolio, sample, manifest, cp_msa/cp_dpa) + tests; pytest tests/test_exam_content.py 164 passed; ruff clean
- 2026-10-06T18:55:15Z backend-dev T012 → done (review fixes): renewal plant opt-in again + wider portfolio proof + sample/run tolerate missing public sets; test_exam_content 167 passed, ruff clean; constructed extraction keys still await an owner decision on items/extraction.json
- 2026-10-06T19:12:59Z orchestrator → T012 failed final review (task-list omission of items/extraction.json, not a builder error); T017–T020 blocked
- 2026-10-06T19:12:59Z orchestrator → owner chose "fix and continue": T012 retry with widened files; follow-ups T021 + T022 added; T017 now also waits on T022, T018 on T021 + T022
- 2026-10-06T19:15:18Z backend-dev T022 → running
- 2026-10-06T19:20:06Z backend-dev → T012 done (retry): items/extraction.json written (public + 58 constructed), sample extraction 6 items, run_constructed(with_public=True) full-build report; test_exam_content 170 passed, ruff clean
- 2026-10-06T19:22:40Z backend-dev T021 → done: stamp guard, kind check, same-run refusal, verdict target from runs, amount normalisation, write_detail/replace guards, FR-26 n + line, test_exam_report.py
- 2026-10-06T19:30:50Z backend-dev T022 → done: answer-key integrity + privacy-audit fixes; test_exam_gate/sheets/routing 184 passed, all test_exam_* 509 passed; ruff clean
- 2026-10-06T19:52:00Z backend-dev T017 → done: eval/exam/evaldb.py (throwaway exam database: reset, migrate, bootstrap, load_playbooks, load_workflows, ingest_document)
- 2026-10-06T20:11:31Z backend-dev T018 → running
- 2026-10-06T20:13:13Z orchestrator → PAUSED by owner. Done since last pass: T012 (retry), T021, T022, T017 (all reviewed). T018 stopped before any edits; T019, T020 not started.
- 2026-10-07T02:40:35Z orchestrator → RESUMED by owner; added T023 (committed evaldb tests, T017's scratch checks were lost); T020 now also waits on T023
- 2026-10-07T02:42:42Z backend-dev T023 → running
- 2026-10-07T02:52:06Z backend-dev T018 → running
- 2026-10-07T03:03:33Z backend-dev T023 → done: committed evaldb tests (reset/migrate/bootstrap/load_playbooks/load_workflows/ingest_document isolation); 42 passed, ruff clean
- 2026-10-07T03:48:26Z backend-dev T018 → done: eval/exam/runner.py part 2 (capture → batch per phase → replay, estimate from real requests, ceiling, resume/abandon, owner contracts direct) + tests/test_exam_batch.py (46 tests, fake client); ruff clean
- 2026-10-07T04:04:30Z backend-dev T018 → running (fixing reviewer defects)
- 2026-10-07T04:45:00Z backend-dev T018 → done: reviewer defects fixed (resume re-holds in-flight batch worst case; CeilingReached rows never stamped; abandon prices cancelled-batch answers; batch usd idempotent; capture-mode free stage; truncation test); 626 exam tests passed, ruff clean
- 2026-10-07T04:27:10Z backend-dev T019 → running
- 2026-10-07T06:12:18Z orchestrator → T019 build failed on a network outage (ENOTFOUND, no files written); T020 not started. Owner said continue: resuming from T019 (T012–T018, T021–T023 cached).
- 2026-10-07T06:17:58Z backend-dev T019 → running
- 2026-10-07T06:52:32Z backend-dev T019 → done: __main__.py CLI (12 commands, isolate() first) + README operator guide + subprocess isolation test; test_exam_isolation 41 passed, all test_exam_* + analytics 694 passed (before +1 test), ruff clean
- 2026-10-07T07:15:36Z orchestrator → session restarted; resumed from T019 review (T019 build done: CLI, README, isolation test 41 passed); T020 follows
- 2026-10-07T07:34:04Z backend-dev T019 → done (review fixes): review fixes: driver stubs the local reranker (cold-cache hermetic), baseline set removes its report if the designation audit fails, 2 new tests (status unfinished/waiting lines, no orphan baseline report); test_exam_isolation 43 passed, all test_exam_* + analytics 697 passed, ruff clean
- 2026-10-07T07:44:37Z qa-engineer T020 → running
- 2026-10-07T07:54:33Z qa-engineer T020 → running: gates green, writing verification.md
- 2026-10-07T07:59:01Z qa-engineer T020 → done: verification.md (41/41 AC PASS, 0 blocking defects, 2 low, 4 info; paid/live proof NOT-RUN by rule)
- 2026-10-07T08:05:51Z orchestrator → RUN COMPLETE: 23/23 tasks done and reviewed (T018, T019 after one fix round each); QA: 41/41 AC pass, backend 1945 passed, exam suite 697 passed, ruff clean, single head 0071; 2 leftover scratch DBs from the network outage dropped. No commit, $0 spent. Next: /verify, then owner commits.
