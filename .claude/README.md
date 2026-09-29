# Spec-Driven Multi-Agent Development Toolkit — Aegis Legal CLM edition

A `.claude` folder that turns this repo into a spec-driven, multi-agent development
environment, adapted from the generic Angular+FastAPI toolkit to this project's real
stack: **Next.js (App Router) + FastAPI (sync SQLAlchemy, module-per-domain) +
Celery + PostgreSQL/pgvector + Docker Compose**.

## The pipeline

| Command | What it does | Gate |
|---|---|---|
| `/specify <idea>` | spec-analyst writes a testable spec; you resolve clarifications and approve | — |
| `/plan` | architect designs API contract, DB schema, page/component tree; you approve | spec APPROVED |
| `/tasks` | dependency-ordered task list with `[P]` parallel markers; you approve | plan APPROVED |
| `/implement` | agents build in parallel waves; live status tracking | tasks APPROVED |
| `/verify` | full test run + read-only code review + verification.md | tasks complete |
| `/status` | read-only board of every feature and running agent — any time | — |

Feature artifacts appear in a top-level `specs/` folder (created automatically).
Approval is always explicit and always yours: an artifact advances only when its
header says `Status: APPROVED`, and only you say yes.

## The agents (`agents/`)

spec-analyst · architect · db-engineer · backend-dev · frontend-dev · qa-engineer ·
code-reviewer (read-only). Each has a hard **ownership boundary** (which paths it may
write) — that's what makes parallel execution safe. Because the backend is
module-per-domain (one folder per domain holding routes/service/schemas/models), the
concrete per-feature file boundaries are recorded in each feature's plan.md.

## What's baked in for this repo specifically

- **CI mirror**: done = `ruff check .` + `alembic upgrade head` + `pytest -q`
  (backend) and `npm run typecheck` + `npm run test` (frontend) all green.
- **Alembic discipline**: revision IDs `00NN_slug` ≤ 32 chars, single head always,
  merge revisions after branch merges, models registered in `app/models.py.__all__`.
- **Security posture as a review gate**: `require_permission` on every route,
  org-scoped queries, audit rows on mutations — the code-reviewer treats misses as
  high severity.
- **Settings hygiene**: new `MOCK_*`/security flags must be added to
  `validate_runtime_settings()` and the locked-down-production-config test.
- **Frontend guardrails**: shared primitives only from `src/components/ui.tsx`
  (add missing ones there first), HTTP only via `src/lib/endpoints.ts`, and
  **never run `npm run lint`** (interactive wizard trap — use typecheck + vitest).

## Monitoring parallel runs

1. **Live in-session** — `/implement` mirrors every task into Claude Code's native
   task list and launches agents as background tasks.
2. **On disk** — every agent heartbeats to `specs/<feature>/status/`
   (`board.md` + one JSON per task). Run `/status` in any session — or just open
   `board.md`.

## Customizing

- Engineering standards: `rules/constitution.md`
- Artifact shapes: `templates/`
- Agent behavior/boundaries: `agents/*.md`
- Workflow steps and gates: `skills/*/SKILL.md`
- Orchestration rules: `CLAUDE.md`
