# Feature Specification: Org-Unit Hierarchy + Scoped, Hierarchical RBAC Resolver

Feature ID: 002-org-hierarchy-rbac
Created: 2026-09-13
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

Today a tenant (organization) is a single flat unit: a role granted to a user applies
everywhere in that org, with no notion of "this business unit" versus "that region."
This feature introduces an internal hierarchy of org units within an organization
(a single tree per organization, rooted at one "Global" org unit, e.g.
Global -> Region -> Entity -> Business Unit) and changes how a user's role grant is
scoped: a grant is now made at a specific org unit and rolls UP the hierarchy only —
a person granted a role at a parent org unit is deemed to hold it for every
descendant unit, never the reverse. It also introduces temporary, narrowly-bounded
delegation of a user's own eligibility to another user, and consolidates all of
this — hierarchy walk, grant validity, delegation — behind a single shared
access-resolution capability so every module checks access the same way instead of
reimplementing the logic. Basic admin screens to manage the org-unit tree, role
grants, and delegations are part of this feature's deliverable. This is feature 1 of
a larger user management / access-control initiative; menu-and-screen-level
(view/add/edit/delete) security and approval-chain reconciliation are later,
separate features built on top of what this feature establishes.

## User stories

- As an org administrator, I want to define a hierarchy of org units within my
  organization, so that I can grant roles at the right level (e.g. a whole Region)
  instead of repeating the same grant unit-by-unit.
- As an org administrator, I want a role granted at a parent org unit to
  automatically cover every unit beneath it, so that I don't have to manage
  duplicate grants as the org structure grows.
- As an org administrator, I want the option to lock a specific role so it never
  rolls up or down the hierarchy — it applies only to the exact org unit it was
  granted at — so that highly sensitive roles can't unintentionally gain broader
  reach through the hierarchy.
- As an org administrator, I want a user's role grant to have a start and end date
  and to be revocable (soft-deleted), so that temporary assignments and offboarding
  are handled without deleting historical grant records.
- As a manager going on leave, I want to delegate some or all of my role/org-unit
  eligibility to a colleague for a bounded window, so that work doesn't stall while
  I'm away, without ever granting them more than I myself hold.
- As a compliance/security reviewer, I want every access grant, scope change, and
  delegation to be traceable to who made it and when, and to be able to tell from
  the audit trail whether an action was performed natively or under delegation, so
  that I can investigate access questions and prove non-repudiation.
- As any authenticated user acting under someone else's active delegation, I want
  my delegated actions to be clearly attributable as delegated in the record, so
  that neither party can later dispute who actually did what.
- As an org administrator, I want a screen where I can see my organization's
  org-unit tree and create, re-parent, or soft-delete org units on it, so that I
  can manage the structure without needing engineering support.
- As an org administrator, I want a screen where I can assign a role to a user at a
  chosen org-unit scope (with an optional expiry) and revoke an existing grant, so
  that I can manage who has access to what without direct database access.
- As a user (or an org administrator on a user's behalf), I want a screen where I
  can create a delegation of my eligibility to a colleague and revoke it early if
  needed, so that temporary coverage is self-service.
- As a developer building the next feature in this initiative (menu/screen security,
  approval-chain reconciliation), I want one authoritative access-resolution
  capability to call, so that I don't have to reimplement hierarchy/expiry/
  delegation logic per module and risk inconsistent enforcement.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify.

**Org-unit hierarchy**

- FR-1: The system SHALL allow an authorized administrator to define org units
  within their organization, each optionally linked to a single parent org unit,
  forming a tree (no cycles, no org unit as its own ancestor).
- FR-2: The system SHALL allow an authorized administrator to rename an org unit,
  move it to a different parent (re-parent), and soft-delete it, without deleting
  role grants or history that reference it.
- FR-3: The system SHALL prevent a re-parent operation that would introduce a cycle
  (an org unit becoming a descendant of itself).
- FR-4: Each organization SHALL have exactly one root (parentless) org unit,
  representing the top ("Global") of its hierarchy. The system SHALL reject an
  attempt to create a second parentless org unit for an organization that already
  has a root, and SHALL reject an attempt to remove an org unit's parent (making it
  a second root) while another root already exists for that organization.
