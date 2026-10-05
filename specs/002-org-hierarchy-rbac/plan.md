# Implementation Plan: Org-Unit Hierarchy + Scoped, Hierarchical RBAC Resolver

Feature ID: 002-org-hierarchy-rbac
Spec: ./spec.md (must be APPROVED)
Created: 2026-09-13
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

One new backend domain, one new shared core module, two new frontend pages, one
Alembic revision.

**New domain `backend/app/org_structure/`** — mirrors the shape of
`backend/app/walls/` (small, admin-gated, audit-logged CRUD: thin `routes.py`,
all logic in `service.py`, Pydantic in `schemas.py`, SQLAlchemy in `models.py`,
scoping helpers in `access.py`). It owns two new tables (`org_unit`,
`delegation`) and the service logic for the third (`user_role`, reshaped).

**The shared resolver lives in `backend/app/core/org_access.py`** — NOT in the
domain. Rationale: FR-11 requires one resolver that every module (including
later features and `core/deps.require_permission` itself) calls; putting it in
a feature domain would make `app/core` depend on a feature package. It sits
alongside `core/rbac.has_permission` (which is unchanged and is still the thing
that answers "does this role include this permission at all") and is consumed
by `core/deps.require_permission`.

**Reshaped existing table**: `user_role` stops being a plain association Table
and becomes a fully mapped model `UserRoleGrant` (still in
`backend/app/auth/models.py`, still named `user_role`), gaining its own `id`
PK, `org_id`, `org_unit_id`, `valid_from`, `valid_to`, actor-tracking and
soft-delete columns. `user_role_table = UserRoleGrant.__table__` is kept as a
module-level alias so the existing readers in `app/roles/service.py`,
`app/auth/models.py` (`User.roles`) and elsewhere keep compiling.

**Untouched by design**: `app/walls/` (ethical walls remain the deny-override
that runs after and overrides any ALLOW this resolver produces — FR-25/AC-20),
`app/authority/` (`AuthorityGrant` is a separate ABAC mechanism — FR-24),
`core/rbac.has_permission` (its string match is unchanged — FR-23; only the
permission *catalog* constants in that file gain two new strings).

```
request
  └─ core/deps.require_permission("<perm>")            ← existing chokepoint
       ├─ core/org_access.effective_permission_values() ← NEW: drops expired/revoked grants
       └─ core/rbac.has_permission()                    ← UNCHANGED string match
  └─ route → service
       ├─ core/org_access.assert_access(user, perm, org_unit_id)  ← NEW scope/expiry/delegation layer
       └─ walls / contracts access predicates                      ← UNCHANGED deny-override
```

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | `backend/app/org_structure/__init__.py` (new, empty package marker), `backend/app/org_structure/models.py` (new: `OrgUnit`, `Delegation`), `backend/app/auth/models.py` (edit: `UserRoleGrant` mapped model + `user_role_table` alias, `Role.allows_hierarchy_rollup`, `User.roles` secondaryjoin), `backend/app/models.py` (registry imports + `__all__`), `backend/alembic/versions/0042_org_hierarchy_rbac.py` (new) |
| backend-dev | `backend/app/org_structure/routes.py`, `backend/app/org_structure/service.py`, `backend/app/org_structure/schemas.py`, `backend/app/org_structure/access.py`, `backend/app/core/org_access.py` (new resolver), `backend/app/core/deps.py` (edit: `require_permission` uses effective permissions), `backend/app/core/rbac.py` (edit: 2 new permission strings + default role wiring), `backend/app/main.py` (edit: include 3 routers), `backend/app/roles/schemas.py` + `backend/app/roles/service.py` (edit: `allows_hierarchy_rollup` on role read/update; `set_user_roles` writes `UserRoleGrant` rows at the org root), `backend/tests/test_org_access_resolver.py`, `backend/tests/test_org_units_api.py`, `backend/tests/test_role_grants_api.py`, `backend/tests/test_delegations_api.py`, `backend/tests/test_phase10_security_hardening.py` (edit only if a permission-catalog assertion breaks) |
| frontend-dev | `frontend/src/app/(app)/org-structure/page.tsx`, `frontend/src/app/(app)/org-structure/_org-unit-tree.tsx`, `frontend/src/app/(app)/org-structure/_org-unit-modal.tsx`, `frontend/src/app/(app)/org-structure/_role-grants-panel.tsx`, `frontend/src/app/(app)/org-structure/_assign-grant-modal.tsx`, `frontend/src/app/(app)/delegations/page.tsx`, `frontend/src/app/(app)/delegations/_delegation-modal.tsx`, `frontend/src/lib/org-tree.ts` (pure tree helpers), `frontend/src/lib/org-tree.test.ts`, `frontend/src/lib/endpoints.ts` (`orgUnitsApi`, `roleGrantsApi`, `delegationsApi`, `rolesApi.update` payload), `frontend/src/lib/types.ts` (new interfaces + `RoleResponse.allows_hierarchy_rollup`), `frontend/src/components/aegis-rail.tsx` (2 nav items), `frontend/src/app/(app)/admin/page.tsx` (RoleEditorModal: rollup checkbox) |
| qa-engineer | `specs/002-org-hierarchy-rbac/verification.md` |

**Shared-hotspot note** — these files serialize; no two tasks touching the same
one may run in the same wave:

- `backend/app/models.py` — db-engineer registry task only.
- `backend/app/auth/models.py` — db-engineer only (same task as the migration's
  model shape, so migration and model land together).
- `backend/app/core/deps.py`, `backend/app/core/rbac.py`, `backend/app/main.py`
  — one backend-dev task each; do not split.
- `backend/app/roles/service.py` + `backend/app/roles/schemas.py` — one
  backend-dev task (the rollup flag and the `set_user_roles` rewrite touch the
  same functions).
- `frontend/src/lib/endpoints.ts`, `frontend/src/lib/types.ts`,
  `frontend/src/app/(app)/admin/page.tsx`,
  `frontend/src/components/aegis-rail.tsx` — one frontend-dev task each.
- `backend/app/jobs/tasks.py`, `frontend/src/components/ui.tsx` — **not touched
  by this feature.**

## Interface freeze (contract-first — fixed before implementation starts)

All paths are under the API prefix `/api/v1` (the prefix `main.py` mounts
routers with). All timestamps are ISO-8601 strings with timezone in JSON and
`timestamptz` in the DB. `ID` = string UUID.

### New permission strings

Two new strings are added to `app/core/rbac.py` (`ALL_PERMISSIONS` via a new
`ORG_STRUCTURE_PERMISSIONS` set, plus `DEFAULT_ROLE_PERMISSIONS` wiring). Every
other permission used below already exists.

| Permission | Meaning | Default roles granted |
|---|---|---|
| `org_unit:read` | View the org-unit tree (needed by every picker, incl. the self-service delegation screen) | admin, member, legal_reviewer, approver |
| `delegation:manage` | Create/list/revoke one's **own** delegations | admin, member, legal_reviewer, approver |

