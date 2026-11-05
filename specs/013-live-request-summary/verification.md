# Verification: Live request summary and extra instructions (013-live-request-summary)

Verified 2026-10-27 by qa-engineer (T008). Working tree uncommitted. Browser items were not exercised (no browser tools); the orchestrator does them.

Legend: BE = `backend/tests/test_request_summary.py` (or `test_intake_drafts.py`); LIB = `frontend/src/lib/request-summary.test.ts`; ED = `frontend/src/lib/summary-edits.test.ts`; BOX = `intake/_summary-box.test.tsx`; WZ = `intake/_agreement-wizard.test.tsx` (describe "the request in words"); RS = `intake/_request-summary.test.tsx`; RO = `intake/_request-overview.test.tsx` (describe "the request summary section").

## Gates

| Gate | Command | Result |
|---|---|---|
| Ruff | `docker compose exec backend ruff check .` | All checks passed (the earlier `contracts/models.py` lint caveat did not show; the file now passes) |
| Alembic | `docker compose exec backend alembic heads` | one head: `0076_request_summary` |
| Backend suite | `docker compose exec backend python -m pytest -q` | 4726 passed, 1 skipped, 1 failed in 8m07s. The one failure is the known unrelated `test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had` |
| Typecheck | `npm run typecheck` (frontend/) | only the two known `.next/types/validator.ts` docstudio errors (TS2307) |
| Vitest | `npm run test` (frontend/) | 81 files, 2067 tests passed |

## Acceptance criteria

