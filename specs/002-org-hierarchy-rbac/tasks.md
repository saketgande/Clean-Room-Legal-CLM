# Task Breakdown: Org-Unit Hierarchy + Scoped, Hierarchical RBAC Resolver

Feature ID: 002-org-hierarchy-rbac
Plan: ./plan.md (APPROVED)
Created: 2026-09-13
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [x] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- `dependsOn: —` means no dependencies (wave 1).
- Every task maps to requirement(s) from spec.md, noted as `(FR-N)`.
- Two tasks that touch the same file never carry `[P]` together.
- The migration task never runs parallel with another migration task.

## Waves

### Wave 1 — Foundation (schema)

- [x] T001 (db-engineer) `OrgUnit`, `Delegation` models; `UserRoleGrant` mapped model
      replacing the `user_role` association table (+ `user_role_table` alias);
      `Role.allows_hierarchy_rollup`; registry entries; Alembic revision
      `0042_org_hierarchy_rbac` on head `0041_merge_heads` including the root-per-org
      backfill data migration (FR-1, FR-4, FR-6, FR-7, FR-12, FR-13, FR-20) —
      dependsOn: — —
      files: `backend/app/org_structure/__init__.py`, `backend/app/org_structure/models.py`,
      `backend/app/auth/models.py`, `backend/app/models.py`,
      `backend/alembic/versions/0042_org_hierarchy_rbac.py`

### Wave 2 — Parallel implementation (interface + resolver + edges)

- [x] T002 [P] (backend-dev) `org_structure` Pydantic schemas (`OrgUnitCreate/Update/Response`,
      `OrgUnitReparentEntry`, `OrgUnitDeleteResponse`, `RoleGrantCreate/Response`,
      `DelegationCreate/Response`, `DelegationEligibilityEntry`) + access helpers
      (`get_org_unit_or_404`, `get_org_root`, `ensure_same_org`) (FR-1, FR-6, FR-13) —
      dependsOn: T001 —
      files: `backend/app/org_structure/schemas.py`, `backend/app/org_structure/access.py`
- [x] T003 [P] (backend-dev) Shared resolver `org_access.py` — `resolve_access`,
      `assert_access`, `effective_permission_values`, `ancestor_unit_ids`,
      `descendant_unit_ids`, `active_grants_for_user`, `delegation_audit_metadata` —
      plus its pytest suite (rollup up/no-lateral/no-rollup-when-flagged-off, independent
      expiry vs. soft-delete exclusion, delegation intersection/no-chain/immediate-revoke)
      (FR-8, FR-9, FR-11, FR-14, FR-15, FR-16, FR-17, FR-22, FR-23) —
      dependsOn: T001 —
      files: `backend/app/core/org_access.py`, `backend/tests/test_org_access_resolver.py`
- [x] T004 [P] (backend-dev) Two new permission strings (`org_unit:read`,
      `delegation:manage`) + `DEFAULT_ROLE_PERMISSIONS` wiring in `rbac.py`; verify/update
      the locked-down production permission-catalog fixture in
      `test_phase10_security_hardening.py` if it enumerates all permission strings —
      dependsOn: T001 —
      files: `backend/app/core/rbac.py`, `backend/tests/test_phase10_security_hardening.py`
- [x] T005 [P] (backend-dev) `roles/schemas.py` + `roles/service.py`: add
      `allows_hierarchy_rollup` to `RoleResponse`/`RoleUpdate` and `update_role`;
      rewrite `set_user_roles` to create/soft-delete `UserRoleGrant` rows at the org
      root instead of assigning the now-viewonly `target.roles`; add
      `deleted_at IS NULL` to `serialize_role`'s `user_count` and `delete_role`'s
      holder count. Run `test_role_assignment_privilege_escalation.py` and
      `test_phase1_auth_foundation.py` as regression gates (FR-7, FR-9, FR-12) —
      dependsOn: T001 —
      files: `backend/app/roles/schemas.py`, `backend/app/roles/service.py`
- [x] T006 [P] (frontend-dev) TS interfaces (`OrgUnit`, `OrgUnitTreeNode`,
      `RoleGrant`, `Delegation`, `DelegationEligibilityEntry`, `RoleResponse.allows_hierarchy_rollup`)
      + endpoint client additions (`orgUnitsApi`, `roleGrantsApi`, `delegationsApi`,
      `rolesApi.update` payload) per the frozen interface contract —
      dependsOn: — —
      files: `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts`
- [x] T007 [P] (frontend-dev) Pure tree helpers `buildOrgTree`, `flattenWithIndent`,
      `isDescendantOf` + vitest coverage (FR-3 client-side cycle guard, FR-26) —
      dependsOn: — —
      files: `frontend/src/lib/org-tree.ts`, `frontend/src/lib/org-tree.test.ts`

### Wave 3 — Parallel build-out (routes/service, chokepoint, pages)

