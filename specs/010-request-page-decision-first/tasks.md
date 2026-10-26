# Task Breakdown: Request page, decision first

Feature ID: 010-request-page-decision-first
Plan: ./plan.md (APPROVED)
Created: 2026-10-23
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- Every task maps to requirement(s) from spec.md, noted as `(FR-N)`.
- Frontend only: no migration, no endpoint, no model, no change to `types.ts`, `endpoints.ts` or `ui.tsx`.
  db-engineer and backend-dev have no tasks.
- All component files are under `frontend/src/app/(app)/intake/` and hold presentation only. `_request-overview.tsx`
  keeps every query, mutation, effect and typed draft (plan: Component design). Props are frozen in plan.md's
  Interface freeze; copy them exactly.
- `_request-overview.tsx` and `_request-overview.test.tsx` are the hotspot: **only T008 edits them**. They still hold spec 009's
  uncommitted edits (ApprovalCard placement, chain decisions); T008 builds on the working tree as it is and must not revert them.
- Each component task runs `npm run typecheck` and its own vitest file. T008 runs the full `npm run test`.
  Never run `npm run lint`. Two stale `.next/types/validator.ts` docstudio typecheck errors are pre-existing.
- Theming: no hardcoded colours, no `bg-white`; use the existing `.ro` palette variables.

## Waves

### Wave 1 — Independent pieces (parallel, disjoint files)
- [ ] T001 [P] (frontend-dev) `plainStepName` (maps the backend's stored names such as "Clm Draft" to plain words) and `readableResult` (readable lines from a review step's drivers instead of `JSON.stringify`) in the shared step helpers, with tests (FR-7, FR-16, FR-13) — dependsOn: — — files: frontend/src/lib/workflow-steps.ts, frontend/src/lib/workflow-steps.test.ts
- [ ] T002 [P] (frontend-dev) Header (one-line summary: title as typed, ref, raised-by + when, owner, pills shown only when true) and the clickable 7-stage progress strip reusing today's stage bar and status line, with tests (FR-1, FR-2, FR-3, FR-20, FR-22) — dependsOn: — — files: frontend/src/app/(app)/intake/_request-header.tsx, frontend/src/app/(app)/intake/_request-header.test.tsx
- [ ] T003 [P] (frontend-dev) Risk section with the single shared `riskFacts()` that the header pill, decision warning and risk section all read; readable issue list; "No open issues." strip; recompute action gated as today; dark-mode-safe severity colours; tests (FR-6, FR-9, FR-12) — dependsOn: — — files: frontend/src/app/(app)/intake/_request-risk.tsx, frontend/src/app/(app)/intake/_request-risk.test.tsx
- [ ] T005 [P] (frontend-dev) "What was asked": description as written, form facts, AI read, the needs-information notices, the answers/deliverables editor entry; the "— source by requester" line removed; tests (FR-8, FR-10, FR-11) — dependsOn: — — files: frontend/src/app/(app)/intake/_request-asked.tsx, frontend/src/app/(app)/intake/_request-asked.test.tsx
- [ ] T006 [P] (frontend-dev) Decision area: approval card first for everyone with a pending approval (read-only form for non-deciders, per spec 009), then the human-step controls (approve and advance with the risk confirm, rework reply, comment, send back) and the negotiation panel for those who can act; empty when nothing is pending; remove the footer line in the negotiation panel; tests (FR-4, FR-5, FR-18) — dependsOn: — — files: frontend/src/app/(app)/intake/_decision-area.tsx, frontend/src/app/(app)/intake/_decision-area.test.tsx, frontend/src/app/(app)/intake/_negotiation-panel.tsx
- [ ] T007 [P] (frontend-dev) Side cards (counterparty relationship, owner and people with Reassign, documents with Open in CLM editor and the contract / trademark actions, Ask Aegis with no canned hint), comments and questions, activity; each collapses to a one-line strip when empty; tests (FR-9, FR-14, FR-15, FR-17, FR-18) — dependsOn: — — files: frontend/src/app/(app)/intake/_request-cards.tsx, frontend/src/app/(app)/intake/_request-cards.test.tsx

### Wave 2 — Needs the step helpers
- [ ] T004 (frontend-dev) Steps card: compact vertical list using `plainStepName`, "Start it" (merged from the old header button and link), Send back for rework, expandable detail on the AI review step (`readableResult`) and the Approval step (the approval chain with each approver's decisions), agent hand-off trace under the list; tests (FR-13, FR-16, FR-18, FR-19) — dependsOn: T001 — files: frontend/src/app/(app)/intake/_request-steps.tsx, frontend/src/app/(app)/intake/_request-steps.test.tsx

### Wave 3 — Integration (the hotspot, one task)
- [ ] T008 (frontend-dev) Wire all pieces into `_request-overview.tsx` in layout A (header + strip, main column: decision area, What was asked, risk, comments, activity; side column: counterparty, owner and people, documents, steps, Ask Aegis); keep every query, mutation, effect and typed draft in the parent; drop the removed fixed text and duplicate buttons; keep loading / load-failure / "being set up" states and spec 009's ApprovalCard behaviour; update the four existing tests named in plan.md's Test strategy and add the AC-5 (two cases), AC-6, AC-8, AC-20 cases; run the full vitest suite and typecheck (FR-1, FR-4..FR-9, FR-17..FR-19, FR-21..FR-24) — dependsOn: T001, T002, T003, T004, T005, T006, T007 — files: frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx

### Wave 4 — QA
- [ ] T009 (qa-engineer) Verify AC-1..AC-26: full vitest and typecheck; in the browser as requester, owner, approver and staff without manage rights; light and dark; 400px width with no sideways page scroll; real long data; and measure the finished page against mockup screen A at the same viewport with DOM geometry (`getBoundingClientRect`), not by eye; confirm every action in the plan's inventory table is reachable; write verification.md (FR-24) — dependsOn: T008 — files: specs/010-request-page-decision-first/verification.md

## Workload

| Agent | Tasks |
|---|---|
| frontend-dev | T001, T002, T003, T005, T006, T007 (parallel), then T004, then T008 |
| qa-engineer | T009 |
| backend-dev, db-engineer | none |

After T008 the orchestrator regenerates the repo graph (`python scripts/build_repo_graph.py`).

## Task detail notes

- The plan's Inventory table (existing element -> new place -> shown when) is the contract for T002-T008. An element missing from a component task must be reported, not dropped.
- Each test needs a docstring naming the failure mode it guards (CLAUDE.md).
- Mockup fidelity: any visible difference from screen A (other than the plan's listed deviations: the strip is about 25px taller, the side column is 300px) goes in verification.md.
- Dev data: pending approvals exist for REQ-7705, REQ-9287, REQ-8377, REQ-9288, REQ-9761 (all started by admin@example.com); for the approver view use a second user on the approving team (an unassigned admin, or the person who started the request, sees the read-only card).
