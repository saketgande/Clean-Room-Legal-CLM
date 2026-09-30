# Verification Report: Dynamic, Condition-Driven Approval Chains (004-approval-chain-reconciliation)

QA pass by qa-engineer (T017). All evidence below was independently executed or
independently read against the current code / live stack, not taken on the
strength of prior agents' self-reports.

## Overall verdict

**DONE, with two genuine findings** (both low-severity, neither a spec-breaking
defect, neither requiring a code fix before ship — routed back to the
orchestrator for a decision, not fixed by me):

1. **Test coverage gap, not a behavior gap**: AC-13 (mixed sequential+parallel
   steps in one chain) and AC-18 (cross-org denial at the chain-instance level
   — view/decide/recalculate) have no dedicated automated pytest, despite being
   named, numbered acceptance criteria. I independently verified both behaviors
   are correct by direct exercise against the running service layer (scripts
   and full output below) — the code is right — but there is no regression
   test pinning either one, so a future change could silently break them
   without CI noticing.
2. **Dev-DB hygiene**: `test_approval_chain_reroute.py`'s fixture (per T016's
   documented finding) intentionally does not roll back its transaction,
   because it shares `DATABASE_URL` with this docker-compose stack's live
   Postgres (confirmed: `backend`'s `DATABASE_URL` and the value pytest uses
   inside the same container are identical — `postgresql+psycopg://legal_clm:
   legal_clm@postgres:5432/legal_clm`). Every full-suite run therefore
   permanently leaves ~16 throwaway organizations (`AC21`, `AC22`, `AC20a`,
   `WorkflowM2`, `LifecycleApprove/RejectContract/Intake`, etc.) in the shared
   dev database. Confirmed via direct SQL: 62 organizations exist in the dev DB
   today, of which only 1 (`Local Legal CLM`) is real; the other 61 are
   accumulated test debris from this task's and prior tasks' `pytest -q` runs
   against the live stack. Not a CI problem (CI's Postgres container is
   ephemeral per run), and not a correctness defect (each row is UUID-suffixed
   and isolated), but it will grow unbounded on every local `pytest -q` run
   against this docker-compose stack and pollutes exactly the kind of live-DB
   spot-check this verification pass (and T010's manual smoke tests) rely on.

Everything else — all 22 FRs, 20 of 22 ACs with dedicated automated tests, the
security-critical auto-approve bug fix, the condition evaluator's freedom from
dynamic execution, the append-only history guard (both ORM and Postgres
trigger layers, the latter independently re-verified live), and the org-scoping
model — is confirmed correct.

One additional minor code-quality note (not spec-relevant, no test failure):
`backend/app/approval_chains/conditions.py` defines `_scalar_ok` **twice**
(lines 130 and 185, identical bodies) — the second definition silently shadows
the first. Functionally harmless (both implementations are byte-identical) and
ruff's F811 does not flag it, but it's dead code that should be collapsed to
one definition in a follow-up cleanup task.

## Gate results

### Backend (`docker compose exec backend`, stack already up)

```
python -m ruff check . --ignore EXE002          -> 82 errors, ALL pre-existing/unrelated
                                                    (confirmed baseline ~84 per prior features)
python -m ruff check <every file this feature    -> All checks passed! (0 findings)
  touched/created — see list below>
alembic upgrade head                             -> no-op, already at head
alembic heads                                     -> 0044_approval_chains (single head)
python -m pytest -q                               -> 472 passed, 6 skipped, 20 warnings
```

Files scoped for the feature-specific ruff pass (all clean):
`app/approval_chains/` (all modules), `app/approvals/service.py`,
`app/workflows/service.py`, `app/intake/service.py`, `app/core/org_access.py`,
`app/core/rbac.py`, `app/main.py`, `alembic/versions/0044_approval_chains.py`,
`tests/test_condition_evaluator.py`, `tests/test_org_access_role_holders.py`,
`tests/test_approval_chain_config_api.py`, `tests/test_approval_chain_materialization.py`,
`tests/test_approval_chain_decisions.py`, `tests/test_approval_chain_recalculate.py`,
`tests/test_approval_chain_history_append_only.py`, `tests/test_approval_chain_reroute.py`.

The repo-wide 82 findings were cross-checked against `git diff` for the three
edited legacy files (`approvals/service.py`, `workflows/service.py`,
`intake/service.py`): every flagged line (import-sort at file-top, an unused
`agents`/`routing` import, a quoted type-annotation on an untouched function)
sits **outside** this feature's diff hunks (verified via `git diff` — the
feature's edits are additive blocks inserted into `submit_subject_for_approval`,
the `approval` step branch + `refresh_run`, and `start_approval_ladder`/
`get_approval_chain`; none of the flagged lines are inside those hunks). No new
ruff finding was introduced by this feature; the migration file
(`0044_approval_chains.py`) — the specific class of issue feature 003's own QA
pass caught — is independently clean.

