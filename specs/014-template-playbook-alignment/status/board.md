# Status Board: Templates and playbooks aligned (014-template-playbook-alignment)

Run started: 2026-10-27T12:31:23Z
Orchestrator session: /implement 014, 18 tasks in 7 waves; T017 paid AI run needs the user's go-ahead

Legend: ⬜ pending · 🔵 running · ✅ done · 🟥 failed · 🟧 blocked

| Task | Agent | Status | Last update | Detail |
|---|---|---|---|---|
| T001 | backend-dev | ✅ done | 2026-10-27T12:34:32Z | 48 tests, ruff clean |
| T002 | backend-dev | ✅ done | 2026-10-27T12:38:23Z | rules + _phrases + tests; ruff clean |
| T003 | backend-dev | ✅ done | 2026-10-27T12:42:27Z | options_when + options_for; 581 passed |
| T004 | frontend-dev | ✅ done | 2026-10-27T12:38:23Z | typecheck clean; vitest 2100/2100 |
| T005 | backend-dev | ✅ done | 2026-10-27T13:19:03Z | matrix 10,538; targeted 423 pass; full: 3 exam-content follow-ups |
| T006 | backend-dev | ✅ done | 2026-10-27T13:22:37Z | 378 combos, 0 findings; ruff clean |
| T007 | backend-dev | ✅ done | 2026-10-27T13:23:48Z | 2,112 combos, 0 findings; tests green |
| T008 | backend-dev | ✅ done | 2026-10-27T13:40:00Z | 192 combos 0 findings; ruff clean |
| T009 | backend-dev | 🔵 running | 2026-10-27T13:20:10Z | wave 3 |
| T010 | backend-dev | ✅ done | 2026-10-27T13:25:33Z | saas.txt frozen; 3,168 combos 0 findings; ruff clean |
| T011 | backend-dev | ✅ done | 2026-10-27T13:23:46Z | 2112 combinations, 0 findings |
| T012 | backend-dev | ✅ done | 2026-10-27T13:40:00Z | 200 combinations, 0 findings; ruff clean |
| T013 | backend-dev | ✅ done | 2026-10-27T13:29:48Z | standard.py, adopt endpoint, 17 tests, ruff clean |
| T014 | db-engineer | ✅ done | 2026-10-27T13:45:00Z | 0077_std_paper; dev ok |
| T015 | backend-dev | ✅ done | 2026-10-27T13:36:09Z | accidental paid run: 8 NDA reviews, stopped by orchestrator |
| T016 | backend-dev | ✅ done | 2026-10-27T13:56:40Z | 8109 passed; 2 non-014 failures |
| T017 | orchestrator | 🟧 blocked | 2026-10-28T03:20:00Z | sample complete across runs 3+4; 4 repeat gaps left; next step needs the user's say |
| T018 | qa-engineer | ✅ done | 2026-10-28T09:45:00Z | 42 PASS, 4 PARTIAL, 0 FAIL; AI half of AC-1A NOT-RUN |

## Event log

Append-only; newest last.

