# Implementation Plan: Dynamic, Condition-Driven Approval Chains

Feature ID: 004-approval-chain-reconciliation
Spec: ./spec.md (must be APPROVED)
Created: 2026-09-14
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

One new backend domain, one new pure evaluator module, one additive function on
feature 002's shared resolver, two new tabs on the existing approvals page, one
Alembic revision.

**Central decision 1 (full justification in "Risks & decisions"): this feature
adopts the ER diagram's separate schema as SIX new tables in a NEW domain
`backend/app/approval_chains/`, and does NOT extend `backend/app/approvals/`'s
tables.** The legacy engine's **schema** (`ApprovalRequest` /
`ApprovalRoutingRule` / `ApprovalRoutingStep` / `ApproverGroup` /
`ApprovalDecision` / `ApprovalToken`) is not altered by one column, which is how
FR-21's cutover boundary and AC-20 stay structurally true: no in-flight legacy
row can ever enter this feature's code, because no legacy row is of this type.

**Central decision 2 (FR-22 — the reroute): new submissions of BOTH subject
types are intercepted at the single shared entry point
`app.approvals.service.submit_subject_for_approval`.** Both
`submit_contract_for_approval` (contracts, via `POST /approvals/requests`) and
`app.intake.approval_bridge.submit_request_for_approval` (intake requests, via
`app/intake/service.py` and `app/workflows/service.py`'s `approval` step) funnel
into that one function, so **one dispatch block covers AC-21 and AC-22 at
once**. The legacy engine's *behaviour* therefore does change for new
submissions — that is what FR-22 requires — while its schema, its in-flight
rows, its decision path, its `ApprovalToken` email flow and its re-submission
idempotency all remain exactly as they are (FR-21).

Total edit surface inside existing domains: **three files, ~50 lines**
(`app/approvals/service.py` ~12, `app/workflows/service.py` ~20,
`app/intake/service.py` ~18) — enumerated line-by-line in "The FR-22
interception point" below and owned explicitly in the path mapping.

**The ER diagram's `workflow_*` table names are deliberately NOT used.**
`backend/app/workflows/` already exists and is a Zapier-style automation-flow
engine (`Workflow` with a JSON `steps` array, `WorkflowRun`, `WorkflowStepRun`),
with its own `workflow:read/create/update/share` permission strings and a
`/workflow-builder` screen. Verified by reading `app/workflows/service.py`: its
`approval` step type delegates to `app.approvals` (it calls
`submit_request_for_approval` / `ApproverGroup`), so it is a *consumer* of the
approval ladder, not a second definition of one. Naming this feature's tables
`workflow_definitions` / `workflow_steps` / `workflow_instances` would put two
unrelated meanings of "workflow" in one schema. Every table here is prefixed
`approval_chain_`.

**New domain `backend/app/approval_chains/`** — mirrors the shape of
`backend/app/org_structure/` (feature 002) and `backend/app/menu_security/`
(feature 003): thin `routes.py`, all logic in `service.py`, Pydantic in
`schemas.py`, SQLAlchemy in `models.py`, org-scoped fetch helpers in
`access.py`. It adds four domain-private modules that those two features did not
need:

- `conditions.py` — the restricted, **non-`eval()`** condition evaluator
  (FR-2/FR-3/FR-4). Pure, DB-free, no `eval`/`exec`/`compile`/dynamic
  `getattr`, so AC-4's adversarial battery targets one function.
- `facts.py` — the **whitelisted fact projection** of a business record, **one
  frozen map per subject type** (`contract` and `intake_request`). A condition
  never names a DB column; it names a canonical fact (`contract_value`,
  `request_value`, `priority`, …) that `facts.py` maps to a model attribute.
  This is what makes AC-6's literal `contract_value (1,200,000) > 1,000,000`
  string true while `Contract.value_amount` stays the column name, and it is a
  second containment layer: an attacker-supplied `field` can only ever select
  from a frozen dict, never reach a relationship or a method.
- `dispatch.py` — the FR-22 seam. The **only** module `app/approvals/service.py`
  imports (lazily), exposing exactly two functions so the edit inside the legacy
  engine stays a handful of lines and this domain owns all the logic.
- `subjects.py` — resolves `(module, module_record_id)` back to the **existing**
  subject object (`ContractSubject` / `IntakeApprovalSubject`) so the chain
  drives the same lifecycle hooks the legacy ladder drives. No lifecycle logic
  is reimplemented.

**Eligible-approver resolution reuses feature 002's resolver**, via ONE new
additive function `org_access.users_holding_role()` in
`backend/app/core/org_access.py` — the inverse direction of `resolve_access`
("list everyone who holds role X at unit Y" instead of "does user U qualify"),
built on the same validity window, the same `ancestor_unit_ids` walk, the same
`Role.allows_hierarchy_rollup` flag and the same delegation-intersection rules.
No eligibility logic exists anywhere else.

**Untouched by design**: every file under `backend/app/approvals/` **except the
one dispatch block in `service.py`** (models, routes, schemas, the decision
path, `ApprovalToken`, `plan_chain`, `resolve_chain`, `_apply_decision`,
`reassign_rung`, `redeem_token_decision` — none change);
`app/intake/approval_bridge.py` (**zero edits** — it inherits the reroute
through the shared function); `app/authority/`
(`AuthorityGrant` unchanged — spec "Out of scope", and still enforced on
approve, see below); `app/walls/` (ethical walls keep overriding any ALLOW);
`app/core/screen_access.py`; `app/core/deps.py`; `app/jobs/tasks.py`;
`frontend/src/components/ui.tsx`.

```
POST /approvals/requests  ──► submit_contract_for_approval ─┐
intake/service.submit     ──► submit_request_for_approval  ─┤  (BOTH already funnel here)
workflows approval step   ──► submit_request_for_approval  ─┘
                                                            ▼
                       app.approvals.service.submit_subject_for_approval
                         1. subject.precheck(db)                       ← UNCHANGED
                         2. live legacy ApprovalRequest chain? -> return it  ← UNCHANGED (FR-21)
                         3. subject.allow_fast_lane / try_fast_lane    ← UNCHANGED
                         4. ►► NEW DISPATCH ◄◄  approval_chains.dispatch
                              active definition for (org, subject.kind)?
                                 yes -> start_chain_for_subject(...) ; return []   (FR-22)
                                 no  -> fall through, audit reroute_skipped
                         5. plan_chain + ApprovalRequest loop          ← UNCHANGED (legacy fallback)

approval_chains.dispatch.start_chain_for_subject
  └─ service.create_instance(subject=…)
       ├─ subjects.resolve_subject / the caller's subject   ← ContractSubject | IntakeApprovalSubject
       ├─ service._materialize_step                         ← FR-5, runs ONCE per step entry
       │    ├─ facts.build_facts(module, record)            ← frozen whitelist, per subject type
       │    ├─ conditions.evaluate_condition(expr, facts)   ← 5 operators, fail-closed
       │    └─ org_access.users_holding_role(...)           ← FR-18, feature 002 reused
       │         ├─ org_access.ancestor_unit_ids()          ← same walk
       │         └─ the same grant-validity + delegation predicate as resolve_access
       └─ subject.on_submit(...)                            ← the EXISTING lifecycle hook

POST .../requirements/{id}/decision  (approval_chain:decide + fresh role-holder re-check)
       ├─ subject.guard_can_decide(db)            ← the existing stage guard
       ├─ authority.enforce_authority(...)        ← on approve; parity with the legacy route
       └─ approve-all -> subject.on_complete(...) | reject -> subject.on_reject(...)
POST .../instances/{id}/recalculate   (approval_chain:recalculate)  ← the ONLY re-evaluation path
       └─ every decision/recalculation appends to approval_chain_history
          (append-only: ORM guard + PG trigger)
```

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | `backend/app/approval_chains/__init__.py` (new, empty package marker), `backend/app/approval_chains/models.py` (new: `ApprovalChainDefinition`, `ApprovalChainStep`, `ApprovalChainStepRule`, `ApprovalChainInstance`, `ApprovalChainRequirement`, `ApprovalChainHistory` + the two append-only ORM event listeners), `backend/app/models.py` (registry imports + `__all__`), `backend/alembic/versions/0044_approval_chains.py` (new — DDL + the append-only Postgres trigger) |
| backend-dev | `backend/app/approval_chains/conditions.py` (new), `backend/app/approval_chains/facts.py` (new), `backend/app/approval_chains/subjects.py` (new), `backend/app/approval_chains/dispatch.py` (new), `backend/app/approval_chains/access.py` (new), `backend/app/approval_chains/service.py` (new), `backend/app/approval_chains/schemas.py` (new), `backend/app/approval_chains/routes.py` (new), **`backend/app/approvals/service.py` (edit: the FR-22 dispatch block inside `submit_subject_for_approval` ONLY — 1 lazy import + ~11 lines; no other function in the file may be touched)**, **`backend/app/workflows/service.py` (edit: the no-contract `approval` step branch + the matching `refresh_run` resume branch ONLY — ~20 lines; no other step type may be touched)**, **`backend/app/intake/service.py` (edit: `start_approval_ladder` + `get_approval_chain` ONLY — ~18 lines; `_serialize_chain` and `_rung_label` must NOT be modified)**, `backend/app/core/org_access.py` (edit: **additive only** — `RoleHolder`, `users_holding_role`, `_grant_validity_clause`), `backend/app/core/rbac.py` (edit: 4 new permission strings + `DEFAULT_ROLE_PERMISSIONS` wiring), `backend/app/main.py` (edit: include 1 router), `backend/tests/test_condition_evaluator.py`, `backend/tests/test_org_access_role_holders.py`, `backend/tests/test_approval_chain_config_api.py`, `backend/tests/test_approval_chain_materialization.py`, `backend/tests/test_approval_chain_decisions.py`, `backend/tests/test_approval_chain_recalculate.py`, `backend/tests/test_approval_chain_history_append_only.py`, `backend/tests/test_approval_chain_reroute.py` (new — AC-21/AC-22/AC-20), `backend/tests/test_phase10_security_hardening.py` (edit **only** if a permission-catalog assertion breaks) |
| frontend-dev | `frontend/src/app/(app)/approvals/page.tsx` (edit: two new tab entries + two renders + the post-submit "chain started" toast), `frontend/src/app/(app)/approvals/_rules-builder.tsx` (edit: one `warning` `MessageBar` stating routing rules no longer apply to new submissions — no logic change), `frontend/src/app/(app)/approvals/_chains-tab.tsx` (new), `frontend/src/app/(app)/approvals/_chain-detail.tsx` (new), `frontend/src/app/(app)/approvals/_condition-rules-tab.tsx` (new), `frontend/src/lib/approval-chains.ts` (new: pure helpers), `frontend/src/lib/approval-chains.test.ts` (new), `frontend/src/lib/endpoints.ts` (edit: `approvalChainsApi` block), `frontend/src/lib/types.ts` (edit: new interfaces + the four optional `IntakeApprovalRung` fields and its `approval_request_id: ID \| null` correction), `frontend/src/app/(app)/intake/page.tsx` (edit: the inline strip's `decide()` branch + hide Reassign for a chain rung — no other change) |
| qa-engineer | `specs/004-approval-chain-reconciliation/verification.md` |

**Shared-hotspot note** — these files serialize; no two tasks touching the same
one may run in the same wave:

- `backend/app/models.py` — db-engineer registry task only.
- `backend/app/approval_chains/models.py` — db-engineer only, same task as the
  migration (model shape and DDL must land together).
- `backend/app/core/org_access.py`, `backend/app/core/rbac.py`,
  `backend/app/main.py` — one backend-dev task each; do not split.
- `backend/app/approval_chains/conditions.py` — one backend-dev task; the
  service task depends on it and must not edit it.
- **`backend/app/approvals/service.py`, `backend/app/workflows/service.py` and
  `backend/app/intake/service.py` — ONE backend-dev task, the "FR-22 reroute"
  task, owning all three files together.** They are the only existing-domain
  files this feature edits; the three edits are three halves of one behavior
  (start the chain / don't auto-advance the workflow when a chain was started /
  show the chain in the intake strip); all three consume the same
  `approval_chains.dispatch` surface; and they must land in the same commit —
  between edits 1 and 2 a workflow-driven intake approval auto-approves with
  zero sign-off, and between edits 1 and 3 the intake strip is blank. **Kept as
  one task, not split**: edit 3 is ~18 lines calling two frozen dispatch
  helpers, with no new dependency of its own, so a follow-on task would add a
  wave and a broken-in-between window to save nothing. This task **depends on**
  the `approval_chains` service + dispatch task and must run in a later wave,
  never in parallel with it.
- `frontend/src/app/(app)/intake/page.tsx` — one frontend-dev task (the
  `decide()` branch + hiding Reassign for a chain rung). It codes against the
  rung shape frozen above, so it **may run in parallel** with the backend
  reroute task; it only shares `src/lib/types.ts` with the other frontend work,
  which is already serialized below.
- `frontend/src/lib/endpoints.ts`, `frontend/src/lib/types.ts`,
  `frontend/src/app/(app)/approvals/page.tsx`,
  `frontend/src/app/(app)/approvals/_rules-builder.tsx` — one frontend-dev task
  each.
- `backend/app/approvals/`, `backend/app/workflows/` and `backend/app/intake/`
  **except each domain's `service.py`** (and within those three files, only the
  functions named in the path-mapping row), `backend/app/authority/**`,
  `backend/app/core/deps.py`, `backend/app/core/screen_access.py`,
  `backend/app/jobs/tasks.py`,
  `frontend/src/app/(app)/intake/_request-overview.tsx`,
  `frontend/src/components/ui.tsx` — **not touched by this feature.**

## Interface freeze (contract-first — fixed before implementation starts)

All paths are under the API prefix `/api/v1`. All timestamps are ISO-8601
strings with timezone in JSON, `timestamptz` in the DB. `ID` = string UUID.

### New permission strings

Added to `app/core/rbac.py` via a new `APPROVAL_CHAIN_PERMISSIONS` set folded
into `ALL_PERMISSIONS`, plus `DEFAULT_ROLE_PERMISSIONS` wiring. The existing
`APPROVAL_PERMISSIONS` set (`approval:read/decide/admin`) is **not modified** —
it belongs to the legacy engine and reusing it would blur the FR-21 boundary.

| Permission | Meaning | Default roles granted |
|---|---|---|
| `approval_chain:read` | View chain instances, materialized requirements, condition explanations and history (further narrowed per instance by the visibility rule below) | admin, member, legal_reviewer, approver |
| `approval_chain:manage` | Define / modify / deactivate chain definitions, steps, base requirements and condition rules (FR-1); also the FR-20 "which role has no eligible holder" view | admin |
| `approval_chain:decide` | Record an approve/reject decision against a materialized requirement (FR-15) | admin, legal_reviewer, approver |
| `approval_chain:recalculate` | Invoke the explicit recalculate action (FR-9) — deliberately distinct from `approval_chain:decide`, so an ordinary approver can never recalculate their own instance | admin |

Reused unchanged: `contract:approve` (starting a chain on a contract — the same
permission the legacy `POST /approvals/requests` uses).

**Instance visibility rule** (applied in `service` on top of
`approval_chain:read`, spec "Permissions, scoping & audit"): a caller may read
an instance iff `instance.org_id == actor.org_id` **AND** any of — the actor is
`instance.started_by_user_id`; the actor is an eligible holder of any of the
instance's materialized required roles; the actor holds `approval_chain:manage`
— **AND**, for `module == "contract"`, the existing
`app.contracts.access.user_can_access_contract(db, contract=…, user=…)` returns
True (ethical walls / clearance keep overriding, unchanged).

### API contract

Router prefix `/approval-chains`, tag `approval-chains`.

| Method | Path | Permission | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/approval-chains/fields` | `approval_chain:manage` | — (query: `module=contract\|intake_request`, default `contract`) | 200 `ConditionFieldCatalogResponse` | 403, 422 (unknown module) |
| GET | `/approval-chains/definitions` | `approval_chain:manage` | — (query: `module?`, `include_inactive=false`) | 200 `ChainDefinitionResponse[]` | 403 |
| POST | `/approval-chains/definitions` | `approval_chain:manage` | `ChainDefinitionCreate` | 201 `ChainDefinitionResponse` | 403, 409 (duplicate name+version, **or a second ACTIVE definition for the same `module`**), 422 (unknown module) |
| PATCH | `/approval-chains/definitions/{definition_id}` | `approval_chain:manage` | `ChainDefinitionUpdate` | 200 `ChainDefinitionResponse` | 403, 404, 409 (activating a second definition for the same `module`), 422 |
| DELETE | `/approval-chains/definitions/{definition_id}` | `approval_chain:manage` | — | 204 no body | 403, 404, 409 (already deleted) |
| POST | `/approval-chains/definitions/{definition_id}/steps` | `approval_chain:manage` | `ChainStepCreate` | 201 `ChainStepResponse` | 403, 404, 409 (duplicate step_key / sequence_order), 422 |
| PATCH | `/approval-chains/steps/{step_id}` | `approval_chain:manage` | `ChainStepUpdate` | 200 `ChainStepResponse` | 403, 404, 409, 422 |
| DELETE | `/approval-chains/steps/{step_id}` | `approval_chain:manage` | — | 204 no body | 403, 404, 409 |
| POST | `/approval-chains/steps/{step_id}/rules` | `approval_chain:manage` | `ChainStepRuleCreate` | 201 `ChainStepRuleResponse` | 403, 404 (step/role), 409 (duplicate base requirement for the role), 422 (malformed condition, unknown field/operator, base+condition mismatch) |
| PATCH | `/approval-chains/rules/{rule_id}` | `approval_chain:manage` | `ChainStepRuleUpdate` | 200 `ChainStepRuleResponse` | 403, 404, 409, 422 |
| DELETE | `/approval-chains/rules/{rule_id}` | `approval_chain:manage` | — | 204 no body | 403, 404, 409 |
| POST | `/approval-chains/instances` | `contract:approve` | `ChainInstanceCreate` | 201 `ChainInstanceDetailResponse` | 403, 404 (definition / record / org unit), 409 (a live instance already exists for this record+definition), 422 (definition inactive / has no steps) |

**FR-22 note:** in normal operation nothing calls `POST /approval-chains/instances`
— chains start automatically when a contract or intake request is submitted
through the platform's existing entry points, via the dispatch described under
"The FR-22 interception point". The endpoint remains as the explicit,
permission-gated way to start a chain for a record whose submission predates or
bypasses those entry points, and as the direct test surface for
materialization. Both paths converge on the same `service.create_instance`, so
there is exactly one materialization code path.
| GET | `/approval-chains/instances` | `approval_chain:read` | — (query: `module?`, `module_record_id?`, `status?`, `limit=100`, `offset=0`) | 200 `ChainInstanceSummary[]` | 403 |
| GET | `/approval-chains/instances/{instance_id}` | `approval_chain:read` | — | 200 `ChainInstanceDetailResponse` | 403, 404 |
| GET | `/approval-chains/instances/{instance_id}/history` | `approval_chain:read` | — | 200 `ChainHistoryEntry[]` | 403, 404 |
| GET | `/approval-chains/instances/{instance_id}/blocked` | `approval_chain:manage` | — | 200 `ChainBlockedResponse` | 403, 404 |
| POST | `/approval-chains/instances/{instance_id}/requirements/{requirement_id}/decision` | `approval_chain:decide` | `ChainDecisionPayload` | 200 `ChainInstanceDetailResponse` | 403 (not an eligible holder), 404, 409 (already decided / out-of-order / instance not pending / requirement superseded or cancelled), 422 (reject without comment) |
| POST | `/approval-chains/instances/{instance_id}/recalculate` | `approval_chain:recalculate` | `ChainRecalculatePayload` | 200 `ChainInstanceDetailResponse` | 403, 404, 409 (instance not pending) |

Full request/response JSON shapes:

```jsonc
// ---------- GET /api/v1/approval-chains/fields ----------
// The FROZEN whitelist a condition_expression's "field" may name (FR-2/FR-4).
// Anything outside this list is rejected at config time (422) and fails closed
// at evaluation time ("field_missing" -> not satisfied).
{
  "module": "contract",
  "operators": ["gt", "lt", "eq", "in", "contains"],
  "fields": [
    { "name": "contract_value",    "type": "number",  "label": "Contract value" },
    { "name": "currency",          "type": "string",  "label": "Currency" },
    { "name": "contract_type",     "type": "string",  "label": "Contract type" },
    { "name": "jurisdiction",      "type": "string",  "label": "Jurisdiction" },
    { "name": "risk_band",         "type": "string",  "label": "Risk band" },
    { "name": "risk_score",        "type": "number",  "label": "Risk score" },
    { "name": "confidentiality",   "type": "string",  "label": "Confidentiality" },
    { "name": "counterparty_name", "type": "string",  "label": "Counterparty" },
    { "name": "lifecycle_stage",   "type": "string",  "label": "Lifecycle stage" },
    { "name": "title",             "type": "string",  "label": "Title" },
    { "name": "matter_id",         "type": "string",  "label": "Matter" },
    { "name": "owner_user_id",     "type": "string",  "label": "Owner" },
    { "name": "effective_date",    "type": "string",  "label": "Effective date (ISO)" },
    { "name": "expiration_date",   "type": "string",  "label": "Expiration date (ISO)" },
    { "name": "renewal_due",       "type": "boolean", "label": "Renewal due" },
    { "name": "archived",          "type": "boolean", "label": "Archived" }
  ]
}
// With ?module=intake_request the SAME shape is returned with the intake fact
// set (FR-22 brings intake requests into scope):
{
  "module": "intake_request",
  "operators": ["gt", "lt", "eq", "in", "contains"],
  "fields": [
    { "name": "request_value",        "type": "number",  "label": "Requested value" },
    { "name": "currency",             "type": "string",  "label": "Currency" },
    { "name": "jurisdiction",         "type": "string",  "label": "Jurisdiction" },
    { "name": "request_type",         "type": "string",  "label": "Request type" },
    { "name": "request_type_key",     "type": "string",  "label": "Request type key" },
    { "name": "department",           "type": "string",  "label": "Department" },
    { "name": "priority",             "type": "string",  "label": "Priority" },
    { "name": "risk_band",            "type": "string",  "label": "Risk band" },
    { "name": "source",               "type": "string",  "label": "Source" },
    { "name": "status",               "type": "string",  "label": "Status" },
    { "name": "stage",                "type": "string",  "label": "Stage" },
    { "name": "work_status",          "type": "string",  "label": "Work status" },
    { "name": "sla_hours",            "type": "number",  "label": "SLA hours" },
    { "name": "sla_status",           "type": "string",  "label": "SLA status" },
    { "name": "request_ref",          "type": "string",  "label": "Reference" },
    { "name": "request_subject",      "type": "string",  "label": "Subject" },
    { "name": "requester_user_id",    "type": "string",  "label": "Requester" },
    { "name": "assigned_to_user_id",  "type": "string",  "label": "Assignee" },
    { "name": "matter_id",            "type": "string",  "label": "Matter" },
    { "name": "has_contract",         "type": "boolean", "label": "Has a contract" }
  ]
}

// ---------- ChainDefinitionResponse ----------
{
  "id": "d100…",
  "org_id": "a91c…",
  "name": "Standard contract approval",
  "module": "contract",              // "contract" | "intake_request" — the subject type
  "version": 1,
  "is_active": true,
  "is_default_seeded": false,        // true => created by migration 0044's cutover seed
  "steps": [ /* ChainStepResponse[] — ordered by sequence_order */ ],
  "created_at": "2026-09-14T10:00:00+00:00",
  "created_by_user_id": "u000…",
  "updated_at": "2026-09-14T10:00:00+00:00",
  "updated_by_user_id": null
}

// POST /api/v1/approval-chains/definitions (ChainDefinitionCreate)
{ "name": "Standard contract approval", "module": "contract", "version": 1, "is_active": true }

// PATCH /api/v1/approval-chains/definitions/{id} (ChainDefinitionUpdate) — all optional
{ "name": "Standard contract approval", "is_active": false }

// ---------- ChainStepResponse ----------
{
  "id": "s100…",
  "org_id": "a91c…",
  "definition_id": "d100…",
  "step_key": "legal_review",
  "name": "Legal review",
  "sequence_order": 1,
  "step_type": "approval",              // "action" | "approval"
  "approval_mode": "sequential",        // "sequential" | "parallel"  — FR-12, PER STEP
  "rules": [ /* ChainStepRuleResponse[] — ordered by sequence_order, then created_at */ ],
  "created_at": "2026-09-14T10:00:00+00:00",
  "created_by_user_id": "u000…",
  "updated_at": "2026-09-14T10:00:00+00:00",
  "updated_by_user_id": null
}

// POST /api/v1/approval-chains/definitions/{id}/steps (ChainStepCreate)
{ "step_key": "legal_review", "name": "Legal review", "sequence_order": 1,
  "step_type": "approval", "approval_mode": "sequential" }
// PATCH /api/v1/approval-chains/steps/{id} (ChainStepUpdate) — all optional
{ "name": "Legal review", "sequence_order": 2, "approval_mode": "parallel" }

// ---------- ChainStepRuleResponse ----------
// ONE row = ONE requirement source. is_base_requirement=true => unconditional
// (condition_expression is null). is_base_requirement=false => exactly one
// comparison in condition_expression. A compound business rule is MULTIPLE rows
// with the same required_role_id (FR-3) — never a nested boolean tree.
{
  "id": "r100…",
  "org_id": "a91c…",
  "step_id": "s100…",
  "is_base_requirement": false,
  "condition_expression": { "field": "contract_value", "operator": "gt", "value": 1000000 },
  "condition_text": "contract_value > 1,000,000",   // server-rendered, read-only
  "required_role_id": "role-fin…",
  "required_role_name": "finance_approver",
  "sequence_order": 2,
  "description": "High-value contracts need Finance sign-off",
  "is_active": true,
  "created_at": "2026-09-14T10:00:00+00:00",
  "created_by_user_id": "u000…",
  "updated_at": "2026-09-14T10:00:00+00:00",
  "updated_by_user_id": null
}

// POST /api/v1/approval-chains/steps/{id}/rules (ChainStepRuleCreate)
{
  "is_base_requirement": false,
  "condition_expression": { "field": "contract_value", "operator": "gt", "value": 1000000 },
  "required_role_id": "role-fin…",
  "sequence_order": 2,
  "description": "High-value contracts need Finance sign-off",
  "is_active": true
}
// A base requirement omits the condition entirely:
{ "is_base_requirement": true, "condition_expression": null,
  "required_role_id": "role-rev…", "sequence_order": 1, "is_active": true }
// PATCH /api/v1/approval-chains/rules/{id} (ChainStepRuleUpdate) — all optional
{ "condition_expression": { "field": "contract_value", "operator": "gt", "value": 2000000 },
  "sequence_order": 2, "is_active": true, "description": null }

// ---------- POST /api/v1/approval-chains/instances (ChainInstanceCreate) ----------
{
  "definition_id": "d100…",
  "module": "contract",
  "module_record_id": "c001…",
  "org_unit_id": null          // optional; null => the org's root unit (see service.create_instance)
}

// ---------- ChainInstanceSummary ----------
{
  "id": "i100…",
  "org_id": "a91c…",
  "definition_id": "d100…",
  "definition_name": "Standard contract approval",
  "module": "contract",
  "module_record_id": "c001…",
  "module_record_label": "MSA — Acme Corp",     // contract.title; "" when unreadable
  "org_unit_id": "0f2b…",
  "org_unit_name": "Region A",
  "current_step_id": "s100…",
  "current_step_key": "legal_review",
  "status": "pending",                          // "pending"|"approved"|"rejected"|"cancelled"
  "is_blocked": true,                           // >=1 pending requirement on the current step is unfulfillable (FR-19)
  "pending_requirement_count": 2,
  "started_by_user_id": "u001…",
  "created_at": "2026-09-14T10:00:00+00:00",
  "updated_at": "2026-09-14T10:05:00+00:00"
}

// ---------- ChainInstanceDetailResponse ----------
{
  "instance": { /* ChainInstanceSummary */ },
  "steps": [
    {
      "step_id": "s100…",
      "step_key": "legal_review",
      "name": "Legal review",
      "sequence_order": 1,
      "approval_mode": "sequential",
      "is_current": true,
      "is_complete": false,
      "is_blocked": true,
      "requirements": [
        {
          "id": "q100…",
          "instance_id": "i100…",
          "step_id": "s100…",
          "required_role_id": "role-rev…",
          "required_role_name": "contract_reviewer",
          "sequence_order": 1,
          "is_base_requirement": true,
          "triggered_by_rule_ids": [],
          "condition_explanations": [],
          "explanation": null,                  // null for a base requirement (FR-6 distinction)
          "status": "pending",                  // "pending"|"approved"|"rejected"|"cancelled"
          "counts_toward_completion": true,
          "superseded_at": null,
          "acted_by_user_id": null,
          "acted_by_label": null,
          "acted_as_role_id": null,
          "delegated_from_user_id": null,
          "acted_at": null,
          "comment": null,
          "is_unfulfillable": false,
          "eligible_user_count": 3,
          "can_decide": true,                   // THIS caller is an eligible holder AND ordering permits
          "blocked_by_sequence": false,         // sequential step, an earlier requirement is still pending
          "materialized_at": "2026-09-14T10:00:00+00:00"
        },
        {
          "id": "q101…",
          "required_role_id": "role-fin…",
          "required_role_name": "finance_approver",
          "sequence_order": 2,
          "is_base_requirement": false,
          "triggered_by_rule_ids": ["r100…", "r101…"],
          "condition_explanations": [
            { "rule_id": "r100…", "field": "contract_value", "operator": "gt",
              "value": 1000000, "actual": 1200000,
              "text": "contract_value (1,200,000) > 1,000,000" },
            { "rule_id": "r101…", "field": "jurisdiction", "operator": "eq",
              "value": "EU", "actual": "EU",
              "text": "jurisdiction (EU) == EU" }
          ],
          "explanation": "required because contract_value (1,200,000) > 1,000,000; and because jurisdiction (EU) == EU",
          "status": "pending",
          "counts_toward_completion": true,
          "superseded_at": null,
          "acted_by_user_id": null, "acted_by_label": null, "acted_as_role_id": null,
          "delegated_from_user_id": null, "acted_at": null, "comment": null,
          "is_unfulfillable": true,
          "eligible_user_count": 0,
          "can_decide": false,
          "blocked_by_sequence": true,
          "materialized_at": "2026-09-14T10:00:00+00:00"
        }
      ]
    }
  ],
  "blocking": [                                 // FR-20 — populated ONLY for a caller holding approval_chain:manage
    { "requirement_id": "q101…", "step_id": "s100…", "step_key": "legal_review",
      "required_role_id": "role-fin…", "required_role_name": "finance_approver",
      "sequence_order": 2, "org_unit_id": "0f2b…", "org_unit_name": "Region A" }
  ],
  "blocking_visible": true,                     // false => the caller lacks approval_chain:manage; `blocking` is []
  "can_recalculate": false,                     // = caller holds approval_chain:recalculate AND status == "pending"
  "history": [ /* ChainHistoryEntry[] — newest first */ ]
}

// ---------- ChainHistoryEntry (append-only, FR-10/FR-11) ----------
{
  "id": "h100…",
  "instance_id": "i100…",
  "step_id": "s100…",
  "requirement_id": "q100…",
  "action": "approved",        // "instance_created"|"materialized"|"approved"|"rejected"|
                               // "recalculated"|"blocked_no_eligible_approver"|
                               // "instance_completed"|"instance_rejected"
  "acted_by_user_id": "u002…",
  "acted_by_label": "Dana Reed",
  "acted_as_role_id": "role-rev…",
  "acted_as_role_name": "contract_reviewer",
  "delegated_from_user_id": "u001…",            // FR-21 (feature 002) two distinct fields — AC-19
  "delegated_from_label": "Sam Ortiz",
  "comments": "Looks good.",
  "before_json": null,
  "after_json": { "status": "approved" },
  "acted_at": "2026-09-14T10:05:00+00:00"
}
// For action="recalculated" the before/after carry the FULL required-approver
// lists (FR-10), each entry {requirement_id, required_role_id, required_role_name,
// sequence_order, is_base_requirement, triggered_by_rule_ids, status}.

// ---------- ChainBlockedResponse (GET .../blocked) ----------
{ "instance_id": "i100…", "blocking": [ /* same shape as `blocking` above */ ] }

// ---------- ChainDecisionPayload ----------
{ "decision": "approve", "comment": null }      // decision: "approve" | "reject"; reject REQUIRES a comment

// ---------- ChainRecalculatePayload ----------
{ "reason": "Contract value corrected from 1,200,000 to 500,000" }   // optional, stored in history.comments
```

**Audit actions written by each mutation** — every one via `write_audit_log(...)`
in the same transaction as the change, with `org_id=actor.org_id`,
`actor_user_id=actor.id`, and (for decisions) the
`org_access.delegation_audit_metadata`-shaped attribution block merged into
`metadata`:

| Endpoint | `action` | `resource_type` | before / after / metadata |
|---|---|---|---|
| POST `/definitions` | `approval_chain.definition_created` | `approval_chain_definition` | after: `{name, module, version, is_active}` |
| PATCH `/definitions/{id}` | `approval_chain.definition_updated` | `approval_chain_definition` | before/after: `{name, is_active}` |
| DELETE `/definitions/{id}` | `approval_chain.definition_deactivated` | `approval_chain_definition` | before: `{name, is_active}`; metadata: `{soft_delete: true}` |
| POST `/definitions/{id}/steps` | `approval_chain.step_created` | `approval_chain_step` | after: `{step_key, sequence_order, approval_mode}` |
| PATCH `/steps/{id}` | `approval_chain.step_updated` | `approval_chain_step` | before/after: `{name, sequence_order, approval_mode}` |
| DELETE `/steps/{id}` | `approval_chain.step_deleted` | `approval_chain_step` | before: `{step_key, sequence_order}` |
| POST `/steps/{id}/rules` | `approval_chain.rule_created` | `approval_chain_step_rule` | after: `{is_base_requirement, condition_expression, required_role_id, required_role_name, sequence_order}` |
| PATCH `/rules/{id}` | `approval_chain.rule_updated` | `approval_chain_step_rule` | before/after: the same five fields — this is the spec's "prior and new definition (condition, required role, sequence position, base vs. conditional)" |
| DELETE `/rules/{id}` | `approval_chain.rule_deleted` | `approval_chain_step_rule` | before: the same five fields |
| POST `/instances` | `approval_chain.instance_created` | `approval_chain_instance` | after: `{definition_id, module, module_record_id, org_unit_id, current_step_id}`; metadata: `{materialized_requirements: [...], unfulfillable_role_ids: [...]}` |
| POST `.../decision` | `approval_chain.decided` | `approval_chain_requirement` | after: `{decision, requirement_id, required_role_id, step_id, sequence_order}`; metadata: `{acting_user_id, on_behalf_of_user_id?, delegation_id?}` |
| POST `.../recalculate` | `approval_chain.recalculated` | `approval_chain_instance` | before/after: the FULL required-approver lists (identical payload to the history row); metadata: `{reason}` — FR-9's "distinguishable in the audit trail from a normal approval action" |
| the FR-22 dispatch, when **no** active definition exists for the org+subject type and the submission falls through to the legacy engine | `approval_chain.reroute_skipped` | `contract` / `intake_request` | metadata: `{reason: "no_active_chain_definition"}` — makes an FR-22 miss observable instead of silent |

Step advancement and instance completion additionally write
`approval_chain.step_materialized`, `approval_chain.completed` and
`approval_chain.rejected` audit rows (`resource_type="approval_chain_instance"`)
so the audit trail is complete without reading the history table.

### The condition evaluator — `backend/app/approval_chains/conditions.py`

Pure, DB-free, import-free of any model. **No `eval`, `exec`, `compile`,
`__import__`, `getattr` on attacker-controlled names, no regex built from
input, no f-string interpolation into any executable context.** Dispatch is a
lookup in a frozen dict of five Python callables; an unknown key is a miss, not
a fallback.

```python
# backend/app/approval_chains/conditions.py
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

OPERATORS: frozenset[str] = frozenset({"gt", "lt", "eq", "in", "contains"})
OPERATOR_SYMBOLS: dict[str, str] = {
    "gt": ">", "lt": "<", "eq": "==", "in": "is one of", "contains": "contains",
}
# Hard caps — an "extremely large value" payload (AC-4) is malformed, never a
# DoS or a memory event.
MAX_FIELD_LEN = 120
MAX_STRING_LEN = 500
MAX_LIST_ITEMS = 100
MAX_ABS_NUMBER = 1e15

REASONS = (
    "satisfied", "not_satisfied",
    "malformed_expression", "unknown_operator", "unknown_field",
    "field_missing", "type_mismatch", "value_out_of_bounds",
)

@dataclass(frozen=True)
class ConditionResult:
    satisfied: bool          # ALWAYS False unless the comparison is well-formed AND true
    malformed: bool          # True => the rule definition itself is invalid (FR-4 fail-closed)
    reason: str              # one of REASONS
    field: str | None
    operator: str | None
    value: Any               # the threshold/comparison value from the definition
    actual: Any              # the item's value at evaluation time (FR-7)
    text: str                # human-readable, "" when malformed

def evaluate_condition(
    expression: Any,
    facts: Mapping[str, Any],
    *,
    allowed_fields: frozenset[str] | None = None,
) -> ConditionResult:
    """Evaluate ONE comparison against ONE named fact. Never raises.

    ``expression`` is UNTRUSTED structured data read straight out of a JSONB
    column. Accepted shape, exactly: a dict with EXACTLY the keys
    {"field", "operator", "value"}. Anything else — a non-dict, a missing key,
    an extra key (including "and"/"or"/"not"/"expr"/"code"), a non-string field
    or operator, an operator outside OPERATORS, a nested dict/list where a
    scalar is required, a string longer than MAX_STRING_LEN, a list longer than
    MAX_LIST_ITEMS or containing a non-scalar, a non-finite or out-of-bounds
    number — returns ``satisfied=False, malformed=True``. A field absent from
    ``facts`` (or from ``allowed_fields`` when given) returns
    ``satisfied=False`` with reason "field_missing"/"unknown_field" — NOT an
    exception, so the step's other rules still evaluate (FR-4, AC-3, AC-4).
    The whole body is additionally wrapped in ``try/except Exception`` whose
    handler returns a malformed result — belt and braces, so no unforeseen
    input can raise out of this function.
    """

def render_condition_text(expression: Any) -> str:
    """'contract_value > 1,000,000' — the config-time label (no actual value).
    Returns '(invalid condition)' for a malformed expression."""

def render_explanation(result: ConditionResult) -> str:
    """'contract_value (1,200,000) > 1,000,000' — the FR-7/AC-6 string, using
    the item's ACTUAL value at evaluation time. Numbers are rendered with
    thousands separators; strings verbatim; lists as 'a, b, c'."""

def validate_expression(expression: Any, *, allowed_fields: frozenset[str]) -> None:
    """Config-time gate used by the rule create/update endpoints: raises
    HTTPException(422, "<specific reason>") for anything evaluate_condition
    would call malformed. A malformed rule therefore normally cannot be stored
    at all; evaluate_condition's fail-closed path is the defence for rows
    written before this feature, by a future migration, or directly in SQL."""
```

Operator semantics (frozen — the five of FR-2, nothing more):

| op | Requires | Satisfied when | Anything else |
|---|---|---|---|
| `gt` | both `value` and `actual` numeric (`int`/`float`, **`bool` excluded**) | `actual > value` | `not_satisfied` / `type_mismatch` |
| `lt` | same | `actual < value` | `not_satisfied` / `type_mismatch` |
| `eq` | scalars | numbers: numeric equality; strings: `strip().lower()` equality (matching this codebase's existing `_matches` / `_grant_covers` convention); booleans: identity; `None` actual: only equal to `None` value | `not_satisfied` |
| `in` | `value` is a list of ≤ `MAX_LIST_ITEMS` scalars | `actual` equals any item under the `eq` rule | `malformed` when `value` is not such a list |
| `contains` | `value` is a scalar | `actual` is a string → case-insensitive substring; `actual` is a list → any item equals `value` under `eq` | `not_satisfied` / `type_mismatch` |

**There is no AND/OR/NOT combinator, no nesting, and no sixth operator** — a
compound business rule is multiple rows (FR-3), enforced by the "exactly three
keys" shape check, so a `{"and": [...]}` payload is rejected as malformed
rather than interpreted (AC-3).

### The fact projection — `backend/app/approval_chains/facts.py`

```python
CONTRACT_FACTS: dict[str, str] = {   # fact name -> Contract attribute name
    "contract_value": "value_amount",
    "currency": "currency",
    "contract_type": "contract_type",
    "jurisdiction": "jurisdiction",
    "risk_band": "risk_band",              # falls back to risk_level when null
    "risk_score": "risk_score",
    "confidentiality": "confidentiality",
    "counterparty_name": "counterparty_name",
    "lifecycle_stage": "lifecycle_stage",
    "title": "title",
    "matter_id": "matter_id",
    "owner_user_id": "owner_user_id",
    "effective_date": "effective_date",    # rendered as an ISO date string
    "expiration_date": "expiration_date",  # rendered as an ISO date string
    "renewal_due": "renewal_due",
    "archived": "archived",
}
# FR-22 brings intake requests into scope, so there is a SECOND frozen map.
# Every name below is a real column on IntakeRequest (verified against
# app/intake/models.py) or a documented derivation the existing
# IntakeApprovalSubject already performs.
INTAKE_REQUEST_FACTS: dict[str, str] = {   # fact name -> IntakeRequest attribute
    "request_ref": "ref",
    "request_subject": "subject",
    "request_type": "type_label",
    "department": "department",
    "priority": "priority",                # "Critical"|"High"|"Medium"|"Low"
    "source": "source",                    # form|copilot|email|api|seed
    "status": "status",                    # open|escalated|approved|closed
    "stage": "stage",
    "work_status": "work_status",
    "sla_hours": "sla_hours",
    "sla_status": "sla_status",            # on_track|at_risk|overdue
    "requester_user_id": "requester_user_id",
    "assigned_to_user_id": "assigned_to_user_id",
    "matter_id": "matter_id",
}
# Derived intake facts (NOT plain columns) — computed by build_facts, each
# reusing the semantics app/intake/approval_bridge.IntakeApprovalSubject
# already uses so a condition rule and the legacy routing matcher read the
# same numbers:
#   request_value     — the monetary value parsed out of field_values, trying
#                       the keys ("value","amount","contract_value",
#                       "deal_value","annual_value") in order (the bridge's
#                       _VALUE_KEYS list, copied with a comment pointing at it)
#   currency          — field_values["currency"]
#   jurisdiction      — field_values["jurisdiction"]
#   risk_band         — priority.lower() (the bridge's _PRIORITY_TO_BAND)
#   request_type_key  — IntakeRequestType.key for request_type_id, else None
#   has_contract      — bool(contract_id)
#
# field_values is free-form JSON supplied by the requester. Only the three keys
# above are projected; an arbitrary key can NEVER be reached by a condition,
# because the whitelist is a frozen dict, not a lookup into field_values.

MODULE_FACTS: dict[str, dict[str, str]] = {
    "contract": CONTRACT_FACTS,
    "intake_request": INTAKE_REQUEST_FACTS,
}
FIELD_TYPES: dict[str, dict[str, str]] = {...}   # per module; drives GET /approval-chains/fields

def allowed_fields(module: str) -> frozenset[str]: ...
def build_facts(db: Session, *, module: str, record_id: str, org_id: str) -> dict[str, Any]:
    """Load the org-scoped record and project it through the frozen whitelist
    for its module. Values are coerced to JSON-safe scalars
    (float | str | bool | None) so a stored explanation never holds a
    Decimal/date object. Raises HTTPException(404) when the record is missing or
    belongs to another org. The `Contract` / `IntakeRequest` model imports are
    function-local, matching this codebase's lazy-import convention and keeping
    `approval_chains` free of a module-level dependency on either domain."""
```

`module` takes exactly two values, `"contract"` and `"intake_request"` — the two
subject types FR-22 names — enforced by a CHECK on both
`approval_chain_definition.module` and `approval_chain_instance.module`. **This
column IS the subject-type discriminator** (see the decision in "Risks &
decisions"): a rule's `condition_expression.field` is validated at config time
against `allowed_fields(definition.module)`, so a contract definition can never
reference `priority` and an intake definition can never reference
`contract_value`. A third subject type is a deliberate migration plus a fact-map
entry, never an accident.

### Eligible-approver resolution — `backend/app/core/org_access.py` (ADDITIVE)

Three additions to feature 002's resolver module. **No existing function's
behavior changes**; `resolve_access`, `ancestor_unit_ids`, `descendant_unit_ids`
and `effective_permission_values` keep their exact current semantics.

```python
@dataclass(frozen=True)
class RoleHolder:
    user_id: str
    role_id: str
    org_unit_id: str                      # the unit the satisfying grant sits at
    via_delegation_id: str | None = None
    on_behalf_of_user_id: str | None = None   # the delegator, when via_delegation_id is set

def _grant_validity_clause(at: datetime):
    """The soft-delete + validity-window predicate, extracted VERBATIM from the
    body of active_grants_for_user (which now calls it). Behaviour-identical;
    this exists so users_holding_role cannot drift from it."""

def users_holding_role(
    db: Session,
    *,
    org_id: str,
    role_id: str,
    org_unit_id: str,
    at: datetime | None = None,
    include_delegates: bool = True,
) -> list[RoleHolder]:
    """The INVERSE of resolve_access: every user who holds ``role_id`` at
    ``org_unit_id`` right now (FR-18). Deduplicated by user_id, native grants
    winning over delegated ones.

    1. Load the role org-scoped; missing/other-org -> [].
    2. chain = ancestor_unit_ids(db, org_id=org_id, org_unit_id=org_unit_id,
       include_self=True); ancestor_only = set(chain[1:]).
    3. Native pass: UserRoleGrant rows where org_id matches, role_id matches,
       _grant_validity_clause(at) holds, and (grant.org_unit_id == org_unit_id
       OR (role.allows_hierarchy_rollup AND grant.org_unit_id in ancestor_only))
       — byte-for-byte the same predicate as resolve_access step 3, so AC-15's
       parent-unit holder is included and no downward/lateral match ever is.
    4. Delegated pass (include_delegates): every Delegation in this org with
       deleted_at IS NULL, status == 'active', start_date <= at <= end_date,
       (role_id IS NULL OR role_id == role_id), and (org_unit_id IS NULL OR
       org_unit_id in descendant_unit_ids(delegation.org_unit_id)) whose
       DELEGATOR appears in the native set -> the DELEGATE is added with
       via_delegation_id / on_behalf_of_user_id set. Narrow-only and one hop
       (FR-14/FR-17 of feature 002) — a delegate is never a delegator here.
    5. Evaluated fresh on every call; nothing cached.
    """
```

`users_holding_role` is the **single** answer to "who may fulfil this
requirement", called from exactly two places in this feature:

- `service._materialize_step` — to compute `eligible_user_count` and set
  `is_unfulfillable = (count == 0)` (FR-19, AC-16);
- `service.record_decision` — re-run **fresh at act time** against the acting
  user (the spec's "loses eligibility before acting" edge case); the
  materialization-time count never authorizes anything.

### The FR-22 interception point — `dispatch.py` + two surgical edits

**Decision: intercept inside the shared `submit_subject_for_approval`, not in
its two callers.** Justified in "Risks & decisions"; the mechanics are frozen
here because the reroute is the one place this feature reaches into another
domain.

```python
# backend/app/approval_chains/dispatch.py — the ONLY module app/approvals imports

def active_definition_for(
    db: Session, *, org_id: str, subject_kind: str
) -> ApprovalChainDefinition | None:
    """The single active, non-deleted definition for (org_id, module=subject_kind).
    `subject_kind` is the existing subject protocol's `kind` attribute, whose
    values are already exactly "contract" and "intake_request" (verified in
    app/approvals/service.ContractSubject and
    app/intake/approval_bridge.IntakeApprovalSubject) — so the discriminator
    needs no translation table. Returns None when the org has none."""

def start_chain_for_subject(
    db: Session, *, actor: User, subject, definition: ApprovalChainDefinition,
    request_id: str | None = None,
) -> ApprovalChainInstance:
    """Idempotent: returns the existing live instance for
    (definition, subject.kind, subject.id) unchanged if one exists, else calls
    service.create_instance(...) — which materializes step 1 (FR-5) and fires
    subject.on_submit(...). Never raises 409 on re-submission, because
    submit_subject_for_approval is contractually idempotent."""

def live_instance_for(
    db: Session, *, org_id: str, module: str, module_record_id: str
) -> ApprovalChainInstance | None:
    """Read-only lookup used by app/workflows/service.py to tell 'a chain was
    started' apart from 'nothing to approve', and by app/intake/service.py to
    find the chain backing a rerouted request. Returns the newest non-deleted
    instance (pending first, then most recently updated) or None."""

def intake_strip_rungs(
    db: Session, *, instance: ApprovalChainInstance
) -> list[dict]:
    """The intake ladder strip's rung dicts, built from a chain instance's
    materialized requirements — the shape `app/intake/service._serialize_chain`
    already returns, so the frontend's existing `IntakeApprovalRung` renderer
    needs no restructuring (see the exact shape below). One rung per
    requirement, ordered by (step.sequence_order, requirement.sequence_order),
    excluding soft-deleted and superseded rows. Pure read — it evaluates NO
    conditions (FR-5)."""

def intake_planned_rungs(
    db: Session, *, org_id: str, definition: ApprovalChainDefinition
) -> list[dict]:
    """The same rung shape for a request that has NOT been submitted yet:
    the first step's BASE requirements only, each with status "planned".
    Condition rules are deliberately NOT evaluated here — FR-5 confines
    evaluation to chain entry, and a read-path preview would violate it. The
    strip therefore shows the guaranteed rungs before submission and the full
    materialized list after."""
```

**Edit 1 — `backend/app/approvals/service.py`, inside
`submit_subject_for_approval` only** (~11 lines + one lazy import), inserted
**after** `subject.precheck(db)`, **after** the existing live-legacy-chain
idempotency query, **after** the fast-lane branch, and **before** the
`plan_chain(...)` call:

```python
    # FR-22: after this feature ships, a NEW submission of either subject type
    # is routed to the condition-driven engine. Everything above this line is
    # untouched, which is what keeps FR-21 true: an in-flight legacy chain is
    # returned by the idempotency query above and never reaches this branch.
    from app.approval_chains import dispatch as chain_dispatch

    definition = chain_dispatch.active_definition_for(
        db, org_id=user.org_id, subject_kind=subject.kind
    )
    if definition is not None:
        chain_dispatch.start_chain_for_subject(
            db, actor=user, subject=subject, definition=definition, request_id=request_id
        )
        return []          # no legacy ApprovalRequest rows are created
    write_audit_log(
        db, action="approval_chain.reroute_skipped", resource_type=subject.kind,
        resource_id=subject.id, org_id=user.org_id, actor_user_id=user.id,
        request_id=request_id, metadata={"reason": "no_active_chain_definition"},
    )
```

Three properties make this the safe seam:

- **`return []` is an already-existing return value** of this function — the
  NDA fast-lane path returns `[]` today — so every caller already handles "no
  `ApprovalRequest` rows were created" without a type change. The declared
  return type `list[ApprovalRequest]` is unchanged.
- **Placement after the idempotency query is FR-21's guarantee**: re-submitting
  a contract or request whose legacy chain is still `PENDING`/`WAITING` returns
  that legacy chain, exactly as today, and never starts a condition-driven one.
- **The fall-through is fail-safe**: with no definition configured the legacy
  engine runs exactly as today and an audit row records the skip. A chain with
  no definition would otherwise mean "zero required approvers", i.e. a silent
  auto-approval. The cutover seed (migration step 8) exists so this branch is
  effectively unreachable in practice, and the audit row is how an operator
  finds it if it ever is.

**Edit 2 — `backend/app/workflows/service.py`, the no-contract `approval`
branch and its `refresh_run` resume branch only** (~20 lines). This edit is
**mandatory, not cosmetic**: the branch currently reads

```python
            if not reqs:
                if sr: sr.note = "No approval rungs required — skipped."
                return "advance"
```

and an `advance` there lets `_finalize_intake_if_approved` approve the request
with **zero sign-off** — precisely the M2 bug its own comment warns about. After
the reroute, `reqs` is always `[]` for intake, so without this edit every
workflow-driven intake approval auto-approves. The two halves:

```python
            # creation side
            instance = chain_dispatch.live_instance_for(
                db, org_id=run.org_id, module="intake_request", module_record_id=req.id
            )
            if not reqs and instance is None:
                if sr: sr.note = "No approval rungs required — skipped."
                return "advance"
            if sr:
                sr.status = "waiting_job"
                sr.result = (
                    {"chain_instance_id": instance.id} if instance is not None
                    else {"approval_ids": [r.id for r in reqs]}
                )
            return "wait"

            # resume side, in refresh_run's `if not run.contract_id:` / t == "approval" block,
            # as a new branch BEFORE the existing `approval_ids` branch (which is untouched):
            cid = (sr.result or {}).get("chain_instance_id") if sr and sr.result else None
            if cid:
                inst = db.get(ApprovalChainInstance, cid)
                if inst is not None and inst.status == "approved":   # -> done, advance
                    ...
                if inst is not None and inst.status == "rejected":   # -> failed, db.commit()
                    ...
```

mirroring the existing `approval_ids` branch's done/failed handling
byte-for-byte (including its `db.commit()` on the rejection path — the M4 fix).
**The contract branch of the same step type needs no edit**: it already sets
`waiting_job` + `"wait"` unconditionally, ignoring the return value, and resumes
off the contract's lifecycle stage — which the new engine still drives, because
`service._advance_step` calls the existing `subject.on_complete(...)` →
`transition_contract_stage(SIGNATURE)`.

**Edit 3 — `backend/app/intake/service.py`, two functions only** (~18 lines).
The intake request detail screen renders an inline approval-ladder strip from
`_serialize_chain(db, rows)`; after the reroute `rows` is `[]`, so without this
edit the strip is blank for every rerouted request — a visible regression, not a
cosmetic one, since it is where intake staff read approval status. **Neither
`_serialize_chain` nor `_rung_label` is modified**; both keep serving legacy
rows. Two call sites gain a branch:

```python
# 1. start_approval_ladder(...) — the submit response's `chain` field
    requests = await submit_request_for_approval(...)
    db.commit(); db.refresh(r)
    chain = _serialize_chain(db, requests)
    if not chain:                                   # rerouted (or fast-laned)
        from app.approval_chains import dispatch as chain_dispatch
        inst = chain_dispatch.live_instance_for(
            db, org_id=actor.org_id, module="intake_request", module_record_id=r.id
        )
        if inst is not None:
            chain = chain_dispatch.intake_strip_rungs(db, instance=inst)
    return {"request": serialize_request(db, r), "chain": chain}

# 2. get_approval_chain(...) — GET /intake/requests/{id}/chain, the strip's
#    react-query source (["intake-chain", id]); THIS is the one that matters on
#    every subsequent load. Inserted as the FIRST branch, before the existing
#    legacy ApprovalRequest query, which is otherwise untouched:
    inst = chain_dispatch.live_instance_for(
        db, org_id=actor.org_id, module="intake_request", module_record_id=r.id
    )
    if inst is not None:
        return chain_dispatch.intake_strip_rungs(db, instance=inst)
    # ... existing legacy `rows` query + its _serialize_chain return ...
    # ... existing "closed/approved -> []" guard ...
    # and the planned-ladder preview now prefers the chain definition when one
    # is active, since routing rules no longer determine new submissions:
    definition = chain_dispatch.active_definition_for(
        db, org_id=actor.org_id, subject_kind="intake_request"
    )
    if definition is not None:
        return chain_dispatch.intake_planned_rungs(db, org_id=actor.org_id, definition=definition)
    # ... existing plan_chain preview, unchanged, for the no-definition org ...
```

Checking the instance **first** is deliberate: a request that has both a legacy
chain from before cutover and (somehow) a new instance shows the live one, and
the common case costs one indexed lookup. The legacy branch is reached
unchanged for every pre-cutover request.

**The rung shape `intake_strip_rungs` / `intake_planned_rungs` return** — a
superset of today's, so nothing that renders it breaks:

```jsonc
{
  "approval_request_id": null,          // ALWAYS null for chain rungs (the field stays, so
                                        // `key={rung.approval_request_id ?? \`p${step_order}\`}`
                                        // and the existing `if (!pending?.approval_request_id)`
                                        // guard keep working untouched)
  "requirement_id": "q100…",            // NEW — present iff this rung came from the new engine
  "chain_instance_id": "i100…",         // NEW — the pair the decide call needs
  "step_order": 2,                      // flat 1..N ordinal across the instance, so the existing
                                        // "step N" label stays meaningful
  "status": "pending",                  // pending|approved|rejected|cancelled|planned — the same
                                        // tokens the strip's RUNG_LABEL map already handles
  "approver_label": "finance_approver", // the required role's name (what _rung_label already
                                        // returns for a role-targeted legacy rung)
  "due_at": null,                       // chains carry no due date in v1
  "explanation": "required because contract_value (1,200,000) > 1,000,000",  // NEW, null for base
  "blocked": false                      // NEW — mirrors requirement.is_unfulfillable (FR-19)
}
```

`mode` / `approvals` / `needed` are **omitted** (they are already optional on
the frontend type and mean quorum, which the new engine does not have — one
requirement is one required approval).

**Frontend consequence, frozen here** (owned by frontend-dev, not by the
reroute task): `IntakeApprovalRung` in `src/lib/types.ts` gains
`requirement_id?: ID | null`, `chain_instance_id?: ID | null`,
`explanation?: string | null`, `blocked?: boolean`, and
`approval_request_id` is corrected to `ID | null` (it is *already* null on the
existing "planned" preview path — today's type is simply wrong about it). In
`src/app/(app)/intake/page.tsx` the inline `decide()` handler branches: when
`pending.requirement_id` is set it calls
`approvalChainsApi.decide(pending.chain_instance_id!, pending.requirement_id, …)`,
otherwise the existing `approvalsApi.decide(...)`; the Reassign control is
hidden for a chain rung (the new engine has no reassign concept — FR-15 routes
by role, and reassignment is not in this spec). `_request-overview.tsx` needs
**no edit** — it only renders `approver_label` / `step_order` / `mode` /
`needed`, all still present or optional.

**Lifecycle parity — `subjects.py`.** A rerouted chain must move its subject
exactly as the legacy ladder does, or contracts stall in REVIEW and intake
requests never reach `approved`. Rather than reimplement any of it, the new
engine calls the **existing** subject protocol:

| Chain event | Call | Contract effect | Intake effect |
|---|---|---|---|
| instance created | `subject.on_submit(...)` | stage → APPROVAL | `intake.submitted_for_approval` audit + timeline |
| every decision, before accepting | `subject.guard_can_decide(db)` | 409 unless in APPROVAL stage | 409 when the request is closed |
| any requirement rejected | `subject.on_reject(..., comment=…)` | stage → REVIEW | status → open, `intake.approval_rejected` |
| last step approved | `subject.on_complete(...)` | stage → SIGNATURE | gate passed, or `finalize_approved` when no flow is running |

`subjects.resolve_subject(db, *, module, record_id, org_id)` rebuilds the object
from `(module, module_record_id)` for paths that do not already have one (the
decision and recalculate endpoints), via lazy imports of
`app.approvals.service.ContractSubject` and
`app.intake.approval_bridge.build_intake_subject`. **Every import across the
`approvals ↔ approval_chains` boundary is function-local in both directions**,
so no import cycle exists at module load — the same lazy-import convention
`approvals/service.py` already uses for `app.intake.approval_bridge` and
`app.authority.service`.

**Authority parity.** `service.record_decision` calls
`app.authority.service.enforce_authority(db, user=actor,
action="contract:approve", contract=subject, resource_type=
"approval_chain_requirement", resource_id=requirement.id, request_id=…)` on an
**approve** decision (never on a reject), exactly as the legacy
`POST /approvals/requests/{id}/decision` route does. `AuthorityGrant` is not
modified; without this call the reroute would silently drop a live ABAC gate.

### Database schema

Six new tables, all in `backend/app/approval_chains/models.py`. All FKs to
`role.id`, `user.id`, `org_unit.id` and `contract.id` are declared **by string**
so this module imports no other domain's models (no import cycle).

Five of the six use `TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin,
SoftDeleteMixin, TimestampMixin`. **`approval_chain_history` deliberately uses
only `TableNameMixin, IdMixin, OrgScopedMixin`** — see its note below.

**`approval_chain_definition`** (`ApprovalChainDefinition`)

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK, default `new_uuid()` |
| org_id | varchar(36) | NOT NULL, indexed (`OrgScopedMixin`) |
| name | varchar(200) | NOT NULL |
| module | varchar(40) | NOT NULL, `CHECK module IN ('contract','intake_request')` (`ck_approval_chain_definition_module`) — **the subject-type discriminator** |
| version | integer | NOT NULL default 1 |
| is_active | boolean | NOT NULL default true |
| is_default_seeded | boolean | NOT NULL default false — true for the rows migration `0044` creates at cutover, so the UI can prompt an admin to review them |
| created_at / updated_at | timestamptz | NOT NULL |
| created_by_user_id / updated_by_user_id | varchar(36) | NULL |
| deleted_at / deleted_by_user_id | timestamptz / varchar(36) | NULL |
| legal_hold | boolean | NOT NULL default false |

- `uq_approval_chain_definition_scope`: UNIQUE `(org_id, name, version)` WHERE
  `deleted_at IS NULL` (`postgresql_where` + `sqlite_where`).
- **`uq_approval_chain_definition_active_module`: UNIQUE `(org_id, module)`
  WHERE `is_active AND deleted_at IS NULL`** — at most ONE active definition per
  org per subject type, which is what makes
  `dispatch.active_definition_for(org, subject_kind)` deterministic without
  reintroducing the legacy criteria matcher. Activating a second definition is a
  409; an admin deactivates the old one first.
- `ix_approval_chain_definition_lookup` on `(org_id, module, is_active)`.

**`approval_chain_step`** (`ApprovalChainStep`)

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| org_id | varchar(36) | NOT NULL, indexed |
| definition_id | varchar(36) | NOT NULL, FK `approval_chain_definition.id` ON DELETE CASCADE, indexed |
| step_key | varchar(80) | NOT NULL |
| name | varchar(200) | NOT NULL |
| sequence_order | integer | NOT NULL |
| step_type | varchar(20) | NOT NULL default `'approval'`, `CHECK step_type IN ('action','approval')` |
| approval_mode | varchar(20) | NOT NULL default `'sequential'`, `CHECK approval_mode IN ('sequential','parallel')` — **FR-12: per step, defaults to enforcing order** |
| (actor / soft-delete / timestamp columns as above) | | |

- `uq_approval_chain_step_key`: UNIQUE `(definition_id, step_key)` WHERE `deleted_at IS NULL`.
- `uq_approval_chain_step_order`: UNIQUE `(definition_id, sequence_order)` WHERE `deleted_at IS NULL`.

**`approval_chain_step_rule`** (`ApprovalChainStepRule`) — the single config
table for BOTH base requirements and condition rules (the ER diagram's own
`is_base_requirement` flag makes a second `*_step_roles` table redundant).

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| org_id | varchar(36) | NOT NULL, indexed |
| step_id | varchar(36) | NOT NULL, FK `approval_chain_step.id` ON DELETE CASCADE, indexed |
| is_base_requirement | boolean | NOT NULL default false |
| condition_expression | JSON (JSONB on PG) | NULL — NULL iff `is_base_requirement` |
| required_role_id | varchar(36) | NOT NULL, FK `role.id` ON DELETE RESTRICT, indexed |
| sequence_order | integer | NOT NULL default 1 |
| description | varchar(300) | NULL |
| is_active | boolean | NOT NULL default true |
| (actor / soft-delete / timestamp columns as above) | | |

- `ck_approval_chain_step_rule_base_xor_condition`: `CHECK ((is_base_requirement
  AND condition_expression IS NULL) OR (NOT is_base_requirement AND
  condition_expression IS NOT NULL))` — a base requirement cannot smuggle in a
  condition and a condition rule cannot be conditionless.
- `uq_approval_chain_step_rule_base`: UNIQUE `(step_id, required_role_id)` WHERE
  `is_base_requirement AND deleted_at IS NULL` — one base requirement per role
  per step. Condition rules are deliberately NOT unique per role: FR-3 requires
  several rules for the same role.
- `ix_approval_chain_step_rule_step` on `(step_id, is_active, deleted_at)`.

**`approval_chain_instance`** (`ApprovalChainInstance`)

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| org_id | varchar(36) | NOT NULL, indexed |
| definition_id | varchar(36) | NOT NULL, FK `approval_chain_definition.id` ON DELETE RESTRICT, indexed |
| module | varchar(40) | NOT NULL, `CHECK module IN ('contract','intake_request')` — copied from the definition at creation; `module_record_id` points at `contract.id` or `intake_request.id` accordingly (no FK, since the target table varies — the same pattern `intake_request.matter_id` already uses) |
| module_record_id | varchar(36) | NOT NULL |
| org_unit_id | varchar(36) | NOT NULL, FK `org_unit.id` ON DELETE RESTRICT, indexed |
| current_step_id | varchar(36) | NULL, FK `approval_chain_step.id` ON DELETE RESTRICT, indexed |
| status | varchar(20) | NOT NULL default `'pending'`, `CHECK status IN ('pending','approved','rejected','cancelled')`, indexed |
| started_by_user_id | varchar(36) | NOT NULL, FK `user.id`, indexed |
| (actor / soft-delete / timestamp columns as above) | | |

- `ix_approval_chain_instance_record` on `(org_id, module, module_record_id)`.
- `uq_approval_chain_instance_live`: UNIQUE `(definition_id, module,
  module_record_id)` WHERE `status = 'pending' AND deleted_at IS NULL` — one
  live chain per record per definition (the 409 on re-create).

**`approval_chain_requirement`** (`ApprovalChainRequirement`) — the FR-5
materialized snapshot. **This table is the frozen record; nothing writes to it
except materialization, a decision, and an explicit recalculation.**

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| org_id | varchar(36) | NOT NULL, indexed |
| instance_id | varchar(36) | NOT NULL, FK `approval_chain_instance.id` ON DELETE CASCADE, indexed |
| step_id | varchar(36) | NOT NULL, FK `approval_chain_step.id` ON DELETE RESTRICT, indexed |
| required_role_id | varchar(36) | NOT NULL, FK `role.id` ON DELETE RESTRICT, indexed |
| sequence_order | integer | NOT NULL |
| is_base_requirement | boolean | NOT NULL default false |
| triggered_by_rule_ids | JSON | NOT NULL default `[]` — **a list** (FR-3: every rule that fired, not one arbitrarily) |
| condition_explanations | JSON | NOT NULL default `[]` — `[{rule_id, field, operator, value, actual, text}]`, captured AT MATERIALIZATION TIME (FR-7 needs the actual value as it was then, so it is stored, never recomputed) |
| status | varchar(20) | NOT NULL default `'pending'`, `CHECK status IN ('pending','approved','rejected','cancelled')`, indexed |
| counts_toward_completion | boolean | NOT NULL default true |
| superseded_at | timestamptz | NULL — set when a recalculation drops a requirement that already carries a decision |
| acted_by_user_id | varchar(36) | NULL, FK `user.id` |
| acted_as_role_id | varchar(36) | NULL, FK `role.id` |
| delegated_from_user_id | varchar(36) | NULL, FK `user.id` |
| acted_at | timestamptz | NULL |
| comment | text | NULL |
| is_unfulfillable | boolean | NOT NULL default false |
| eligible_user_count | integer | NOT NULL default 0 |
| materialized_at | timestamptz | NOT NULL |
| (actor / soft-delete / timestamp columns as above) | | |

- `uq_approval_chain_requirement_role`: UNIQUE `(instance_id, step_id,
  required_role_id)` WHERE `deleted_at IS NULL AND superseded_at IS NULL` —
  **this index is FR-3/AC-5 in the DB**: two rules firing for the same role at
  the same step can only ever produce one live requirement.
- `ix_approval_chain_requirement_step` on `(instance_id, step_id, status)`.

**`approval_chain_history`** (`ApprovalChainHistory`) — **truly append-only
(FR-11/AC-10).**

Mixins: `TableNameMixin, IdMixin, OrgScopedMixin` **only**. No `TimestampMixin`
(its `updated_at` carries an `onupdate=utcnow` — a column whose whole purpose
is recording a mutation that must never happen), no `SoftDeleteMixin` (a
`deleted_at` is a deletion channel), no `ActorTrackedMixin` (its
`updated_by_user_id` is likewise an update channel). `acted_at` is the one
timestamp. This is a deliberate departure from the codebase's standard mixin
set and the only table in the feature that departs.

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK, default `new_uuid()` |
| org_id | varchar(36) | NOT NULL, indexed |
| instance_id | varchar(36) | NOT NULL, FK `approval_chain_instance.id` ON DELETE RESTRICT, indexed |
| step_id | varchar(36) | NULL, FK `approval_chain_step.id` ON DELETE RESTRICT |
| requirement_id | varchar(36) | NULL, FK `approval_chain_requirement.id` ON DELETE RESTRICT |
| action | varchar(40) | NOT NULL, `CHECK action IN ('instance_created','materialized','approved','rejected','recalculated','blocked_no_eligible_approver','instance_completed','instance_rejected')`, indexed |
| acted_by_user_id | varchar(36) | NULL, FK `user.id` |
| acted_as_role_id | varchar(36) | NULL, FK `role.id` |
| delegated_from_user_id | varchar(36) | NULL, FK `user.id` — **a second, distinct field from `acted_by_user_id`** (AC-19, feature 002 FR-21) |
| comments | text | NULL |
| before_json | JSON | NULL |
| after_json | JSON | NULL |
| acted_at | timestamptz | NOT NULL, indexed |

`ON DELETE RESTRICT` on all three FKs is intentional: a history row must be
able to outlive nothing — deleting an instance/step/requirement it references
is refused by the database. `approval_chain_instance` is soft-deleted only.

Append-only is enforced **twice**:

1. **ORM layer**, in `models.py`:
   ```python
   @event.listens_for(ApprovalChainHistory, "before_update", propagate=True)
   @event.listens_for(ApprovalChainHistory, "before_delete", propagate=True)
   def _reject_history_mutation(mapper, connection, target):
       raise RuntimeError("approval_chain_history is append-only: append a new entry instead")
   ```
2. **Database layer**, in the migration (Postgres only; wrapped in
   `if op.get_bind().dialect.name == "postgresql":`):
   ```sql
   CREATE FUNCTION approval_chain_history_append_only() RETURNS trigger AS $$
   BEGIN RAISE EXCEPTION 'approval_chain_history is append-only'; END;
   $$ LANGUAGE plpgsql;
   CREATE TRIGGER trg_approval_chain_history_append_only
     BEFORE UPDATE OR DELETE ON approval_chain_history
     FOR EACH ROW EXECUTE FUNCTION approval_chain_history_append_only();
   ```
   The trigger is what makes AC-10's "**any** code path, including an
   administrative one" literally true — a raw `UPDATE`/`DELETE` issued outside
   the ORM is refused too. The test suite runs on Postgres
   (`backend/tests/conftest.py` sets a Postgres `DATABASE_URL`), so this is
   directly testable.

**Migration**: revision ID `0044_approval_chains` (19 chars ≤ 32) on the current
single head **`0043_menu_screen_security`** (verified by walking every
`down_revision` in `backend/alembic/versions/`: 45 revisions, exactly one head,
`0043_menu_screen_security`). Steps, in order, inside one revision:

1. `create_table("approval_chain_definition", ...)` + CHECK + indexes.
2. `create_table("approval_chain_step", ...)` + CHECKs + the two partial uniques.
3. `create_table("approval_chain_step_rule", ...)` + the XOR CHECK + partial
   unique + lookup index. `condition_expression` is `postgresql.JSONB` with a
   `sa.JSON` variant fallback, matching how existing JSON columns in this
   codebase are declared.
4. `create_table("approval_chain_instance", ...)` + CHECKs + indexes + the live
   partial unique.
5. `create_table("approval_chain_requirement", ...)` + CHECK + the role partial
   unique + lookup index.
6. `create_table("approval_chain_history", ...)` + CHECK + indexes — with the
   reduced column set above (no `updated_at`, no `deleted_at`, no
   `updated_by_user_id`, no `legal_hold`).
7. The append-only function + trigger (Postgres only).
8. **FR-22 cutover seed**, in Python on `op.get_bind()` — dialect-agnostic raw
   SQL, no import of app code (migrations must not drift with the models):
   ```
   for each row in organization:
       approver_role_id = SELECT id FROM role
                           WHERE org_id = org.id AND name = 'approver'   -- else 'admin'
       for module in ('contract', 'intake_request'):
           INSERT approval_chain_definition(
               id=uuid4(), org_id=org.id,
               name='Default approval chain (' || module || ')',
               module=module, version=1, is_active=true,
               is_default_seeded=true, created_at=now, updated_at=now, legal_hold=false)
           INSERT approval_chain_step(
               id=uuid4(), org_id=org.id, definition_id=<above>,
               step_key='approval', name='Approval', sequence_order=1,
               step_type='approval', approval_mode='sequential', …)
           INSERT approval_chain_step_rule(
               id=uuid4(), org_id=org.id, step_id=<above>,
               is_base_requirement=true, condition_expression=NULL,
               required_role_id=approver_role_id, sequence_order=1, is_active=true, …)
   ```
   **This seed is what makes FR-22 safe on day one.** Without a definition the
   dispatch falls back to the legacy engine (never auto-approves, but also never
   satisfies FR-22); with a zero-step definition it would materialize zero
   required approvers, i.e. a silent auto-approval. One base requirement on the
   org's `approver` role (falling back to `admin`, which always exists) is the
   conservative floor: a new submission always requires exactly one named role's
   approval until an administrator configures something richer. Feature 002's
   migration backfilled every existing `user_role` grant at the org **root**
   unit, and a rerouted instance's `org_unit_id` defaults to that same root, so
   the seeded requirement resolves to a non-empty holder set on day one rather
   than materializing blocked.

**No `ApprovalRequest`/`ApprovalDecision` row is read, written, migrated or
backfilled** — FR-21. `ApprovalRoutingRule`/`ApprovalRoutingStep` rows are
likewise left exactly as they are and are **not** translated (decision and
rejected alternative in "Risks & decisions").

`downgrade()` (working, in reverse): drop the trigger + function (Postgres
only), then `drop_table` for `approval_chain_history`,
`approval_chain_requirement`, `approval_chain_instance`,
`approval_chain_step_rule`, `approval_chain_step`,
`approval_chain_definition` — each preceded by its index drops. Fully lossless
for pre-existing data: nothing outside these six tables is altered.

No pgvector involvement.

### Frontend TypeScript models (added to `src/lib/types.ts`)

```ts
// Mirrors the API contract exactly — copy verbatim, do not "improve".
export type ConditionOperator = "gt" | "lt" | "eq" | "in" | "contains";
export type ChainSubjectType = "contract" | "intake_request";
export type ChainInstanceStatus = "pending" | "approved" | "rejected" | "cancelled";
export type ChainRequirementStatus = "pending" | "approved" | "rejected" | "cancelled";
export type ChainApprovalMode = "sequential" | "parallel";
export type ChainStepType = "action" | "approval";
export type ChainHistoryAction =
  | "instance_created"
  | "materialized"
  | "approved"
  | "rejected"
  | "recalculated"
  | "blocked_no_eligible_approver"
  | "instance_completed"
  | "instance_rejected";

export interface ConditionExpression {
  field: string;
  operator: ConditionOperator;
  value: string | number | boolean | Array<string | number | boolean> | null;
}

export interface ConditionFieldDescriptor {
  name: string;
  type: "number" | "string" | "boolean";
  label: string;
}

export interface ConditionFieldCatalogResponse {
  module: ChainSubjectType;
  operators: ConditionOperator[];
  fields: ConditionFieldDescriptor[];
}

export interface ChainStepRuleResponse {
  id: ID;
  org_id: ID;
  step_id: ID;
  is_base_requirement: boolean;
  condition_expression: ConditionExpression | null;
  condition_text: string | null;
  required_role_id: ID;
  required_role_name: string;
  sequence_order: number;
  description: string | null;
  is_active: boolean;
  created_at: string;
  created_by_user_id: ID | null;
  updated_at: string;
  updated_by_user_id: ID | null;
}

export interface ChainStepResponse {
  id: ID;
  org_id: ID;
  definition_id: ID;
  step_key: string;
  name: string;
  sequence_order: number;
  step_type: ChainStepType;
  approval_mode: ChainApprovalMode;
  rules: ChainStepRuleResponse[];
  created_at: string;
  created_by_user_id: ID | null;
  updated_at: string;
  updated_by_user_id: ID | null;
}

export interface ChainDefinitionResponse {
  id: ID;
  org_id: ID;
  name: string;
  module: ChainSubjectType;
  version: number;
  is_active: boolean;
  is_default_seeded: boolean;
  steps: ChainStepResponse[];
  created_at: string;
  created_by_user_id: ID | null;
  updated_at: string;
  updated_by_user_id: ID | null;
}

export interface ChainInstanceSummary {
  id: ID;
  org_id: ID;
  definition_id: ID;
  definition_name: string;
  module: ChainSubjectType;
  module_record_id: ID;
  module_record_label: string;
  org_unit_id: ID;
  org_unit_name: string;
  current_step_id: ID | null;
  current_step_key: string | null;
  status: ChainInstanceStatus;
  is_blocked: boolean;
  pending_requirement_count: number;
  started_by_user_id: ID;
  created_at: string;
  updated_at: string;
}

export interface ChainConditionExplanation {
  rule_id: ID;
  field: string;
  operator: ConditionOperator;
  value: string | number | boolean | Array<string | number | boolean> | null;
  actual: string | number | boolean | null;
  text: string;
}

export interface ChainRequirementResponse {
  id: ID;
  instance_id: ID;
  step_id: ID;
  required_role_id: ID;
  required_role_name: string;
  sequence_order: number;
  is_base_requirement: boolean;
  triggered_by_rule_ids: ID[];
  condition_explanations: ChainConditionExplanation[];
  explanation: string | null;
  status: ChainRequirementStatus;
  counts_toward_completion: boolean;
  superseded_at: string | null;
  acted_by_user_id: ID | null;
  acted_by_label: string | null;
  acted_as_role_id: ID | null;
  delegated_from_user_id: ID | null;
  acted_at: string | null;
  comment: string | null;
  is_unfulfillable: boolean;
  eligible_user_count: number;
  can_decide: boolean;
  blocked_by_sequence: boolean;
  materialized_at: string;
}

export interface ChainInstanceStep {
  step_id: ID;
  step_key: string;
  name: string;
  sequence_order: number;
  approval_mode: ChainApprovalMode;
  is_current: boolean;
  is_complete: boolean;
  is_blocked: boolean;
  requirements: ChainRequirementResponse[];
}

export interface ChainBlockingEntry {
  requirement_id: ID;
  step_id: ID;
  step_key: string;
  required_role_id: ID;
  required_role_name: string;
  sequence_order: number;
  org_unit_id: ID;
  org_unit_name: string;
}

export interface ChainHistoryEntry {
  id: ID;
  instance_id: ID;
  step_id: ID | null;
  requirement_id: ID | null;
  action: ChainHistoryAction;
  acted_by_user_id: ID | null;
  acted_by_label: string | null;
  acted_as_role_id: ID | null;
  acted_as_role_name: string | null;
  delegated_from_user_id: ID | null;
  delegated_from_label: string | null;
  comments: string | null;
  before_json: unknown | null;
  after_json: unknown | null;
  acted_at: string;
}

export interface ChainInstanceDetailResponse {
  instance: ChainInstanceSummary;
  steps: ChainInstanceStep[];
  blocking: ChainBlockingEntry[];
  blocking_visible: boolean;
  can_recalculate: boolean;
  history: ChainHistoryEntry[];
}

export interface ChainBlockedResponse {
  instance_id: ID;
  blocking: ChainBlockingEntry[];
}

// EXISTING interface, extended additively for the intake inline ladder strip
// (FR-22's intake half). Every added field is optional, so legacy rungs — which
// omit them — still typecheck. `approval_request_id` is widened to `ID | null`
// because the existing "planned" preview path already returns null there;
// today's type is simply wrong about it and the components already guard for it.
export interface IntakeApprovalRung {
  approval_request_id: ID | null;   // was `ID` — corrected, not invented
  step_order: number;
  status: string;                   // pending|approved|rejected|cancelled|planned
  approver_label: string;
  due_at: string | null;
  mode?: "any" | "all";
  approvals?: number;
  needed?: number;
  requirement_id?: ID | null;       // NEW — set iff this rung is a chain requirement
  chain_instance_id?: ID | null;    // NEW — the pair approvalChainsApi.decide needs
  explanation?: string | null;      // NEW — FR-7 text, null for a base requirement
  blocked?: boolean;                // NEW — mirrors requirement.is_unfulfillable (FR-19)
}
```

### Endpoint client additions (`src/lib/endpoints.ts`)

One new `approvalChainsApi` block, built on the existing `apiFetch` + query-string
helpers, matching the `screenAccessApi` block added by feature 003.

- `approvalChainsApi.fields(module?: string) → Promise<ConditionFieldCatalogResponse>` — GET `/approval-chains/fields`
- `approvalChainsApi.definitions(params?: { module?: string; include_inactive?: boolean }) → Promise<ChainDefinitionResponse[]>` — GET `/approval-chains/definitions`
- `approvalChainsApi.createDefinition(payload: { name: string; module: string; version?: number; is_active?: boolean }) → Promise<ChainDefinitionResponse>` — POST `/approval-chains/definitions`
- `approvalChainsApi.updateDefinition(id: ID, payload: { name?: string; is_active?: boolean }) → Promise<ChainDefinitionResponse>` — PATCH `/approval-chains/definitions/{id}`
- `approvalChainsApi.deleteDefinition(id: ID) → Promise<void>` — DELETE `/approval-chains/definitions/{id}`
- `approvalChainsApi.createStep(definitionId: ID, payload: { step_key: string; name: string; sequence_order: number; step_type?: ChainStepType; approval_mode?: ChainApprovalMode }) → Promise<ChainStepResponse>` — POST `/approval-chains/definitions/{id}/steps`
- `approvalChainsApi.updateStep(stepId: ID, payload: { name?: string; sequence_order?: number; approval_mode?: ChainApprovalMode }) → Promise<ChainStepResponse>` — PATCH `/approval-chains/steps/{id}`
- `approvalChainsApi.deleteStep(stepId: ID) → Promise<void>` — DELETE `/approval-chains/steps/{id}`
- `approvalChainsApi.createRule(stepId: ID, payload: { is_base_requirement: boolean; condition_expression: ConditionExpression | null; required_role_id: ID; sequence_order?: number; description?: string | null; is_active?: boolean }) → Promise<ChainStepRuleResponse>` — POST `/approval-chains/steps/{id}/rules`
- `approvalChainsApi.updateRule(ruleId: ID, payload: Partial<{ condition_expression: ConditionExpression | null; required_role_id: ID; sequence_order: number; description: string | null; is_active: boolean }>) → Promise<ChainStepRuleResponse>` — PATCH `/approval-chains/rules/{id}`
- `approvalChainsApi.deleteRule(ruleId: ID) → Promise<void>` — DELETE `/approval-chains/rules/{id}`
- `approvalChainsApi.createInstance(payload: { definition_id: ID; module: string; module_record_id: ID; org_unit_id?: ID | null }) → Promise<ChainInstanceDetailResponse>` — POST `/approval-chains/instances`
- `approvalChainsApi.instances(params?: { module?: string; module_record_id?: ID; status?: ChainInstanceStatus; limit?: number; offset?: number }) → Promise<ChainInstanceSummary[]>` — GET `/approval-chains/instances`
- `approvalChainsApi.instance(id: ID) → Promise<ChainInstanceDetailResponse>` — GET `/approval-chains/instances/{id}`
- `approvalChainsApi.history(id: ID) → Promise<ChainHistoryEntry[]>` — GET `/approval-chains/instances/{id}/history`
- `approvalChainsApi.blocked(id: ID) → Promise<ChainBlockedResponse>` — GET `/approval-chains/instances/{id}/blocked`
- `approvalChainsApi.decide(instanceId: ID, requirementId: ID, payload: { decision: "approve" | "reject"; comment?: string | null }) → Promise<ChainInstanceDetailResponse>` — POST `/approval-chains/instances/{id}/requirements/{rid}/decision`
- `approvalChainsApi.recalculate(instanceId: ID, payload: { reason?: string | null }) → Promise<ChainInstanceDetailResponse>` — POST `/approval-chains/instances/{id}/recalculate`

## Component design

### Backend

- **Models**: `backend/app/approval_chains/models.py` — the six classes above
  plus the two append-only event listeners.
  `ApprovalChainStepRule.required_role = relationship("Role", lazy="joined")`
  and `ApprovalChainRequirement.required_role = relationship("Role",
  lazy="joined")` (role names are rendered on every read). Every FK is declared
  by **string**, so this module imports nothing from `app.auth`,
  `app.org_structure`, `app.contracts` or `app.approvals`.
  `backend/app/models.py` gains the six imports and the six `__all__` entries
  (names must actually exist — ruff F822).
- **Schemas**: `backend/app/approval_chains/schemas.py` — `ConditionExpressionIn`
  (`field: str`, `operator: Literal["gt","lt","eq","in","contains"]`,
  `value: float | int | str | bool | list[...] | None`, `model_config =
  ConfigDict(extra="forbid")` so an `and`/`or`/`code` key is a 422 at the edge),
  `ConditionFieldDescriptor`, `ConditionFieldCatalogResponse`,
  `ChainDefinitionCreate/Update/Response`, `ChainStepCreate/Update/Response`,
  `ChainStepRuleCreate/Update/Response`, `ChainInstanceCreate`,
  `ChainInstanceSummary`, `ChainConditionExplanation`,
  `ChainRequirementResponse`, `ChainInstanceStep`, `ChainBlockingEntry`,
  `ChainHistoryEntry`, `ChainInstanceDetailResponse`, `ChainBlockedResponse`,
  `ChainDecisionPayload` (`decision: Literal["approve","reject"]`),
  `ChainRecalculatePayload`.
- **Access helpers**: `backend/app/approval_chains/access.py` — every one
  filters on `actor.org_id` (AC-18): `get_definition_or_404`,
  `get_step_or_404`, `get_rule_or_404`, `get_instance_or_404`,
  `get_requirement_or_404`, `get_org_role_or_404`,
  `get_module_record_or_404(db, *, actor, module, record_id)` (for `contract`:
  org-scoped fetch **plus** `app.contracts.access.user_can_access_contract` —
  ethical walls keep overriding, unchanged), and
  `assert_instance_visible(db, *, actor, instance)` implementing the visibility
  rule from the permissions section. Org-unit lookups reuse the existing
  `app.org_structure.access.get_org_unit_or_404` / `get_org_root`.
- **Service**: `backend/app/approval_chains/service.py` — org scoping + audit on
  every mutating function:
  - `get_field_catalog(module)` — from `facts.MODULE_FACTS`; no DB, no audit.
  - `list_definitions` / `create_definition` / `update_definition` /
    `delete_definition` / `create_step` / `update_step` / `delete_step` /
    `create_rule` / `update_rule` / `delete_rule` — all org-filtered; `create_rule`
    and `update_rule` call `conditions.validate_expression(expr,
    allowed_fields=facts.allowed_fields(definition.module))` → 422 on anything
    malformed, and `access.get_org_role_or_404` on `required_role_id` → 404 for
    another org's role. Each writes its audit row per the table above.
    Deletes are soft deletes setting `deleted_at` **and** `deleted_by_user_id`.
    **Editing config never touches an existing instance's materialized
    requirements** (FR-8) — there is no code path from config to
    `approval_chain_requirement` other than `_materialize_step`.
  - `create_instance(db, *, actor, payload, subject=None, raise_on_existing=True)`
    — validates the definition is in the actor's org, active, non-deleted and
    has ≥1 step (422); validates the module record via
    `access.get_module_record_or_404`; resolves `org_unit_id` (given →
    `get_org_unit_or_404`; omitted → `org_structure.access.get_org_root(db,
    actor.org_id).id`); if a live instance already exists for `(definition,
    module, module_record_id)` → 409 from the HTTP path,
    **returned unchanged** when `raise_on_existing=False` (the dispatch path,
    which must stay idempotent like `submit_subject_for_approval`); sets
    `current_step_id` to the lowest `sequence_order` step; calls
    `_materialize_step`; calls `subject.on_submit(...)` (the caller's subject
    when the dispatch supplied one, else `subjects.resolve_subject(...)`);
    writes `approval_chain.instance_created` audit + `instance_created` and
    `materialized` history rows.
  - `_materialize_step(db, *, actor, instance, step) -> list[ApprovalChainRequirement]`
    — **the FR-5 evaluation, and the ONLY place conditions are ever evaluated**:
    1. `facts = facts.build_facts(db, module=instance.module,
       record_id=instance.module_record_id, org_id=instance.org_id)`.
    2. Load the step's active, non-deleted rules ordered by `sequence_order`,
       `created_at`.
    3. For each **base** rule → a requirement source for its `required_role_id`
       with `is_base_requirement=True`. For each **condition** rule → call
       `conditions.evaluate_condition(rule.condition_expression, facts,
       allowed_fields=...)`; keep it only when `result.satisfied` (malformed,
       unknown-field, type-mismatch and false all fall through identically —
       FR-4, and never raise, so the remaining rules still evaluate).
    4. **Group the surviving sources by `required_role_id`** → exactly one
       requirement row per role (FR-3/AC-5), `sequence_order` = the minimum
       `sequence_order` among its sources, `is_base_requirement` = True if any
       source is base, `triggered_by_rule_ids` = every non-base source's rule
       id, `condition_explanations` = one entry per fired condition source
       (each carrying `field`, `operator`, `value`, `actual`, and
       `conditions.render_explanation(...)`'s text — FR-7).
    5. For each requirement, `holders = org_access.users_holding_role(db,
       org_id=instance.org_id, role_id=..., org_unit_id=instance.org_unit_id)`;
       `eligible_user_count = len(holders)`; `is_unfulfillable = not holders`
       (FR-19). Every unfulfillable requirement additionally appends a
       `blocked_no_eligible_approver` history row.
    6. Persist with `materialized_at = utcnow()`. Called exactly twice in the
       codebase: from `create_instance` and from `_advance_step`. **Never from a
       read path** (FR-5's "not on every subsequent read or status check") —
       the requirement rows are read verbatim by every GET.
  - `record_decision(db, *, actor, instance_id, requirement_id, payload)` —
    org-scoped fetch with `with_for_update()` on the instance (matching the
    legacy engine's locking style); 409 if the instance is not `pending`; 409 if
    the requirement is already decided, cancelled or superseded (no
    double-decisioning); 422 if `decision == "reject"` without a comment;
    `subject = subjects.resolve_subject(...)` then `subject.guard_can_decide(db)`
    (the existing stage / request-closed guard, reused verbatim); on an
    **approve**, `authority.enforce_authority(db, user=actor,
    action="contract:approve", contract=subject, resource_type=
    "approval_chain_requirement", resource_id=requirement.id, …)` — parity with
    the legacy decide route, so the reroute does not silently drop the
    `AuthorityGrant` ceiling (never called on a reject, matching today);
    **eligibility re-checked FRESH**: `holders =
    org_access.users_holding_role(...)`, `holder = next((h for h in holders if
    h.user_id == actor.id), None)`, 403 `"You are not an eligible holder of the
    required role for this approval"` when None (FR-15, and the "lost
    eligibility before acting" edge case); **sequential gate** (FR-13): when
    `step.approval_mode == "sequential"`, 409 `"An earlier required approval on
    this step is still pending"` if any live, counting requirement on the same
    step with a lower `sequence_order` is still `pending`; on approve → set
    `status='approved'`, `acted_by_user_id=actor.id`,
    `acted_as_role_id=holder.role_id`,
    `delegated_from_user_id=holder.on_behalf_of_user_id`, `acted_at`; then if
    every live counting requirement on the step is approved → `_advance_step`;
    on reject → set the requirement `rejected`, every other live `pending`
    requirement on the instance `cancelled`, `instance.status='rejected'`,
    **and `subject.on_reject(..., comment=payload.comment)`** (contract → back
    to REVIEW; intake request → back to `open`)
    (FR-17/AC-14, without waiting for anyone else). Writes the
    `approval_chain.decided` audit row (with the delegation attribution
    metadata) **and** an `approved`/`rejected` history row carrying
    `acted_by_user_id` and `delegated_from_user_id` as two distinct fields
    (AC-19).
  - `_advance_step(db, *, actor, instance)` — next step by `sequence_order`;
    found → `current_step_id = next.id`, `_materialize_step(...)`, history
    `materialized`, audit `approval_chain.step_materialized`; none →
    `instance.status='approved'`, `current_step_id=None`,
    **`subject.on_complete(...)`** (contract → SIGNATURE, which is also what
    lets a contract workflow's waiting approval step resume unchanged; intake
    request → gate passed, or `finalize_approved` when no flow is running),
    history `instance_completed`, audit `approval_chain.completed`.
  - `recalculate(db, *, actor, instance_id, payload)` — **FR-9.** 409 if the
    instance is not `pending`. Snapshots `before` = the current live requirement
    list for `current_step_id`; re-runs the same grouping as
    `_materialize_step` against **current** facts; then reconciles:
    *added role* → insert a new pending requirement; *unchanged role* → refresh
    `triggered_by_rule_ids`, `condition_explanations`, `sequence_order`,
    `eligible_user_count`, `is_unfulfillable` (a decided requirement keeps its
    decision); *removed role* with no decision → soft-delete
    (`deleted_at`/`deleted_by_user_id`); *removed role* **with** a decision →
    leave `status` and the decision intact, set `superseded_at = utcnow()` and
    `counts_toward_completion = False` (the decision stays visible in history
    and on the row but no longer gates completion — the spec's exact edge-case
    wording). Writes ONE `recalculated` history row with the full before/after
    lists (FR-10) and the `approval_chain.recalculated` audit row. After
    reconciliation, re-checks completion (a recalculation that removes the last
    outstanding requirement completes the step).
  - `list_instances` / `get_instance_detail` / `get_history` /
    `get_blocked` — read-only, org-filtered, `assert_instance_visible` on every
    single-instance read; `blocking` populated only when the caller holds
    `approval_chain:manage`, else `[]` with `blocking_visible=false` (FR-20);
    `can_decide` / `blocked_by_sequence` computed per requirement for the
    calling user. **No evaluation and no writes on any of these paths** (FR-5,
    FR-8, AC-7, AC-9).
- **Dispatch / subjects**: `backend/app/approval_chains/dispatch.py` and
  `subjects.py` (signatures and the two edited call sites frozen under "The
  FR-22 interception point"). `dispatch.py` is the only module
  `app/approvals/service.py` and `app/workflows/service.py` import from this
  domain, and both import it **lazily inside the function**, so the dependency
  graph stays acyclic at module load in both directions.
- **Router**: `backend/app/approval_chains/routes.py` — one thin `APIRouter`
  (prefix `/approval-chains`, tag `approval-chains`), no business logic,
  permissions exactly per the contract table. Registered in
  `backend/app/main.py` immediately after the menu-security routers.
  `require_screen_level` is **not** added to these routes: feature 003's FR-17
  tranche 1 is contracts/matters/trademarks/notices/intake, and `/approvals` is
  in its explicitly deferred retrofit. Adding it here would silently extend
  that tranche.
- **Celery**: **none, deliberately.** FR-9/AC-9 forbid any scheduled job,
  data-change hook or system-initiated re-evaluation. `app/jobs/tasks.py` is not
  in any agent's path mapping for this feature, and the absence of a task is
  itself the evidence for AC-9.
- **Settings**: none. No external service, no `MOCK_<NAME>` flag, no
  `validate_runtime_settings()` entry, so
  `tests/test_phase10_security_hardening.py::test_runtime_settings_accepts_a_correctly_locked_down_production_config`
  needs no change. That file is in backend-dev's paths only in case one of its
  *permission-catalog* assertions enumerates `ALL_PERMISSIONS`.

### Frontend (Next.js page/component tree)

The existing `src/app/(app)/approvals/page.tsx` is a `"use client"` page with a
scoped-CSS tab bar (`Requests` | `Approval routing` | `Approver groups` |
`Intake routing`). This feature adds **two tab entries and two renders** to that
`tabs` array and switch — nothing else in the file changes. The legacy
`RequestsTab` / `RulesTab` / `GroupsTab` are untouched, which is what keeps the
two engines visibly separate in the UI as well as in the schema.

- `src/app/(app)/approvals/page.tsx` (edit) — adds
  `{ id: "chains", label: "Chains" }` (rendered for any user; the API filters
  what they may see) and
  `{ id: "conditions", label: "Condition rules" }` (rendered only when
  `can(user, "approval_chain:manage")`, matching how `isAdmin` already gates
  the intake-routing tab). One further change inside `RequestsTab`'s submit
  handler: because a rerouted submission now returns an empty `ApprovalRequest`
  list (FR-22), the success toast reads "Approval chain started — see the
  Chains tab" and the handler invalidates `["approval-chain-instances"]` as
  well as `["approvals"]`. No other logic in the tab changes.
- `src/app/(app)/approvals/_rules-builder.tsx` (edit) — a single `warning`
  `MessageBar` at the top of the rule list: "Routing rules no longer determine
  approvers for new submissions. New contract and intake-request approvals use
  **Condition rules**. These rules remain visible for reference and still
  govern approvals that were already in flight." This is the honest, visible
  half of the routing-rule disposition decision below. **No logic change** —
  the builder keeps working, since routing rules still serve in-flight legacy
  chains and the legacy fallback path.
- `_chains-tab.tsx` (new) — react-query `["approval-chain-instances", filters]`
  → `approvalChainsApi.instances()`. A `Table` of record label / definition /
  current step / status `Badge` / blocked `Badge` / updated-at, with `Select`
  filters for status and module. Selecting a row opens `_chain-detail.tsx`.
  `CenterSpinner` → `ErrorState` → `EmptyState` for the three non-data states.
- `_chain-detail.tsx` (new) — the FR-5/FR-6/FR-7/FR-12/FR-13/FR-20 surface.
  react-query `["approval-chain-instance", id]` →
  `approvalChainsApi.instance(id)`. Renders, per step:
  - a step header `Card` with the step name and a `Badge` reading
    **Sequential** or **Parallel** (`approval_mode`) — AC-13 is visible at a
    glance;
  - one row per requirement showing `sequence_order`, the required role, a
    status `Badge`, and — for a condition-triggered requirement — the
    `explanation` string rendered verbatim from the server plus a
    `Base requirement` / `Condition-triggered` `Badge` pair (FR-6's distinction,
    AC-6's text);
  - an `Approve` / `Reject` `Button` pair shown only when
    `requirement.can_decide`; disabled with the tooltip "An earlier approver on
    this step has not acted yet" when `blocked_by_sequence` (the server still
    409s — FR-13's boundary is the API);
  - `Reject` opens a `Modal` requiring a comment;
  - a `danger` `MessageBar` when `instance.is_blocked`, listing
    `blocking[].required_role_name` when `blocking_visible`, and otherwise
    reading "This step is blocked: a required role has no eligible approver.
    Ask an administrator." (FR-19 visible to everyone, FR-20's specifics only
    to managers);
  - a `Recalculate required approvers` `Button` shown when
    `can_recalculate`, behind `useConfirm` + a reason `Input`, calling
    `approvalChainsApi.recalculate` (FR-9);
  - a history `Table` (newest first) rendering `action`, actor, **and the
    delegator as its own column** when `delegated_from_label` is set (AC-19),
    with recalculation rows visually distinguished by a `Badge` (AC-8's
    "visibly different in kind").
- `_condition-rules-tab.tsx` (new) — the FR-1 admin surface. A **subject-type
  `Tabs`/`Select` (`Contracts` | `Intake requests`)** picks the `module`, since
  each subject type has its own active definition and its own fact surface
  (FR-22). A `MessageBar` appears when the selected definition has
  `is_default_seeded: true`: "This is the default chain created at cutover — it
  requires one approver from the *approver* role. Review it against the routing
  rules you had configured." Then: definition
  `Select` → step list → per-step rule `Table` (base vs conditional `Badge`,
  `condition_text`, required role, sequence order, active) with add/edit/delete
  `Modal`s. The rule editor's **field `Select` is populated from
  `approvalChainsApi.fields(module)`** (the selected subject type's fact set,
  so an intake rule can only pick intake facts) and its **operator `Select`
  from the same response's `operators`** — so the UI can only ever compose one
  of the five whitelisted comparisons over a whitelisted field, and the
  server's `validate_expression` is the boundary. A `MessageBar` on the step editor
  states: "A compound rule ('value over 1M AND jurisdiction is EU') is two
  separate rules requiring the same role" (FR-3's guidance where an admin needs
  it).
- `src/lib/approval-chains.ts` (new) — pure, React-free helpers, unit-tested:
  `OPERATOR_LABELS: Record<ConditionOperator, string>`,
  `formatConditionText(expr: ConditionExpression): string`,
  `requirementTone(r: ChainRequirementResponse): "green"|"amber"|"red"|"neutral"`,
  `stepProgress(step: ChainInstanceStep): { decided: number; total: number }`
  (counts only `counts_toward_completion && !superseded_at` requirements),
  `nextActionableSequence(step): number | null`.
- **New shared primitives in `src/components/ui.tsx`: none.** Every primitive
  used above — `Badge, Button, Card, CardBody, CardHeader, CardTitle,
  CenterSpinner, EmptyState, ErrorState, Field, Input, MessageBar, Modal,
  PageHeader, Select, Table, TD, TH, THead, TR, Textarea, useConfirm` — is
  already exported from `frontend/src/components/ui.tsx` (verified against the
  file's export list). Icons from `lucide-react`; toasts from
  `@/components/toast`.
- **Demo-mode behavior**: this repo has **no `src/lib/demo.ts`** (verified:
  `frontend/src/lib/` holds api, approval-chains *(new)*, auth, client-logger,
  contract-blocks, endpoints, intake, layout, org-tree, query, screen-access,
  types, utils). There is no demo mode to keep functional; both new tabs
  degrade to `CenterSpinner` → `ErrorState` when the API is unreachable, like
  every other surface.

## Test strategy

- **Backend pytest** (Postgres, per `backend/tests/conftest.py`; the suite runs
  against an `alembic upgrade head` database, so the append-only trigger is
  live in tests):
  - `test_condition_evaluator.py` — pure-function tests, no DB. The AC-3/AC-4
    adversarial battery, parameterized over at least: `{"and": [...]}`,
    `{"or": [...]}`, `{"not": {...}}`, `{"field":"x","operator":"exec","value":1}`,
    `{"field":"x","operator":"__import__","value":"os"}`, an operator of
    `">"`/`"=="` (symbol instead of token), a missing `field`/`operator`/`value`
    key, an extra key, `field` as a dict/list/int/None, `value` as a nested
    dict, a 10 MB string, a list of 10 000 items, `1e400`/`NaN`/`Infinity`,
    `{"field":"contract_value","operator":"gt","value":"not-a-number"}`, a
    field not in the whitelist, a field whose fact is `None`, and `expression`
    itself being `None`/a list/a string/an int. Every case asserts
    `satisfied is False`, no exception escapes, and (where applicable)
    `malformed is True`. Plus happy paths for all five operators, the FR-3
    "both fired" case, and `render_explanation` producing exactly
    `contract_value (1,200,000) > 1,000,000` (AC-6).
  - `test_org_access_role_holders.py` — fixture builds Global → Region A →
    Entity 1 (reusing feature 002's org-unit fixture shape). Cases: a holder
    granted at the exact unit is returned; a holder granted at a **parent**
    unit with `allows_hierarchy_rollup=True` is returned (**AC-15**); the same
    holder with the flag `False` is not; an expired `valid_to` holder is not; a
    revoked (`deleted_at`) holder is not; a holder in another org is never
    returned (AC-18); a delegate of a native holder is returned with
    `via_delegation_id` / `on_behalf_of_user_id` set, and a delegate of a
    delegate is not (no chains); zero holders → `[]`, not an error. Plus a
    regression assertion that `active_grants_for_user` and `resolve_access`
    behave identically before and after the `_grant_validity_clause`
    extraction (the existing `test_org_access_resolver.py` is the wider gate
    and must still pass untouched).
  - `test_approval_chain_config_api.py` — definition/step/rule CRUD happy
    paths; a base rule with a condition → 422; a condition rule without one →
    422; a malformed/unknown-operator condition → 422 at create AND at patch;
    a role from another org → 404; a duplicate base requirement for the same
    role on a step → 409; two condition rules for the same role on a step →
    201 (explicitly allowed, FR-3); 403 without `approval_chain:manage`;
    the `approval_chain.rule_updated` audit row carrying the prior and new
    condition, role, sequence position and base flag.
  - `test_approval_chain_materialization.py` — **AC-1** (base + condition both
    materialized at `contract_value = 1,200,000`), **AC-2** (only the base at
    500,000), **AC-3** (a malformed rule stored directly in the DB is skipped
    while its well-formed sibling on the same step still fires), **AC-5**
    (two rules, one role → exactly one requirement whose
    `condition_explanations` has two entries and whose `explanation` names
    both), **AC-6** (the requirement's `explanation` string and its
    `triggered_by_rule_ids` link back to the rule), **AC-16** (a role with zero
    holders → `is_unfulfillable=true`, the step does not complete when the other
    requirement is approved, and a `blocked_no_eligible_approver` history row
    exists), a step with zero condition rules materializing just the base
    requirements, and **AC-18** (an Org-A user creating an instance on an Org-B
    contract → 404; reading an Org-B instance → 404).
  - `test_approval_chain_decisions.py` — **AC-11** (sequential step: the
    sequence-2 approver gets 409 while sequence 1 is pending), **AC-12**
    (parallel step: either order accepted), **AC-13** (one definition, step A
    sequential + step B parallel, asserted in one test), **AC-14** (one reject
    → step and instance rejected immediately, the other requirement
    `cancelled`), **AC-19** (a delegate's decision produces a history row with
    `acted_by_user_id` and `delegated_from_user_id` as two distinct non-equal
    fields), a non-holder's decision → 403, a holder whose grant was revoked
    between materialization and acting → 403, a second decision on the same
    requirement → 409, a reject without a comment → 422, 403 without
    `approval_chain:decide`, **AC-17** (a `approval_chain:manage` holder sees
    `blocking` populated on GET; a plain `approval_chain:read` holder sees
    `blocking: []` and `blocking_visible: false` while still seeing
    `is_blocked: true`).
  - `test_approval_chain_recalculate.py` — **AC-7** (materialize at 1,200,000,
    mutate the contract to 500,000 directly in the DB, GET the instance twice,
    assert the Finance requirement is still present and unchanged), **AC-8**
    (invoke recalculate → the Finance requirement is gone from the live list and
    a single `recalculated` history row holds the before/after lists, the actor
    and the timestamp, with `action != "approved"`), **AC-9** (after the same
    data change, with no recalculate call: GET the instance repeatedly, call the
    list endpoint, and assert `materialized_at` and the requirement set are
    byte-identical — plus a source-level assertion that
    `_materialize_step` has exactly two call sites, both mutating), recalculate
    on an untouched instance behaving like materialization, recalculate removing
    a requirement that already carries a decision leaving the decision intact
    with `counts_toward_completion=false` and `superseded_at` set, recalculate
    adding a requirement blocking completion, and 403 for a user holding
    `approval_chain:decide` but not `approval_chain:recalculate`.
  - `test_approval_chain_history_append_only.py` — **AC-10**, three ways: an
    ORM `row.comments = "x"; db.flush()` raises; an ORM `db.delete(row)` raises;
    a raw `db.execute(text("UPDATE approval_chain_history SET comments='x'"))`
    and a raw `DELETE` both raise `ProgrammingError`/`InternalError` from the
    Postgres trigger. Plus: appending a correcting entry succeeds.
  - `test_approval_chain_reroute.py` — **the FR-22 / AC-21 / AC-22 / AC-20 core.**
    - **AC-21**: with an active `contract` definition (a base `contract_reviewer`
      requirement plus a `contract_value > 1,000,000 → finance_approver`
      condition rule), `POST /api/v1/approvals/requests` for a 1.2M contract
      returns `[]`, creates **zero** `ApprovalRequest` rows for that contract,
      creates exactly one `approval_chain_instance` whose step-1 requirements
      are the reviewer (base) and the finance approver (with
      `triggered_by_rule_ids` and the FR-7 explanation), and moves the contract
      to the APPROVAL stage (`on_submit` fired).
    - **AC-22**: the same assertions for an intake request submitted through
      `app.intake.service`'s submit-for-approval path with an active
      `intake_request` definition and a `request_value > 50,000 → finance_approver`
      rule — proving the reroute is not contracts-only.
    - **AC-20 / FR-21, three ways**: (a) a contract with a legacy chain already
      `PENDING` (created before any definition existed) is re-submitted → the
      existing legacy rows are returned unchanged and **no**
      `approval_chain_instance` is created (the dispatch sits after the
      idempotency query); (b) that legacy chain is decisioned to completion
      through `POST /approvals/requests/{id}/decision` and behaves exactly as
      today; (c) with **no** active definition for the org, a new submission
      still produces legacy `ApprovalRequest` rows and writes one
      `approval_chain.reroute_skipped` audit row.
    - **Lifecycle parity**: a rerouted contract chain rejected → contract back
      in REVIEW; fully approved → contract in SIGNATURE. A rerouted intake
      chain rejected → request `open`; fully approved with no workflow running →
      request `approved`/`complete`.
    - **Authority parity**: with an `AuthorityGrant` policy for
      `contract:approve` capped below the contract's value, an eligible role
      holder's approve on a rerouted chain gets 403 from `enforce_authority`
      (and a reject is not gated).
    - **Workflow parity (the M2 guard)**: a no-contract intake workflow whose
      `approval` step runs with an active definition must set the step to
      `waiting_job` with `chain_instance_id` and **must not** advance; the
      request must NOT reach `approved` until the chain completes; and once the
      chain is approved, `refresh_run` advances the flow. A companion case
      asserts the pre-existing "no rungs at all" path still advances.
    - **Intake strip parity (edit 3)**: after a rerouted intake submission the
      submit response's `chain` array is **non-empty** and each entry carries
      `requirement_id`, `chain_instance_id`, a role `approver_label`, a
      `status`, `approval_request_id: null`, and an `explanation` on the
      condition-triggered rung; `GET /intake/requests/{id}/chain` returns the
      same rungs on a subsequent read (the branch that actually matters for the
      strip); a request with a **pre-cutover legacy** chain still returns the
      legacy rungs unchanged through `_serialize_chain`; an **unsubmitted**
      request with an active definition returns the definition's base
      requirements with `status: "planned"` **and no condition evaluation
      occurs** (asserted by mutating the request's value between two reads and
      seeing the planned list unchanged — FR-5 on a read path); an unsubmitted
      request in an org with no definition still returns the legacy
      `plan_chain` preview.
    - Fast-lane parity: an NDA that qualifies for the fast lane still skips
      approval entirely and creates no chain (the dispatch sits after the
      fast-lane branch).
  - **Regression gate (FR-21, FR-22)**: the entire existing approvals, intake
    and workflow suites must pass with **no test edits**, except where a test
    asserts the *legacy* chain shape for a NEW submission — those tests, if any,
    are evidence of the intended FR-22 behavior change and must be updated
    deliberately, with the change called out in the task's status detail rather
    than quietly adjusted. `git diff --stat` must show **`service.py` only**
    within each of `backend/app/approvals/`, `backend/app/workflows/` and
    `backend/app/intake/` — three files, ~50 lines, every other file in those
    three domains untouched. That bounded diff — rather than an empty one — is
    the AC-20 inspection artifact. The existing intake suite
    (`backend/tests/test_flow*`, `test_*intake*`) is the regression gate for
    edit 3.
- **Frontend vitest**: `src/lib/approval-chains.test.ts` —
  `formatConditionText` renders each of the five operators (including `in` over
  a list and `contains`); `stepProgress` excludes superseded and
  non-counting requirements; `requirementTone` maps unfulfillable → red,
  approved → green, pending → amber; `nextActionableSequence` returns the lowest
  pending sequence on a sequential step and `null` on a parallel one.
- **Acceptance verification** (`/verify`): AC-1 … AC-19, AC-21 and AC-22 are
  each evidenced by a named pytest above. **AC-20 is evidenced by
  `test_approval_chain_reroute.py`'s three in-flight cases PLUS a documented
  source inspection** recorded in `verification.md`: `git diff --stat` showing
  `backend/app/approvals/service.py` and `backend/app/workflows/service.py` as
  the only two touched existing-domain files, with the dispatch block quoted in
  full and its placement (after the legacy idempotency query) called out — the
  "an in-flight chain can never be rerouted" half is a property of where those
  lines sit, which a pytest demonstrates but an inspection proves. A
  `docker compose up -d` walkthrough completes the evidence: submit a contract
  before seeding a definition, confirm a legacy chain; seed/activate a
  definition, submit a second contract, confirm a materialized chain; decision
  the first one to completion under the legacy engine untouched.

## Risks & decisions

- **CENTRAL DECISION 1 — a new schema in a new domain
  (`backend/app/approval_chains/`), NOT an extension of
  `backend/app/approvals/`'s tables.** (FR-22's reroute is orthogonal to this:
  it changes *which engine new submissions use*, not *whose tables the new
  engine writes to* — and every reason below is about the tables.) Four reasons,
  in order of weight:
  1. **FR-21 becomes structural instead of conditional.** Extending the
     existing schema means every legacy code path — `plan_chain`,
     `submit_subject_for_approval`, `_apply_decision`, `_quorum_needed`,
     `reassign_rung`, `redeem_token_decision`, `approval_chain` (the route),
     `app/workflows/service.py`'s `approval` step, and
     `app/intake/approval_bridge.py` — grows an "is this row condition-driven
     or legacy?" branch. Nine branch points, each of which, if wrong, silently
     applies the new rules to an in-flight legacy request, which is precisely
     what FR-21 and AC-20 forbid. A separate table makes it impossible: no
     legacy row can reach this code because no legacy row is of this type.
  2. **FR-11's append-only history is unattainable by extension.**
     `ApprovalDecision` carries `TimestampMixin` (an `updated_at` with
     `onupdate=utcnow`) and is written and read by the legacy engine. Adding a
     `BEFORE UPDATE OR DELETE` trigger to it would break the legacy engine;
     adding a *new* append-only table alongside it while `ApprovalDecision`
     remains the real decision record would give the same instance two
     histories with different mutability rules. The new
     `approval_chain_history` table has no `updated_at`, no `deleted_at`, no
     `updated_by_user_id`, and both an ORM guard and a DB trigger.
  3. **The shape genuinely differs.** `ApprovalRequest` is one row per *rung*
     targeting a group / a named user / a role **string**, with a
     `step_order` and an any/all `mode`. This feature needs one row per
     *required role* (`role_id` FK, not a string), a `sequence_order` within a
     step that is independent of the step's own order, a per-step
     sequential/parallel flag (FR-12 — `mode` is per-rung and means quorum, not
     ordering), `is_base_requirement`, a **list** of `triggered_by_rule_ids`, a
     snapshotted `condition_explanations` blob, `is_unfulfillable`,
     `eligible_user_count`, `counts_toward_completion` and `superseded_at`.
     That is nine-plus new nullable columns bolted onto a table with five live
     readers — a shape the legacy engine would have to ignore and the new
     engine would have to police at runtime.
  4. **`ApprovalToken`'s email-link flow stays cleanly out of scope** (spec
     "Out of scope"). It hangs off `ApprovalRequest`; if condition-driven
     chains were `ApprovalRequest` rows, every token-issuing path
     (`_activate_step`) would need a suppression branch, and a bug there is an
     unauthenticated decision on a chain this feature never meant to expose
     externally.
  *Rejected*: extending `app/approvals/` — the cheapest-looking option and the
  one with the most ways to silently violate FR-21, FR-11 and the
  ApprovalToken exclusion. *Also rejected*: a hybrid that stores condition
  rules on `ApprovalRoutingStep` while materializing into new tables — it
  splits one feature's configuration across two engines' schemas, so a routing
  rule's step would carry config that only one of its two consumers honors.
- **Cost of the decision, stated plainly:** two approval mechanisms coexist in
  the schema, but **only one of them routes new work**. After cutover the legacy
  engine serves exactly three things: approvals already in flight (FR-21), the
  `ApprovalToken` email-link path those in-flight chains use (spec "Out of
  scope"), and the fail-safe fallback when an org has no active chain definition
  (audited as `approval_chain.reroute_skipped`). Everything new goes through the
  condition-driven engine (FR-22). Retiring the legacy tables once no in-flight
  chain remains is a deliberate follow-on decision, out of scope here.

- **THE FR-22 INTERCEPTION POINT — inside the shared
  `submit_subject_for_approval`, not in its two callers.** Reading the actual
  function confirmed three things that make this the right seam: both subject
  types already funnel through it (so ONE edit satisfies AC-21 and AC-22, and
  the third caller — `app/workflows/service.py`'s `approval` step, which calls
  `submit_request_for_approval` — is covered for free); it **already has a
  "returns `[]`, creates no `ApprovalRequest`" path** (the NDA fast lane), so no
  caller needs a type change; and its first two operations are `subject.precheck`
  and the live-legacy-chain idempotency query, so inserting the dispatch *after*
  them makes FR-21 a property of statement order rather than of a status check I
  would otherwise have to write and get right. The edit is ~11 lines and one
  lazy import; all the logic lives in `approval_chains/dispatch.py`.
  *Rejected*: **editing the two callers** (`submit_contract_for_approval` and
  `submit_request_for_approval`) — it also covers both subject types, but it
  duplicates the dispatch in two domains where the two copies can drift, and it
  leaves the shared function still able to build a legacy chain for any future
  caller, so FR-22 would hold by convention instead of by construction.
  *Rejected*: **intercepting at the HTTP route** `POST /approvals/requests` — it
  misses intake entirely (intake submits through the service, never that route)
  and misses every workflow-driven submission, failing AC-22 outright.
  *Rejected*: **a feature flag / settings toggle** for the reroute — FR-22 is
  unconditional for new submissions; a flag would make the cutover boundary a
  runtime variable and would need a `validate_runtime_settings()` entry for a
  behavior the spec states as a requirement.

- **`app/workflows/service.py` must also be edited — this is a correctness
  requirement, not a nicety.** Its no-contract `approval` branch currently reads
  `if not reqs: sr.note = "No approval rungs required — skipped."; return
  "advance"`, and advancing there lets `_finalize_intake_if_approved` mark the
  request **approved with zero sign-off** (the exact failure its own M2 comment
  documents). After the reroute `reqs` is always `[]` for intake, so without
  this edit every workflow-driven intake approval silently auto-approves — a
  security defect, and a direct violation of FR-16. The fix consults
  `dispatch.live_instance_for(...)` on the creation side and resumes off
  `chain_instance_id` in `refresh_run`, mirroring the existing `approval_ids`
  branch (including its `db.commit()` on rejection, the M4 fix). The **contract**
  branch of the same step type needs no edit: it sets `waiting_job` + `"wait"`
  unconditionally and resumes off the contract's lifecycle stage, which the new
  engine still drives via `subject.on_complete()`. *Rejected*: leaving
  `workflows/service.py` alone and having the dispatch return fabricated
  `ApprovalRequest` rows so `if not reqs` stays false — that writes legacy rows
  for condition-driven work, which is exactly what FR-22 and the schema
  separation forbid.

- **Lifecycle and authority parity is achieved by CALLING the existing code,
  not reimplementing it.** `subjects.py` rebuilds the existing `ContractSubject`
  / `IntakeApprovalSubject` and the chain engine calls their `on_submit`,
  `guard_can_decide`, `on_reject`, `on_complete` hooks at the same four moments
  the legacy ladder calls them; `record_decision` calls the existing
  `authority.enforce_authority` on approve exactly as the legacy decide route
  does. Without these the reroute would look correct and silently drop the
  contract stage machine, the intake terminal transition, and the
  `AuthorityGrant` ceiling. *Rejected*: duplicating the transitions inside
  `approval_chains/service.py` (two copies of the contract stage machine is how
  they drift).

- **ROUTING-RULE DISPOSITION: `ApprovalRoutingRule`/`ApprovalRoutingStep` are
  left in place, become inert for new submissions, and are NOT auto-translated
  into chain definitions. The migration instead seeds a conservative default
  definition per org per subject type, and the routing-rules UI says plainly
  that it no longer applies.** Automatic translation was seriously considered —
  the legacy `criteria` matcher (`min_value`/`max_value`/`contract_type`/
  `risk_band`/exact-match) maps almost one-to-one onto this feature's
  `gt`/`lt`/`eq`/`in` operators — and **rejected on two unfixable semantic
  mismatches**:
  1. **Approver targets don't translate.** A routing step targets an
     `approver_group_id`, an `approver_user_id`, or an `approver_role` *string*.
     This feature's requirements are `role_id` FKs to `role` (FR-18's
     org-unit-scoped role resolution has no notion of an approver group or a
     named individual). The dominant configured case — a group such as
     "Finance" — has no faithful `role_id`, and inventing one per group would
     silently create RBAC roles nobody granted.
  2. **Conjunction becomes disjunction.** A legacy rule's multi-key `criteria`
     is an AND across its keys. Under FR-3, several rules requiring the same
     role are additive — any one firing adds the role, i.e. an OR. So a
     two-key routing rule cannot be expressed as two condition rules without
     inverting its meaning and widening who must approve.
  A translation that is wrong in the dominant case is worse than none: it
  produces an authoritative-looking approver list nobody authored. The chosen
  alternative is loud rather than silent — a seeded one-step default that can
  never auto-approve (always exactly one required role), `is_default_seeded`
  surfaced in the API and flagged in the Condition-rules tab, and a `warning`
  `MessageBar` on the routing-rules tab. *Also rejected*: (a) leaving orgs with
  **no** definition and letting every submission fall back to legacy — FR-22
  would then be satisfied by nobody; (b) deleting or deactivating the routing
  rules at cutover — they still legitimately serve in-flight chains and the
  fallback path, and destroying configuration an admin authored is not this
  feature's call.

- **The subject-type discriminator is the existing `module` column, and at most
  one definition per (org, subject type) may be active.** `module` was already
  on `approval_chain_definition` / `approval_chain_instance`; FR-22 simply
  widens its CHECK to `('contract','intake_request')`. It doubles as the fact-map
  key, so `validate_expression` rejects an intake fact on a contract definition
  and vice versa at config time (422) — a definition can never mix the two fact
  surfaces, which is what makes a per-subject-type discriminator necessary at
  all. The partial unique `(org_id, module) WHERE is_active AND deleted_at IS
  NULL` makes `dispatch.active_definition_for` deterministic.
  *Rejected*: **a separate `subject_type` column alongside `module`** (two
  columns that must always agree); *rejected*: **one definition spanning both
  subject types** (its rules would reference facts that exist for only one of
  them, so every rule on the wrong subject would fail closed — a silently
  half-working configuration); *rejected*: **several active definitions selected
  by criteria at submit time** — that is the legacy criteria matcher rebuilt
  inside the new engine, scope the spec does not ask for, and it would put a
  second, unconditioned matching language next to the one FR-2 carefully
  restricts.
- **The ER diagram's `workflow_*` names are not used.**
  `backend/app/workflows/` is an existing, unrelated automation-flow engine
  whose `approval` step already delegates to `app.approvals` (verified in
  `workflows/service.py`). Two meanings of "workflow" in one schema is a
  permanent source of wrong-file edits. Every table is `approval_chain_*` and
  the domain is `approval_chains`. *Rejected*: the literal ER names; also
  rejected: putting these tables inside `app/workflows/` (a different concept
  with a different permission family).
- **Six tables, not seven: `workflow_step_roles` and
  `workflow_step_approval_rules` are merged into `approval_chain_step_rule`.**
  The ER diagram's own `is_base_requirement` flag already distinguishes the two
  cases; a separate base-requirement table would duplicate `required_role_id`,
  `sequence_order` and the whole audit column set, and would make FR-3's
  "group by role, base and conditional together" a two-table union in the
  hottest code path. A `CHECK` makes the base/condition XOR unrepresentable in
  the wrong combination. *Rejected*: two tables (mirrors the diagram more
  literally; costs a union and a duplicated audit surface for no behavioral
  gain).
- **`org_unit_scope_strategy` from the ER diagram is dropped.** FR-19 defines
  eligibility at "the chain instance's org-unit scope" — one scope, not a
  per-role strategy. Adding a configurable strategy would invent a second
  scoping axis the spec never asks for and would multiply the eligibility test
  matrix. The instance's `org_unit_id` is the scope for every requirement.
- **The evaluator lives in `app/approval_chains/conditions.py`, a dedicated
  module inside the domain — NOT in `app/core/`.** Feature 002 put
  `org_access.py` in `app/core/` because `app/core/deps.py` itself calls it;
  feature 003 put `screen_access.py` there for the same reason. Neither reason
  applies here: nothing in `app/core/` evaluates conditions, and no other
  domain does either, so hosting it in core would make core carry feature logic
  with no core consumer — the inverse of both precedents. It is nonetheless a
  **separate file from `service.py`** (not a private helper) because it is the
  feature's security boundary: a pure, DB-free function is what AC-4's
  adversarial battery can hammer with no fixtures, and a reviewer can audit the
  whole "can this execute anything?" question by reading one short file.
  *Rejected*: `app/core/condition_eval.py` (core dependency inversion);
  burying it in `service.py` (untestable without a DB, unauditable as a unit).
- **`users_holding_role` is ADDED TO `app/core/org_access.py` rather than put
  in a sibling module — a deliberate, flagged departure from feature 003's
  precedent.** spec.md's "Out of scope" reads: *"Any change to feature 002's
  org-unit hierarchy, grant, or delegation resolution **algorithm**
  (`app.core.org_access`) — reused as-is for eligible-approver resolution
  (FR-18), never modified."* This plan changes **no algorithm**:
  `resolve_access`, `ancestor_unit_ids`, `descendant_unit_ids`,
  `effective_permission_values` and `delegation_audit_metadata` keep their exact
  current behavior, and the only edit to an existing function is extracting
  `active_grants_for_user`'s WHERE clause into `_grant_validity_clause(at)`
  which `active_grants_for_user` then calls — behavior-identical, covered by
  the existing `test_org_access_resolver.py`. Feature 003's sibling-module
  choice was driven by a *different* access axis (screen levels) that would
  have dragged `app.menu_security.models` into `app/core`; here the question
  ("who holds role X at unit Y") reads the very tables and the very flag
  `org_access` already owns (`UserRoleGrant`, `OrgUnit`,
  `Role.allows_hierarchy_rollup`, `Delegation`), so a sibling module would have
  to re-express the validity window, the ancestor-walk rollup predicate and the
  delegation-narrowing rules — exactly the duplication FR-18 and feature 002's
  FR-11 exist to forbid. **This is the one cross-cutting change in the feature
  and is flagged for the user's explicit attention before approval.**
  *Rejected*: `app/core/approver_eligibility.py` as a sibling (honors the
  spec's wording more literally, at the cost of a second copy of the predicate
  that must never drift); a per-candidate loop over `resolve_access` (it
  answers a different question — permission-string access, not role holding —
  and would be O(users) queries per requirement).
- **Eligibility is re-resolved at act time, never read from the snapshot.**
  `eligible_user_count` / `is_unfulfillable` on a requirement are a
  materialization-time *fact for display and blocking*; the 403 at decision
  time comes from a fresh `users_holding_role` call. The spec's "loses
  eligibility before acting" edge case requires exactly this, and it means a
  stale snapshot can never authorize anything.
- **Sequential violation is a hard 409, not a flag.** FR-13 permits either
  ("prevented … or, at minimum, clearly flagged"). Rejecting is the stronger
  reading, is unambiguous to test (AC-11), and avoids inventing an
  "out-of-order but accepted" state the rest of the model would have to carry.
  The UI additionally disables the button (`blocked_by_sequence`), but the API
  is the boundary.
- **FR-19's gap-resolution mechanism is: nothing automatic.** The spec
  explicitly leaves the mechanism to plan.md and requires no timeout or
  escalation. The decision is that the gap is resolved by the administrator
  granting the missing role (feature 002's `POST /role-grants`) and then
  invoking the existing **recalculate** action, which re-runs
  `users_holding_role` and clears `is_unfulfillable`. No new endpoint, no
  reassignment concept, no auto-escalation — every one of which would be new
  scope. This reuse is why `recalculate` refreshes `eligible_user_count` /
  `is_unfulfillable` even for requirements whose rule set did not change.
- **A requirement removed by recalculation that already carries a decision is
  `superseded_at` + `counts_toward_completion = false`, not
  `status='superseded'`.** Overwriting `status` would erase the very decision
  the spec says must remain visible. The partial unique index is therefore keyed
  on `WHERE deleted_at IS NULL AND superseded_at IS NULL`.
- **Append-only is enforced in two layers, and the DB layer is Postgres-only.**
  The ORM listeners catch application code; the trigger catches raw SQL and
  anything outside the ORM, which is what AC-10's "any code path, including an
  administrative one" demands. The migration guards the trigger with a dialect
  check so a SQLite-backed dev run still upgrades; CI and the test suite run on
  Postgres, so the trigger is exercised. *Rejected*: ORM-only enforcement (a
  raw `UPDATE` would pass, failing AC-10's plain reading); revoking
  `UPDATE`/`DELETE` grants on the table (the app connects as the owner, so it
  would be a no-op).
- **`AuthorityGrant` is not extended, and here is why it was considered.**
  `app/authority/service.py`'s `evaluate_authority` is this codebase's closest
  existing "restricted, declarative rule evaluator over a Contract's
  attributes" — `_grant_covers` already compares value / contract type /
  jurisdiction / risk band without `eval`, and `conditions.py`'s
  comparator-dispatch shape is modeled on it. But `AuthorityGrant` answers
  "**is this actor allowed to act at all**" (a ceiling on a principal), and is
  a *deny* gate evaluated live on every attempt; this feature answers "**which
  approvers does this instance require**" (an additive requirement on an item),
  and must be evaluated **once and frozen** (FR-5/FR-8). Those are opposite
  lifecycles — freezing an authority ceiling would be a security bug, and
  live-re-evaluating a required-approver list is exactly what FR-8 forbids.
  Both gates can apply to the same contract approval; neither replaces the
  other. The spec puts `AuthorityGrant` out of scope, and it is not in any
  agent's path mapping.
- **`module` is CHECK-constrained to exactly the two subject types FR-22 names**
  (`'contract'`, `'intake_request'`), never free-form. An unmapped module would
  make every condition on it fail closed silently, which looks like a bug; a
  third subject type is a deliberate migration plus a `facts.MODULE_FACTS`
  entry.
- **Risk — a condition rule referencing a role that is later deleted.**
  `required_role_id` is `ON DELETE RESTRICT`, so a role in use by a rule or a
  materialized requirement cannot be deleted; `app/roles/service.delete_role`
  will surface the DB error as a 409-shaped failure. Noted rather than
  silently relying on it: `test_approval_chain_config_api.py` does **not** test
  role deletion (it is another domain's endpoint), so this is a documented
  consequence, not a claimed behavior.
- **The intake inline ladder strip is fixed inside this feature (edit 3), not
  deferred — user decision.** An earlier draft left `app/intake/service.py`
  alone and accepted a blank strip for rerouted requests, on the grounds that
  the Chains tab is the authoritative view. That was rejected by the user: the
  strip is where intake staff read approval status, so a blank one is a
  regression, not a cosmetic gap. **How it is kept narrow**: neither
  `_serialize_chain` nor `_rung_label` is modified (both keep serving legacy
  rows verbatim); the two edited functions each gain one early branch; and the
  mapping itself lives in `approval_chains/dispatch.py`, so the new engine owns
  the shape and `intake/service.py` only calls it. The rung dict is a **superset
  of the existing shape** with four optional additions rather than a second,
  differently-shaped entry type — which was the real objection to the earlier
  version of this fix, and is what makes it safe. *Rejected*: returning the full
  `ChainInstanceDetailResponse` in that field (a breaking shape change for a
  strip that needs six fields); *rejected*: leaving `approval_request_id` out of
  the chain rungs (the frontend keys and guards on it — keeping it as an
  explicit `null` means the renderer and its `?? \`p${step_order}\`` fallback
  need no restructuring).
- **The planned-ladder preview shows base requirements only, and evaluates
  nothing.** `get_approval_chain` renders a "planned" ladder for a not-yet-
  submitted request. With routing rules now inert, that preview must come from
  the chain definition — but evaluating condition rules to build it would be
  evaluation on a read path, which FR-5 confines to chain entry and AC-9 tests
  for. So the preview lists the first step's base requirements and nothing
  else; conditions appear once the chain actually starts. *Rejected*: a
  "preview" evaluation (muddies FR-5 and would show a requirement that a later,
  real materialization might not produce); *rejected*: leaving the legacy
  `plan_chain` preview in place for orgs that have a definition (it would
  advertise routing rules that no longer determine anything — actively
  misleading).
- **Risk — an org that renamed or deleted its `approver` role.** The cutover
  seed picks the role named `approver`, falling back to `admin` (which always
  exists as the built-in). An org that renamed `approver` gets an admin-only
  default chain — conservative, never an auto-approval, and visible via
  `is_default_seeded` in the Condition-rules tab. Noted rather than
  hidden.
- **Risk — `approval_chain:read` is granted to every default role.** Like
  feature 002's `org_unit:read` and feature 003's `menu:read`, the permission
  string is the coarse gate; the per-instance visibility rule (requester /
  eligible approver / manager, plus `user_can_access_contract`) is the real
  boundary and is applied in the service on every single-instance read. A
  member with no relationship to an instance gets 404, not a redacted body.

### FR traceability

| FR | Where it lands |
|---|---|
| FR-1 | `approval_chain_step_rule` (base + conditional in one table) + `POST /steps/{id}/rules` + `_condition-rules-tab.tsx` |
| FR-2 | `conditions.OPERATORS` (exactly five) + the "exactly three keys" shape check + `ConditionExpressionIn` with `extra="forbid"` + `ck_..._base_xor_condition` |
| FR-3 | `service._materialize_step` step 4 (group by `required_role_id`) + `uq_approval_chain_requirement_role` + `triggered_by_rule_ids` / `condition_explanations` as lists |
| FR-4 | `conditions.evaluate_condition`'s fail-closed contract (never raises, malformed ⇒ not satisfied) + the per-rule loop in `_materialize_step` that continues past a miss |
| FR-5 | `_materialize_step`, called from exactly two mutating sites (`create_instance`, `_advance_step`); `approval_chain_requirement` rows are the fixed record every read returns verbatim |
| FR-6 | `approval_chain_requirement.is_base_requirement` + `triggered_by_rule_ids` (FK-linked rule ids) |
| FR-7 | `approval_chain_requirement.condition_explanations` (field, operator, value, **actual at eval time**) + `conditions.render_explanation` + the `explanation` response field |
| FR-8 | No evaluation on any read path; config mutations never touch requirement rows; `test_approval_chain_recalculate.py`'s AC-7 case |
| FR-9 | `POST /instances/{id}/recalculate`, gated on the distinct `approval_chain:recalculate`; **no Celery task, no beat schedule, no data-change hook anywhere in the feature** |
| FR-10 | The single `recalculated` history row with full before/after lists + the `approval_chain.recalculated` audit row |
| FR-11 | `approval_chain_history`'s reduced column set + the two ORM event listeners + the Postgres `BEFORE UPDATE OR DELETE` trigger |
| FR-12 | `approval_chain_step.approval_mode` (per step, NOT NULL default `'sequential'`, CHECK-constrained) |
| FR-13 | `service.record_decision`'s sequential gate → 409; `blocked_by_sequence` on the response drives the disabled UI control |
| FR-14 | The same gate is skipped entirely when `approval_mode == 'parallel'` |
| FR-15 | `POST .../requirements/{id}/decision` + the fresh `users_holding_role` membership check → 403 |
| FR-16 | `_advance_step` fires only when every live, counting requirement on the step is `approved`; unfulfillable and superseded rows are handled explicitly |
| FR-17 | `record_decision`'s reject branch: requirement `rejected`, siblings `cancelled`, `instance.status='rejected'`, immediately |
| FR-18 | `org_access.users_holding_role` — same validity clause, same `ancestor_unit_ids` walk, same `allows_hierarchy_rollup` flag, same delegation narrowing as `resolve_access` |
| FR-19 | `is_unfulfillable` / `eligible_user_count` set at materialization; `is_blocked` on the instance; a `blocked_no_eligible_approver` history row; the step cannot complete while such a requirement is pending |
| FR-20 | `blocking[]` + `blocking_visible` on the instance detail (populated only for `approval_chain:manage`) and `GET /instances/{id}/blocked` — on the chain instance itself, not in a log |
| FR-21 | Six brand-new tables (no legacy column altered); the dispatch sits **after** `submit_subject_for_approval`'s live-legacy-chain idempotency query, so an in-flight chain is returned untouched and never rerouted; no `ApprovalRequest`/`ApprovalDecision` row is read, written or migrated; `test_approval_chain_reroute.py`'s three in-flight cases + the bounded `git diff` are the verification artifacts |
| FR-22 | `approval_chains/dispatch.py` + the ~11-line dispatch block in `app.approvals.service.submit_subject_for_approval` (the shared entry point BOTH `submit_contract_for_approval` and `app.intake.approval_bridge.submit_request_for_approval` funnel into) + the `workflows/service.py` no-auto-advance guard + the `intake/service.py` strip branches (`start_approval_ladder`, `get_approval_chain`) so the rerouted chain is visible where intake staff actually read it + migration `0044` step 8's per-org, per-subject-type default definition so the reroute always has a chain to build |

### AC traceability

AC-1, AC-2 → `test_approval_chain_materialization.py`; AC-3, AC-4 →
`test_condition_evaluator.py` (plus AC-3's stored-malformed-rule case in
`test_approval_chain_materialization.py`); AC-5, AC-6 →
`test_approval_chain_materialization.py`; AC-7, AC-8, AC-9 →
`test_approval_chain_recalculate.py`; AC-10 →
`test_approval_chain_history_append_only.py` (ORM update, ORM delete, raw SQL
update, raw SQL delete); AC-11, AC-12, AC-13, AC-14 →
`test_approval_chain_decisions.py`; AC-15 → `test_org_access_role_holders.py`
(parent-unit holder with rollup); AC-16 →
`test_approval_chain_materialization.py`; AC-17 →
`test_approval_chain_decisions.py` (manager sees `blocking`, non-manager sees
`blocking_visible: false`); AC-18 → `test_approval_chain_materialization.py`
(cross-org create/read 404) + `test_org_access_role_holders.py` (cross-org
holder never returned); AC-19 → `test_approval_chain_decisions.py` (two
distinct history fields under an active feature-002 delegation); **AC-20 →
`test_approval_chain_reroute.py`'s three in-flight cases plus the documented
source inspection + docker-compose walkthrough in `verification.md`** (the
bounded two-file diff, the dispatch block quoted with its placement after the
idempotency query, a legacy chain submitted and completed with no
`approval_chain_*` row created) — see Test strategy; **AC-21 →
`test_approval_chain_reroute.py`** (contract submitted via
`POST /api/v1/approvals/requests` produces a materialized chain with a
condition-triggered requirement and zero `ApprovalRequest` rows); **AC-22 →
`test_approval_chain_reroute.py`** (the identical assertions for an intake
request submitted through the intake submit-for-approval path, plus the
workflow-driven case that must not auto-advance, plus the intake-strip parity
cases proving the rerouted chain is visible on the request detail screen).
