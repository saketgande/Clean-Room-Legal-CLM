# Verification Report: Menu/Screen-Level Security (003-menu-screen-security)

QA agent: qa-engineer · Date: 2026-09-14 · Task: T020 (Wave 6) · Amended: 2026-09-14 (post T021/T022)

## Overall verdict

**AMENDMENT (2026-09-14): both defects below are now CLOSED.** T021
(db-engineer) fixed all 12 ruff findings in
`alembic/versions/0043_menu_screen_security.py`; T022 (frontend-dev) added
the missing per-row action-level `Select` to `_screen-grants-panel.tsx`,
closing FR-22/FR-23's UI gap. Both fixes were independently re-verified by
the orchestrator (not just self-reported by the fixing agent). See the
"Defects / gaps" section below for full before/after evidence per defect.

**Final verdict: FULLY VERIFIED — DONE, zero open defects.** Restated final
gate numbers after both fixes: backend `pytest -q` → **362 passed, 6
skipped** (unchanged — the fixes were lint/UI-only, no test-affecting logic
changed); `alembic heads` → single head (`0043_menu_screen_security`),
confirmed via a downgrade/upgrade round-trip that also confirmed seed-data
byte-identity (SHA-256 hash over all menu_item label/icon pairs unchanged,
row counts 39 screens / 4 action levels / 28 menu items / 107
role_screen_access unchanged); `ruff check .` on this feature's own files,
**now including the migration**, is clean (0 findings on
`alembic/versions/0043_menu_screen_security.py`, was 12); repo-wide `ruff
check . --ignore EXE002` is now **84 findings**, matching the pre-existing
feature-002-era baseline exactly (confirms the 12 fixed findings all
belonged to this feature and zero pre-existing debt was touched, and zero
new debt was introduced); frontend `npm run typecheck` clean; `npm run test`
→ **4 files, 31/31 passed**, unchanged.

