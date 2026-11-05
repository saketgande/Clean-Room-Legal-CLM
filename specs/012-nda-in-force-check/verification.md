# Verification: NDA already in force check at intake (012-nda-in-force-check)

Verified 2026-10-27 by qa-engineer (T009). Code is uncommitted in the working tree. No browser tools were available, so every browser-only item is "not exercised". Nothing was written to dev data (all live checks were GETs).

## Gates

| Gate | Command | Result |
|---|---|---|
| Lint | `docker compose exec backend ruff check .` | All checks passed |
| One Alembic head | `docker compose exec backend alembic heads` | `0075_nda_check (head)` only |
| Backend suite | `docker compose exec backend python -m pytest -q -x --deselect tests/test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had` | 4700 passed, 1 skipped, 1 deselected (the known unrelated failure, not run), 3 warnings, 420s. No other failure. |
| Frontend types | `npm run typecheck` | Only the two known `.next/types/validator.ts` docstudio errors |
| Frontend tests | `npm run test` | 77 files, 1914 passed |

`npm run lint` was not run (per repo rule).

## Acceptance criteria

Test names: BE = `backend/tests/test_nda_check.py`, CT = `test_contract_types.py`, RC = `test_request_counterparty.py`; FE files under `frontend/src`: NC = `app/(app)/intake/_nda-check.test.tsx`, LIB = `lib/nda-check.test.ts`, WZ = `app/(app)/intake/_agreement-wizard.test.tsx`, RO = `app/(app)/intake/_request-overview.test.tsx`, TN = `app/(app)/contracts/_type-needed.test.tsx`, TP = `components/contract-type-picker.test.tsx`, WS = `app/(app)/contracts/[id]/_clm-workspace.test.tsx`.

PASS = automated evidence executed and green. "Browser: not exercised" means the visual/click-through half of the plan's evidence was not done.

