# Implementation Plan: Menu/Screen-Level Security (VIEW/ADD/EDIT/DELETE) with Dynamic Menu Visibility

Feature ID: 003-menu-screen-security
Spec: ./spec.md (must be APPROVED)
Created: 2026-09-14
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

One new backend domain, one new shared core module, one new core dependency,
one new frontend admin page, one guard component, one Alembic revision that
also carries the whole catalog seed + the FR-26 cutover backfill.

**New domain `backend/app/menu_security/`** — mirrors the shape of
`backend/app/org_structure/` (feature 002): thin `routes.py`, all logic in
`service.py`, Pydantic in `schemas.py`, SQLAlchemy in `models.py`, org-scoped
fetch helpers in `access.py`. It owns four new tables (`screen`,
`action_level`, `menu_item`, `role_screen_access`).

**The screen-access resolver lives in a NEW core module
`backend/app/core/screen_access.py`** — *not* inside `org_access.py`, and *not*
in the domain. Two constraints force this:

1. spec.md's "Out of scope" says, verbatim: *"Any change to feature 002's
   org-unit hierarchy, delegation, or permission-string resolution algorithm
   itself (`app.core.org_access`…) — this feature adds a distinct access axis
   … that composes with, but does not modify, that existing resolver."*
   Adding a function to `org_access.py` would edit the module the spec fences
   off, and `org_access.py` is imported by `core/deps.py` and therefore by
   every gated route in the app (~330 endpoints) — the worst possible blast
   radius for an additive change.
2. Feature 002's precedent — *"one shared resolver, never reimplemented,
   living in `app/core/` because `app/core/deps.py` itself calls it"* — is
   about **where** and **how many**, not about **which file**. This feature
   honors it exactly: exactly one implementation of screen-access resolution,
   in `app/core/`, consumed by the menu-tree endpoint, the route dependency
   and the "what can I do here" endpoint alike (FR-6/FR-15).

