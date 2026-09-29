# Task Breakdown: Menu/Screen-Level Security (VIEW/ADD/EDIT/DELETE) with Dynamic Menu Visibility

Feature ID: 003-menu-screen-security
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

## Waves

### Wave 1 — Foundation (schema)

- [ ] T001 (db-engineer) `Screen`, `ActionLevel`, `MenuItem`, `RoleScreenAccess`
      models; registry entries; Alembic revision `0043_menu_screen_security` on
      head `0042_org_hierarchy_rbac` — DDL, the 39-row screen catalog seed, the
      28-row menu-tree seed mirroring today's rail exactly, the seeded
      action-level reference rows, and the FR-26 backfill (every existing role
      gets an org-wide `RoleScreenAccess` row per existing screen, at the level
      matching its current permission-string access, with a guaranteed DELETE
      row for admin on the `screen_access` screen for FR-25) (FR-1, FR-2, FR-3,
      FR-24, FR-25, FR-26) —
      dependsOn: — —
      files: `backend/app/menu_security/__init__.py`, `backend/app/menu_security/models.py`,
      `backend/app/models.py`, `backend/alembic/versions/0043_menu_screen_security.py`

### Wave 2 — Parallel implementation (resolver + interface + client contract)

- [ ] T002 [P] (backend-dev) Resolver `core/screen_access.py` —
      `resolve_screen_access`/`resolve_all_screen_access` (context = union of
      `ancestor_unit_ids` over the org units where the caller holds an active
      role grant; highest-level-wins across the chain; `allows_hierarchy_rollup`
      gates ancestor matches, exact/org-wide matches always count) — plus its
      pytest suite (rollup-consistent-with-org_access, no-lateral/no-upward,
      rollup-disabled locks to exact unit, highest-level-wins never reduced by a
      narrower grant, cross-org isolation) (FR-4, FR-5, FR-6, FR-18, FR-19) —
      dependsOn: T001 —
      files: `backend/app/core/screen_access.py`, `backend/tests/test_screen_access_resolver.py`
- [ ] T003 [P] (backend-dev) Three new permission strings (`menu:read`,
      `screen_access:read`, `screen_access:manage`) + `DEFAULT_ROLE_PERMISSIONS`
      wiring in `rbac.py`; verify/update the permission-catalog fixture in
      `test_phase10_security_hardening.py` only if it enumerates all permission
      strings —
      dependsOn: T001 —
      files: `backend/app/core/rbac.py`, `backend/tests/test_phase10_security_hardening.py`
- [ ] T004 [P] (backend-dev) `menu_security` Pydantic schemas
      (`ActionLevelResponse`, `ScreenResponse`, `MenuNode`, `MenuTreeResponse`,
      `ScreenAccessEntry`, `MyScreenAccessResponse`, `ScreenGrantCreate/Update/Response`)
      + access helpers (`get_screen_or_404`, `get_action_level_or_422`,
      `get_grant_or_404`, `get_org_role_or_404`) (FR-1, FR-13, FR-16) —
      dependsOn: T001 —
      files: `backend/app/menu_security/schemas.py`, `backend/app/menu_security/access.py`
- [ ] T005 [P] (frontend-dev) TS interfaces (`MenuNode`, `MenuTreeResponse`,
      `ScreenResponse`, `ActionLevelResponse`, `MyScreenAccessResponse`,
      `ScreenGrantResponse`) + endpoint client additions (`menuApi`, `screensApi`,
      `screenAccessApi`) per the frozen interface contract —
      dependsOn: — —
      files: `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts`
- [ ] T006 [P] (frontend-dev) Pure client-side screen-access matcher + a
      `useScreenAccess`-style hook consuming `MyScreenAccessResponse` for
      control-gating (FR-9) + vitest coverage —
      dependsOn: — —
      files: `frontend/src/lib/screen-access.ts`, `frontend/src/lib/screen-access.test.ts`

### Wave 3 — Parallel build-out (routes/service, chokepoint, pages)

