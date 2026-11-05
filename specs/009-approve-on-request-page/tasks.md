# Task Breakdown: Approve and reject on the request page

Feature ID: 009-approve-on-request-page
Plan: ./plan.md (APPROVED)
Created: 2026-10-22
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- Every task maps to requirement(s) from spec.md, noted as `(FR-N)`.
- No migration and no model change, so db-engineer has no task and the Alembic head is untouched.
- The three backend tasks are deliberately chained (no `[P]`): they share `app/approvals/*`,
  `app/intake/*`, `app/workflows/service.py` and `tests/test_approval_card.py`.
- Backend and frontend run in parallel against the interface freeze in plan.md.
- `frontend/src/lib/types.ts` and `endpoints.ts` are touched by T004 only.
- `docker compose restart backend` is needed once `app/intake/approval_summary.py` exists (new module).

## Waves

### Wave 1 — Foundation (parallel: backend read module ∥ frontend types)
- [ ] T001 [P] (backend-dev) Summary module `app/intake/approval_summary.py` (profiles, `tag_for` by `field_sources`, `snippet`, `request_contract`, `subject_contract`, `open_risk_counts` from `contract_risk`, `current_clauses`, `build_summary` with the no-contract "requester-stated" fallback); workflow view in `app/workflows/service.py` (`APPROVAL_VIEWS`, `_clean_steps` 422 on unknown value, `approval_view_for`); pytest cases for tags, null -> "Not available", stale clauses skipped, quotes, profile field lists, counts equal `contract_risk`, unknown view refused, run keeps its own step copy (FR-4..FR-15, FR-19, FR-29) — dependsOn: — — files: backend/app/intake/approval_summary.py, backend/app/workflows/service.py, backend/tests/test_approval_card.py
- [ ] T004 [P] (frontend-dev) TS interfaces (`ApprovalSummary`, summary field, `ApprovalDecisionEntry`, extended `IntakeApprovalRung` with `can_decide`/`decisions`), `RISK_ACK_REQUIRED_MESSAGE`, `intakeApi.approvalSummary`, `approvalsApi.decide` 4th argument — copied exactly from the interface freeze (FR-2, FR-18, FR-21) — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts

### Wave 2 — Decision rules ∥ frontend build-out
- [ ] T002 (backend-dev) Approvals domain: `assigned_only` in `_user_eligible_to_decide` (service, wrapper, `decide_in_app`) and `_can_decide_approval`; `list_approvals` uses it; `_apply_decision` strips the comment; `ApprovalDecisionPayload.acknowledged_open_risks`; decision route order 404 -> 409 -> 403 -> 422 ack gate, `approval.risk_acknowledged` audit row on success, `ponytail:` comment for the ungated assistant path; extend `test_approval_decide_entry_points.py` only, never weaken it (FR-1, FR-3, FR-16..FR-21, FR-27) — dependsOn: T001 — files: backend/app/approvals/routes.py, backend/app/approvals/service.py, backend/tests/test_approval_card.py, backend/tests/test_approval_decide_entry_points.py
- [ ] T005 [P] (frontend-dev) `_approval-card.tsx` (summary grid with source tags, "Not available", read-only line, risk line, acknowledgement checkbox only when critical+high > 0, comment, Approve/Reject, invalidations + workflow refresh after success, inline errors, buttons disabled while pending); render it above the chain card in `_request-overview.tsx` and list each rung's decisions (who, comment, when); component test + overview test additions (FR-1, FR-2, FR-6, FR-16..FR-18, FR-20, FR-22..FR-26) — dependsOn: T004 — files: frontend/src/app/(app)/intake/_approval-card.tsx, frontend/src/app/(app)/intake/_approval-card.test.tsx, frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx
- [ ] T006 [P] (frontend-dev) Approvals page asks once on the 422 acknowledgement message and resends with it true (D4) + test; workflow designer "What the approver sees" select on Approval steps writing `config.summary` (FR-15, FR-28) — dependsOn: T004 — files: frontend/src/app/(app)/approvals/page.tsx, frontend/src/app/(app)/approvals/page.test.tsx, frontend/src/app/(app)/workflow-builder/[id]/_designer.tsx

### Wave 3 — Intake wiring
- [ ] T003 (backend-dev) Intake domain: `_serialize_chain(rows, *, actor)` adds `can_decide` (assigned_only) and `decisions[]`, planned rungs get `can_decide: False`; chain route permission `intake:create`; new `GET /intake/requests/{id}/approval-summary` (thin, permission `intake:create`, contract access check); pytest for requester sees chain, non-approver false, already-decided false, walled contract 404, org scoping, last-rung approval on a no-contract request advances after workflow refresh, non-final rung leaves the request unapproved, reject sends back (FR-1..FR-3, FR-22..FR-26) — dependsOn: T001, T002 — files: backend/app/intake/service.py, backend/app/intake/routes.py, backend/tests/test_approval_card.py

### Wave 4 — Integration & QA
- [ ] T007 (qa-engineer) Run ruff, `alembic upgrade head`, `pytest -q`, `npm run typecheck`, `npm run test`; walk the stack (restart backend first) as a Finance approver, a Legal approver, the requester and an unrelated user (note: in dev an admin must be on a team to approve); verify AC-1..AC-31, write verification.md — dependsOn: T003, T005, T006 — files: specs/009-approve-on-request-page/verification.md

## Workload

| Agent | Tasks |
|---|---|
| backend-dev | T001, T002, T003 (serial) |
| frontend-dev | T004, then T005 ∥ T006 |
| qa-engineer | T007 |
| db-engineer | none |

After T003 the orchestrator regenerates the repo graph (`python scripts/build_repo_graph.py`).

## Task detail notes

- Each test needs a docstring naming the failure mode it guards (CLAUDE.md).
- The plan's Test strategy (B1-B27, F1-F13) is the case list; T001/T002/T003 and T005/T006 each cover the cases for the files they own, and T007 confirms none are missing.
- Self-approval keeps today's rule (`requested_by_user_id`), per the plan's resolved open question.