Reused: `admin_panel:access` (org-unit mutations, role rollup flag, acting on
another user's delegation, `direction=all`), `user:update_role` (role-grant
assign/revoke), `user:read` (role-grant listing).

### API contract

| Method | Path | Permission | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/org-units` | `org_unit:read` | — (query: `include_deleted=false`) | 200 `OrgUnitResponse[]` | 403 |
| POST | `/org-units` | `admin_panel:access` | `OrgUnitCreate` | 201 `OrgUnitResponse` | 403, 404 (parent), 409 (second root), 422 (blank name) |
| PATCH | `/org-units/{org_unit_id}` | `admin_panel:access` | `OrgUnitUpdate` | 200 `OrgUnitResponse` | 403, 404, 409 (cycle / second root), 422 |
| DELETE | `/org-units/{org_unit_id}` | `admin_panel:access` | — | 200 `OrgUnitDeleteResponse` | 403, 404, 409 (root with descendants / already deleted) |
| GET | `/role-grants` | `user:read` | — (query: `user_id?`, `org_unit_id?`, `include_revoked=false`) | 200 `RoleGrantResponse[]` | 403 |
| POST | `/role-grants` | `user:update_role` | `RoleGrantCreate` | 201 `RoleGrantResponse` | 403 (escalation), 404 (user/role/unit), 409 (duplicate active grant), 422 (dates, deleted unit) |
| DELETE | `/role-grants/{grant_id}` | `user:update_role` | — | 204 no body | 403, 404, 409 (already revoked) |
| GET | `/delegations` | `delegation:manage` | — (query: `direction=mine\|received\|all`, default `mine`) | 200 `DelegationResponse[]` | 403 (`all` without `admin_panel:access`) |
| GET | `/delegations/eligibility` | `delegation:manage` | — (query: `delegator_user_id?`) | 200 `DelegationEligibilityEntry[]` | 403 (other user without `admin_panel:access`), 404 |
| POST | `/delegations` | `delegation:manage` | `DelegationCreate` | 201 `DelegationResponse` | 403 (not own + not admin / re-delegation / delegator lacks the narrowed access), 404, 422 (same user, end<start) |
| POST | `/delegations/{delegation_id}/revoke` | `delegation:manage` | — | 200 `DelegationResponse` | 403 (delegate or third party), 404, 409 (already revoked) |

Full request/response JSON shapes:

```jsonc
// ---------- Org units ----------
// OrgUnitResponse
{
  "id": "0f2b…",
  "org_id": "a91c…",
  "name": "Region A",
  "parent_id": "c001…",          // null for the root
  "is_root": false,
  "depth": 1,                     // root = 0
  "path_names": ["Global", "Region A"],
  "child_count": 2,               // direct, non-deleted children
  "active_grant_count": 7,        // non-revoked, unexpired grants scoped here
  "deleted_at": null,
  "created_at": "2026-09-13T10:00:00+00:00",
  "updated_at": "2026-09-13T10:00:00+00:00"
}

// POST /api/v1/org-units  request (OrgUnitCreate)
{ "name": "Region A", "parent_id": "c001…" }   // parent_id null => attempt to create the root

// PATCH /api/v1/org-units/{id} request (OrgUnitUpdate) — both fields optional;
// an omitted field is unchanged. parent_id: null is an explicit "make root"
// request and is rejected with 409 while another root exists (FR-4).
{ "name": "Region A (EMEA)", "parent_id": "c001…" }

// DELETE /api/v1/org-units/{id} response (OrgUnitDeleteResponse)
{
  "deleted_org_unit_id": "0f2b…",
  "reparented": [
    { "org_unit_id": "bu01…", "name": "BU 1", "from_parent_id": "0f2b…", "to_parent_id": "c001…" },
    { "org_unit_id": "bu02…", "name": "BU 2", "from_parent_id": "0f2b…", "to_parent_id": "c001…" }
  ]
}

// ---------- Role grants ----------
// RoleGrantResponse
{
  "id": "g100…",
  "org_id": "a91c…",
  "user_id": "u001…",
  "user_label": "Dana Reed",
  "role_id": "r001…",
  "role_name": "approver",
  "allows_hierarchy_rollup": true,
  "org_unit_id": "0f2b…",
  "org_unit_name": "Region A",
  "valid_from": null,
  "valid_to": "2026-12-31T23:59:59+00:00",
  "is_active": true,                 // not revoked AND inside its validity window, evaluated now
  "revoked_at": null,                // = deleted_at
  "revoked_by_user_id": null,        // = deleted_by_user_id
  "created_at": "2026-09-13T10:00:00+00:00",
  "created_by_user_id": "u000…"
}

// POST /api/v1/role-grants request (RoleGrantCreate)
{
  "user_id": "u001…",
  "role_id": "r001…",
  "org_unit_id": "0f2b…",
  "valid_from": null,                // optional, null = effective immediately
  "valid_to": "2026-12-31T23:59:59+00:00"   // optional, null = no expiry
}

// ---------- Delegations ----------
// DelegationResponse
{
  "id": "d100…",
  "org_id": "a91c…",
  "delegator_user_id": "u001…",
  "delegator_label": "Dana Reed",
  "delegate_user_id": "u002…",
  "delegate_label": "Sam Ortiz",
  "role_id": null,                   // null = every role the delegator holds
  "role_name": null,
  "org_unit_id": "e001…",            // null = every org unit the delegator holds at
  "org_unit_name": "Entity 1",
  "start_date": "2026-10-01T00:00:00+00:00",
  "end_date": "2026-10-14T23:59:59+00:00",
  "status": "active",                // "active" | "revoked"
  "is_active": true,                 // status active AND inside the window, evaluated now
  "can_revoke": true,                // per the REQUESTING user (FR-18) — drives the UI control
  "revoked_at": null,                // = deleted_at
  "revoked_by_user_id": null,
  "created_at": "2026-09-13T10:00:00+00:00",
  "created_by_user_id": "u001…"      // may differ from delegator (admin acting on their behalf)
}

// POST /api/v1/delegations request (DelegationCreate)
{
  "delegator_user_id": null,         // null = the calling user; a non-null OTHER user requires admin_panel:access
  "delegate_user_id": "u002…",
  "role_id": null,
  "org_unit_id": "e001…",
  "start_date": "2026-10-01T00:00:00+00:00",
  "end_date": "2026-10-14T23:59:59+00:00"
}