| AC | Evidence | Verdict |
|---|---|---|
| AC-1 | WZ "shows the NDA already in force for an NDA with a counterparty...", "is gone for any other kind of agreement...", "is hidden until a counterparty is named", "switching the kind away hides it...", "asks again for a different counterparty (AC-1)". Live: `GET /intake/nda-check` for Initech returns results; for a no-match name returns empty. Browser: not exercised. | PASS (browser not exercised) |
| AC-2 | WZ "a check that fails shows one line and filing still works, sending no nda_check (AC-2)", "a check still loading does not hold filing...". NC "says it is checking while loading, and that filing still works when it fails". RO "a failing NDA check shows its own Retry line...". | PASS |
| AC-3 | BE `test_status_decides_the_list_and_ended_contracts_are_absent`; NC/LIB "says so when there is no end date..." | PASS |
| AC-4 | BE `test_status_decides_the_list_and_ended_contracts_are_absent` (closed, archived, ended yesterday absent) | PASS |
| AC-5 | BE `test_status_decides_the_list_and_ended_contracts_are_absent`; NC "shows in force, then in progress, then the MSA hint...". Live: Initech returns 2 in force + 2 in progress, matching `psql` (stages active/active, approval/review). | PASS |
| AC-6 | NC "shows in force, then in progress...", "opens every contract in a new tab..."; BE `test_other_org_no_permission_and_links_open`. Live response carries title, stage, end_date, id. Browser click-through: not exercised. | PASS (browser not exercised) |
| AC-7 | BE `test_kind_is_known_only_for_nda_drafted_from_our_template`; NC wording case. Live: dev NDAs return `kind: null` (read as "Type not recorded"). | PASS |
| AC-8 | BE `test_order_and_ending_soon_boundaries` | PASS |
| AC-9 | BE `test_order_and_ending_soon_boundaries`; LIB "reads 0, 1 and n days without '1 days'" | PASS |
| AC-10 | BE `test_walled_nda_or_msa_leaves_no_trace`, `test_other_org_no_permission_and_links_open`, `test_lawyer_behind_a_wall_sees_the_choice_and_reason_but_no_list`. Live walled-user probe: not exercised (would need creating a user and a wall; not done to keep dev data untouched). Browser: not exercised. | PASS (live and browser not exercised) |
| AC-11 | WZ "Cancel request leaves the form and files nothing (AC-11)"; NC "Cancel calls onCancel, Continue calls onContinue...". Live DB counts before/after a browser cancel: not exercised. | PASS (live not exercised) |
| AC-12 | BE `test_filing_stores_the_record_with_one_audit_and_one_timeline_row`, `test_forged_or_walled_ids_never_enter_the_record`, `test_a_retried_filing_adds_no_second_record`; WZ "Continue with a reason files with the ids it showed and the reason", "filing without choosing Continue still sends what was shown, with reason null". Live filing: not exercised (would write dev rows). | PASS (live not exercised) |
| AC-13 | BE `test_request_page_excludes_own_contract_and_recomputes_on_every_read`, `test_reading_writes_no_audit_or_timeline_rows`. Live: `GET /intake/requests/{id}/nda-check` as admin on 10 existing dev NDA requests returns per-request results (in force or in progress by counterparty). Browser as assigned lawyer: not exercised. | PASS (browser not exercised) |
| AC-14 | BE `test_lawyer_behind_a_wall_sees_the_choice_and_reason_but_no_list`, `test_filing_record_listed_is_refiltered_per_viewer_with_current_titles`; NC "leaves out 'They were shown' when nothing listed is visible to this viewer (AC-14)", "shows the filing record with the reason and only the contracts this viewer may see" | PASS |
| AC-15 | RC `test_ended_nda_is_not_counted_as_in_force_in_the_note`, `test_own_contract_and_other_counterparties_are_excluded_and_nda_counted` | PASS |
| AC-16 | BE `test_other_org_no_permission_and_links_open` | PASS |
| AC-17 | BE `test_other_org_no_permission_and_links_open` (listed implies opens); NC "opens every contract in a new tab...". Browser click-through of every link: not exercised. | PASS (browser not exercised) |
| AC-18 | BE `test_recognition_is_by_stored_type_never_by_title`; CT `test_canonical_contract_type_reads_what_is_stored` | PASS |
| AC-19 | BE `test_reading_writes_no_audit_or_timeline_rows`, `test_filing_stores_the_record_with_one_audit_and_one_timeline_row`. Live: audit_log count unchanged (22110 before and after) across GET `/contracts/types`, `/contracts/type-needed`, three `/intake/nda-check` calls and a request-page check (the one +1 seen earlier was the login I made). One-row-on-filing live: not exercised. | PASS (live filing not exercised) |
| AC-20 | BE `test_live_msa_is_a_hint_not_an_nda`; NC "never calls an MSA an NDA in force (AC-20, AC-22)" | PASS |
| AC-21 | BE `test_live_msa_is_a_hint_not_an_nda`; NC "shows in force, then in progress, then the MSA hint..." Live: Initech MSA is in signature (not live), correctly absent from `msas`; no dev counterparty with a live MSA was probed. | PASS (live live-MSA case not exercised) |
| AC-22 | BE `test_live_msa_is_a_hint_not_an_nda`; NC "never calls an MSA an NDA in force (AC-20, AC-22)"; WZ "a live MSA alone is shown and recorded as shown (FR-33)" | PASS |
| AC-23 | BE `test_walled_nda_or_msa_leaves_no_trace` | PASS (browser not exercised) |
| AC-24 | BE `test_register_mode_follows_the_link_and_name_mode_follows_the_name`, `test_unknown_or_foreign_register_id_falls_back_to_name_without_error`; NC "with nothing says one quiet line and no count, and notes a name match". Live: register pick returned `matched_by: "register"`, free text `"name"`. | PASS |
| AC-25 | BE `test_order_and_ending_soon_boundaries` | PASS |
| AC-26 | WZ "an empty result files without nda_check, and shows no Cancel or Continue (AC-26)", "Cancel request leaves the form and files nothing (AC-11)"; BE `test_nothing_matched_stores_nothing`, `test_filing_without_nda_check_records_nothing`. Live cancel leaves no rows: not exercised. | PASS (live not exercised) |
| AC-27 | CT `test_drafting_an_nda_request_stores_the_listed_nda_type`. Browser draft: not exercised. | PASS (browser not exercised) |
| AC-28 | CT `test_type_needed`, `test_suggested_type_is_the_document_reading_only_while_needed`, `test_put_sets_the_type_with_one_audit_and_one_timeline_row_and_touches_nothing_else`; TP (4 tests); WS "asks for a type when the server says one is needed and the viewer may edit", "stays quiet without contract:update...". No single test chains "set to NDA, then appears in the check"; each half is covered separately. Live set-one-type: not exercised. | PARTIAL (chained flow and live not exercised) |
| AC-29 | CT `test_type_needed_lists_blank_and_other_titled_nda_but_not_walled_or_foreign`, `test_closed_or_archived_blank_type_is_not_listed`, `test_type_needed_is_truncated_past_the_cap`; TN (3 tests). Live: see log, API 11 vs `psql` 11. | PASS |
| AC-30 | CT `test_put_sets_the_type_with_one_audit_and_one_timeline_row_and_touches_nothing_else`, `test_put_titles_for_a_change_and_for_a_confirmation`, `test_put_repeating_the_same_value_writes_nothing_new`, `test_put_refuses_a_value_outside_the_list`, `test_put_is_404_for_a_walled_or_foreign_contract_and_403_without_update`, `test_a_person_chosen_type_survives_a_later_ai_reading`, `test_patch_refuses_contract_type_but_other_fields_still_work`. Live psql of audit/timeline rows: not exercised. | PASS (live not exercised) |
| AC-31 | CT `test_canonical_contract_type_reads_what_is_stored`; BE `test_recognition_is_by_stored_type_never_by_title`. Live: the dev "Mutual Non-Disclosure Agreement" types are recognised (Globex/Initech lists include them). | PASS |

