# Verification: Template and Playbook Sync

Feature ID: 006-template-playbook-sync
Spec: ./spec.md (APPROVED) · Plan: ./plan.md (APPROVED) · Tasks: ./tasks.md (APPROVED)

This file has one section per phase checkpoint. Phase 2 (T022) and the final pass (T024) append below.

---

# Phase 1 (T013): sync status

Task: T013 (qa-engineer)
Verified: 2026-10-15, branch `drl-saket-v2`, working tree = HEAD `1b5c57e` (clean; nothing uncommitted except this file and the status files). The orchestrator's browser walk was added the same day (section 7)
Scope: tasks T001 to T012 (Phase 1 = 45 FRs, 45 ACs). Phase 2 and 3 ACs are listed in section 3 as "later phase".

## Verdict

- **Gates are green.** `ruff` clean; one Alembic head `0074_template_sync`; backend **3307 passed, 1 skipped, 0 failed**;
  frontend **993 passed (63 files)**; `tsc --noEmit` shows only the 2 allowed stale `.next/types/validator.ts` errors.
- **45 Phase 1 ACs: 42 Pass, 2 Partial (AC-46, AC-56), 1 Browser (AC-60), 0 Fail.** 30 ACs belong to Phase 2/3.
  The browser walk settled AC-17 (Pass) and the Back-button half of AC-56; AC-60 is only partly walked.
  "Pass" means the clauses of the AC that Phase 1 owns are proven by a test I opened and read, and (where the
  stack allows) a live call. Any clause owned by a later phase is named in the notes and re-verified then.