// GET /api/v1/delegations/eligibility response (DelegationEligibilityEntry[])
[
  {
    "role_id": "r001…",
    "role_name": "approver",
    "allows_hierarchy_rollup": true,
    "org_unit_id": "0f2b…",
    "org_unit_name": "Region A",
    "valid_to": null
  }
]
```

**Audit actions written by each mutation** (every one via
`write_audit_log(...)` in the same transaction as the change, with
`org_id=actor.org_id`, `actor_user_id=actor.id`, and the delegation metadata
block described under "Delegated-action attribution"):

| Endpoint | `action` | `resource_type` | before / after |
|---|---|---|---|
| POST `/org-units` | `org_unit.created` | `org_unit` | after: `{name, parent_id}` |
| PATCH `/org-units/{id}` (rename) | `org_unit.updated` | `org_unit` | before/after: `{name}` |
| PATCH `/org-units/{id}` (parent change) | `org_unit.reparented` | `org_unit` | before/after: `{parent_id}` |
| DELETE `/org-units/{id}` | `org_unit.deleted` | `org_unit` | before: `{name, parent_id}`; metadata: `{reparented_child_ids: [...]}` |
| DELETE `/org-units/{id}` (one row **per moved child**) | `org_unit.reparented_on_delete` | `org_unit` (the child) | before/after: `{parent_id}`; metadata: `{consequence_of_org_unit_id: "<deleted id>"}` |
| PATCH `/roles/{id}` (rollup flag) | `role.updated` (existing action, payload extended) | `role` | before/after now include `allows_hierarchy_rollup` |
| POST `/role-grants` | `role_grant.created` | `user_role` | after: `{user_id, role_id, org_unit_id, valid_from, valid_to}` |
| DELETE `/role-grants/{id}` | `role_grant.revoked` | `user_role` | before: `{user_id, role_id, org_unit_id, valid_to}`; metadata: `{revocation: true}` (distinguishes revoke from natural expiry — AC-17) |
| POST `/delegations` | `delegation.created` | `delegation` | after: `{delegator_user_id, delegate_user_id, role_id, org_unit_id, start_date, end_date}` |
| POST `/delegations/{id}/revoke` | `delegation.revoked` | `delegation` | before: `{status: "active"}`; after: `{status: "revoked"}` |

### The shared resolver — `backend/app/core/org_access.py`

Single module, single public entry point. No other module reimplements the
ancestry walk, expiry check, soft-delete filter, or delegation intersection
(FR-11, FR-22).

```python
# backend/app/core/org_access.py

@dataclass(frozen=True)
class ResolvedAccess:
    allowed: bool
    reason: str                 # see REASONS below
    role_ids: tuple[str, ...]   # grants that satisfied the check ( () when denied )
    org_unit_id: str | None     # the unit the check was scoped to
    via_delegation_id: str | None = None
    on_behalf_of_user_id: str | None = None   # the delegator, when via_delegation_id is set

REASONS = (
    "native_grant", "delegated_grant",        # allowed
    "no_grant", "role_lacks_permission",      # denied
    "org_unit_not_found",                     # unknown / soft-deleted / other org
)

def resolve_access(
    db: Session,
    *,
    user: User,
    permission: str,
    org_unit_id: str,
    at: datetime | None = None,          # default utcnow(); injectable for tests
    allow_delegated: bool = True,        # False on the recursive delegator pass (FR-17)
    restrict_role_ids: Sequence[str] | None = None,   # delegation role narrowing
) -> ResolvedAccess: ...

def assert_access(db, *, user, permission, org_unit_id, at=None) -> ResolvedAccess:
    """resolve_access, but raises HTTPException(403, "...") and calls
    app.core.authz.record_decision(outcome='denied') when not allowed."""

def effective_permission_values(db: Session, *, user: User, at=None) -> set[str]:
    """The user's permission strings from NON-revoked, UNEXPIRED grants only.
    Mirrors User.permission_values' active_role_id semantics. Called by
    core/deps.require_permission so an expired grant grants nothing anywhere."""

def ancestor_unit_ids(db, *, org_id: str, org_unit_id: str, include_self: bool = True) -> list[str]:
    """Walk parent_id upward. Non-deleted units only. [] if the unit is missing,
    soft-deleted, or belongs to another org. Depth-capped at 64 (cycle guard)."""

def descendant_unit_ids(db, *, org_id: str, org_unit_id: str, include_self: bool = True) -> list[str]:
    """Breadth-first walk downward. Non-deleted units only. Used for cycle
    detection on re-parent and for delegation org-unit narrowing."""

def active_grants_for_user(db, *, user_id: str, org_id: str, at=None) -> list[UserRoleGrant]:
    """The single soft-delete + validity-window filter (FR-9, FR-22)."""

def delegation_audit_metadata(resolved: ResolvedAccess, actor: User) -> dict:
    """{'acting_user_id', 'on_behalf_of_user_id', 'delegation_id'} — the FR-21
    two-field attribution block, merged into every mutation's audit metadata."""