- FR-5: WHEN an org unit is soft-deleted, the system SHALL automatically re-parent
  each of its direct children to the deleted org unit's former parent, so the tree
  remains a single connected structure with no orphaned subtree. This automatic
  re-parenting SHALL itself be recorded in the audit trail as an explicit change
  (which org units were re-parented, from/to which parent, as a consequence of
  which deletion). Existing role grants scoped to the deleted org unit remain
  intact but are excluded from new access resolutions going forward; grants scoped
  to its (now re-parented) descendants continue to resolve as before, walking up
  through the new parent.

**Role grant scoping and expiry**

- FR-6: The system SHALL allow an authorized administrator to grant a role to a
  user scoped to a specific org unit, with an optional effective-from date and an
  optional effective-until date.
- FR-7: A role SHALL carry a single flag controlling whether grants of that role
  roll up the org-unit hierarchy (default: rolls up) or are locked to exactly the
  org unit at which they were granted (no rollup, even from a parent to itself —
  i.e. the grant only satisfies checks scoped to that exact org unit). Other than
  this flag, the existing role model is unchanged: a role remains defined and
  reusable within a single organization (reusable across that organization's own
  org units, via being grantable at any org unit within it), and is never shared or
  visible across organizations.
- FR-8: WHEN resolving whether a user holds a role at a given org unit, the system
  SHALL grant access if the user holds an active, unexpired, non-revoked grant of
  that role at that exact org unit, OR (if the role allows hierarchy rollup) at any
  ancestor of that org unit. A grant SHALL NEVER satisfy a check scoped to an
  ancestor of the org unit it was granted at (no downward or lateral rollup).
- FR-9: A role-grant's effective-until date and its independent revocation
  (soft-delete) SHALL each independently exclude the grant from resolution — a
  grant past its effective-until date is excluded even if not revoked, and a
  revoked grant is excluded even if its effective-until date has not passed or was
  never set.
- FR-10: The system SHALL allow an authorized administrator to revoke (soft-delete)
  a role grant before its effective-until date, immediately excluding it from
  future access resolutions.
- FR-11: Every hierarchy-and-expiry access resolution (used by this feature and by
  every future consumer) SHALL be performed by one shared, single resolver
  capability. No module may reimplement the ancestry walk, expiry check, or
  soft-delete check independently.

**Migration of existing grants**

- FR-12: WHEN this feature is deployed, every pre-existing role grant (today's
  flat, org-unit-less user-role assignment) SHALL be backfilled to be scoped to its
  organization's root ("Global") org unit, with no effective-until date, so that no
  user loses access as a result of the cutover. Narrowing any of these
  backfilled grants to a more specific org unit is a separate, subsequent
  administrative action (see FR-6/FR-10), not part of the migration itself.

**Delegation**

- FR-13: The system SHALL allow a user (the delegator) to delegate some or all of
  their own current role/org-unit eligibility to exactly one other user (the
  delegate) for a bounded date range, optionally narrowed to a specific role and/or
  a specific org unit.
- FR-14: A delegation's effective access SHALL be the intersection of what the
  delegator actually, currently holds (per FR-8/FR-9) and whatever the delegation
  record narrows it to. A delegation SHALL NEVER grant the delegate more access
  than the delegator currently holds, even if the delegation record's own bounds
  are broader.
- FR-15: A delegation SHALL have a status (active or revoked) in addition to its
  date range. WHEN a delegation is revoked, it SHALL stop granting access
  immediately — there is no grace period.
- FR-16: The system SHALL re-evaluate every delegation fresh at the time of each
  access resolution. Delegated access SHALL NOT be cached or evaluated only at
  creation time.
- FR-17: The system SHALL NOT allow a delegate to re-delegate access they hold only
  by virtue of a delegation (no delegation chains). Each delegation record links
  exactly one delegator to one delegate; the resolver SHALL NOT recurse through
  delegation records.
