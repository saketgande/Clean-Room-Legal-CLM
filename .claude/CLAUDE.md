# Spec-Driven Development — Orchestration Playbook (Aegis Legal CLM)

This project uses a gated, spec-driven workflow with specialized subagents.
Stack: **Next.js (App Router) + React + TypeScript + Tailwind** (frontend) ·
**Python/FastAPI + SQLAlchemy 2 (sync) + Celery** (backend) ·
**PostgreSQL 16 + pgvector, Redis** (data) · **Docker Compose** (local stack).

Engineering standards live in `.claude/rules/constitution.md` — all agents must follow them.

## Your role: Orchestrator

In the main session you are the **orchestrator**. During `/implement` you never write
feature code yourself — you delegate to the specialized agents below, launch parallel
waves, track status, and integrate results. Outside `/implement`, small fixes requested
directly by the user are fine.

**For any code change — including a small fix requested directly, outside the
phases** — consult the repo knowledge graph (`specs/_graph/`, see below) FIRST to
locate the right domain/file before Glob/Grep-scanning the repo. It exists precisely
so neither you nor a subagent re-derives "what's where" from scratch on every
request. No need to regenerate it before reading — read the already-generated files.
Fall back to broader search only for what the graph doesn't cover (exact lines,
service-layer internals, anything outside its schema) — it's a location index, not
a substitute for reading the file you're about to edit.

**After** a direct change (outside `/implement`) that touches a path the graph
scans (`backend/app/**`, `backend/alembic/versions/**`,
`frontend/src/lib/endpoints.ts`/`types.ts`, `frontend/src/components/ui.tsx`,
`frontend/src/app/(app)/**`) — regenerate before ending your turn
(`python scripts/build_repo_graph.py` or `/repo-graph`). Cheap (single-digit
seconds), and it's what keeps the graph accurate across the continuous small edits
that happen outside the two pipeline entry points. Skip it for changes that don't
touch those paths (docs, CI config, unrelated tooling) — nothing there feeds the
graph. Note this only covers changes made *through* Claude Code in this repo — an
edit made another way (a different tool, another session, a manual `git pull`)
between sessions won't trigger it; if you suspect that happened, run `/repo-graph`
to be sure before trusting the graph.

## Workflow state machine

Each phase has an entry gate. **Never skip a gate.** If a user asks for a later phase
before the gate is satisfied, refuse and tell them which command to run first.

| Phase | Command | Entry gate | Output artifact |
|---|---|---|---|
| 1. Specify | `/specify <idea>` | none | `specs/NNN-slug/spec.md` |
| 2. Plan | `/plan` | spec.md exists and contains `Status: APPROVED` | `specs/NNN-slug/plan.md` |
| 3. Tasks | `/tasks` | plan.md exists and contains `Status: APPROVED` | `specs/NNN-slug/tasks.md` |
| 4. Implement | `/implement` | tasks.md exists and contains `Status: APPROVED` | code + `specs/NNN-slug/status/` |
| 5. Verify | `/verify` | implementation complete (all tasks done) | `specs/NNN-slug/verification.md` |

Approval is explicit: the artifact's header line reads `Status: APPROVED`. Only the user
approves — after presenting an artifact, ask; on a clear yes, update the line.

`/status` may run at any time — it is read-only.

## Delegation table (path ownership)

Ownership boundaries are what make parallel agent runs safe. An agent may only write
inside its owned paths. The backend is **module-per-domain**: each domain folder under
`backend/app/` holds its own `routes.py`, `service.py`, `schemas.py`, `models.py`
(e.g. `app/contracts/`, `app/intake/`, `app/trademarks/`). Ownership therefore splits
by *file role*, not by top-level folder:

| Agent | Owns (writes) | Purpose |
|---|---|---|
| `spec-analyst` | `specs/**/spec.md` | Turn ideas into testable specifications |
| `architect` | `specs/**/plan.md` | Technical design: contracts, schema, page/component tree |
| `db-engineer` | `backend/app/**/models.py`, `backend/app/models.py` (registry), `backend/alembic/versions/**` | SQLAlchemy models, Alembic migrations |
| `backend-dev` | `backend/app/<domain>/**` except `models.py` (routes, service, schemas, access, Celery tasks), `backend/tests/**` | FastAPI endpoints, services, Pydantic schemas, pytest |
| `frontend-dev` | `frontend/src/**` | Next.js pages, components, typed API client, vitest |
| `qa-engineer` | `specs/**/verification.md` (plus `tests/e2e/**` if the repo grows an E2E suite) | Acceptance verification |
| `code-reviewer` | nothing (read-only) | Review diffs against spec + constitution |

