# Backend architecture

A FastAPI app organised **by feature**, not by layer. There is no top-level
`models/`, `routers/`, `services/` — instead each feature is a self-contained
package under `app/` that holds its own layers. Learn one module and you know
them all.

## The one convention that runs through everything

Inside a feature package (`app/<feature>/`) the files mean the same thing every time:

| File | Role |
|------|------|
| `models.py` | SQLAlchemy ORM tables for the feature |
| `routes.py` | FastAPI endpoints (thin — validate, call the service, return) |
| `service.py` | The business logic. **Read this first to understand a feature.** |
| `schemas.py` | Pydantic request/response shapes |
| *(others)* | Feature-specific helpers, named for what they do (`lifecycle.py`, `drafting.py`, `access.py`, …) |

Request flow: **`main.py` mounts every router → `routes.py` → `service.py` → `models.py` / DB.**
`main.py` is the index — it lists every router and is the fastest way to see what the API exposes.

## Where things live (by domain)

### Contracts — the core object
Three packages, split by concern (this is the split that most often confuses newcomers):
- **`contracts/`** — the contract itself: lifecycle state machine (`lifecycle.py`), risk (`risk.py`), comments (`comments_*.py`), access rules (`access.py`), stage triggers.
- **`contract_files/`** — the *documents*: file upload, versions, text snapshots, shares (`/contracts/{id}/versions`, `/shares`, `/negotiation-revision`). Files and versions live here, not in `contracts/`.
- **`contract_brain/`** — *retrieval Q&A* over contract text (the RAG layer behind "ask a question about this contract").

### Intake — the front door (the biggest module, ~20 files)
`intake/` is capture → **triage** → route → assign → SLA. Notable files:
- `service.py` — the create/triage/route spine.
- `triage_agent.py` — the context-aware AI triage (classifies, flags missing info, picks the workflow).
- `flow_agent.py`, `litigation_agent.py`, `email_triage_agent.py` — other AI classifiers.
- `teams.py` — the assignment pools (Teams & Routing); `routing.py` — legacy keyword rules; `gates.py` — Tier-0 approval gates; `screening.py` — conflicts/sanctions; `drafting.py` — draft a contract from a request.

### Workflow engine vs Prompt Library (two different things, similar names)
- **`workflows/`** — the **governance workflow ENGINE**: `Workflow`/`WorkflowRun`/`WorkflowStepRun`, the step executor (`service.py: advance_run`), parallel/conditional steps. This is what the `/workflow-builder` designer edits and what runs on a request.
- **`prompt_library/`** — reusable **prompt templates** (`Prompt`/`PromptVersion`/`PromptRun`), surfaced at `/prompts`. NOT an engine. *(Historically these two were named the other way round — see `docs`/git history.)*

### Approvals & signing
- **`approvals/`** — the approval ladder (approver groups, decisions, DoA rungs).
- **`signatures/`** — e-signature requests + recipients.

### Post-execution
- **`obligations/`**, **`renewals/`**, **`notices/`** — obligation tracking, renewal events, the legal-notice register.

### AI (how the assistant thinks and acts)
- **`ai/`** (~22 files) — the AI engine:
  - `controller.py` — the **agentic loop** (streams a turn, calls tools, feeds results back).
  - `tool_registry.py` — every Ask Aegis tool: input schema + permission + category.
  - `tool_runtime.py` — the tool **handlers** (one method per tool).
  - `prompt_versions.py` — all system/skill prompts (incl. `assistant_streaming` — the assistant's output style).
  - `agent_catalog.py` — the standalone-agent registry; `cost_guard.py`, `registry.py` — plumbing.
- **`assistant/`** — assistant *sessions* (the chat's persistence + routes). The `ai/` engine does the thinking; `assistant/` stores the conversation.

### Access control & org (the "admin config" surfaces)
- **`auth/`** (authN), **`roles/`** (RBAC roles), **`authority/`** (Delegation-of-Authority / ABAC action gates), **`grants/`** (object-level access), **`walls/`** (ethical walls), **`organizations/`**, **`admin/`**.

### Other features
- **`projects/`**, **`playbooks/`**, **`tabular_review/`**, **`search/`**, **`word_addin/`** (the Word task-pane backend).

### Cross-cutting infrastructure (not a feature)
- **`core/`** — shared plumbing: `database.py`, `config.py`, `deps.py` (DI + permission guards), `middleware.py`, `exceptions.py`, `logging.py`, `rbac.py`, `enums.py`. Touched by everything.
- **`integrations/`** — external services (`claude.py`, Gmail, storage).
- **`jobs/`** — Celery tasks (async work: clause extraction, screening, SLA sweeps).
- **`notifications/`**, **`observability/`**, **`debug/`** — notifications, metrics/logging, debug endpoints.
- **`main.py`** — app factory + router wiring. **`models.py`** (top level) — re-exports every feature's ORM models so Alembic and cross-module imports have one place to find them.

### Not production code
- **`ideal/`** — a **non-prod design prototype** (in-memory, no schema, only mounted behind a debug flag). It has its own unrelated `Workflow` class — do not confuse it with the real engine in `workflows/`.

## Reading order for a newcomer
1. `main.py` — see every router mounted → the whole API surface at a glance.
2. Pick a feature you care about, open its `service.py` — that's the logic; `routes.py` is just the HTTP shell.
3. `core/deps.py` — how auth, DB sessions, and permission checks are injected everywhere.
4. `ai/controller.py` + `ai/tool_registry.py` — how Ask Aegis calls into all the features.

## Migrations
DB schema changes live in `alembic/versions/NNNN_*.py`, applied with `alembic upgrade head`. Table names derive from the ORM class name (snake_case) via `TableNameMixin`.