- FR-18: Only the delegator who created a delegation, or an organization
  administrator, SHALL be permitted to revoke it before its end date. The delegate
  named on a delegation SHALL NOT be permitted to revoke it themselves.

**Audit and non-repudiation**

- FR-19: Every creation, modification, and revocation of an org unit (including the
  automatic re-parenting described in FR-5), a role's hierarchy-rollup flag, a role
  grant, or a delegation SHALL be recorded in the audit trail with the acting user,
  a timestamp, and the nature of the change (what changed, prior and new value
  where applicable).
- FR-20: Every soft-delete (org unit, role grant, or delegation) SHALL record the
  authenticated user who performed it. No code path may soft-delete any of these
  records without an identified actor.
- FR-21: WHEN an action is performed by a user operating under an active
  delegation, the audit trail SHALL record both the user who actually performed
  the action and the user whose eligibility was delegated to them, as two distinct
  fields — never collapsed into a single "acted as" identity.
- FR-22: The system SHALL enforce exclusion of soft-deleted org units, role grants,
  and delegations centrally (a single shared mechanism), not by requiring every
  query author to remember to filter them out.

**Boundaries with existing mechanisms**

- FR-23: This feature's resolver SHALL sit in front of / alongside the existing
  permission-string check (does this role include this permission at all) rather
  than replacing it — a resolved access decision requires both: the role includes
  the required permission, AND the org-unit-scope/expiry/delegation resolution in
  this feature succeeds.
- FR-24: The existing contract-approval/signing authority-limits mechanism
  (value/type/jurisdiction-bounded delegation of authority for contract:approve and
  contract:sign) is a distinct mechanism from the general-purpose delegation
  introduced by this feature and is unaffected by it (see Out of scope).
- FR-25: The existing ethical-wall row-level restriction mechanism continues to
  override every access decision produced by this feature's resolver, including
  for administrators (see Out of scope).

**Admin UI**

- FR-26: The system SHALL provide an administrative screen, scoped to the
  administrator's own organization, to view the org-unit tree and to create,
  re-parent, and soft-delete org units on it, enforcing FR-3 (no cycles) and FR-4
  (single root) at the point of action with a clear rejection message when
  violated.
- FR-27: The system SHALL provide an administrative screen to assign a role to a
  user at a chosen org-unit scope (with optional effective-from/effective-until
  dates) and to revoke an existing role grant, restricted to org units and users
  within the administrator's own organization.
- FR-28: The system SHALL provide a screen for a user to create a delegation of
  their own eligibility (optionally narrowed by role and/or org unit, bounded by a
  date range) and for the delegator or an organization administrator to revoke an
  active delegation, consistent with FR-18 (the delegate has no revoke control for
  a delegation made to them).

## Permissions, scoping & audit

This platform is multi-tenant with role-based access, ethical walls, and an
immutable audit trail. Pin down, per action in this feature:

- **Manage org units** (create, rename, re-parent, soft-delete), via API or the
  admin screen (FR-26): restricted to users holding an administrative permission
  for organization/user-management within their own organization. Org units are
  strictly org-scoped — an administrator may only manage org units belonging to
  their own organization; org units are never visible or referenceable across
  organizations. Audit: every create/rename/re-parent/soft-delete records actor,
  timestamp, org unit affected, and (for re-parent, including the automatic
  re-parenting triggered by a soft-delete per FR-5) the prior and new parent.
- **Configure a role's hierarchy-rollup flag**: restricted to the same
  administrative permission as role management today, org-scoped to the role's own
  organization. Audit: records actor, timestamp, role affected, prior and new flag
  value.
- **Grant / modify / revoke a role grant** (role + org unit + validity window), via
  API or the admin screen (FR-27): restricted to users holding the administrative
  permission for user/role management, org-scoped — an administrator may only
  grant roles scoped to org units within their own organization, to users within
  their own organization. Audit: records actor, timestamp, target user, role, org
  unit, validity window, and for revocation, that it was a revocation (not an
  expiry).
