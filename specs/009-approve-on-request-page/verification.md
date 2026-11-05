# Verification: Approve and reject on the request page (009)

Verified 2026-10-22 by qa-engineer (task T007). Backend restarted first (`docker compose restart backend`).

## Gates

| Gate | Command | Result |
|---|---|---|
| Lint | `docker compose exec backend ruff check .` | All checks passed |
| Migrations | `docker compose exec backend alembic upgrade head`; `alembic heads` | no-op, single head `0074_template_sync` |
| Backend tests | `docker compose exec backend python -m pytest -q` | 4565 passed, 1 skipped, 1 failed. The one failure is `tests/test_ai_gateway_phase2.py::test_moved_prompts_are_byte_for_byte_what_the_modules_had`. It is pre-existing and unrelated (uncommitted `prompt_versions.py` edits that predate 009). Not stashed or touched. |
| Feature tests | `tests/test_approval_card.py` + `tests/test_approval_decide_entry_points.py` (-v) | 49 of 49 pass (30 + 19) |
| Frontend typecheck | `npm run typecheck` | Only the two known stale `.next/types/validator.ts` docstudio errors |
| Frontend tests | `npm run test` | 66 files, 1763 tests, all pass |

`npm run lint` was not run, by instruction.

## Live walk: setup and limits (read before trusting "live")

- Stack: Docker, API http://localhost:8000/api/v1. Real HTTP calls with real bearer tokens for four new users. No demo mode.
- Seeded through a script run in the backend container. It uses the ORM, because there is no API to create a request already sitting on an approval step. It creates:
  - users `qa9-fin`, `qa9-legal` (role approver), `qa9-req`, `qa9-other` (role member), password `qa9-password-123`;
  - teams "QA9 Finance" and "QA9 Legal";
  - six requests QA9-A..F, each with an approval rung or ladder and a one-step Approval run.
- I put five existing dev contracts into stage `approval` with SQL: Hooli SaaS 3880e101 (2 critical, 4 high), Hooli MSA 1904014d (1/1), QA Acme MSA 77cd8ff2 (2 high), Globex MSA 63500a96 (2 high) and the clean Northwind NDA 92f3fbae. `can_decide` is false on any contract outside the approval stage (existing gate, `_can_decide_approval`). The dev DB is left with this data.
- `admin@example.com` has no team, so it is the "unassigned admin".
- The dev stack runs with `DISABLE_RBAC=true`. Permission gating by role (`approval:decide`, `workflow:update`) is therefore NOT exercised live. In dev a plain member could PATCH a workflow (200), which is expected under that flag. Assignment-based refusals are enforced regardless and were exercised.
- The browser tools (`mcp__Claude_Browser__*`) are not available in this agent session. No browser walk, screenshots or dark-mode check were done. Every UI item below is covered by vitest only.
- Login is rate limited to 10 per minute; tokens were cached.

## AC map

PASS = executed and observed. "Live" = real API against the running stack. "Test" = named pytest or vitest case that ran green in the gates above.

