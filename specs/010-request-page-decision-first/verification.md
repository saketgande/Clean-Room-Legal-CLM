# Verification: 010 Request page, decision first

Verifier: qa-engineer (T009), 2026-10-23. No browser tools were available to me. Live-page figures below are the orchestrator's measurements of REQ-7705 (http://localhost:3000/intake?open=88a50a2f-c681-4ac6-af8f-fc7d66a9bc1f, local dev admin) and are cited as such, not re-measured by me. Everything else is vitest I ran.

## Gates

| Gate | Command (from frontend/) | Result |
|---|---|---|
| Unit tests | `npm run test` | PASS: 72 files, 1835 tests passed, 0 failed |
| Typecheck | `npm run typecheck` | Only the two accepted errors: `.next/types/validator.ts(105,39)` and `(114,39)` (docstudio pages). Nothing else. |
| Lint | not run (hangs on a wizard in this repo) | NOT-RUN by design |
| Backend | `git diff --stat -- backend` | 8 modified files (ai/agent_catalog.py, ai/prompt_versions.py, approvals/routes+service, intake/routes+service, workflows/service.py, tests/test_approval_decide_entry_points.py). Per the orchestrator these are spec 009's uncommitted edits; I did not judge them. Note: ai/prompt_versions.py shows a very large diff (3478 lines) - worth confirming it belongs to 009 before commit. No backend pytest/ruff run (feature is frontend-only). |

## AC table

Result key: PASS = vitest case(s) green and, where noted, consistent with the orchestrator's live measurement. PARTIAL = only part covered, or covered by vitest/reuse only with the browser part not exercised. No FAIL.

| AC | Result | Evidence |
|---|---|---|
| AC-1 order | PASS | `_request-overview.test.tsx` "puts the cards in layout A's order in both columns, and draws one stage bar". Live (orchestrator): main = Approval card, What was asked, AI risk review, Comments and questions, Activity; side = Counterparty relationship, Owner and people, Documents, Steps, Ask Aegis. |
| AC-2 header pills | PASS | `_request-header.test.tsx` "shows the summary and both pills when high risk and overdue", "shows no pills when nothing is wrong, and falls back for missing names", "shows SLA at risk, and the crumb goes back". Live: "high risk" pill present on REQ-7705. |
| AC-3 one progress strip | PASS | overview "...draws one stage bar" (one LifecycleView; no "You are here", "At a glance", "Workflow & stages"). Live: one strip. |
| AC-4 reply owed | PASS (vitest only) | `_decision-area.test.tsx` "blocks Approve until the rework reply is typed, and shows the rejection reason", "wires approve, send back and comment..."; overview "shows why it was rejected and holds the Approve button until there is a reply". Not exercised live. |
| AC-5 read-only / none | PASS | overview "shows a pending approval the viewer cannot decide at the top of the main column, read-only (AC-5 a)", "takes no space for the decision when nothing is pending... (AC-5 b)"; `_decision-area.test.tsx` "renders nothing when nothing is pending...", "shows a read-only approval node alone...". Live: admin (who started the request) sees the read-only card first. |
| AC-6 each fact once | PASS, one minor note | overview "keeps the header to a one-line summary, and shows each fact once outside the approval card (AC-6)". Live repeat check: 'Initech Software' 2x (What was asked + counterparty card), 'step 4 of 6' 1x, 'high risk' 2x (header pill + risk section wording). Note D-1: approval-card TERM text differs in format from What was asked. |
| AC-7 no raw data / internal names | PASS | overview "never prints a step's saved internal name or raw result data (AC-7)"; `workflow-steps.test.ts` "maps every stored default name; failure mode: 'Clm Draft' leaking...", "renders driver objects as text, never JSON...". Live: no `deviation_id`, no `{"`, no "Clm Draft". |
| AC-8 typed title, no system text | PASS | overview "shows the requester's own title and words, and none of the system's filler (AC-8)". Live: title 'Today_14_test'; no 'form by Local Admin', no 'workflow engine drives...', no header 'Open CLM editor'/'Start workflow'. |
| AC-9 no fixed copy | PASS | AC-8 test above (no "workflow engine drives"); `_request-cards.test.tsx` "shows the three chips and no canned hint sentence while the chat is empty". Live: absent. Empty-everything request not exercised live. |
| AC-10 one-line empties | PASS (vitest only) | `_request-cards.test.tsx` "collapses to one line with no CLM link when nothing is attached...", "collapses to one line without a composer..."; `_request-risk.test.tsx` "shows 'No open issues.' for low with zero findings". |
| AC-11 SOW + notices | PASS (vitest only) | `_request-asked.test.tsx` "renders notices only when they apply", "lists only the non-empty Aegis read groups", "renders the answers slot"; overview deliverables editor cases ("shows why the draft is held and offers no Approve & advance...", "saves the rows, asks the held run to look again..."). No SOW request exercised live. |
| AC-12 risk section | PASS (vitest only) | `_request-risk.test.tsx` "shows band, counts, issue with clause and fix, and conceded drivers", "offers Recompute only with canRecompute", "hides Recompute without canRecompute". |
| AC-13 comments | PASS (vitest only) | `_request-cards.test.tsx` "marks a sent-back entry with its step and posts on Enter", "disables Post for blank text"; overview "labels the reply in the comment thread...". |
| AC-14 steps / send back | PASS (vitest only) | `_request-steps.test.tsx` "shows badges and assignee on each row", "send back stays disabled until a step is picked"; overview "says why Send back is off until a step is picked...", "has one Send back form...". Server send-back not exercised. |
| AC-15 suggested workflow | PASS (vitest only) | `_request-steps.test.tsx` "offers Start it only when allowed, as one button", "covers setting up, none and empty states". |
| AC-16 documents | PASS (vitest only) | `_request-cards.test.tsx` "collapses to one line with no CLM link when nothing is attached and there is no contract", "lists files and links to the contract editor once". Live: header no longer carries 'Open CLM editor'. |
| AC-17 reassign | PARTIAL | Control present/disabled-until-picked: `_request-cards.test.tsx` "offers Reassign only when given..."; overview "offers each action once to a manager: Reassign, Post, Send back, Approve & advance (AC-18)". Same toast/audit not exercised (handler unchanged, backend untouched). |
| AC-18 each FR-24 state | PARTIAL | Action matrix vitest above plus the many pre-existing overview action cases (retry review, draft/ingest, trademark, rejection reply, pick workflow). The FR-24 states (no workflow, human step, approval, rejected, complete, no contract) were not opened live. |
| AC-19 expandable detail | PASS (vitest only) | `_request-steps.test.tsx` "hides the review result until opened", "puts the approval chain under its step, collapsed, with decisions once opened", "collapses the hand-off trace and uses plain step names"; overview "lists each rung's decisions with who, comment and when". |
| AC-20 permissions | PASS (vitest) / PARTIAL live | overview "gives a person who may only look no action, but still the read-only approval card (AC-20)", "is read-only for a viewer who cannot decide", "shows Approve and Reject above the chain to the assigned approver only". Live only as admin; requester/approver/staff views not exercised. |
| AC-21 light/dark | PARTIAL | `_request-risk.test.tsx` "has no hardcoded colours". Orchestrator dark-mode live measure: 139 text leaves, 6 below 3:1 (future-stage labels 'Signature', 'Active', 'Closed' and '5','6','7', rgb 68,73,82 on 12,15,22, ~2.1:1). These are the shared stage bar's dimmed "not reached yet" labels, unchanged by this feature: pre-existing low-contrast note, not an 010 defect. No white block found per orchestrator. Light mode contrast not measured. |
| AC-22 narrow | PASS (live, orchestrator) | 375px: document scrollWidth 375, no horizontal page scroll; stage strip scrolls inside its own container (640px-min grid, overflow-x-auto); approval card first. ~800px pane stacks to one column. Every action reachable at 375 was not clicked through. |
| AC-23 states | PASS (vitest only) | overview "says it was not found and offers the way back, instead of 'Loading request...' for ever", "says there is no access when the server refuses", "says the workflow is being set up, and does not offer a second one to start". Not exercised live. |
| AC-24 mockup side by side | PARTIAL | Order and widths match per orchestrator (main 868px at x=234, side 300px at x=1118). Documented deviations (plan.md): strip ~25px taller; side column 300px vs mockup 270px. No user acceptance recorded yet; only REQ-7705 compared; the other FR-24 states not compared. |
| AC-25 audit | PARTIAL | No backend change by this feature; handlers reused. Not exercised: no audit rows read after an action from the new layout. |
| AC-26 stage click | PARTIAL | Strip reuses the shared `LifecycleView` whose stage buttons (`aria-pressed`, toggle on click) are unchanged; `_request-header.test.tsx` "shows the warn tone class and the seven stages" checks the seven stages render. No test or browser click on a stage was performed. |