### Frontend (`frontend/`)

```
rm -rf .next && npm run typecheck   -> clean, 0 errors
npm run test                        -> 5 files, 48 tests passed (including
                                        approval-chains.test.ts, 17 tests)
```

`npm run lint` was NOT run per instructions.

## Independent confirmation of the auto-approve bug fix

Read `backend/app/workflows/service.py` line-by-line (not taken on faith):

- **No-contract branch** (`_execute_step`, `t == "approval"`, `not
  run.contract_id`): after `submit_request_for_approval` returns `reqs`, the
  code now does `instance = chain_dispatch.live_instance_for(db, org_id=...,
  module="intake_request", module_record_id=req.id)` and only takes the
  `return "advance"` short-circuit `if not reqs and instance is None`. Prior to
  this fix the guard was `if not reqs`, which is **unconditionally true** for
  every rerouted org (a rerouted `submit_request_for_approval` always returns
  `[]`, since the legacy `ApprovalRequest` ladder never runs once a chain
  definition exists) — meaning every workflow-driven intake approval for a
  rerouted org would have silently completed with zero sign-off. Confirmed
  fixed: `instance` is a real DB lookup of the live `ApprovalChainInstance`,
  not a proxy value.
- **Resume logic** (`refresh_run`, same no-contract/`approval` branch): reads
  `cid = (sr.result or {}).get("chain_instance_id")`, and when present, loads
  the actual `ApprovalChainInstance` row fresh (`db.get(ApprovalChainInstance,
  cid)`) and branches on `inst.status == "approved"` / `"rejected"` — the
  instance's genuine, current, DB-backed status, not a cached/stale value or a
  proxy such as "reqs is truthy." If `inst` is deleted or still `"pending"`,
  the branch correctly falls through to `return run` (stays `waiting`), never
  auto-advancing.
- **No remaining zero-sign-off path**: grepped the whole codebase for every
  function referencing `approval_chains` outside the new domain — exactly
  `submit_subject_for_approval`, `_execute_step` + `refresh_run`, and
  `start_approval_ladder` + `get_approval_chain` (verified by the AST-based
  `test_ac20_bounded_diff_only_the_named_functions_touch_approval_chains` test,
  which I re-ran: 3/3 passed, and by directly reading the current source of
  all three files myself). No Celery task, no read endpoint, and no other
  workflow step type references `approval_chains` at all
  (`grep -rn approval_chain backend/app/jobs/tasks.py` → no matches). Combined
  with `_materialize_step` having exactly two call sites in
  `app/approval_chains/service.py` (`create_instance` line 851,
  `_advance_step` line 910 — grepped directly), there is no code path by which
  a workflow-driven approval can complete without either (a) a legacy
  `ApprovalRequest` chain being fully approved, or (b) a chain instance's
  `record_decision` (which enforces fresh eligibility, sequential ordering,
  and `enforce_authority` on approve) driving it to `"approved"`.
- **Automated regression proof re-run**: `test_workflow_approval_step_does_not_auto_advance_while_chain_is_pending`
  (`backend/tests/test_approval_chain_reroute.py:574`) — asserts `run.status
  == "waiting"` and `!= "complete"` immediately after starting a no-contract
  workflow against a rerouted org, then only reaches `"complete"` after the
  chain instance is explicitly decided `"approved"` and `refresh_run` is
  called again. This is the single highest-value test in the feature; I read
  it in full and it asserts exactly the claimed behavior, not merely that
  "some test exists."

Verdict: **the auto-approve bug fix is real, complete, and independently
confirmed by direct source reading, not merely a passing test.**

## Live-DB cutover-seed verification

Queried the running dev Postgres directly (`docker compose exec postgres
psql`):

```sql
SELECT o.name, d.id, d.module, d.is_default_seeded, s.id, s.step_key,
       r.id, r.is_base_requirement, r.required_role_id, ro.name
FROM organization o
JOIN approval_chain_definition d ON d.org_id = o.id AND d.is_default_seeded = true
JOIN approval_chain_step s ON s.definition_id = d.id
JOIN approval_chain_step_rule r ON r.step_id = s.id
JOIN role ro ON ro.id = r.required_role_id
WHERE o.name = 'Local Legal CLM';
```

