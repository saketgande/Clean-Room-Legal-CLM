# F-06 — AssistantSession admin read override (High)

**Agent 2 finding:** `_get_session_for_user` at `backend/app/assistant/routes.py:512-520` enforces `session.created_by_user_id != current_user.id` as a 404, with no admin bypass. Org admins investigating runaway tool loops or sensitive AI conversations had to drop to raw SQL — bypassing audit logging.

## Headline change
- New split: `_get_session_for_user_read` (admins allowed within same org) vs `_get_session_for_user_write` (creator only). All read endpoints (`GET /sessions/{id}` and friends) call `_read`; mutating endpoints (`PATCH`, `POST /contracts`, `POST /stream`, `POST /resume`) stay on `_write`.
- Admin reads of another user's session write an `assistant.session.admin_read` audit row with `session_creator_user_id` in the `after` payload.
- Admin-detection routes through `ContractAccessPolicy.can_read_session(session, user)` (CC-1) so admin semantics live in exactly one place.

## Cross-cutting dependencies
- **CC-1** `app/core/access_policy.py` — `ContractAccessPolicy.can_read_session` / `can_admin_read_session` / `can_write_session`.

## Agent 3 done-conditions met
- `tests/test_F06_admin_session.py::test_admin_can_read_other_user_session` — org admin reads a session created by another user and gets the row back (200, not 404).
- `tests/test_F06_admin_session.py::test_admin_cannot_write_other_user_session` — same admin attempting `PATCH` returns 404.
- `tests/test_F06_admin_session.py::test_admin_read_audited` — the read writes an `assistant.session.admin_read` audit row.

## Files
- `assistant_routes_session_access.py` — rewritten access helpers.

## Tests
- `proposals/audit_2026-06-02/tests/test_F06_admin_session.py`
