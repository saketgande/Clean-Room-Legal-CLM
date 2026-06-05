# F-09 — Contract-handle TOCTOU race (High) + F-18 + F-30 + F-36

**Agent 2 finding:** Contract-handle generation in three places (`controller._handle_for_contract`, `tool_runtime._find_contracts`, `routes._ensure_contract_handle`) does `count = len(...)` → `INSERT contract-N` with no lock. Two concurrent workers on the same session both compute the same count and both insert the same handle. Compounds with F-18 (three copies of the logic), F-30 (validation divergence), F-36 (different concurrency patterns).

## Headline change
- All three call sites collapse to imports of `allocate_contract_handle(...)` from `app/ai/session_state.py` (CC-2).
- That function takes a Postgres advisory lock keyed by `hash(session_id)`, re-checks for an existing row after the lock, picks the next handle index from `SELECT count(*)`, INSERTs, and catches `IntegrityError` against the new UNIQUE(session_id, handle) index as a last-ditch defense (the lock is the primary correctness mechanism; the index is the safety net for the SQLite test path where the lock is a no-op).
- The unique-constraint check for `requested_handle` (previously only present in `routes._ensure_contract_handle`, missing in `controller._handle_for_contract`) is now uniformly applied to all callers.
- The companion migration `proposals/audit_2026-06-02/rewrites/cross_cutting/migration_session_handle_unique.py` adds the UNIQUE index AND prunes any existing duplicates BEFORE creating it.

## Cross-cutting dependencies
- **CC-2** `app/ai/session_state.py` — `allocate_contract_handle`.
- Migration `0009_assistant_contract_handle_unique.py`.

## Agent 3 done-conditions met
- `tests/test_F09_handle_race.py::test_concurrent_handle_allocation_unique` — 50 parallel calls on the same `(session_id, contract_id)` pair produce exactly one handle row.
- `tests/test_F09_handle_race.py::test_requested_handle_collision_409` — requesting a handle name already in use returns 409 in all three call paths.
- Grep `f"contract-{count}"` returns 0 hits in `app/ai/` and `app/assistant/` after merge.

## Files
- `ai_controller_handles.py` — rewritten `_handle_for_contract`.
- `ai_tool_runtime_handles.py` — rewritten `_find_contracts` handle path.
- `assistant_routes_handles.py` — rewritten `_ensure_contract_handle`.

## Tests
- `proposals/audit_2026-06-02/tests/test_F09_handle_race.py`
- `proposals/audit_2026-06-02/tests/test_cross_cutting.py` — `allocate_contract_handle` unit tests.
