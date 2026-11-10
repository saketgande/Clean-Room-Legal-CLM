# Verification: Request page refinements (011-request-page-refinements)

Verifier: qa-engineer (T007) · Date: 2026-10-23 · Tree: uncommitted working tree, branch drl-saket-v2 (nothing stashed or modified)

## Verdict

Gates are green apart from the one known unrelated failure. The backend access and audit behaviour is proven live and by pytest. The page layout and measurements are the orchestrator's (no browser available to QA). Of 29 ACs: 21 PASS, 5 PARTIAL, 3 NOT-RUN, 0 FAIL. No defect was found in feature 011's own code. One pre-existing leak (D1) and one pre-existing environment quirk (D2) were found. The feature is acceptable for the backend and unit-tested frontend. It is NOT fully signed off, because dark mode, 375px width, the side-by-side mockup acceptance (AC-29) and live step actions have not been exercised.

## Gates

| Gate | Command | Result |
|---|---|---|
| Ruff | `docker compose exec backend ruff check .` | All checks passed |
| Migrations | `docker compose exec backend alembic upgrade head`; `alembic heads` | no-op upgrade; single head `0074_template_sync` |
| Backend tests | `docker compose exec backend python -m pytest -q` | 1 failed, 4603 passed, 1 skipped (412 s). The one failure is the known `tests/test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had` (uncommitted prompt_versions.py edits, predates 011) |
| Typecheck | `npm run typecheck` (frontend/) | Only the 2 accepted `.next/types/validator.ts` docstudio TS2307 errors |
| Frontend tests | `npm run test` (frontend/) | 72 files, 1858 tests passed |
| Lint | not run (hangs on an interactive wizard, per project rule) | n/a |

## Acceptance criteria

"Live" = real API on the running stack (details below). "Orch." = the orchestrator's browser measurement, cited, not re-measured by QA. Frontend test files live in `frontend/src/app/(app)/intake/` (steps, cards, overview) and `frontend/src/lib/`.