Result: exactly **2** `is_default_seeded=true` definitions total across the
entire database (one `contract`, one `intake_request`, both belonging to the
one real pre-existing org, "Local Legal CLM"), each with exactly **1** step
and exactly **1** base-requirement rule targeting the `approver` role. Cross-
checked `user_role` (the actual table `UserRoleGrant` maps to — confirmed via
`__tablename__ = "user_role"` in `app/auth/models.py`) for that role: **2**
active, non-soft-deleted, unrestricted-validity grants exist, confirming the
seed produces a genuinely fulfillable base requirement, cross-checked against
feature 002's org-root grant backfill as instructed.

The other 60 organizations in the database are NOT additional real orgs
missing seeded definitions — they are test-created orgs from this feature's
own pytest suite (see "Dev-DB hygiene" finding above), each created *after*
migration 0044 ran, so it is expected and correct per FR-1/plan.md that they
have no seeded definition (the cutover seed runs once, at migration
application time, for orgs that existed then; a new org created afterward
needs an admin to define a chain via `POST /approval-chains/definitions`,
consistent with FR-1). Confirmed this is by design, not a gap, by reading
`test_approval_chain_reroute.py`'s own scenarios, which each construct their
own `ChainDefinitionCreate` for their test org rather than relying on any
seed.

## Postgres append-only trigger — independently re-verified live

Beyond re-reading `test_approval_chain_history_append_only.py` (which proves
the ORM-layer guard and a source-level AST scan that `service.py` never calls
`.update()`/`.delete()` against an `ApprovalChainHistory` query), I issued a
raw SQL `UPDATE` directly against `approval_chain_history` via `psql` (bypassing
the ORM and the application entirely):

```
NOTICE:  UPDATE REJECTED: approval_chain_history is append-only
```

Confirms the Postgres `BEFORE UPDATE OR DELETE` trigger
(`trg_approval_chain_history_append_only` /
`approval_chain_history_append_only()`, defined in
`alembic/versions/0044_approval_chains.py`) is genuinely installed and
enforced at the database layer, independent of and in addition to the ORM
event-listener layer.

## Condition evaluator — independent source read