| AC | Evidence | Result |
|---|---|---|
| AC-1 | LIB "AC-1: an NDA answered alone says so; a half-filled pair falls back to one sentence each"; WZ "sits in the aside on the first step ... and follows the answers"; LIB "new agreement: a mutual NDA reads as one sentence and the NDA terms". Browser: not exercised | PASS (browser not exercised) |
| AC-2 | WZ "a summary that cannot be built still files, with text null (AC-2, FR-9)"; LIB "never throws on odd answers". See finding F1 for the on-screen note | PASS (see F1) |
| AC-3 | LIB "AC-3: an empty form is gaps only, worded by kind, with the required flag"; BOX "is a named section whose gaps are told apart by words, not colour alone (FR-4)". Look: not exercised | PASS (browser not exercised) |
| AC-4 | LIB "AC-4: a question that does not apply is never mentioned, and appears when it does"; "the guard" cases (every question has exactly one rule, all nine forms) | PASS |
| AC-5 | LIB "AC-5: six deliverables are three names and a count ..."; "statement of work"; "moneyText keeps the wizard's rendering exactly" | PASS |
| AC-6 | WZ "on the review step, shows the editor AND the answers list, in the planned order (AC-6, FR-12, FR-38)" | PASS (browser not exercised) |
| AC-7 | ED "AC-7: undo clears edits and open offers but keeps instructions and accepted answers"; BOX "disables Undo my edits until there is something to undo, then calls back (FR-13)" | PASS |
| AC-8 | ED "AC-8: English law offers India to England and Wales and leaves every answer alone"; BOX "puts an offer in a labelled group tied to its line" | PASS |
| AC-9 | ED "AC-9: dates follow the browser's order; a duration in words is read"; ED "parseDate (pinned table)", "parseMoney (pinned table)"; "an end date before the start date is not offered" | PASS |
| AC-10 | ED "AC-10: a reworded free-text answer is offered as the new answer, with or without the prefix" | PASS |
| AC-11 | ED "AC-11: a new party is never a pick; kept as an instruction naming the current party" | PASS |
| AC-12 | ED "AC-12: term and law give two offers; an unknown law gives one offer and a note" | PASS |
| AC-13 | WZ "accepting an offer changes the answer the same way a control does: the routing is asked again (AC-13, FR-20)". NDA-check reaction and the live panel: not exercised | PASS (browser not exercised) |
| AC-14 | ED "AC-14: declining keeps the edit as an instruction and the answer unchanged"; WZ "an open offer at Submit is filed as an instruction with a note" | PASS |
| AC-15 | ED "AC-15: the counterparty and our entity are never in a patch"; BE `test_filing_keeps_instructions_and_audits_counts_but_not_text` (no register rows). Register-row non-creation is by construction (no edit path calls a register endpoint) | PASS |
| AC-16 | ED "AC-16: a form change withdraws the offer and the instruction notes the new answer"; WZ "a resumed draft whose answer has since changed does not offer the old change (FR-36, FR-23)" | PASS |
| AC-17 | ED "AC-17: deleting an optional line offers to clear the answer"; "AC-17: deleting a required line brings it back and keeps nothing"; BOX "calls a clearing offer 'Clear the answer?' with a Clear answer button". Table/register-pick lines differ, see F3 | PASS (see F3) |
| AC-18 | BE `test_filing_keeps_instructions_and_audits_counts_but_not_text`, `test_text_only_and_no_summary`; WZ "files with the sentences as text, and a pending instruction it was never asked to add (FR-34)". Live psql of a filed row: not exercised (would write dev data) | PASS (live filing not exercised) |
| AC-19 | WZ "Add moves the typed line into the list and Remove takes it out (FR-29)"; BOX "lists each instruction in full with its note and a numbered Remove" | PASS |
| AC-20 | RS "shows the text, numbered instructions, the differs note, writer and time"; RO "shows the instructions inside the page"; BE `test_reading_the_summary_follows_request_visibility_and_writes_nothing`. Live GET proved the endpoint only for the all-null shape (below) | PASS (browser not exercised) |
| AC-21 | BE `test_instructions_never_reach_the_request_itself` (only `models.py` and `request_summary.py` mention the column; two filings differing only by instructions are identical in description, answers, label, priority) | PASS |
| AC-22 | WZ "over the word limit is refused with the way out, nothing is cut and nothing is sent (AC-22, FR-32)", "exactly at the limit files (AC-22)"; BOX "warns near the limit, refuses over it, and names the way out"; BE `test_word_limit_is_the_only_refusal`; ED "AC-22: wordCount ...". 2,100-word paste in a browser: not exercised | PASS (browser not exercised) |
| AC-23 | BE `test_filing_keeps_instructions_and_audits_counts_but_not_text` (text stored as filed), `test_accepted_changes_not_matching_the_filed_answer_are_dropped`; WZ "files with the sentences as text ..."; RS (saved text shown). Real NDA draft from the filed request: not exercised | PASS (draft check by construction/static guard only) |
| AC-24 | WZ "an open offer at Submit is filed as an instruction with a note, with no prompt (AC-24, FR-34)"; ED "AC-24: finalize checks unchecked edits, declines open offers and keeps pending text, without blocking" | PASS |
| AC-25 | WZ "Save as draft sends the box, and resuming shows an instruction and an open offer (AC-25, FR-36)"; BE (test_intake_drafts.py) `test_summary_box_round_trips_and_is_replaced`, `test_a_draft_saved_without_a_summary_box_has_none` | PASS |
| AC-26 | BE `test_a_retried_filing_keeps_the_first_record`. Double-click in a browser: not exercised | PASS (browser not exercised) |
| AC-27 | LIB "AC-27: the note to approvers is a read-only sentence"; WZ "the note to approvers stays an answer and never becomes an instruction (FR-38)" | PASS |
| AC-28 | BE `test_reading_the_summary_follows_request_visibility_and_writes_nothing` (requester and staff 200; other plain member and other org 404, same as the request). Ethical-wall case specifically: relies on the shared `get_request` gate, no wall-specific 013 test. Live as plain employee: not exercised (dev runs `DISABLE_RBAC=true`, so a live probe would prove nothing) | PASS (wall case by shared gate) |
| AC-29 | BE `test_filing_keeps_instructions_and_audits_counts_but_not_text` (one `intake.summary_filed` audit row and timeline row, counts only, no text); `test_reading_the_summary_...` (GET writes none). Live psql of a filed row: not exercised | PASS (live filing not exercised) |
| AC-30 | BOX "is a fieldset with a legend, a textbox per sentence line ...", "checks a line on Enter and on blur ...", "has one polite status region that carries the announcement (AC-30)", "is not a live region"; WZ "has a link to the box for small screens ...". Keyboard-only pass, VoiceOver, 390px: not exercised | PARTIAL (roles tested; browser not exercised) |
| AC-31 | BE `test_reading_the_summary_follows_request_visibility_and_writes_nothing` (foreign-org user gets 404) | PASS |
| AC-32 | BE `test_patch_cannot_touch_the_summary_and_no_write_route_exists`; live `POST .../summary` returned 405; RS "has no edit controls once filed" | PASS |

Counts: 31 PASS (13 of them with browser look/behaviour not exercised), 1 PARTIAL (AC-30), 0 FAIL, 0 NOT-RUN. Wording, spacing, dark mode and 390px for every UI item remain for the orchestrator's browser pass.

## Live API check (after `docker compose restart backend`; DISABLE_RBAC=true in dev)