- [x] T008 [P] (backend-dev) `org_structure/service.py` + `org_structure/routes.py` —
      org-unit CRUD/re-parent/soft-delete-with-reparent, role-grant list/create/revoke,
      delegation list/eligibility/create/revoke, each with permission gates, org
      scoping, and audit rows; pytest for all three resource groups
      (FR-1..FR-6, FR-10, FR-13..FR-15, FR-17..FR-21) —
      dependsOn: T002, T003, T004 —
      files: `backend/app/org_structure/service.py`, `backend/app/org_structure/routes.py`,
      `backend/tests/test_org_units_api.py`, `backend/tests/test_role_grants_api.py`,
      `backend/tests/test_delegations_api.py`
- [x] T009 [P] (backend-dev) `require_permission`'s inner dependency gains
      `db: Session = Depends(get_db)` and calls
      `org_access.effective_permission_values(db, user=current_user)` in place of
      `current_user.permission_values` — expiry now enforced app-wide, per the
      approved cross-cutting decision (FR-9, FR-23) —
      dependsOn: T003 —
      files: `backend/app/core/deps.py`
- [x] T010 [P] (frontend-dev) `/org-structure` page: `Tabs` (Org units | Role grants),
      `_org-unit-tree.tsx` (indented tree, Rename/Move/Delete, reparented-children
      `MessageBar` after delete), `_org-unit-modal.tsx` (create/rename/move, parent
      picker excludes self+descendants), `_role-grants-panel.tsx` (list + revoke),
      `_assign-grant-modal.tsx` (user/role/org-unit pickers, optional validity window)
      (FR-26, FR-27) —
      dependsOn: T006, T007 —
      files: `frontend/src/app/(app)/org-structure/page.tsx`,
      `frontend/src/app/(app)/org-structure/_org-unit-tree.tsx`,
      `frontend/src/app/(app)/org-structure/_org-unit-modal.tsx`,
      `frontend/src/app/(app)/org-structure/_role-grants-panel.tsx`,
      `frontend/src/app/(app)/org-structure/_assign-grant-modal.tsx`
- [x] T011 [P] (frontend-dev) `/delegations` self-service page: "Delegated by me"
      (revoke when `can_revoke`) and "Delegated to me" (no revoke control) cards,
      admin "All delegations" tab; `_delegation-modal.tsx` populated from
      `delegationsApi.eligibility()` (FR-28) —
      dependsOn: T006, T007 —
      files: `frontend/src/app/(app)/delegations/page.tsx`,
      `frontend/src/app/(app)/delegations/_delegation-modal.tsx`
- [x] T012 [P] (frontend-dev) Two nav entries — `Org structure` (admin section,
      `admin_panel:access`) and `Delegations` (workspace section, `delegation:manage`) —
      dependsOn: T006 —
      files: `frontend/src/components/aegis-rail.tsx`
- [x] T013 [P] (frontend-dev) `RoleEditorModal` in the admin page: "Rolls up the
      org-unit hierarchy" checkbox bound to `allows_hierarchy_rollup`, disabled for the
      built-in `admin` role (FR-7) —
      dependsOn: T006 —
      files: `frontend/src/app/(app)/admin/page.tsx`

### Wave 4 — Integration

- [x] T014 (backend-dev) Register the three new routers (`org_units_router`,
      `role_grants_router`, `delegations_router`) in `main.py` next to `walls_router` —
      dependsOn: T008 —
      files: `backend/app/main.py`

### Wave 5 — QA

- [x] T015 (qa-engineer) Verify AC-1..AC-23 against the running stack; write
      `verification.md`. AC-9 (migration backfill preserves pre-existing access) has
      no pytest fixture path (the test DB is already post-migration) — document and
      execute it as a manual `docker compose` procedure: seed flat `user_role` rows
      pre-migration on a scratch DB, run `alembic upgrade head`, confirm every user
      keeps identical effective access via `org_access.effective_permission_values` —
      dependsOn: T008, T009, T010, T011, T012, T013, T014 —
      files: `specs/002-org-hierarchy-rbac/verification.md`

## Task detail notes

- **T001** is the single highest-risk task: it reshapes `user_role` from an
  association `Table` into a full model with a new PK shape. Do the migration and the
  model edit in the same task/commit so schema and ORM never disagree mid-implementation.
  The migration's data-backfill step (root org unit per existing organization, then
  every existing `user_role` row gets that root's id, `valid_to = NULL`) must run
  inside the same revision, not a follow-up script — CI applies migrations once.
- **T005**'s regression run is a hard gate, not a suggestion — `set_user_roles` is
  the write path the existing admin role-assignment screen already depends on; a
  silent behavior change here is exactly the kind of privilege-escalation risk
  `test_role_assignment_privilege_escalation.py` exists to catch.
- **T008** reuses (imports, does not copy) the existing grant-escalation check from
  `app/roles/service.py` — if T005 changes that helper's signature, resolve the
  conflict by having T008's implementer re-read the current file rather than
  assuming the plan's snapshot.
- **T009** is a genuine performance-sensitive change (one extra indexed query on
  every permission-gated request in the app) — the implementer should confirm the
  new query has an index-backed WHERE clause (`user_id`, `deleted_at`, `valid_to`)
  before merging, not just that it returns correct results.
- Wave 3 has 6 parallel tasks across 2 agents — the orchestrator should launch all
  six together and track them independently; none share a file with another.