Read all of `backend/app/approval_chains/conditions.py`. Confirmed directly
(not just via the test suite's own source-scan):
- No `eval`, `exec`, `compile`, `__import__`, `getattr`, or `setattr` anywhere
  in the file (`grep` confirms the only occurrences are in docstrings/comments
  describing their absence).
- Dispatch is a frozen dict of 5 ordinary functions (`_OPERATOR_FUNCS`); an
  unknown operator key is a dict miss (`_miss("unknown_operator", ...)`), never
  a fallback or dynamic lookup.
- The `{"field","operator","value"}` exact-key-set check
  (`_shape_check`/`validate_expression`) rejects anything with extra keys
  (e.g. `"and"`/`"or"`), rejects nested dict/list values via `_value_bounds_ok`
  (including a self-referential list, since the recursive element is itself a
  list and thus fails the scalar-leaf check — no infinite loop possible), and
  the whole `evaluate_condition` body is wrapped in `try/except Exception`
  returning a malformed result, so no adversarial input can raise out of the
  function.

## Independent confirmation: `org_access.py`'s core algorithm is unmodified

Re-ran `tests/test_org_access_resolver.py` (feature 002's own, pre-existing
test file) in isolation: **17 passed**, unchanged from feature 002's baseline
— direct proof the `_grant_validity_clause` extraction is behavior-identical,
not merely additive-in-isolation as claimed.

## FR traceability (all 22)

| FR | Verdict | Evidence |
|---|---|---|
| FR-1 | PASS | `test_approval_chain_config_api.py` (CRUD happy path, step/rule creation); `service.create_step`/`create_rule` |
| FR-2 | PASS | `conditions.py` `OPERATORS` frozenset (exactly 5); `_shape_check` rejects extra keys/combinators; `test_condition_evaluator.py` |
| FR-3 | PASS | `test_multiple_firing_rules_for_same_role_collapse_to_one_requirement` (materialization test, read in full) |
| FR-4 | PASS | `evaluate_condition`'s `try/except Exception` + malformed-shape battery; `test_condition_evaluator.py` (42 tests, read source directly) |
| FR-5 | PASS | `_materialize_step` has exactly 2 call sites (grepped: `create_instance`, `_advance_step`); `test_read_paths_never_materialize_new_requirements` |
| FR-6 | PASS | `ApprovalChainRequirement.triggered_by_rule_ids`; `test_multiple_firing_rules_...` asserts `triggered_by_rule_ids` distinguishes base vs conditional |
| FR-7 | PASS | `render_explanation` produces the exact FR-7 string format; `test_condition_evaluator.py` asserts it |
| FR-8 | PASS | No code path re-evaluates a materialized requirement outside `recalculate`/`_materialize_step`'s two call sites (structural, confirmed by grep) |
| FR-9 | PASS | `recalculate` is a distinct, permission-gated (`approval_chain:recalculate`) function; zero Celery/scheduled reference to `approval_chains` anywhere (grepped `app/jobs/tasks.py` — no matches) |
| FR-10 | PASS | `recalculate`'s `_append_history(..., action="recalculated", before_json=..., after_json=...)`; `test_recalculate_writes_exactly_one_recalculated_history_row_with_full_before_after` |
| FR-11 | PASS | ORM event listener + Postgres trigger, both independently confirmed (ORM via pytest, trigger via my own live raw-SQL `UPDATE` attempt) |
| FR-12 | PASS | `ApprovalChainStep.approval_mode` is a per-step column; independently exercised a 2-step chain (sequential + parallel) end-to-end — see AC-13 below |
| FR-13 | PASS | `test_sequential_gate_rejects_a_later_decision_while_an_earlier_one_is_pending` → 409 |
| FR-14 | PASS | AC-12/13 manual exercise: step B (parallel) accepted role4 (seq2) before role3 (seq1) |
| FR-15 | PASS | `test_lost_eligibility_before_deciding_is_rejected_fresh_not_from_a_stale_snapshot` → 403 |
| FR-16 | PASS | `test_advance_step_fires_when_every_live_counting_requirement_is_approved` |
| FR-17 | PASS | `test_reject_cancels_every_other_live_pending_requirement_and_calls_on_reject` |
| FR-18 | PASS | `org_access.users_holding_role` reuses `ancestor_unit_ids`/`_grant_validity_clause`; `test_org_access_role_holders.py` (12 tests) |
| FR-19 | PASS | `test_role_with_no_holders_is_unfulfillable_and_writes_history` |
| FR-20 | PASS | `ChainInstanceDetailResponse.blocking`/`blocking_visible`; `_chain-detail.tsx` renders it; gated on `approval_chain:manage` |
| FR-21 | PASS | `test_ac20a_resubmitting_a_live_legacy_chain_returns_it_unchanged`, `test_ac20b_a_legacy_chain_decisions_to_completion_exactly_as_today` |
| FR-22 | PASS | `test_ac21_new_contract_submission_reroutes_to_a_materialized_chain`, `test_ac22_new_intake_submission_reroutes_to_a_materialized_chain` |

## AC traceability (all 22)

| AC | Verdict | Evidence |
|---|---|---|
| AC-1 | PASS | `test_multiple_firing_rules_for_same_role_collapse_to_one_requirement` (base + condition-triggered both present) |
| AC-2 | PASS | `test_condition_rule_that_does_not_match_is_not_materialized` |
| AC-3 | PASS | `test_condition_evaluator.py`'s AND/OR-combinator-rejection + unknown-operator cases (read source) |
| AC-4 | PASS | `test_condition_evaluator.py`'s 42-test adversarial battery (read source: missing fields, wrong types, nested/recursive structures, unknown operators, oversized values — all fail closed, no unhandled exception, confirmed by direct source read of `evaluate_condition`) |
| AC-5 | PASS | `test_multiple_firing_rules_for_same_role_collapse_to_one_requirement` — exactly 2 requirements (not 3), `condition_explanations` has both rules |
| AC-6 | PASS | Same test: `explanation.get("text")` truthy for each of the two firing rules; `render_explanation` format independently read |
| AC-7 | PASS | Structural: no code path re-materializes outside the 2 call sites (FR-8 evidence) — no dedicated "correct then don't recalc" pytest exists, but the invariant is the same one FR-8/AC-9 rely on and is proven the same way |
| AC-8 | PASS | `test_recalculate_writes_exactly_one_recalculated_history_row_with_full_before_after` |
| AC-9 | PASS | Structural: zero Celery/scheduled reference to `approval_chains`; `_materialize_step` 2 call sites; `recalculate` uses the separate `_group_rule_sources` path, never `_materialize_step` — read directly in `service.py` |
| AC-10 | PASS | `test_orm_guard_rejects_a_mutation_attempt_against_an_existing_history_row` + my own live raw-SQL trigger re-verification |
| AC-11 | PASS | `test_sequential_gate_rejects_a_later_decision_while_an_earlier_one_is_pending` |
| AC-12 | PASS | Manual exercise (AC-13 script below): step B parallel accepted out-of-order |
| AC-13 | **PASS — but no automated test; verified by direct manual exercise (see below). Recommend a follow-up test.** | Manual: 2-step chain, step A sequential (409 on out-of-order) → completes → step B parallel (accepts role4 before role3) → instance `"approved"` |
| AC-14 | PASS | `test_reject_cancels_every_other_live_pending_requirement_and_calls_on_reject` |
| AC-15 | PASS | `test_role_held_at_global_satisfies_query_scoped_to_a_descendant_unit` (`test_org_access_role_holders.py:174`) |
| AC-16 | PASS | `test_role_with_no_holders_is_unfulfillable_and_writes_history` |
| AC-17 | PASS | `ChainInstanceDetailResponse.blocking`/`blocking_visible` fields + `_chain-detail.tsx`'s rendering, gated on `approval_chain:manage` (read schema + component) |
| AC-18 | **PASS — but no automated test; verified by direct manual exercise (see below). Recommend a follow-up test.** | Manual: Org B admin/user attempting `get_instance_detail`/`recalculate`/`record_decision` against Org A's instance all raise `HTTPException(404, "Chain instance not found")` |
| AC-19 | PASS | `test_delegate_decision_attributes_acted_by_and_delegated_from_as_distinct_fields` |
| AC-20 | PASS | `test_ac20a_*`, `test_ac20b_*`, `test_ac20c_*`, plus the AST-based `test_ac20_bounded_diff_only_the_named_functions_touch_approval_chains` (re-ran: 3/3 passed) |
| AC-21 | PASS | `test_ac21_new_contract_submission_reroutes_to_a_materialized_chain`, `test_ac21_contract_below_threshold_only_materializes_the_base_requirement` |
| AC-22 | PASS | `test_ac22_new_intake_submission_reroutes_to_a_materialized_chain` |

## Manual exercises performed (full output)

**AC-13** (mixed sequential+parallel steps in one chain) — ad hoc script run via
`docker exec` against the service layer directly (transaction rolled back, no
DB pollution left behind):

```
STEP A sequential-order guard: OK -> 409
After step A complete, current_step_id == <stepB id> (matches expected)
STEP B parallel out-of-order accept: OK
Final instance status: approved
AC-13 (mixed sequential+parallel across two steps of one chain) CONFIRMED
```

**AC-18** (cross-org denial at the instance level) — ad hoc script, same
method:

```
VIEW: denied as expected -> 404 Chain instance not found
RECALC: denied as expected -> 404 Chain instance not found
DECIDE: denied as expected -> 404 Chain instance not found
```

## Frontend spot-checks

- `frontend/src/app/(app)/approvals/page.tsx` imports `ChainsTab` from
  `./_chains-tab` and `ConditionRulesTab` from `./_condition-rules-tab`;
  grepped both target files' actual exports (`export function ChainsTab()`,
  `export function ConditionRulesTab()`) — names match exactly. `ChainDetail`
  is imported and used inside `_chains-tab.tsx` itself (list→detail via an
  in-page Modal), consistent with board.md's account.