- **No paid call was made by QA.** No check was started, no template saved/reset/restored/re-pinned, no draft made.
  The dev stack has `MOCK_CLAUDE=false`; the 8 template checks now on it are the orchestrator's, made in the browser walk
  (8 real reviews on claude-sonnet-4-6, $0.33 from `a_i_call_log`, under the owner's $1 ceiling).
- **Defects: 0 P0/P1, 1 P2 (P2-1, found in the browser walk: the check panel shows the model's free-text summary, which can contradict the findings), 4 P3** (section 6). No Fail.
- **One standing caveat for the browser walk:** the dev compose sets `DISABLE_RBAC=true`, so the running dev API does
  not refuse a read+run user. 403s are proven by the test suite **and** by a temporary RBAC-enforcing second API process
  (section 4.2). Do not read an un-refused click as a bug in dev.

Result vocabulary: **Pass** = executed here, evidence holds. **Partial** = Phase 1 scope has a named gap. **Browser** =
cannot be proven below a real browser; automated support is listed and the walk is in section 7. **Fail** = executed,
did not hold. **Later phase** = belongs to Phase 2 or 3.

## 1. Gates (exact commands and output)

Backend commands from the repo root against the running dev stack (`docker compose exec -T backend ...`, `/app` = `backend/`).
`tests/conftest.py` forces every `MOCK_*` on and `DISABLE_RBAC=false`, so the suite cannot spend money.

| Gate | Command | Result |
|---|---|---|
| Lint (CI 1) | `docker compose exec -T backend ruff check .` | `All checks passed!` (ruff 0.16.6) |
| Heads | `docker compose exec -T backend alembic heads` | `0074_template_sync (head)`: exactly one |
| Migrate (CI 2) | `docker compose exec -T backend alembic upgrade head` ; `alembic current` | exit 0, at `0074_template_sync (head)` |
| Fresh DB + round trip | scratch DB `qa_t013_scratch` (created, dropped afterwards): `alembic upgrade head` from empty, `downgrade -1`, `upgrade head`, via `-e DATABASE_URL=...` | whole chain ran to `0074_template_sync`; downgrade went to `0073_merge_trademark_suite`; re-upgrade OK; after the round trip both new tables and `contract.review_playbook_id` exist |
| Whole backend suite (CI 3) | `docker compose exec -T backend python -m pytest -q -p no:cacheprovider` | **`3307 passed, 1 skipped, 44 warnings in 347.05s (0:05:47)`**, exit 0. The 44 warnings are one existing `SAWarning` (FK cycle `document_extract`/`trademark`, `tests/test_exam_batch.py:132`). The skip is not in a feature file (0 skipped in the 8 below) |
| Feature backend files | `docker compose exec -T backend python -m pytest -v -p no:cacheprovider -rs tests/test_template_status.py tests/test_template_check_jobs.py tests/test_template_check.py tests/test_drafting_templates.py tests/test_template_drafting_record.py tests/test_playbook_pin.py tests/test_template_schemas.py tests/test_docx_fields.py` | **`419 passed in 34.10s`**: status 41 · check_jobs 56 · check 43 · drafting_templates 106 · drafting_record 26 · playbook_pin 23 · schemas 62 · docx_fields 62 |
| Frontend types | `cd frontend && npm run typecheck` | exit 1 with **only** the 2 allowed errors, both `.next/types/validator.ts` (stale `docstudio` routes). 0 errors in `src/` |
| Frontend tests | `cd frontend && npm run test` | `Test Files 63 passed (63)`, `Tests 993 passed (993)` |
| Feature frontend files | `npx vitest run "src/app/(app)/templates" src/lib/templates.test.ts "src/app/(app)/intake/_request-overview.test.tsx"` | `5 files, 173 passed`: templates.test.ts 36 · _wording-editor 20 · _check-panel 35 · _request-overview 34 · page 48 |

`npm run lint` was not run (hangs on a wizard in this repo).

## 2. How to read the AC table

Test ids are `<short>::<test_name>`. Short forms: `status` `jobs` `check` `tpl` `rec` `pin` `schemas` = `backend/tests/test_template_status.py`,
`test_template_check_jobs.py`, `test_template_check.py`, `test_drafting_templates.py`, `test_template_drafting_record.py`,
`test_playbook_pin.py`, `test_template_schemas.py`. `page` `panel` `editor` = `frontend/src/app/(app)/templates/page.test.tsx`,
`_check-panel.test.tsx`, `_wording-editor.test.tsx`. `lib` = `frontend/src/lib/templates.test.ts`. `overview` =
`frontend/src/app/(app)/intake/_request-overview.test.tsx`. Every cited test passed in the runs above. "Live" = a call I made
against the running stack (section 4). The frontend tests run in jsdom: they prove the component logic and wording, not a real
browser.

## 3. Phase 1 acceptance criteria

| AC | FR | Evidence | Result | Notes / gap |
|---|---|---|---|---|
| AC-1 | 1 | `tpl::test_list_get_and_versions_match_the_frozen_shapes`, `tpl::test_each_shipped_template_has_its_own_binding`, `tpl::test_the_api_answers_in_the_frozen_shapes`; `page::the list (FR-1, FR-2, FR-65) shows each template's status, playbook, binding and wording without opening it`. Live: `GET /drafting-templates` returns the 7 shipped templates with name, playbook (pinned, available), binding, source, version, status Browser: 7 rows load, all "Never checked", "Needs attention (7)", no console errors (section 7). | Pass | The "Customer Agreement" row is Phase 2 |
| AC-2 | 2 | `schemas::test_literal_union_equals_plan` (the eight states are the only ones); `status::_status` helper asserts on every status test that the answer validates, has exactly the contract's keys and `label == LABELS[state]`; `page::...shows every status in words with an icon, not by colour alone` (all eight). Live: every one of the 7 rows carries one state Browser: each row showed one status word (Never checked, then Passes / Has findings). | Pass | |
| AC-3 | 3 | Positive: `status::test_a_complete_current_review_with_nothing_found_passes`. Each negative: wording `status::test_new_wording_since_the_last_check_is_out_of_date_for_the_wording`, playbook version `...newer_playbook_version...`, partial read `status::test_a_template_longer_than_the_review_can_read_is_partly_checked_never_passes`, findings `status::test_findings_are_counted_with_the_highest_severity` | Pass | |
| AC-4 | 4 | `status::test_findings_are_counted_with_the_highest_severity` (2 findings, "2 findings, highest High."), `status::test_a_single_low_severity_finding_still_counts`, `check::test_an_item_at_the_fallback_position_is_still_a_tracked_deviation` (a fallback-position item is kept as a finding) | Pass | The fallback-position item and the status count are proven in two tests, not one end to end (P3-2) |
| AC-5 | 5 | `status::test_a_check_in_flight_is_checking_and_the_earlier_result_stays_viewable` (queued and running; earlier result keeps version 0 / playbook v1), `panel::a check in progress (FR-5, FR-19) polls checkDetail every 3 s ... keeps the earlier result`, `panel::check history ... opens a row's findings, labelled with the older wording it covered` | Pass | |
| AC-6 | 6 | `status::...out_of_date_for_the_wording`, `...out_of_date_for_the_playbook`, `status::test_both_moving_says_both`, `status::test_a_release_that_edits_the_shipped_wording_turns_version_zero_out_of_date` | Pass | |
| AC-7 | 7 | `status::test_a_template_longer_than_the_review_can_read_is_partly_checked_never_passes` (reason names the unread 20%), `status::test_a_partly_checked_template_with_findings_is_still_partly_checked`, `jobs::test_a_long_template_is_recorded_as_partly_read` | Pass | |
| AC-8 | 8 | `status::test_a_failed_attempt_is_couldnt_check_with_its_reason_and_never_passes` (all 7 error codes; earlier result stays), `jobs::test_the_organisations_spending_limit_fails_the_check_as_cost_cap` (message says "spending limit"), `jobs::test_an_ai_failure_fails_the_check_as_unavailable_and_leaks_nothing`, `jobs::test_asking_again_after_a_failed_check_of_this_wording_is_a_retry`, `page::a check and the editor offers Try again on Couldn't check, only to a person who may run reviews` / `hides Try again from a person who may not run reviews`, `panel::Couldn't check (FR-8)` | Pass | |
| AC-9 | 9 | Never checked: `status::test_a_template_never_checked_says_so`. No playbook: `status::test_a_template_nothing_reviews_is_no_playbook_and_says_why`, `jobs::test_no_playbook_means_no_check_no_job_and_no_ai_usage` (no row, no `JobRun`, no `AISkillRun`), `check::test_check_says_so_when_no_playbook_covers_the_template` (409, no row/job/AI), `jobs::test_a_pinned_playbook_that_is_gone_starts_nothing_and_is_not_replaced`, `tpl::test_the_check_is_requested_only_after_the_change_has_committed` (a save does call `request_check`) | Pass | "Save with no playbook" is shown by composition (save calls `request_check`; `request_check` returns None with no row/job/AI), not by one test that saves through the real `checks` module in an org with no playbook (P3-2) |
| AC-10 | 10 | `page::what the page must say about a check (FR-10)` (the three statements verbatim, "Last checked ... against NDA Playbook v2."), `panel::what the page must say (FR-10)` (same plus "Status: ...", "Last completed check: finished ..., against ... version 3, of version 3.") | Pass | Also in the browser walk (section 7) |
| AC-11 | 13 | Save, reset, load back, change playbook each ask the first and start the second: `editor::saving (FR-13, FR-59) says a paid review starts, sends nothing until confirmed`, `page::changing the wording saves only after saying a paid review starts`, `...resets a shipped template ... after confirming`, `...loads an earlier version back ...`, `page::which playbook reviews a template changes the playbook only after saying a paid review starts`, `panel::version history ... loads an earlier version back only after a confirm that mentions the paid review`. Backend: `tpl::test_the_check_is_requested_only_after_the_change_has_committed` (parametrized `saved`, `reset`, `restored`, `playbook_changed`; each requests the check with that trigger, after the commit), `jobs::test_the_service_answers_in_the_contracts_shapes_and_a_save_queues_its_check` (real module: queued check, `trigger == "saved"`, status `checking`) Browser: the Change playbook modal states the paid review and that drafts already made keep their playbook, and its confirm is disabled while the pin is unchanged. The Save / Reset / Load-back confirms were not clicked through in the browser. | Pass | Upload, type created, type reactivated are Phase 2. Browser walk covers the real confirm wording |
| AC-12 | 14 | `jobs::test_a_publish_checks_only_the_templates_that_playbook_reviews` (a publish of an unrelated better-named playbook starts nothing; a publish of X starts only X's templates, with `trigger_detail`), `jobs::test_a_publish_skips_a_retired_template`, `check::test_publishing_queues_a_check_of_the_template_that_playbook_reviews` (the real hook through the real publish route), `check::test_publishing_asks_for_the_template_checks_only_after_the_publish_committed`, `check::test_seeding_the_library_checks_every_template_its_playbooks_review`, `jobs::test_a_failure_checking_one_template_never_fails_the_publish_or_the_others` (two templates on one playbook). Code: the only two paths that set a version PUBLISHED are `playbooks/routes.py:574-577` (hooked) and `playbooks/library.py:340-343` (seed route hooked); the dev seed CLI `app/devtools.py:131` calls `seed_playbook_library` without the hook (intended: shipping starts no paid check) | Pass | "Retired" is exercised with a row set to `retired`; creating one is Phase 2 |
| AC-13 | 15 | `jobs::test_a_save_while_one_is_running_waits_and_a_later_one_replaces_it` (one running, newest replaces the waiting one, the replaced one is `superseded` and audited, nothing dispatched behind a running check), `jobs::test_the_database_itself_refuses_a_second_waiting_or_running_check` (partial unique indexes), `jobs::test_a_superseded_check_is_a_no_op_for_its_job`, `jobs::test_a_saved_check_reads_the_version_it_was_queued_for_not_the_newest`, `status::test_new_wording_since_the_last_check_is_out_of_date_for_the_wording`. Live: the two partial unique indexes `uq_drafting_template_check_one_queued/_running` exist (section 5) | Pass | |
| AC-14 | 16 | `jobs::test_a_check_runs_the_unsaved_text_it_was_asked_about`, `check::test_check_reads_the_unsaved_text_from_the_editor`, `status::test_unsaved_and_superseded_checks_do_not_count_as_a_check_of_the_template` (status unchanged), `jobs::test_an_unsaved_check_is_refused_while_one_is_waiting`, `page::a check and the editor checks the saved wording when the editor is clean: sends no body` / `checks the editor's wording when it holds unsaved changes, and labels the result unsaved`, `panel::Check now (FR-16)` | Pass | |
| AC-15 | 17 | `jobs::test_check_all_starts_exactly_the_checkable_templates_and_says_why_it_skipped_the_rest`, `jobs::test_check_all_carries_on_past_a_template_that_fails_to_queue`, `check::test_check_all_queues_one_check_for_each_covered_template_and_skips_the_rest`, `page::Check all (FR-17) states how many paid reviews it runs, names them, and calls nothing until confirmed` / `shows k of N finished ... and carries on past it`. Live (the orchestrator's Check all): 7 `check_all` rows for the 7 active templates, 7 `template_check` jobs, 1 `drafting_template.check_all_started` audit row, all 7 completed Browser: Check all through the button; the confirm said "This runs 7 paid AI reviews" and listed the 7; progress "Check all: 0 of 7 finished" (5 running, 2 waiting), then 7 of 7 with no refresh. | Pass | The dialog's count comes from the loaded list; the server decides what starts and the progress strip shows the server's list (P3-3). "Eight templates" needs the Customer type (Phase 2); the live stack has 7 |
| AC-16 | 18 | `status::test_a_check_serializes_to_exactly_the_contracts_summary_and_full_shapes`, `status::test_a_check_says_who_asked_and_whether_the_system_acted_for_them`, `check::test_the_checks_of_a_template_list_newest_first_with_unsaved_ones_flagged`, `panel::check history (FR-18) lists when, who, why, which wording, which playbook version and the outcome of every check`. Live: `GET /nda/checks/{id}` returns every field (state, trigger, versions, playbook, requester, on_behalf, times, findings); the same `msa` records come back for `legal1@example.com` as for admin (stored rows, not session state) | Pass | |
| AC-17 | 19 | Job is separate from the request: `jobs::test_a_request_queues_one_check_and_hands_it_to_the_worker`, `jobs::test_the_worker_runs_template_check_jobs`, `check::test_asking_for_a_check_answers_202_with_the_recorded_check_and_pays_for_nothing`, `panel::a check in progress` (page polls and recovers). Live: the worker container ran the orchestrator's 8 `template_check` jobs to `succeeded` Browser: Check now on Vendor, navigated away while it read "Checking. A check is running."; the DB showed running to completed about 16 s later; on return Vendor showed Passes + Results vary. | Pass | Walked in the browser (section 7). Nothing in the page holds the check: it is a stored row + a Celery job |
| AC-18 | 20 | `jobs::test_the_organisations_spending_limit_fails_the_check_as_cost_cap`, `jobs::test_a_failed_check_is_never_run_again_by_a_redelivered_job`, `jobs::test_a_failed_check_starts_no_job_of_its_own_again`, `jobs::test_a_job_handed_to_a_second_worker_is_interrupted_not_run_again`, `jobs::test_a_reclaimed_job_whose_first_worker_had_set_the_check_running_is_still_interrupted`. Code: `celery_app.beat_schedule` has no template/drafting entry (grep: none) | Pass | |
| AC-19 | 21 | `jobs::test_a_check_makes_no_run_finding_or_contract`, `rec::test_a_check_of_the_template_changes_no_contract_run_or_finding`, `check::test_the_job_reviews_the_saved_template_as_a_bare_request_drafts_it_and_stores_no_contract`, `jobs::test_the_real_controller_records_the_review_under_the_check_with_no_contract`. Live (after the orchestrator's 8 checks): the 8 AI runs with `resource_type = drafting_template_check` have `contract_id` NULL; `playbook_run` and `playbook_deviation` rows created since the first check: 0 | Pass | 8 contracts were created in the same window; all are pytest fixtures from other tests (titles "Every version structure test", "Interrupted review test", ...), created by my own full-suite run, not by checks |
| AC-20 | 22 | `check::test_review_text_asks_the_playbook_review_the_question_a_contract_review_asks` (same skill `playbook_review`; payload keys = `auto_review_contract`'s minus the two contract ids; `rules == PlaybooksService._auto_rule_payloads(...)`), `check::test_every_review_path_gives_the_model_the_same_rules_and_a_clear_approval_key`, `jobs::test_a_completed_check_reads_the_template_as_a_bare_request_drafts_it`, `pin::test_a_better_named_playbook_published_later_does_not_review_a_pinned_contract`. Code: both `review_text` (`playbooks/service.py:1420-1435`) and `auto_review_contract` (`:1487-1493`) call `select_run_version` and `_auto_rule_payloads` | Pass | Proven by one shared builder + same skill + same pinned playbook, not by a test that runs a check and a real draft review side by side and diffs the two payloads (P3-2) |
| AC-21 | 23 | `jobs::test_findings_get_an_index_a_wording_flag_and_template_forms_of_their_quotes`, `panel::a finding (FR-23) shows the clause, severity in words, approval, issue, quoted passage and suggested wording`. Live: a stored MSA finding has all 11 contract keys (index, rule_index, clause_type, severity, issue, quote, quote_template, suggested_fix, suggested_fix_template, suggestion_is_wording, approval_required) | Pass | |
| AC-22 | 24 | `lib::locatePassage (FR-24)` (0, 1 and 2 occurrences, whitespace differences, regex characters), `panel::Show in template (FR-24)` (passes the located range; disabled and says so when the passage is not in the current wording), `editor::jump to a passage (FR-24)` (selects the range, highlight dropped once the text changes), `page::a check and the editor jumps to the passage in the wording when Show in template is pressed` | Pass | Also in the browser walk |
| AC-23 | 25 | `lib::canApplyFinding truth table (FR-25)` (Word-based, advice, older version, twice, zero times, no permission), `panel::Apply suggestion (FR-25)` (offered once and replaces exactly the passage; not twice; not after applied; not without permission; never for a Word-based template, which still shows the suggestion; not for advice; not for older wording; not for someone else's unsaved check), `page::offers Apply suggestion once, puts it in the editor as an unsaved change, and Discard clears it` / `does not offer ... when the passage appears twice`, `page::sends the suggestions applied from a check with the save`, `tpl::test_applied_findings_are_stored_audited_and_shown_in_history`, `tpl::test_a_finding_that_is_not_from_a_check_of_this_template_is_refused`. Live: the MSA's stored finding is a wording suggestion whose quote occurs exactly once in the body, so Apply is offered; the SOW's is advice, so it is not Browser: on the MSA finding, Apply suggestion put the wording ("five percent (5%)") into the editor as unsaved text, the button became "Check the unsaved wording", Apply was hidden, Save/Discard enabled; Discard restored the shipped text and Apply was offered again; nothing saved. | Pass | The "note" the AC mentions is the version's `applied_findings` record (clause + severity, shown in History beside the note and written to the audit row), not the free-text note field (P3-1) |
| AC-32 | 35 | `tpl::test_wording_that_names_no_party_needs_an_explicit_yes`, `tpl::test_wording_with_no_party_field_needs_the_yes_to_save_through_the_api`, `editor::a wording with no field for a party (FR-35) warns, and saves only after the person ticks that it is intended`, `lib::missingPartyFields` | Pass | The upload and create-type entry ways are Phase 2 |
| AC-41 | 45 | `rec::test_a_draft_is_unchanged_when_its_template_is_edited_reset_or_its_playbook_published`, `pin::test_changing_the_templates_playbook_leaves_contracts_already_drafted_on_their_old_pin`, `rec::test_the_draft_text_is_what_the_template_always_rendered` | Pass | "Retired" is Phase 2 |
| AC-42 | 46 | `rec::test_the_draft_names_its_template_version_and_status_everywhere_it_is_recorded` (version 2, two findings, highest High: contract metadata, `intake.contract_drafted` audit row, request timeline and contract timeline all name template, version and status), `rec::test_a_template_that_passes_is_recorded_without_a_notice`, `rec::test_a_draft_the_workflow_makes_carries_the_notice_in_its_history` | Pass | The spec example says version 4; the test uses version 2 on purpose (so a version left at 0 fails) |
| AC-46 | 51 | Pin half: `pin::test_a_drafted_msa_is_reviewed_and_banded_by_its_pin_after_a_better_named_playbook_is_published`, `pin::test_a_counterparty_revision_of_a_drafted_msa_is_reviewed_by_the_pin`, `pin::test_a_pin_that_the_name_would_never_pick_still_reviews_the_contract`, `pin::test_retry_review_goes_to_the_pin_not_to_a_better_named_playbooks_review`, `tpl::test_set_playbook_refuses_a_playbook_that_cannot_review` (foreign/deleted/unpublished playbook refused), `rec::test_the_draft_holds_the_templates_pin_before_any_ai_job_is_dispatched`. Name-pick regression suites ran unmodified inside the 3307 | Partial | The AC's refusal ("creating a type with a playbook the drafts would not be reviewed by is refused before anything is saved") is the create-type flow, Phase 2 (T019), as the plan says |
| AC-51 | 56 | `tpl::test_saved_wording_is_what_drafting_uses_and_reset_goes_back` (reset makes version 2 holding the shipped wording; the edited version stays readable), `tpl::test_reset_on_the_shipped_wording_makes_no_version_and_no_check`, `tpl::test_the_check_is_requested_only_after_the_change_has_committed[reset]`, `page::resets a shipped template to its shipped wording after confirming` | Pass | |
| AC-52 | 57 | `tpl::test_list_get_and_versions_match_the_frozen_shapes`, `tpl::test_each_version_lists_only_its_own_saved_checks`, `panel::version history (FR-57, FR-58) shows each version's number, source, author, note, applied suggestions and check outcomes` / `reads a version's wording, with fields as chips, without changing anything`. Immutability: no route updates or deletes a version (the route table is pinned by `tpl::test_the_routes_use_exactly_the_permissions_the_application_already_had`); `uq_drafting_template_version (org_id, key, version)` | Pass | The "uploaded" source label is Phase 2 |
| AC-53 | 58 | `tpl::test_restore_makes_a_new_version_and_leaves_the_earlier_ones_alone`, `tpl::test_restoring_version_zero_is_a_reset_and_a_missing_version_is_not_found`, `page::loads an earlier version back into the editor and drops what was unsaved`, `panel::loads an earlier version back only after a confirm that mentions the paid review` | Pass | Copying the original file and marks is Phase 2 |
| AC-54 | 59 | `tpl::test_a_stale_save_is_refused_with_who_and_when_and_writes_nothing`, `tpl::test_two_saves_made_on_the_same_version_cannot_both_win` (the unique version number is the real guard), `tpl::test_a_save_made_on_a_stale_page_is_a_409_through_the_api`, `editor::a colleague saved first (FR-59)` (names who saved, shows both versions side by side, Load theirs / Keep mine), `page::shows a colleague's change instead of overwriting it`. Live: a save without `expected_version` is a 422 (section 4.1) | Pass | |
| AC-55 | 60 | `page::the states of the page (FR-60)`: loading state, empty state saying what to do next, list error with retry, one-template error with retry (list stays) | Pass | Also in the browser walk |
| AC-56 | 61 | jsdom: `page::leaving with unsaved changes (FR-61)` (switch template asks and keeps the edit on Cancel; no question when clean; `beforeunload` armed only while dirty; link capture; Back button asks and stays; Back not trapped when clean; guard re-arms after a discard; typing back to the saved text does not arm it) Browser (Back button): `history.state` carried `tplGuard:1`; Back stayed on /templates with "You have unsaved changes to this template."; Cancel kept the edit; after Discard the sentinel was gone within 800 ms and a clean Back left to /intake with no dialog. | Partial | Back is proven in a real browser. Gap: `beforeunload` on a real refresh or tab close is jsdom-proven only. The "unfinished upload / recognised text will be lost" clause is Phase 2 |
| AC-57 | 62 | `editor::fill-in fields under their plain names (FR-62)` (chips, never raw code; list with what each fills and an example; Insert at the cursor; pasted known codes become chips, unknown stay visible), `lib::toEditorText / toBodyText (FR-62)`, `panel::...reads a version's wording, with fields as chips`, `page::previews the editor's wording with example values and lists the fields it uses by their plain names`, `tpl::test_every_placeholder_has_its_own_plain_name` | Pass | The upload view is Phase 2 |
| AC-58 | 63 | `page::the status strip (FR-63) counts templates by status and narrows to those that need attention`, `keeps the open template open when a filter hides it`, `says so when nothing needs attention, and offers the whole list back`, `lib::needsAttention / tones` (exactly the five statuses) Browser: the "Needs attention (7)" strip on first load. | Pass | |
| AC-59 | 64 | `page::who is offered what (FR-64)`: a read+run viewer gets no Change playbook, Reset or Save, a read-only editor, the reason line "You can view and check templates; changing them needs the permission to edit playbooks.", and still has Check all, Check now, Checks and History; a read-only user is told checking needs the run permission. `lib::templatePermissions` | Pass | Walk it with `legal1@example.com` (section 7). Dev API will not refuse writes (RBAC off), but the UI gates on `/auth/me` permissions |
| AC-60 | 65 | Words not colour: `page::shows every status in words with an icon, not by colour alone`. Keyboard: `page::marks the open template and lets the keyboard pick another` (list rows are real buttons, `aria-current`), `page::moves between tabs with the arrow keys`; the other controls are `Button`s Browser (partly): list items are native buttons, tabIndex 0, name from content, `aria-current` on the selected one; tabs are `role=tab`. | Browser | A full keyboard-only walk (list, tabs, Check now, Check all, Save, dialogs) was not done |
| AC-61 | 66 | `check::test_reading_checks_needs_the_permission_to_view_playbooks`, `tpl::test_reading_a_template_needs_the_permission_to_view_playbooks`, `tpl::test_a_person_with_no_playbook_permission_at_all_cannot_change_a_template`. **Live with RBAC enforcing** (4.2): member `user1@example.com` gets 403 "Missing permission: playbook:read" on list, options, template, versions, checks and preview; no token is 401 | Pass | |
| AC-62 | 67 | `check::test_checking_needs_the_permission_to_run_a_playbook[post /saas/checks, post /check-all]` (a `playbook:read`-only user: 403, no AI call, no check row, no `JobRun`). **Live with RBAC enforcing**: member gets 403 "Missing permission: playbook:run" on `POST /nda/checks` (twice) and `POST /check-all`; nothing started | Pass | The live run had no user that holds read but not run (no default role does); that exact case is the unit test |
| AC-63 | 69 | `tpl::test_another_organisations_wording_and_pin_are_invisible`, `tpl::test_another_organisations_versions_and_playbooks_are_not_reachable_through_the_api`, `check::test_a_check_is_found_only_under_its_own_template_and_organisation`, `tpl::test_set_playbook_refuses_a_playbook_that_cannot_review` (another org's playbook). Live: a check id asked under another template is 404 | Pass | The dev stack has one organisation, so cross-org was not exercised live |
| AC-64 | 70 | Rows written and asserted: saved with `applied_findings` (`tpl::test_applied_findings_are_stored_audited_and_shown_in_history`), pin before/after (`tpl::test_set_playbook_pins_audits_before_and_after_and_starts_a_check`), lazy pin as the system (`tpl::test_the_first_use_pins_todays_name_match_and_audits_it`), check started/finished/superseded/failed with person or "system on behalf of" (`jobs::test_automatic_triggers_are_the_systems_on_behalf_of_the_person_and_manual_ones_are_theirs`, `jobs::test_a_completed_check_reads_the_template_as_a_bare_request_drafts_it`), check all (`jobs::test_check_all_starts_exactly_...`), contract drafted (`rec::test_the_draft_names_its_template_version_and_status_everywhere_it_is_recorded`). Live: the orchestrator's run left 1 `check_all_started`, 8 `check_started` (actor = the person), 8 `check_finished` (actor NULL, on behalf in metadata), 7 `playbook_pinned` (system); `audit_log` has DB triggers `audit_log_no_update` and `audit_log_no_delete`; `verify_audit_hash_chain` over all 15,928 rows is intact, and all 144 template rows link and hash correctly | Pass | Upload, scan, type created/retired/reactivated audits are Phase 2 |
| AC-65 | 11 | `status::test_checks_of_the_same_wording_and_playbook_that_disagree_are_marked_as_varying`, `status::test_agreeing_checks_are_not_marked` (both found / both none), `status::test_the_marker_is_gone_when_the_wording_or_the_playbook_version_changes`, `status::test_an_unsaved_check_that_disagrees_does_not_make_the_results_vary`, `status::test_reading_the_marker_starts_no_review_and_writes_nothing`, `status::test_two_real_checks_of_identical_wording_that_disagree_vary_through_the_worker`, `panel::explains results vary with how many checks found findings and how many found none`. **Live, from real reviews:** the Vendor template (shipped wording, Vendor playbook v1) was `has_findings` after Check all and `passes` with `results_vary: true` after the orchestrator's second check; the status followed the newer check Browser: after Check now on Vendor (1 finding, then 0 on identical wording) the page showed PASSES + RESULTS VARY. | Pass | |
| AC-66 | 12 | `jobs::test_a_completed_check_reads_the_template_as_a_bare_request_drafts_it`, `jobs::test_check_all_starts_exactly_the_checkable_templates_and_says_why_it_skipped_the_rest`, `check::test_the_nda_form_offers_no_term_shorter_than_the_playbooks_two_years`, `page::what the page must say about a check (FR-10)` (form answers are not covered). Live: 7 Check all reviews + 1 manual = **exactly 8** `playbook_review` AI runs with `resource_type = drafting_template_check`; nothing else reviewed | Pass | |
| AC-69 | 47 | `rec::test_a_draft_is_made_in_every_state_and_the_notice_names_that_state` (Passes, Has findings, Never checked, Out of date, Partly checked, Couldn't check; each drafts, notice text pinned, version recorded, AI jobs dispatched), `rec::test_a_template_nothing_reviews_still_drafts_and_the_notice_says_why`, `rec::test_a_template_whose_pinned_playbook_is_gone_still_drafts_and_keeps_the_pin`, `rec::test_a_check_in_flight_does_not_hold_a_draft_back`, `rec::test_a_template_that_passes_is_recorded_without_a_notice`, `rec::test_a_draft_the_workflow_makes_carries_the_notice_in_its_history`, `rec::test_the_route_hands_the_notice_to_the_page_and_still_drafts` / `...says_nothing_for_a_template_that_passes`, `overview::shows the server's notice at the top, without changing the draft's own success flow` / `shows no notice when the template passes` | Pass | "New type created moments ago" is Phase 2. A real draft was **not** made on the stack: drafting queues metadata/clause/embedding jobs and an automatic review, which are paid (section 8) |
| AC-70 | 68 | `tpl::test_a_person_who_may_view_and_run_but_not_edit_cannot_change_a_template` (403 on every change route), `tpl::test_an_editor_can_save_reset_restore_and_change_the_playbook_through_the_api` (admin allowed), `tpl::test_the_routes_use_exactly_the_permissions_the_application_already_had` (route-by-route table). **Live with RBAC enforcing** (4.2): `legal1@example.com` (legal_reviewer) gets 403 "Missing permission: playbook:update" on `PUT /nda`, `POST /nda/reset`, `POST /nda/versions/0/restore`, `PUT /nda/playbook`, with no template/version/check/pin row created. Permission list unchanged: `permission` table = 56 rows, none contains "template"; `git diff 3f6434a..HEAD -- backend/app/core/rbac.py backend/app/roles` is empty | Pass | Upload/create/retire/reactivate/scan-step routes are Phase 2 |

### Counts

| | Count | ACs |
|---|---|---|
| Phase 1 ACs | 45 | |
| Pass | 42 | AC-1..23, 32, 41, 42, 51..55, 57..59, 61..66, 69, 70 |
| Partial | 2 | AC-46 (create-type refusal is Phase 2), AC-56 (real `beforeunload` not walked) |
| Browser | 1 | AC-60 (keyboard-only walk not done) |
| Fail | 0 | |
| Later phase | 30 | see below |

Of the 42 Pass rows, 12 also have a clause owned by a later phase, named in their Notes and re-verified then: AC-1, 11, 12, 15, 32, 41, 52, 53, 57, 64, 69, 70.

**Later phase (not failed, not tested here):**

- Phase 2 (26 FRs): AC-24, 25, 26, 27, 28, 29, 30, 31, 33, 34, 35, 36, 43, 44, 45, 47, 48, 49, 50, 67, 68, 71, 72, 73, 74, 75.
  AC-48 depends on T021 (drafting from a new type).
- Phase 3 (4 FRs): AC-37, 38, 39, 40 (Word-format drafts). AC-37 needs real Microsoft Word; so does AC-36 (Phase 2).
- Total 30. Phase 1 backend already contains `docx_fields.py` (T004, 62 tests passing, not wired to any route); it is verified as part of AC-31/AC-36/AC-37 in T022/T024.

## 4. Live API against the running stack

Stack: `docker compose ps` all healthy (backend, worker, beat, frontend, postgres, redis). API `http://localhost:8000/api/v1`,
`/healthz` 200. Dev container env: `MOCK_CLAUDE=false`, `DISABLE_RBAC=true` (from `docker-compose.override.yml:31`),
`ENVIRONMENT=development`. Login: `POST /api/v1/auth/login {"email","password"}` returns `access_token`.

### 4.1 As the dev admin (`admin@example.com`), read-only calls

| Call | Result |
|---|---|
| `GET /drafting-templates` | 200, 7 rows (nda, msa, consultancy, sow, vendor, saas, dpa), all `active`, `source: default`, `version: 0`, `editable: true`, playbook `pinned: true, available: true` |
| `GET /drafting-templates?include_retired=true` | 200. `?include_retired=maybe` 422 |
| `GET /drafting-templates/options` | 200, 8 bindings (7 held, 1 free: Selling to a customer), 8 published playbooks with version numbers |
| `GET /drafting-templates/nda` | 200, `DraftingTemplateOut` |
| `GET /drafting-templates/nda/versions` | 200, one synthetic version 0 (`is_default: true`, `created_at: null`) |
| `GET /drafting-templates/nda/checks` | 200; `?limit=1` returns 1 row; `?limit=0` and `?limit=201` are 422 |
| `GET /drafting-templates/nda/checks/{id}` | 200 `TemplateCheckOut`; `/nda/checks/not-a-real-id` 404 "No such check."; a real id asked under `/msa/checks/` 404 |
| `GET /drafting-templates/nope` and `/nope/versions`, `/nope/checks` | 404 "No such template" |
| `GET /drafting-templates/NDA` | 404. `/nda/` trailing slash 307 (framework redirect) |
| `POST /drafting-templates/nda/check` (the old route) | **404 `Not Found`** |
| `POST /drafting-templates/nda/preview {"body":"Between {company} and {counterparty}."}` | 200 `{"text":"Between Aegis Pharma Ltd and Globex Corporation."}` (read-only, no AI). Body with `{client_name}` is 422 |
| No token / bad token | 401 / 401 |
| Write routes with the body schema broken (Pydantic rejects before any handler runs, so nothing can be written or paid for): `PUT /nda {"body":"x"}`, `PUT /nda {...,"expected_version":-1}`, `POST /nda/reset {}`, `POST /nda/versions/0/restore {}`, `PUT /nda/playbook {}`, `POST /nda/checks {"body":5}` | 422 each, naming `expected_version` (missing / `greater_than_equal`), `playbook_id` (missing), `body` (`string_type`). Template versions and statuses unchanged afterwards |
| Shape check | The JSON of list, options, template, versions, checks list and one check was validated with `model_validate` against `app/drafting_templates/schemas.py` **and** compared key set by key set (recursively): **no missing key, no extra key** in any. A stored finding has exactly the 11 contract keys. (No finding existed in the NDA's checks, so the finding key set was read from the MSA's) |
| Timing | list 50-70 ms, template 22-25 ms, versions 15 ms, checks 14 ms, options 37-42 ms |

### 4.2 Permissions (read+run user, member, no token)

**Dev RBAC is off** (`DISABLE_RBAC=true`, `core/rbac.py:240` returns True for every check), so the running API on :8000 answers a
legal reviewer's write exactly as it would an admin's. I therefore did **not** call any real write route as a non-admin on :8000. To get a live 403 without that risk I
started a **second API process inside the backend container with `DISABLE_RBAC=false`** (`uvicorn app.main:app --port 8001`,
bound to 127.0.0.1, same dev database), ran the calls below, then killed it and removed my temp files (8001 is down, 8000 untouched).
Write routes were sent with **invalid bodies**, so a missed 403 would have shown as 422 and written nothing. Before any call that could start a paid check, the script asserted
a member's `GET /drafting-templates` was 403 (proof RBAC was enforcing).

Read+run user = **`legal1@example.com`** (role `legal_reviewer` = `playbook:read` + `playbook:run`; `/auth/me` lists exactly those two playbook permissions). It is a seeded dev user (`app/devtools.py` `DEV_USERS`); I created no user.

| User | Call | Result |
|---|---|---|
| legal1 | `GET /drafting-templates`, `/options`, `/nda`, `/nda/versions`, `/nda/checks` | 200 each; `/nda/checks/not-a-real-id` 404 |
| legal1 | `PUT /nda`, `POST /nda/reset`, `POST /nda/versions/0/restore`, `PUT /nda/playbook` | **403 "Missing permission: playbook:update"** each |
| member `user1@example.com` (no playbook permission) | list, options, `/nda`, `/nda/versions`, `/nda/checks`, `POST /nda/preview` | **403 "Missing permission: playbook:read"** each |
| member | `POST /nda/checks` (with `{}` and with no body), `POST /check-all` | **403 "Missing permission: playbook:run"**; nothing started |
| member | the four change routes | 403 "Missing permission: playbook:update" |
| no token | `GET /`, `POST /nda/checks`, `POST /check-all`, `PUT /nda` | 401 each |

Writes: before and after the 17 refused calls, for the dev org, `drafting_template_version` 0 → 0, `drafting_template_check` 8 → 8, `drafting_template_type` 7 → 7,
pinned types 7 → 7, `audit_log` rows `drafting_template%` 143 → 143, `contract` 519 → 519. The only change was +17 `denied` authorization-decision audit rows (`core/authz.record_decision`), one per refusal.

## 5. Data checks (dev database `legal_clm`)

Run with `docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`.

- `alembic_version` = `0074_template_sync`.
- `\d drafting_template_check`: every column of plan.md's table (28 columns, types and nullability as specified), PK, FKs to
  organization/playbook/playbook_version/user, indexes `ix_..._org_id`, `ix_..._org_key_created`, `ix_..._status`, `ix_..._playbook_id`, `ix_..._playbook_version_id`, and the two
  partial uniques `uq_drafting_template_check_one_queued` (`status = 'queued'`) and `uq_drafting_template_check_one_running` (`status = 'running'`) on `(org_id, template_key)`.
- `\d drafting_template_type`: columns as planned, `uq_drafting_template_type_key (org_id, key)`, partial uniques `uq_drafting_template_type_binding (org_id, form_key, COALESCE(agreement_kind,'')) WHERE status='active'` and `uq_drafting_template_type_name (org_id, lower(name)) WHERE status='active'`, FKs incl. `playbook_id`.
- `\d drafting_template_version`: new columns `applied_findings` (json) and `restored_from_version` (integer).
- `contract.review_playbook_id`: `character varying`, nullable, FK `fk_contract_review_playbook_id_playbook`, index `ix_contract_review_playbook_id`.
- The same objects exist on a database built from empty by `alembic upgrade head` (section 1).
- **Worker has the new job type loaded.** `docker compose exec -T worker python -c "import app.drafting_templates.checks"` imports from `/app/app/drafting_templates/checks.py` with `request_check`, `run_check_job`, `template_status`, `check_all`, `on_playbooks_published` present, and `app.jobs.tasks` contains the `template_check` branch. The worker log shows `redis` connection errors from 09:10 to 09:15:39 (the stack restart; redis came back after the worker), `Connected to redis` and `celery@... ready` at 09:15:45, then normal operation: `run_ai_job` tasks for the orchestrator's Check all at 09:19 (seven in ~11 to 35 s) and a manual one at 09:22. `job_run` has 8 `template_check` rows, all `succeeded`.
- The orchestrator's checks left: 8 `drafting_template_check` rows (7 `check_all`, 1 `manual` for Vendor, all `completed`, none truncated, none unsaved), 7 `playbook_pinned` audit rows (the lazy pin, by the system), 8 `check_started` + 8 `check_finished`, 1 `check_all_started`. Statuses now: NDA, Consultancy, SaaS, DPA **Passes**; MSA **Has findings** (1, Medium, `service_levels`); SOW **Has findings** (1, High, `scope_and_deliverables`); Vendor **Passes + Results vary**.
- `audit_log` trigger names: `audit_log_no_update`, `audit_log_no_delete`. `verify_audit_hash_chain(db)` returns True; a row-by-row pass over all 15,928 rows found no break, including 144 `drafting_template.*` rows.
- The dev org still has 0 `drafting_template_version` rows (all 7 templates are on the shipped wording, version 0): nothing in Phase 1 verification edited a template.

## 6. Defects and observations

No FAIL and nothing blocking, but P2-1 is worth fixing before the owner relies on the check panel. Code is read-only for QA; nothing was patched.

| ID | Severity | Where | Finding | Suggested handling |
|---|---|---|---|---|
| P2-1 | P2 | `frontend/src/app/(app)/templates/_check-panel.tsx:455` and `:461`; FR-23 / FR-10 | Found in the browser walk. The check panel prints the model's free-text `summary` under the findings banner, and it can contradict the findings. Stored MSA check: the summary says "Three deviations are flagged ... on closer reading ... so no deviation there" and goes on about indemnification, while the only stored finding is `service_levels` (medium). The findings list is the authoritative record; the summary is the model thinking aloud. A reader can believe there are three issues, or an indemnity issue. Confirmed against the stored row (`drafting_template_check.summary`). | Stop rendering `check.summary` in `_check-panel.tsx`; keep the findings count. Adjust any test that expects it |
| P3-1 | P3 | `drafting_templates/service.py:588-631` `_applied_findings`/`_add`; `_version-history.tsx:165-168`; AC-23 / FR-25 | FR-25 says "the saved version's note SHALL record which finding it applied". The implementation keeps the free-text `note` as typed and records the applied findings in the separate `applied_findings` column (clause + severity + check id + index), shown in History as "Applied 1 suggestion from checks: Service Levels (Medium)." and written to the audit row. The information is recorded and shown; it is not in the note text. Matches plan.md. | Owner to confirm the structured record is acceptable. If the note text itself is wanted, prefill the note with the applied clause names on the client |
| P3-2 | P3 (test depth) | AC-4, AC-9, AC-20 | Three ACs are proven by composition of tests rather than by one end-to-end test: (a) a fallback-position finding reaching the status count (review keeps it; status counts any finding); (b) a save in an org with no playbook through the real `checks` module (`request_check` returns None; save calls it); (c) a check's review payload vs a real draft's review payload compared side by side (shared `select_run_version` + `_auto_rule_payloads` + same skill, confirmed in code at `playbooks/service.py:1420-1435` and `:1487-1493`). Each link is tested and the code was read; no gap in behaviour found | Optional: add one test per item in a later task |
| P3-3 | P3 | `_check-all.tsx` / `page.tsx`, FR-17 | The "This runs N paid AI reviews" count is worked out from the loaded list (refreshed every 3 s while anything is checking). The server decides what actually starts (`checks.check_all`) and the progress strip uses the server's `started` list. If the list is stale when the person confirms, the stated N can differ from what runs, in either direction (never more than the templates with a playbook). | Accept, or have the dialog fetch a fresh count before showing it |
| P3-4 | P3 | `_template-list.tsx:86`, `_template-detail.tsx:194`, `_check-panel.tsx:647`, `_version-history.tsx:178` | Found in the browser walk. The playbook version is written "v{n}" after a name that can already hold a version, so the NDA playbook reads "Corporate NDA Negotiation Playbook v1.0 v2" on the list card, the detail header and "Last checked ...". The Change playbook modal (`_template-detail.tsx:437`) and the Checks tab (`_check-panel.tsx:306, 351, 426`) say "(version 2)" / "version 2", which reads correctly. | Use "(version {n})" everywhere; update the tests that expect `v2` (for example `page.test.tsx`, "against NDA Playbook v2.") |

### Template content findings (owner decisions, not code defects)

From the real reviews in the browser walk. The code did its job; the paper, or the reviewer, is what the owner may want to look at.

- The SOW template holds literal unfilled brackets (`[Deliverable name and description]`, `[YYYY-MM-DD]`, `[name]`), so every drafted SOW is flagged High (`scope_and_deliverables`).
- MSA service credits have no floor (`service_levels`, medium; the MSA leaves credits to the SoW). The spec's measurements flagged the same gap in earlier runs.
- Vendor's mutual liability cap (`limitation_of_liability`, high) was flagged on 1 of 2 runs on identical wording, likely the reviewer misjudging who benefits. This is the variance the Results vary marker exists for.

Items checked and found fine, because they were tempting to fault:

- `check_all` and `on_playbooks_published` filter every query by `org_id`; `get_check` filters by org, key and id.
- A check never writes a contract, review run, deviation or finding: 0 `playbook_run` / `playbook_deviation` rows from the live checks.
- The audit lock rule holds in the live run: all 8 checks started and finished, no hang (finish rows are written by the worker with the system as actor).
- `GET`s never pin or write: `status::test_reading_the_status_of_an_unpinned_template_writes_no_pin_and_no_check`; live, no row changed across the read calls.
- No new permission, no new route outside `/drafting-templates`, `rbac.py` untouched. The only publish paths are both hooked.
- The dev seed CLI (`devtools.py`) does not run the template-check hook after `seed_playbook_library`; that matches "shipping starts no paid check" (A-11) and is not a defect.

## 7. Browser walk (orchestrator)

Done by the orchestrator in the built-in browser at 1440x900, as admin, on `http://localhost:3000/templates`, 2026-10-15 about 14:48 to 14:56 IST
(09:18 to 09:26 UTC). QA did not drive the browser; these are the orchestrator's results, copied here and checked against the database (the check rows,
job rows and stored summaries match what was reported).

**Spend:** 8 real reviews on claude-sonnet-4-6, **$0.33** in total (from `a_i_call_log`), under the owner-approved ceiling of $1.

| AC | What was done | Result |
|---|---|---|
| AC-1, 2, 58 | First load: 7 templates, all "Never checked", "Needs attention (7)", no console errors | **Pass** |
| AC-15 | Check all through the button. The confirm said "This runs 7 paid AI reviews" and listed the 7. After confirming: "Check all: 0 of 7 finished" (5 running, 2 waiting). The list polled to 7 of 7 with no refresh: Passes 4 (NDA, Consultancy, SaaS, DPA), Has findings 3 (MSA medium `service_levels`; SOW high `scope_and_deliverables`; Vendor high `limitation_of_liability`) | **Pass** |
| AC-51 / D-11 | After Check all each playbook line changed from "matched by name; locked at its first check or draft" to "locked to this template" (the lazy pin) | **Pass** (matches the 7 `playbook_pinned` audit rows) |
| AC-21, 22, 23 | The MSA finding card showed the quote and the suggested wording. "Apply suggestion" put the wording into the editor as unsaved text: the textarea contained "five percent (5%)", the Check button became "Check the unsaved wording", Apply was hidden, Save and Discard were enabled. Discard restored the shipped text and Apply was offered again. Nothing was saved | **Pass** |
| AC-56 (Back) | With unsaved work `history.state` carried `tplGuard:1`. Browser Back stayed on /templates, showed "You have unsaved changes to this template." and kept the edit. Cancel kept the edit. After Discard the sentinel was gone within 800 ms, and a clean Back left to /intake with no dialog | **Pass** for Back. Refresh and tab close (`beforeunload`) are jsdom-proven only: AC-56 stays **Partial** |
| AC-17 | Check now on Vendor, then navigated away while the status read "Checking. A check is running." The DB showed running to completed about 16 s later. On return Vendor showed PASSES + RESULTS VARY | **Pass** |
| AC-65 | Same walk: Vendor had 1 finding and then 0 on identical wording; the page showed the Results vary marker and the status followed the newer check | **Pass** (live) |
| AC-11 / FR-51 | Change playbook modal: states the paid review and that "Contracts already drafted keep the playbook they were drafted with"; the current pin is preselected; "Change playbook and start the check" is disabled while unchanged; all 8 published playbooks are offered; Cancel closed it with nothing written | **Pass** for this control. The Save, Reset and Load-back confirms were not clicked through (jsdom-proven) |
| AC-60 | List items are native buttons (tabIndex 0, name from content, `aria-current` on the selected one); the tabs are `role=tab`. No full keyboard-only walk | **Browser, partly walked** |
| AC-10, 54, 55, 57 | Not walked | jsdom / backend evidence only |
| AC-59 | The `legal1` viewer UI was not walked | `page.test.tsx` (`who is offered what`) plus the RBAC-enforced 403 run in section 4.2 |
| AC-42, 69 | A real draft from a template was not made (it queues paid extraction and review jobs) | Test evidence only (section 8) |

Logins for anyone repeating the walk (local dev only): admin `admin@example.com`, viewer `legal1@example.com` (`legal_reviewer`), no-access member `user1@example.com`; all with the seeded dev password. The dev API has RBAC off, so as `legal1` the page gates by the permission list but the server would not refuse a write.

## 8. NOT-RUN and why

| Item | Reason | How to prove it |
|---|---|---|
| Browser items not walked: AC-10, 54, 55, 57, 59, the keyboard-only pass (AC-60), `beforeunload` on refresh/close (AC-56), the Save / Reset / Load-back confirms | The orchestrator's walk covered the rest (section 7) | jsdom evidence stands; a short second walk, or Playwright for AC-56/60 |
| A real draft from a template on the stack (AC-42, AC-69 end to end) | Drafting queues `metadata_extraction`, `clause_extraction`, `embeddings` and an automatic playbook review: paid AI calls | Owner approves one draft in the browser walk; then read the contract's history ("Drafted from the ... template, version N - <status>") and the request page notice |
| Any real save/reset/restore on a template | Each starts a paid check; the Change playbook modal was opened and cancelled only | Browser walk, with the owner's OK on spend |
| A user holding `playbook:read` without `playbook:run` against the live API | No default role has that combination | `check::test_checking_needs_the_permission_to_run_a_playbook` (`playbook:read` only) |
| Cross-organisation reads on the live stack | The dev database serves one organisation for this user | The three cross-org tests in AC-63 |
| Real Microsoft Word checks (AC-36, AC-37) | Phase 2/3 | T022, T024 |

E2E tooling: none needs installing for Phase 1. The risky parts (single flight, closing the tab, audit ordering) are covered by service-level tests plus the live worker run; a Playwright suite would only add value for AC-56 and AC-60 (real `beforeunload`, Back and keyboard). If the owner wants those repeatable, propose adding Playwright under `tests/e2e/` in Phase 2.

---

# Phase 2 (T022)

*Not started. T022 appends here after Wave 10.*

# Phase 3 and final pass (T024)

*Not started. T024 appends here after Wave 12.*
