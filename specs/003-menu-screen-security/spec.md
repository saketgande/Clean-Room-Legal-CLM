# Feature Specification: Menu/Screen-Level Security (VIEW/ADD/EDIT/DELETE) with Dynamic Menu Visibility

Feature ID: 003-menu-screen-security
Created: 2026-09-14
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

Today, which pages a user can see in the app's navigation is decided by a hardcoded
list of nav items checked against permission strings, and mutating API endpoints do
not independently verify that the caller is allowed to perform that specific action
on that specific page ("screen") beyond whatever permission-string check they
already have. This feature introduces a second, orthogonal axis of access control —
per-screen VIEW/ADD/EDIT/DELETE grants, organized under a navigable menu tree — so
that: (1) a user only ever sees a menu item for a page they can at least view, (2)
add/edit/delete controls on a page reflect what that user is actually allowed to do,
and (3) the API independently re-verifies the caller's screen-level access on every
add/edit/delete request, so a hidden button is never the only thing standing between
a user and an unauthorized write. Menu visibility is a derived property of the same
access resolution the API uses — never a separate, looser rule — and the frontend's
navigation must be built entirely from that resolution rather than a hardcoded list.
This feature builds the full mechanism (data model, shared resolver, API-layer
enforcement point, menu-tree endpoint, frontend migration, and an admin management
screen) and applies the API-layer enforcement to a first tranche of five page
routes, with the remaining existing routes explicitly deferred as a tracked
follow-up (see "Rollout scope" below). This is feature 3 of the larger user
management / access-control initiative, building on feature 002's org-unit
hierarchy and shared access resolver; approval-chain reconciliation (item 4 of that
initiative) is explicitly out of scope here.

## User stories

- As any authenticated user, I want the navigation menu to show only the pages I can
  actually do something on, so that I'm not confused by links that lead to a wall of
  "access denied."
- As any authenticated user, I want the add/edit/delete controls on a page to match
  what I'm actually allowed to do (e.g. a read-only user sees no "Edit" button), so
  that the interface doesn't invite me to attempt actions that will fail.
- As a security reviewer, I want every add/edit/delete API request on a first-wave
  screen to be checked against the caller's screen-level access independently of
  what the UI displayed, so that hiding a button is never mistaken for actual
  enforcement and a devtools/direct-API/replay attempt cannot bypass it.
- As a security reviewer, I want the same access resolution to drive both "is this
  menu item visible" and "is this API request allowed," so that there is never a
  case where a link is shown but the underlying action is refused, or a link is
  hidden while the underlying action remains reachable and unguarded.
