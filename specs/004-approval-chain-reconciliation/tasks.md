# Task Breakdown: Dynamic, Condition-Driven Approval Chains

Feature ID: 004-approval-chain-reconciliation
Plan: ./plan.md (APPROVED)
Created: 2026-09-14
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- `dependsOn: —` means no dependencies (wave 1).
- Every task maps to requirement(s) from spec.md, noted as `(FR-N)`.
- Two tasks that touch the same file never carry `[P]` together.
- The migration task never runs parallel with another migration task.
- **`backend/app/approvals/service.py`, `backend/app/workflows/service.py` and
  `backend/app/intake/service.py` are edited by ONE task (T010) only** — per
  plan.md's shared-hotspot note, all three edits are three halves of one
  behavior and must land in the same commit.

## Waves

### Wave 1 — Foundation (schema)

- [ ] T001 (db-engineer) `ApprovalChainDefinition`, `ApprovalChainStep`,
      `ApprovalChainStepRule`, `ApprovalChainInstance`, `ApprovalChainRequirement`,
      `ApprovalChainHistory` models (every FK declared by string — no import
      from `app.auth`/`app.org_structure`/`app.contracts`/`app.approvals`);
      the two append-only ORM event listeners on `ApprovalChainHistory`;
      registry entries; Alembic revision `0044_approval_chains` on head
      `0043_menu_screen_security`, including the DDL, the append-only Postgres
      trigger on `approval_chain_history`, and the cutover seed (one default
      chain definition per org per subject type — one sequential step, one
      base requirement on the org's `approver` role, falling back to `admin`
      — with `is_default_seeded=true`) (FR-1, FR-10, FR-19, FR-21, FR-22) —
      dependsOn: — —
      files: `backend/app/approval_chains/__init__.py`,
      `backend/app/approval_chains/models.py`, `backend/app/models.py`,
      `backend/alembic/versions/0044_approval_chains.py`

### Wave 2 — Parallel implementation (evaluator, resolver, interface, client contract)

- [ ] T002 [P] (backend-dev) `conditions.py` — `evaluate_condition`,
      `validate_expression`, `render_explanation`, the five-operator dispatch
      (`gt`/`lt`/`eq`/`in`/`contains`, tokens not symbols), no `eval`/`exec`/
      `compile`/`getattr`, fail-closed on any malformed/unknown-operator/
      oversized input (never raises); adversarial-input pytest battery
      (FR-2, FR-3, FR-4) —
      dependsOn: — —
      files: `backend/app/approval_chains/conditions.py`,
      `backend/tests/test_condition_evaluator.py`
- [ ] T003 [P] (backend-dev) `facts.py` — `MODULE_FACTS` catalog and
      `build_facts`/`allowed_fields` for both subject types: `contract` facts
      (`contract_value`→`value_amount`, `jurisdiction`, `risk_band`, etc.) and
      `intake_request` facts (`request_type`, `department`, `priority`,
      derived `request_value`/`currency`/`jurisdiction`/`risk_band` reusing
      `IntakeApprovalSubject`'s existing `_VALUE_KEYS`/`_PRIORITY_TO_BAND`
      semantics) — a frozen whitelist, never a live lookup into
      `field_values` (FR-1, FR-2 field surface) —
      dependsOn: — —
      files: `backend/app/approval_chains/facts.py`
- [ ] T004 [P] (backend-dev) `org_access.py` additive-only change: `RoleHolder`,
      `users_holding_role(db, *, org_id, role_id, org_unit_id, at,
      include_delegates)`, and `_grant_validity_clause(at)` extracted from
      `active_grants_for_user`'s existing WHERE clause (behavior-identical —
      `active_grants_for_user` must call the new helper too, with no change
      to its own return values); pytest confirming both functions produce
      identical results on the same fixtures as before the extraction
      (FR-17, FR-18) —
      dependsOn: — —
      files: `backend/app/core/org_access.py`, `backend/tests/test_org_access_role_holders.py`