Counts: 31 ACs. Automated evidence green for all 31. PASS with no caveat: 17 (AC-2, 3, 4, 5, 7, 8, 9, 14, 15, 16, 18, 20, 22, 24, 25, 29, 31). PASS with live and/or browser half not exercised: 13. PARTIAL: 1 (AC-28). FAIL: 0.

## Live check log (dev stack, `docker compose restart backend` first, healthz ok; admin@example.com)

- `GET /contracts/types`: 200, 9 values in the frozen order (NDA, MSA, Statement of Work, Software / SaaS Agreement, Vendor Agreement, Consultancy Agreement, DPA, Customer Agreement, other). Without a token: 401.
- `GET /contracts/type-needed`: 200, 11 items, not truncated. Items are the blank-type "Mutual NDA ..." and "MUTUAL NON.docx" contracts plus two "other"-typed "NDA Request (Custom draft)" contracts.
- Count vs database: `psql` pulled every non-deleted contract (51: `deleted_at is null`) with type, title, stage, archived and the `field_sources.contract_type` flag. "Unrecognised" was computed by re-applying the exact regex table from `app/contracts/contract_types.py` in a script (not by SQL): needed = not archived, not closed, and (type canonicalises to none, or canonicalises to "other" while the title canonicalises to a known type and the type was not user-chosen). Result 11, equal to the API's 11. Caveat: admin sees everything (no walls in dev), so this proves the rule, not the wall filter.
- `GET /intake/nda-check` for Initech (register id): `matched_by: register`, 2 in force (ends 2027-10-15 and 2027-10-30), 2 in progress (approval, review), no MSAs (its MSA is in signature). Matches `psql`. Same name as free text: `matched_by: name`, same list. Unknown name: empty lists, 200. No token: 401. `kind` is null for all dev NDAs.
- `GET /intake/requests/{id}/nda-check`: 200 for the 10 newest dev NDA requests, per-request results; for a non-NDA/other request `applies:true, check:null` (latest request).
- No audit rows from GETs: `audit_log` count 22110 before and after the calls. (A login adds one row; counts were taken after login.)
- Not done live, to avoid writing dev data: filing a request, cancelling, setting a type, drafting an NDA. Those are proven by the suite only.
- Walled-user probe: not done (needs a second user and a wall; relied on `test_walled_nda_or_msa_leaves_no_trace` and `test_lawyer_behind_a_wall_sees_the_choice_and_reason_but_no_list`).

## Browser items (orchestrator to check)

Not exercised (no browser): wizard panel appearance on NDA + Initech, cancel/continue clicks and DB counts, NDA card on the request page as assigned lawyer and as a walled user, contract page type prompt, `/contracts` type-needed panel and setting one type, link click-through, light and dark screenshots (no `bg-white`, no hex).

## Findings

