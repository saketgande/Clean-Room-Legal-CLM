# Backend Structure Audit — `backend/app`

**Verdict:** The confusion is **not** piles of dead legacy features. 33 of 35
modules are live and wired into the app. The problem is **rename debt** — two
renames were applied to directory names but never to the code inside them, so
the same thing answers to two names. That's what makes the structure hard to read.

Actually-dead code is small (§3). Fixing the *names* (§1–2) is 90% of the win.

---

## 1. Rename debt — the real cause of the confusion (highest impact)

### 1a. `flows` → `workflows` (engine) — directory renamed, internals not

- Directory is `app/workflows/`, but the code inside still says **`flow`** everywhere:
  `list_flows`, `start_flow`, `select_flow`, `serialize_flow`, `advance_flow`,
  `seed_builtin_flows`, `create_flow`, local vars `flows`, `f`.
  **~171 occurrences across 5 files** (`service.py` 102, `routes.py` 36, `models.py` 15,
  `builtin.py` 9, `__init__.py` 4).
- **Worst artifact — `workflows/__init__.py`** literally says:
  > "Named `flows` (not `workflows`) because `app.workflows` is already the Prompt Library."

  Every clause is now false: the directory *is* `workflows/`, and the Prompt
  Library is now `prompt_library/`. Anyone reading this to understand the module
  is actively misled. **Fix this docstring first — it's a 2-minute, zero-risk win.**
- Callers across the app still import the `flow`-named functions
  (`intake/service.py`, `intake/flow_agent.py`, `intake/litigation_agent.py`,
  `contracts/stage_triggers.py`, `ai/tool_runtime.py`), so a rename touches those too.

### 1b. `projects` → `matters` — directory renamed, internals not

- Directory is `app/matters/`, but **165 internal `project` references** remain:
  `list_projects`, `search_projects`, `user_has_project_access_for_contract`, etc.
- The router is **mounted twice** — `main.py:156` at `/matters` (canonical) and
  `main.py:158` at `/projects` (a "deprecated alias until Matters ships"). Two URLs,
  one module.
- AI tool surface still calls it projects: `ai/tool_registry.py:323`
  `_register("list_projects", ...)`, `ai/tool_runtime.py:363/661` `_list_projects`,
  `search/routes.py:242` `GET /projects`.

**Why this matters:** a reader can't tell whether `matters` and `projects` are the
same thing or two features. They're one. Same for `flows`/`workflows`.

---

## 2. Naming — modules whose purpose isn't obvious from the name

These aren't wrong, just opaque. Worth a one-line module docstring (or rename) so
the layout is self-describing:

| Module | What it actually is |
|--------|--------------------|
| `walls/` | ? (verify — likely ethical/information walls) |
| `authority/` | delegation-of-authority / approval limits |
| `grants/` | access grants |
| `roles/` | RBAC roles |
| `ideal/` | **non-prod redesign prototype** (see §3.1) — name gives no hint it's throwaway |

Recommendation: not renames — just add a first-line docstring to each `__init__.py`
saying what it does. Cheap, and it fixes "I can't tell these apart from the name."

---

## 3. Genuinely unused code (verified zero references outside its own definition)

### 3.1 `app/ideal/` — dead in production (highest cleanup value)
- Mounted only when `environment in {local, development, test}` (`main.py:185-189`);
  never in prod. Only one reference to `app.ideal` in the whole backend (that mount).
  Self-referential otherwise.
- It's a redesign prototype (in-memory, no schema). **Keep or delete is a product
  call, not a code call** — but at minimum its name should signal "prototype", and
  it should live outside the prod module tree (e.g. `sandbox/` or `prototypes/`).

### 3.2 Zero-reference symbols — safe to delete
- `contract_files/schemas.py:8` — `class StorageObjectResponse`
- `intake/agents.py:141` — `def priority_hint`
- `matters/access.py:93` — `def user_has_project_access_for_contract`
- `ideal/workflow.py:648` — `def rounds_of` (inside the already-dead module)

### 3.3 Test-only symbol
- `ai/agent_catalog.py:65` — `def all_agents` — only used by
  `tests/test_agent_catalog.py`, no production caller.

### Verified NOT dead (don't touch)
- `contract_brain/lineage.py` `looks_like_amendment` / `infer_parent` → used by
  `scripts/backfill_lineage_edges.py` + tests.
- `app/devtools.py` → CLI entry point (`python -m app.devtools seed`).
- `integrations/databricks.py`, `search/fts.py`, `contract_brain/clause_taxonomy.py`
  → imported in production paths.

---

## Recommended order (each step independently shippable)

1. **Fix `workflows/__init__.py` docstring** — 2 min, zero risk, removes the single
   most misleading text in the backend.
2. **Delete §3.2 dead symbols** — tiny diff, no behavior change.
3. **Decide on `ideal/`** — keep-as-prototype (rename/move) or delete. Product call.
4. **Finish `flows→workflow` rename inside `app/workflows/`** — mechanical but wide;
   do it as one isolated PR (rename functions + update the ~6 caller files).
5. **Finish `projects→matters` rename** — same; also drop the `/projects` alias mount
   once the frontend stops calling it (check frontend first).

Steps 4–5 are the big ones. They're low-risk (pure renames, caught by tests/imports)
but wide diffs — best done one at a time, not mixed with logic changes.