| Check | Result |
|---|---|
| Login `admin@example.com`, `GET /api/v1/intake/requests/acca720f-.../summary` (an existing request) | 200 `{"text":null,"at":null,"by_name":null,"instructions":[]}` (all-null shape for old requests) |
| `GET .../requests/does-not-exist/summary` | 404 "Request not found" |
| GET without a token | 401 "Authentication required" |
| `POST .../summary` | 405 (no write route) |
| GET writes nothing | psql counts before and after three GETs: audit_log 22169 / 22169, resource_timeline_event 17087 / 17087, requests with a filed summary 0 / 0 |
| Plain employee, ethical wall, other org | not exercised live (RBAC is off in dev; covered by BE tests above) |

No dev data was created or changed. Write paths (filing, drafts) are proven by tests only.

## Findings

| # | Severity | Finding |
|---|---|---|
| F1 | Low | (a) `buildSummary` catches its own errors and returns `[]`, so an internal rule bug shows an empty list, not the "The summary could not be built. You can still file." note (that note only appears if something throws past it; `_agreement-forms.tsx:476` catches and the box has the text). FR-9/AC-2 are met either way (filing works; `text` is null when empty? the wizard test covers the throwing path only). The gap: a swallowed error is silent to the requester, and an empty summary may be filed as an empty/absent text. Not a spec violation; the spec requires only that filing is unaffected. Suggest: have `buildSummary` rethrow or return a flag so the note shows. |
| F2 | Low | (b) DEC-2 wording (`attachHintFor`: "shorten them — this form has no place to attach a file" for the signed-outside-system and cancel forms) is implemented but no test pins it; BOX only passes a fixed `attachHint`. Suggest one wizard test over the limit on the cancel form. |
| F3 | Low | (c) Table lines never offer "Clear the answer?" (kept as instruction "Remove this line: ..."), and a deleted register-pick line just returns. This matches the plan (D6, D7, FR-22 forbid changing picks) and FR-24 is about answers a requester can clear; but FR-24 literally says deleting a whole generated sentence offers "Clear the answer?". For table answers this is a documented, test-pinned narrowing ("a declined clear is kept as a 'Remove this line' instruction", "deleting a register-pick line never offers to clear it"). Accept, or confirm with the user. |
| F4 | Info | (d) A textarea answer's newlines become spaces in its sentence line. Cosmetic; the answer itself is unchanged, and the line is one text box per sentence by design (D2). Accept. |
| F5 | Info | Earlier-reported `contracts/models.py` lint failure was not reproduced: whole-repo ruff passes now. |
| F6 | Info | Live checks of write paths (filing with instructions, audit row contents, walled-user 404 at the API) were not run on the dev stack to avoid creating data; they are covered by backend tests that roll back. |

No FAIL-level defects. The one failing backend test and the two typecheck errors are pre-existing and unrelated.

## Verdict

PASS for the automated gates and for 31 of 32 criteria; AC-30 is PARTIAL pending the browser pass (keyboard, VoiceOver, 390px, light/dark, wizard look and offers flow). No blocking defects; F1 to F3 are low-severity deviations to accept or fix. Ready to proceed once the orchestrator's browser checks are done.

---

## Orchestrator browser check (2026-10-27, built-in browser, dev data, nothing filed)

| Item | Result |
|---|---|
| Live box on step 1, empty form | PASS: "Your request in words" lists every applying question as a gap, "(required)"/"(optional)" in words; footer "Built from your answers. Updates as you answer." |
| Live update (AC-1) | PASS: choosing NDA → "This is an NDA."; picking Initech Software Ltd → "The other side is Initech Software Ltd." |
| Aside order (FR-10) | PASS: NDA check → Your request in words → What these answers do → Where this request goes |
| Review-step editor, offers, extra instructions, request page section, keyboard/screen reader, 390 px, dark mode | NOT-RUN in the browser (reaching Review needs every required answer on three steps); covered by `_summary-box.test.tsx`, `_agreement-wizard.test.tsx` ("the request in words"), `_request-summary.test.tsx` |

Ruff note: the out-of-scope lint failure in `backend/app/contracts/models.py` seen mid-run (edited outside this feature at 15:08) no longer reproduces at T008.

---

## /verify consolidation (orchestrator, 2026-10-27)

### Follow-ups since T008

- **F1 fixed**: `buildSummary` no longer swallows rule errors, so the wizard shows "The summary could not be built. You can still file." Test: "a broken rule reaches the caller…" in `request-summary.test.ts`. Odd answers still never throw (fuzz test kept).
- **F2 fixed**: the over-limit wording for regularize/cancellation is pinned by two `_agreement-wizard.test.tsx` DEC-2 tests. Pinning exposed a copy bug ("Shorten them, or shorten them — …") in both the filing notice and the `ExtraInstructions` bar; fixed with `attachHint: string | null` and a shared `NO_ATTACH` sentence ("Shorten them — this form has no place to attach a file."), plus a `_summary-box.test.tsx` case.