| AC | Evidence | Result |
|---|---|---|
| AC-1 | Live: request A, rung 1 chain as `qa9-fin` gives `can_decide: true`. Test: `test_assigned_approver_can_decide_until_they_have`, vitest F1. UI buttons not seen in a browser. | PASS (API and vitest) |
| AC-2 | Live: requester, unrelated member and unassigned admin each get 200 on chain and summary, with `can_decide: false` on every rung. Test: `test_requester_sees_the_chain_but_cannot_decide`, vitest F2. A read-only line in the browser was not seen. | PASS (API and vitest) |
| AC-3 | Live: after `qa9-fin` approved, their chain shows `can_decide: false`. Test: B3 `...until_they_have`. | PASS |
| AC-4 | Live: decision POST on A rung 1 as requester, other, unassigned admin, and the other rung's approver gives 403 "You are not assigned to decide this approval", with no state change. Test: `test_refuses_an_approver_who_is_not_assigned[tool,route]`, `test_an_admin_who_is_not_assigned_cannot_decide[tool,route]`. | PASS |
| AC-5 | Test only: `test_another_orgs_user_gets_nothing`, `test_other_orgs_user_cannot_read_the_chain`. The dev DB has one organization, so a cross-org call was not run live. | PASS (tests only) |
| AC-6 | Live: `job_run` row count 12192 before and after two summary calls. Test: `test_summary_starts_no_ai_work`. | PASS |
| AC-7 | Live: request E, value tagged `verified` after the contract's `field_sources.value_amount` was set to `user`; liability tagged `extracted`. Test: `test_value_is_verified_and_a_clause_is_extracted`. | PASS |
| AC-8 | Test: `test_missing_facts_are_null_not_invented`. Live corroboration: missing facts come back `value: null, source: null` (D liability and renewal, A renewal). The client maps null to "Not available" (vitest F3). The literal "no payment clause" case was not built live. | PASS (test plus live null behaviour) |
| AC-9 | Test only: `test_stale_and_older_version_clauses_and_renewals_are_skipped`. | PASS (test only) |
| AC-10 | Live: liability and payment terms returned as quoted clause text, `quote: true`, shortened, no number. Test: `test_snippet_quotes_and_shortens_long_clauses`, vitest F4. | PASS |
| AC-11 | Live: request A (step view finance) returns exactly value, term, payment_terms, liability, renewal. Test: `test_profile_field_lists`. | PASS |
| AC-12 | Live: request B (view legal) returns exactly playbook risk, indemnity, termination, governing law, non-standard clauses. | PASS |
| AC-13 | Live: request C (no view set) returns profile general: value, term, overall risk, top issues. | PASS |
| AC-14 | Live: request B has team_name "QA9 Finance" but view legal, and returns the Legal fields. Test: B11. | PASS |
| AC-15 | Live API: PATCH of an approval step with `summary: finance` persisted (re-read shows `('approval','finance')`) and wrote a `workflow.updated` audit row. `summary: budget` is refused with 422 "Approver view must be general, finance or legal". Test: `test_unknown_view_is_refused_and_known_ones_kept`, `test_run_keeps_its_own_copy_of_the_view`. Requests on such a step show Finance: see AC-11. NOT-RUN: the builder dropdown in the browser (the designer has no test). Role gating (admin only) is not live-verifiable because RBAC is off in dev. The scratch workflow was deleted. | PASS (API), builder UI NOT-RUN |
| AC-16 | Live: request D (no contract) shows the form's value, term and payment terms; liability and renewal are null. Test: `test_no_contract_uses_the_requesters_own_answers`. | PASS |
| AC-17 | Live: FIN approve on A rung 1 with no comment gives 200 and the decision shows `comment: null`. Test: `test_approving_with_no_open_risks_writes_no_acknowledgement`. | PASS |
| AC-18 | Live: reject with `"   "` gives 422 "Rejection requires a comment", and a missing comment gives the same. The rung stayed pending, and reject with a comment then worked. Test: `test_a_blank_rejection_comment_is_refused`, vitest F8. | PASS |
| AC-19 | Live: approve on the risky contract (2 critical, 4 high) without the acknowledgement gives 422 "Open critical or high risks must be acknowledged before approving"; the rung stays pending. Test: `test_approving_with_open_risks_needs_acknowledgement`. | PASS |
| AC-20 | Live: approve with `acknowledged_open_risks: true` gives 200. `audit_log` has `approval.risk_acknowledged` with `{"critical":2,"high":4,"contract_id":...,"intake_request_id":...}` plus `approval.decided`. Test: `test_acknowledged_approval_writes_the_audit_row`. See observation O1 about the `intake_request_id` value. | PASS |
| AC-21 | Live: for the Hooli MSA, `GET /contracts/{id}/risk` (page header source) gives critical 1, high 1, and the card's `open_risk` is 1/1 and its Playbook risk fact is "1 critical · 1 high". Test: `test_open_risk_equals_contract_risk`. The header was not compared on screen. | PASS (API) |
| AC-22 | Live: clean contract C, `open_risk` 0/0, approved with no acknowledgement, 200. Test: `test_approving_with_no_open_risks_writes_no_acknowledgement`, vitest F7. The absence of the checkbox in the browser was not seen. | PASS (API and vitest) |
| AC-23 | Live: after the decision, the chain (read as the requester) lists the approver name "QA9 fin", decision, comment and `decided_at` per rung. Test: B20 and vitest F12. No in-page refresh without navigation was observed. | PASS (API and vitest) |
| AC-24 | Live: request D (no contract), FIN approved the only rung, then `POST /workflows/runs/{id}/refresh` (the call the card makes, vitest F10). The run is `complete` and the request `approved` / stage `complete` in 0s. I did not separately capture the run state between the decision and the refresh. Test: `test_last_rung_approval_advances_the_request_after_refresh`. | PASS |
| AC-25 | Live: A, two rungs. After FIN approved rung 1, rung 2 became `pending`, `can_decide` was true for `qa9-legal` and false for FIN, and the request stayed open. Test: `test_non_final_rung_leaves_the_request_waiting`. | PASS |
| AC-26 | Live: rejected E rung 1 with a comment and the chain shows `status: rejected` with the comment. In F, a rejection cancelled rung 2. Because the workflow has no earlier Review step, E's run stopped (`failed`), as spec'd for that case. The `workflow.step_returned` audit row "Sent back to Review ... rejected: Liability cap too low" exists, from the contract-linked run. Test: `test_reject_does_not_approve_the_request`. The reply prompt after the send-back was not exercised in a browser. | PASS (send-back by audit row and test) |
| AC-27 | Test only: `test_refuses_the_requester_approving_their_own_request[tool,route]`. Live, the requester got 403 only as a non-assignee, not as an assigned approver. | PASS (test only) |
| AC-28 | Test only: `test_refuses_an_approver_above_their_authority[tool,route]`. No authority policy was seeded live. | PASS (test only) |
| AC-29 | Live: a repeated approve after a success gives 409 "Approval request is already decided", with a single decision row. This was sequential, not truly concurrent; locking is covered by the existing entry-point tests (`...already_decided_approval_is_409...`). | PASS (sequential live; concurrency by test only) |
| AC-30 | Test only: vitest F13 (`approvals/page.test.tsx`), and the whole 1763-test frontend suite passes. The approvals page was not opened. The chain reflects an approvals-page decision, because both use the same decision endpoint. | PASS (vitest only), NOT-RUN live |
| AC-31 | Live: D (no contract) shows value, term and payment terms tagged `requester-stated`; missing ones are null. Test: B13. | PASS |