- [ ] T005 [P] (backend-dev) Four new permission strings
      (`approval_chain:read`, `approval_chain:manage`, `approval_chain:decide`,
      `approval_chain:recalculate`) + `DEFAULT_ROLE_PERMISSIONS` wiring in
      `rbac.py`; verify/update the permission-catalog fixture in
      `test_phase10_security_hardening.py` only if it enumerates all
      permission strings —
      dependsOn: — —
      files: `backend/app/core/rbac.py`, `backend/tests/test_phase10_security_hardening.py`
- [ ] T006 [P] (backend-dev) `approval_chains` Pydantic schemas
      (`ConditionExpressionIn` with `extra="forbid"`, `ChainDefinition/Step/
      StepRule Create/Update/Response`, `ChainInstanceCreate/Summary/Detail`,
      `ChainRequirementResponse`, `ChainConditionExplanation`,
      `ChainBlockingEntry`, `ChainHistoryEntry`, `ChainBlockedResponse`,
      `ChainDecisionPayload`, `ChainRecalculatePayload`) + access helpers
      (`get_definition_or_404`, `get_step_or_404`, `get_rule_or_404`,
      `get_instance_or_404`, `get_requirement_or_404`, `get_org_role_or_404`,
      `get_module_record_or_404` — reusing
      `app.contracts.access.user_can_access_contract` for the contract case
      so ethical walls keep overriding — `assert_instance_visible`) (FR-1,
      FR-16, FR-18) —
      dependsOn: T001 —
      files: `backend/app/approval_chains/schemas.py`, `backend/app/approval_chains/access.py`
- [ ] T007 [P] (frontend-dev) TS interfaces (`ChainDefinitionResponse`,
      `ChainInstanceSummary/Detail`, `ChainRequirementResponse`,
      `ChainConditionExplanation`, `ChainHistoryEntry`, `ChainBlockingEntry`,
      `ConditionExpression`) + endpoint client additions (`approvalChainsApi`:
      `fields`, `definitions`, `createDefinition`, `steps`/`rules` CRUD,
      `instances`, `instance`, `decide`, `recalculate`) + the four new
      optional fields on `IntakeApprovalRung`
      (`requirement_id`/`chain_instance_id`/`explanation`/`blocked`) and its
      `approval_request_id: ID | null` type correction, per the frozen
      interface contract —
      dependsOn: — —
      files: `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts`
- [ ] T008 [P] (frontend-dev) Pure helpers `OPERATOR_LABELS`,
      `formatConditionText`, `requirementTone`, `stepProgress`,
      `nextActionableSequence` + vitest coverage —
      dependsOn: — —
      files: `frontend/src/lib/approval-chains.ts`, `frontend/src/lib/approval-chains.test.ts`

### Wave 3 — Parallel build-out (domain service, frontend pages)

- [ ] T009 [P] (backend-dev) `approval_chains/subjects.py` (`resolve_subject`,
      the `on_submit`/`guard_can_decide`/`on_reject`/`on_complete` hooks per
      subject type — contract → lifecycle stage transitions, intake → stage/
      `open`/`finalize_approved`), `dispatch.py` (the frozen interception
      surface `app/approvals/service.py` and `app/workflows/service.py` import
      lazily: `active_definition_for`, `live_instance_for`,
      `intake_strip_rungs`, `intake_planned_rungs`), `service.py`
      (`get_field_catalog`, definition/step/rule CRUD with
      `conditions.validate_expression` on every write, `create_instance`,
      `_materialize_step` — the ONLY place conditions are ever evaluated, FR-5
      — `record_decision` with fresh eligibility re-check + sequential gate +
      `enforce_authority` parity, `_advance_step`, `recalculate` with the
      before/after reconciliation and `superseded_at` handling, `list_instances`/
      `get_instance_detail`/`get_history`/`get_blocked` — read-only, no
      evaluation, no writes), `routes.py` (one `APIRouter`, prefix
      `/approval-chains`); pytest for config CRUD, materialization, decisions,
      recalculation, and the append-only history guard (both the ORM listener
      and the Postgres trigger) (FR-1, FR-3, FR-5 through FR-16, FR-18 through FR-20) —
      dependsOn: T002, T003, T004, T005, T006 —
      files: `backend/app/approval_chains/subjects.py`,
      `backend/app/approval_chains/dispatch.py`,
      `backend/app/approval_chains/service.py`,
      `backend/app/approval_chains/routes.py`,
      `backend/tests/test_approval_chain_config_api.py`,
      `backend/tests/test_approval_chain_materialization.py`,
      `backend/tests/test_approval_chain_decisions.py`,
      `backend/tests/test_approval_chain_recalculate.py`,
      `backend/tests/test_approval_chain_history_append_only.py`