- **Create a delegation**, via API or the admin/self-service screen (FR-28): the
  delegator (delegating their own eligibility) or an administrator with the
  user/role management permission, acting only within the delegator's own
  organization. A delegation may only narrow (role and/or org unit) within what
  the delegator already holds in that organization. Audit: records actor (who
  created the delegation record — may differ from the delegator, e.g. an admin
  creating it on the delegator's behalf), delegator, delegate, role/org-unit
  narrowing, date range, timestamp.
- **Revoke a delegation**: the delegator or an organization administrator only —
  never the delegate (FR-18). Audit: records actor, timestamp, delegation
  affected, and that status changed to revoked.
- **Resolve access** (read-only, used by every permission check across the
  platform): available to the authenticated request pipeline itself, not a
  user-facing action; SHALL always evaluate strictly within the acting user's own
  organization — an org unit, role grant, or delegation belonging to a different
  organization SHALL NEVER be considered. No separate audit entry per resolution
  (resolutions happen on every request), but the underlying mutating action being
  authorized (e.g. approving a contract) records, per FR-21, whether it was
  performed natively or under delegation.

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one
functional requirement.

- AC-1 (FR-3): Given an org unit "Region A" with no parent, when an administrator
  attempts to set "Region A"'s parent to one of its own descendants, then the
  system rejects the change and no cycle is created.
- AC-2 (FR-4): Given an organization that already has a root org unit ("Global"),
  when an administrator attempts to create a new org unit with no parent, or to
  clear an existing org unit's parent, then the system rejects the action because
  the organization already has a root.
- AC-3 (FR-8): Given a role granted to a user at the "Global" org unit with
  hierarchy rollup allowed, when access is resolved for that user scoped to a
  child org unit ("Region A" -> "Entity 1" -> "BU 1"), then access is granted.
- AC-4 (FR-8): Given a role granted to a user at "BU 1" only, when access is
  resolved for that user scoped to "BU 1"'s parent ("Entity 1") or to "Global",
  then access is denied.
- AC-5 (FR-7, FR-8): Given a role with hierarchy rollup disabled granted to a user
  at "Entity 1", when access is resolved for that user scoped to "Entity 1" itself,
  then access is granted; when resolved scoped to a child org unit of "Entity 1",
  then access is denied.
- AC-6 (FR-9): Given a role grant with an effective-until date in the past and no
  revocation, when access is resolved for that grant's scope, then access is
  denied.
- AC-7 (FR-9, FR-10): Given a role grant that has been revoked (soft-deleted) but
  whose effective-until date has not passed or was never set, when access is
  resolved for that grant's scope, then access is denied.
- AC-8 (FR-5): Given an org unit "Entity 1" with children "BU 1" and "BU 2" and a
  parent "Region A", when an administrator soft-deletes "Entity 1", then "BU 1"
  and "BU 2" are automatically re-parented to "Region A", the tree remains
  connected with no orphaned org unit, and the audit trail records the
  re-parenting of "BU 1" and "BU 2" (from "Entity 1" to "Region A") as a
  consequence of the deletion.
- AC-9 (FR-12): Given a pre-existing (pre-migration) flat role grant for a user in
  an organization, when the migration runs, then the grant resolves as scoped to
  that organization's root ("Global") org unit with no effective-until date, and
  the user's access at every org unit in that organization is unchanged from
  before the migration.
- AC-10 (FR-13, FR-14): Given a delegator who holds a role at "Region A" and
  creates a delegation to a delegate narrowed to "Entity 1" (a descendant of
  "Region A"), when access is resolved for the delegate scoped to "Entity 1"
  during the delegation's active window, then access is granted, reflecting the
  intersection of the delegator's actual grant and the delegation's narrowing.
- AC-11 (FR-14): Given a delegation record whose stated org-unit/role narrowing is
  broader than what the delegator actually holds, when access is resolved for the
  delegate, then access is limited to what the delegator actually holds, never
  broader.
- AC-12 (FR-15): Given an active delegation, when the delegator or an
  administrator revokes it, then an access resolution for the delegate performed
  immediately afterward denies access — no grace period.
- AC-13 (FR-15): Given a delegation whose end date has passed, when access is
  resolved for the delegate, then access is denied.
- AC-14 (FR-17): Given a delegate who received access solely via a delegation, when
  that delegate attempts to create a further delegation of that same access to a
  third user, then the system rejects it (no delegation chains).
- AC-15 (FR-18): Given an active delegation naming a delegate, when that delegate
  (not the delegator, not an administrator) attempts to revoke the delegation,
  then the system rejects the attempt; only the delegator or an organization
  administrator can revoke it.
- AC-16 (FR-21): Given a user acting under an active delegation performs an action
  gated by this feature's resolver, when the resulting audit/timeline record is
  inspected, then it shows the acting user and the delegating user as two distinct
  fields, not collapsed into one identity.
- AC-17 (FR-19, FR-20): Given an administrator soft-deletes a role grant, when the
  audit trail is inspected, then it shows the administrator as the actor, a
  timestamp, and that the record was revoked — no soft-delete of any org unit,
  role grant, or delegation is recorded without an identified actor.
- AC-18 (FR-8, organization-scoping): Given two organizations each with their own
  org-unit hierarchy, when an administrator of Organization A attempts to view,
  reference, or grant against an org unit belonging to Organization B, then the
  system denies it — org units, role grants, and delegations never cross
  organization boundaries.
- AC-19 (FR-23): Given a user holds a role at the correct org-unit scope but that
  role's permission set does not include the permission required for the action,
  when access is resolved, then access is denied, confirming the org-unit resolver
  supplements rather than bypasses the existing permission-string check.
- AC-20 (FR-24, FR-25): Given the existing contract-approval authority-limit
  mechanism and the existing ethical-wall mechanism, when this feature ships, then
  neither mechanism's behavior changes, and an ethical-wall denial still overrides
  an otherwise-granted access decision from this feature's resolver.
- AC-21 (FR-26): Given an administrator viewing their organization's org-unit
  tree screen, when they create a new org unit under an existing one, re-parent an
  existing org unit, or soft-delete one, then the tree reflects the change, an
  attempted cycle or second root is rejected with a clear message (per AC-1/AC-2),
  and no org unit from another organization is ever visible on the screen.
- AC-22 (FR-27): Given an administrator on the role-grant management screen, when
  they assign a role to a user at a chosen org unit with an optional expiry date,
  then the grant takes effect immediately per FR-8/FR-9; when they revoke a grant
  from the same screen, then it stops granting access immediately.
- AC-23 (FR-28, FR-18): Given a user on the delegation screen, when they create a
  delegation narrowed to a role and/or org unit they hold, then the delegation
  takes effect per FR-14; when the delegator or an org administrator revokes it
  from the screen, it stops granting access immediately; the delegate sees no
  revoke control for a delegation made to them.

## Out of scope

- Menu-level and screen-level (VIEW/ADD/EDIT/DELETE) security — a later feature in
  this initiative, built on top of this resolver.
- Approval-chain reconciliation — a later feature in this initiative.
- Per-user permission overrides layered on top of role-based access (permissions
  remain role-based only; no per-user exceptions).
- Field-level or row-level data masking — that remains the responsibility of the
  existing ethical-wall mechanism, which is unaffected and continues to override.
- The existing contract-approval/signing authority-limit mechanism (value,
  contract type, jurisdiction, risk-band bounded grants for contract:approve and
  contract:sign) — a distinct, pre-existing mechanism, unaffected and unreplaced by
  this feature's general-purpose delegation.
- Single sign-on / identity-provider integration.
- Rate limiting.
- Any change to how the permission string itself is checked (`has_permission`'s
  core string match) — this feature adds a layer in front of/alongside it, not a
  replacement.
- Delegation chains (a delegate re-delegating received access to a third party).
- Sharing or defining a role across more than one organization — roles remain
  org-scoped exactly as modeled today; only the hierarchy-rollup flag is new.
- Allowing an organization to have more than one root org unit (a forest) — every
  organization has exactly one connected tree.
- Bulk/self-service reorganization tooling beyond the basic admin screens in FR-26
  (e.g. drag-and-drop tree editing, bulk CSV import of org units) — the admin
  screens cover single-item create/re-parent/soft-delete, assign/revoke, and
  create/revoke actions only.

## Edge cases & error behavior

- Every organization has exactly one root ("Global") org unit; attempting to
  create or produce a second root for the same organization is rejected (FR-4,
  AC-2).
- Attempting to grant a role scoped to an org unit that has been soft-deleted
  SHALL be rejected.
- Attempting to create a delegation where the delegator and delegate are the same
  user SHALL be rejected.
- Attempting to create a delegation with an end date before its start date SHALL
  be rejected.
- A user with zero active role grants (all expired, revoked, or never granted)
  SHALL resolve to no access for every scope, distinct from a system error.
- Concurrent modification: if a role grant is revoked at the same moment it is
  being relied upon by an in-flight request, the request SHOULD be resolved using
  a consistent snapshot (either fully before or fully after the revocation), never
  a partially-applied state.
- A delegation narrowed to a role the delegator does not (or no longer) hold at
  all SHALL resolve to no access via that delegation, per the intersection rule
  (FR-14), rather than erroring at delegation-creation time if the delegator held
  it at creation but lost it later.
- Re-parenting an org unit that has active role grants scoped to it or its
  descendants does not alter those grants' org_unit_id; it changes which ancestors
  they roll up through going forward.
- Soft-deleting an org unit whose children exist automatically re-parents those
  children to the deleted unit's former parent (FR-5); soft-deleting the root
  ("Global") org unit itself is rejected, since a root re-parenting to nothing
  would either orphan the entire tree or violate the single-root rule — the root
  can only be soft-deleted once it has no remaining descendants (i.e. as the very
  last org unit in the organization's tree).
- A delegate attempting to revoke a delegation made to them (via API or the
  delegation screen) SHALL be rejected with a clear permission error (FR-18,
  AC-15).

## Open questions

None. All clarifications raised during specification were resolved by the user
prior to approval review; see FR-4, FR-5, FR-7, FR-12, FR-18, and FR-26–FR-28 for
the resulting firm requirements.

---

### Context for planning (non-normative)

The following implementation-adjacent facts from the reviewed source material are
recorded here only to save the architect re-deriving them; they are not
requirements of this spec and carry no normative weight:

- Source ER diagram names the entities `org_units` (self-referencing hierarchy:
  Global -> Region -> Entity -> BU), `roles`, `permissions`, `role_permissions`,
  `user_roles` (with `org_unit_id`, `valid_from`, `valid_to`), `delegations`, with
  a repo-wide audit/soft-delete convention (`created_at/by`, `updated_at/by`,
  `deleted_at/by`) on every table except `workflow_instance_history` (append-only).
- Existing codebase: `organizations` domain (`backend/app/organizations/models.py`)
  is a single flat tenant record with no internal hierarchy today. `backend/app/
  auth/models.py` has `Role`, `Permission`, `role_permission_table`, and a flat
  `user_role_table` (no org-unit scope, no validity window) — this is the table
  the FR-12 migration/backfill applies to. `backend/app/core/rbac.py`'s
  `has_permission()` does the permission-string check this feature's resolver must
  sit alongside, not replace.
- `backend/app/authority/` (`AuthorityGrant`) is a distinct, pre-existing ABAC
  mechanism scoped only to `contract:approve`/`contract:sign` with its own
  value/type/jurisdiction/risk-band limits and its own delegation-like
  `delegated_by_user_id` field — explicitly out of scope, do not conflate with the
  general-purpose `delegations` concept in this feature.
- `backend/app/walls/` (`EthicalWall`/`EthicalWallPrincipal`) is a pre-existing
  row-level access override that overrides every ALLOW including admins —
  explicitly out of scope, must remain untouched and continue to take precedence.
- The security spec's stated threat model for this feature: prevent horizontal
  privilege escalation (a role scoped to one org unit acting on another org unit's
  resources) and prevent silent access changes (scope or grant changing without an
  audit record of who/when).
- The admin screens (FR-26–FR-28) are net-new UI surfaces; today's `roles` domain
  frontend (`rolesApi`) has no org-unit or delegation concepts to extend — these
  are new screens/flows, not modifications of the existing role-management screen's
  behavior for permission assignment.