Totals: 26 ACs; 20 PASS (of which 15 are vitest-only for their interactive parts), 6 PARTIAL (AC-17, 18, 21, 24, 25, 26), 0 FAIL.

## Live page notes (orchestrator measurement, REQ-7705)
- After follow-ups: no 'Agent working...' on an approval step; 'Top issues' in the approval card spans the full row.
- One page-level 401 console error on fresh load (a failed resource, not a crash). Source not identified; worth a glance.
- No horizontal page scroll at 1440 or 375.

## Defects
No FAIL. Minor findings:
- D-1 (cosmetic, known, backend-sourced): approval-card TERM reads "2026-10-23 to 2027-10-23" while What was asked shows "23 Oct 2026 - 1 year". Same dates, different format, since the card's term text comes from the backend summary (spec 009). Strictly AC-6 asks repeated values to equal the page's; accepted as minor, outside this feature's frontend-only scope.
- D-2 (pre-existing, not 010): dimmed future-stage labels at ~2.1:1 in dark mode (see AC-21).
- D-3 (to confirm): one 401 console error on fresh load; cause unknown.

## Measured vs mockup
Order matches screen A in both columns. Main 868px / side 300px (mockup side 270px, accepted deviation pending user sign-off); strip ~25px taller than mockup. No per-card height comparison was provided.

## Not exercised
- Requester, approver (non-owner), owner, and staff-without-manage views (only admin, who started the request, was used).
- The 'being set up' and load-failure states live; the contract-less request live.
- SOW deliverables, trademark flows live.
- Reassign, Send back, Ask Aegis, stage-strip clicks, and any audit/toast follow-through.
- FR-24 states other than REQ-7705 (no workflow yet, waiting on human step, rejected with reply owed, complete).
- Light-mode contrast; mockup per-card heights.
- Backend pytest/ruff (no backend change by 010).

## Verdict
Frontend gates are green (1835 tests; only the two accepted typecheck errors). No acceptance criterion failed on anything I could run or on the orchestrator's live measurements of REQ-7705. The page is, however, only live-verified for one request as admin; role views, the other FR-24 states, interactive actions with audit and the side-by-side acceptance remain unverified, so I would call this "implementation passes, acceptance not complete" until those are walked in a browser and the mockup deviations are accepted by the user.
