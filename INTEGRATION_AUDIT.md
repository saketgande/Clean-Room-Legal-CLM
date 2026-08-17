# Aegis CLM — Integration & Overlap Audit (code-level)

**What this is.** A whole-app audit of where the Aegis CLM has *overlapping* concepts (multiple mechanisms doing the same job) and *disconnected* concepts (entities that should link but don't), traced through the actual backend code and frontend UI. Every finding carries verbatim code with `file:line`, a line-by-line logic trace, a concrete failure scenario, and the smallest fix.

**How to read it.** Six parts, ordered by severity. Part 1 (access control) contains real security holes and should be read first. Part 2 is the user-reported headline (contracts can't seed requests). Parts 3–6 are structural coherence problems that make the app feel like "separate apps stitched together."

**The one root cause behind everything.** The app was built in sequential *Phases* bolted on beside each other and never composed. Almost every finding is a variant of two patterns: (a) a new layer that claims to *unify* older ones ships, but the older ones are never removed — so now there are more, not fewer; and (b) *write-only dead scaffolding* — a field/endpoint/table written by one path and read by no code, so it looks like a feature but silently does nothing.

---

## Severity map

| # | Area | Worst symptom | Sev |
|---|------|---------------|-----|
| 1 | Access control | Read-only grant + org-wide `contract:update` = full write; MAC classification editable by the permission it gates; walls leak through projects; project/playbook grants are read by no code | 🔴 Critical |
| 2 | Contract ↔ Request ↔ Project | One-way FK only; contract can't name its request; `project_id` has no FK and the only writer (`promote`) has zero UI callers | 🔴 High |
| 3 | Teams / people | Four parallel "group of people" tables that never reference each other; routing discards the team, storing only a user | 🟠 High |
| 4 | Approvals | Backend unified (good), but a dead `approval_gate_user_id` no-op gate and a contract-only `/approvals` UI (no single approver inbox) | 🟠 Med-High |
| 5 | Process engines | "workflow" names three things; two unsynced progress trackers on one request; orphaned `WorkflowRun` | 🟡 Medium |
| 6 | UI / navigation | Two competing organizing models; `RulesTab` mounted at two routes; one CTA ejects while its sibling stays in-panel | 🟡 Medium |

---

# Part 1 — Access control: eleven uncomposed mechanisms with real security holes

## 1.0 The eleven mechanisms, mapped to code

Every "can this user do X" decision is answered by one of these, and no two share an evaluator:

| # | Mechanism | Definition | Enforced at |
|---|-----------|-----------|-------------|
| 1 | RBAC permission verbs | `auth/models.py:59` `permission_values`, `core/rbac.py:130` | `core/deps.py:82` `require_permission` |
| 2 | MAC / clearance | `contracts/access.py:44` `clearance_permits` | `contracts/access.py:132,167` |
| 3 | Ethical walls (deny-override) | `walls/service.py:73,89` | `contracts/access.py:92,164` |
| 4 | ResourceGrant (additive) | `grants/service.py:61,75` | `contracts/access.py:139,177` — **contract only** |
| 5 | AuthorityGrant / DoA | `authority/service.py:113` | `signatures/routes.py:90`, `approvals/{service:979,routes:713}` |
| 6 | ProjectMember role | `projects/access.py:66` | `projects/access.py:84-90` |
| 7 | ProjectShare level | `projects/access.py:133` | `projects/access.py:77-82` |
| 8 | ContractShare (external token) | `contract_files/models.py:112` | public token path, no internal check |
| 9 | Ownership / creator | `contracts/access.py:170` | inline |
| 10 | Pending-approver implicit | `contracts/access.py:53` | `contracts/access.py:174` |
| 11 | `is_org_admin` (dual definition) | `core/access.py:5` | everywhere |

Mechanisms 2, 3, 4, 9, 10 are composed into `accessible_contract_filter`/`user_can_access_contract` for **contracts**; 6, 7 live in a **separate** `projects/access.py` that reuses none of them; 5 is a third island touched only by approve/sign; 8 is a fourth island with no internal gate. Below is where the seams leak.

## 1.1 A `read`-level grant plus the `contract:update` verb yields full write

The write endpoint gates on the RBAC verb, then fetches the row through a **read**-level check:

```python
# contracts/routes.py:230
@router.patch("/{contract_id}", response_model=ContractResponse)
def update_contract(
    contract_id: str,
    payload: ContractUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:update")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return update_contract_metadata(db, contract=contract, user=current_user,
        updates=payload.model_dump(exclude_unset=True), ...)
```

`get_contract_for_user` is a pure read gate — it never receives or checks an access level:

```python
# contracts/service.py:27
def get_contract_for_user(db, *, contract_id, user) -> Contract:
    contract = db.get(Contract, contract_id)
    if (contract is None or contract.org_id != user.org_id
        or contract.deleted_at is not None
        or not user_can_access_contract(db, contract=contract, user=user)):
        raise HTTPException(404, "Contract not found")
    return contract
```

The grant is resolved at the default `read` floor — the level ladder exists but is unused here:

```python
# contracts/access.py:176
    if user_has_grant(db, user=user, resource_type="contract", resource_id=contract.id):
        return True
# grants/service.py:23   LEVELS = ["read", "comment", "update", "share", "owner"]
# grants/service.py:75   def user_has_grant(..., min_level: str = "read")   # can discriminate — never asked to
```

And the mutation writes every provided field unconditionally:

```python
# contracts/service.py:98
    for key, value in updates.items():
        if value is not None and hasattr(contract, key):
            setattr(contract, key, value)
```

**Logic.** `require_permission("contract:update")` checks an *org-wide* verb held by the default `member`/`legal_reviewer` roles (`core/rbac.py:92,104`). The row gate then calls `user_has_grant(...)` with `min_level` defaulted to `"read"`. Neither the row gate nor `update_contract_metadata` ever asks for `min_level="update"`. `access_level` is read into the serializer but never compared against the operation.

**Failure.** Dana = `legal_reviewer` (`contract:update` ✓). Contract `C-500` is shared to Dana at `access_level="read"`. `PATCH /contracts/C-500 {"counterparty_name":"Evil Corp","value_amount":1}` → verb passes → read grant satisfies the read gate → financials overwritten. The `read < comment < update` ladder was decorative.

**Fix.** Thread the operation's required level into the check: add `min_level` to `user_can_access_contract` (default `"read"`, forwarded at line 177) and call it with `"update"` from `contracts/service.py:67`. Owner/creator/admin stay short-circuit `True`.

## 1.2 Project- and playbook-scoped grants are write-only dead data

The grant API accepts and fully persists three resource types:

```python
# grants/service.py:25    RESOURCE_TYPES = {"contract", "project", "playbook"}
# grants/service.py:114   _resource_owner_id resolves contract, project AND playbook owners
```

But the **only** readers filter on `"contract"`:

```python
# contracts/access.py:139   Contract.id.in_(granted_resource_ids(user, "contract"))
# contracts/access.py:177   user_has_grant(db, user=user, resource_type="contract", ...)
```

Project access is decided by a function that never imports the grant service:

```python
# projects/access.py:54
def user_can_access_project(db, *, project, user, access="read") -> bool:
    if project.org_id != user.org_id: return False
    if is_org_admin(user) or project.owner_user_id == user.id: return True
    role = db.scalar(select(ProjectMember.role).where(...))   # ProjectMember + ProjectShare only
    ...
```

**Failure.** Priya (project owner) grants Bob `owner` on project `P-9` via `POST /grants` → `201`, audit `grant.created`, serialized `"active": true`. Bob calls `GET /projects/P-9` → `user_can_access_project` checks admin/owner/ProjectMember/ProjectShare, never grants → `404`. Priya sees a successful active grant; Bob is locked out. Silent no-op with a success receipt.

**Fix (honest one):** either narrow `RESOURCE_TYPES = {"contract"}` and reject the other two until wired (stops the lie today), or add `granted_resource_ids(user,"project")`/`user_has_grant(...,"project",...)` branches to `projects/access.py`. Ship the narrowing now.

## 1.3 Three live sharing systems, three incompatible level vocabularies

`ResourceGrant` claims to have unified the others — verbatim docstring:

```python
# grants/models.py:13
class ResourceGrant(...):
    """... This unifies the three fragmented sharing mechanisms (ProjectShare,
    ContractShare, Workflow.shared_user_ids) into one relationship model ..."""
    # read < comment < update < share < owner
    access_level = Column(String(40), nullable=False, default="read")
```

Yet all three still exist and are the live path, each with a **different** ladder:

- ResourceGrant: `read < comment < update < share < owner` (`grants/service.py:23`)
- ProjectShare: `read / update / share` (`projects/access.py:12`)
- ProjectMember: `owner / manager / editor / member` (`projects/access.py:10`)
- ContractShare: `access_mode ∈ ShareAccessMode` + boolean `download_allowed` (`contract_files/models.py:125,128`)

No mapping table, no shared enum. `user_can_access_project` reads ProjectShare/ProjectMember; `accessible_contract_filter` reads ResourceGrant; nothing reads across. The "unification" is a docstring aspiration; the migration deleting the old three never happened.

**Failure.** Security reviewer asks "who can reach project P-9 and its contracts?" They trust the docstring, check ResourceGrant, find Bob's `owner` grant — wrong twice (the grant is inert per 1.2, and the real holders are in `ProjectShare`+`ProjectMember`). An external `ContractShare` token grants download to anyone with the link, invisible in all three internal tables.

**Fix.** Not a one-liner: pick `ResourceGrant` as the single internal source of truth, migrate `ProjectShare`/`ProjectMember` rows in with a level map (`editor→update, manager→share, owner→owner`), repoint `projects/access.py`, keep `ContractShare` as the deliberately-separate *external* channel (rename `ExternalContractLink`). Until then, delete the misleading "unifies" sentence so no reviewer trusts it.

## 1.4 RBAC reads the *active* role only; walls/grants/authority read *all* roles

```python
# auth/models.py:59
@property
def permission_values(self) -> set[str]:
    if self.active_role_id:
        active_role = next((r for r in self.roles if r.id == self.active_role_id), None)
        if active_role is not None:
            return {p.value for p in active_role.permissions}     # ACTIVE role only
    values = set()
    for role in self.roles:
        for p in role.permissions: values.add(p.value)
    return values
```

But grants, authority, and walls all match against **every** role the user holds:

```python
# grants/service.py:45   role_ids = [r.id for r in user.roles]   → matches any role
# authority/service.py:52 same shape
# walls/service.py:39     _principal_match_sql — same, all roles
```

**Failure.** Sam holds `{approver, member}`, active role `member`. A ResourceGrant is issued to the `approver` role for contract `C-7`. Sam (active as `member`, deliberately shedding privilege) calls `GET /contracts/C-7` → `granted_resource_ids` matches `approver` via all-roles clause → contract visible. "Switch to my restricted role" drops RBAC verbs but not resource grants — privilege reduction is half-honored.

**Fix.** Add one `effective_role_ids(user)` helper (returns `[active_role_id]` when set, else all) and call it from all four principal builders. Active-only vs all-roles is a product decision; the four must simply agree.

## 1.5 MAC defeats itself: `confidentiality` is editable by the verb it gates

```python
# contracts/access.py:44
def clearance_permits(user, contract) -> bool:
    if is_org_admin(user): return True
    contract_rank = _rank(getattr(contract, "confidentiality", None), _DEFAULT_CONTRACT_LEVEL)
    return contract_rank <= user_clearance_rank(user)
```

A *user's* clearance is admin-only to set (`roles/routes.py:73`, `_ASSIGN` dep). But a *contract's* classification — the other operand — rides the ordinary metadata PATCH:

```python
# contracts/schemas.py:52
    confidentiality: str | None = Field(default=None,
        pattern="^(public|internal|confidential|restricted)$")
# contracts/service.py:90   "confidentiality": contract.confidentiality,   # in the audited before-set
# contracts/service.py:98   setattr(contract, key, value)                  # written like any field
```

**Failure.** Any `contract:update` holder who can reach a contract can set `confidentiality="public"`, dropping its rank to 0 so **every** user in the org can read it — or reclassify to hide it. MAC enforces a value its own subjects can rewrite.

**Fix.** One guard at `contracts/service.py:98`: `if "confidentiality" in updates and not is_org_admin(user): raise 403` — or drop `confidentiality` from `ContractUpdate` and give it a dedicated admin-gated endpoint mirroring `set_user_clearance`.

## 1.6 Two disagreeing definitions of "admin"; admin's power differs per layer

```python
# core/access.py:5
def is_org_admin(user) -> bool:
    if has_permission(user.permission_values, "admin_panel:access"):   # active-role-scoped
        return True
    return any(role.name == ADMIN_ROLE_NAME for role in user.roles)    # all-roles, name-based
```

Admin is **walled out** for contracts (`contracts/access.py:164` deny runs before the admin allow at `:170`) but **not** for projects — the admin short-circuit is first, with no wall check anywhere in the function:

```python
# projects/access.py:54
def user_can_access_project(db, *, project, user, access="read") -> bool:
    if project.org_id != user.org_id: return False
    if is_org_admin(user) or project.owner_user_id == user.id: return True   # no wall predicate
```

**Failure.** Admin Morgan is behind a project-scoped EthicalWall on `P-42` (conflict of interest). `GET /contracts/{c in P-42}` → walled out, correct. `GET /projects/P-42` → `is_org_admin` short-circuits `True` → Morgan reads the project name, description, `ProjectActivity` feed, and member roster. The wall advertised as binding "even org-admin" leaks the conflicted matter through the project surface.

**Fix.** (1) Collapse `is_org_admin` to a single predicate. (2) Add a project-wall predicate to `user_can_access_project` *before* the admin short-circuit, mirroring the contract ordering.

## 1.7 Walls and clearance guard contracts but leak through the project surface

`wall_block_filter`/`user_is_walled` and `clearance` are consumed **only** by `contracts/access.py`. `projects/access.py` contains neither — yet `EthicalWall.scope_type` explicitly supports `"project"`:

```python
# walls/models.py:29   scope_type = Column(...)  # 'contract' | 'project'
```

**Failure.** A `project`-scoped wall bars Nadia from `P-42`. `GET /contracts` correctly excludes P-42's contracts. `GET /projects/P-42` (she's a lingering `ProjectMember`) → `role is not None` → `True`; wall never consulted. She reads `GET /projects/P-42/members` and `/activity` — the conflicted matter's team and timeline, leaked. A project-scoped wall protects the contracts but not the project it is literally scoped to.

**Fix.** Extract a `project_wall_block(user)` predicate and `AND` it into `project_scope_query` and `user_can_access_project`. Same missing guard as 1.6; one shared helper closes both.

## 1.8 DoA (AuthorityGrant) is an isolated approve/sign gate, blind to read access

```python
# authority/service.py:29   ACTIONS = {"contract:approve", "contract:sign"}
```

`enforce_authority` is called at only three sites (signature send, two approval-decision paths) and evaluates value/type/jurisdiction/risk ceilings only. It never calls `user_can_access_contract`; and `accessible_contract_filter` never calls `evaluate_authority`. They are mutually blind.

**Failure.** While GC is on leave, admin issues `AuthorityGrant{principal=taylor, action="contract:sign", max_value=5M}`. Taylor isn't owner/member of `C-11`. `GET /contracts/C-11` → not owner/creator/pending-approver/grantee/project-member → `404`. Taylor cannot even see the contract they hold signing authority for. The delegation silently requires a *second*, separately-granted read path.

**Fix.** Add one branch to `user_can_access_contract`: "user holds an active AuthorityGrant for approve/sign covering this contract" → allow read. Small and unsurprising.

## 1.9 External `ContractShare` links bypass every internal control once minted

Creation enforces internal access (`contract_file:share` + `get_contract_for_user`, `contract_files/routes.py:958`). But the artifact is a bearer token + optional passcode with its own `access_mode`, and the consuming path authenticates the **token**, not a `User` — so walls/clearance/grants/RBAC are all off that path.

**Failure.** `C-13` is `restricted`; later a wall bars `external_counsel` from it. Yesterday Robin minted a share (`download_allowed=True`, no expiry). Today anyone with the link+passcode downloads the restricted PDF: consumption checks only passcode + `revoked_at` + `expires_at`. Raising a wall or revoking internal access does not invalidate outstanding tokens.

**Fix.** At token consumption, re-check contract-level deny state that isn't user-specific (refuse if `confidentiality=="restricted"` or an active wall covers it), and auto-revoke outstanding shares when a wall is created. Rename `ExternalContractLink` so it stops reading as a peer of `ResourceGrant`.

## Part 1 root cause

There is no single `can(user, action, resource)` chokepoint. Eleven mechanisms across four modules (`contracts/access.py`, `projects/access.py`, `grants/service.py`, `authority/service.py`) were each added as a Phase composing a *different subset* of the others. `accessible_contract_filter` is the closest to unified — but only for the contract table, so anything reached through projects (1.7), authority (1.8), or external links (1.9) escapes the deny-overrides, and anything varying by *level* (1.1, 1.3) or *role scope* (1.4) is answered inconsistently. The correct fix is subtractive: delete the duplicated `projects/access.py` allow-logic and the inert project/playbook grant surface, fold project sharing into `ResourceGrant`, and route all resource reads through one `accessible_filter(resource_type, user, min_level)`.

---

# Part 2 — Contract ↔ Request ↔ Project: the triangle never forms

The user's headline: *"the contracts which I made in this tool can't be directly used to create a request."* Confirmed, plus five related breaks. The only working link is a single one-way FK.

## 2.1 The request-create surface accepts no contract reference

```python
# intake/schemas.py:50
class RequestCreate(BaseModel):
    type_label: str
    subject: str | None = None
    request_type_id: str | None = None
    department: str | None = None
    priority: str = Field(default="Medium", pattern="^(Critical|High|Medium|Low)$")
    description: str = ""
    field_values: dict | None = None
    requester_name: str | None = None
    source: str = Field(default="form", pattern="^(form|copilot|email|api|seed)$")
    # no contract_id
```

Nine fields, none a contract. Pydantic drops any unknown key at the boundary, so even a hand-crafted call carrying `contract_id` is silently discarded. The FK column exists (`intake/models.py:191`) but is never assigned on the create path.

**Fix.** Add `contract_id: str | None = None` to `RequestCreate` and one validated write in `create_request` mirroring `promote()`'s existence check. The column already exists.

## 2.2 The contract detail page has no request-spawning action

The only `/intake` reference on the 2800-line contract page is a breadcrumb:

```tsx
// contracts/[id]/page.tsx:1514
<Link href="/intake"> <ArrowLeft/> Legal Intake </Link>
```

The "More" menu is documented UTILITY-only (`:1471`). Grep for "New/Raise/Create/File request" returns nothing. From a live contract there is no "raise a review/amendment/renewal request" control.

**Fix.** One More-menu item calling the 2.1 endpoint with the current contract id preset.

## 2.3 The `Contract` row cannot name the request that created it

`Contract` (`contracts/models.py:15-67`) has no `request_id`/`intake_request_id` column. The draft handler writes one side only:

```python
# intake/drafting.py:401
    # Link both directions and record it on the request's timeline.
    request.contract_id = contract.id           # the ONLY real link written
    request.updated_by_user_id = actor.id
    write_audit_log(db, action="intake.contract_drafted", ...)   # the "other direction" is just a log
```

And the `request_id` passed into contract creation is the **HTTP correlation id**, not the intake request id:

```python
# intake/drafting.py:384
    result = await create_contract_from_upload(db, upload=upload, user=actor, title=title,
        counterparty_name=counterparty, contract_type=spec["contract_type"],
        request_id=http_request_id)      # http_request_id = audit correlation id, used only in write_audit_log
```

**Failure.** Draft `C` from `REQ-4123`; weeks later open `C` directly. There is no requester, no `REQ-4123`, no SLA. Recovery needs a full-column scan `SELECT id FROM intake_request WHERE contract_id=...`; if the request was closed and `ondelete SET NULL` fired, even that returns nothing.

**Fix.** Add `Contract.intake_request_id` (FK, `ondelete SET NULL`, indexed) and set `contract.intake_request_id = request.id` right after creation in **both** draft handlers.

## 2.4 `project_id` is a loose string with no DB integrity

```python
# intake/models.py:190
    project_id = Column(String(36), nullable=True)  # selective promotion (matter); no FK
    contract_id = Column(String(36), ForeignKey("contract.id", ondelete="SET NULL"), nullable=True)
```

The asymmetry is deliberate — the next line does `contract_id` correctly. `promote` validates existence at write time but there's no constraint, no `ON DELETE`; and `Project` is soft-deletable (`SoftDeleteMixin`), so a promoted request can point at a tombstone with nothing to catch it.

**Fix.** `project_id = Column(String(36), ForeignKey("project.id", ondelete="SET NULL"), nullable=True)` — one migration, null any orphans first.

## 2.5 The `promote` path is dead: no UI caller

Backend endpoint (`intake/routes.py:322`) and client wrapper (`endpoints.ts:1070`) both exist. Caller: none — `grep -rn "\.promote(" frontend/src/app` = **0 hits** (independently confirmed). And `promote` (`service.py:1010-1025`) is the *only* code that ever executes `r.project_id = p.id`. So `project_id` is always NULL in practice; the "selective promotion (matter)" concept is unreachable.

**Fix.** One "Add to matter" button on the ticket detail calling the existing `intakeApi.promote`. Zero backend change.

## 2.6 A drafted contract is never filed into the request's project

`create_contract_from_upload` already files a contract into a matter when given `project_id`:

```python
# contract_files/service.py:404
    if project_id:
        db.add(ProjectContract(org_id=user.org_id, project_id=project_id,
            contract_id=contract.id, created_by_user_id=user.id, updated_by_user_id=user.id))
```

But both intake draft handlers omit it (`drafting.py:384-392` template path, `:460-463` attachment path) — they pass `title/counterparty/contract_type/request_id`, never `project_id=request.project_id`. So even a promoted request's drafted contract is not inserted into the project's `ProjectContract` table.

**Fix.** Add `project_id=request.project_id` to both `create_contract_from_upload` calls. Two-token diff, twice (sibling-caller case — both paths need it).

## The actual entity graph

```
                         POST /intake/requests
   RequestCreate (schemas.py:50)  ── [2.1] no contract_id field → create-time link impossible
        │
        ▼
   ┌────────────────────────┐  contract_id  FK, SET NULL          ┌────────────┐
   │     IntakeRequest      │ ─────────────────────────────────►  │  Contract  │
   │                        │   written 1-way @ drafting.py:402   │            │
   │                        │ ◄╌╌╌╌╌╌╌  X  ╌╌╌╌╌╌╌  [2.3]         │            │
   │                        │   no request_id column on Contract   └─────┬──────┘
   │  project_id            │   (request_id kwarg = HTTP corr. id)       │ contract_id FK
   │   String, NO FK  ╌╌╌╌╌╌┼╌ [2.4] loose string ╌╌╌┐                  │
   │   written ONLY by      │                          ╎           ┌──────▼─────────┐
   │   promote() [2.5]      │                          └╌ [2.6] ╌► │ ProjectContract │
   │   ZERO UI callers      │            never written from intake │  project_id FK  │
   └────────────────────────┘                                      └──────┬─────────┘
        ▲ [2.2] contract page has no "raise request" action              │ project_id FK
        └── request→contract is the only edge, write-once from intake  ┌─▼─────────┐
                                                                        │  Project  │
  ───►  real FK, reachable          ╌╌╌►  loose/​unpopulated            │ (matter)  │
   X    column doesn't exist                                            └───────────┘
```

**Fix set (no new tables, no new endpoints):** add `Contract.intake_request_id` + write it in both draft handlers (2.3); promote `project_id` to a real FK (2.4); forward `project_id=request.project_id` into both `create_contract_from_upload` calls (2.6); plus two UI wirings — the `contract_id` field feeding a contract-page "raise request" action (2.1/2.2) and a ticket "Add to matter" button onto the already-live promote endpoint (2.5).

---

# Part 3 — Teams, people & assignment: four parallel "group" systems, none connected

Six grouping notions, seven responsibility fields, one defect: every group's only FK points at `user.id`, and the one place a team could become a durable owner (`IntakeRequest`) discards it.

## 3.1 IntakeTeam collapses to one user and is never stored

```python
# intake/models.py:76   IntakeTeam (key, name, strategy=least_loaded|round_robin, overflow_team_id, members)
# intake/models.py:100  IntakeTeamMember (team_id FK, user_id FK, capacity, last_assigned_at)
```

Routing resolves the pool to a single user and drops the team:

```python
# intake/routing.py:82
elif r.set_team_id:
    pick = teams_mod.pick_from_pool(db, team_id=r.set_team_id)   # PoolPick carries team_id AND user_id
    if pick and pick.user_id != w.assignee:
        w.assignee = pick.user_id                                 # only user_id is kept
        actions.append(f"pool {pick.team_name} → {pick.user_name}...")  # team_name → audit string only
# intake/routing.py:107
    request.assigned_to_user_id = w.assignee                      # no team column exists to write
```

`IntakeRequest` (model body `141-193`) has `assigned_to_user_id`, `approval_gate_user_id` … and **no `team_id`**. The team exists only for the microseconds `pick_from_pool` runs.

**Failure.** "List all open requests owned by the Contracts team" is unanswerable: rows carry a user, not a team; reconstructing team-at-routing-time via `assigned_to_user_id → IntakeTeamMember` is ambiguous (multi-team members), lossy (membership edited since via wholesale `t.members = rows`), and wrong for human-reassigned requests.

**Fix.** Add `IntakeRequest.routed_team_id` (FK, SET NULL) and capture `pick.team_id` at `routing.py:82-86`. Then "requests owned by Contracts" = `WHERE routed_team_id = :id`.

## 3.2 Role is a permission bundle, not a team, wired to nothing else

```python
# auth/models.py:15  user_role (user_id, role_id)
# auth/models.py:43  Role.permissions (M2M)  — consumed only by permission_values
```

`Role.id` is never a target of any routing rule, approver step, project member, or intake team. Routing rules (`intake/models.py:129-135`) can `set_assignee_user_id` or `set_team_id`, never `set_role`. So "route to whoever has the Legal Counsel role" forces admins to hand-build a duplicate `IntakeTeam` named "Legal Counsel" and re-enter the same humans.

**Fix.** Keep `Role` as pure RBAC; make the *team* the membership spine (3.7) and point routing at teams. No code change here — stop creating team-shaped roles.

## 3.3 ApproverGroup is a second pool-of-people, twin of IntakeTeam

```python
# approvals/models.py:29  approver_group_member (group_id, user_id)
# approvals/models.py:37  ApproverGroup — docstring: "A named pool of approvers for a business
#                          function (Legal Counsel, Finance, Procurement…)… not a duplicated RBAC role."
```

The docstring flags the overlap it isn't solving — but `ApproverGroup` **is** a duplicate of `IntakeTeam`: both are "named pool of people for a business function," both drawn from by a routing engine (`ApprovalRoutingRule` vs `IntakeRoutingRule`). The only difference is the picker (any/all quorum vs least_loaded/round_robin). Membership lives in two unrelated join tables.

**Failure.** "Who's on the Legal team?" has two authoritative answers (`IntakeTeamMember` vs `approver_group_member`); onboarding a lawyer means editing both, in two admin screens; miss one → they get intake tickets but never approvals (or vice-versa), silently.

**Fix.** Merge `ApproverGroup` and `IntakeTeam` into one `Team` with a `strategy`/`purpose` column. Lazy first step: a DB view unioning both member tables so "who's on Legal" has one query, then converge writers.

## 3.4 ProjectMember is the ONLY grouping that gates contract access — and it ignores teams

```python
# projects/models.py:39  ProjectMember (project_id, user_id, role="member")   # role: a THIRD free-text "role"
```

Joined into contract visibility (`contracts/access.py:95-141` filter, `:179-196` row check) via `ProjectContract → ProjectMember`. `IntakeTeam` and `ApproverGroup` appear nowhere in access control.

**Failure.** "Let the Contracts team see all Contracts-team contracts" has no term in `accessible_contract_filter`. You must create a `Project`, add every teammate as a `ProjectMember`, and link every contract via `ProjectContract`. The group that *works* a matter (intake team) and the group that *can read* the resulting contract (project members) are disjoint sets maintained by hand.

**Fix (once a team spine exists):** add one OR-term `Contract.owning_team_id.in_(select(TeamMember.team_id).where(TeamMember.user_id==user.id))`, mirroring the existing `project_membership` pattern.

## 3.5 Contract ownership is one person, with no team lineage

```python
# contracts/models.py:35  owner_user_id = Column(..., ForeignKey("user.id"), nullable=False)  # one owner, no team FK
```

When an intake request becomes a contract, `IntakeRequest.contract_id` links them, but the request never stored `routed_team_id` (3.1), so even `Contract → IntakeRequest → user → team` hits the same ambiguity.

**Failure.** "Which team carries the most renewals due this quarter" — `renewal_due` is per-contract; there is no `GROUP BY owning_team`. Ownership resolves to individuals, and individuals aren't durably grouped.

**Fix.** Add `Contract.owning_team_id` (FK, SET NULL, nullable), populated at promotion from `IntakeRequest.routed_team_id`.

## 3.6 `department` & "Operations": routing keys with no entity behind them

```python
# intake/models.py:152  department = Column(String(60), nullable=True)
# intake/routing.py:43  case-insensitive string equality against match_department
# intake/page.tsx:329   const DEPARTMENTS = ["Product","Engineering",...,"Operations","Legal","Executive"]  (hardcoded)
```

`department` is free text with no `Department` table, no FK, no membership. "Operations" is both a department string *and* a UI tab id — unrelated tokens sharing a spelling.

**Failure.** A rule with `match_department="Legal "` (trailing space) never fires; `times_fired` stays 0 with no error, because nothing validates the value at either end.

**Fix.** Derive the dropdown from distinct `IntakeTeam` keys (or a small lookup) so routing condition and picker share one source. A full `Department` entity is YAGNI until departments need owners/permissions.

## 3.7 The seven scattered responsibility fields + the spine to adopt

All raw user FKs, six of seven resolving to a lone user; only approvals can name a group (`approver_group_id`), and that group is the disconnected `ApproverGroup`:

```python
# intake:  assigned_to_user_id, approval_gate_user_id, triaged_by_user_id, handoff_user_id (not even an FK)
# intake:  IntakeTask.assignee_user_id
# contracts: Contract.owner_user_id      projects: Project.owner_user_id + ProjectMember.role (3rd "role")
# approvals: approver_user_id / approver_group_id / approver_role
```

**Minimal spine.** (1) One `Team`+`TeamMember` table — promote `IntakeTeam`/`IntakeTeamMember` (richest shape), fold `ApproverGroup` in with a `purpose` column. (2) Add nullable `owning_team_id` FK beside each owner/assignee on `IntakeRequest`, `Contract`, `Project` (the `*_user_id` stays as "current human"; the new column is "durable group owner"). (3) Populate `IntakeRequest.routed_team_id` at `routing.py:82-86`. (4) Add one OR-term to `accessible_contract_filter`. One table consolidation + one nullable FK per ownable entity + one write line + one filter clause turns "which team owns this" from unanswerable into a single indexed column.

---

# Part 4 — Approvals: unified backend, two broken edges

**Do not break this:** contracts and intake share **one** engine, **one** dual-FK table, **one** decide endpoint. `ApprovalRequest` carries both `contract_id` and `intake_request_id` (exactly one set, `approvals/models.py:96-99`); `_subject_clause` (`service.py:253`) and the single write site (`service.py:606-624`) branch on `subject.kind`; both subjects decide through `POST /approvals/requests/{id}/decision`, and the intake ticket UI calls the same `approvalsApi.decide` (`intake/page.tsx:1067`). The two failures are at the edges.

## 4.1 The orphaned `approval_gate_user_id` dead gate

The enforcement function exists:

```python
# intake/service.py:696
def _approval_gate_blocked(db, *, actor, request, attempted, http_request_id) -> None:
    gate = request.approval_gate_user_id
    if not gate or actor.id == gate or is_org_admin(actor): return
    ... record_decision(... "intake:approve" ... "denied" ...)
    raise HTTPException(403, "Approval is gated to a specific person for this request")
```

It is *written* by routing rules:

```python
# intake/models.py:136  IntakeRoutingRule.require_approval_from_user_id
# intake/routing.py:105  request.approval_gate_user_id = w.gate
```

…but its only enforcement read is inside itself, and it has **zero callers** — `grep -rn "_approval_gate_blocked" backend/app` returns only the `def` line (independently confirmed). The actual intake approval enforcement runs through the shared ladder (`plan_chain`/`_apply_decision` + `enforce_authority`), which never consults `approval_gate_user_id`.

**Failure.** Admin builds a rule "For MSA, require approval from Jordan." Files an MSA → `routing.py:105` sets `approval_gate_user_id=jordan`; the UI shows a 🔒 badge. But the ladder rungs are decided by value/type/risk + Tier-0 gates, not this field. **Any** eligible approver (Sam, a Legal Counsel member ≠ Jordan) clicks Approve → succeeds. No 403, no `intake.approval_blocked` audit row. A governance control that appears configured is a silent no-op.

**Fix (preferred = delete):** remove `_approval_gate_blocked`, `require_approval_from_user_id`, `approval_gate_user_id`, and the serializer/schema fields; route "require approval from X" through the ladder's existing per-user rungs (`ApprovalRoutingRule` with `approver_user_id`). Alternative (keep as a hard gate): wire the guard into `IntakeApprovalSubject.guard_can_decide` (`approval_bridge.py:121`), threading the actor through the one `_apply_decision` call at `service.py:771`.

## 4.2 The contract-only `/approvals` frontend

```python
# approvals/routes.py:246  _serialize_approval → emits "contract_id" but NEVER "intake_request_id"
```

`list_approvals` returns all rows (incl. intake ones the viewer can decide), but the serializer strips the intake discriminator. The page then hardcodes a contract link + contract-only title map:

```tsx
// approvals/page.tsx:195
<Link href={`/contracts/${req.contract_id}`}>{titleMap.get(req.contract_id) ?? req.contract_id}</Link>
// titleMap built only from contractsApi.list  →  grep "intake_request_id" frontend/src = 0 hits
```

**Trace.** An intake rung has `contract_id=null`. `titleMap.get(null)=undefined` → blank label; `href="/contracts/null"` → clicking 404s. The row also carries working Approve/Reject buttons (server-computed `can_decide`), so the approver can act on something they can't identify.

**Failure.** Priya (Legal Counsel) has a contract rung on "Acme MSA" and an intake rung (a Tier-0 exclusivity gate on `REQ-4123`). `/approvals` shows the MSA row correctly **and** a blank row linking to `/contracts/null` (that's REQ-4123). The intake rung is usably visible only if she separately opens `/intake`, finds REQ-4123, and opens its governance-ladder panel (a *separate* read path, `intakeApi.approvalChain`). No single inbox despite one backing table.

**Fix.** Add `"intake_request_id": req.intake_request_id` (and ideally a server `subject_label`) to `_serialize_approval`; make the row link/label conditional (`req.contract_id ? /contracts/… : /intake?request=…`). Turns `/approvals` into the unified inbox the backend already supports — no new endpoint, no new decision path.

---

# Part 5 — Process engines: "workflow" names three things

Five candidate engines + one dashboard. One pairing is correctly integrated (Flows ↔ contract lifecycle); the rest are parallel.

## 5.1 Two unsynced progress trackers on one `IntakeRequest`

**Tracker A — intake stage machine.** `request.stage` (`new→assigned→review→complete`) has exactly one writer:

```python
# intake/service.py:331   request.stage = to_stage   (inside _transition; the ONLY writer)
```

**Tracker B — Flow step machine**, on the same request row. Its entire state surface is `run.status`/`run.current_index`:

```python
# flows/service.py:175  advance_run: ... run.current_index += 1   (Flow progress lives here)
# grep "\.stage = " backend/app/flows  →  0 hits   (flows never write request.stage)
```

The only flow→stage writeback is terminal *and* skipped for contract-bearing flows:

```python
# flows/service.py:222
def _finalize_intake_if_approved(db, *, run, actor) -> None:
    if run.contract_id: return          # contract flows never advance request.stage at all
```

**Failure.** REQ-4123 assigned (`stage="assigned"`). A flow drafts a contract (`run.contract_id` set), advances the **contract** to `approval`, `run.current_index=2`. Now: intake queue shows **Assigned**, governance ladder shows **Signature pending**, contract page shows **Approval** — three surfaces disagree. The request can reach a signed, active contract while its intake stage still reads "Assigned"; the `stage_timestamps` SLA trail is incomplete.

**Fix.** `FlowRun` should own progress when a run exists; the `stage` spine becomes its projection (exactly as `contract.lifecycle_stage` already is — see 5.4). Add a `_STEP_TO_INTAKE_STAGE` map and one `_transition(to_stage=...)` writeback at the `current_index` chokepoint (~6 lines).

## 5.2 `WorkflowRun`: an orphan run-table, written once, never read

```python
# workflows/models.py:49  WorkflowRun (status default "queued", output, error_message …)  — shaped like a run engine
# ai/tool_runtime.py:670  run = WorkflowRun(..., status="succeeded", ...)   # hardcoded; never transitioned
```

`grep -rn "WorkflowRun" backend/app | grep -v import` → the `__all__` export + the one write. No `select(WorkflowRun)`, no status update, no reader. Even the analytics that would consume runs deliberately reads `AuditLog` instead (`workflows/routes.py:141`, comment: *"Uses the audit log (not WorkflowRun)"*). The `status` column is structurally incapable of showing anything but `"succeeded"` because `_run_workflow` is a single-shot prompt expansion, not a stepped run.

**Fix.** Delete the model + its `__all__` export; the tool returns its dict directly (the audit-log ping already records usage). One migration to drop the table.

## 5.3 The "workflow" name collision, inverted at the code layer

```tsx
// app-shell.tsx:60   { href: "/workflows",        label: "Prompt Library" }  → workflowsApi → backend app/workflows/ (Workflow/WorkflowRun)
// app-shell.tsx:66   { href: "/workflow-builder", label: "Workflows" }       → flowsApi     → backend app/flows/     (Flow/FlowRun)
```

The module literally named `workflows/` powers the **Prompt Library**; the module named `flows/` powers **Workflows**. Both share the `workflow:*` permission scope, so RBAC can't disambiguate either. An engineer told "fix the workflow engine" opens `backend/app/workflows/` and lands in the prompt library (no executor), while the real engine (`advance_run`, step dispatch) is in `flows/`. There's also a *third* "Workflows" — an approval-ladder builder tab inside Legal Intake (`intake/page.tsx:138`).

**Fix (pure rename).** `backend/app/workflows/` → `prompts/` (`Workflow`→`Prompt`, drop `WorkflowRun`); `/workflows`→`/prompts`, `workflowsApi`→`promptsApi`; leave `flows/` as sole owner of "workflow." Minimum viable: a one-line header comment on `workflows/__init__.py` — *"This is the Prompt Library, NOT the workflow engine — that's app/flows/."*

## 5.4 Flows ↔ contract lifecycle — the one correct pair (reference, don't touch)

```python
# flows/service.py:751  _advance_contract_to → transition_contract_stage (contract's own chokepoint)
# contracts/stage_triggers.py:44  advance_flow_for_contract (contract stage-entry writes back into the Flow)
```

Bidirectional, through single seams on both sides. This is the pattern 5.1 is missing — its fix is precisely the intake-spine half of this bridge.

## 5.5 Playbooks — a review-scoring engine, a third `*Run`, loosely coupled

`PlaybookRun` (`playbooks/models.py:56`) produces `PlaybookDeviation` rows and is a job status (`queued→succeeded`), not a multi-step cursor. It's the third `*Run` table (with `WorkflowRun`, `FlowRun`) where only `FlowRun` is a state machine. A completed run does not advance any contract/flow stage. Notably, the Flow `approval` step (`flows/service.py:542`) submits to the ladder **without** consulting open high-severity `PlaybookDeviation` — so a contract with open critical deviations can pass the Flow approval gate.

**Fix (optional hardening).** Count open high-sev deviations before `submit_contract_for_approval` and route to `waiting_human` if any exist (~4 lines).

## 5.6 SLA — not an engine

`/sla` is a thin dashboard over `IntakeRequest.sla_status`/`sla_hours` (derived posture, no transitions). No structural change. Listed to confirm it's not a sixth engine.

---

# Part 6 — UI / navigation: two competing organizing principles

## 6.1 "Workflows" is a triple-name collision

`NAV` (`app-shell.tsx:40-75`) label and href are independent fields, so nothing enforces that "Workflows" ↔ `/workflows`. The result (mirror of 5.3): the nav item **"Workflows"** opens `/workflow-builder` (flow engine); the route **`/workflows`** is labeled "Prompt Library"; and Legal Intake has a third **Workflows** tab (approval-ladder builder, `intake/page.tsx:138`). A user hunting "workflows" faces an inverted map.

**Fix.** Relabel the `/workflow-builder` nav item "Flow Engine" (`app-shell.tsx:65`) and the Intake tab "Approval Ladders" (`intake/page.tsx:138`, its function is already `WorkflowsBuilderTab`). One string each, zero routing changes.

## 6.2 Approval configuration lives on four surfaces

```tsx
// intake/page.tsx:26   import { RulesTab } from "../approvals/_rules-builder";   // same component…
// intake/page.tsx:1002 <RulesTab />                                            // …rendered inside Intake
// approvals/page.tsx:48 title="Approvals & routing"                            // …and as the /approvals page
```

Plus per-request `ApprovalLadderCard` (`intake/page.tsx:1025`) and Admin → Authority DoA limits (`admin/page.tsx:109`). The same routing-rule editor is mounted at two routes with no breadcrumb telling the user they're the same records — silent last-write-wins collision.

**Fix.** Make `/approvals` the canonical editor; replace the inline `<RulesTab/>` in Intake with a link to it. Keep `ApprovalLadderCard` (per-request) and Admin→Authority (limits) — those are genuinely different.

## 6.3 Five Lifecycle nav pages duplicate the contract-workspace tabs

The contract workspace (`contracts/[id]/page.tsx:160-168`) has in-context Obligations/Renewals panels that link *out* to portfolio pages (`"Open in Obligations →"` `:1281`, `"Manage renewal →"` `:1325`), and those pages link *back* into `/contracts/{id}` (`obligations/page.tsx:234`). The user bounces between contract-scoped and portfolio-scoped views of identical records — the codebase's central tension: a **contract-centric** model and a **concept-centric** model, both fully built, cross-linking into each other.

**Fix.** Make contract-centric primary for *acting*; drop the out-links from the contract panels so in-context work stays in context, and demote `/obligations`/`/renewals` to a portfolio/reports grouping (keep them — they have cross-contract value).

## 6.4 Sibling CTAs on the contract page diverge (the sharpest bug)

```tsx
// contracts/[id]/page.tsx:1462
case "view_approvals":   router.push("/approvals");   break;   // ejects to global list, drops contract context
case "view_obligations": setPanel("obligations");      break;   // stays in the workspace
```

Two adjacent, semantically parallel CTAs behave oppositely. The root cause is that there is no `approvals` entry in `PANELS` (`:160-168`), so the author had to eject.

**Fix.** Add an `approvals` panel (mirroring `obligations`; the page already imports `approvalsApi` and holds `ApprovalChainStep`) and change line 1463 to `setPanel("approvals")`.

## 6.5 `/assistant` is an orphaned duplicate of `/`

`app/(app)/page.tsx:11` and `app/(app)/assistant/page.tsx:10` render the identical `<AssistantWorkspace/>`. Nav points only at `/`; `/assistant` survives only as an `isActive` alias (`app-shell.tsx:164`). A second URL for one screen — splits analytics, two canonical addresses.

**Fix.** Replace `assistant/page.tsx` body with `redirect("/")` (same 8-line pattern as `command/page.tsx`) and drop the alias clause.

## 6.6 `/sla` is mis-grouped under Lifecycle (it's an Intake surface)

`/sla` gates on `intake:read` and describes "the legal intake **queue** … attorney workload, routing-rule effectiveness" (`sla/page.tsx:14-30`) — yet sits in the **Lifecycle** nav group beside Signatures/Renewals. Intake's own code carries a stale comment that SLA is a "first-class tab" (`intake/page.tsx:2145`) though no SLA tab exists there anymore.

**Fix.** Move the `/sla` nav entry to the Workspace group next to `/intake`; fix the stale comment. No route change.

## 6.7 Intake "Teams" has no Admin partner

Team management (`TeamsTab`) is reachable only via Legal Intake → Operations → Teams, three levels deep and admin-only (`intake/page.tsx:2148,2163`). Admin's tabs (`admin/page.tsx:104-111`) are Organization / Users & Access / Roles / Ethical Walls / Authority / Settings / Integrations — no Teams. Admins looking for org structure won't find it.

**Fix.** `TeamsTab` is already a standalone export — render it under a new Admin "Teams" tab (reuse the component, no new UI).

## Orphaned / redirect routes

| Route | Reachability | Verdict |
|-------|--------------|---------|
| `/assistant` | URL only + `isActive` alias | **True orphan** — byte-identical to `/`. Redirect it (6.5). |
| `/command` | `redirect("/intake")` | Intentional bookmark-catcher. Leave. |
| `/contracts` (list) | `redirect("/intake")` | Intentional — portfolio moved to the Intake wall. Leave. |
| `/contracts/[id]` | Deep links only | By design, but the central CLM object has **no browseable home** outside the Intake wall. Watch. |
| `/admin`, `/jobs`, `/notifications` | Account menu / sidebar bottom | Intentional. |

## Part 6 root cause

One decision was never made: **is the contract the primary object, or is each lifecycle concept?** The app ships both and wires them to cross-link, so `RulesTab` renders at two routes, `AssistantWorkspace` at two routes, and one CTA ejects while its sibling stays. Fix: make contract-centric primary for *acting*, concept-centric a "portfolio/reports" grouping for *browsing*; add the missing `approvals` panel so no CTA needs `router.push`; collapse the duplicate mounts. None require new components — they delete duplication or reuse what exists.

---

# Consolidated fix roadmap

Ordered by impact-per-effort. Nothing here needs a new subsystem; most are subtractive or reuse code that already exists.

**Phase A — security (do first):**
1. Thread `min_level` into contract access so a `read` grant can't write (1.1); admin-gate `confidentiality` changes (1.5).
2. Add wall + clearance predicates to `projects/access.py` (1.6, 1.7).
3. Narrow `RESOURCE_TYPES` to `{"contract"}` or wire project/playbook grant reads (1.2); delete the false "unifies" docstring (1.3).
4. One `effective_role_ids(user)` helper used by all four principal builders (1.4).

**Phase B — reconnect the core lifecycle:**
5. Add `Contract.intake_request_id` + write it in both draft handlers (2.3); make `project_id` a real FK (2.4); forward `project_id` into both `create_contract_from_upload` calls (2.6).
6. Wire the two UI actions: "raise request from this contract" (2.1/2.2) and ticket "Add to matter" (2.5).

**Phase C — one team spine:**
7. Consolidate IntakeTeam/ApproverGroup (and stop team-shaped Roles) into one `Team`; add `routed_team_id`/`owning_team_id` nullable FKs; add the team OR-term to contract access (3.1–3.7).

**Phase D — kill dead/duplicate scaffolding:**
8. Delete the `approval_gate_user_id` dead gate (4.1); add `intake_request_id` to the approvals DTO for a unified inbox (4.2).
9. Sync intake `stage` from `FlowRun` (5.1); delete `WorkflowRun` (5.2); rename `workflows/`→`prompts/` to kill the collision (5.3 / 6.1).
10. Add the missing `approvals` contract panel and de-duplicate route mounts (6.2, 6.4, 6.5).

**One-line verdict.** The app isn't broken because features are missing — it's incoherent because the same concept (a group of people, a process, an approval, an access decision, a contract-request link) is modeled two-to-eleven times and the copies never reference each other. Every fix above either deletes a copy or adds the one FK/clause that makes the copies point at each other.