- As an organization administrator, I want a screen where I can control which roles
  can view/add/edit/delete each securable page (optionally scoped to part of my
  org's hierarchy), so that I can manage this without engineering involvement.
- As an organization administrator, I want the org-unit scope I already understand
  from managing role grants (feature 002) to work the same way for screen access —
  a grant I make at a parent org unit covers everything beneath it — so that I don't
  have to learn a second, different scoping model.
- As a developer maintaining any existing or future page/endpoint, I want one
  clearly-declared, impossible-to-skip way to require a minimum action level on a
  screen for a route, so that I can't accidentally ship a mutating endpoint with no
  screen-level check.
- As a compliance/security reviewer, I want every change to who can view/add/edit/
  delete on a screen to be traceable to who made it and when, so that access changes
  are never silent.
- As an existing user of the system, I want this feature's cutover to preserve
  exactly the access I have today, so that a security feature shipping does not
  itself become an outage or a silent access change.
- As a platform operator, I want the built-in administrator role's own access to the
  screen-access management screen to be impossible to accidentally lock down, so
  that a misconfiguration can never lock every administrator out of the surface that
  manages this very system.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify.

**Definitions**

- FR-1: A "screen" is exactly one Next.js page route in the application — a single
  entry in the application's route tree, identified by that route's path. Every
  securable page in the product corresponds to exactly one screen record, and every
  screen record corresponds to exactly one page route (a one-to-one mapping).

**Menu and screen structure**

- FR-2: The system SHALL organize the application's navigable areas as a tree of
  menu nodes, where each node is either a grouping node (a label with no directly
  associated page route) or a node linking to exactly one screen (FR-1).
- FR-3: The system SHALL treat every screen as independently securable regardless of
  how a user reaches it — via the menu tree, a direct/deep link, or both. A page
  route reachable only by direct link is subject to the identical access checks as
  one reachable via the menu.
- FR-4: The system SHALL define exactly four action levels per screen in a strict,
  fixed order — VIEW, ADD, EDIT, DELETE — such that holding a given level implies
  holding every level below it (e.g. EDIT implies VIEW and ADD). A state such as
  "VIEW and DELETE but not EDIT" SHALL NOT be representable.
- FR-5: An access grant for a role on a screen SHALL specify a single maximum action
  level for that (role, screen) combination, optionally scoped to a specific org
  unit (see FR-18), from which every level up to and including that maximum is
  held, and every level above it is not.

**Access resolution (the shared capability)**

- FR-6: The system SHALL provide one shared screen-access resolution capability that
  determines, for a given user, org-unit context, and screen, the highest action
  level (if any) that user currently holds. Every consumer of this decision — menu-
  tree generation, API enforcement, and any UI-facing "what can I do here" query —
  SHALL call this same capability and SHALL NOT reimplement or approximate the
  resolution independently.
- FR-7: WHEN a user requests the menu tree, the system SHALL include a screen-linked
  menu node if and only if that user's resolved action level for the linked screen
  is at least VIEW, using the identical resolution as FR-6/FR-10 — never a separate
  or looser visibility rule.
- FR-8: A grouping menu node SHALL be included in a user's menu tree if and only if
  it has at least one descendant node (direct or nested) that is itself included for
  that user; a grouping node with no visible descendants SHALL NOT appear.
- FR-9: The system SHALL provide a way for the frontend to determine, for the screen
  currently being displayed, the user's resolved action level, so that add/edit/
  delete controls can be shown, hidden, or disabled accordingly.

**API enforcement (the actual security boundary)**

- FR-10: Every request that performs an ADD, EDIT, or DELETE action on a
  screen-backed resource SHALL independently re-resolve the authenticated caller's
  current action level for that screen (per FR-6) at request time, and SHALL reject
  the request (403) if the resolved level is below what the action requires. This
  check SHALL NOT rely on, or trust as evidence of authorization, any value
  previously computed by or received from the client (including what the UI did or
  did not display). This requirement applies to the routes in scope per FR-17.
- FR-11: The mechanism used to attach a required screen and minimum action level to
  a route SHALL be declared directly on the route itself (not optional, best-effort,
  or skippable middleware a route author could forget to apply), such that a route
  performing ADD/EDIT/DELETE on a screen-backed resource in scope cannot ship
  without this check being visibly present in its definition.
- FR-12: Screen/action-level access (FR-10) is independent of, and additive to, any
  existing fine-grained permission-string check a route already performs. Neither
  mechanism replaces the other; a route MAY require both a specific permission
  string and a minimum screen action level, and SHALL reject the request if either
  is not satisfied.
- FR-13: UI-layer hiding or disabling of a control based on resolved action level
  (FR-9) is a usability aid only. The system SHALL NEVER treat the absence of a
  visible control, or any other client-supplied signal, as sufficient evidence that
  a request is authorized — FR-10's independent server-side check, where in scope
  per FR-17, is mandatory regardless of UI state.
- FR-14: WHEN a user's resolved action level for a screen is below VIEW, any direct
  or deep-link navigation attempt to that screen SHALL be blocked with a clear
  no-access state, independent of whether the corresponding menu item would have
  been shown.

**Consistency across UI and API**

- FR-15: For a given user, screen, and action, WHERE FR-10's API-layer enforcement
  is in scope for that screen (per FR-17), the menu-visibility decision (FR-7), the
  UI control state (FR-9), and the API accept/reject decision (FR-10) SHALL always
  agree, because all three derive from the same resolution capability (FR-6)
  evaluated with the same inputs. No code path may compute any one of these three
  from a different rule than the others.

**Rollout scope**

- FR-16: The system SHALL define a screen record (FR-1) and include it in the menu
  tree's resolution (FR-6/FR-7/FR-8) for every existing page route in the
  application, not only the routes in the first enforcement tranche (FR-17) — so
  that menu visibility and navigation (FR-7, FR-20) are governed by this feature's
  resolver across the whole application from day one, even for routes whose
  mutating endpoints are not yet independently enforced at the API layer.
- FR-17: WITHIN this feature, the independent API-layer ADD/EDIT/DELETE check
  (FR-10, FR-11, FR-15) SHALL be applied to the following first-tranche page
  routes: **Contracts, Matters, Trademarks, Notices, Intake**. All other existing
  page routes (including but not limited to: admin, playbooks, workflows,
  renewals, signatures, approvals, obligations, other matters-area sub-pages not
  listed above, search, jobs, ai-usage, prompts, sla, tabular-reviews,
  org-structure, delegations) are explicitly OUT OF SCOPE for the FR-10 API-layer
  retrofit in this feature. This is a stated, tracked deferral — not a silent gap —
  and requires a follow-up feature to bring the same ADD/EDIT/DELETE enforcement to
  the remaining routes.

**Org-unit scoping of grants**

- FR-18: A screen-access grant made at a given org unit SHALL cover that org unit
  and every one of its descendant org units, using the identical ancestor-walk
  resolution feature 002 already uses for permission-string grants (a grant at a
  parent org unit is held for every descendant unit, never the reverse). A user's
  resolved action level for a screen at a given org unit SHALL reflect the highest
  action level provided by any grant (for a role the user holds) found at that org
  unit or any of its ancestors.
- FR-19: WHEN more than one grant applies to a user for the same screen — whether
  because the user holds more than one role, or because grants exist at more than
  one org unit along the same ancestor chain — the system SHALL resolve the
  effective action level as the HIGHEST level among all applicable grants. A grant
  at a more specific (descendant) org unit SHALL NEVER reduce or override a higher
  action level already provided by a grant at a broader (ancestor) org unit; only a
  grant providing an equal or higher level than what already applies can affect the
  resolved outcome.

**Frontend migration off the hardcoded nav array**

- FR-20: The frontend's primary navigation SHALL be constructed entirely from the
  menu tree resolved for the current user (FR-7/FR-8), and SHALL NOT contain any
  hardcoded, statically-defined list of navigable items that bypasses this
  resolution — replacing the current hardcoded nav-item array.
- FR-21: WHEN the resolved menu tree changes for a user (e.g. a role's grants are
  modified), the navigation the user sees SHALL reflect the change on their next
  menu-tree fetch (e.g. next page load or refresh) without requiring an
  application deployment.

**Administration of screen-access grants**

- FR-22: The system SHALL allow an authorized administrator to create, modify (raise
  or lower the maximum action level, subject to FR-25), and revoke a role's access
  grant for a screen, optionally scoped to an org unit (FR-18), restricted to roles,
  screens, and org units within the administrator's own organization.
- FR-23: The system SHALL provide an administrative screen, scoped to the
  administrator's own organization, to view existing role-to-screen action-level
  grants, assign a new grant (role, screen, action level, optional org-unit scope),
  and revoke an existing grant — consistent with feature 002's precedent of
  providing admin screens for its own org-unit/role-grant/delegation management.
- FR-24: Menu and screen definitions themselves (the inventory of page routes and
  their organization into the menu tree) are structural application data
  describing what pages exist, maintained as the application is built — they are
  not end-user-editable content. Only the access grants against them (FR-22) are
  administrator-managed.
- FR-25: The built-in administrator role's grant of DELETE-level access to the
  screen-access management admin screen (FR-23) itself SHALL be non-revocable and
  SHALL NOT be reducible below DELETE by any administrator action (via the admin
  screen or otherwise), mirroring feature 002's precedent of locking the built-in
  admin role's critical grants (e.g. its hierarchy-rollup flag) — so a
  misconfiguration can never lock every administrator out of the surface that
  manages this very system.

**Migration / cutover**

- FR-26: On this feature's cutover, for every existing screen (FR-16, i.e. every
  existing page route, not only the first-tranche routes), every existing role
  SHALL be seeded with a role_screen_access grant that preserves that role's
  CURRENT effective access level as of immediately before cutover: a role whose
  existing behavior is equivalent to being able to add, edit, or delete on that
  screen SHALL be seeded at DELETE; a role whose existing behavior is
  read-only-equivalent SHALL be seeded at VIEW; a role with no existing access to
  that screen SHALL receive no grant. This seeding SHALL result in no user's
  effective day-one access changing as a direct result of this feature shipping.
  Any subsequent narrowing or broadening of a seeded grant is a separate,
  deliberate administrative action (FR-22), not part of the migration itself.