- [ ] T007 [P] (backend-dev) `menu_security/service.py` + `menu_security/routes.py` —
      `get_menu_tree` (bottom-up pruning: screen_link survives iff resolved
      >= VIEW, group survives iff >=1 surviving descendant), `get_my_screen_access`
      (always resolves for the caller only — no `user_id` parameter anywhere,
      structurally preventing cross-user queries), `list_screens`,
      `list_action_levels`, `list_screen_grants`/`create_screen_grant`/
      `update_screen_grant`/`revoke_screen_grant` (org-filtered, audited,
      `_assert_bootstrap_grant_not_weakened` guarding the admin/`screen_access`
      DELETE lock on both PATCH and DELETE); three thin routers
      (`menu_router`, `screens_router`+`action_levels_router`,
      `screen_access_router`); pytest for menu-tree resolution and grant CRUD
      (FR-1, FR-7, FR-8, FR-13 through FR-16, FR-22, FR-23, FR-25) —
      dependsOn: T002, T003, T004 —
      files: `backend/app/menu_security/service.py`, `backend/app/menu_security/routes.py`,
      `backend/tests/test_menu_tree_api.py`, `backend/tests/test_screen_access_grants_api.py`
- [ ] T008 [P] (backend-dev) `require_screen_level(screen_code, min_level)`
      FastAPI dependency added to `core/deps.py`, calling `screen_access`'s
      resolver and raising 403 below the required level. `require_permission`
      itself is untouched (FR-10, FR-11) —
      dependsOn: T002 —
      files: `backend/app/core/deps.py`
- [ ] T009 [P] (frontend-dev) `aegis-rail.tsx` FR-20 migration: delete the
      hardcoded `GROUPS` array and the no-op `can()` filter, replace with a
      recursive `renderNode` over `menuApi.tree()`; keep `svg()`, `isActive()`,
      `RAIL_CSS`, and every class name byte-identical (FR-7, FR-20) —
      dependsOn: T005 —
      files: `frontend/src/components/aegis-rail.tsx`
- [ ] T010 [P] (frontend-dev) `ScreenGuard` component mounted once in
      `src/app/(app)/layout.tsx`, keyed on `usePathname()` → `route_path`,
      redirecting/blocking a deep-linked route the caller has no VIEW-level
      resolved access to (FR-12) —
      dependsOn: T005 —
      files: `frontend/src/components/screen-guard.tsx`, `frontend/src/app/(app)/layout.tsx`
- [ ] T011 [P] (frontend-dev) `/screen-access` admin page: screens/roles/org-unit
      filters, a grants table (`_screen-grants-panel.tsx`) with Revoke
      (`useConfirm`, disabled + `is_locked` messaging for the FR-25 bootstrap
      row), and an assign-grant modal (`_assign-screen-grant-modal.tsx`) with
      role/screen/org-unit pickers and an action-level select (FR-22, FR-23) —
      dependsOn: T005, T006 —
      files: `frontend/src/app/(app)/screen-access/page.tsx`,
      `frontend/src/app/(app)/screen-access/_screen-grants-panel.tsx`,
      `frontend/src/app/(app)/screen-access/_assign-screen-grant-modal.tsx`
- [ ] T012 [P] (frontend-dev) FR-9 control gating on the five tranche-1 pages —
      Add/Edit/Delete buttons hidden/disabled per the resolved action level from
      `useScreenAccess()`, never the only enforcement (server is authoritative) —
      dependsOn: T006 —
      files: `frontend/src/app/(app)/contracts/page.tsx`,
      `frontend/src/app/(app)/matters/page.tsx`,
      `frontend/src/app/(app)/trademarks/page.tsx`,
      `frontend/src/app/(app)/notices/page.tsx`,
      `frontend/src/app/(app)/intake/page.tsx`

### Wave 4 — Parallel tranche-1 retrofit + integration

- [ ] T013 [P] (backend-dev) Add `Depends(require_screen_level("contracts", <level>))`
      to all 6 ADD/EDIT/DELETE routes in the contracts domain, per plan.md's
      route-by-route list (FR-10, FR-11, FR-17) —
      dependsOn: T008 —
      files: `backend/app/contracts/routes.py`