`screen_access.py` **imports and calls** `org_access.ancestor_unit_ids` and
`org_access.active_grants_for_user` (FR-18's "identical ancestor-walk") and
honors `Role.allows_hierarchy_rollup` the same way `resolve_access` does. It
reimplements nothing.

**New chokepoint `require_screen_level(screen_code, min_level)`** in
`backend/app/core/deps.py`, declared in the route signature immediately below
the existing `require_permission(...)` — the sibling pattern, so FR-11/AC-4's
"visibly present in the route's definition" is literal.

**Untouched by design**: `app/core/org_access.py` (read-only dependency),
`app/core/rbac.has_permission` (only the permission *catalog* constants gain
three strings), `app/walls/`, `app/authority/`, `app/jobs/tasks.py`,
`frontend/src/components/ui.tsx`.

```
request
  └─ core/deps.require_permission("<perm>")               ← UNCHANGED (FR-12: both must pass)
  └─ core/deps.require_screen_level("<screen>", "<LEVEL>")← NEW, declared on the route
       └─ core/screen_access.assert_screen_level()
            └─ core/screen_access.resolve_screen_access()  ← THE single resolution (FR-6)
                 ├─ org_access.active_grants_for_user()    ← REUSED verbatim
                 └─ org_access.ancestor_unit_ids()         ← REUSED verbatim (FR-18)
  └─ route → service → walls / accessible_contract_filter  ← UNCHANGED deny-override

GET /menu-tree        ─┐
GET /screen-access/me ─┴─► the SAME resolve_screen_access() (FR-7, FR-9, FR-15)
```

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | `backend/app/menu_security/__init__.py` (new, empty package marker), `backend/app/menu_security/models.py` (new: `Screen`, `ActionLevel`, `MenuItem`, `RoleScreenAccess`), `backend/app/models.py` (registry imports + `__all__`), `backend/alembic/versions/0043_menu_screen_security.py` (new — DDL + catalog seed + FR-26 backfill) |
| backend-dev | `backend/app/core/screen_access.py` (new resolver), `backend/app/core/deps.py` (edit: add `require_screen_level`), `backend/app/core/rbac.py` (edit: 3 new permission strings + `DEFAULT_ROLE_PERMISSIONS` wiring), `backend/app/menu_security/routes.py`, `backend/app/menu_security/service.py`, `backend/app/menu_security/schemas.py`, `backend/app/menu_security/access.py`, `backend/app/main.py` (edit: include 3 routers), `backend/app/contracts/routes.py` (edit), `backend/app/matters/routes.py` (edit), `backend/app/trademarks/routes.py` (edit), `backend/app/notices/routes.py` (edit), `backend/app/intake/routes.py` (edit), `backend/tests/test_screen_access_resolver.py`, `backend/tests/test_menu_tree_api.py`, `backend/tests/test_screen_access_grants_api.py`, `backend/tests/test_screen_level_enforcement.py`, `backend/tests/test_phase10_security_hardening.py` (edit **only** if a permission-catalog assertion breaks) |
| frontend-dev | `frontend/src/components/aegis-rail.tsx` (rewrite data source), `frontend/src/components/screen-guard.tsx` (new), `frontend/src/app/(app)/layout.tsx` (edit: mount `ScreenGuard`), `frontend/src/app/(app)/screen-access/page.tsx` (new), `frontend/src/app/(app)/screen-access/_screen-grants-panel.tsx` (new), `frontend/src/app/(app)/screen-access/_assign-screen-grant-modal.tsx` (new), `frontend/src/lib/screen-access.ts` (new: pure matcher + hook), `frontend/src/lib/screen-access.test.ts` (new), `frontend/src/lib/endpoints.ts` (edit: `menuApi`, `screensApi`, `screenAccessApi`), `frontend/src/lib/types.ts` (edit: new interfaces), `frontend/src/app/(app)/contracts/page.tsx`, `frontend/src/app/(app)/matters/page.tsx`, `frontend/src/app/(app)/trademarks/page.tsx`, `frontend/src/app/(app)/notices/page.tsx`, `frontend/src/app/(app)/intake/page.tsx` (edit: FR-9 control gating only) |
| qa-engineer | `specs/003-menu-screen-security/verification.md` |

**Shared-hotspot note** — these files serialize; no two tasks touching the same
one may run in the same wave:

- `backend/app/models.py` — db-engineer registry task only.
- `backend/app/menu_security/models.py` — db-engineer only, same task as the
  migration (model shape and DDL must land together).
- `backend/app/core/deps.py`, `backend/app/core/rbac.py`, `backend/app/main.py`
  — one backend-dev task each; do not split.
- `backend/app/core/screen_access.py` — one backend-dev task; every other
  backend task depends on it and must not edit it.
- The five tranche-1 `routes.py` files — one backend-dev task **per domain**
  (five independent, parallelizable tasks; they share no file).
- `frontend/src/lib/endpoints.ts`, `frontend/src/lib/types.ts`,
  `frontend/src/components/aegis-rail.tsx`,
  `frontend/src/app/(app)/layout.tsx` — one frontend-dev task each.
- `backend/app/jobs/tasks.py`, `frontend/src/components/ui.tsx`,
  `backend/app/core/org_access.py` — **not touched by this feature.**

## Interface freeze (contract-first — fixed before implementation starts)

All paths are under the API prefix `/api/v1`. All timestamps are ISO-8601
strings with timezone in JSON, `timestamptz` in the DB. `ID` = string UUID.

### New permission strings

Added to `app/core/rbac.py` via a new `MENU_SECURITY_PERMISSIONS` set folded
into `ALL_PERMISSIONS`, plus `DEFAULT_ROLE_PERMISSIONS` wiring.

| Permission | Meaning | Default roles granted |
|---|---|---|
| `menu:read` | Fetch **one's own** resolved menu tree / own screen-access resolution / the action-level reference list | admin, member, legal_reviewer, approver |
| `screen_access:read` | List the screen catalog and existing role→screen grants (the admin screen's reads) | admin |
| `screen_access:manage` | Create / modify / revoke a screen-access grant | admin |

`menu:read` is granted to every default role because the rail is fetched on
every page load by every user (FR-7, FR-20); gating it on `admin_panel:access`
would blank the navigation for non-admins. This mirrors feature 002's
`org_unit:read` precedent exactly.

### API contract

| Method | Path | Permission | Request body | Response | Errors |
|---|---|---|---|---|---|
| GET | `/menu-tree` | `menu:read` | — (query: `org_unit_id?`) | 200 `MenuTreeResponse` | 403, 404 (unknown `org_unit_id`) |
| GET | `/screen-access/me` | `menu:read` | — (query: `screen_code?`, `org_unit_id?`) | 200 `MyScreenAccessResponse` | 403, 404 (unknown screen/unit) |
| GET | `/action-levels` | `menu:read` | — | 200 `ActionLevelResponse[]` | 403 |
| GET | `/screens` | `screen_access:read` | — (query: `module?`) | 200 `ScreenResponse[]` | 403 |
| GET | `/screen-access/grants` | `screen_access:read` | — (query: `role_id?`, `screen_id?`, `org_unit_id?`, `include_revoked=false`) | 200 `ScreenGrantResponse[]` | 403 |
| POST | `/screen-access/grants` | `screen_access:manage` | `ScreenGrantCreate` | 201 `ScreenGrantResponse` | 403, 404 (role/screen/unit not in org), 409 (duplicate live grant), 422 (unknown action level) |
| PATCH | `/screen-access/grants/{grant_id}` | `screen_access:manage` | `ScreenGrantUpdate` | 200 `ScreenGrantResponse` | 403, 404, 409 (FR-25 bootstrap lock), 422 |
| DELETE | `/screen-access/grants/{grant_id}` | `screen_access:manage` | — | 204 no body | 403, 404, 409 (already revoked / FR-25 bootstrap lock) |

**There are deliberately NO menu-item or screen CRUD endpoints.** FR-24 states
the inventory of page routes and their organization into the menu tree is
structural application data, not end-user-editable content. `screen`,
`action_level` and `menu_item` rows are created and maintained exclusively by
Alembic migrations. `GET /screens` and `GET /action-levels` are read-only
pickers for the admin UI.

Full request/response JSON shapes:

```jsonc
// ---------- GET /api/v1/menu-tree ----------
// Pruned per FR-7 (screen_link node present iff resolved level >= VIEW) and
// FR-8 (group node present iff it has >=1 surviving descendant).
{
  "org_unit_id": null,                 // echoes the resolution context; null = the caller's own grant units
  "nodes": [
    {
      "id": "m001…",
      "parent_id": null,
      "label": "Workspace",
      "icon": null,                     // raw SVG path data (the string today's rail renders), null for groups
      "menu_type": "group",             // "group" | "screen_link"
      "sequence_order": 10,
      "screen_id": null,
      "screen_code": null,
      "route_path": null,
      "action_level": null,             // groups carry no level
      "children": [
        {
          "id": "m011…",
          "parent_id": "m001…",
          "label": "Contracts",
          "icon": "<path d=\"M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z\"/><path d=\"M14 3v5h5\"/>",
          "menu_type": "screen_link",
          "sequence_order": 40,
          "screen_id": "s004…",
          "screen_code": "contracts",
          "route_path": "/contracts",
          "action_level": "EDIT",        // "VIEW"|"ADD"|"EDIT"|"DELETE" — the caller's resolved level
          "children": []
        }
      ]
    }
  ]
}

// ---------- GET /api/v1/screen-access/me ----------
// FR-9 (UI control state) and FR-14 (deep-link gate) both read this. Returns
// ONLY screens the caller resolves to >= VIEW; a screen absent from `screens`
// is "below VIEW". Never another user's resolution (FR: own-resolution only).
{
  "org_unit_id": null,
  "screens": [
    { "screen_id": "s004…", "screen_code": "contracts",        "route_path": "/contracts",     "action_level": "EDIT",   "rank": 3 },
    { "screen_id": "s005…", "screen_code": "contract_detail",  "route_path": "/contracts/[id]","action_level": "EDIT",   "rank": 3 },
    { "screen_id": "s012…", "screen_code": "notices",          "route_path": "/notices",       "action_level": "VIEW",   "rank": 1 }
  ]
}
// With ?screen_code=contracts the same shape is returned with at most one entry.

// ---------- GET /api/v1/action-levels ----------
[
  { "id": "a1…", "code": "VIEW",   "rank": 1 },
  { "id": "a2…", "code": "ADD",    "rank": 2 },
  { "id": "a3…", "code": "EDIT",   "rank": 3 },
  { "id": "a4…", "code": "DELETE", "rank": 4 }
]

// ---------- GET /api/v1/screens ----------
[
  {
    "id": "s004…",
    "code": "contracts",
    "name": "Contracts",
    "module": "Workspace",
    "route_path": "/contracts",
    "is_enforced": true                 // true for the FR-17 tranche-1 five, false otherwise (UI hint only)
  }
]

// ---------- ScreenGrantResponse ----------
{
  "id": "g900…",
  "org_id": "a91c…",
  "role_id": "r001…",
  "role_name": "legal_reviewer",
  "role_is_builtin_admin": false,
  "allows_hierarchy_rollup": true,
  "screen_id": "s004…",
  "screen_code": "contracts",
  "screen_name": "Contracts",
  "org_unit_id": null,                  // null = organization-wide (every unit)
  "org_unit_name": null,
  "max_action_level_id": "a3…",
  "max_action_level": "EDIT",
  "max_action_rank": 3,
  "is_locked": false,                   // FR-25: true => cannot be lowered or revoked
  "is_active": true,                    // = deleted_at IS NULL
  "revoked_at": null,
  "revoked_by_user_id": null,
  "created_at": "2026-09-14T10:00:00+00:00",
  "created_by_user_id": "u000…",
  "updated_at": "2026-09-14T10:00:00+00:00",
  "updated_by_user_id": null
}

// ---------- POST /api/v1/screen-access/grants (ScreenGrantCreate) ----------
{
  "role_id": "r001…",
  "screen_id": "s004…",
  "org_unit_id": null,                  // optional; null = organization-wide
  "action_level": "EDIT"                // one of VIEW|ADD|EDIT|DELETE
}

// ---------- PATCH /api/v1/screen-access/grants/{id} (ScreenGrantUpdate) ----
{ "action_level": "DELETE" }
```

**Audit actions written by each mutation** (via `write_audit_log(...)` in the
same transaction as the change, `org_id=actor.org_id`,
`actor_user_id=actor.id`, `resource_type="role_screen_access"`,
`resource_id=<grant id>`):

| Endpoint | `action` | before / after / metadata |
|---|---|---|
| POST `/screen-access/grants` | `screen_access.granted` | after: `{role_id, role_name, screen_id, screen_code, org_unit_id, max_action_level}`; before: `null` |
| PATCH `/screen-access/grants/{id}` | `screen_access.updated` | before: `{max_action_level: "VIEW"}`, after: `{max_action_level: "EDIT"}`, metadata: `{role_id, role_name, screen_id, screen_code, org_unit_id}` — this is exactly AC-11 |
| DELETE `/screen-access/grants/{id}` | `screen_access.revoked` | before: `{role_id, role_name, screen_id, screen_code, org_unit_id, max_action_level}`, after: `null`, metadata: `{revocation: true}` |

**FR-28 denials** reuse the platform's existing access-decision trail —
`app.core.authz.record_decision` (the same function `require_permission` and
`org_access.assert_access` already call), writing an `access.denied` row on an
isolated session so it survives the 403's rolled-back transaction:

```python
record_decision(
    user=current_user,
    action=f"screen:{screen_code}:{min_level}",   # the screen + the action attempted
    outcome="denied",
    resource_type="screen",
    resource_id=screen_code,
    reason=f"insufficient_screen_level:{resolved.level or 'none'}",  # the RESOLVED level
)
```
That one call carries every field AC-12 requires: acting user
(`actor_user_id`), screen (`resource_id`), action attempted (`action`),
resolved level (`reason`), denial outcome (`access.denied`). **No change to
`app/core/audit.py` or `app/core/authz.py` is required.**

### The screen-access resolver — `backend/app/core/screen_access.py`

Single module, single algorithm. Nothing else in the codebase resolves screen
access (FR-6, FR-15).

```python
# backend/app/core/screen_access.py

ACTION_LEVELS: tuple[str, ...] = ("VIEW", "ADD", "EDIT", "DELETE")
LEVEL_RANK: dict[str, int] = {"VIEW": 1, "ADD": 2, "EDIT": 3, "DELETE": 4}

REASONS = ("granted", "no_grant", "screen_not_found", "org_unit_not_found")

@dataclass(frozen=True)
class ResolvedScreenAccess:
    screen_code: str
    screen_id: str | None            # None when the screen code is unknown
    org_unit_id: str | None          # the unit the check was scoped to (None = the caller's own grant units)
    level: str | None                # None == below VIEW (no access)
    rank: int                        # 0 when level is None
    role_ids: tuple[str, ...]        # the grants that produced `level` ( () when denied )
    reason: str

def resolve_screen_access(
    db: Session,
    *,
    user: User,
    screen_code: str,
    org_unit_id: str | None = None,
    at: datetime | None = None,
) -> ResolvedScreenAccess: ...

def resolve_all_screen_access(
    db: Session,
    *,
    user: User,
    org_unit_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, ResolvedScreenAccess]:
    """Every screen the user resolves to >= VIEW, keyed by screen_code.
    One batched pass with the IDENTICAL predicate as resolve_screen_access —
    the menu tree and /screen-access/me call this; per-screen and batched
    results are asserted equal in test_screen_access_resolver.py (FR-15)."""

def assert_screen_level(
    db: Session,
    *,
    user: User,
    screen_code: str,
    min_level: str,
    org_unit_id: str | None = None,
    at: datetime | None = None,
) -> ResolvedScreenAccess:
    """resolve_screen_access, but records an FR-28 denial via
    app.core.authz.record_decision and raises HTTPException(403, ...) when
    `rank` is below LEVEL_RANK[min_level]."""
```

`resolve_screen_access` algorithm (exact, so every consumer behaves identically
— FR-15):

1. Load `Screen` by `code == screen_code`, `deleted_at IS NULL`. Missing →
   `level=None, reason="screen_not_found"` (the screen catalog is
   platform-wide, so no `org_id` filter applies here — FR-24).
2. `grants = org_access.active_grants_for_user(db, user_id=user.id,
   org_id=user.org_id, at=at)` — **reused verbatim**, so a revoked or expired
   role grant confers no screen access either.
   `user_role_ids = {g.role_id for g in grants}`. Empty → `level=None,
   reason="no_grant"`.
3. Determine the **context units**:
   - `org_unit_id` given → load it org-scoped and non-deleted; missing →
     `reason="org_unit_not_found"`. `context_units = [org_unit_id]`.
   - `org_unit_id` omitted → `context_units = sorted({g.org_unit_id for g in
     grants})` — every unit where the user actually holds a role.
4. `candidate_units = set()`; for each `u` in `context_units`:
   `candidate_units |= set(org_access.ancestor_unit_ids(db, org_id=user.org_id,
   org_unit_id=u, include_self=True))` — **the reused ancestor walk**. Record
   `exact_units = set(context_units)` separately.
5. Query `RoleScreenAccess` where `org_id == user.org_id` (AC-13),
   `screen_id == screen.id`, `role_id IN user_role_ids`,
   `deleted_at IS NULL`, and
   (`org_unit_id IS NULL` **or** `org_unit_id IN candidate_units`).
6. Keep a row if **any** of:
   - `row.org_unit_id IS NULL` (organization-wide grant — always applies), or
   - `row.org_unit_id in exact_units` (granted exactly at a context unit), or
   - `row.role.allows_hierarchy_rollup` **and** `row.org_unit_id in
     candidate_units` (granted at an **ancestor** — rolls DOWN only, never up,
     never lateral; gated on the same role flag `org_access.resolve_access`
     step 3 uses, which is what "identically" in FR-18 means).
7. `level` = the code of the **highest** `rank` among the kept rows
   (FR-19: highest wins; a narrower descendant grant can never reduce a
   broader ancestor grant, because this is a MAX over a union, never an
   override). `role_ids` = the roles of every row at that winning rank.
   No kept rows → `level=None, reason="no_grant"`.
8. Holding `level` implies holding every lower level (FR-4): callers compare
   `resolved.rank >= LEVEL_RANK[min_level]`. `"VIEW and DELETE but not EDIT"`
   is unrepresentable because a grant stores exactly one
   `max_action_level_id` (FR-5).

Consistent-snapshot behavior: the whole resolution runs on the caller's single
`Session`, like feature 002's resolver — never a mix of pre- and
post-revocation state within one request (spec edge case: in-flight requests
are evaluated against the state at the time the API processes them).

**Ethical walls are unaffected.** `accessible_contract_filter` /
`app/walls/` continue to run after this check and continue to override an
ALLOW, exactly as they do for `org_access.resolve_access` (spec "Out of
scope", edge cases). No wall code is in any agent's path mapping.

### The route dependency — `backend/app/core/deps.py`

Declared next to `require_permission`, same factory shape, same file:

```python
def require_screen_level(screen_code: str, min_level: str):
    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> "ResolvedScreenAccess":
        from app.core import screen_access
        return screen_access.assert_screen_level(
            db, user=current_user, screen_code=screen_code, min_level=min_level
        )

    return dependency
```

(The import is function-local to keep `deps.py` free of a new module-level
dependency on `screen_access` → `menu_security.models`, mirroring how
`record_decision` is already imported lazily in this file.)

Representative route — `backend/app/contracts/routes.py`, exactly as it will
read after the edit (FR-12: both gates, neither replacing the other; FR-11:
visibly present in the signature):

```python
_CONTRACTS_ADD = require_screen_level("contracts", "ADD")      # module-level, next to the router

@router.post("/{contract_id}/parties", response_model=ContractPartyResponse,
             status_code=status.HTTP_201_CREATED)
def add_party(
    contract_id: str,
    payload: ContractPartyCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:update")),
    _screen=Depends(_CONTRACTS_ADD),
):
    return add_contract_party(db, contract_id=contract_id, user=current_user, payload=payload)
```

`_screen` is an unused-by-the-body dependency (the same idiom the codebase
already uses for `current_user` on routes that don't read it); ruff does not
flag a leading-underscore parameter.

### Tranche-1 retrofit — the exact route list (FR-17)

Every route below gains `_screen=Depends(<constant>)`. Its existing
`require_permission(...)` is **unchanged** (FR-12). Module-level constants are
declared once per file next to the router, in the style
`backend/app/notices/routes.py` and `backend/app/intake/routes.py` already use
for `_READ`/`_CREATE`/`_UPDATE`.

Some routes use a mutating HTTP verb for a **read-shaped** action (recompute a
score, run an AI extraction/suggestion, probe an integration). Those are
gated at **VIEW**, not at an add/edit level, so that FR-26's "no day-one
access change" holds for a role seeded at VIEW that can call them today.

**`backend/app/contracts/routes.py`** — screen `contracts`
(`_CONTRACTS_VIEW/_ADD/_EDIT/_DELETE`)

| Method | Path | Existing permission | min_level |
|---|---|---|---|
| POST | `/contracts/upload` | `contract:create` | ADD |
| POST | `/contracts/{contract_id}/risk` | `contract:read` | VIEW |
| PATCH | `/contracts/{contract_id}` | `contract:update` | EDIT |
| POST | `/contracts/{contract_id}/lifecycle` | `contract:update` | EDIT |
| POST | `/contracts/{contract_id}/parties` | `contract:update` | ADD |
| DELETE | `/contracts/{contract_id}/parties/{party_id}` | `contract:update` | DELETE |

**`backend/app/matters/routes.py`** — screen `matters`. (This one router object
is mounted twice in `main.py`, at `/matters` and at the legacy `/projects`
prefix; one edit covers both mounts.)

| Method | Path | Existing permission | min_level |
|---|---|---|---|
| POST | `""` | `project:create` | ADD |
| PATCH | `/{matter_id}` | `project:update` | EDIT |
| DELETE | `/{matter_id}` | `project:delete` | DELETE |
| POST | `/{matter_id}/items` | `project:update` | EDIT |
| POST | `/{matter_id}/folders` | `project:update` | ADD |
| PATCH | `/{matter_id}/folders/{folder_id}` | `project:update` | EDIT |
| DELETE | `/{matter_id}/folders/{folder_id}` | `project:update` | DELETE |
| PUT | `/{matter_id}/members` | `project:share` | EDIT |
| DELETE | `/{matter_id}/members/{user_id}` | `project:share` | DELETE |
| POST | `/{matter_id}/shares` | `project:share` | ADD |
| POST | `/{matter_id}/shares/{share_id}/revoke` | `project:share` | DELETE |
| PUT | `/{matter_id}/contracts` | `project:update` | ADD |
| PATCH | `/{matter_id}/contracts/{contract_id}` | `project:update` | EDIT |
| DELETE | `/{matter_id}/contracts/{contract_id}` | `project:update` | DELETE |

**`backend/app/trademarks/routes.py`** — screen `trademarks`

| Method | Path | Existing permission | min_level |
|---|---|---|---|
| POST | `/trademarks` | `trademark:create` | ADD |
| POST | `/trademarks/intake` | `trademark:create` | ADD |
| PATCH | `/trademarks/{trademark_id}` | `trademark:update` | EDIT |
| POST | `/trademarks/search-similar` | `trademark:search` | VIEW |
| POST | `/trademarks/documents/upload` | `trademark:extract` | ADD |
| POST | `/trademarks/documents/extract` | `trademark:extract` | VIEW |
| POST | `/trademarks/documents/ingest` | `trademark:extract` | ADD |
| POST | `/trademarks/integrations/test/{service_name}` | `trademark:integrations_manage` | VIEW |

**`backend/app/notices/routes.py`** — screen `notices`

| Method | Path | Existing permission | min_level |
|---|---|---|---|
| POST | `/notices` | `notice:create` | ADD |
| POST | `/notices/extract` | `notice:create` | ADD |
| POST | `/notices/run-reminders` | `notice:update` | EDIT |
| PATCH | `/notices/{notice_id}` | `notice:update` | EDIT |
| POST | `/notices/{notice_id}/status` | `notice:update` | EDIT |
| POST | `/notices/{notice_id}/draft-response` | `notice:update` | EDIT |
| POST | `/notices/{notice_id}/escalate` | `notice:update` | EDIT |
| POST | `/notices/{notice_id}/notes` | `notice:update` | ADD |
| POST | `/notices/{notice_id}/documents` | `notice:update` | ADD |
| DELETE | `/notices/{notice_id}/documents/{document_id}` | `notice:update` | DELETE |
| DELETE | `/notices/{notice_id}` | `admin_panel:access` | DELETE |

**`backend/app/intake/routes.py`** — screen `intake`

| Method | Path | Existing permission | min_level |
|---|---|---|---|
| POST | `/intake/requests` | `intake:create` | ADD |
| PATCH | `/intake/requests/{request_id}` | `intake:update` | EDIT |
| POST | `/intake/requests/{request_id}/triage` | `intake:update` | EDIT |
| POST | `/intake/requests/{request_id}/suggest-flow` | `intake:update` | VIEW |
| POST | `/intake/requests/{request_id}/submit-for-approval` | `intake:read` | EDIT |
| POST | `/intake/requests/{request_id}/gates` | `intake:update` | EDIT |
| POST | `/intake/requests/{request_id}/handoff` | `intake:update` | ADD |
| POST | `/intake/requests/{request_id}/tasks` | `intake:update` | ADD |
| PATCH | `/intake/tasks/{task_id}` | `intake:update` | EDIT |
| DELETE | `/intake/tasks/{task_id}` | `intake:update` | DELETE |
| POST | `/intake/tasks/{task_id}/effort` | `intake:update` | EDIT |
| POST | `/intake/requests/{request_id}/promote` | `intake:update` | EDIT |
| POST | `/intake/requests/{request_id}/draft-contract` | `intake:read` | EDIT |
| POST | `/intake/requests/{request_id}/ingest-attachment` | `intake:read` | EDIT |
| POST | `/intake/requests/{request_id}/pause` | `intake:update` | EDIT |
| POST | `/intake/requests/{request_id}/screen` | `intake:update` | EDIT |
| POST | `/intake/requests/{request_id}/documents` | `intake:create` | ADD |
| PUT | `/intake/requests/{request_id}/parties` | `intake:read` | EDIT |
| POST | `/intake/copilot/turn` | `intake:create` | VIEW |
| POST | `/intake/copilot/file` | `intake:create` | ADD |

**Explicitly NOT gated inside the intake router**, and recorded here as part of
FR-17's tracked deferral rather than a silent gap:

- `/intake/email-webhook`, `/intake/teams-webhook` — unauthenticated inbound
  webhooks. There is no authenticated caller to resolve, so FR-10 (which is
  defined on "the authenticated caller") cannot apply.
- `/intake/request-types/*`, `/intake/teams/*`, `/intake/routing-rules/*`,
  `/intake/kb/*`, `/intake/mailbox/poll`, `/intake/gmail-sync`,
  `/intake/sanctions/refresh`, `/intake/sla-scan` — these are gated on
  `admin_panel:access` and are administered from the **`admin`** screen, which
  is not in the FR-17 tranche. They belong to the deferred retrofit.

### Database schema

Four new tables. `screen`, `action_level` and `menu_item` are the
**platform-wide structural catalog** and are therefore **not** org-scoped —
FR-24: *"No per-organization scoping applies (the inventory of page routes is
shared across the platform; only the grants against it are org-scoped)."*
Only `role_screen_access` carries `org_id`.

All four use `TableNameMixin, IdMixin, ActorTrackedMixin, SoftDeleteMixin,
TimestampMixin` (the full audit/soft-delete convention noted in spec.md's
planning context); `role_screen_access` adds `OrgScopedMixin`.

**`screen`** (`Screen`)

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK, default `new_uuid()` |
| code | varchar(80) | NOT NULL, UNIQUE, indexed |
| name | varchar(160) | NOT NULL |
| module | varchar(80) | NOT NULL |
| route_path | varchar(200) | NOT NULL, UNIQUE, indexed (FR-1's 1:1 with a Next.js page route) |
| created_at / updated_at | timestamptz | NOT NULL |
| created_by_user_id / updated_by_user_id | varchar(36) | NULL |
| deleted_at / deleted_by_user_id | timestamptz / varchar(36) | NULL |
| legal_hold | boolean | NOT NULL default false |

**`action_level`** (`ActionLevel`) — small seeded reference table, 4 rows, never
written at runtime.

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| code | varchar(10) | NOT NULL, UNIQUE — `CHECK code IN ('VIEW','ADD','EDIT','DELETE')` (`ck_action_level_code`) |
| rank | integer | NOT NULL, UNIQUE — `CHECK rank BETWEEN 1 AND 4` (`ck_action_level_rank`) |
| (audit/soft-delete/timestamp columns as above) | | |

The `CHECK` + `UNIQUE` pair is what makes the spec's "attempting to configure a
level outside the four defined levels SHALL be rejected" true in the DB as well
as in the Pydantic `Literal["VIEW","ADD","EDIT","DELETE"]`.

**`menu_item`** (`MenuItem`) — named `menu_item`, not `menu`: grepped
`backend/app/**` and `frontend/src/**`; no existing table, model, TS interface
or endpoint uses `menu`/`menu_item` (the only hit is an unrelated docstring in
`app/intake/flow_agent.py`), so there is no collision either way, and
`menu_item` reads better against the `MenuItem` class name `TableNameMixin`
derives.

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| parent_id | varchar(36) | NULL, FK `menu_item.id` ON DELETE RESTRICT, indexed |
| label | varchar(160) | NOT NULL |
| icon | text | NULL — the raw SVG path string today's rail renders |
| sequence_order | integer | NOT NULL default 0 |
| menu_type | varchar(20) | NOT NULL, `CHECK menu_type IN ('group','screen_link')` (`ck_menu_item_menu_type`) |
| screen_id | varchar(36) | NULL, FK `screen.id` ON DELETE RESTRICT, indexed |
| (audit/soft-delete/timestamp columns as above) | | |

- `ck_menu_item_screen_link`: `CHECK ((menu_type = 'screen_link' AND screen_id
  IS NOT NULL) OR (menu_type = 'group' AND screen_id IS NULL))` — FR-2's
  "either a grouping node or a node linking to exactly one screen", enforced in
  the DB.
- `ck_menu_item_no_self_parent`: `CHECK (parent_id IS NULL OR parent_id <> id)`.
- `ix_menu_item_parent_seq` on `(parent_id, sequence_order)` — the tree build.

**`role_screen_access`** (`RoleScreenAccess`) — the only org-scoped table.

| Column | Type | Constraints |
|---|---|---|
| id | varchar(36) | PK |
| org_id | varchar(36) | NOT NULL, indexed (`OrgScopedMixin`) |
| role_id | varchar(36) | NOT NULL, FK `role.id` ON DELETE CASCADE, indexed |
| screen_id | varchar(36) | NOT NULL, FK `screen.id` ON DELETE CASCADE, indexed |
| org_unit_id | varchar(36) | NULL, FK `org_unit.id` ON DELETE RESTRICT, indexed (NULL = organization-wide, FR-18's "optionally scoped") |
| max_action_level_id | varchar(36) | NOT NULL, FK `action_level.id` ON DELETE RESTRICT, indexed |
| created_at / updated_at | timestamptz | NOT NULL |
| created_by_user_id / updated_by_user_id | varchar(36) | NULL (FR-27's actor) |
| deleted_at / deleted_by_user_id | timestamptz / varchar(36) | NULL — **revocation IS the soft delete**, as with feature 002's `UserRoleGrant` |
| legal_hold | boolean | NOT NULL default false |

Indexes / constraints:
- `uq_role_screen_access_scope`: UNIQUE on `(role_id, screen_id, org_unit_id)`
  WHERE `deleted_at IS NULL` (`postgresql_where` + `sqlite_where`).
- `uq_role_screen_access_orgwide`: UNIQUE on `(role_id, screen_id)` WHERE
  `org_unit_id IS NULL AND deleted_at IS NULL`. **Both indexes are required**:
  Postgres treats NULLs as distinct in a unique index, so the first index alone
  would not stop two live organization-wide grants for the same (role, screen).
- `ix_role_screen_access_lookup` on `(org_id, screen_id, role_id, deleted_at)`
  — the resolver's hot path (step 5).
- `ix_role_screen_access_org_unit` on `(org_unit_id)`.

**Migration**: revision ID `0043_menu_screen_security` (24 chars ≤ 32) on the
current single head **`0042_org_hierarchy_rbac`** (verified by walking every
`down_revision` in `backend/alembic/versions/`: `0042_org_hierarchy_rbac` is
the only head). Steps, in order, inside one revision:

1. `op.create_table("action_level", ...)` + CHECKs; `op.bulk_insert` the four
   rows `(VIEW,1) (ADD,2) (EDIT,3) (DELETE,4)` with fresh UUIDs.
2. `op.create_table("screen", ...)` + indexes; `op.bulk_insert` the **39
   screen rows** from the catalog table below.
3. `op.create_table("menu_item", ...)` + indexes/CHECKs; `op.bulk_insert` the
   4 group rows + 24 screen-link rows from the menu-seed table below.
4. `op.create_table("role_screen_access", ...)` + the two partial unique
   indexes and the two lookup indexes.
5. **FR-26 cutover backfill**, in Python on `op.get_bind()` — dialect-agnostic
   raw SQL, no import of app code (migrations must not drift with the models):
   ```
   levels   = {code: id}   from action_level
   screens  = {code: id}   from screen
   for each row in role (every org's every role):
       perms = SELECT p.value FROM role_permission rp
                 JOIN permission p ON p.id = rp.permission_id
                WHERE rp.role_id = role.id
       for screen_code, (read_perms, write_perms) in SEED_MAP.items():
           if write_perms is ALL:      level = 'DELETE'          # ungated screen
           elif perms & write_perms:   level = 'DELETE'
           elif perms & read_perms:    level = 'VIEW'
           else:                       continue                   # no grant (AC-16 third role)
           INSERT role_screen_access(id=uuid4(), org_id=role.org_id,
                                     role_id=role.id,
                                     screen_id=screens[screen_code],
                                     org_unit_id=NULL,            # organization-wide
                                     max_action_level_id=levels[level],
                                     created_at=now, updated_at=now,
                                     legal_hold=false)
   # FR-25 bootstrap: the built-in admin role's non-revocable grant
   for each role WHERE name = 'admin':
       UPSERT that role's row for screen 'screen_access' to max_action_level_id = levels['DELETE']
   ```
   `SEED_MAP` is embedded as a literal dict in the migration file (a frozen
   snapshot of the pre-cutover permission model — it must NOT import
   `app.core.rbac`, whose constants will keep evolving).

`downgrade()` (working, in reverse): `op.drop_table("role_screen_access")`,
`op.drop_table("menu_item")`, `op.drop_table("screen")`,
`op.drop_table("action_level")` — each preceded by its index drops. Fully
reversible; nothing outside these four tables is altered, so unlike feature
002's downgrade this one is lossless for pre-existing data.

No pgvector involvement.

#### Screen catalog (39 rows) + FR-26 seed map

`route_path` is the literal Next.js route (FR-1, one-to-one). `read` / `write`
are the permission-string sets used by the FR-26 backfill: **write → seed
DELETE, else read → seed VIEW, else no grant**. `write = ALL` means the page
has no permission gate today, so every role can already do everything on it and
is seeded DELETE to preserve that exactly.

| # | code | name | module | route_path | read perms | write perms |
|---|---|---|---|---|---|---|
| 1 | `home` | Ask Aegis | Intelligence | `/` | — | ALL |
| 2 | `my_work` | My Work | Workspace | `/my-work` | — | ALL |
| 3 | `notifications` | Notifications | Workspace | `/notifications` | — | ALL |
| 4 | `search` | Search | Workspace | `/search` | — | ALL |
| 5 | `prompts` | Prompt Library | Intelligence | `/prompts` | — | ALL |
| 6 | `assistant` | Assistant | Intelligence | `/assistant` | `assistant:use` | `assistant:use_ai_tools` |
| 7 | `contract_brain` | Contract Brain | Intelligence | `/brain` | `contract:read` | — |
| 8 | `tabular_reviews` | Tabular Review | Intelligence | `/tabular-reviews` | `contract:read` | `contract:update` |
| 9 | `tabular_review_detail` | Tabular Review detail | Intelligence | `/tabular-reviews/[id]` | `contract:read` | `contract:update` |
| 10 | `playbooks` | Playbooks | Intelligence | `/playbooks` | `playbook:read` | `playbook:create`, `playbook:update`, `playbook:delete`, `playbook:run`, `playbook:publish` |
| 11 | `playbook_detail` | Playbook detail | Intelligence | `/playbooks/[id]` | (same as 10) | (same as 10) |
| 12 | `playbook_builder` | Playbook builder | Intelligence | `/playbooks/build` | (same as 10) | (same as 10) |
| 13 | `contracts` | Contracts | Workspace | `/contracts` | `contract:read` | `contract:create`, `contract:update` |
| 14 | `contract_detail` | Contract detail | Workspace | `/contracts/[id]` | (same as 13) | (same as 13) |
| 15 | `matters` | Matters | Workspace | `/matters` | `project:read` | `project:create`, `project:update`, `project:delete`, `project:share` |
| 16 | `matter_detail` | Matter detail | Workspace | `/matters/[id]` | (same as 15) | (same as 15) |
| 17 | `intake` | Legal Intake | Workspace | `/intake` | `intake:read` | `intake:create`, `intake:update` |
| 18 | `delegations` | Delegations | Workspace | `/delegations` | `delegation:manage` | `delegation:manage` |
| 19 | `approvals` | Approvals | Lifecycle | `/approvals` | `approval:read` | `approval:decide`, `approval:admin` |
| 20 | `signatures` | Signatures | Lifecycle | `/signatures` | `contract:read` | `contract:sign` |
| 21 | `obligations` | Obligations | Lifecycle | `/obligations` | `obligation:read` | `obligation:update` |
| 22 | `sla` | SLA | Lifecycle | `/sla` | `intake:read` | `intake:update` |
| 23 | `notices` | Notices | Lifecycle | `/notices` | `notice:read` | `notice:create`, `notice:update`, `admin_panel:access` |
| 24 | `notice_detail` | Notice detail | Lifecycle | `/notices/[id]` | (same as 23) | (same as 23) |
| 25 | `renewals` | Renewals | Lifecycle | `/renewals` | `contract:read` | `contract:renew` |
| 26 | `trademarks` | Trademarks | Workspace | `/trademarks` | `trademark:read`, `trademark:search`, `trademark:integrations_manage` | `trademark:create`, `trademark:update`, `trademark:extract` |
| 27 | `trademark_detail` | Trademark detail | Workspace | `/trademarks/[id]` | (same as 26) | (same as 26) |
| 28 | `trademark_calendar` | Trademark calendar | Workspace | `/trademarks/calendar` | (same as 26) | (same as 26) |
| 29 | `trademark_intake` | Trademark intake | Workspace | `/trademarks/intake` | (same as 26) | (same as 26) |
| 30 | `trademark_extract` | Trademark extract | Workspace | `/trademarks/extract` | (same as 26) | (same as 26) |
| 31 | `trademark_integrations` | Trademark integrations | Workspace | `/trademarks/integrations` | (same as 26) | (same as 26) |
| 32 | `trademark_reports` | Trademark reports | Workspace | `/trademarks/reports` | (same as 26) | (same as 26) |
| 33 | `workflow_builder` | Workflows | Admin | `/workflow-builder` | `workflow:read` | `workflow:create`, `workflow:update`, `workflow:share` |
| 34 | `workflow_builder_detail` | Workflow detail | Admin | `/workflow-builder/[id]` | (same as 33) | (same as 33) |
| 35 | `ai_usage` | AI Usage & Cost | Admin | `/ai-usage` | `admin_panel:access` | `admin_panel:access` |
| 36 | `jobs` | Background Jobs | Admin | `/jobs` | `admin_panel:access` | `admin_panel:access` |
| 37 | `admin` | Roles & teams | Admin | `/admin` | `admin_panel:access` | `admin_panel:access` |
| 38 | `org_structure` | Org structure | Admin | `/org-structure` | `admin_panel:access` | `admin_panel:access` |
| 39 | `screen_access` | Screen access | Admin | `/screen-access` | `admin_panel:access` | `admin_panel:access` |

`is_enforced` (a derived response field, not a column) is `true` for
`contracts`, `matters`, `trademarks`, `notices`, `intake` — the FR-17 tranche —
and `false` for all others; the service computes it from a
`TRANCHE_1_SCREEN_CODES` frozenset in `app/menu_security/service.py`.

Screens 6, 7, 9, 11, 12, 14, 16, 24, 26–32, 34 have **no menu node** — they are
sub-pages, detail routes, or (for `assistant` / the trademarks area) pages the
current rail already does not link. Per FR-3 and the spec's deep-link edge
case, they are still full screens: seeded, resolved, and gated on direct
navigation (FR-14). This preserves today's navigation exactly (FR-20/FR-21 are
about the *source* of the nav, not about adding links).

#### Menu tree seed (28 rows) — mirrors today's `GROUPS` array exactly

`icon` is the **verbatim SVG path string** copied out of the current
`aegis-rail.tsx` literal for that item, so the migration to a server-driven
rail is pixel-identical.

| parent | label | menu_type | screen_code | sequence_order |
|---|---|---|---|---|
| — | Workspace | group | — | 10 |
| Workspace | Legal Intake | screen_link | `intake` | 10 |
| Workspace | My Work | screen_link | `my_work` | 20 |
| Workspace | Notifications | screen_link | `notifications` | 30 |
| Workspace | Contracts | screen_link | `contracts` | 40 |
| Workspace | Matters | screen_link | `matters` | 50 |
| Workspace | Search | screen_link | `search` | 60 |
| Workspace | Delegations | screen_link | `delegations` | 70 |
| — | Intelligence | group | — | 20 |
| Intelligence | Ask Aegis | screen_link | `home` | 10 |
| Intelligence | Contract Brain | screen_link | `contract_brain` | 20 |
| Intelligence | Tabular Review | screen_link | `tabular_reviews` | 30 |
| Intelligence | Playbooks | screen_link | `playbooks` | 40 |
| Intelligence | Prompt Library | screen_link | `prompts` | 50 |
| — | Lifecycle | group | — | 30 |
| Lifecycle | Approvals | screen_link | `approvals` | 10 |
| Lifecycle | Signatures | screen_link | `signatures` | 20 |
| Lifecycle | Obligations | screen_link | `obligations` | 30 |
| Lifecycle | SLA | screen_link | `sla` | 40 |
| Lifecycle | Notices | screen_link | `notices` | 50 |
| Lifecycle | Renewals | screen_link | `renewals` | 60 |
| — | Admin | group | — | 40 |
| Admin | AI Usage & Cost | screen_link | `ai_usage` | 10 |
| Admin | Background Jobs | screen_link | `jobs` | 20 |
| Admin | Workflows | screen_link | `workflow_builder` | 30 |
| Admin | Roles & teams | screen_link | `admin` | 40 |
| Admin | Org structure | screen_link | `org_structure` | 50 |
| Admin | Screen access | screen_link | `screen_access` | 60 |

The only *new* item is "Screen access" — FR-23's admin screen has to be
reachable. Its icon is a lucide-style shield-check path added in the migration.

### Frontend TypeScript models (added to `src/lib/types.ts`)

```ts
// Mirrors the API contract exactly — copy verbatim, do not "improve".
export type ActionLevelCode = "VIEW" | "ADD" | "EDIT" | "DELETE";
export type MenuNodeType = "group" | "screen_link";

export interface ActionLevelResponse {
  id: ID;
  code: ActionLevelCode;
  rank: number;
}

export interface MenuNode {
  id: ID;
  parent_id: ID | null;
  label: string;
  icon: string | null;
  menu_type: MenuNodeType;
  sequence_order: number;
  screen_id: ID | null;
  screen_code: string | null;
  route_path: string | null;
  action_level: ActionLevelCode | null;
  children: MenuNode[];
}

export interface MenuTreeResponse {
  org_unit_id: ID | null;
  nodes: MenuNode[];
}

export interface ScreenAccessEntry {
  screen_id: ID;
  screen_code: string;
  route_path: string;
  action_level: ActionLevelCode;
  rank: number;
}

export interface MyScreenAccessResponse {
  org_unit_id: ID | null;
  screens: ScreenAccessEntry[];
}

export interface ScreenResponse {
  id: ID;
  code: string;
  name: string;
  module: string;
  route_path: string;
  is_enforced: boolean;
}

export interface ScreenGrantResponse {
  id: ID;
  org_id: ID;
  role_id: ID;
  role_name: string;
  role_is_builtin_admin: boolean;
  allows_hierarchy_rollup: boolean;
  screen_id: ID;
  screen_code: string;
  screen_name: string;
  org_unit_id: ID | null;
  org_unit_name: string | null;
  max_action_level_id: ID;
  max_action_level: ActionLevelCode;
  max_action_rank: number;
  is_locked: boolean;
  is_active: boolean;
  revoked_at: string | null;
  revoked_by_user_id: ID | null;
  created_at: string;
  created_by_user_id: ID | null;
  updated_at: string;
  updated_by_user_id: ID | null;
}
```

### Endpoint client additions (`src/lib/endpoints.ts`)

- `menuApi.tree(params?: { org_unit_id?: ID }) → Promise<MenuTreeResponse>` — GET `/menu-tree`
- `menuApi.myScreenAccess(params?: { screen_code?: string; org_unit_id?: ID }) → Promise<MyScreenAccessResponse>` — GET `/screen-access/me`
- `menuApi.actionLevels() → Promise<ActionLevelResponse[]>` — GET `/action-levels`
- `screensApi.list(params?: { module?: string }) → Promise<ScreenResponse[]>` — GET `/screens`
- `screenAccessApi.list(params?: { role_id?: ID; screen_id?: ID; org_unit_id?: ID; include_revoked?: boolean }) → Promise<ScreenGrantResponse[]>` — GET `/screen-access/grants`
- `screenAccessApi.create(payload: { role_id: ID; screen_id: ID; org_unit_id?: ID | null; action_level: ActionLevelCode }) → Promise<ScreenGrantResponse>` — POST `/screen-access/grants`
- `screenAccessApi.update(id: ID, payload: { action_level: ActionLevelCode }) → Promise<ScreenGrantResponse>` — PATCH `/screen-access/grants/{id}`
- `screenAccessApi.revoke(id: ID) → Promise<void>` — DELETE `/screen-access/grants/{id}`

All built on the existing `apiFetch` + `qs` helpers in that file, matching the
`orgUnitsApi` / `roleGrantsApi` blocks added by feature 002.

## Component design

### Backend

- **Models**: `backend/app/menu_security/models.py` — `Screen`, `ActionLevel`,
  `MenuItem`, `RoleScreenAccess`. `RoleScreenAccess` declares
  `role = relationship("Role", lazy="joined")` (the resolver reads
  `role.allows_hierarchy_rollup` on every candidate row) and
  `action_level = relationship("ActionLevel", lazy="joined")`. Its FKs to
  `role.id` and `org_unit.id` are declared **by string**, so this module does
  not import `app.auth.models` or `app.org_structure.models` and no import
  cycle is created. `backend/app/models.py` gains the four imports and the four
  `__all__` entries (names must actually exist — ruff F822).
- **Schemas**: `backend/app/menu_security/schemas.py` — `ActionLevelResponse`,
  `ScreenResponse`, `MenuNode` (self-referencing; `model_rebuild()` after the
  class body), `MenuTreeResponse`, `ScreenAccessEntry`,
  `MyScreenAccessResponse`, `ScreenGrantCreate`, `ScreenGrantUpdate`,
  `ScreenGrantResponse`. `action_level` fields are
  `Literal["VIEW","ADD","EDIT","DELETE"]` so an out-of-range level is a 422 at
  the edge (spec edge case).
- **Access helpers**: `backend/app/menu_security/access.py` —
  `get_screen_or_404(db, screen_id=None, code=None)` (platform-wide, no org
  filter — FR-24), `get_action_level_or_422(db, code)`,
  `get_grant_or_404(db, org_id, grant_id)` (**org-filtered**),
  `get_org_role_or_404(db, org_id, role_id)` (**org-filtered** — AC-13/AC-14's
  "no role from another organization is ever visible"). Org-unit lookups reuse
  the existing `app.org_structure.access.get_org_unit_or_404`.
- **Service**: `backend/app/menu_security/service.py` — org scoping and audit
  noted per function:
  - `list_action_levels(db)` — reference data, no org scope, no audit.
  - `list_screens(db, *, module=None)` — platform catalog, no org scope;
    computes `is_enforced` from `TRANCHE_1_SCREEN_CODES`.
  - `get_menu_tree(db, *, actor, org_unit_id=None)` — calls
    `screen_access.resolve_all_screen_access(db, user=actor,
    org_unit_id=org_unit_id)` **once**, loads all `menu_item` rows
    (`deleted_at IS NULL`, ordered by `parent_id, sequence_order`), builds the
    tree, then prunes bottom-up: a `screen_link` node survives iff its screen
    appears in the resolution map (FR-7); a `group` node survives iff ≥1 child
    survives after pruning (FR-8, recursive so an empty grandparent also
    disappears). No audit (a read on every page load).
  - `get_my_screen_access(db, *, actor, screen_code=None, org_unit_id=None)` —
    the same `resolve_all_screen_access` call (or the single-screen
    `resolve_screen_access` when `screen_code` is given). **Always resolves for
    `actor` only** — there is no `user_id` parameter anywhere in this module,
    which is how "a user can never query another user's resolved access" is
    made structurally impossible rather than merely checked.
  - `list_screen_grants(db, *, actor, role_id, screen_id, org_unit_id,
    include_revoked)` — `RoleScreenAccess.org_id == actor.org_id` on every
    query; joins `Role` (also org-filtered) for `role_name`; computes
    `is_locked`.
  - `create_screen_grant(db, *, actor, payload)` — validates role/org-unit are
    in `actor.org_id` (404 otherwise), screen exists (404), level valid (422),
    no live duplicate `(role, screen, org_unit)` (409); writes the row with
    `created_by_user_id = actor.id`; audit `screen_access.granted`.
  - `update_screen_grant(db, *, actor, grant_id, payload)` — org-filtered
    fetch; **`_assert_bootstrap_grant_not_weakened(...)`**; sets
    `updated_by_user_id`; audit `screen_access.updated` with before/after
    levels (AC-11).
  - `revoke_screen_grant(db, *, actor, grant_id)` — org-filtered fetch; 409 if
    already revoked; **`_assert_bootstrap_grant_not_weakened(...)`**; sets
    `deleted_at` **and** `deleted_by_user_id = actor.id`; audit
    `screen_access.revoked` with `metadata={"revocation": True}`.
  - `_assert_bootstrap_grant_not_weakened(db, *, grant, new_level_rank | None)`
    — **the FR-25 lock.** Raises
    `HTTPException(409, "The built-in admin role's DELETE access to the screen-access
    management screen cannot be reduced or revoked")` when
    `grant.role.name == ADMIN_ROLE_NAME` (imported from `app.core.rbac`) **and**
    `grant.screen.code == "screen_access"` **and** (`new_level_rank is None`
    i.e. a revoke, **or** `new_level_rank < 4`). Because it lives in the single
    service path that both the PATCH and the DELETE endpoint funnel through, it
    holds "through which surface" (AC-15) for the API as well as the UI.
  - `is_locked` on the response is computed by the same predicate, so the UI
    disables exactly what the server refuses.
- **Resolver**: `backend/app/core/screen_access.py` (signatures + algorithm
  frozen above).
- **Router**: `backend/app/menu_security/routes.py` — three thin `APIRouter`s,
  no business logic: `menu_router` (prefix `/menu-tree`, tag `menu`),
  `screens_router` (prefix `/screens`, tag `screens`) — which also carries
  `/action-levels` as its own `APIRouter` `action_levels_router` for a clean
  path — and `screen_access_router` (prefix `/screen-access`, tag
  `screen-access`). Permissions exactly per the contract table. Registered in
  `backend/app/main.py` immediately after `delegations_router`.
- **Chokepoint edit**: `backend/app/core/deps.py` — add `require_screen_level`
  (shape frozen above). `require_permission` is **not modified**.
- **Permission catalog edit**: `backend/app/core/rbac.py` — add
  `MENU_SECURITY_PERMISSIONS = {"menu:read", "screen_access:read",
  "screen_access:manage"}`, fold into `ALL_PERMISSIONS`, add `"menu:read"` to
  all four `DEFAULT_ROLE_PERMISSIONS` entries (admin gets the other two via
  `set(ALL_PERMISSIONS)`).
- **Celery**: none. Resolution is evaluated per request (FR-10, FR-21); there
  is nothing to sweep or schedule.
- **Settings**: none. No external service, no `MOCK_<NAME>` flag, no
  `validate_runtime_settings()` entry, so
  `tests/test_phase10_security_hardening.py::test_runtime_settings_accepts_a_correctly_locked_down_production_config`
  needs no change. That file is listed in backend-dev's paths only in case one
  of its *permission-catalog* assertions enumerates `ALL_PERMISSIONS`.

### Frontend (Next.js page/component tree)

**1. `src/components/aegis-rail.tsx` — the FR-20 migration (exact).**

- **Delete** the module-level `GROUPS` constant, the `Item` type, and the
  `can(user, it.perm)` filter (plus the now-unused `can` import).
- **Keep, byte-identical**: the `svg()` helper, `isActive()`, `RAIL_CSS`, the
  `<aside className="aerail">` shell, the `.brand` header and the `.me` footer,
  and every class name (`nav`, `sec`, `on`). The visual result is unchanged;
  only the data source moves.
- **Add**: `const { data } = useQuery({ queryKey: ["menu-tree"], queryFn: () =>
  menuApi.tree(), staleTime: 60_000 })`. The `<nav>` body becomes
  `{(data?.nodes ?? []).map(renderNode)}` where `renderNode` is a local
  recursive function:
  - `menu_type === "group"` → `<div key={n.id}><div className="sec">{n.label}</div>{n.children.map(renderNode)}</div>`
    (a nested group renders the same way one level in, with
    `style={{ paddingLeft: depth * 8 }}`; today's tree is two levels deep, and
    recursion means a future third level needs no code change).
  - `menu_type === "screen_link"` → `<Link key={n.id} href={n.route_path!}
    onClick={onNavigate} className={isActive(pathname, n.route_path!) ? "on" : ""}>
    {svg(n.icon ?? "")}<span>{n.label}</span></Link>` — the exact JSX the
    current item branch emits, with `it.href/it.icon/it.label` swapped for the
    node's fields.
  - Pruning is **entirely server-side** (FR-7/FR-8/FR-15) — the component does
    no filtering whatsoever, which is what makes AC-9 checkable by inspection.
  - Loading / error: render an empty `<nav>` (the rail chrome, brand and user
    footer stay), so a slow or failed fetch degrades to "no links", never to a
    crash or a stale hardcoded list.
- **`isActive` keeps its two special cases** (`/contracts`,
  `/workflow-builder`) — they are prefix rules about pathname matching, not a
  nav-item list, so they do not violate FR-20.

**2. `src/lib/screen-access.ts` (new) — pure helpers + one hook.**

```ts
export const LEVEL_RANK: Record<ActionLevelCode, number>;          // VIEW..DELETE = 1..4
export function matchRoutePath(pathname: string, routePath: string): boolean;
   // "/contracts/abc-123" matches "/contracts/[id]"; "/contracts" does not.
   // Segment-wise compare; a "[...]" segment matches exactly one segment.
export function screenForPathname(entries: ScreenAccessEntry[], pathname: string):
   ScreenAccessEntry | undefined;                                   // most-specific (fewest wildcards) wins
export function useScreenAccess():                                  // react-query ["screen-access","me"]
   { levels: Map<string, ActionLevelCode>; entries: ScreenAccessEntry[];
     isLoading: boolean; can(screenCode: string, level: ActionLevelCode): boolean };
```
`matchRoutePath` / `screenForPathname` / `LEVEL_RANK` are pure and React-free
and are what `src/lib/screen-access.test.ts` unit-tests.

**3. `src/components/screen-guard.tsx` (new) + `src/app/(app)/layout.tsx`
(edit) — the FR-14 deep-link gate, once, for every route.**

`<ScreenGuard>{children}</ScreenGuard>` wraps the existing children inside the
`(app)` layout. It reads `usePathname()` and `useScreenAccess()`:
- still loading → render `<CenterSpinner />`;
- `screenForPathname(entries, pathname)` returns **undefined** → render
  `children` (the pathname is not a registered screen — e.g. a 404 route or a
  future page not yet catalogued; a guard that blocked unknown routes would
  deny things this feature never claimed to govern);
- entry found → render `children` (its presence in the response already means
  ≥ VIEW);
- entry **absent** while other entries exist and the pathname *does* match a
  known `route_path` from the catalog → render the
  `Lock` + `EmptyState "Access restricted"` block copied from
  `org-structure/page.tsx`.

To distinguish "this pathname is not a governed screen" (allow) from "this is a
governed screen I have no grant on" (block), the guard needs the full catalog
of route paths, while `GET /screen-access/me` deliberately returns only the
*granted* ones. **Decision**: `src/lib/screen-access.ts` exports a
`ALL_SCREEN_ROUTE_PATHS: readonly string[]` constant — the 39 `route_path`
values from the catalog table above, verbatim. The guard blocks iff
`pathname` matches an entry in `ALL_SCREEN_ROUTE_PATHS` **and** no entry in
the `/screen-access/me` response. This is a route *inventory* (a list of what
pages exist), not a nav list and not an access rule — every access decision
still comes from the server response — so it does not reintroduce what FR-20
forbids. `src/lib/screen-access.test.ts` asserts the constant matches the
route paths derivable from `src/app/(app)/**/page.tsx`, so a new page added
without a `screen` row fails the frontend suite. *Rejected*: fetching the
catalog from `GET /screens` (admin-gated — non-admins would get a 403 and the
guard would fail open for everyone), and widening `/screen-access/me` to
return every screen with a nullable level (leaks the full screen inventory to
every user for no functional gain).

**4. `src/app/(app)/screen-access/page.tsx` (new) — FR-23's admin screen.**
Structure copied from `org-structure/page.tsx`:
- `"use client"`; renders the `Lock`/`EmptyState` "Access restricted" block
  unless `can(user, "admin_panel:access")` (same guard the org-structure page
  uses today), with `<PageHeader title="Screen access" description="Control
  which roles can view, add, edit and delete on each screen." />`.
- `_screen-grants-panel.tsx` — FR-23's "view existing grants". `Select`
  filters for role (`rolesApi.list()`), screen (`screensApi.list()`) and org
  unit (`orgUnitsApi.list()`, indented labels via the existing
  `flattenWithIndent` from `src/lib/org-tree.ts`). A `Table` of
  role / screen / org-unit scope / max level `Badge` / status, with per-row
  `Select` (change level → `screenAccessApi.update`) and a Revoke `Button`
  behind `useConfirm`. Both controls are **disabled** when
  `grant.is_locked` (FR-25/AC-15's UI half, mirroring how
  `admin/page.tsx`'s `RoleEditorModal` disables `allows_hierarchy_rollup` for
  the built-in admin role). Server 409s surface verbatim via
  `useToast(..., "error")`.
- `_assign-screen-grant-modal.tsx` — FR-22/FR-23's "assign a new grant": role
  `Select`, screen `Select`, action-level `Select` (from
  `menuApi.actionLevels()`, so the four levels are data-driven), optional
  org-unit `Select` with an explicit "Organization-wide" option mapping to
  `org_unit_id: null`. Every picker is populated from org-scoped endpoints, so
  no other organization's role/screen/unit can appear (AC-14).

**5. FR-9 control gating on the five tranche pages.** In
`contracts/page.tsx`, `matters/page.tsx`, `trademarks/page.tsx`,
`notices/page.tsx`, `intake/page.tsx`: `const { can: canOn } =
useScreenAccess();` and wrap each add/edit/delete control in
`canOn("contracts", "ADD") && …` (etc., using that page's screen code). These
are the **only** edits to those five files — no behavior change beyond showing
or hiding a control. Per FR-13 this is a usability aid; the server check is
the boundary.

- **New shared primitives in `src/components/ui.tsx`: none.** Every primitive
  used above — `Badge, Button, Card, CardBody, CardHeader, CardTitle,
  CenterSpinner, EmptyState, ErrorState, Field, Input, MessageBar, Modal,
  PageHeader, Select, Table, TD, TH, THead, TR, Tabs, useConfirm` — is already
  exported from `frontend/src/components/ui.tsx` (verified against the file's
  export list). Icons from `lucide-react`; toasts from `@/components/toast`.
- **Demo-mode behavior**: this repo has **no `src/lib/demo.ts`** (verified:
  `frontend/src/lib/` holds api, auth, client-logger, contract-blocks,
  endpoints, intake, layout, org-tree, query, types, utils). There is no demo
  mode to keep functional. The rail degrades to an empty `<nav>` and the new
  page to `CenterSpinner` → `ErrorState` when the API is unreachable, like
  every other page.

## Test strategy

- **Backend pytest** (Postgres, per `backend/tests/conftest.py`; note the suite
  runs against an `alembic upgrade head` database, so the migration's seeded
  `screen` / `action_level` / `menu_item` rows are present in tests):
  - `test_screen_access_resolver.py` — fixture builds Global → Region A → BU 1
    (reusing feature 002's org-unit fixture shape). Cases: level implication
    VIEW<ADD<EDIT<DELETE (AC-1); two roles, VIEW + EDIT, resolves EDIT (AC-7);
    ancestor grant EDIT at Region A + descendant grant VIEW at BU 1 resolves
    EDIT when scoped to BU 1 (AC-8, FR-19); no upward/lateral rollup;
    `allows_hierarchy_rollup=False` → ancestor grant ignored, exact-unit grant
    honored; organization-wide (`org_unit_id IS NULL`) grant applies at every
    unit; Org-B grant never applies to an Org-A user (AC-13); revoked and
    expired `UserRoleGrant` confer nothing (the `active_grants_for_user` reuse);
    zero grants → `level=None, reason="no_grant"`, not an error; unknown screen
    code → `reason="screen_not_found"`; **`resolve_all_screen_access` and
    per-screen `resolve_screen_access` agree for every screen** (the FR-15
    single-rule guarantee).
  - `test_menu_tree_api.py` — a user with VIEW on exactly one screen under a
    group sees only that group and that child; the group's other children and
    every empty group are absent (AC-2); `action_level` on each node equals the
    resolver's answer for that screen; a screen with no menu node never appears
    though `/screen-access/me` lists it; 403 without `menu:read`.
  - `test_screen_access_grants_api.py` — create/modify/revoke happy paths;
    duplicate live grant 409; both partial unique indexes exercised (one
    org-unit-scoped duplicate, one organization-wide duplicate); invalid level
    422; cross-org role / org unit 404 (AC-14); 403 without
    `screen_access:manage`; the `screen_access.updated` audit row carries actor,
    timestamp, role, screen and prior+new level (AC-11); the built-in admin
    role's `screen_access` grant cannot be PATCHed below DELETE nor DELETEd,
    from any actor (AC-15).
  - `test_screen_level_enforcement.py` — the FR-10/FR-12/FR-28 core. A user
    seeded VIEW on `contracts` gets 403 on `PATCH /contracts/{id}`,
    `POST /contracts/{id}/parties` and
    `DELETE /contracts/{id}/parties/{party_id}` via raw HTTP (AC-3), and the
    denial writes an `access.denied` audit row naming the user, the screen,
    the attempted action and the resolved level (AC-12); a user holding
    `contract:update` but lacking the screen level is rejected, and a user
    holding the screen level but lacking `contract:update` is rejected (AC-5);
    a user with EDIT on `matters` succeeds on `PATCH /matters/{id}` while the
    menu tree shows Matters and `/screen-access/me` reports EDIT — all three
    asserted in **one** test for the same user/screen/action (AC-6); a
    non-tranche endpoint (`PATCH /renewals/...`) is unaffected while its screen
    still resolves for menu visibility (AC-17).
  - Regression gates for the route edits: `backend/tests/` existing
    `test_*contract*`, `test_*matter*`/`test_*project*`, `test_*trademark*`,
    `test_*notice*`, `test_*intake*`/`test_flow*` files must still pass —
    every one of them authenticates as a role that the FR-26 backfill seeds at
    DELETE for the relevant screen, which is precisely the property FR-26
    claims. A failure there is evidence the backfill map is wrong, not that the
    test needs changing.
- **Frontend vitest**: `src/lib/screen-access.test.ts` —
  `matchRoutePath` matches `/contracts/abc` to `/contracts/[id]` and not to
  `/contracts`, and does not match `/contracts/abc/extra`;
  `screenForPathname` prefers the exact (`/trademarks/calendar`) over the
  wildcard (`/trademarks/[id]`); `LEVEL_RANK` orders the four levels;
  `ALL_SCREEN_ROUTE_PATHS` contains exactly the route paths derivable from
  `src/app/(app)/**/page.tsx` (a drift guard on the 39-row catalog).
- **Acceptance verification** (`/verify`): AC-1..AC-8 and AC-11..AC-15 and
  AC-17 are each evidenced by a named pytest above. AC-9 is evidenced by
  `npm run typecheck` plus a **source inspection of
  `frontend/src/components/aegis-rail.tsx` recorded in verification.md**
  asserting no nav-item array and no `can(...)` filter remain. AC-10 is
  evidenced by the vitest cases plus a manual `docker compose up -d`
  walkthrough (log in as a role without a grant on a screen, navigate to its
  URL directly, observe the "Access restricted" state). **AC-16 is evidenced by
  a documented docker-compose migration procedure**, not a pytest: seed a
  pre-migration DB at `0042_org_hierarchy_rbac` with three roles (one with
  `contract:update`, one with `contract:read` only, one with neither), run
  `alembic upgrade head`, and assert the first has a DELETE
  `role_screen_access` row for `contracts`, the second a VIEW row, the third no
  row — the suite runs against an already-upgraded schema, so a unit test
  cannot observe the pre-migration state. This mirrors how feature 002
  evidenced its own AC-9.

## Risks & decisions

- **The screen resolver is a NEW `app/core/screen_access.py`, not an addition
  to `app/core/org_access.py`.** spec.md's "Out of scope" fences off changes to
  `app.core.org_access`; that module is also imported by `core/deps.py` and
  therefore by every gated route, so an additive edit there carries the app's
  largest blast radius. The new module *imports* `ancestor_unit_ids` and
  `active_grants_for_user` from it, so FR-18's "identical ancestor-walk" is
  literally the same code path, and feature 002's "one shared resolver, never
  reimplemented" rule is honored for this feature's own axis. *Rejected*:
  adding `resolve_screen_access` to `org_access.py` (edits a spec-fenced file
  with maximal blast radius); putting the resolver in
  `app/menu_security/service.py` (would make `app/core/deps.py` import a
  feature package — the exact dependency inversion feature 002's plan rejected).
- **Screen catalog and menu tree are NOT org-scoped, and have no CRUD
  endpoints.** FR-24 says so explicitly. This also means one `screen.code` is
  globally unique and one `menu_item` tree serves every tenant, keeping the
  resolver's step 1 a single unfiltered lookup. *Rejected*: per-org menu trees
  (invents an editing surface FR-24 forbids and multiplies the seed by tenant
  count).
- **FR-26 seeds every grant with `org_unit_id = NULL` (organization-wide), not
  at the org root unit.** A root-unit grant only rolls down to descendants for
  roles whose `allows_hierarchy_rollup` is true; a role with that flag set to
  false would silently lose access at cutover, breaking FR-26's "no user's
  effective day-one access changing." An organization-wide grant bypasses the
  rollup flag entirely and reproduces today's flat, org-unit-unaware behavior
  exactly. It also makes the migration simpler and dialect-agnostic (no root
  lookup). *Rejected*: seeding at the org root (correct for the common case,
  silently wrong for non-rollup roles).
- **Two partial unique indexes on `role_screen_access`, not one.** Postgres
  treats NULLs as distinct in a unique index, so
  `(role_id, screen_id, org_unit_id) WHERE deleted_at IS NULL` would happily
  accept two live organization-wide grants for the same (role, screen). The
  second index, `(role_id, screen_id) WHERE org_unit_id IS NULL AND deleted_at
  IS NULL`, closes that. The service's 409 is the readable message; the indexes
  are the guarantee under concurrency — the same belt-and-braces feature 002
  used for its single-root rule.
- **FR-25 is a service-layer guard plus a seeded row, not a DB constraint.** A
  CHECK constraint cannot express "this row's `screen_id` resolves to the
  screen whose code is `screen_access` and this row's `role_id` resolves to the
  role named `admin`" without a cross-table lookup, and a trigger would hide
  the rule from the codebase. `_assert_bootstrap_grant_not_weakened` sits on
  the single service path both the PATCH and the DELETE endpoint funnel
  through, so AC-15's "through which surface" holds. *Rejected*: (a) a DB
  constraint/trigger, (b) hard-coding the resolver to return DELETE for the
  built-in admin role on `screen_access` — that would make the admin screen's
  access untestable through the grant mechanism and would silently mask a
  misconfiguration rather than refusing it.
- **Read-shaped mutating verbs are gated at VIEW, not at ADD/EDIT.** Six routes
  in the tranche (`POST /contracts/{id}/risk`,
  `POST /trademarks/search-similar`, `POST /trademarks/documents/extract`,
  `POST /trademarks/integrations/test/{name}`,
  `POST /intake/requests/{id}/suggest-flow`, `POST /intake/copilot/turn`) use a
  mutating HTTP verb for a read/analysis action. Gating them at ADD would deny
  a role seeded at VIEW something it can do today, breaking FR-26. They still
  carry the dependency, so FR-11's "visibly present" holds for every route in
  the tranche. *Rejected*: omitting the dependency from them (creates exactly
  the "silently missing" gap FR-11/AC-4 forbid).
- **Detail and sub-page routes are separate screens seeded identically to their
  list screen, and tranche-1 enforcement uses the list screen's code.** FR-1
  mandates one screen per page route, so `/contracts` and `/contracts/[id]` are
  two rows; but the contracts mutating endpoints back both pages, so gating
  them on two different codes would be ambiguous. Enforcement therefore uses
  the domain's primary code (`contracts`, `matters`, `trademarks`, `notices`,
  `intake`), while the detail/sub-page screens govern navigation (FR-3/FR-14)
  and are seeded with the identical level so day-one behavior is unchanged. An
  administrator can later diverge them deliberately (FR-22).
- **The deep-link gate lives once in `src/app/(app)/layout.tsx`, not in 38
  pages.** FR-14 is unqualified (every screen, not just the tranche), and
  touching every page file would be a huge, error-prone diff where a single
  omission is an unguarded route. One layout-level `ScreenGuard` keyed on
  `usePathname()` → `route_path` covers every current and future route,
  including deep links, by construction. *Rejected*: a per-page guard (38
  files, silently skippable — the same anti-pattern FR-11 rejects on the
  backend).
- **Risk — today's frontend permission filter is a no-op, so some users WILL
  see fewer nav items after cutover.** `frontend/src/lib/intake.ts`'s
  `can(user, perm)` currently reads `return !!user;` — it ignores the
  permission string entirely, so today's rail shows all 24 items to every
  logged-in user regardless of their role. After this feature, the rail is
  pruned by real resolution, so e.g. a `member` (no `notice:read`) will stop
  seeing the Notices link. **Decision**: FR-26's "effective access" is read as
  *what a user can actually do* (the backend permission strings the API
  enforces), not *what links render*, because the link-rendering rule is the
  very "separate, looser visibility rule" FR-7/FR-20 exist to abolish. No
  user loses the ability to perform any action they can perform today; some
  users lose links to pages that would have shown them a wall of access-denied
  errors — which is user story #1 of this spec. **This is the one user-visible
  behavior change and is flagged for the user's explicit attention before
  approval.**
- **Risk — the FR-26 seed map is a hand-built snapshot.** The map lives as a
  literal in the migration and cannot import `app.core.rbac` (migrations must
  not drift with app code). If a permission string is mis-assigned, a role gets
  the wrong day-one level. Mitigation: the existing domain test suites
  (contracts, matters, trademarks, notices, intake) act as the regression
  gate — they authenticate as roles the map must seed at DELETE, so a wrong
  map turns into a 403 in CI rather than a silent production narrowing.
- **Risk — 39 screens × N roles × M orgs rows at cutover.** For a
  single-tenant install with 4 built-in roles that is ~120 rows; the migration
  inserts them in one batched pass. Harmless, but noted so the row count is not
  a surprise on the admin screen's first load (the grants panel therefore
  defaults to a role filter rather than listing everything).
- **`POST` for create + `PATCH` for modify + `DELETE` for revoke**, matching
  feature 002's role-grant shape (`DELETE` + 204, soft delete, screen
  re-lists). FR-22 names all three operations separately, so a
  create-or-update upsert on `POST` was rejected: it would make the
  `screen_access.granted` vs `screen_access.updated` audit distinction
  (FR-27's "prior and new maximum action level") ambiguous.

### FR traceability

| FR | Where it lands |
|---|---|
| FR-1 | `screen` table with UNIQUE `route_path`; the 39-row catalog, one row per `src/app/(app)/**/page.tsx` |
| FR-2 | `menu_item` table: self-FK `parent_id`, `menu_type` CHECK, `ck_menu_item_screen_link` |
| FR-3 | Screens exist for routes with no menu node (rows 6, 7, 9, 11, 12, 14, 16, 24, 26–32, 34); `ScreenGuard` in `(app)/layout.tsx` gates by pathname, not by menu presence |
| FR-4 | `action_level` (code CHECK + rank 1–4 UNIQUE); `LEVEL_RANK` comparison (`rank >= min_rank`) in `screen_access.py` |
| FR-5 | `role_screen_access.max_action_level_id` — a single FK, so a non-contiguous level set is unrepresentable |
| FR-6 | `app/core/screen_access.py` — the only implementation; `resolve_all_screen_access` and `resolve_screen_access` share one predicate |
| FR-7 | `service.get_menu_tree` prune step: `screen_link` survives iff present in `resolve_all_screen_access`'s map |
| FR-8 | `service.get_menu_tree` bottom-up recursive group prune |
| FR-9 | `GET /screen-access/me` + `useScreenAccess()` + the five tranche page edits |
| FR-10 | `require_screen_level` → `assert_screen_level` → `resolve_screen_access`, re-resolved per request from the DB; no client input is read |
| FR-11 | `Depends(require_screen_level(...))` in each route signature (the tranche route tables above); AC-4 verified by inspection |
| FR-12 | Both `Depends(require_permission(...))` and `Depends(require_screen_level(...))` on every tranche route; `require_permission` unmodified |
| FR-13 | Frontend gating is `useScreenAccess()`-driven only; `test_screen_level_enforcement.py`'s raw-HTTP cases prove the server refuses regardless |
| FR-14 | `src/components/screen-guard.tsx` mounted once in `src/app/(app)/layout.tsx` |
| FR-15 | One resolver, one predicate; the resolver test asserts batched == per-screen; AC-6 asserts menu + `/me` + API in one test |
| FR-16 | All 39 screens seeded in migration step 2 and resolved by the menu tree regardless of tranche membership |
| FR-17 | The five tranche route tables above; `TRANCHE_1_SCREEN_CODES` → `ScreenResponse.is_enforced`; the intake deferral list |
| FR-18 | `resolve_screen_access` step 4 calls `org_access.ancestor_unit_ids`; step 6 honors `Role.allows_hierarchy_rollup` exactly as `org_access.resolve_access` step 3 does |
| FR-19 | `resolve_screen_access` step 7 — MAX over the union of applicable rows; never an override |
| FR-20 | `aegis-rail.tsx` rewrite: `GROUPS` and the `can()` filter deleted, `menuApi.tree()` is the sole source |
| FR-21 | `menuApi.tree()` is fetched per mount with `staleTime: 60_000`; grants are read live from the DB on every resolution — no deploy involved |
| FR-22 | `POST` / `PATCH` / `DELETE /screen-access/grants`, org-filtered role/screen/unit lookups in `access.py` |
| FR-23 | `src/app/(app)/screen-access/page.tsx` + `_screen-grants-panel.tsx` + `_assign-screen-grant-modal.tsx` |
| FR-24 | No menu/screen CRUD endpoints exist; `screen`/`action_level`/`menu_item` are seeded by migration `0043` only |
| FR-25 | `service._assert_bootstrap_grant_not_weakened` + the migration's admin/`screen_access` DELETE upsert + `is_locked` disabling the UI controls |
| FR-26 | Migration `0043_menu_screen_security` step 5 + the seed map column of the screen-catalog table |
| FR-27 | `screen_access.granted` / `.updated` / `.revoked` audit rows with before/after `max_action_level` and role/screen/org-unit metadata |
| FR-28 | `assert_screen_level` calls the existing `app.core.authz.record_decision` with the screen, the attempted action and the resolved level |

### AC traceability

AC-1 → `test_screen_access_resolver.py`; AC-2 → `test_menu_tree_api.py`;
AC-3 → `test_screen_level_enforcement.py` (raw-HTTP 403 on contracts
ADD/EDIT/DELETE); AC-4 → source inspection of the five tranche `routes.py`
files recorded in `verification.md` (the dependency is in every signature in
the tranche tables); AC-5 → `test_screen_level_enforcement.py` (both
one-of-two-gates-missing directions); AC-6 → `test_screen_level_enforcement.py`
(the single three-way-agreement test on Matters/EDIT); AC-7, AC-8 →
`test_screen_access_resolver.py`; AC-9 → `aegis-rail.tsx` source inspection +
`npm run typecheck`; AC-10 → `src/lib/screen-access.test.ts` +
`docker compose` walkthrough in `verification.md`; AC-11 →
`test_screen_access_grants_api.py` (audit before/after); AC-12 →
`test_screen_level_enforcement.py` (the `access.denied` row); AC-13 →
`test_screen_access_resolver.py` (Org-B grant ignored); AC-14 →
`test_screen_access_grants_api.py` (cross-org 404) + the
`/screen-access` walkthrough; AC-15 → `test_screen_access_grants_api.py`
(PATCH-below-DELETE 409 and DELETE 409, from two different admin actors);
**AC-16 → the documented migration procedure in `verification.md`** (see Test
strategy); AC-17 → `test_screen_level_enforcement.py` (renewals unguarded but
its screen still resolves for the menu).