**Audit**

- FR-27: Every creation, modification, and revocation of a screen-access grant
  (FR-22) SHALL be recorded in the audit trail with the acting administrator, a
  timestamp, the role and screen affected, the org-unit scope (if any, per FR-18),
  and the prior and new maximum action level.
- FR-28: Every API-layer rejection under FR-10 (an ADD/EDIT/DELETE request denied
  for insufficient screen-level access, on an in-scope screen per FR-17) SHALL be
  recorded on the platform's access-decision audit trail, identifying the acting
  user, the screen, the action attempted, the resolved level, and that the outcome
  was a denial — consistent with how this platform already records
  permission-string denials.

## Permissions, scoping & audit

This platform is multi-tenant with role-based access, ethical walls, and an
immutable audit trail. Pin down, per action in this feature:

- **View the menu tree / a screen's resolved action level** (FR-7, FR-9): available
  to every authenticated user for their own resolution only; a user can never see or
  query another user's resolved access. Strictly scoped to the requesting user's own
  organization — a screen-access grant belonging to a different organization's role
  is never considered. No per-request audit entry (this is a read on every page
  load, like other permission checks), but the underlying mutating action it gates
  is audited per FR-28 when refused and per the action's own domain audit trail when
  allowed and performed.
- **Perform an ADD/EDIT/DELETE action on a screen-backed resource** (FR-10, scoped
  to the FR-17 tranche): gated on the resolved action level for the acting user's
  own organization and org unit (FR-18); a grant scoped to one organization or org
  unit never authorizes access outside that scope, though it does roll up to every
  descendant org unit per FR-18. Every denial is audited per FR-28; every allowed
  action continues to be audited by that domain's own existing mutation audit trail
  (this feature adds a precondition to existing mutations, it does not change what
  they record on success).