- [ ] T014 [P] (backend-dev) Same retrofit for the 14 ADD/EDIT/DELETE routes in
      the matters domain (covers both the `/matters` and legacy `/projects`
      mount, one router) (FR-10, FR-11, FR-17) —
      dependsOn: T008 —
      files: `backend/app/matters/routes.py`
- [ ] T015 [P] (backend-dev) Same retrofit for the 8 ADD/EDIT/DELETE routes in
      the trademarks domain (FR-10, FR-11, FR-17) —
      dependsOn: T008 —
      files: `backend/app/trademarks/routes.py`
- [ ] T016 [P] (backend-dev) Same retrofit for the 11 ADD/EDIT/DELETE routes in
      the notices domain (FR-10, FR-11, FR-17) —
      dependsOn: T008 —
      files: `backend/app/notices/routes.py`
- [ ] T017 [P] (backend-dev) Same retrofit for the 20 ADD/EDIT/DELETE routes in
      the intake domain, excluding unauthenticated webhooks and
      `admin_panel:access`-gated config endpoints per plan.md's exclusion list
      (FR-10, FR-11, FR-17) —
      dependsOn: T008 —
      files: `backend/app/intake/routes.py`
- [ ] T018 (backend-dev) Register the three new routers (`menu_router`,
      `screens_router`+`action_levels_router`, `screen_access_router`) in
      `main.py` immediately after `delegations_router` —
      dependsOn: T007 —
      files: `backend/app/main.py`

### Wave 5 — Cross-cutting consistency test

- [ ] T019 (backend-dev) Integration test suite proving, for representative
      user/screen/action combinations across all five tranche-1 domains, that
      menu-tree visibility, frontend button-rendering data (resolved action
      level), and actual API accept/reject behavior all AGREE — the specific
      three-way consistency check the security spec calls out as the one thing
      a narrower test can miss (FR-7, FR-9, FR-10, FR-11, FR-12) —
      dependsOn: T013, T014, T015, T016, T017, T018 —
      files: `backend/tests/test_screen_level_enforcement.py`

### Wave 6 — QA

- [ ] T020 (qa-engineer) Verify all 28 FRs and all 17 ACs against the running
      stack; write `verification.md`. AC-16 (migration backfill preserves
      day-one access) has no pytest fixture path — document and execute it as a
      manual `docker compose` procedure, same pattern as feature 002's AC-9.
      AC-4 and AC-9 (structural/source-inspection criteria per plan.md) are
      recorded via direct code inspection, not a test run —
      dependsOn: T007, T009, T010, T011, T012, T018, T019 —
      files: `specs/003-menu-screen-security/verification.md`

## Task detail notes

- **T001** seeds the menu tree to mirror today's live `aegis-rail.tsx` `GROUPS`
  array exactly (label, icon, order) — read the actual current file before
  writing seed data, don't reconstruct it from memory of the plan's summary.
  The FR-26 backfill logic (mapping today's permission-string access to a
  seeded action level per role/screen) must run inside this same revision, not
  a follow-up script — CI applies migrations once.
- **T002** is the second-highest-risk task in the feature (after T001) — the
  resolver must reuse `org_access.ancestor_unit_ids`/`active_grants_for_user`
  by import, never reimplement the ancestry walk. A regression test asserting
  `core/org_access.py` is unmodified by this feature is worth including.
- **T007**'s `_assert_bootstrap_grant_not_weakened` is the FR-25 security
  boundary — it must be reachable from both the PATCH and DELETE grant
  endpoints through one shared code path, not duplicated logic in each route.
- **T009** is a visual-regression-risk task — the rail's exact CSS/markup must
  be preserved; only the data source and the now-real filtering change. A
  before/after screenshot comparison is worth doing manually even though it
  isn't part of the automated gate.
- **T013–T017** are five independent, file-disjoint tasks — safe to launch
  together as a single wave-4 batch across all five domains.
- **T019** depends on all five retrofits AND the router registration (T018)
  because it needs a live, fully-wired app to exercise the API layer
  end-to-end alongside the menu-tree and client-gating logic.