- [ ] T011 [P] (frontend-dev) `approvals/page.tsx`: two new tab entries
      (`chains` rendered for any user, `conditions` gated on
      `can(user, "approval_chain:manage")`), the rerouted-submission success
      toast ("Approval chain started — see the Chains tab") and cache
      invalidation — no other change to the existing tab logic;
      `_rules-builder.tsx`: one `warning` `MessageBar` stating routing rules no
      longer determine new-submission approvers — no logic change (FR-22) —
      dependsOn: T007 —
      files: `frontend/src/app/(app)/approvals/page.tsx`,
      `frontend/src/app/(app)/approvals/_rules-builder.tsx`
- [ ] T012 [P] (frontend-dev) `_chains-tab.tsx` (filterable instance table) and
      `_chain-detail.tsx` (per-step Sequential/Parallel badge, per-requirement
      status + condition explanation + base/conditional badge, Approve/Reject
      gated on `can_decide`, the sequential-block tooltip, the blocked
      `MessageBar` respecting `blocking_visible`, the Recalculate action, and
      the history table with the delegator as its own column) (FR-6, FR-7,
      FR-9, FR-12, FR-13, FR-19, FR-20) —
      dependsOn: T007, T008 —
      files: `frontend/src/app/(app)/approvals/_chains-tab.tsx`,
      `frontend/src/app/(app)/approvals/_chain-detail.tsx`
- [ ] T013 [P] (frontend-dev) `_condition-rules-tab.tsx` — subject-type picker
      driving `module`, the default-seeded-definition banner, definition →
      step → rule editor with the field/operator selects populated from
      `approvalChainsApi.fields(module)` (server remains the real boundary via
      `validate_expression`), and the compound-rule guidance message (FR-1,
      FR-2, FR-3) —
      dependsOn: T007, T008 —
      files: `frontend/src/app/(app)/approvals/_condition-rules-tab.tsx`
- [ ] T014 [P] (frontend-dev) `intake/page.tsx`: the inline chain strip's
      `decide()` branches to `approvalChainsApi.decide(chain_instance_id,
      requirement_id, …)` when `requirement_id` is present on a rung, and
      Reassign is hidden for a chain-sourced rung (the new engine has no
      reassign concept) — codes against the frozen rung shape from T007, no
      other change —
      dependsOn: T007 —
      files: `frontend/src/app/(app)/intake/page.tsx`

### Wave 4 — Reroute + integration

- [ ] T010 (backend-dev) The FR-22 reroute, landed as one commit across all
      three files: `approvals/service.py`'s `submit_subject_for_approval`
      gains the dispatch block (after the existing legacy-idempotency check,
      before the legacy `plan_chain` fallback) calling
      `dispatch.active_definition_for`/`create_instance` when an active
      definition exists for the org+module, else falls through to legacy with
      an `approval_chain.reroute_skipped` audit row; `workflows/service.py`'s
      no-contract `approval` step branch and its matching `refresh_run` resume
      branch are fixed to track/resume off the new engine's chain instance
      instead of auto-advancing on an empty legacy request list (the latent
      auto-approve bug found during planning); `intake/service.py`'s
      `start_approval_ladder` and `get_approval_chain` gain an early branch
      using `dispatch.intake_strip_rungs`/`intake_planned_rungs` when a live
      chain instance exists, ahead of (not replacing) the legacy query —
      `_serialize_chain` and `_rung_label` are NOT modified (FR-21, FR-22) —
      dependsOn: T009 —
      files: `backend/app/approvals/service.py`, `backend/app/workflows/service.py`,
      `backend/app/intake/service.py`
