# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AEGIS — a contract lifecycle management (CLM) platform with an AI assistant.
Next.js frontend, FastAPI backend, Celery worker + beat, Postgres (pgvector) and Redis.

## Commands

Everything runs in Docker; the stack brings up its own Postgres and Redis.

```bash
make dev-build       # build + start everything (migrations run automatically)
make seed            # create the default admin — admin@example.com / local-dev-password
make logs            # tail all services
make migrate         # alembic upgrade head, on demand
make shell           # bash in the backend container
make nuke            # stop + DELETE all volumes (full reset)
```

Frontend <http://localhost:3000> · API <http://localhost:8000> (`/healthz`, `/docs`).
Source under `backend/` and `frontend/` is bind-mounted and hot-reloads.

`make prod` / `prod-build` use the base compose file only (built images, no bind
mounts); the default `dev` target auto-merges `docker-compose.override.yml`.

### Tests and lint

Run backend tests **inside the container** — they need Postgres, and the host
Python is usually a different version than the image. The dev tools are
installed as modules but their scripts aren't on the container's PATH, so use
`python -m`. `conftest.py` forces every `MOCK_*` on, so the dev container's
real API keys are never spent by a test run — but tests do write rows into
the dev database.

```bash
docker compose exec backend python -m pytest -q                       # whole suite
docker compose exec backend python -m pytest tests/test_foo.py -q     # one file
docker compose exec backend python -m pytest tests/test_foo.py::test_bar -q
docker compose exec backend ruff check .                    # lint (line-length 100)
```

Frontend, from `frontend/`: `npm run test` (vitest), `npm run typecheck`
(`tsc --noEmit`), `npm run lint`.

CI (`.github/workflows/ci.yml`) runs exactly: `ruff check .` → `alembic upgrade
head` → `pytest -q`. Match it locally before pushing.

`.github/workflows/eval.yml` is manual-only and hits the real Claude API
(real cost). It seeds golden fixtures then runs `eval/run_eval.py`.

### AI quality harness

```bash
docker compose exec backend python eval/run_eval.py                 # retrieval + taxonomy (free, no LLM)
docker compose exec backend python eval/run_eval.py --faithfulness  # also generates answers (costs tokens)
```

### Backend reload caveat

uvicorn `--reload` misses **newly created** files on macOS. After adding a new
Python module, `docker compose restart backend` — edits to existing files
hot-reload fine.

## Architecture

### The document pipeline (the spine of the app)

Every upload path — web (`POST /contracts/upload`), the Word add-in, and
AI-drafted intake documents — funnels into **`create_contract_from_upload`**
(`backend/app/contract_files/service.py`). Read that one function before
changing anything upload-related; it is the orchestrator.

```
validate  →  store bytes  →  extract text  →  persist rows  →  queue AI jobs
```