- **Create / modify / revoke a screen-access grant** (FR-22): restricted to an
  authorized administrator, acting only on roles, screens, and org units within
  their own organization — an administrator SHALL NEVER grant or view screen access
  for a role belonging to a different organization, and (per FR-25) SHALL NEVER be
  able to reduce the built-in admin role's DELETE-level grant on the screen-access
  management screen itself. Audit: actor, timestamp, role, screen, org-unit scope,
  prior and new maximum action level (FR-27), and for revocation, that it was a
  revocation.
- **Menu/screen structural definitions** (FR-24): not a runtime, user-facing
  administrative action within this feature's scope — maintained as application
  structure. No per-organization scoping applies (the inventory of page routes is
  shared across the platform; only the grants against it are org-scoped).

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one
functional requirement.

- AC-1 (FR-4, FR-5): Given a role granted EDIT-level access to a screen, when that
  role's effective action levels are inspected, then the role is confirmed to hold
  VIEW, ADD, and EDIT, but not DELETE.
- AC-2 (FR-7, FR-8): Given a user whose only grant is VIEW-level access to one
  screen under a grouping menu node with three screen-linked children, when that
  user requests the menu tree, then only the grouping node and the one screen-linked
  child they have VIEW access to appear; the other two children and any grouping
  node with zero visible children elsewhere in the tree are absent.
