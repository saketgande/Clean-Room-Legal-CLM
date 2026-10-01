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
3. **Extract text.** New uploads are read first by the Documents reader
   (`structure.read_with_documents_reader` → `app.docstudio` parsers +
   `structure.build`): page furniture (running headers, page numbers,
   e-signature stamps) dropped, Word numbering and tracked insertions kept,
   and elements arrive pre-placed with a clause tree (`parent_id`, `level` =
   depth). When it returns None (scan, damaged or unsupported file, bad
   offsets) the old `extract_text` path runs unchanged; it scores quality and
   below 0.55 falls back to OCR via Reducto (the only provider; Databricks was
   removed 2026-09-29). Existing snapshots are never re-read — their offsets
   are what citations point into. Measured 2026-09-30 on 15 real contracts:
   0 offset errors, 819 clauses nested (0 before), 0.9% of words removed —
   all page furniture.
4. **Persist the row chain:** `StorageObject → Contract → ContractFile →
   ContractVersion → ContractTextSnapshot`, then the snapshot is split into
   `ContractDocumentElement` rows (headings, clauses, tables with char offsets).
5. **Queue jobs:** `metadata_extraction`, `clause_extraction`, `embeddings`.
   The contract then auto-advances INTAKE → REVIEW.

### Counterparty revisions (negotiation rounds)

`POST /contracts/{id}/counterparty-revision` saves their returned file as a new
version AND opens a `RevisionRound` against the version we sent
(`app/contract_files/revisions.py`): clauses matched with the Documents clause
matcher (`docstudio.versions.carry_ids`, ignoring clause numbers so renumbering
isn't a change), each change classed against what WE changed in the version we
sent (changed / countered / reverted / ours / added / removed), and — from a
Word file — flagged `unmarked` when it isn't one of their tracked changes.
Decisions are per change; finishing either agrees (and completes the workflow's
waiting `counterparty` step) or writes our reply version (their text with our
wording back). Playbook findings/comments per change are looked up on read.
### Word editor (ONLYOFFICE) in the contract page

`app/contract_files/editor.py`. On when `ONLYOFFICE_URL` + `ONLYOFFICE_JWT_SECRET`
are set (dev compose sets both; production refuses the published dev secret).
The page gets a config signed with the editor's secret; the editor fetches the
file via a 1-hour link signed with OUR secret (`typ=editor_file`) and saves via
`/editor/contract-saved` — believed only if the editor signed the body AND our
`typ=editor_save` link names the user (access re-checked at save time); the
edited file is fetched only from `ONLYOFFICE_INTERNAL_URL` + the callback's
path (SSRF guard). A save becomes a normal new version (`add_version_from_upload`,
Documents reader, AI jobs). PDFs/generated versions open as the Word export of
their text. Editing during an open revision round makes that round's finish
409 (their version is no longer current) — log their latest file first.

Word text is read with the Documents reader's `_paragraph_text` (keeps tracked
insertions) — `contract_files` now imports from `app.docstudio`, so removing
Documents means moving `parsing/docx._paragraph_text` and `versions.carry_ids`.

### Drafting templates (our standard paper)

"Draft" on a request fills a template (`intake/drafting.render_document`) —
one per playbook: nda, msa, consultancy, sow, vendor, saas, dpa
(`app/drafting_templates`). The shipped text is `defaults/<key>.txt`, each
written to pass the playbook that reviews its contract type; an org's edits
are versions in `drafting_template_version` (newest wins, `body` NULL = back
to the default), made on the Templates page (`/templates`, Intelligence).
Drafted contracts are auto-reviewed by their playbook, so a template that
disagrees with its playbook gets our own paper redlined — change one, check it
against the other. Saves are refused unless every `{placeholder}` is one
`template_values` fills.

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
retrieved. `resolve_scope_contract_ids` gates scope (portfolio / single
contract) and enforces access. There are no Matters (removed 2026-09-29,
migration 0062): contracts aren't grouped, walls are contract-scoped, and
access comes only from owner/creator, admin, pending approver or a grant.

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
for real. Just don't reason as though it isolates tenants today — it doesn't.

Intake has **no sanctions or conflict screening** (removed 2026-09-28: it was
name-only against one US list and blocked nothing). `app/intake/screening.py`
now computes only the counterparty relationship note; the `screening` column
and `intake_screening` job kept their names. Sanctions checks are expected to
happen in the client's own screening tool.

State-changing operations write both `write_audit_log` (immutable compliance
record) and `write_timeline_event` (user-visible history) from
`app/core/audit.py`. Follow that pattern for new mutations.

`write_audit_log` takes an app-wide **advisory lock held until the transaction
commits**. Never write an audit row on a *second* session inline while the
request's own session may already hold it — that request then waits on itself
and every writer in the app freezes (happened 2026-09-28; `core/authz.py`
now writes off-thread). Don't hold an audit write open across slow work (AI
calls commit first). Sessions carry `idle_in_transaction_session_timeout`
(`db_idle_transaction_timeout_ms`, 6 min) as the backstop.

Every new model must be imported in `backend/app/models.py` or Alembic
autogenerate and `create_all` will not see its table.

### Intake triage: form vs free text

A request filed on one of the nine agreement forms (`field_values.request_form`)
is **decided by the form** (`triage_agent.form_triage`): category from the form,
the requester's priority, `needs_info` false, the workflow from "Used for" or the
criteria match, and it auto-starts. The model only writes `ai_triage.read` (the
`intake_form_read` agent: mismatches with the form, bespoke asks, negotiation
points) — advisory, never re-routes or holds. Free text (email, Teams, chat,
notices) keeps full model triage. The owner is never the requester
(`pick_from_pool(exclude_user_id=...)`).