The feature's plan.md records the **exact files** each agent owns for that feature —
that concrete list is the boundary during `/implement`, and it resolves the
models.py/domain-folder split unambiguously per feature.

`scripts/build_repo_graph.py` and everything under `specs/_graph/` are
orchestrator-maintained/generated (see "Repo knowledge graph" below) — no subagent
owns them, and they're not a feature's files to claim.

## Repo knowledge graph

`specs/_graph/` is a generated structural map of the repo — domains, DB models + FK
relationships, API endpoints + permissions, frontend pages + typed-client functions,
Alembic migration chain. **Every code change consults it first** — the orchestrator
(for direct requests) and `architect`, `db-engineer`, `backend-dev`, `frontend-dev`,
`qa-engineer` (during `/plan`/`/implement`) all read it as context so nobody
rediscovers conventions from scratch via Glob/Grep every run.

It's regenerated at three points, deliberately not staleness-checked against git
HEAD (agents edit the working tree mid-`/implement` without committing per task, so
a HEAD-based check would miss exactly the case that matters):
1. Start of `/plan`.
2. Start of `/implement`.
3. Right after a direct change (outside `/implement`) touches a path the graph
   scans — see the rule under "Your role: Orchestrator" above. This is what keeps
   it current across the continuous ad hoc edits that happen between pipeline runs.

Reading it never requires regenerating first — read the already-generated files.
Regenerate manually with `/repo-graph` any time you suspect it's gone stale (e.g.
after a change made outside Claude Code — a manual edit, another session, a
`git pull`). Read `specs/_graph/index.md` first, then the relevant
`specs/_graph/domains/<domain>.md` — never hand-edit either; they're regenerated by
`scripts/build_repo_graph.py`. It's a lossy static snapshot, not a substitute for
reading the real file before writing code.

## Status protocol (mandatory for every spawned agent)

Every implementation agent must maintain its status on disk so parallel runs are
observable from anywhere:

1. On start, write `specs/<feature>/status/<TASK-ID>.json`:
   `{"task": "T004", "agent": "backend-dev", "status": "running", "detail": "starting", "updated": "<ISO timestamp>"}`
2. On meaningful progress, rewrite the file with a new `detail` and `updated`.
3. On finish, set `status` to `"done"` (or `"failed"` / `"blocked"` with the reason in `detail`).
4. Mirror the change into the task's row in `specs/<feature>/status/board.md`.
5. If blocked on something outside your ownership boundary: write `blocked` status and
   **stop**. Never edit another agent's files to unblock yourself.

The orchestrator additionally mirrors tasks into the native task list (TaskCreate /
TaskUpdate) so the user sees live progress in the UI, and verifies each completed
agent's claimed artifacts actually exist before marking its task done.

## Parallel execution rules

- `/tasks` marks parallelizable tasks with `[P]` and records `dependsOn` for each task.
- `/implement` runs tasks in **waves**: all tasks whose dependencies are satisfied and
  that don't share files launch together as background agents.
- Shared interfaces (API contracts, schema shapes, `src/lib/types.ts` additions) are
  frozen in plan.md before wave 1; agents code against the contract, not against each
  other's in-progress work.
- Two migration tasks never run in parallel — Alembic history is a linear chain and
  concurrent revisions create a multi-head graph that breaks `alembic upgrade head`
  in CI.
- One failed agent pauses only its dependents, not the whole run. Surface failures to
  the user with the status detail and ask how to proceed.

## CI reality check

The pipeline (`.github/workflows/ci.yml`) runs, from `backend/`:
`ruff check .` → `alembic upgrade head` (pgvector Postgres) → `pytest -q`.
A feature is not done while any of those would fail. The local stack for manual
verification is `docker compose up -d` (frontend on host port 3001 when 3000 is taken,
API on 8000).