### Suites (exact commands)

| Gate | Command | Result |
|---|---|---|
| Lint | `docker compose exec backend python -m ruff check .` | All checks passed |
| Migrations | `alembic upgrade head && alembic heads` | single head `0076_request_summary` |
| Backend tests | `docker compose exec backend python -m pytest -q` | 4734 passed, 1 skipped, 1 failed: `test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had` (known, unrelated) |
| Typecheck | `cd frontend && npm run typecheck` | only the 2 known stale `.next/types/validator.ts` docstudio errors |
| Frontend tests | `cd frontend && npm run test` | 81 files, 2070 passed |

### Code review (code-reviewer, read-only): APPROVE WITH NITS

Held: only a click (`acceptOffer`) changes an answer; register picks and table lines never offered; drafting/routing/triage never read `filed_summary`; `record_summary` in a savepoint on the request's own session; audit has counts and old/new only; 422 before anything is saved; duplicate retry stores nothing twice; no write route; GET behind `get_request`; drafts round-trip; contract matches plan.

| # | Severity | Finding (all still need a click; nothing changes silently) | Owner |
|---|---|---|---|
| R1 | Low | Money scan path ignores hedge words: "around 70 lakh" is offered as INR 70,00,000 (template path rejects "approx."). `summary-edits.ts:205-208` | frontend-dev |
| R2 | Low | Comma amounts accept invalid grouping: "1,50" → 150, "1,234,56". `summary-edits.ts:127` | frontend-dev |
| R3 | Low | A contradicting edit on a Yes/No line ("No personal data … apart from names" when the answer is Yes) is kept as an instruction but loses its "differs" note. `summary-edits.ts:412-414` | frontend-dev |
| R4 | Low | A declined offer can reappear when the line is blurred again (`checkLine` resets `declined`). `summary-edits.ts:369-434` | frontend-dev |
| R5 | Low | Negation read as the option: "Don't host in the US" offered as United States. `summary-edits.ts:276-282` | frontend-dev |
| R6 | Nit | Resumed draft `summary_box` not shape-checked; server accepts any dict. `_agreement-forms.tsx`, `schemas.py` | frontend-dev / backend-dev |
| R7 | Nit | Stale header comment in `request-summary.ts:9` ("never throws") after F1. | frontend-dev |
| R8 | Nit | An accepted "Clear the answer?" is not in the audit's `answers_changed` (blank keys are removed before the check); matches the plan, possible plan gap. | backend-dev / user |

### R1–R5 and R7 fixed (frontend-dev, 2026-10-27, user-approved)

| # | Fix | Test |
|---|---|---|
| R1 | One shared `HEDGE` check (about, around, approx, roughly, up to, over, under, at least, at most, more/less than, maybe, ~…) on both the template and scan money paths: no offer for hedged amounts | R1 test in `summary-edits.test.ts` |
| R2 | Comma numbers accept only Western (1,234,567) or Indian (12,34,567 / 1,50,00,000) grouping; "1,50", "1,234,56", "12,34", "1,2345" → no offer | rows added to the pinned money tables |
| R3 | A Yes/No-line edit agrees with the answer only if it equals the sentence, or contains it with no extra negation and not the other option's sentence; otherwise kept with the differs note | R3 test |
| R4 | `checkLine` carries declined keys forward while the line's text is unchanged; a declined offer no longer reappears on blur | R4 test |
| R5 | Select scan path: a negation word (no, not, don't, never, without, except, other than…) outside the option wording → no offer, instruction with differs note | R5 test |
| R7 | `request-summary.ts` header comment corrected (odd answers never throw; a broken rule does, by design) | — |

Limit: R4's declined keys survive only while the line's text is unchanged; typing again re-checks from scratch (the frozen `check` type has no room for the declined value).

Re-run: `summary-edits.test.ts` + `request-summary.test.ts` 131 passed (orchestrator); full `npm run test` 81 files, 2080 passed and typecheck clean apart from the 2 known docstudio errors (agent-reported). R6 and R8 remain open nits (not asked).

### Final verdict

**DONE (with nits R6, R8).** R1–R5 and R7 fixed. 31 of 32 ACs pass on tests, AC-30 partial (no hands-on keyboard / screen-reader / 390 px pass); suites green apart from the one known unrelated failure; single Alembic head; reviewer APPROVE WITH NITS. R1–R5 are "never guess" edge cases (FR-18) where the box makes an offer it shouldn't; the requester still has to click, so no answer changes on its own. Nothing is committed.