The form's value (in `default_currency`, INR — the forms have no currency
field), dates and parties reach the contract through `apply_request_facts`,
passed as `on_created` to `create_contract_from_upload` so they are on the row
before the AI jobs read it. `field_sources` precedence: user > form > ai.



`IntakeTeam` (Admin → Teams, `app/intake/teams.py`) is the ONLY group of people
that does work: it owns new requests (expertise match, else the one
`is_default_intake` team), does workflow steps (`step.config.team_id`), and
approves (`ApprovalRequest.approver_team_id`, any one or all members). Approver
groups, "pools", the builder's fixed department list and the token→group maps
were merged into it (migration 0056). A workflow step names a team by id — never
add a name/role lookup in front of it. Roles are permissions only; Authority
limits the value someone may approve or sign.

### Workflow steps sit under lifecycle stages

Every workflow step carries `stage` (intake · drafting · review · approval ·
signature · active · closed — the `ContractLifecycleStage` values). Rules live in
`app/workflows/stages.py`: draft/approval/signature steps have a fixed stage,
counterparty is drafting or review, steps follow lifecycle order, parallel steps
share a stage. `_clean_steps` fills missing stages and 422s a bad order. The
lifecycle view (`frontend/src/components/lifecycle-view.tsx`, on the request
overview and the contract page) lists steps under each stage; `currentStage`
there says where a request/contract is (live run's current step → contract
stage → Intake/Closed).

The engine moves the contract: a Drafting/Review step starting brings it forward
to that stage (`_enter_step_stage`), Approval/Signature steps move it in their own
branches, signing reaches Active. A rejected approval (or a signature pulled back)
**sends the workflow back** to the step's `return_to`, else the last Review step
that ran (`_send_back_after_rejection`), recording a `kind="return"` comment the
lifecycle bar shows; it only stops the run when there's no earlier Review/Drafting
step. **Approvals start only from a workflow's Approval step** — the manual
submit route, the intake submit route and the assistant's `submit_for_approval`
tool were deleted (2026-09-29).

Stages that aren't mostly workflow steps get their rows from their own tables —
request events (Intake), signers (Signature), obligations + renewal (Active),
expiry (Closed) — via `app/workflows/lifecycle_rows.py` (`GET /workflows/lifecycle`).
**Every contract gets a workflow:** one uploaded straight into the CLM has no
request, so `start_flow_for_contract` files one (`file_request_for_contract`,
source `upload`) and starts it; a `clm_draft` step on a run that already has a
contract is skipped. Approval authority (Admin → Authority) is shown, not just
enforced: `authority_limits` / `approval_authority_note` feed Teams members and
each run's approval steps (`authority_note`); nothing shows while no policy exists.

**Workflows sit under an agreement type** (`criteria.used_for`: `[{"form":
"<form key>", "agreement_type": <New-agreement kind or null>}]`) and carry
**"Chosen when" conditions** (`criteria.conditions`: `{field, op: is|is_not|
under|at_least|between, value[, value2][, currency]}` — `field: "value"` is the
request's money figure). A **form request only ever gets a workflow of its type
whose conditions all hold** (`select_flow` / `_pick_used_for`): a kind-specific
workflow beats a whole-form one, then more conditions, then `eval_order`. A
blank answer or a value in another currency never meets a condition. If none
fits, the request gets **no** workflow and waits for a person to pick (the
request page shows a picker) — no word match, no catch-all. Word criteria
(`match_type`/`match_keyword`) now only serve untyped workflows for requests
without a form (email, chat). `_clean_conditions` refuses conditions on
questions the form never asks; two enabled workflows with the same type AND
the same conditions are a 409. The list page groups workflows by type; the
builder's settings panel edits type + conditions (`frontend/src/lib/workflow-types.ts`).
The starter workflow for every agreement type lives in
`app/workflows/contract_library.py` (added by "Seed defaults"; value tiers in
INR are starting points). `test_contract_workflow_library.py` checks every
form/kind routes to exactly one of them — keep it passing when you change a tier.

**There are no request types** (removed 2026-09-29, migration 0058). The nine
agreement forms in `app/intake/agreement_forms.json` (mirrored by
`AGREEMENT_FORMS` in the frontend) are the only request forms: a request names
its form in `field_values.request_form`, filing validates against
`form_fields(key)`, and non-form requests (email, chat, the general form) carry
only a `type_label` from `agents.BUILTIN_EXTRA_TYPES`. Don't reintroduce a
type table or `request_type_id`.

The forms' questions (labels, options, required, `show` rules — a question
applies only when every rule matches) live ONLY in `agreement_forms.json`; the
wizard (`frontend/.../intake/_agreement-forms.tsx`) loads them from
`GET /intake/forms` and adds just layout (which step, which control, what each
answer drives). Filing drops answers to questions that don't apply. Change a
question in the JSON; `agreement-forms.test.ts` fails if a question has no step.
A finished amendment / renewal / termination / novation workflow writes its
change onto the parent contract (`apply_change_request`). Word criteria (`match_type`/`match_keyword`) are only the
fallback for requests no workflow is set up for, and neither they nor the triage
AI may override a "Used for" pick (`flow_used_for`, `used_for_suggestion`).

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