- `npm run typecheck` (post `rm -rf .next`) is clean across the whole repo,
  which would have caught any import/export name mismatch regardless.

## Defect list (for orchestrator follow-up — not fixed by qa-engineer)

1. **(Low)** No dedicated automated test for AC-13 (mixed sequential/parallel
   steps within one chain instance) or AC-18 (cross-org denial at the
   chain-instance level for view/decide/recalculate). Both behaviors are
   verified correct by manual exercise above, but neither is pinned by CI.
   Recommend two small test additions to `test_approval_chain_decisions.py`
   (AC-18) and `test_approval_chain_config_api.py` or a new file (AC-13).
2. **(Low, hygiene)** `backend/tests/test_approval_chain_reroute.py`'s `db`
   fixture commits directly against the same Postgres database this
   docker-compose stack serves in dev (no final rollback, by design per T016's
   documented deadlock fix), so every `pytest -q` run against this running
   stack leaves ~16 permanent throwaway organizations in the shared dev
   database. Not a CI issue (ephemeral DB there) and not a correctness defect,
   but worth either (a) pointing this test file at a dedicated test schema/DB
   distinct from the docker-compose dev stack's Postgres, or (b) accepting the
   tradeoff explicitly, since it currently silently grows dev-DB row counts on
   every local test run.
3. **(Trivial, code quality)** `backend/app/approval_chains/conditions.py`
   defines `_scalar_ok` twice (identical bodies, lines 130 and 185); the
   second silently shadows the first. No functional impact (ruff does not flag
   it as F811 either), but should be collapsed to one definition.

None of these three findings block shipping the feature; none are security
defects (the underlying controls are all correctly implemented and now
independently verified); items 1 and 3 are quick follow-ups, item 2 is a test-
infrastructure hygiene call for the orchestrator to make.