| AC | Result | Evidence |
|---|---|---|
| AC-1 order, no Steps in side column | PASS | vitest `_request-overview` "puts the cards in layout A's order in both columns, and draws one stage bar"; Orch. 1440px: main = Approval, What was asked, AI risk review, Workflow, Activity, Comments; side = Counterparty, Owner and people, Documents, Ask Aegis |
| AC-2 six tiles, 3 of 6 done, current highlighted | PASS | vitest `_request-steps` "draws one tile per step, the open one highlighted with who and the check action"; Orch.: six tiles, current has "Overdue 1 day" and "Check approval status"; `workflow-steps` "counts whole days late and says plain Overdue under a day" |
| AC-3 skipped/failed not counted | PASS | `_request-steps` "marks skipped and failed steps without counting them as done"; overview "shows a skipped step as skipped, not as done", "counts only the steps that applied, and says how many were skipped"; lifecycle-view "does not call a skipped step done" |
| AC-4 manage controls behave as before, same audit entries | PARTIAL | Expand-hidden-until-opened, no raw data, send-back needs a chosen step: `_request-steps` "hides the review result until opened", "keeps the approval chain with its decisions behind its link", "collapses the hand-off trace and uses plain step names", "opens the send-back form ... disabled until a step is picked"; overview "has one Send back form..." and "offers each action once to a manager (AC-18)". NOT exercised: using the controls against the real backend and comparing audit rows before and after |
| AC-5 requester sees no controls | PASS (unit) | `_request-steps` "shows no links or buttons when there is nothing to open"; overview "shows a requester the tiles and every state but none of the controls (AC-5, AC-26)". Not exercised in a browser as a requester |
| AC-6 narrow width, no horizontal scroll | NOT-RUN | No browser; 375/390px not measured by anyone |
| AC-7 merged request+contract activity with who/when | PASS | pytest `test_happy_path_sentences_order_sources_and_who`, `test_rule_based_sentences`; live REQ-7705: 42 items, sources contract 35 / request 7, sentences e.g. "AI review complete · 1 issue found", "Escalated — SLA breached", who = "AI"/"Aegis"/person name. Not every event kind listed in AC-7 was individually confirmed on REQ-7705 (the pytest covers each kind) |
| AC-8 day grouping, newest first | PASS | vitest cards "groups by day, newest day first and newest event first, with who and time"; `request-facts` "keeps the given order, one heading per local day, and adds the year only for another year"; live: all 42 `at` values strictly newest-first; Orch.: grouped by day |
| AC-9 no codes, ids or raw data | PASS | pytest `test_no_sentence_ever_shows_a_code_an_id_or_json`, `test_unknown_contract_event_reads_contract_updated_and_is_system`, `test_every_event_type_written_in_the_code_has_a_sentence`; live regex scan of all 42 sentences/who for UUIDs, braces, dotted codes: 0 hits; vitest "has no system toggle when no event is hidden, and no ids in the DOM" |
| AC-10 hand-offs and comments still listed | PASS | pytest happy path (hand-off + run comment in the list); overview "carries a time in Comments, and Activity shows what the activity endpoint sent" |
| AC-11 system events hidden, toggle, absent when none | PASS | vitest "keeps system events hidden until the toggle, and hides them again", "has no system toggle when no event is hidden"; live: 25 of 42 flagged `system`; Orch.: "Show system details (25)" |
| AC-12 empty = one line; load failure = line + retry | PASS (unit) | vitest "says there is no activity, with no system toggle", "shows a loading line and an error line with Retry, never a blank card"; overview "keeps the rest of the page when Activity cannot load" |
| AC-13 walled contract's events absent | PASS | live (below, check b): walled viewer got 3 items before the wall and only "Request filed" after; admin still 3; pytest `test_walled_viewer_gets_request_events_but_no_contract_events_and_no_denied_row`, `test_requester_sees_own_events_and_handoffs_but_not_run_comments`; live another member's request -> 404 |
| AC-14 first 20 then show earlier | PASS (unit) | vitest "shows 20 at a time and Show earlier activity reveals the rest"; pytest `test_capped_at_500_newest_and_truncated` |
| AC-15 name + summary from visible set | PASS | live: name "Initech Software Ltd", summary and note equal the list lengths (check a); vitest "shows the name, the summary line and 5 + 5 items..."; `request-facts` "builds the line and leaves out zero parts" |
| AC-16 contract items, newest first, click opens | PARTIAL | live: items carry title, type, stage, value, date; order live-first then newest first (all 5 live, 10-09 to 09-29); every listed contract opens (200) for admin; vitest "links a contract to its page with stage, type, value and no raw codes"; Orch. rows as documents. Real click navigation in a browser not exercised |
| AC-17 request items, newest first, click opens | PARTIAL | live: 6 items newest first, each opens (200); vitest "calls onOpenRequest on a plain click and leaves modified clicks to the browser", overview "opens another request from the Counterparty card through onOpenRequest". Real click not exercised in a browser |
| AC-18 own contract/request excluded | PASS | live REQ-7705: own contract id and own request id in neither list; pytest `test_own_contract_and_other_counterparties_are_excluded_and_nda_counted` |
| AC-19 name matching unchanged | PASS (pytest only) | `test_name_match_is_unchanged_and_the_sql_prefilter_never_drops_a_hit` (parametrised, incl. "Umbrella Corp"/"Umbrella Corporation"). Not exercised live |
| AC-20 5+5 with show more, summary = whole set | PASS | vitest "shows the name, the summary line and 5 + 5 items with Show more revealing the rest"; pytest `test_lists_are_capped_at_100_but_counts_cover_the_whole_visible_set`; Orch.: 5 contracts + 5 requests + "Show 1 more" |
| AC-21 collapse one side / nothing on file | PASS (unit) | vitest "says so in one line when one side is empty", "with nothing on file shows only the name and the note, no counts" |
| AC-22 walled contract not inferable | PASS | live (check b): for the walled viewer contracts 2 -> 1, "active" unchanged by the wall (1), requests 11 -> 9, note "1 prior contract(s)", and the walled title, id and value (12345678) appear in no counterparty or activity payload. pytest `test_walled_contract_is_absent_from_list_counts_note_and_every_request_payload`. No total value exists in the payload (`test_money_is_per_contract_and_no_total_exists`). See D1 for the one residue |
| AC-23 all hidden = new counterparty | PASS (pytest only) | `test_every_matching_contract_hidden_reads_as_a_new_counterparty`; vitest "with nothing on file...". Live: a viewer lacking `contract:read` got contracts [] and no contract count (check e) but not a fully-empty case |
| AC-24 requester own only; staff; walled request | PASS | live (check c, b): requester saw only QA011-M0 and QA011-WM (own), none of the 9 others'; staff saw others'; the two requests tied to the walled contract dropped for the walled user (11 -> 9). pytest `test_requester_sees_only_their_own_earlier_requests_and_staff_sees_all`, `test_request_tied_to_a_walled_contract_is_dropped_for_the_walled_viewer` |
| AC-25 listed items open | PASS | pytest `test_every_listed_item_opens_for_the_same_viewer`; live: all 5 listed contracts and 6 listed requests return 200 for admin on REQ-7705; walled contract returns 404 for the walled user and is not listed |
| AC-26 each role sees only its own | PARTIAL | live: requester (own events/handoffs only, others' request 404), staff-with-wall, staff-without-contract:read, admin each verified; pytest covers the same. Approver and owner views and the absence of action controls per role in the browser are not exercised |
| AC-27 light and dark | NOT-RUN | Orch. measured light only. Dark mode of the new cards not exercised. vitest "does not use a class other pages style as a clipped one-line label" only guards the `cpbody` rename |
| AC-28 no audit/timeline from reads; step action once | PARTIAL | live (check g): audit_log 21058 -> 21058 and resource_timeline_event 16313 -> 16313 around 7 reads (activity, counterparty, detail, list across 3 users); pytest `test_reading_writes_no_audit_and_no_timeline_rows`, `test_route_shape_no_party_and_nothing_written`. Taking a step action from the new card and counting its entries: not exercised |
| AC-29 side by side with mockup, six states | NOT-RUN | Needs the user's acceptance; only REQ-7705 measured (Orch.). The FR-30 states (no workflow, human step, rejected, complete, no contract) were not walked |

Tally: PASS 21 (AC-1,2,3,5,7,8,9,10,11,12,13,14,15,18,19,20,21,22,23,24,25; several are unit or pytest evidence only, as marked), PARTIAL 5 (AC-4,16,17,26,28), NOT-RUN 3 (AC-6,27,29), FAIL 0.

## Live access checks

Setup note. The dev compose sets `DISABLE_RBAC=true`, which makes every user an admin (`admin_panel:access` for everyone), so ordinary-user checks are meaningless on :8000. For checks b to e I started one transient container from the same image with `DISABLE_RBAC=false` on :8001 (`docker compose run -d --name qa011-rbac -p 8001:8000 -e DISABLE_RBAC=false backend uvicorn ...`), same database, and removed it afterwards. Check a, f, g, h and the click-through were also run with the admin. All responses are from the real API; excerpts below.

**(a) Staff, REQ-7705 `/counterparty`: PASS.** HTTP 200, `name: "Initech Software Ltd"`, before my test data `summary {contracts 5, active 2, requests 6}`, `note "Known counterparty: 5 prior contract(s), 3 NDA on file, 6 prior request(s)."`, 5 contracts and 6 requests listed (counts equal list lengths), own contract `57f476a3-...` and own request absent. (After my fixtures were added the same call read 7 / 3 / 11 with 7 and 11 items; they were removed again.)

**(b) Ethical wall: PASS (with D1).** Fixtures: a non-admin legal_reviewer user owning two Initech contracts ("QA011 visible deal", "QA011 walled secret", value 12345678) plus requests; wall created through `POST /ethical-walls` with the legal user as principal on the walled contract.
- Before the wall, legal user: contracts 2 (both), requests 11, note "2 prior contract(s), 11 prior request(s)"; activity of a request about that contract: "AI review complete · 3 issues found", "Stage changed to Review", "Request filed".
- After the wall, same user: contracts 1 (only "QA011 visible deal"), `active` 1, requests 9, note "Known counterparty: 1 prior contract(s), 9 prior request(s)." Walled title, id and value appear nowhere in the counterparty or activity payload. Activity shrank to one item, "Request filed" (request event kept, contract events gone). `GET /contracts/{walled id}` for that user: 404.
- Across 73 items of `GET /intake/requests` for the walled user, the walled title/id/value appeared only inside the two requests that are themselves tied to that contract (see D1); no other request or `/intake/my-work` carried it.
- Admin (not a wall principal) still saw the contract: counterparty contracts 8 incl. the walled one, activity still 3 items, `GET /contracts/{id}` 200.

**(c) Requester: PASS.** Non-staff member (`intake:create`, no `intake:read`) on their own request: requests [QA011-M0, QA011-WM] (both their own; the 9 requests by others not listed or counted; "2 prior request(s)"); contracts [] (they own none); another member's request id: `/counterparty` and `/activity` both 404 "Request not found".

**(d) Other organisation: PASS.** A request created in a second organisation: `/counterparty` and `/activity` return 404 "Request not found" for the admin and for the staff user (pytest `test_route_404_for_other_members_request_and_other_org_and_403_without_permission` and `test_route_is_404_for_another_members_request_and_another_org` also cover it).

**(e) No `contract:read`: PASS.** A temporary role with only `intake:read`, `intake:create` was given to the staff user who owns a visible Initech contract: `/counterparty` returned `contracts: []`, `summary.contracts 0`, `active 0`, note "Known counterparty: 9 prior request(s)." (no contract count). The user's role was restored and the temporary role deleted.

**(f) Activity wording: PASS.** REQ-7705 `/activity`: HTTP 200, 42 items, 25 `system: true`, `truncated: false`, keys `at,id,sentence,source,system,tone,who`, strictly newest-first, no UUID, braces or dotted codes in any sentence or `who`. Examples: "Escalated — SLA breached" (Aegis), "AI review complete · 1 issue found" (AI), "AI started: Playbook review" (system), "Contract Brain updated" (system).

**(g) No audit/timeline writes by reads: PASS.** `select count(*) from audit_log` 21058 before and after; `resource_timeline_event` 16313 before and after, around seven GETs (activity, counterparty, detail, list) as admin, staff and requester (tokens obtained beforehand).

**(h) REQ-7705 `GET /intake/requests/{id}`: PASS.** `screening` keys are `["counterparty","parties","checked_at","status"]`; the string "relationship" appears nowhere in the response.

## Defects

**D1 (pre-existing, not introduced by 011; low to medium). The request detail and list payloads carry the contract id and title of a contract the viewer is walled off from.**
- Repro: contract C is walled for user U. Request R is about C and is one U may see (staff see all requests). As U: `GET /intake/requests/{R}` and `GET /intake/requests` include `"contract_id": "<C id>"` and `"contract_title": "<C title>"` (set in `backend/app/intake/service.py` around line 703 from `_contract_title`), while `GET /contracts/{C id}` returns 404 for U. 011's Counterparty and Activity do not leak it (verified). Against FR-24's "no inference of a contract they may not see", the Counterparty card cannot be used to infer it, but the request page header/list still names it. Likely belongs to a separate fix (blank `contract_id`/`contract_title` when the viewer fails the contract access check); out of 011 scope per the spec's "no change to who can see what", so flagged for a decision.

**D2 (pre-existing, environment; informational). A user created through the invitation flow has no role rows.** Repro: admin `POST /users/invitations` (role_name member or legal_reviewer), then `POST /auth/invitations/accept`: `GET /auth/me` shows `roles: []`, `permissions: []` and `active_role_name: null` even though `active_role_id` is set, until an admin calls `PUT /roles/user/{id}`. Unrelated to 011; worked around with the roles API.

No defect found in the 011 endpoints or UI code paths that were exercised.

## Clean-up record

Created, all through the app's API unless noted:
- Users: `qa011-legal_reviewer@example.com`, `qa011-member@example.com` (invitation + accept + `PUT /roles/user`); two invitations.
- Ethical wall "QA011 wall" (`POST /ethical-walls`); temporary role `qa011_nocontract` (`POST /roles`).
- Via a one-off script run inside the backend container with the ORM (the only way to get a contract with a chosen counterparty and fixed ids without launching AI jobs): 2 contracts ("QA011 visible deal", "QA011 walled secret"), 6 requests (refs QA011-*), 6 timeline events, one extra organisation "QA011 Other Org" with one user and one request.
- Transient container `qa011-rbac` on :8001 (`DISABLE_RBAC=false`).

Removed: the wall (`DELETE /ethical-walls`, 204); the temporary role (`DELETE /roles`, 204); the container (`docker rm -f qa011-rbac`); by a one-off script inside the backend container, all QA rows above plus their dependent rows (6 requests, 2 contracts, 3 users incl. the other-org user, 1 organisation, 21 refresh tokens, 3 user_role rows, the timeline events), and the 2 invitations (SQL delete). Final check by psql: 0 rows for users, requests, contracts, organisation, walls, roles and invitations matching `QA011`/`qa011`; 0 `qa011` containers. Changes made and restored: the staff user's role was switched to the temporary role and back (user later deleted). The dev compose files, code and the working tree were not touched.

Left behind (immutable): `audit_log` rows written by this session (logins, invitation created/accepted, role changes, wall create/delete), which stay by design; the global audit and timeline counts therefore differ from the figures in check g, which were taken before the clean-up. The pytest run also writes rows into the dev DB, as noted in CLAUDE.md.

## Not exercised

- Dark mode of the Workflow, Activity and Counterparty cards (AC-27).
- 375/390px width of the new cards, horizontal scroll, tile and link reachability (AC-6, FR-7, FR-29).
- Requester, approver and owner views in a browser; using Send back, Reassign, Check approval status and clicking list items in a browser; the audit/timeline entries of a step action taken from the new card (AC-4, AC-26, AC-28).
- Side-by-side against the mockup and the six FR-30 request states, and the user's acceptance of any difference (AC-29).
- Name-matching variants ("Umbrella Corp"/"Umbrella Corporation") against the running app (AC-19); a live viewer with every matching contract hidden (AC-23).
- Known cosmetic from the orchestrator's run: the Counterparty card is tall (about 875px) with 5 + 5 rows.
- Proposal: if the repo is to cover AC-6, 27 and 29 automatically it needs a browser E2E tool (Playwright) under `tests/e2e/`; not installed.

## Orchestrator measurements cited (not re-measured by QA)

At 1440px: main column 868px, side 300px; main order Approval, What was asked, AI risk review, Workflow, Activity, Comments; side order Counterparty, Owner and people, Documents, Ask Aegis; no error strips or raw codes; Workflow card with six tiles, current tile highlighted with "Overdue 1 day" and a "Check approval status" button, four links; Activity 42 items, 25 system hidden behind "Show system details (25)", grouped by day newest first; Counterparty card after the `cpbody` fix: body 298px in a 300px card, white-space normal, nothing clipped, 5 contracts (stage pill, type, value, date), 5 earlier requests and "Show 1 more".