Totals: 31 of 31 pass on the evidence available. Of those, 23 have live API evidence. Pytest or vitest only: AC-5, AC-9, AC-27, AC-28, AC-30. Nothing was seen in a browser (see Not exercised). One defect (D1) was found that the AC map does not catch because it sits at the boundary of AC-1/FR-1.

## Defects

### D1 (medium, FR-1 / AC-1): an upcoming (waiting) rung shows the Approve/Reject buttons to its approver

- The chain's `can_decide` is true for the assigned approver of a rung whose status is `waiting`. `_serialize_chain` (`backend/app/intake/service.py` ~1513) calls `_can_decide_approval`, which does not check that the rung is `pending`.
- Repro: request A (two rungs: `qa9-fin` on rung 1 pending, `qa9-legal` on rung 2 waiting). `GET /intake/requests/b4e92835-2f1c-490d-b2ee-ac49e85202ba/approval-chain` as `qa9-legal` gives `[(1,'pending',False),(2,'waiting',True)]`.
- The card chooses its active rung with `rungs.find(r => r.can_decide)` (`frontend/src/app/(app)/intake/_approval-card.tsx` line 34). `qa9-legal` therefore sees Approve and Reject on a rung that is not yet active.
- Submitting returns 409 "Approval request is already decided". The server refuses and nothing changes, but the message is wrong and the buttons should not appear.
- Fix direction: `can_decide` should also require `a.status == "pending"`. `test_planned_rungs_cannot_be_decided` covers only a rung with no approval row, not a `waiting` one.
- Untested aspect: an approved rung where a different team member is also assigned would show the same flaw. I did not reproduce it. A cancelled rung is not listed in the chain after a rejection (F), so it does not appear.

## Observations (not defects)

- O1: audit row `approval.risk_acknowledged` for request A recorded `intake_request_id` 1f2173fc..., an older request already linked to the same contract, not request A. It happens because my seed linked a second request to a contract that already had one. Real flows have one request per contract. Worth a look only if a contract can carry several requests.
- O2: a form value with no currency shows as "500,000.00" with no unit (request D). The stored form has no currency field. The spec says never invent one, so this is consistent.
- O3: the contract stage gate means an approver of a pending rung on a contract not in the Approval stage sees no buttons (existing behaviour, mirrors `guard_can_decide`).

## Not exercised

- Browser walk and screenshots (Approve and Reject buttons visible, read-only line, acknowledgement checkbox, message strings, the comment kept on error, the `Not available` text, dark mode and light mode): no browser tools. Covered by vitest `_approval-card.test.tsx` (F1..F11), `_request-overview.test.tsx` (F12), and `approvals/page.test.tsx` (F13).
- Workflow builder dropdown (AC-15 UI).
- Approvals page flow (AC-30), including `window.confirm` on the risk acknowledgement.
- Cross-organisation requests live (AC-5): a single org in the dev DB.
- RBAC-gated refusals (role without `approval:decide`; non-admin editing a workflow): `DISABLE_RBAC=true` in dev.
- True concurrent double-submit (AC-29), authority-limit refusal live (AC-28), requester-as-assigned-approver live (AC-27), stale-version extractions live (AC-9), a rejection's reply prompt in the UI.

## Cleanup

Dev DB rows left behind: users `qa9-*@example.com`, teams "QA9 Finance" / "QA9 Legal", requests `QA9-*`, their approvals and runs, and the five contracts above, whose `lifecycle_stage` I set to `approval`. Original stages: QA Acme MSA was `signature`, the other four were `review`. Remove with `make nuke` or by hand if they matter.