The FR-10 API-layer security boundary was already solid at the original
pass and remains so — this amendment closes the two peripheral gaps (a
CI-lint hygiene issue in a migration file, and an admin-UI functional gap
vs. the frozen plan) that kept the original verdict at "DONE, with gaps"
rather than a clean pass. Informational, non-blocking items from the
original pass (trademarks menu-item gap, no drift-guard test for
ScreenGuard's route list) remain informational — see their own subsection
below, unchanged.

## Gate results (executed, not self-reported)

### Backend (`docker compose exec backend`, from `backend/`)

| Gate | Command | Result |
|---|---|---|
| Lint (feature files only) | `python -m ruff check . --ignore EXE002` | Original pass: 96 findings repo-wide; 0 attributable to `app/models.py`; 12 NEW findings in `alembic/versions/0043_menu_screen_security.py` (see defect #1, now RESOLVED). **Post-T021: 0 findings on `0043_menu_screen_security.py`; repo-wide 84 findings, matching the pre-existing feature-002-era baseline exactly** — independently re-verified by the orchestrator. |
| Lint (repo-wide, matches CI exactly) | `python -m ruff check .` (no ignore) | 430 findings — the extra 334 vs. the `--ignore EXE002` run are all `EXE002` ("file executable but no shebang"), confirmed a Windows-Docker-bind-mount file-permission artifact (not present in a real Linux CI checkout; same technique used by feature 002's own QA pass) |
| Migration | `alembic upgrade head` | clean, no errors |
| Single head | `alembic heads` | `0043_menu_screen_security (head)` — exactly one head, confirmed |
| Tests | `python -m pytest -q` | **362 passed, 6 skipped**, 0 failed (matches board.md's claim exactly) |

**Ruff scoping methodology** (per instructions, mirroring feature 002's QA
technique): `app/models.py` shows 2 findings (`I001` import-block-unsorted at
line 1, `RUF022` `__all__`-not-sorted at line 76). I extracted the file as it
existed at `HEAD` (before this feature's 10-line diff) and ran ruff against
that base version in isolation — **the base version already has the
identical 2 findings**, confirming these are pre-existing and NOT introduced
by this feature's edit (the feature's diff only appends 4 import names + 4
`__all__` entries without altering the file's existing sort order).
`alembic/versions/0043_menu_screen_security.py` is a **brand-new file** (not
present before this feature), so every finding on it is unambiguously new —
see defect #1.

### Frontend (from `frontend/`)

| Gate | Command | Result |
|---|---|---|
| Typecheck | `rm -rf .next && npm run typecheck` | clean, 0 errors |
| Tests | `npm run test` | **4 files, 31/31 passed** (`contract-blocks.test.ts`, `org-tree.test.ts`, `screen-access.test.ts` — 11 tests, `markdown.test.tsx`) |

`npm run lint` intentionally not run per instructions.

## FR-by-FR verification

| FR | Verdict | Evidence |
|---|---|---|
| FR-1 | PASS | `screen` table UNIQUE on `route_path`; DB query confirms 39 rows |
| FR-2 | PASS | `menu_item` table w/ `parent_id` self-FK, `menu_type` CHECK, `ck_menu_item_screen_link`; DB confirms 28 rows |
| FR-3 | PASS | Screens with no menu node exist and resolve (e.g. `trademark_detail`, `assistant`); `ScreenGuard` gates by pathname, not by menu presence — confirmed by source read of `screen-guard.tsx` |
| FR-4 | PASS | `action_level` CHECK + rank 1-4 UNIQUE (DB confirms 4 rows); `test_screen_access_resolver.py`'s rank-implication cases pass |
| FR-5 | PASS | `role_screen_access.max_action_level_id` is a single FK — non-contiguous levels structurally unrepresentable |
| FR-6 | PASS | `resolve_screen_access`/`resolve_all_screen_access` is the only implementation; `test_resolve_all_agrees_with_per_screen_resolve` passes |
| FR-7 | PASS | `test_menu_tree_includes_screen_link_only_when_resolved_view_or_above` passes; confirmed by source read of `service.get_menu_tree` |
| FR-8 | PASS | `test_menu_tree_group_absent_when_no_descendant_survives` + `test_menu_tree_nested_group_prunes_recursively` pass |
| FR-9 | PASS | `GET /screen-access/me` + `useScreenAccess()` hook (read: `frontend/src/lib/screen-access.ts`) + confirmed wired into all 5 tranche pages (`grep` spot-check) |
| FR-10 | PASS | `require_screen_level`→`assert_screen_level`→`resolve_screen_access`, re-resolved per request from DB; `test_screen_level_enforcement.py`'s literal-dependency-chain tests call the actual `Depends(...)` objects, not a re-implementation |
| FR-11 | PASS | AC-4 source inspection (below) — every tranche route carries `_screen=Depends(_XXX_LEVEL)` |
| FR-12 | PASS | `test_permission_held_but_screen_level_missing_is_rejected` / `test_screen_level_held_but_permission_missing_is_rejected` both pass — both gates independently required |
| FR-13 | PASS | Frontend gating is UI-only (`useScreenAccess`); server-side raw-dependency tests prove the boundary holds regardless of UI state |
| FR-14 | PASS | `ScreenGuard` mounted once in `(app)/layout.tsx` (confirmed by source read); blocks any known screen route absent from `/screen-access/me`'s entries |
| FR-15 | PASS | One resolver; `test_resolve_all_agrees_with_per_screen_resolve`; `test_three_way_agreement_when_access_is_{sufficient,insufficient}` (AC-6) directly exercises menu+`/me`+API together for all 5 tranche domains |
| FR-16 | PASS | All 39 screens seeded (DB-confirmed count); resolver operates over the full catalog regardless of tranche membership |
| FR-17 | PASS | 6+14+8+11+20 = 59 tranche routes retrofitted per T013-T017's board entries; spot-checked `contracts/routes.py`'s 6 listed routes via grep — all present |
| FR-18 | PASS | `resolve_screen_access` imports and calls `org_access.ancestor_unit_ids`/`active_grants_for_user` (confirmed no reimplementation, see "org_access.py non-modification" below); `test_grant_at_global_rolls_up_to_a_descendant_unit`, `test_no_rollup_role_is_locked_to_its_exact_granted_unit` pass |
| FR-19 | PASS | `test_highest_level_wins_narrower_grant_never_reduces` — read in full, asserts DELETE (global) survives over a narrower VIEW (entity_1) at a descendant context |
| FR-20 | PASS | `aegis-rail.tsx` source read in full — no `GROUPS` array, no `Item` type, no `can()` filter; sole data source is `useQuery(["menu-tree"], () => menuApi.tree())` |
| FR-21 | PASS | `staleTime: 60_000` on the menu-tree query; resolver reads live DB state on every resolution — no deploy required for a grant change to surface |
| FR-22 | **PASS** (was PARTIAL, closed by T022) | POST/PATCH/DELETE `/screen-access/grants` all exist, are org-filtered, and are pytest-covered (`create`/`update`/`revoke` happy paths + cross-org 404s all pass) — the API-level capability was always complete. The UI gap is now also closed — see defect #2 (RESOLVED). |
| FR-23 | **PASS** (was PARTIAL, closed by T022) | View + assign + revoke + **modify** are all now present and functional in `/screen-access`. T022 added a per-row action-level `Select` to `_screen-grants-panel.tsx` (enabled when the actor has `screen_access:manage` and the grant is active) that calls `screenAccessApi.updateGrant(id, { action_level })`, invalidates the grants list on success, and toasts on error; disabled with adapted lock messaging when `grant.is_locked` (FR-25). `npm run typecheck` clean, `npm run test` 4 files/31 passed. See defect #2 (RESOLVED). |
| FR-24 | PASS | No menu/screen CRUD endpoints exist (only GET `/screens`, GET `/action-levels`); `screen`/`action_level`/`menu_item` are seeded exclusively by migration 0043 |
| FR-25 | PASS | DB query confirms `admin`/`screen_access`/`DELETE` row exists; `test_bootstrap_admin_grant_cannot_be_lowered_below_delete_via_patch`, `test_bootstrap_admin_grant_cannot_be_revoked_via_delete`, `test_bootstrap_lock_is_a_single_shared_predicate_for_patch_and_delete` all pass; UI lock confirmed by source read of `_screen-grants-panel.tsx` (Revoke `Button` `disabled={g.is_locked}` with visible "Locked" badge + tooltip) |
| FR-26 | PASS | See "AC-16" below — manual DB query confirms exact backfill correctness for 2 screens x 4 roles (8 role/screen combinations spanning DELETE/VIEW/no-grant outcomes) |
| FR-27 | PASS | `test_update_screen_grant_raises_the_level_and_audits_before_after` read in full — asserts `audit.before == {"max_action_level": "VIEW"}`, `audit.after == {"max_action_level": "EDIT"}`, actor is `org.admin_user` |
| FR-28 | PASS | `test_denied_screen_level_check_writes_an_access_denied_audit_row` read in full — asserts `action == "access.denied"`, `actor_user_id`, `resource_type == "screen"`, `metadata_json["outcome"] == "denied"`, `metadata_json["permission"] == "screen:contracts:EDIT"`, `metadata_json["reason"]` present |

## AC-by-AC verification

| AC | Verdict | Evidence |
|---|---|---|
| AC-1 | PASS | `test_screen_access_resolver.py` rank-implication cases (VIEW<ADD<EDIT<DELETE) pass |
| AC-2 | PASS | `test_menu_tree_includes_screen_link_only_when_resolved_view_or_above` + `test_menu_tree_group_absent_when_no_descendant_survives` pass |
| AC-3 | PASS | `test_three_way_agreement_when_access_is_insufficient` (all 5 domains) — read in full, the "insufficient" branch calls the literal `_XXX_EDIT` dependency object and asserts `HTTPException(403)` |
| AC-4 | PASS (source inspection) | Grepped all 5 tranche `routes.py` files for `require_screen_level`/`_screen=Depends(...)` constants — present on every listed route per plan.md's route tables (T013-T017 board entries corroborated by spot-check of `contracts/routes.py`) |
| AC-5 | PASS | `test_permission_held_but_screen_level_missing_is_rejected` + `test_screen_level_held_but_permission_missing_is_rejected` — both read in full, both directions independently rejected |
| AC-6 | PASS | `test_three_way_agreement_when_access_is_sufficient`/`_insufficient` — read in full; asserts resolver level == menu-tree node's `action_level` == `/screen-access/me` entry's level == actual dependency-chain accept/reject, for the same user/screen/action, for all 5 tranche domains |
| AC-7 | PASS | `test_screen_access_resolver.py` (two-roles VIEW+EDIT → EDIT case present per board.md; consistent with `test_highest_level_wins_narrower_grant_never_reduces`'s pattern) |
| AC-8 | PASS | `test_grant_at_global_rolls_up_to_a_descendant_unit` — read in full: EDIT at parent + narrower context resolves EDIT scoped to a descendant unit |
| AC-9 | PASS | `aegis-rail.tsx` read in full — zero hardcoded nav array, zero `can()` filter; `npm run typecheck` clean |
| AC-10 | PASS (backend + component-logic verification; no live browser click-through performed) | `screen-guard.tsx` read in full — blocks any pathname matching the 39-route catalog that is absent from `/screen-access/me`'s response, renders `Lock`+`EmptyState "Access restricted"`; `ScreenGuard` confirmed mounted once in `(app)/layout.tsx`; the underlying access decision is proven correct by the same `resolve_screen_access`/`get_my_screen_access` pytest coverage FR-7/FR-9 rely on. **Not independently exercised via an actual browser session in this pass** — flagged as a lighter-weight verification method than the plan's suggested manual walkthrough; recommend a follow-up manual click-through if that level of assurance is required. |
| AC-11 | PASS | `test_update_screen_grant_raises_the_level_and_audits_before_after` — read in full, exact before/after assertion |
| AC-12 | PASS | `test_denied_screen_level_check_writes_an_access_denied_audit_row` — read in full |
| AC-13 | PASS | `test_grant_in_a_different_org_never_satisfies_this_users_check` — read in full, org-B org-wide DELETE grant confirmed to never leak to org-A user |
| AC-14 | PASS | `test_create_screen_grant_rejects_role_from_another_org` + `test_create_screen_grant_rejects_org_unit_from_another_org` + `test_list_screen_grants_is_org_filtered` all pass; UI pickers (`_assign-screen-grant-modal.tsx`) are populated exclusively from org-scoped endpoints (`rolesApi.list`, `screensApi.list`, `orgUnitsApi.list`) |
| AC-15 | PASS | `test_bootstrap_admin_grant_cannot_be_lowered_below_delete_via_patch` + `test_bootstrap_admin_grant_cannot_be_revoked_via_delete` + `test_bootstrap_lock_is_a_single_shared_predicate_for_patch_and_delete` all pass — read in full, confirms rejection through both the PATCH and DELETE surfaces from independent actors |
| AC-16 | PASS (manual DB query method — no pytest fixture exists for a pre-migration state, matching feature 002's precedent) | See "AC-16 verification method" below |
| AC-17 | PASS | `test_non_tranche_screen_has_no_screen_level_dependency_but_still_resolves` — read in full; AST-parses `renewals_routes` source confirming zero `require_screen_level` reference, then confirms the `renewals` screen still resolves via `resolve_screen_access`/`get_my_screen_access` |

## AC-16 verification method (documented, as required)

No pytest fixture path exists for AC-16 because the suite runs against an
already-upgraded schema (identical constraint feature 002 faced for its own
equivalent AC). Verification method used: **direct SQL query against the
live, already-migrated dev database** (`docker compose exec postgres psql`),
comparing the FR-26 backfill's actual output to what `app/core/rbac.py`'s
`DEFAULT_ROLE_PERMISSIONS` predicts for the default roles seeded into this
dev environment.

Query and result (roles x `contracts`/`matters` screens):

```sql
SELECT r.name, s.code, al.code
FROM role_screen_access rsa
JOIN role r ON r.id = rsa.role_id
JOIN screen s ON s.id = rsa.screen_id
JOIN action_level al ON al.id = rsa.max_action_level_id
WHERE rsa.deleted_at IS NULL AND s.code IN ('contracts','matters')
ORDER BY r.name, s.code;
```

| role | screen | level | expected (from rbac.py's `DEFAULT_ROLE_PERMISSIONS`) |
|---|---|---|---|
| admin | contracts | DELETE | write-shaped (all perms) → DELETE ✓ |
| admin | matters | DELETE | write-shaped (all perms) → DELETE ✓ |
| approver | contracts | VIEW | `contract:read` only, no create/update → VIEW ✓ |
| approver | matters | *(no row)* | no `project:*` permission at all → no grant ✓ |
| legal_reviewer | contracts | DELETE | `contract:update` held → DELETE ✓ |
| legal_reviewer | matters | VIEW | `project:read` only, no update/create/delete/share → VIEW ✓ |
| member | contracts | DELETE | `contract:create`+`contract:update` held → DELETE ✓ |
| member | matters | VIEW | `project:read` only → VIEW ✓ |

All 8 role/screen combinations match the pre-cutover permission model exactly
— the write role (DELETE), read-only role (VIEW), and no-access role (no
row) cases from AC-16's Given/When/Then are all represented and correct.
**Result: PASS.**

## Targeted structural checks

**`org_access.py` non-modification (T002/T003 claim):**
`git status --short backend/app/core/org_access.py` shows it as untracked
(new from feature 002, not yet committed) with **no diff against feature
002's landed state** (grep for `menu_security`/`screen_access`/
`RoleScreenAccess`/`Screen` inside the file returns zero hits). The
regression test `test_org_access_module_is_not_imported_for_writing_only_reading_helpers`
was read in full: it AST-parses `screen_access.py`'s source and asserts (a)
it imports from `app.core.org_access`, (b) it does NOT define
`resolve_access`/`ancestor_unit_ids`/`active_grants_for_user` itself. PASS.

**Trademarks menu-item gap (per T019's flag):**
Confirmed via direct DB query (`SELECT ... FROM menu_item mi LEFT JOIN screen
s ... WHERE s.code LIKE 'trademark%'` → **0 rows**) that no menu node exists
for any trademarks-family screen today. This matches the pre-existing rail
(verified: `aegis-rail.tsx`'s current menu-tree seed, read in `plan.md`'s
"Menu tree seed" table, never had a Trademarks item — the current rail's
`GROUPS` array had no Trademarks link before this feature either). **This is
a genuine, confirmed pre-existing gap in the menu catalog, not a
screen-access bug introduced by this feature.** Informational only, not a
blocking defect. `test_screen_level_enforcement.py`'s
`has_menu_node=False` flag on the `trademarks` parametrize case correctly
accounts for this (verified by reading the test file in full — the
assertions genuinely branch on this flag rather than being tautological).

**FR-25 bootstrap lock — both layers:**
- API layer: 3 pytest cases (above), all read in full, all pass.
- UI layer: `_screen-grants-panel.tsx` read in full — the Revoke `Button` is
  rendered with `disabled={g.is_locked}` and a `Lock` icon + "Locked" label +
  explanatory `title` tooltip when `g.is_locked` is true. Confirmed
  functionally analogous to feature 002's `RoleEditorModal` precedent for
  `allows_hierarchy_rollup`.

## Defects / gaps found

### Defect #1 — `alembic/versions/0043_menu_screen_security.py` fails plain `ruff check .` (12 findings) — **RESOLVED (T021)**

**Status: CLOSED.** T021 (db-engineer) fixed all 12 findings: joined the 11
implicitly-concatenated SVG icon string literals into single literals
(ISC004), and combined the SIM114 `if`/`elif` pair
(`if write_perms is _ALL: ... elif perms & write_perms: ...` →
`if write_perms is _ALL or perms & write_perms: ...`, safe due to `or`
short-circuiting). Verification of the fix:
- `ruff check alembic/versions/0043_menu_screen_security.py --ignore EXE002`
  → **0 findings** (was 12).
- No runtime/seed-data behavior changed: T021 computed a SHA-256 hash over
  all `menu_item` (label, icon) pairs before and after a full `alembic
  downgrade -1` / `alembic upgrade head` round-trip on the edited file — the
  hash matched exactly both times, and row counts (39 screens / 4 action
  levels / 28 menu items / 107 role_screen_access) were unchanged.
- `alembic heads` → single head, unchanged.
- `pytest -q` → 362 passed, 6 skipped, unchanged.
- Independently re-verified by the orchestrator: repo-wide `ruff check .
  --ignore EXE002` dropped from 96 findings (this report's original count)
  to **84**, matching the pre-existing feature-002-era baseline exactly —
  confirming all 12 fixed findings belonged to this feature and zero
  pre-existing debt was touched.

**Original finding detail (for record), severity: low-medium (CI-blocking
lint issue, not a runtime/security bug):**
This is a brand-new file (introduced entirely by this feature's T001), so
every finding on it is unambiguously new, not pre-existing noise. Running
`ruff check alembic/versions/0043_menu_screen_security.py --ignore EXE002`
in isolation:

- 11x `ISC004` ("Unparenthesized implicit string concatenation in
  collection... Did you forget a comma?") — the multi-line SVG `icon` path
  strings in the menu-item seed data are written as adjacent string literals
  without a trailing comma inside tuple/list literals, which ruff flags as a
  likely-missing-comma risk (even though in this case the concatenation is
  intentional and correct).
- 1x `SIM114` ("Combine `if` branches using logical `or` operator") in the
  FR-26 backfill loop (`if write_perms is _ALL: ... elif perms & write_perms:
  ...` could be combined).

None of these are disabled in `backend/pyproject.toml`'s `[tool.ruff.lint]
ignore` list (which only covers `B008`, `BLE001`, `S110`, `S112`,
`DTZ011`). Since CI runs `ruff check .` with no path/rule scoping, **this
file would fail CI's lint gate as committed today.** T001's board.md entry
claims "ruff clean" — that claim does not hold under a full,
non-scoped run; it may have been checked with a narrower ignore list or
before all seed rows were added.

**Recommendation (original, now actioned by T021 as described above):**
either wrap each multi-line SVG-path string group in parentheses (the fix
`ruff --fix` itself suggests, and it will not change runtime behavior —
Python already concatenates adjacent literals identically either way, this
only silences the ambiguity warning) and combine the two `if`/`elif`
branches, or add a narrowly-scoped `# noqa: ISC004` per literal with a
one-line justification per the constitution's "no `# noqa` without
justification" rule. This is a mechanical fix, not a design change — safe
for a follow-up task, does not require re-opening plan.md. **T021 took the
parenthesization + `or`-combine route, as recommended.**

### Defect #2 — FR-22's "modify" grant capability is not reachable from the FR-23 admin screen — **RESOLVED (T022)**

**Status: CLOSED.** T022 (frontend-dev) added the missing per-row
action-level `Select` to `_screen-grants-panel.tsx` per plan.md's original
component design: the level column now renders an inline `Select`
(VIEW/ADD/EDIT/DELETE) in place of the static `Badge` whenever the actor
holds `screen_access:manage` and the grant is active; `onChange` calls
`screenAccessApi.updateGrant(id, { action_level: level })` — the real
payload field name (`action_level`, not `max_action_level` as an earlier
assumption had it) was confirmed against `ScreenGrantUpdate` in
`src/lib/types.ts` before implementation — via a react-query mutation that
invalidates the grants list on success and toasts on error. The FR-25
bootstrap lock is respected: the `Select` is disabled when `grant.is_locked`,
with lock messaging adapted to "...cannot be reduced" alongside the existing
lock icon/label (the Revoke button's own lock UI/copy was left untouched).
Falls back to the original read-only `Badge` when the viewer lacks
`screen_access:manage` or the grant is revoked.

Verification of the fix:
- `rm -rf .next && npm run typecheck` → clean, 0 errors.
- `npm run test` → 4 files, 31/31 passed, unchanged.
- Independently re-verified by the orchestrator: `npm run typecheck` clean.

An administrator can now raise or lower an existing grant's level in place
through the UI without a revoke-and-recreate round-trip, so the PATCH path
now produces the single `screen_access.updated` before/after audit row
FR-27/AC-11 describe (rather than the two separate revoke+grant rows the
gap previously forced), and the FR-25 bootstrap-locked row is correctly
immovable through both the Select and the Revoke button.

**Original finding detail (for record), severity: low (functional
completeness gap, not a security gap — the API half of FR-22 was always
fully implemented, tested, and enforces FR-25 correctly):**
plan.md's Component design section (§"Component design > Frontend", item 4)
explicitly specifies: *"A `Table` of role / screen / org-unit scope / max
level `Badge` / status, with **per-row `Select` (change level →
`screenAccessApi.update`)** and a Revoke `Button` behind `useConfirm`."*

Reading the actual landed `_screen-grants-panel.tsx` in full: the level is
rendered as a static, read-only `Badge` — there is no per-row `Select`, and
no call site anywhere in the codebase invokes `screenAccessApi.updateGrant`
(confirmed by a repo-wide grep: the only occurrence of `updateGrant` in
`frontend/src/` is its own definition in `endpoints.ts`). An administrator
using the `/screen-access` page today can only **create** a new grant or
**revoke** an existing one — there is no way to raise or lower an existing
grant's level in place through the UI. To change a level, an administrator
would have to revoke the existing grant and create a new one, which (a)
produces two separate audit rows (`screen_access.revoked` then
`screen_access.granted`) instead of the single `screen_access.updated`
before/after row FR-27/AC-11 describe, and (b) cannot be done at all for the
FR-25 bootstrap-locked row (whose Revoke button is disabled, and which now
has no raise/lower path in the UI either — though this specific row is meant
to be immovable, so that part is actually correct by construction).

FR-22 itself, read literally, is an API-level requirement and IS satisfied
(the PATCH endpoint exists, is tested, and is org/bootstrap-guarded
correctly — see FR-22's PASS row above). The gap is that FR-23's UI, as
actually built, does not expose that capability, contrary to plan.md's own
frozen component design and contrary to the spirit of FR-23's "so that I can
manage this without engineering involvement" administrator user story for
anything other than a brand-new grant or a full revoke-and-recreate.

**Recommendation (original, now actioned by T022 as described above):** add
the per-row action-level `Select` to `_screen-grants-panel.tsx` per
plan.md's original design (small, scoped frontend follow-up task;
`screenAccessApi.updateGrant` and the backend PATCH endpoint already exist
and are tested, so this is UI-only work).

### Minor/informational item — no automated drift guard for `ScreenGuard`'s route inventory

plan.md's test strategy specifies a vitest case asserting `ALL_SCREEN_ROUTE_PATHS`
(the 39-route catalog list) matches the route paths derivable from
`src/app/(app)/**/page.tsx`, so that a newly added page without a
corresponding `screen` row would fail the frontend test suite. In the
landed implementation, `ALL_SCREEN_ROUTE_PATHS` lives inline in
`screen-guard.tsx` (T010) rather than in `screen-access.ts` (T006) as
plan.md specified — a reasonable adaptation given T006 and T010 ran in the
same wave without a frozen hand-off — but no such drift-guard test exists
anywhere in `frontend/src/lib/screen-access.test.ts` or elsewhere (confirmed
by grep: `ALL_SCREEN_ROUTE_PATHS` has exactly 2 references, both inside
`screen-guard.tsx` itself). This means a future page added without a
matching `screen` row (or catalog entry) will silently fail open in
`ScreenGuard` (unknown routes are allowed through by design) rather than
being caught by CI. Not a defect in current behavior — informational, for a
future hardening task.

## Informational items (not defects)

- **Trademarks has no menu_item row** — confirmed via DB query (0 rows) and
  confirmed as a genuine pre-existing gap in the menu catalog (the current
  rail never had a Trademarks link before this feature), not a
  screen-access bug. `test_screen_level_enforcement.py` correctly accounts
  for this via its `has_menu_node=False` flag on the trademarks case.
- **`EXE002` ruff noise (334 findings)** is a Windows-Docker-bind-mount file
  permission artifact in this local dev environment, not a real CI failure
  mode — confirmed by the instruction to scope around it and by the sheer
  volume (spanning files this feature never touched).
- All five tranche-1 domain regression suites (`test_*contract*`,
  `test_*matter*`/`test_*project*`, `test_*trademark*`, `test_*notice*`,
  `test_*intake*`/`test_flow*`) pass unchanged within the full 362-passed
  run, which is itself evidence the FR-26 backfill map correctly grants
  DELETE-equivalent access to whatever role each of those existing test
  suites authenticates as (per plan.md's own stated regression-gate logic).

## Files read/executed for this verification (for traceability)

- `specs/003-menu-screen-security/{spec.md,plan.md,tasks.md,status/board.md}` (full)
- `backend/app/core/screen_access.py`, `backend/app/core/deps.py` (require_screen_level), `backend/app/core/org_access.py` (grep-scanned)
- `backend/app/menu_security/{models.py,service.py,routes.py,schemas.py,access.py}` (read via test files' imports + targeted greps)
- `backend/alembic/versions/0043_menu_screen_security.py` (ruff-scanned in full)
- `backend/tests/{test_screen_access_resolver.py,test_menu_tree_api.py,test_screen_access_grants_api.py,test_screen_level_enforcement.py,test_phase10_security_hardening.py}` (all read in full or near-full, all executed)
- `frontend/src/components/{aegis-rail.tsx,screen-guard.tsx}` (read in full)
- `frontend/src/app/(app)/layout.tsx` (read in full)
- `frontend/src/app/(app)/screen-access/{page.tsx,_screen-grants-panel.tsx,_assign-screen-grant-modal.tsx}` (read in full)
- `frontend/src/lib/{screen-access.ts,screen-access.test.ts,endpoints.ts (screenAccessApi block)}` (read in full)
- Spot-checked FR-9 gating via grep in `contracts/page.tsx`, `matters/page.tsx`, `notices/page.tsx`
- Live DB queries against the running `postgres` container for AC-16, FR-25, and the trademarks menu-item gap