```

`resolve_access` algorithm (exact, so every consumer behaves identically):

1. Load `OrgUnit` by `org_unit_id` **filtered on `org_id == user.org_id` and
   `deleted_at IS NULL`**. Missing → `denied / org_unit_not_found` (FR-5's
   "excluded from new resolutions", AC-18's cross-org denial).
2. `chain = ancestor_unit_ids(..., include_self=True)`.
3. Native pass: `active_grants_for_user(user.id, user.org_id, at)`, keep grants
   where `restrict_role_ids` is None or `grant.role_id in restrict_role_ids`,
   then keep grants where
   `grant.org_unit_id == org_unit_id` **or**
   (`grant.role.allows_hierarchy_rollup` **and** `grant.org_unit_id in chain[1:]`).
   Never any other direction (FR-8: no downward, no lateral).
4. For each surviving grant, call the **existing**
   `core.rbac.has_permission(role.permission_values, permission)` (FR-23 — the
   string check is reused verbatim, not reimplemented). Any hit →
   `allowed / native_grant`. Candidate grants but no permission hit →
   `denied / role_lacks_permission` (AC-19).
5. If still denied and `allow_delegated`: for each `Delegation` where
   `delegate_user_id == user.id`, `org_id == user.org_id`, `deleted_at IS NULL`,
   `status == 'active'`, `start_date <= at <= end_date` (evaluated fresh every
   call — FR-16, no cache):
   - if `d.org_unit_id` is not None and `org_unit_id not in
     descendant_unit_ids(d.org_unit_id, include_self=True)` → skip;
   - `sub = resolve_access(db, user=<delegator>, permission=permission,
     org_unit_id=org_unit_id, at=at, allow_delegated=False,
     restrict_role_ids=(d.role_id,) if d.role_id else None)` — the **intersection**
     (FR-14) and the **no-chain** rule (FR-17) in one line;
   - `sub.allowed` → `allowed / delegated_grant` with `via_delegation_id=d.id`,
     `on_behalf_of_user_id=d.delegator_user_id`.
6. Otherwise `denied / no_grant`.

Consistent-snapshot behavior (spec edge case): the whole resolution runs on the
caller's single `Session`/transaction — one READ COMMITTED snapshot per
statement chain, never a mix of pre- and post-revocation state within a request.

**Delegated-action attribution (FR-21):** `write_audit_log` already stores the
acting user in `actor_user_id`. The delegator goes in `metadata_json` as
`on_behalf_of_user_id` (plus `delegation_id`), produced by
`delegation_audit_metadata()`. Two distinct fields, never collapsed. No change
to `app/core/audit.py` is required.

**Where THIS feature consumes the resolver (FR-23 wired end to end):** every
mutation endpoint below first passes `require_permission(...)` (string check),
then calls `assert_access(db, user=actor, permission=<same string>,
org_unit_id=<the target unit>)` — for org-unit mutations the target unit (and,
on re-parent, the new parent too); for role grants the grant's `org_unit_id`;
for delegations the delegation's `org_unit_id` or the org root when null. This
is what makes AC-16 and AC-19 observable on this feature's own surface.

### Database schema

**New table `org_unit`** (`OrgUnit` — TableNameMixin, IdMixin, OrgScopedMixin,
ActorTrackedMixin, SoftDeleteMixin, TimestampMixin):

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK, default `new_uuid()` |
| org_id | varchar(36) | NOT NULL, indexed (`OrgScopedMixin`) |
| name | varchar(200) | NOT NULL |
| parent_id | varchar(36) | NULL, FK `org_unit.id` ON DELETE RESTRICT, indexed |
| created_at / updated_at | timestamptz | NOT NULL |
| created_by_user_id / updated_by_user_id | varchar(36) | NULL |
| deleted_at | timestamptz | NULL (soft delete) |
| deleted_by_user_id | varchar(36) | NULL (FR-20: always set by the service) |
| legal_hold | boolean | NOT NULL default false (`SoftDeleteMixin`) |

Indexes/constraints:
- `ix_org_unit_org_id` (mixin), `ix_org_unit_parent_id`
- `ix_org_unit_org_parent` on `(org_id, parent_id)` — the ancestry/children walk
- `uq_org_unit_single_root`: UNIQUE index on `(org_id)` **WHERE `parent_id IS
  NULL AND deleted_at IS NULL`** (`postgresql_where` + `sqlite_where`) — FR-4
  enforced in the DB as well as the service
- `ck_org_unit_no_self_parent`: CHECK `(parent_id IS NULL OR parent_id <> id)`

**Reshaped table `user_role`** — **decision: it becomes a full mapped model**
`UserRoleGrant` (TableNameMixin with explicit `__tablename__ = "user_role"`,
IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin).
It must carry `created_by_user_id` (who granted), `deleted_by_user_id` (who
revoked — FR-20/AC-17) and a validity window, which an association `Table`
cannot express; and a user may now hold the same role at several org units, so
the composite `(user_id, role_id)` PK must go.

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | **new** PK, default `new_uuid()` |
| user_id | varchar(36) | NOT NULL, FK `user.id` ON DELETE CASCADE, indexed |
| role_id | varchar(36) | NOT NULL, FK `role.id` ON DELETE CASCADE, indexed |
| org_id | varchar(36) | **new** NOT NULL, indexed |
| org_unit_id | varchar(36) | **new** NOT NULL, FK `org_unit.id` ON DELETE RESTRICT, indexed |
| valid_from | timestamptz | **new** NULL (= effective immediately) |
| valid_to | timestamptz | **new** NULL (= no expiry) |
| created_at / updated_at | timestamptz | **new** NOT NULL |
| created_by_user_id / updated_by_user_id | varchar(36) | **new** NULL |
| deleted_at / deleted_by_user_id | — | **new** NULL — revocation IS the soft delete |
| legal_hold | boolean | **new** NOT NULL default false |

- `uq_user_role_scope`: UNIQUE index on `(user_id, role_id, org_unit_id)` WHERE
  `deleted_at IS NULL` — one live grant per (user, role, unit); re-granting a
  revoked scope creates a new row (409 otherwise).
- `ix_user_role_lookup` on `(user_id, org_id, deleted_at)` — the resolver's hot path.
- `user_role_table = UserRoleGrant.__table__` stays exported from
  `app/auth/models.py` for existing `select().select_from(user_role_table)`
  readers in `app/roles/service.py`.
- `User.roles` becomes
  `relationship("Role", secondary=user_role_table, viewonly=True, lazy="selectin",
  primaryjoin="and_(User.id == user_role.c.user_id, user_role.c.deleted_at.is_(None))",
  secondaryjoin="Role.id == user_role.c.role_id")`. **viewonly** because grants
  now carry data an implicit association insert cannot populate; all writes go
  through `UserRoleGrant` rows in the service layer. Expiry is deliberately
  *not* expressed in the join (dialect-fragile, cached per load) — it is applied
  by `effective_permission_values()` at the `require_permission` chokepoint.

**Changed table `role`**: new column `allows_hierarchy_rollup boolean NOT NULL
DEFAULT true` (FR-7, default rolls up). No index (low cardinality, always read
via the role row itself).

**New table `delegation`** (`Delegation` — TableNameMixin, IdMixin,
OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin):

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| org_id | varchar(36) | NOT NULL, indexed |
| delegator_user_id | varchar(36) | NOT NULL, FK `user.id`, indexed |
| delegate_user_id | varchar(36) | NOT NULL, FK `user.id`, indexed |
| role_id | varchar(36) | NULL, FK `role.id`, indexed (NULL = all roles held) |
| org_unit_id | varchar(36) | NULL, FK `org_unit.id`, indexed (NULL = all units held) |
| start_date | timestamptz | NOT NULL |
| end_date | timestamptz | NOT NULL |
| status | varchar(20) | NOT NULL default `'active'`, indexed |
| created_at / updated_at / created_by_user_id / updated_by_user_id | | standard mixins |
| deleted_at / deleted_by_user_id / legal_hold | | `SoftDeleteMixin` — **revocation sets `status='revoked'` AND `deleted_at`/`deleted_by_user_id`**; the response's `revoked_at`/`revoked_by_user_id` are these columns. No separate `revoked_at` column (one source of truth; `status` exists because FR-15 requires an explicit status). |

- `ck_delegation_distinct_parties`: CHECK `delegator_user_id <> delegate_user_id`
- `ck_delegation_date_order`: CHECK `end_date >= start_date`
- `ck_delegation_status`: CHECK `status IN ('active','revoked')`
- `ix_delegation_delegate_lookup` on `(delegate_user_id, status, end_date)` — the
  resolver's delegation pass
- `ix_delegation_delegator` on `(delegator_user_id, status)`

**Migration**: revision ID `0042_org_hierarchy_rbac` (22 chars ≤ 32), on the
current single head **`0041_merge_heads`** (verified: `0041_merge_heads` is the
only head). Steps, in order, inside one revision:

1. `op.create_table("org_unit", ...)` + its indexes/CHECK, including the partial
   unique root index.
2. `op.add_column("role", sa.Column("allows_hierarchy_rollup", sa.Boolean(),
   nullable=False, server_default=sa.true()))` (server default kept — existing
   rows must roll up per FR-7's default).
3. `user_role` widening, all columns added **nullable first**:
   `id, org_id, org_unit_id, valid_from, valid_to, created_at, updated_at,
   created_by_user_id, updated_by_user_id, deleted_at, deleted_by_user_id,
   legal_hold`.
4. **Data migration (FR-12), in Python on `op.get_bind()`** — dialect-agnostic,
   no dependency on app code (migrations must not drift with the models):
   ```
   for each row in organization:
       if no org_unit exists for that org:
           insert org_unit(id=uuid4(), org_id=org.id, name='Global',
                           parent_id=NULL, created_at=now, updated_at=now,
                           legal_hold=false)
       root_id[org.id] = that unit's id
   for each row in user_role:
       org = SELECT org_id FROM "user" WHERE id = user_role.user_id
       UPDATE user_role SET id = uuid4(), org_id = org,
                            org_unit_id = root_id[org],
                            valid_from = NULL, valid_to = NULL,
                            created_at = now, updated_at = now,
                            legal_hold = false
   DELETE FROM user_role WHERE user_id NOT IN (SELECT id FROM "user")  -- defensive
   ```
   No `valid_to` is set, so nobody loses access at cutover (AC-9).
5. `op.alter_column(... nullable=False)` for `id, org_id, org_unit_id,
   created_at, updated_at, legal_hold`.
6. `op.drop_constraint("pk_user_role", "user_role", type_="primary")` →
   `op.create_primary_key("pk_user_role", "user_role", ["id"])`.
7. FK `fk_user_role_org_unit_id_org_unit`, index `ix_user_role_lookup`, partial
   unique `uq_user_role_scope`.
8. `op.create_table("delegation", ...)` + indexes/CHECKs.

`downgrade()` (working, in reverse): drop `delegation`; drop the partial unique
+ FK + added `user_role` columns, **first de-duplicating** `(user_id, role_id)`
(keep the oldest non-deleted row per pair — multi-unit grants cannot survive a
composite PK), then restore the composite PK; drop `role.allows_hierarchy_rollup`;
drop `org_unit`.

No pgvector involvement.

### Frontend TypeScript models (added to `src/lib/types.ts`)

```ts
// Mirrors the API contract exactly — copy verbatim.
export interface OrgUnitResponse {
  id: ID;
  org_id: ID;
  name: string;
  parent_id: ID | null;
  is_root: boolean;
  depth: number;
  path_names: string[];
  child_count: number;
  active_grant_count: number;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrgUnitReparentEntry {
  org_unit_id: ID;
  name: string;
  from_parent_id: ID | null;
  to_parent_id: ID | null;
}

export interface OrgUnitDeleteResponse {
  deleted_org_unit_id: ID;
  reparented: OrgUnitReparentEntry[];
}

export interface RoleGrantResponse {
  id: ID;
  org_id: ID;
  user_id: ID;
  user_label: string;
  role_id: ID;
  role_name: string;
  allows_hierarchy_rollup: boolean;
  org_unit_id: ID;
  org_unit_name: string;
  valid_from: string | null;
  valid_to: string | null;
  is_active: boolean;
  revoked_at: string | null;
  revoked_by_user_id: ID | null;
  created_at: string;
  created_by_user_id: ID | null;
}

export type DelegationStatus = "active" | "revoked";

export interface DelegationResponse {
  id: ID;
  org_id: ID;
  delegator_user_id: ID;
  delegator_label: string;
  delegate_user_id: ID;
  delegate_label: string;
  role_id: ID | null;
  role_name: string | null;
  org_unit_id: ID | null;
  org_unit_name: string | null;
  start_date: string;
  end_date: string;
  status: DelegationStatus;
  is_active: boolean;
  can_revoke: boolean;
  revoked_at: string | null;
  revoked_by_user_id: ID | null;
  created_at: string;
  created_by_user_id: ID | null;
}

export interface DelegationEligibilityEntry {
  role_id: ID;
  role_name: string;
  allows_hierarchy_rollup: boolean;
  org_unit_id: ID;
  org_unit_name: string;
  valid_to: string | null;
}

// Existing interface, one added field:
// export interface RoleResponse { …; allows_hierarchy_rollup: boolean; }
```

### Endpoint client additions (`src/lib/endpoints.ts`)

- `orgUnitsApi.list(params?: { include_deleted?: boolean }) → Promise<OrgUnitResponse[]>` — GET `/org-units`
- `orgUnitsApi.create(payload: { name: string; parent_id: ID | null }) → Promise<OrgUnitResponse>` — POST `/org-units`
- `orgUnitsApi.update(id, payload: { name?: string; parent_id?: ID | null }) → Promise<OrgUnitResponse>` — PATCH `/org-units/{id}`
- `orgUnitsApi.remove(id) → Promise<OrgUnitDeleteResponse>` — DELETE `/org-units/{id}`
- `roleGrantsApi.list(params?: { user_id?: ID; org_unit_id?: ID; include_revoked?: boolean }) → Promise<RoleGrantResponse[]>` — GET `/role-grants`
- `roleGrantsApi.create(payload: { user_id: ID; role_id: ID; org_unit_id: ID; valid_from?: string | null; valid_to?: string | null }) → Promise<RoleGrantResponse>` — POST `/role-grants`
- `roleGrantsApi.revoke(id) → Promise<void>` — DELETE `/role-grants/{id}`
- `delegationsApi.list(direction: "mine" | "received" | "all" = "mine") → Promise<DelegationResponse[]>` — GET `/delegations`
- `delegationsApi.eligibility(delegatorUserId?: ID) → Promise<DelegationEligibilityEntry[]>` — GET `/delegations/eligibility`
- `delegationsApi.create(payload: { delegator_user_id?: ID | null; delegate_user_id: ID; role_id?: ID | null; org_unit_id?: ID | null; start_date: string; end_date: string }) → Promise<DelegationResponse>` — POST `/delegations`
- `delegationsApi.revoke(id) → Promise<DelegationResponse>` — POST `/delegations/{id}/revoke`
- `rolesApi.update` payload gains optional `allows_hierarchy_rollup?: boolean`.

## Component design

### Backend

- **Models**: `backend/app/org_structure/models.py` — `OrgUnit`, `Delegation`.
  `backend/app/auth/models.py` — `UserRoleGrant` (table `user_role`) +
  `user_role_table` alias, `Role.allows_hierarchy_rollup`, `User.roles`
  viewonly relationship. `backend/app/models.py` — import + `__all__` entries
  for `OrgUnit`, `Delegation`, `UserRoleGrant` (names must exist — ruff F822).
  `UserRoleGrant` lives in `auth/models.py`, not the new domain, to avoid an
  `auth → org_structure → auth` import cycle; its FKs to `org_unit` are
  declared by string so no import is needed.
- **Schemas**: `backend/app/org_structure/schemas.py` — `OrgUnitCreate`,
  `OrgUnitUpdate`, `OrgUnitResponse`, `OrgUnitReparentEntry`,
  `OrgUnitDeleteResponse`, `RoleGrantCreate`, `RoleGrantResponse`,
  `DelegationCreate`, `DelegationResponse`, `DelegationEligibilityEntry`.
  `OrgUnitUpdate` uses a sentinel so "omit `parent_id`" (leave alone) differs
  from "`parent_id: null`" (make root) — Pydantic v2
  `model_fields_set` / `exclude_unset` check in the service.
- **Access helpers**: `backend/app/org_structure/access.py` —
  `get_org_unit_or_404(db, org_id, org_unit_id, *, include_deleted=False)`,
  `get_org_root(db, org_id)`, `ensure_same_org(actor, *entities)`. Every one
  filters on `actor.org_id` (AC-18).
- **Service**: `backend/app/org_structure/service.py` — org scoping + audit on
  every function:
  - `list_org_units(db, *, actor, include_deleted)` — `org_id` filter; computes
    `depth`/`path_names`/`child_count`/`active_grant_count`.
  - `create_org_unit(db, *, actor, payload)` — `assert_access` at the parent (or
    at the unit itself for a first root); 409 when `parent_id is None` and a
    root exists; audit `org_unit.created`.
  - `update_org_unit(db, *, actor, org_unit_id, payload)` — rename and/or
    re-parent; cycle check via `descendant_unit_ids(org_unit_id)` (409 if the
    new parent is self or a descendant — FR-3/AC-1); 409 on clearing the parent
    while another root exists (FR-4/AC-2); separate audit rows for rename vs
    re-parent.
  - `delete_org_unit(db, *, actor, org_unit_id)` — 409 for the root when it has
    any non-deleted descendant; re-parents direct children to the deleted
    unit's parent, writing one `org_unit.reparented_on_delete` audit row per
    child plus the `org_unit.deleted` row (FR-5/AC-8); sets `deleted_at` **and**
    `deleted_by_user_id = actor.id` (FR-20).
  - `list_role_grants` / `create_role_grant` / `revoke_role_grant` — org-scoped;
    `create` rejects a soft-deleted target unit (422), a cross-org user/role/unit
    (404), a duplicate live scope (409), `valid_to < valid_from` (422), and
    reuses `app.roles.service._assert_actor_can_grant`-equivalent escalation
    logic by calling the existing helper (imported, not copied) so an actor
    cannot grant permissions they do not hold.
  - `list_delegations` / `delegation_eligibility` / `create_delegation` /
    `revoke_delegation` — `create` rejects self-delegation (422), `end < start`
    (422), a non-self delegator without `admin_panel:access` (403), and a
    delegator who does not hold the narrowed role/unit **natively**
    (`resolve_access(..., allow_delegated=False)` → 403, which is exactly the
    no-chain rule of FR-17/AC-14). `revoke` allows only
    `actor.id == delegation.delegator_user_id` or `is_org_admin(actor)`
    (`app/core/access.py`, existing) — 403 otherwise (FR-18/AC-15).
- **Resolver**: `backend/app/core/org_access.py` (signatures frozen above).
- **Router**: `backend/app/org_structure/routes.py` — three thin `APIRouter`s,
  no business logic: `org_units_router` (prefix `/org-units`, tag
  `org-units`), `role_grants_router` (prefix `/role-grants`, tag
  `role-grants`), `delegations_router` (prefix `/delegations`, tag
  `delegations`). Permissions exactly per the contract table. Registered in
  `backend/app/main.py` next to `walls_router`.
- **Chokepoint edit**: `backend/app/core/deps.py` — `require_permission`'s inner
  dependency gains `db: Session = Depends(get_db)` and calls
  `org_access.effective_permission_values(db, user=current_user)` in place of
  `current_user.permission_values`. `has_permission` itself is untouched.
- **Roles domain edit**: `backend/app/roles/{schemas,service}.py` —
  `RoleResponse.allows_hierarchy_rollup`, `RoleUpdate.allows_hierarchy_rollup`,
  handled in `update_role` (existing `admin_panel:access` gate, existing
  `role.updated` audit extended); `serialize_role`'s `user_count` and
  `delete_role`'s holder count gain `deleted_at IS NULL`; `set_user_roles`
  rewritten to create/soft-delete `UserRoleGrant` rows at the org **root** unit
  (preserving today's flat semantics for that legacy screen) instead of
  assigning `target.roles`, which is now viewonly.
- **Celery**: none. Expiry is evaluated at resolution time (FR-9/FR-16), so
  there is no sweeper job and nothing to schedule.
- **Settings**: none. No external service, no `MOCK_<NAME>` flag, no
  `validate_runtime_settings()` entry.

### Frontend (Next.js page/component tree)

- `src/app/(app)/org-structure/page.tsx` — `"use client"`. Admin screen; renders
  the `Lock`/`EmptyState` "Access restricted" block (copied from
  `admin/page.tsx`) unless `can(user, "admin_panel:access")`. `Tabs`:
  `Org units` | `Role grants`.
  - `_org-unit-tree.tsx` — FR-26. Fetches `orgUnitsApi.list()` via react-query
    (`["org-units"]`), builds the tree with `buildOrgTree()` from
    `src/lib/org-tree.ts`, renders nested rows with indentation, `Badge` for
    "Root", grant counts, and per-row `Button`s: Rename, Move, Delete
    (`useConfirm`). API 409s surface verbatim through `useToast(..., "error")`
    ("clear rejection message" per FR-26/AC-21). After a delete, renders a
    `MessageBar` listing the auto-re-parented children from
    `OrgUnitDeleteResponse.reparented` (AC-8).
  - `_org-unit-modal.tsx` — create/rename/move form: `Field` + `Input` (name)
    and `Select` (parent). The parent `Select` omits the unit itself and all
    its descendants (`isDescendantOf()` from `src/lib/org-tree.ts`) so a cycle
    can't even be proposed; the server check remains authoritative.
  - `_role-grants-panel.tsx` — FR-27. User `Select` filter +
    `roleGrantsApi.list({ user_id })` `Table` (user, role, org unit, window,
    status `Badge`), Revoke `Button` with `useConfirm`.
  - `_assign-grant-modal.tsx` — user `Select` (`usersApi.list()`), role
    `Select` (`rolesApi.list()`), org-unit `Select` (indented tree labels),
    optional `valid_from` / `valid_to` `Input type="date"`.
- `src/app/(app)/delegations/page.tsx` — FR-28. Self-service, gated on
  `can(user, "delegation:manage")`. Two `Card`s: **Delegated by me**
  (`direction=mine`, each row has a Revoke `Button` when `can_revoke`) and
  **Delegated to me** (`direction=received`, **no revoke control** — AC-23).
  Admins additionally get an "All delegations" `Tabs` entry
  (`direction=all`) when `can(user, "admin_panel:access")`.
  - `_delegation-modal.tsx` — delegate `Select`, role `Select` and org-unit
    `Select` populated from `delegationsApi.eligibility()` (so the user can only
    pick what they actually hold — FR-14 at the UI layer), start/end date
    `Input type="date"`; server-side 403/422 messages surfaced via `useToast`.
- `src/lib/org-tree.ts` — pure, dependency-free helpers: `buildOrgTree(units)`,
  `flattenWithIndent(units)`, `isDescendantOf(units, candidateId, ancestorId)`.
  Unit-tested; no React.
- `src/components/aegis-rail.tsx` — two nav items: `Admin` section →
  `{ href: "/org-structure", label: "Org structure", perm: "admin_panel:access" }`;
  `Workspace` section → `{ href: "/delegations", label: "Delegations", perm: "delegation:manage" }`.
- `src/app/(app)/admin/page.tsx` — one addition inside `RoleEditorModal`: a
  "Rolls up the org-unit hierarchy" checkbox bound to
  `allows_hierarchy_rollup` (disabled for the built-in `admin` role, which is
  read-only there today).
- **New shared primitives in `src/components/ui.tsx`: none.** Every primitive
  used above — `Badge, Button, Card, CardBody, CardHeader, CardTitle,
  CenterSpinner, EmptyState, ErrorState, Field, Input, MessageBar, Modal,
  PageHeader, Select, Table, TD, TH, THead, TR, Tabs, useConfirm` — is already
  exported from `frontend/src/components/ui.tsx` (verified against the file).
  Icons from `lucide-react`; toasts from `@/components/toast`.
- **Demo-mode behavior**: this repo has **no `src/lib/demo.ts`** (checked —
  `frontend/src/lib/` contains api, auth, client-logger, contract-blocks,
  endpoints, intake, layout, query, types, utils). There is therefore no demo
  mode to keep functional. Both pages degrade to the standard
  `CenterSpinner` → `ErrorState` path when the API is unreachable, like every
  other page.

## Test strategy

- **Backend pytest** (Postgres, per `backend/tests/conftest.py`):
  - `test_org_access_resolver.py` — fixture builds Global → Region A → Entity 1
    → BU 1. Cases: rollup down (AC-3), no upward/lateral rollup (AC-4),
    `allows_hierarchy_rollup=False` exact-unit-only (AC-5), expired `valid_to`
    denied (AC-6), revoked-but-unexpired denied (AC-7), role at correct scope
    but lacking the permission string denied with
    `reason="role_lacks_permission"` (AC-19), org-B unit denied with
    `reason="org_unit_not_found"` (AC-18), zero grants → denied not error,
    delegation intersection allowed (AC-10), over-broad delegation clamped
    (AC-11), revoked delegation denied immediately (AC-12), ended delegation
    denied (AC-13), no delegation chain (recursion depth, FR-17), an active
    ethical wall still denies a resolver-allowed contract (AC-20).
  - `test_org_units_api.py` — create/rename/re-parent/delete happy paths;
    cycle 409 (AC-1), second root 409 (AC-2), root-with-descendants delete 409,
    cross-org 404 (AC-18), missing-permission 403, auto-re-parent + the two
    `org_unit.reparented_on_delete` audit rows (AC-8), `deleted_by_user_id` set
    (FR-20).
  - `test_role_grants_api.py` — assign/revoke happy path (AC-22), duplicate 409,
    `valid_to < valid_from` 422, soft-deleted unit 422, cross-org 404, 403
    without `user:update_role`, escalation guard 403, the `role_grant.revoked`
    audit row carrying actor + `revocation: true` (AC-17).
  - `test_delegations_api.py` — create/revoke happy path, self-delegation 422,
    `end < start` 422, delegate-revokes 403 (AC-15), admin-revokes 200,
    re-delegation 403 (AC-14), `can_revoke` false on a received delegation
    (AC-23), audit metadata carries `acting_user_id` + `on_behalf_of_user_id`
    as two fields (AC-16).
  - Regression: existing `test_role_assignment_privilege_escalation.py` and
    `test_phase1_auth_foundation.py` must still pass against the reshaped
    `user_role` and the `require_permission` change.
- **Frontend vitest**: `src/lib/org-tree.test.ts` — `buildOrgTree` orders and
  nests correctly with an out-of-order flat list; `isDescendantOf` detects a
  deep descendant and rejects an unrelated unit and self; `flattenWithIndent`
  produces stable depth labels for the `Select` pickers.
- **Acceptance verification** (`/verify`): AC-1..AC-8, AC-10..AC-20, AC-22,
  AC-23 are each evidenced by a named pytest above. AC-21 and AC-23's UI half
  are evidenced by `npm run typecheck` + the vitest tree tests + a manual
  `docker compose up -d` walkthrough of `/org-structure` and `/delegations`
  captured in `verification.md`. **AC-9 (migration backfill) is evidenced by a
  documented docker-compose procedure**: seed a pre-migration DB at
  `0041_merge_heads` with users/roles, run `alembic upgrade head`, assert every
  `user_role` row has `org_unit_id` = its org's root and `valid_to IS NULL`, and
  that a resolver check at each unit matches the pre-migration outcome. It is
  deliberately not a pytest case — the suite runs against an already-upgraded
  schema, so a unit test could not observe the pre-migration state.

## Risks & decisions

- **`user_role` becomes a full mapped model (`UserRoleGrant`), not an extended
  association Table.** Rationale: it needs `created_by_user_id` /
  `deleted_by_user_id` (FR-20, AC-17), a validity window (FR-6/FR-9), and a
  user may hold the same role at several units, which the composite
  `(user_id, role_id)` PK forbids. *Rejected*: adding columns to the existing
  `Table` and keeping the relationship writable — SQLAlchemy's secondary-table
  inserts cannot populate `org_id`/`org_unit_id`/actor columns, so every grant
  written through `User.roles` would violate NOT NULL.
- **`User.roles` becomes `viewonly`**, filtered on `deleted_at IS NULL` only.
  Consequence: `app/roles/service.set_user_roles` must write grant rows
  explicitly (at the org root, preserving today's flat semantics for that
  legacy screen). *Rejected*: encoding the validity window in the relationship's
  join condition — `func.now()` comparisons in a relationship are dialect-fragile
  and cached per load, which would violate FR-16's "evaluate fresh".
- **`require_permission` calls `effective_permission_values()`** (one extra
  indexed query per gated request) instead of `user.permission_values`.
  Rationale: without it an *expired* grant would still confer permissions on
  every non-scoped endpoint, contradicting FR-9 and leaving FR-23's "both checks"
  half-wired. This is the one cross-cutting change in the feature — flagged for
  the user's attention. *Rejected*: leaving expiry purely to the org-unit-scoped
  resolver, which would mean expiry only bites on endpoints that opt in.
- **The resolver lives in `app/core/org_access.py`, not in the domain.** FR-11
  requires one shared capability that `app/core/deps.py` itself calls; a domain
  module would invert the dependency direction. *Rejected*:
  `app/org_structure/access.py` as the resolver home (kept, but only for
  org-scoped fetch helpers).
- **New domain `org_structure` rather than extending `organizations`.**
  `app/organizations/` is the flat tenant record (a single model, no service
  layer); this feature is three tables, a resolver-integrated service layer and
  eleven endpoints. Module-per-domain says give it its own folder. *Rejected*:
  extending `organizations` (would bloat a deliberately thin module) and three
  separate domains (`org_units`/`role_grants`/`delegations` are one cohesive
  transactional unit — deleting a unit touches grants, creating a delegation
  reads grants).
- **Delegation revoke is `POST /{id}/revoke`, not `DELETE`.** FR-15 models this
  as a status transition with a response body the UI re-renders from; `DELETE`
  would imply record removal. Role-grant revoke *is* `DELETE` + 204 because
  FR-10 frames it as a soft-delete and the screen just re-lists.
- **Two new permission strings** (`org_unit:read`, `delegation:manage`) rather
  than reusing `admin_panel:access` everywhere: the delegation screen is
  explicitly self-service for any user (FR-28), and every role-grant/delegation
  picker needs the org-unit list, so gating tree reads on the admin permission
  would make FR-28 unusable for non-admins. Adding them requires updating
  `DEFAULT_ROLE_PERMISSIONS` and possibly the locked-down-production fixture in
  `tests/test_phase10_security_hardening.py`.
- **Single root enforced twice** — a partial unique index (`WHERE parent_id IS
  NULL AND deleted_at IS NULL`) *and* a service check returning a readable 409.
  The index is the guarantee under concurrency; the service check is the message
  FR-26/AC-2 require.
- **Cycle prevention walks descendants, not ancestors**, and every walk is
  depth-capped at 64. A pre-existing corrupt cycle would otherwise hang the
  request rather than 409.
- **Risk — blast radius of the `user_role` reshape.** `app/roles/service.py`
  (three raw `user_role_table` counts and one `target.roles =` assignment) and
  `app/auth/` are the only writers found; each must gain a `deleted_at IS NULL`
  filter. Mitigation: existing `test_role_assignment_privilege_escalation.py`
  and `test_phase1_auth_foundation.py` are regression gates and must be run by
  the backend-dev task that touches `roles/service.py`.
- **Risk — the downgrade is lossy.** Restoring the composite PK forces
  de-duplication of multi-unit grants. Documented in the migration docstring;
  acceptable because downgrade is a dev/CI affordance, not a production path.

### FR traceability

| FR | Where it lands |
|---|---|
| FR-1 | `org_unit` table (self-FK `parent_id`) + `POST /org-units` + `ck_org_unit_no_self_parent` |
| FR-2 | `PATCH /org-units/{id}` (rename + re-parent), `DELETE /org-units/{id}` (soft delete; grants untouched) |
| FR-3 | `service.update_org_unit` cycle check via `descendant_unit_ids` → 409 |
| FR-4 | `uq_org_unit_single_root` partial unique index + 409 in `create_org_unit` / `update_org_unit` |
| FR-5 | `service.delete_org_unit` auto-re-parent + `org_unit.reparented_on_delete` audit rows; resolver step 1 excludes deleted units |
| FR-6 | `POST /role-grants` with `org_unit_id`, `valid_from`, `valid_to` |
| FR-7 | `role.allows_hierarchy_rollup` column (default true) + resolver step 3 + `PATCH /roles/{id}` |
| FR-8 | `resolve_access` steps 2–3 (`ancestor_unit_ids`, upward-only match) |
| FR-9 | `active_grants_for_user` (independent `deleted_at` and `valid_to` filters) + `effective_permission_values` |
| FR-10 | `DELETE /role-grants/{id}` (soft delete, immediate exclusion) |
| FR-11 | `backend/app/core/org_access.py` — the only implementation; no ancestry/expiry logic anywhere else |
| FR-12 | Migration `0042_org_hierarchy_rbac` step 4 (root per org + backfill of every `user_role` row, `valid_to` NULL) |
| FR-13 | `delegation` table + `POST /delegations` (one delegator, one delegate, bounded dates, optional role/unit) |
| FR-14 | `resolve_access` step 5 — recursion into the delegator's own resolution |
| FR-15 | `delegation.status` + revoke sets `status='revoked'` and `deleted_at`; resolver filters both, no grace window |
| FR-16 | `resolve_access` re-runs the delegation pass on every call; nothing cached |
| FR-17 | `allow_delegated=False` on the delegator pass + 403 at creation when the delegator holds the narrowing only via delegation |
| FR-18 | `service.revoke_delegation` — delegator or `is_org_admin` only; `can_revoke` on the response |
| FR-19 | Audit-action table above (10 actions, before/after payloads) |
| FR-20 | `SoftDeleteMixin.deleted_by_user_id` set by every soft-delete path (org unit, grant, delegation) |
| FR-21 | `delegation_audit_metadata()` → `actor_user_id` (column) + `on_behalf_of_user_id` (metadata), two distinct fields |
| FR-22 | `active_grants_for_user` / `ancestor_unit_ids` / the delegation filter in `org_access.py` are the single exclusion mechanism |
| FR-23 | `resolve_access` step 4 calls the existing `core.rbac.has_permission`; `require_permission` runs first on every route |
| FR-24 | `app/authority/` not in any agent's path mapping; regression-asserted in `test_org_access_resolver.py` |
| FR-25 | `app/walls/` not in any agent's path mapping; AC-20 test asserts a wall still denies a resolver-allowed contract |
| FR-26 | `src/app/(app)/org-structure/page.tsx` + `_org-unit-tree.tsx` + `_org-unit-modal.tsx` |
| FR-27 | `_role-grants-panel.tsx` + `_assign-grant-modal.tsx` |
| FR-28 | `src/app/(app)/delegations/page.tsx` + `_delegation-modal.tsx` (`can_revoke` hides the delegate's control) |

### AC traceability

AC-1→`test_org_units_api.py`; AC-2→`test_org_units_api.py`; AC-3,4,5→
`test_org_access_resolver.py`; AC-6,7→`test_org_access_resolver.py`; AC-8→
`test_org_units_api.py`; **AC-9→documented migration procedure in
`verification.md`** (see Test strategy); AC-10..AC-14→
`test_org_access_resolver.py`; AC-15→`test_delegations_api.py`; AC-16→
`test_delegations_api.py` (audit metadata); AC-17→`test_role_grants_api.py`;
AC-18→`test_org_access_resolver.py` + `test_org_units_api.py`; AC-19→
`test_org_access_resolver.py`; AC-20→`test_org_access_resolver.py` (wall
override) and the empty diff on `app/walls/` + `app/authority/`; AC-21→
`/org-structure` manual walkthrough + `org-tree.test.ts`; AC-22→
`test_role_grants_api.py`; AC-23→`test_delegations_api.py` (`can_revoke`) +
`/delegations` manual walkthrough.
