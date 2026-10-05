# Manual Testing Findings — 002/003/004 Initiative

Tracked during live manual testing of the deployed app (2026-09-15+). Each finding
gets a fix task once testing is done, batched together.

## Finding 1 — Permission backfill gap (all three features)

**Severity:** High — blocked the app entirely for the existing pre-shipped org.

**What:** `rbac.py`'s `DEFAULT_ROLE_PERMISSIONS`/`ALL_PERMISSIONS` are Python
constants. The actual DB `Permission` rows and role grants are only created/synced
by `bootstrap_roles()` (backend/app/auth/service.py), which runs once, at org
setup (`create_first_admin`). None of features 002/003/004's migrations
(0042/0043/0044) re-ran this sync for organizations that already existed before
each feature shipped — so `org_unit:read`, `delegation:manage`, `menu:read`,
`screen_access:read`, `screen_access:manage`, `approval_chain:read/manage/decide/recalculate`
never got created as `Permission` rows or granted to any pre-existing org's roles.

**Why automated tests missed it:** every pytest fixture creates a *fresh* org via
`bootstrap_roles`, which always picks up the current code's full permission set —
there is no test coverage for "an org that existed before this feature shipped."

**Immediate workaround applied (dev org only, not a real fix):** manually re-ran
`bootstrap_roles(db, org.id)` for the one existing dev org — idempotent, safe,
confirmed via `effective_permission_values` and a full pytest re-run (473 passed,
6 skipped, unchanged).

**Real fix needed:** a proper migration (or a one-off idempotent backfill script
callable in any environment) that, for every existing organization, ensures every
current `ALL_PERMISSIONS` value exists as a `Permission` row and is granted to the
matching default role per `DEFAULT_ROLE_PERMISSIONS` — without clobbering any
custom permission grants an admin may have manually added/removed outside the
defaults (bootstrap_roles today unconditionally overwrites `role.permissions`,
which is fine for a never-customized dev org but could be destructive against a
real production org with hand-tuned role permissions — the real fix should
probably ADD missing permissions rather than blindly reset the whole set).

**Status:** FIXED. Added migration `backend/alembic/versions/0045_backfill_role_permissions.py` — additive-only (never removes an existing grant), backfills any missing `permission`/`role_permission` rows for every existing org's built-in-named roles. Applied and verified: target account's permissions confirmed correct, full pytest suite green (473 passed/6 skipped), downgrade/upgrade round-trip tested, `alembic upgrade head --sql` (offline mode) confirmed safe.

---

(more findings appended below as testing continues)

## Finding 2 — Workflow builder's approval-step config doesn't warn it's superseded

**Severity:** Low — UX/clarity only, not a security or correctness issue.

**What:** `frontend/src/app/(app)/workflow-builder/[id]/_designer.tsx` lets an admin
configure an "approval" step's `approver_role`/`routing_rule_id` fields. Per feature
004's FR-22 reroute, once an org has an active `approval_chain_definition` for the
relevant module, `submit_subject_for_approval` reroutes before ever consulting these
params — so they become silently inert for any workflow step that reaches the
approval stage after cutover. Feature 004 already added exactly this warning to the
legacy Rules Builder (`_rules-builder.tsx`'s `MessageBar`) but missed the equivalent
warning in the workflow builder, which configures the same underlying legacy
routing concept via a different UI surface.

**Real fix needed:** add a similar `warning` `MessageBar` (or inline field-level
note) on the "approval" step type's config panel in `_designer.tsx`, worded for
this context (e.g. "This approver role / routing rule only applies when no
condition-driven chain definition is active for this module — see Approvals →
Condition rules").

**Status:** FIXED. Added an amber warning banner to the "Assignment" tab of an
`approval`-type step in `_designer.tsx`, conditioned on `cur.type === "approval"`,
matching the legacy Rules Builder's wording. `npm run typecheck` clean. Not yet
visually confirmed in a live logged-in session — please check the Assignment tab
of an approval step in the workflow builder.