| # | Severity | Finding |
|---|---|---|
| F1 | Info | The full backend run used `-x` and deselected the known-unrelated prompts test; no other failure surfaced. |
| F2 | Low | AC-28's "once set to NDA it appears in the NDA check" is covered only as two separate tests, no chained one. Recognition by stored type plus the PUT test make it very likely, but it is inferred. |
| F3 | Info | All dev NDAs return `kind: null`, so every dev result reads "Type not recorded" (by design, D4). A mutual/one-way label cannot be seen in dev data until an NDA is drafted from the template. |
| F4 | Info | `timeline_event` is not a table name here, so the timeline row count was not compared live; timeline rows are covered by tests only. |

No defects found.

## Verdict

PASS for the automated and API-level scope: all gates green, no failing criteria, 0 defects. Not signed off for the browser-only items and the live write paths (filing, cancel, set type); the orchestrator should exercise those in the browser before calling the feature done.

---

## /verify consolidation (orchestrator, 2026-10-27)

### Suites (re-run after T010, exact commands)

| Gate | Command | Result |
|---|---|---|
| Lint | `docker compose exec backend python -m ruff check .` | All checks passed |
| Migrations | `docker compose exec backend alembic upgrade head && alembic heads` | single head `0075_nda_check` |
| Backend tests | `docker compose exec backend python -m pytest -q` | 4701 passed, 1 skipped, 1 failed: `test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had` (known, unrelated to 012: compares prompts edited by another uncommitted change) |
| Typecheck | `cd frontend && npm run typecheck` | only the 2 known stale `.next/types/validator.ts` docstudio errors |
| Frontend tests | `cd frontend && npm run test` | 77 files, 1914 passed |

### Follow-up since T009

- **T010 (DEC-3)**: NDAs drafted on our template before the `drafted_from_template` marker existed now read their kind from the linked request's "Our template" answer. F3 above is resolved: REQ-7705 now shows "Mutual · Ends 15 Oct 2027" etc. `test_unmarked_nda_on_our_template_reads_its_kind_from_the_request`.

### Browser items (orchestrator, built-in browser, dev data, nothing written)

| Item | Result |
|---|---|
| `/contracts` type-needed panel | PASS: "11 contracts need a type", rows with title link, counterparty, stage, current type; suggested type preselected, Save disabled until a value is picked |
| Wizard panel (New agreement → NDA → Initech Software Ltd) | PASS: "NDA in force" (2, sorted by end date), "In progress, not yet in force" (2, with stage), Cancel request + Continue (AC-1, AC-6) |
| Request page card (REQ-7705) | PASS: card above Counterparty; the request's own contract not listed (AC-13 read side) |
| Contract page prompt (`Mutual NDA — Amazon`, blank type) | PASS: "This contract has no recognised type." with picker, readable in dark mode |
| Live filing / cancel / set type | NOT-RUN live (would write dev data); covered by tests: `test_nda_check.py` filing cases, wizard Cancel test, `test_contract_types.py` PUT cases |
| Walled user | NOT-RUN live; covered by `test_walled_nda_or_msa_leaves_no_trace`, `test_lawyer_behind_a_wall_sees_the_choice_and_reason_but_no_list` |

### Code review (code-reviewer, read-only): APPROVE WITH NITS

Visibility, FR-4 (filing never blocked, savepoint), audit (one row per filing/type change, none on reads or cancel), frozen rules and constitution all held.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| R1 | Low | Audit `after` payload of `intake.nda_check_continued` holds listed contract ids/kind/status; another audit reader walled off from one could see an id. | Accepted: no app route serves the audit log (checked: no route reads `AuditLog`), only DB-level access; ids are only ones the filer could see (spec audit rule met). Revisit if an audit viewer is built. |
| R2 | Nit | Stale comment above `NdaCheckCard` ("wired into the request page separately"). | Fixed (frontend-dev, 2026-10-27). |
| R3 | Nit | `nda_check` recorded when a result was on screen even without clicking Continue. | Accepted: matches FR-20 (any filing after a result shown is recorded). |
| R4 | Nit | Inline `style={{ fontSize: 12.5 }}` in `NdaCheckCard`. | Fixed: now `text-[12.5px]`, look unchanged (frontend-dev, 2026-10-27). |

### Final verdict

**DONE (with nits).** All 31 ACs pass on automated evidence (AC-28 chained step inferred, F2), every suite green apart from the one known unrelated failure, single Alembic head, reviewer APPROVE WITH NITS. Not exercised live: filing, cancel, set type and the walled-user view (test-covered). Nothing is committed.
