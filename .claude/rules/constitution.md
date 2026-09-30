# Engineering Constitution — Aegis Legal CLM

Non-negotiable standards for this codebase. Every agent reads this before writing code.
The architect enforces these in plan.md; the code-reviewer verifies them in /verify.
Where this document and the surrounding code disagree, match the surrounding code and
flag the discrepancy — consistency with the module you're editing beats abstract rules.

## General

1. **Spec is the source of truth.** Code implements what spec.md says — no invented
   features, no silently dropped requirements. Ambiguity goes back to the user as a
   question, never resolved by guessing.
2. **Contract-first.** API request/response shapes, DB schema, and `src/lib/types.ts`
   additions are frozen in plan.md before implementation. Changing a contract
   mid-implementation requires updating plan.md and notifying the orchestrator.
3. **Every task ends verified.** A task is done only when its tests pass locally.
4. **Stay inside your ownership boundary** (see delegation table in `.claude/CLAUDE.md`
   and the feature's plan.md path mapping).
5. **CI is the definition of green**: from `backend/` — `ruff check .`,
   `alembic upgrade head`, `pytest -q`. Never mark work complete that would fail any
   of these.

## Backend — Python / FastAPI

- Python 3.11+ (3.12 in CI/Docker), full type hints, Pydantic v2.
- **Module-per-domain layout.** A feature lives in `backend/app/<domain>/` with
  `routes.py` (thin routers), `service.py` (business logic), `schemas.py` (Pydantic),
  `models.py` (SQLAlchemy), optionally `access.py` (scoping helpers). Follow the shape
  of an existing module (`app/contracts/`, `app/intake/`) rather than inventing a new
  layout. Routers never contain business logic or raw multi-step queries.
- **Sync SQLAlchemy 2.0** (`Session`, `select()`), injected via `Depends(get_db)`.
  This codebase is deliberately sync — do not introduce async sessions.
- **Every route is permission-gated**: `Depends(require_permission("<resource>:<action>"))`
  (see `app/core/deps.py` / `app/core/rbac.py`). No unauthenticated app routes.
- **Everything is org-scoped.** Every query filters by `org_id` from the current user;
  contract-adjacent queries additionally apply `accessible_contract_filter(user)`
  (ethical walls / grants). A missing org filter is a security defect, not a nit.
- **Mutations write audit rows** in the same transaction: `write_audit_log(...)` (and
  `write_timeline_event` where the domain shows one). See `app/core/audit.py`.
- Errors: `raise HTTPException(status, "human message")` — match the existing style of
  the module; correct REST status codes (404 not-found, 409 conflict, 422 validation).
- New settings go on the `Settings` class in `app/core/config.py` (pydantic-settings,
  env-var driven). **If you add a `MOCK_<X>` flag or any security-relevant setting,
  you must also register it in `validate_runtime_settings()` AND update
  `tests/test_phase10_security_hardening.py::test_runtime_settings_accepts_a_correctly_locked_down_production_config`**
  — CI enforces that the "locked-down production config" fixture stays complete.
- External integrations live in `app/integrations/` behind a `MOCK_<NAME>` flag so CI
  and dev run without real keys. Degrade to "not_configured" rather than failing the
  whole request when a key is absent (see `app/trademarks/providers/`).
- Async/background work is a **Celery task** in `app/jobs/tasks.py` dispatched via the
  existing patterns — never a fire-and-forget thread.
- Lint: `ruff check .` from `backend/` must pass. The ignore list in `pyproject.toml`
  (`B008`, `BLE001`, `S110`, `S112`, `DTZ011`) is deliberate — do not add `# noqa`
  for other rules without justification; fix the finding instead.
- Tests: pytest in `backend/tests/`; happy path + at least one failure path per
  endpoint; when you change validation/config behavior, update the tests that pin it.
  Run `pytest -q` (or at minimum the affected files) before reporting done.
- No secrets in code or in test fixtures, ever.

## Database — PostgreSQL 16 + pgvector (Alembic)

- All schema changes go through Alembic migrations in `backend/alembic/versions/`.
  **Never edit a migration that has been applied/committed** — write a new one.
- **Revision IDs: `00NN_short_slug`, and ≤ 32 characters total.** The
  `alembic_version.version_num` column is `VARCHAR(32)`; a longer revision ID passes
  locally until the version-table UPDATE, then fails the whole upgrade. Keep slugs
  terse (`0032_merge_heads`, not `0032_merge_trademarks_and_org_join_request_drop`).
- **One head, always.** After merging branches, if two migrations share a
  `down_revision`, add a no-op merge revision
  (`down_revision = ("<head-a>", "<head-b>")`) so `alembic upgrade head` stays
  unambiguous. CI runs `upgrade head` and fails on multiple heads.
- Every migration has a working `downgrade()`.
- Naming: snake_case tables, `<table>_id` FKs, indexes on every FK and
  frequently-filtered column; `org_id` column + index on tenant-owned tables;
  `created_at`/`updated_at` timestamptz. String UUIDs via `new_uuid()` per existing
  models.
- New models must be imported into the `app/models.py` registry and listed in its
  `__all__` (and only names that actually exist — CI's ruff F822 catches phantoms).
- pgvector: embedding columns follow the existing 384-dim convention
  (`app/ai/embeddings.py`); create the extension idempotently
  (`CREATE EXTENSION IF NOT EXISTS vector`) in migrations that need it.

## Frontend — Next.js (App Router) + React + TypeScript + Tailwind

- Pages live at `frontend/src/app/(app)/<feature>/page.tsx`; colocate feature-private
  components as `_component-name.tsx` in the same folder. Client components declare
  `"use client"`.
- **Shared primitives come from `@/components/ui` only** (Button, Card, Badge, Modal,
  Table, MessageBar, …). Before importing a component from there, confirm it is
  actually exported; if the design needs a new primitive, **add it to
  `src/components/ui.tsx` first**, styled with the existing semantic Tailwind tokens
  (`info`/`success`/`warning`/`danger` + `-subtle`, `brand-*`, `slate-*`) so it works
  in light and dark mode. Importing a non-existent name renders `undefined` and
  crashes the page at runtime — TypeScript alone will not save you if you skip
  typecheck.
- **All HTTP goes through the typed client**: endpoint wrappers in
  `src/lib/endpoints.ts` on top of `src/lib/api.ts`. Components never call `fetch`
  directly. Request/response types live in `src/lib/types.ts` and mirror the backend
  schemas frozen in plan.md — copy them exactly, don't "improve" them.
- Icons: `lucide-react`. Toasts: `useToast` from `@/components/toast`.
- Strict TypeScript; no untyped `any` without a justifying comment.
- Gates before reporting done: `npm run typecheck` (tsc --noEmit) and
  `npm run test` (vitest), from `frontend/`. **Do not run `npm run lint`** — this
  repo's `next lint` has no ESLint config and opens an interactive setup wizard that
  hangs non-interactive sessions.
- Demo mode (`src/lib/demo.ts`) lets the UI run without a backend; when adding a
  feature page, keep it functional in demo mode if the surrounding feature is.

## Local stack & verification

- Full stack: `docker compose up -d` (postgres+pgvector, redis, migrate, backend :8000,
  worker, beat, frontend). Dev override gives hot reload; frontend may be remapped to
  host port 3001 when 3000 is occupied — check `docker-compose.override.yml`.
- Backend-only iteration: run pytest/ruff/alembic directly; the app's test suite is
  self-contained (mocks on, SQLite where applicable).

## Git & hygiene

- Small, task-scoped commits referencing the task ID (`T004: add renewals endpoint`).
- No commented-out code, no debug prints left behind.
- New dependencies require a one-line justification in the task's status detail
  (backend: `pyproject.toml` + `requirements.lock`; frontend: `package.json`).
- Never commit `backend/.env` (real keys live there), `.vs/`, or local port remaps
  unless the user asks.