- AC-3 (FR-10, FR-13): Given a user whose resolved action level for the Contracts
  screen (a first-tranche screen, FR-17) is VIEW only, when that user sends a
  direct API request to perform an ADD, EDIT, or DELETE action on that screen's
  backing resource (bypassing the UI entirely, e.g. via a raw HTTP call), then the
  request is rejected with 403, regardless of whether the corresponding UI control
  was ever rendered or hidden.
- AC-4 (FR-11): Given a newly added mutating route on a first-tranche screen, when
  the route is reviewed, then it is confirmed to declare its required screen and
  minimum action level directly in its definition (not via optional middleware),
  such that omitting the check is visibly absent from the route's declaration, not
  silently missing.
- AC-5 (FR-12): Given a route requiring both a specific permission string and a
  minimum screen action level, when a caller holds the permission string but not
  the required screen action level (or vice versa), then the request is rejected in
  either case.
- AC-6 (FR-15): Given a specific user and a first-tranche screen and action (e.g.
  EDIT on Matters), when the menu-visibility state, the UI control state, and the
  API's actual accept/reject behavior are each independently checked for that
  user/screen/action combination, then all three agree (a test asserting only one
  of the three is insufficient to claim this criterion met).
- AC-7 (FR-19): Given a user holding two roles, one granting VIEW on a screen and
  another granting EDIT on the same screen at the same org-unit scope, when that
  user's effective action level for the screen is resolved, then it resolves to
  EDIT (the highest applicable grant).
- AC-8 (FR-18, FR-19): Given a role granted EDIT on the Trademarks screen at a
  parent org unit ("Region A") and a second, narrower grant of VIEW only on the
  same screen at a child org unit ("BU 1" under "Region A") for the same role, when
  a user holding that role is resolved for the Trademarks screen scoped to "BU 1",
  then the resolved action level is EDIT — the narrower grant never reduces the
  broader ancestor grant's level.
- AC-9 (FR-20): Given the application's rendered sidebar navigation, when its
  source is inspected, then every item traces back to the resolved menu tree
  response for the current user, and no hardcoded nav-item list independent of that
  response is used to render it.
- AC-10 (FR-14): Given a user with no resolved access (below VIEW) to a screen, when
  that user navigates directly to that screen's route by URL, then they see a clear
  no-access state rather than the screen's content, regardless of the menu's
  visibility state for that item.
- AC-11 (FR-27): Given an administrator raises a role's maximum action level on a
  screen from VIEW to EDIT, when the audit trail is inspected, then it shows the
  administrator as actor, a timestamp, the role and screen affected, and the prior
  (VIEW) and new (EDIT) maximum level.
- AC-12 (FR-28): Given a user whose resolved action level for a first-tranche
  screen is below what an attempted ADD/EDIT/DELETE request requires, when the
  request is rejected, then the access-decision audit trail records the acting
  user, the screen, the attempted action, the resolved level, and the denial
  outcome.
- AC-13 (organization scoping): Given two organizations each with their own roles
  and screen-access grants, when a user of Organization A is resolved for a screen,
  then only Organization A's grants for the role(s) that user holds are considered
  — a grant belonging to Organization B is never applied.
- AC-14 (FR-23): Given an administrator on the screen-access management admin
  screen, when they assign a new role→screen→action-level grant (optionally scoped
  to an org unit) or revoke an existing one, then the change takes effect on the
  next resolution for affected users per FR-22/FR-27, and no role/screen/org unit
  from another organization is ever visible on the screen.
- AC-15 (FR-25): Given the built-in administrator role's DELETE-level grant on the
  screen-access management admin screen, when any administrator (including another
  administrator, or an attempt via the admin screen itself or directly via the
  underlying grant-management action) attempts to revoke it or reduce it below
  DELETE, then the system rejects the attempt.
- AC-16 (FR-26): Given a pre-cutover role whose current behavior is equivalent to
  add/edit/delete access on a given screen, a second pre-cutover role equivalent
  to read-only access on that screen, and a third pre-cutover role with no access
  to that screen, when this feature's migration runs, then the first role is seeded
  with a DELETE-level grant, the second with a VIEW-level grant, the third receives
  no grant, and every affected user's day-one effective access is confirmed
  unchanged from immediately before cutover.