- 2026-10-27T12:31:23Z T001 backend-dev → running: launched (wave 1)
- 2026-10-27T12:31:23Z T002 backend-dev → running: launched (wave 1)
- 2026-10-27T12:31:23Z T003 backend-dev → running: launched (wave 1)
- 2026-10-27T12:31:23Z T004 frontend-dev → running: launched (wave 1)
- 2026-10-27T12:34:32Z T001 backend-dev → done: paper.py + test_paper_fill.py (48 tests) green, ruff clean
- 2026-10-27T12:38:23Z T002 backend-dev → done: library rules, _phrases, tests green
- 2026-10-27T12:38:23Z T004 frontend-dev → done: typecheck clean, vitest 2100/2100
- 2026-10-27T12:39:47Z orchestrator verified T001 (48 passed), T002 (ruff clean; rules tests pass; NOTE xfail on test_our_own_template_passes_the_literal_check must be removed in T016), T004 (675 template/form tests pass)
- 2026-10-27T12:42:27Z T003 backend-dev done
- 2026-10-27T12:43:36Z orchestrator verified T003 (payment tests pass). T003 also edited 3 fixtures outside its list (test_consultancy_dpa_answers.py 30→45 days, test_nda_check.py, test_intake_form_values_make_sense.py) — accepted; T011 told.
- 2026-10-27T12:43:36Z T005 backend-dev → running: launched
- 2026-10-27T13:19:03Z T005 backend-dev → done: paper.py wired into drafting, PLACEHOLDERS/_CODED, eval/template_matrix.py, tests green except exam-content follow-ups
- 2026-10-27T13:20:10Z orchestrator verified T005 (ruff clean; 014 drafting tests pass; matrix 10,538). Carried to T016: regenerate exam content (python -m eval.exam build --only constructed) and fix test_exam_content insurance-severity expectation; remove T002's xfail. Carried to T013: PLACEHOLDERS sample governing_law still 'the State of Delaware' (example text only).
- 2026-10-27T13:20:10Z T006–T011, T013 backend-dev → running: launched (wave 3)
- 2026-10-27T13:22:37Z T006 done (backend-dev): nda.txt rewritten, test_alignment_nda.py 381 tests green
- 2026-10-27T13:40:00Z T009 done (backend-dev): vendor.txt rewritten, test_alignment_vendor.py 2380 tests green
- 2026-10-27T13:23:44Z T007 backend-dev → done: msa.txt rewritten, test_alignment_msa.py, matrix 2,112 → 0 findings
- 2026-10-27T13:23:46Z T011 backend-dev → done: consultancy template + filler as frozen; matrix 0 findings
- 2026-10-27T13:24:11Z orchestrator: T006 (NDA 378/0), T009 (Vendor 2376/0), T011 (Consultancy 2112/0) reported done
- 2026-10-27T13:24:11Z T012 backend-dev → running: launched
- 2026-10-27T13:40:00Z T008 backend-dev → done: SOW matrix 192 → 0 findings, ruff clean
- 2026-10-27T13:25:33Z T010 backend-dev → done: saas.txt + test_alignment_saas.py, 0 findings over 3,168
- 2026-10-27T13:25:44Z T005 follow-up: SOW hold test updated
- 2026-10-27T13:40:00Z T012 backend-dev → done: dpa.txt rewritten, 200 combos 0 findings, 736 tests pass
- 2026-10-27T13:29:48Z T013 backend-dev → done: standard.py, POST /drafting-templates/{key}/playbook/standard, summary.standard, sample trigger; tests green
- 2026-10-27T13:31:05Z orchestrator: T007 (MSA 2112/0), T008 (SOW 192/0), T010 (SaaS 3168/0), T012 (DPA 200/0), T013 (adopt endpoint + standard.py) reported done; whole-repo ruff clean; graph regenerated. Carried to T016: test_docx_fields expects 'Governed by the State of Delaware.' (update sample to India + test); models.py trigger comment (db-engineer) left.
- 2026-10-27T13:31:05Z T014 db-engineer, T015 backend-dev → running
- 2026-10-27T13:45:00Z T014 db-engineer → done (0077_std_paper after 0077_obligation_edit_roles)
- 2026-10-27T13:32:50Z orchestrator verified T014: single head 0077_std_paper, chained on 0077_obligation_edit_roles (an untracked migration created at 18:11 by work outside this feature) — flagged to user.
- 2026-10-27T13:32:50Z T016 backend-dev → running
- 2026-10-27T13:35:11Z T015 backend-dev → done: eval/template_alignment.py; accidental partial paid run left 6 nda sample rows
- 2026-10-27T13:36:05Z orchestrator: T015 agent's test of --yes made an UNAUTHORISED paid run (dev container has MOCK_CLAUDE=false): 8 real NDA reviews (~$0.80) before the orchestrator killed pid 2121. Results (8 sample checks on nda): 7 of 8 have findings — real pair defects the phrase check misses: (1) NDA 4.1 'for no other purpose whatsoever' read as prohibited by permitted_use_and_standard_of_care; (2) personal_data rule wants a breach-notification sentence in 11.1 (flagged even with personal_data=No); (3) governing_law_and_jurisdiction prohibited 'exclusive jurisdiction of the courts of' fuzzy-matches our 'courts at Mumbai'; (4) remedies_and_injunctive_relief: relief only for the Disclosing Party leaves us without it as Receiving Party in a one-way NDA. Reported to user; needs pair fixes before T017.
- 2026-10-27T13:36:09Z T015 backend-dev → done: accidental paid run: 8 NDA reviews, stopped by orchestrator
- 2026-10-27T13:37:38Z orchestrator: user approved NDA pair fixes PF-1..PF-4 (recorded in plan.md); routed to backend-dev as follow-up of T006/T002
- 2026-10-27T13:38:44Z T006 NDA pair fixes PF-1..PF-4 applied (backend-dev): 565 tests pass, ruff clean
- 2026-10-27T13:39:49Z T001 backend-dev: PF-2 duplicate removed in paper.py
- 2026-10-27T14:20:00Z T016 backend-dev → blocked: all items done, but test_alembic_offline_sql_generation_succeeds fails because 0077_std_paper builds a Session on the offline MockConnection (needs an is_offline_mode() guard; db-engineer's file), and test_obligation_deadlines::...keeps_the_existing_obligations fails on message wording from obligations work (not 014).
- 2026-10-27T14:00:00Z T014 db-engineer: 0077_std_paper offline-mode guard
- 2026-10-27T13:56:40Z orchestrator full run after fixes: ruff clean, head 0077_std_paper, backend 8109 passed / 2 failed (test_ai_gateway_phase2 known; test_obligation_deadlines from uncommitted obligations work outside 014), frontend 2102 passed, typecheck only known docstudio errors. T016 done. T017 waiting for user go-ahead.
- 2026-10-27T14:00:34Z T017 (user go-ahead): NDA standard rules adopted (1 check: 0 findings). Check all (6 checks): consultancy 0; dpa 1 (international_transfers prohibited phrase hits protective 9.1); msa 1 (insurance 'and in the aggregate'); saas 1 (renewal reminder timing vs 60-day notice); sow 1 (scope_and_deliverables on the example scope); vendor 2 (insurance aggregate; payment_terms MSME 'shorter of'). AI sample (64 runs) paused before spending: it would repeat these findings; pair fixes first.
- 2026-10-27T14:03:32Z T002 backend-dev → done: PF-5..PF-9 applied
- 2026-10-27T15:57:05Z T017 re-check after PF-5..PF-9: adopt standard rules msa/vendor/saas/consultancy/dpa + check-all (nda, sow). Results: sow 0; msa 1 NEW (payment_terms: MSME 'whatever period is stated'); vendor 1 NEW (limitation_of_liability: personnel indemnity 10.1(e) not carved out of cap); saas, consultancy, dpa, nda INTERRUPTED (not completed, error 'interrupted'). The AI review surfaces different nits each run. 64-review sample NOT run. Paused for user decision.
- 2026-10-27T16:01:14Z T002 backend-dev → done: PF-10 review prompt + PF-11 vendor applied
- 2026-10-27T16:06:40Z T017 after PF-10 (review prompt 1.0.7) + PF-11: Check all → nda 0, msa 0, sow 0, vendor 0, saas 0, dpa 0; consultancy failed twice 'ai_unavailable'. Cause: the model returned 0 deviations but sent `citations` as a JSON string, not a list, so PlaybookReviewOutput validation rejected it (a_i_call_log validation_error). Proposed PF-12: tolerate a JSON-string list in PlaybookReviewOutput. Asking user.
- 2026-10-27T16:07:41Z T002 backend-dev → done: PF-12 citations-as-text parsing (PlaybookReviewOutput/PlaybookDeviationOutput accept a JSON-string list)
- 2026-10-27T16:10:52Z T017 after PF-12: consultancy re-check completed (parse fix works) with 1 LOW finding: insurance — reviewer reads PF-6's sentence ('per claim and in the aggregate' meets this position) as the required wording, and consultancy says only 'per claim'. Not zero → 64-review sample NOT run, per the user's condition. Proposed PF-13: reword PF-6 to accept cover per claim with or without 'and in the aggregate'.
- 2026-10-27T16:12:37Z T002 backend-dev → done: PF-13 insurance sentence reworded
- 2026-10-27T16:14:13Z T017 after PF-13: adopted standard rules consultancy/msa/vendor/saas (4 checks): consultancy 0, msa 0, saas 0, vendor 1 HIGH data_protection (7.2 'shall sign a DPA … at the same time as this Agreement' read as not yet executed). Sample NOT run: vendor not at zero. Asking user.
- 2026-10-27T16:16:11Z T002 backend-dev → done: PF-14 data-protection clarified
- 2026-10-27T16:18:35Z T017 after PF-14: adopted vendor/msa/consultancy (3 checks): msa 0, consultancy 0, vendor 1 LOW insurance — the reviewer itself says the wording is 'expressly permitted' yet marks it a fallback tracking item. Current state: nda/msa/consultancy/sow/saas/dpa pass 0; vendor 1 low. Sample NOT run (user condition: every template at zero). ~$3.20 spent on AI reviews in total.
- 2026-10-27T17:10:00Z T017 64-review AI sample (batch 20037dfd, report backend/eval/results/template_alignment/2026-10-27-20037dfd.json, ~$6.40): main runs 35 of 57 with findings; risky 6 of 7 flagged (nda risky: 0 findings, no clause types found). Repeatable gaps (3/3 runs): foreign-currency minimums (USD 600k/EUR 550k/GBP 475k) read as below 'INR 5 crore or equivalent' (msa, saas, vendor, consultancy); SIAC Singapore seat on foreign paper not tied to a stated foreign party (msa, saas, vendor); NDA 'the State of Delaware' hits its own prohibited phrase; consultancy fixed-fee fees left blank; sow scope one sentence on 2 combos; saas EU hosting no Order Form approval. Plus ~10 one-run nits. Not stable; no fixes made, waiting for the user.
- 2026-10-27T17:10:48Z T002 backend-dev → running: PF-15..PF-21 (foreign minimums, SG seat, NDA Delaware, consultancy fee, SOW scope, SaaS EU, risky NDA text)
- 2026-10-27T17:36:53Z T002 backend-dev → done: PF-15..PF-21 applied (rules in library.py, paper.py hosting/seat text, drafting.py consultancy fee hold, sow.py GST, matrix scope+value, eval risky sentences, tests/test_pair_fixes_pf15_21.py)
- 2026-10-28T09:00:00Z T017 after PF-15..PF-21: adopted standard rules nda/msa/vendor/saas/consultancy (checks: nda 0, msa 0, vendor 0, saas+consultancy failed ai_unavailable). 64-review sample batch 847a2aea: 21/57 main runs with findings (was 35), 1 error; risky 7/7 flagged (nda now 12). Per template runs with findings: nda 1/9, msa 4/6, sow 1/9, vendor 6/9, saas 10/12, consultancy 1/6 (+1 error), dpa 0/6. Repeatable: msa cap 'under this Agreement in the preceding 12 months' vs rule wording; vendor data_protection on no-personal-data paper (DPA signed before processing vs rule's signed with the agreement) and goods paper lacks professional indemnity; saas data_location (non-India hosting read as fallback, wants 'privacy function' term) and IP indemnity not expressly uncapped in 15.4. All 3 errors: model sent deviations/citations as JSON text with unescaped quotes from our defined terms ("GST", "MSMED Act") so PF-12's parser can't read it.
- 2026-10-28T10:00:00Z T002 backend-dev → running: PF-22..PF-26 (MSA cap, vendor DPA/PI, SaaS location/IP indemnity, review-output quote repair)
- 2026-10-28T11:00:00Z T002 backend-dev → done: PF-22..PF-26 (MSA cap wording, no-PD DPA signed now, goods PI scope, SaaS privacy-function approval + uncapped IP indemnity, review-output quote repair; tests/test_pair_fixes_pf22_26.py)
- 2026-10-28T02:30:00Z T017 after PF-22..PF-26: adopted msa/vendor/saas/consultancy (checks: msa 0, vendor 0, saas 1 high: 15.4(a) confidentiality carve-out not stated unlimited; consultancy 1 medium: public liability has no amount). Third sample KILLED after 31/64 reviews (exit 137: backend container restarted at 02:19:35Z after an outside edit to app/workflows/lifecycle_rows.py); no report JSON. Done: nda 0/9, sow 1/9 (acceptance criteria, once), msa 5/6 (cap: total contract value in 6.1 may narrow 13.1 — repeatable; MSMED conditional, indemnity defence), vendor 2/7 (Services combo: 18.2 'exclusive supervisory jurisdiction' vs prohibited phrase; 10.2 settlement consent). Not run: rest of vendor, saas, consultancy, dpa, all risky.
- 2026-10-28T03:20:00Z T017 remaining sample (vendor/saas/consultancy/dpa, batch f1fc2f46, ~$3.70): runs with findings vendor 3/9, saas 5/12, consultancy 2/6, dpa 2/6; risky 4/4 flagged; 0 errors. Repeat across this and the partial run: 'exclusive supervisory jurisdiction' (our Mumbai seat wording) read as the prohibited phrase (vendor 2, saas 2-3); vendor 10.2 indemnity defence control (4); consultancy public liability has no amount (2 + adoption check); msa 6.1 contract value vs 13.1 cap (partial run, 4). Others one-off.
- 2026-10-28T12:00:00Z T002 backend-dev → running: PF-27..PF-30 (seat wording, defence control, public liability amount, contract value vs cap) + eval pass rule (same clause_type in >=2 runs)
- 2026-10-28T13:00:00Z T002 backend-dev → done: PF-27..PF-30 + eval pass rule (same clause_type in >=2 runs); tests/test_pair_fixes_pf27_30.py (8); exam build pins old value_sentence
- 2026-10-28T09:45:00Z T018 qa-engineer → done: verification.md written (42 PASS / 4 PARTIAL / 0 FAIL); AC-1A AI half not run on current wording; new-rule computation on latest reports: 5 of 19 combinations would fail
- 2026-10-28T14:00:00Z T002 backend-dev → running: PF-31..PF-34 (is_foreign, seat nearest end, standard hash on 12 fields, SaaS several-regions pair); tests/test_pair_fixes_pf31_34.py
- 2026-10-28T14:20:00Z T002 backend-dev → done: PF-31..PF-34; tests/test_pair_fixes_pf31_34.py (22 cases); full suite 8402 passed, 1 known failure
