# F-03 — Cross-tenant leak via admin shortcut (Critical)

**Agent 2 finding:** `accessible_contract_filter(user)` returns `true()` for org admins. Every join-with-Contract query that does NOT also constrain `Contract.org_id == user.org_id` could surface cross-org rows. Concrete callers identified by Agents 1+2: `obligations/routes.py:50-58`, `renewals/routes.py` `list_renewals`.

## Headline change
- New `app/core/access_policy.py` exports `ContractAccessPolicy.scope_query(...)` / `access_predicate(user)` / `_access_predicate(user)`. The org filter is mandatory and always emitted before the membership/share predicate.
- `app/contracts/access.py` `accessible_contract_filter(user)` becomes a back-compat shim that delegates to `ContractAccessPolicy.access_predicate(user)` — **no more bare `true()`**.
- `obligations.routes.list_obligations` and `renewals.routes.list_renewals` are updated to use the new shim; their behavior is the same for non-admin users, but admins can no longer see cross-org rows even if `Obligation.contract_id` somehow points to a different-org contract.
- `obligations.list_obligations` also picks up `status_filter` as an enum (folds in F-21).

## Cross-cutting dependencies
- **CC-1** `app/core/access_policy.py` — `ContractAccessPolicy`.

## Agent 3 done-conditions met
- Grep `accessible_contract_filter` no longer returns `true()` in any path — verified by inspecting `contracts_access.py` (only `ContractAccessPolicy.access_predicate` returned).
- `tests/test_F03_access_filter.py::test_org_admin_cannot_see_other_org_obligations` exercises the deliberate cross-org mis-key and asserts the admin sees zero rows.
- Every existing caller (12 sites total per Agent 3) still compiles against the same function signature, because we kept the shim.

## Files
- `contracts_access.py` — rewritten access predicate module.
- `obligations_routes_list.py` — updated caller (also picks up enum `status_filter` from F-21).
- `renewals_routes_list.py` — updated caller.

## Tests
- `proposals/audit_2026-06-02/tests/test_F03_access_filter.py`
- `proposals/audit_2026-06-02/tests/test_cross_cutting.py` — `ContractAccessPolicy` unit tests.