- AC-17 (FR-17): Given a page route not on the first-tranche list (e.g. the
  Renewals page), when an ADD/EDIT/DELETE request is sent to its backing endpoint,
  then FR-10's independent screen-level check is not required to be present on
  that endpoint within this feature (its retrofit is explicitly deferred), while
  the route's screen record still exists and its menu-node visibility is still
  governed by FR-7's VIEW-level resolution like every other screen.

## Out of scope

- Approval-chain reconciliation — item 4 of this initiative, a separate future
  feature.
- Field-level or row-level data masking — that remains the responsibility of the
  existing ethical-wall mechanism, which is unaffected by this feature and
  continues to override its decisions exactly as it does for feature 002's
  resolver.
- Any change to feature 002's org-unit hierarchy, delegation, or permission-string
  resolution algorithm itself (`app.core.org_access`, `app.core.rbac.has_permission`)
  — this feature adds a distinct access axis (which action level on which screen)
  that composes with, but does not modify, that existing resolver.
- Per-user overrides of screen access layered on top of role-based grants — screen
  access, like feature 002's permission grants, remains role-based only.
- Single sign-on / identity-provider integration.
- Rate limiting.
- Retrofitting the FR-10 API-layer ADD/EDIT/DELETE check onto any existing page
  route other than the first-tranche five listed in FR-17 (Contracts, Matters,
  Trademarks, Notices, Intake). The remaining routes (admin, playbooks, workflows,
  renewals, signatures, approvals, obligations, other matters-area sub-pages not in
  the tranche, search, jobs, ai-usage, prompts, sla, tabular-reviews, org-structure,
  delegations, and any others not listed in FR-17) keep their current
  permission-string-only enforcement on mutating endpoints and are a stated,
  tracked follow-up for a subsequent feature — not silently dropped.
- Delegation of screen-access grants (feature 002's delegation mechanism is not
  extended to this feature's grants; see FR-18 for the org-unit-rollup behavior
  that IS carried over).

## Edge cases & error behavior

- A screen with zero grants for a user's held role(s) resolves to no access (below
  VIEW) for that user — the screen's menu node is absent and, for a first-tranche
  screen, any direct navigation or API request against it is refused, distinct from
  a system error.
- A grouping menu node whose entire subtree resolves to no access for a user is
  itself absent from that user's menu tree (never shown empty).
- Attempting to create a screen-access grant referencing a role or org unit outside
  the administrator's own organization SHALL be rejected.
- Attempting to configure a grant with an action level outside the four defined
  levels, or a level that does not respect the VIEW<ADD<EDIT<DELETE hierarchy,
  SHALL be rejected as an invalid grant (per FR-4/FR-5, only a single maximum level
  is representable).
- Attempting to revoke or reduce the built-in administrator role's DELETE-level
  grant on the screen-access management admin screen SHALL be rejected regardless
  of who attempts it or through which surface (FR-25).
- A user's resolved action level changing (grant modified or revoked) while they
  have the affected screen open SHALL be reflected on their next resolution
  fetch/action, not necessarily instantaneously mid-session — but any in-flight
  ADD/EDIT/DELETE request on a first-tranche screen SHALL be evaluated against the
  access state at the time the API processes it (FR-10), not the state when the
  page was first loaded.
- A screen reachable only via deep link (no menu node points to it) is still
  subject to the identical VIEW-level gate on direct navigation (FR-3, FR-14) — the
  absence of a menu entry is not a substitute for an access check.
- A page route outside the first-tranche list still gets a screen record and
  participates in menu-tree VIEW-level visibility (FR-16) even though its mutating
  endpoints are not yet independently enforced at the API layer (FR-17) — this is
  an intentional, bounded gap during this feature's rollout, not an inconsistency
  to "fix" by a downstream consumer of this spec.