- [ ] T015 [P] (backend-dev) Register `approval_chains_router` in `main.py`
      immediately after the menu-security routers —
      dependsOn: T009 —
      files: `backend/app/main.py`

### Wave 5 — Cross-cutting reroute test

- [ ] T016 (backend-dev) Integration test suite proving: an in-flight legacy
      `ApprovalRequest` at cutover is untouched by a re-submission (FR-21); a
      new contract submission and a new intake-request submission each
      produce a materialized chain via the reroute, not a legacy flat request
      (FR-22/AC-21/AC-22); an org with no active definition falls through to
      legacy with the skip audit row; the intake inline strip's five parity
      cases (rung shape, decide routing, Reassign hidden) agree with the
      Chains tab's data for the same instance; and — as the AC-20 evidence a
      pytest can't assert on its own — this task documents the inspection
      showing the diff is bounded to exactly the three `service.py` files and
      the functions named in T010's scope, nothing else in `app/approvals/`,
      `app/workflows/`, or `app/intake/` —
      dependsOn: T010, T015 —
      files: `backend/tests/test_approval_chain_reroute.py`

### Wave 6 — QA

- [ ] T017 (qa-engineer) Verify all 22 FRs and all 22 ACs against the running
      stack; write `verification.md`. AC-9 ("no automatic recalculation ever
      happens, no matter how much time passes") is evidenced by the absence of
      any Celery task in this feature's path mapping plus a source assertion
      that `_materialize_step` has exactly two call sites, both mutating
      (`create_instance`, `_advance_step`) — document this as the verification
      method, same pattern as feature 003's AC-16. AC-20 (existing-domain
      blast radius) is evidenced by inspection, not a pytest — confirm T016's
      documented diff bound directly against the actual file contents —
      dependsOn: T009, T010, T011, T012, T013, T014, T015, T016 —
      files: `specs/004-approval-chain-reconciliation/verification.md`

## Task detail notes

- **T001** seeds one default chain definition per org per subject type at
  migration time — this is the honest, visible alternative to a lossy
  translation of existing `ApprovalRoutingRule` config (rejected in plan.md:
  approver-group targets don't map to `role_id` FKs, and multi-condition AND
  would silently become OR). Confirm the seed produces a definition with
  `is_default_seeded=true` and at least one base requirement whose role
  resolves to a non-empty holder set (feature 002's org-root grant backfill
  should guarantee this) before reporting done.
- **T004**'s extraction of `_grant_validity_clause` out of
  `active_grants_for_user` is the one edit to an existing, already-shipped
  file this feature makes outside the reroute — it must be proven
  behavior-identical (same WHERE clause, same results on existing fixtures),
  not just additive in isolation.
- **T009** is the largest task in the feature and the one everything else in
  wave 4 depends on — treat `_materialize_step`'s "called exactly twice in
  the codebase" invariant as load-bearing; a third call site (e.g. from a read
  endpoint) would violate FR-5 even if it "only happens sometimes."
- **T010** is a single task by design (see the Format section above) — do not
  attempt to split it across parallel tasks. The three edits are three halves
  of one behavior and an intermediate state between any two of them is a real
  security/correctness gap (silent auto-approval, or a blank intake strip).
- **T016** depends on both T010 (the reroute) and T015 (router registration)
  because proving the reroute's own behavior doesn't require live HTTP routes,
  but the parity checks against `_chain-detail.tsx`'s data source do.