1. **Validate before persisting.** MIME allowlist, magic-byte sniff
   (`validate_upload_mime` — the client's content-type is untrusted), chunked
   read with a size cap, optional ClamAV scan.
2. **Store.** `storage_service.save_bytes` — local volume or S3, chosen by
   `settings.storage_backend`. Once bytes are on disk, **any exception must
   delete them**; the orchestrator's `except` block does exactly this. Preserve
   that invariant.
3. **Extract text.** `extract_text` (pypdf / python-docx / plain) scores
   quality; below 0.55 it falls back to OCR via Databricks, else Reducto.
4. **Persist the row chain:** `StorageObject → Contract → ContractFile →
   ContractVersion → ContractTextSnapshot`, then the snapshot is split into
   `ContractDocumentElement` rows (headings, clauses, tables with char offsets).
5. **Queue jobs:** `metadata_extraction`, `clause_extraction`, `embeddings`.
   The contract then auto-advances INTAKE → REVIEW.

### Two clause representations — both are needed

- **`ContractDocumentElement`** (`contract_files`) — deterministic, structural,
  from the parser. *Every* block of the document in order, with char offsets.
  This is the complete-coverage backbone.
- **`ClauseExtraction`** (`contract_brain`) — LLM, semantic. Only the
  interesting clauses, classified by type, staleable.

Keep the deterministic layer authoritative and the LLM layer an enrichment on
top. LLM extraction has real recall gaps, and a clause it silently misses would
otherwise not exist anywhere in the system.

### Offsets anchor to the snapshot

`char_start`/`char_end` on both clause tables, and the embedding metadata, are
offsets **into `ContractTextSnapshot.text`**. Re-running OCR with a different
model version produces different text and silently invalidates every stored
offset and citation. Treat the snapshot as a pinned artifact, not a cache.

### Jobs

`create_job` → `dispatch_job` → Celery. All handlers live in one dispatch chain
in `backend/app/jobs/tasks.py` (`_run_ai_job`), keyed on `job.job_type`.

Jobs take a `SELECT ... FOR UPDATE` lock and short-circuit if already
RUNNING/SUCCEEDED, so a redelivered broker message can't double-spend AI calls.
Idempotency keys are `{job_type}:{version_id}:{snapshot_id}`. Periodic work is
in `celery_app.conf.beat_schedule`.

### Retrieval (Contract Brain / Ask Aegis)

`hybrid_sources` (`backend/app/contract_brain/retrieval.py`) is the **single**
retrieval used by both `/search` and `/ask`, so an answer's sources can never
diverge from what Find shows. Four lenses: pgvector cosine over
`ContractEmbedding`, Postgres FTS over `ClauseExtraction`, FTS over raw text,
and the knowledge graph. A cross-encoder reranker over-fetches then narrows.

Vector search is restricted to `contract_version_id ==
Contract.current_authoritative_version_id` so stale versions are never
retrieved. `resolve_scope_contract_ids` gates scope (portfolio / matter /
single contract) and enforces access.

Embeddings: `BAAI/bge-small-en-v1.5`, 384-dim, local via fastembed. Switching
`embedding_provider` to voyage means 1024 dims — a pgvector column migration
plus a full re-embed, since the column dimension is fixed. If fastembed is
unavailable the code **raises** rather than writing meaningless random vectors,
unless `allow_mock_embeddings` is explicitly on.

`contextual_chunking` prepends `[Contract: {title}]` to the **embedded** text
only — never to the stored `chunk_text`, which is what gets quoted back as
citations. Changing chunking or this flag leaves a mixed corpus; run
`backfill_embeddings` to re-embed.

### AI skills

Skills are declared in `backend/app/ai/registry.py` and executed by
`AIController.run_job_skill` (`app/ai/controller.py`). Prompts are versioned in
`app/ai/prompt_versions.py`; outputs are Pydantic schemas in `app/ai/schemas.py`.
Each skill has a feature flag. Add a skill by registering a spec, not by
calling the Claude client directly.

`backend/app/integrations/claude.py` is the only place that talks to Anthropic.
Mock integrations default OFF in code and are switched ON via `MOCK_*` env for
local dev and tests.

### Cross-cutting model conventions

Models compose mixins from `app/core/database.py`: `TableNameMixin` (derives
`__tablename__`), `IdMixin` (uuid string PK), `OrgScopedMixin` (`org_id` on
nearly everything — **keep scoping every query by it**, see below),
`ActorTrackedMixin`, `TimestampMixin`, `SoftDeleteMixin` (includes `legal_hold`).

**This app is single-tenant.** `org_id` is dormant scaffolding, not a live
tenant boundary. `create_first_admin` (`app/auth/service.py`) selects
`Organization` with no filter and returns 409 once one is `setup_complete`, and
`register_user` finds *the* org the same way — there is no code path that
creates a second one, and no request carries a tenant selector.
`app/intake/models.py` says the same thing in its own docstring.

Keep writing `org_id` and keep filtering on it anyway: it costs nothing, it is
what would make multi-tenancy possible later, and the access code
(`accessible_contract_filter`, ethical walls, clearance) filters alongside it
for real. Just don't reason as though it isolates tenants today — it doesn't,
and a few places would break if a second org existed:

- `ux_sanctions_source_ref` is unique on `(source, source_ref)` with **no**
  `org_id`, so only one org can hold the OFAC list, while `screen_sanctions`
  filters by `org_id` — a second org would screen against an empty list.

State-changing operations write both `write_audit_log` (immutable compliance
record) and `write_timeline_event` (user-visible history) from
`app/core/audit.py`. Follow that pattern for new mutations.

Every new model must be imported in `backend/app/models.py` or Alembic
autogenerate and `create_all` will not see its table.

### Routes and permissions

`app/main.py` registers ~36 routers under an API prefix. Route handlers are thin;
the work lives in the sibling `service.py`. Endpoints authorize with
`Depends(require_permission("resource:action"))` (`app/core/deps.py`).

Note: `has_permission()` may be locally patched to always return `True` in a
working checkout — verify before assuming RBAC is exercised in dev.

### Naming history (older notes and code comments may be stale)

- Engine flows were renamed to **workflows** — API `/workflows`, module
  `app/workflows/`, tables `workflow*`.
- The Prompt Library's "workflows" are **`prompt_library`** — API
  `/prompt-library`, frontend route `/prompts`, tables `prompt*`.

Some test filenames still say `flows` (e.g. `test_flows_ai_task_agents.py`)
while importing from `app.workflows.service`.

### Frontend

Next.js App Router under `frontend/src/app/(app)/`. One shared sidebar
(`AegisRail`) for every route — never add a per-screen sidebar. Theming uses the
`slate-*` scale in `globals.css`; **never use `bg-white` or hardcoded colors**,
they break dark mode.

## Migrations

Revisions live in `backend/alembic/versions/`. Feature branches have produced
multiple heads before — check `alembic heads` after merging, and add a merge
revision rather than editing existing ones. (The count used to be written here
and was wrong within a few commits; `alembic heads` is the answer that stays
true.)

## Conventions worth matching

Comments in this codebase explain *why*, and tests carry a docstring naming the
failure mode they guard rather than restating the assertion. Follow both.

`ponytail:` comments mark deliberate simplifications with a known ceiling and an
upgrade path — read one before "improving" the code it sits on.