- Ethical-wall row-level restrictions, where applicable to a screen's underlying
  data, continue to apply and override on top of an otherwise-granted screen action
  level, exactly as they override feature 002's resolver — this feature does not
  change that precedence.

---

### Context for planning (non-normative)

The following implementation-adjacent facts from the reviewed source material and
verified current codebase state are recorded here only to save the architect
re-deriving them; they are not requirements of this spec and carry no normative
weight.

- Source ER diagram names the entities `menus` (self-referencing tree: `parent_id`,
  `label`/`icon`, `sequence_order`, `menu_type` enum `group`|`screen_link`,
  `screen_id` nullable FK set only when `menu_type=screen_link`), `screens` (`id`,
  `code` unique, `name`/`module`, `route_path` — the field this spec's FR-1 ties
  literally to a Next.js page route), `action_levels` (`id`, `code`
  VIEW|ADD|EDIT|DELETE, `rank` 1–4, a small seeded reference table), and
  `role_screen_access` (`role_id` FK, `screen_id` FK, `org_unit_id` FK nullable,
  `max_action_level_id` FK). Full audit/soft-delete convention
  (`created_at/by`, `updated_at/by`, `deleted_at/by`) applies to all four tables per
  the existing project convention.
- `backend/app/core/org_access.py` (feature 002, verified as read, implemented and
  APPROVED) exposes `resolve_access`, `assert_access`, `effective_permission_values`,
  and the `ancestor_unit_ids`/`descendant_unit_ids` ancestor-walk helpers —
  org-unit-scoped, hierarchy-rollup-aware resolution keyed on a *permission
  string*. FR-18/FR-19 require this feature's screen-access resolver to reuse the
  identical ancestor-walk semantics for a different axis (action level on a
  screen, not a permission string); plan.md decides the concrete mechanism (e.g.
  whether it calls `ancestor_unit_ids` directly or composes with it some other
  way) — the *behavior* (rollup + highest-level-wins) is now a fixed requirement,
  not an open design question.
- `backend/app/core/deps.py`'s `require_permission(permission)` is the existing
  permission-string route dependency, verified unaffected by and orthogonal to this
  feature (FR-12) — a route may need both `require_permission(...)` and a
  screen/action-level dependency.
- `frontend/src/components/aegis-rail.tsx` was read and confirmed to contain a
  hardcoded `GROUPS`/`Item[]` nav array (`{href, label, icon, perm}` literals)
  filtered client-side via `can(user, it.perm)` — this is the concrete artifact
  FR-20/FR-21 require migrating to consume a menu-tree endpoint instead; today's
  `perm`-string gate is exactly the "separate, looser visibility rule" the source
  security spec warns against once this feature ships alongside it unmigrated.
- The security spec source material recommends the API-layer check be implemented
  as a FastAPI dependency parameterized by screen code and minimum action level
  (e.g. `Depends(require_screen_level(screen_code, min_level))`), analogous in
  spirit to `require_permission`, so the requirement is declared in the route
  signature and cannot be silently omitted (informs FR-11, left as a mechanism
  choice for plan.md).
- First-tranche domains (FR-17) map to these existing backend/frontend domains as
  currently named in the repo: `contracts`, `matters`, `trademarks`, `notices`,
  `intake`. The architect should confirm exact route/domain boundaries against
  `specs/_graph/` when planning which mutating endpoints within each domain are
  screen-backed and in scope.
- feature 002's precedent for a non-revocable built-in-role setting (informing
  FR-25): `frontend/src/app/(app)/admin/page.tsx`'s `RoleEditorModal` disables the
  `allows_hierarchy_rollup` checkbox (read-only) when editing the built-in `admin`
  role, per task T013 of that feature — FR-25 asks for the analogous lock on the
  built-in admin role's grant for this feature's own admin screen.
- The codebase currently has roughly 36 backend domains and ~330 endpoints; only
  the five FR-17 tranche-1 domains get the FR-10 API-layer retrofit in this
  feature — the remainder is a stated follow-up, not silently dropped (see "Out of
  scope").
