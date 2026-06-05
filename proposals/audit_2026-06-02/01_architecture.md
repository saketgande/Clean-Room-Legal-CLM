# AEGIS Legal CLM — Architecture Brief (Agent 1: The Archaeologist)

**Date:** 2026-06-02
**Scope:** Full-codebase descriptive map for Agents 2/3/4. Backend-heavy. No critique, no recommendations.

---

## 1. One-sentence purpose

AEGIS Legal CLM is a multi-tenant FastAPI + Next.js contract-lifecycle-management platform whose distinguishing feature is a backend-owned Claude AI engine that runs structured "skills" and a tool-using assistant against contracts and produces cited, validated, audit-logged outputs.

---

## 2. Tech stack & runtime

- **Backend language / framework:** Python 3.11+ (Docker image uses python:3.12-slim); FastAPI (`>=0.115`) with `uvicorn[standard]`.
- **ORM:** SQLAlchemy 2.0 (declarative, `DeclarativeBase`). Sessions via a single module-level `SessionLocal` (`backend/app/core/database.py:84-86`), `expire_on_commit=False`.
- **Migrations:** Alembic 1.13+. Versions live in `backend/alembic/versions/0001..0008_*.py`.
- **Database:** Postgres (production), with `pgvector/pgvector:pg16` image in `docker-compose.yml`. Driver `psycopg[binary]>=3.2.3`, with `pgvector.sqlalchemy.Vector(384)` columns. Tests appear to run on SQLite (audit advisory lock is guarded `try/except` because SQLite lacks `pg_advisory_xact_lock` — see `app/core/audit.py:54-60`).
- **Cache / broker:** Redis 7 (alpine) for both Celery broker and result backend (`app/jobs/celery_app.py:6-10`).
- **Queue:** Celery 5.4 with the `[redis]` extra. One worker container in compose. Tasks in `app/jobs/tasks.py`.
- **HTTP client:** `httpx>=0.27.2` (long-lived `AsyncClient` instances for Claude/DocuSign).
- **Auth:** `bcrypt` for passwords, `pyjwt[crypto]` for JWTs (HS256). NOTE: `pyproject.toml` still references `python-jose` in a code review document — actual code uses PyJWT.
- **Rate limiting:** `slowapi>=0.1.9` mounted as middleware; per-route `@limiter.limit(...)` on auth endpoints. Keyed via shared `limiter` instance (`app/core/rate_limit.py`).
- **Resiliency:** `tenacity>=9.0.0` (used in `app/integrations/claude.py:208-221` for Anthropic 429/5xx retry).
- **Document handling:** `pypdf>=5.0.1`, `python-docx>=1.1.2`, `python-magic>=0.4.27`, `openpyxl>=3.1.2`, `reductoai>=0.22.0` (OCR).
- **AI SDK:** `anthropic>=0.40.0` listed in deps but the code calls Anthropic's HTTP messages API directly via `httpx` (`app/integrations/claude.py:190-224`) rather than going through the SDK.
- **Embeddings:** `fastembed>=0.4.2` (model id `BAAI/bge-small-en-v1.5`, dim 384). Used by `app/ai/embeddings.py`.
- **Frontend:** Next.js 15 (app router), React 19, Tailwind 3.4, TanStack Query 5, react-markdown 9, recharts 2. Tests via Vitest + Testing Library (one test file: `markdown.test.tsx`).
- **Deployment shape:** Docker Compose (`backend/docker-compose.yml`): services `api`, `worker`, `postgres`, `redis`; a separate `docker-compose.prod.yml` exists. The `start.sh` runs Postgres+Redis in Docker and api/worker/frontend bare-metal for local dev.

---

## 3. Layered architecture

### Routing layer (FastAPI)
- **App factory:** `backend/app/main.py:59-130` (`create_app()`). The module-level `app = create_app()` is what uvicorn loads.
- **Lifespan:** `backend/app/main.py:42-56` starts the batched RequestLog writer and registers shutdown hooks that close the long-lived Claude/DocuSign HTTP clients.
- **Middleware stack (in order):** `SlowAPIMiddleware` (rate limit) → `RequestContextMiddleware` (per-request id + RequestLog) → `CORSMiddleware` → optional `TrustedHostMiddleware` → optional `HTTPSRedirectMiddleware`.
- **All routers mounted under `settings.api_v1_prefix` (default `/api/v1`)** (`main.py:101-124`). Twenty-two routers registered: `auth_router`, `users_router`, `organizations_router`, `projects_router`, `contracts_router`, `contract_files_router`, `external_share_router`, `ai_router`, `assistant_router`, `workflows_router`, `playbooks_router`, `hub_router`, `contract_brain_router`, `approvals_router`, `signatures_router`, `obligations_router`, `renewals_router`, `tabular_review_router`, `search_router`, `notifications_router`, `jobs_router`, `admin_router`, `debug_router`.
- **Exception handlers:** Registered via `app.core.exceptions.register_exception_handlers`.

### Service layer
- Each domain has a `service.py` of plain module-level functions (no service classes). The AI subsystem has the only class-based controllers: `AIController` (singleton `ai_controller`) and `ToolRuntime` (singleton `tool_runtime`).
- Functions typically take `(db: Session, *, ..., user: User)` as kwargs and call helpers in `app/core/audit.py` (`write_audit_log`, `write_timeline_event`) and `app/contracts/lifecycle.py` (`transition_contract_stage`).
- Commits live mostly in **routes** for human-driven endpoints, but the service layer also commits in many places (e.g. obligations route, signatures service, AI controller calls `db.commit()` repeatedly). Pattern is "commit early, commit often" rather than unit-of-work-at-the-edge.

### Data layer
- **Base classes / mixins:** `app/core/database.py:33-67` — `IdMixin` (string-36 UUID PK), `TimestampMixin` (`created_at` / `updated_at`), `OrgScopedMixin` (mandatory `org_id`), `ActorTrackedMixin` (`created_by_user_id`, `updated_by_user_id`), `SoftDeleteMixin` (`deleted_at`, `deleted_by_user_id`, `legal_hold`), `TableNameMixin` (auto-snake-case).
- **All models registered at import time** through `backend/app/models.py`, which re-imports every domain `models.py`. Alembic's `env.py` (not opened but inferred) walks this for autogeneration.
- **Engine:** `create_engine(settings.database_url, pool_pre_ping=True, pool_size, max_overflow, pool_recycle, pool_timeout)` (`app/core/database.py:72-79`).
- **Session lifetime:** `get_db()` dependency yields a `SessionLocal()` and explicit `db.rollback(); raise` on exception (`app/core/deps.py:17-32`).
- **Naming:** SQLAlchemy `MetaData` uses an explicit `NAMING_CONVENTION` mapping (`app/core/database.py:18-24`) so Alembic-generated constraint names are deterministic.

### Integration layer
- All integrations live in `app/integrations/`. Each integration exposes a singleton client (`claude_client`, `docusign_client`, `reducto_client`, `resend_client`, `storage_service`).
- **Mock-vs-real switch:** Each has a `settings.mock_<name>` boolean (`mock_claude`, `mock_docusign`, `mock_reducto`, `mock_resend`). On `True`, the client returns a canned/mock response without making a network call. `validate_runtime_settings` (`app/core/config.py:142-182`) refuses to start the app in non-local environments with any mock still on.
- **Long-lived `httpx.AsyncClient`:** Claude (`app/integrations/claude.py:38-53`) and DocuSign (`app/integrations/docusign.py:25-41`) keep module-level shared clients. Resend opens a per-call client (`app/integrations/resend.py:21-31`).

### AI / LLM layer
The most architecturally elaborate part of the codebase. Files in `backend/app/ai/`:

- **`controller.py` (the orchestrator, ~1680 LOC, recently modified).** `AIController` exposes three public flavours:
  - `run_structured_skill(...)` — single Claude call that demands a structured tool-use response matching a Pydantic `output_model` schema. Persists `AISkillRun`, `AICallLog`, citations, and per-skill side effects (metadata / clauses / obligations / renewals).
  - `stream_assistant_run(...)` — async generator yielding SSE events; runs the tool-use loop up to `settings.ai_max_tool_iterations` (default 8). Detects `confirmation_required` mid-loop and pauses the run.
  - `resume_assistant_run(...)` — re-enters the tool loop after a user confirms a paused tool call.
- **`tool_registry.py` (recently modified)** — A `ToolRegistry` singleton holds 24 `ToolSpec`s (each with `name`, `category` from `AssistantToolCategory`, `required_permission`, `input_model` Pydantic, `confirmation_policy`, `feature_flag`, `exposed_session_types`, `idempotency_strategy`). Tools registered: `read_contract`, `find_in_contract`, `list_project_contracts`, `get_contract_status`, `list_workflows`, `list_playbooks`, `run_workflow`, `generate_contract_docx`, `edit_contract`, `replicate_contract_version`, `run_playbook_review`, `redline_against_playbook`, `ask_contract_brain`, `submit_for_approval`, `send_for_signature`, `extract_obligations`, `create_tabular_review`, `read_table_cells`, `external_share`, `archive_contract`, `my_attention_items`, `find_contracts`, `list_obligations`.
- **`tool_runtime.py` (recently modified, ~1846 LOC)** — `ToolRuntime` validates input via the spec's pydantic model, writes an `AssistantToolCall` row, branches on `requires_confirmation` (returns `confirmation_required=True` + creates an `AIConfirmation`) or runs the tool inline via `_execute_validated()` (a long if/elif dispatch). Includes the DOCX rendering helpers (`_build_redline_docx`, `_render_structured_contract_docx`, `_enable_word_track_revisions`, `_append_deleted_text`/`_append_inserted_text` using `python-docx` low-level OOXML), suggestion anchoring (`_anchor_suggestions`, `_apply_anchored`), and idempotency-key generation.
- **`registry.py`** — `SkillRegistry` (separate from `ToolRegistry`) registers 13 `SkillSpec`s: `contract_metadata_extraction`, `clause_extraction`, `assistant_streaming`, `contract_docx_generation`, `contract_edit_suggestions`, `obligation_extraction`, `renewal_extraction`, `contract_brain_query_parse`, `contract_brain_answer`, `tabular_cell_extraction`, `tabular_review_chat`, `playbook_review`. Each carries `prompt_key`, `prompt_version`, `output_model`, `feature_flag`, `max_tokens`, `temperature`.
- **`prompt_builder.py`** — Single `PromptBuilder` class builds a `BuiltPrompt(system_prompt, user_prompt, context_manifest)`. Combines `prompt_bundle.shared_system_prompt`, the skill-specific prompt, the input payload (with long text redacted), and an "Untrusted contract text:" block.
- **`prompt_versions.py`** — Database-backed prompt versions (`AIPromptVersion` model), with `DEFAULT_SKILL_PROMPTS` baked into the file as fallbacks and a single `SHARED_LEGAL_SYSTEM_PROMPT` constant. `get_active_prompt_bundle(db, org_id, prompt_key, default_version, model_config)` resolves the active version for an org.
- **`prompts/`** — Markdown files (`*.v2.md`) with the canonical prompt text for several skills.
- **`citations.py`** — `validate_citation()` fuzz-matches a quote against source text using `rapidfuzz` (with `SequenceMatcher` fallback). Thresholds 82.0 for OCR, 90.0 for native text, raised to 92.0 for short citations.
- **`redaction.py`** — `redact_ai_payload()` collapses long text/contract-content fields in stored AI inputs to `<redacted text length=N>`.
- **`confirmations.py`** — `create_confirmation`, `confirm_confirmation`, `reject_confirmation` over `AIConfirmation`. Confirmations expire after 30 minutes (`DEFAULT_CONFIRMATION_MINUTES`).
- **`context.py`** — `build_contract_context(db, ...)` resolves a contract → its current authoritative version → its text snapshot and truncates text to `MAX_FULL_TEXT_CHARS = 25_000` for prompt-stuffing. Also provides `chunk_text()` for embeddings.
- **`skill.py`** — Single `SkillSpec` dataclass.
- **`embeddings.py`** — `generate_embeddings_for_snapshot` chunks text and inserts `ContractEmbedding` rows. Tries `fastembed`; falls back to a deterministic random vector seeded by `hashlib.sha256(chunk)` if fastembed isn't available (head-of-file imports).
- **`schemas.py`** — All Pydantic skill output models (`ContractMetadataOutput`, `ClauseExtractionOutput`, `ContractEditSuggestionsOutput`, `ObligationExtractionOutput`, `RenewalExtractionOutput`, `BrainAnswerOutput`, `TabularCellOutput`, `PlaybookReviewOutput`, `AssistantAnswerOutput`, etc.), citation IO (`CitationInput`, `CitationValidationResult`), and API response models (`AISkillRunResponse`, `AIPromptVersionResponse`, `SkillInfo`).
- **`fallback.py`** — `fallback_metadata_from_text()` for when Claude fails on a metadata extraction job (used in `controller._fallback_output`).
- **`routes.py`** — `/ai/skills`, `/ai/skill-runs`, `/ai/prompt-versions`, and rerun endpoints (`/ai/contracts/{id}/metadata-extraction`, `/clause-extraction`, `/embeddings`).
- **`evals.py`, `errors.py`, `service.py`, `streaming.py`, `usage.py`, `validation.py`** — Small helper modules; `service.py` is nearly empty (just imports).

---

## 4. Domain modules

Each backend domain follows the same pattern (`routes.py` + `models.py` + sometimes `service.py` / `schemas.py` / `access.py` / `lifecycle.py`).

### `admin`
- **Purpose:** Per-org admin settings (KV with secret flag).
- **Files:** `routes.py`. No models (uses `AdminSetting` from `core/models.py`).
- **Endpoints:** `GET /admin/settings`, `PUT /admin/settings`.
- **Depends on:** `core.models.AdminSetting`, `core.audit`.

### `approvals`
- **Purpose:** Contract approval requests, decisions, routing rules, single-use email-token approvals.
- **Files:** `routes.py`, `service.py` (recently modified), `models.py`.
- **Endpoints:** `GET /approvals`, `GET/POST /approvals/routing-rules`, `POST /approvals/requests`, `POST /approvals/requests/{id}/decision`, `POST /approvals/token-decision`.
- **Top-level service functions:** `evaluate_routing`, `submit_contract_for_approval` (async, sends Resend email), `decide_in_app`, `redeem_token_decision`, `_apply_decision`.
- **Depends on:** `contracts.lifecycle`, `integrations.resend`, `core.audit`, `core.security` (token mint/hash).

### `assistant`
- **Purpose:** Conversational session/run/message/tool-call surface; SSE streaming endpoint.
- **Files:** `routes.py`, `models.py`, `tools.py` (not opened — likely small/legacy).
- **Endpoints:** `GET /assistant/tools`, CRUD on `/assistant/sessions`, `GET /assistant/sessions/{id}/messages`, `GET /assistant/sessions/{id}/runs`, `GET /assistant/runs/{id}`, `GET /assistant/runs/{id}/tool-calls`, `POST /assistant/sessions/{id}/stream` (SSE), `POST /assistant/runs/{id}/resume` (SSE), `POST /assistant/confirmations/{id}/confirm`, `POST /assistant/confirmations/{id}/reject`.
- **Depends on:** `ai.controller`, `ai.confirmations`, `ai.tool_registry`, `ai.citations`, `contracts.service`, `projects.access`.

### `auth`
- **Purpose:** User identity, sessions (access+refresh JWT), invitations, password reset, org join requests, API keys, role switching.
- **Files:** `routes.py`, `service.py`, `models.py`, `schemas.py`.
- **Endpoints (router `/auth`):** `POST /setup/first-admin`, `POST /register`, `POST /login`, `POST /refresh`, `POST /logout`, `POST /active-role`, `POST /invitations/accept`, `POST /password-reset/request`, `POST /password-reset/confirm`, `GET /me`.
- **Endpoints (router `/users`):** list/decide-approval for users, invitations CRUD, join-request decision, api-keys CRUD.
- **Models:** `Permission`, `Role`, `User`, `RefreshToken`, `RevokedAccessToken` (per-jti revocation table — added in migration `0008`), `PasswordResetToken`, `ApiKey`, `UserInvitation`, `OrgJoinRequest`, `UserApprovalDecision`.
- **Depends on:** `core.security` (`hash_password`, `verify_password`, `create_access_token`, `decode_access_token`, `hash_token`, `create_token_secret`), `core.rate_limit`, `integrations.resend`.

### `contract_brain`
- **Purpose:** RAG + knowledge-graph retrieval over contracts; runs Claude's "brain answer" skill.
- **Files:** `routes.py`, `retrieval.py`, `ingestion.py`, `models.py`.
- **Endpoints:** `POST /contract-brain/ask`, `GET /contract-brain/queries`, `GET /contract-brain/precedents`, `POST /contract-brain/ingest`.
- **Models:** `KnowledgeNode`, `KnowledgeEdge`, `ClauseExtraction`, `BrainQuery`.
- **Retrieval pipeline** (`retrieval.py`): `resolve_scope_contract_ids` → `_vector_chunks` (pgvector cosine on `ContractEmbedding.embedding`) + `_graph_facts` (`KnowledgeEdge` + bulk-loaded `KnowledgeNode`s) + `_fulltext_clauses` (Python-ranked keyword over `CLAUSE_CANDIDATE_POOL=600` candidates). `assemble_context()` concatenates the three sources with `[graph]`/`[snippet]`/`[clause:type]` prefixes.
- **Ingestion** (`ingestion.py`): Reads existing `ClauseExtraction`, `ContractParty`, `Obligation`, `ApprovalRequest`, `SignatureRequest`, `PlaybookDeviation` and builds graph nodes/edges. Marks prior graph stale rather than deleting.
- **Depends on:** `ai.controller`, `ai.embeddings._embed`, `contracts.service`, `projects.access`.

### `contract_files`
- **Purpose:** Storage objects, contract files, versions, text snapshots, edits, embeddings, external shares.
- **Files:** `routes.py`, `service.py`, `models.py`, `schemas.py`, `text_extraction.py`.
- **Endpoints (router `/contracts/{contract_id}`):** files/versions/edits/shares — `list_contract_files`, `list_contract_versions`, `get_version_text_snapshot`, `download_contract_version`, `list_contract_edits`, `accept_contract_edit`, `reject_contract_edit`, `restore_contract_version`, `delete_contract_version`, `list_contract_shares`, `create_contract_share`, `revoke_contract_share`.
- **Endpoints (router `/external-shares`):** `view_external_share`, `download_external_share` — unauthenticated, token+optional-passcode auth via `_get_active_share`.
- **Models:** `StorageObject` (sha256+size+mime), `ContractFile`, `ContractVersion`, `ContractTextSnapshot`, `ContractEdit`, `ContractShare`, `ContractEmbedding` (pgvector `Vector(384)`).
- **Service:** `create_contract_from_upload` (orchestrator), helpers `_read_upload_with_limit` (streaming size cap), `_sniff_mime_type` (magic-byte check), `_resolve_extracted_text` (native + OCR fallback), `_persist_intake_records`, `_queue_initial_contract_jobs`, `queue_activation_ai_jobs`, `next_version_number`, `requeue_contract_ai_jobs`.
- **Text extraction** (`text_extraction.py`): pypdf, python-docx, plain text, image (OCR-required). Caps text at `settings.pdf_max_extracted_text_bytes = 8 MB`. Triggers OCR fallback when `quality_score < 0.55`.
- **Depends on:** `integrations.reducto`, `integrations.storage`, `jobs.service`, `projects.access`.

### `contracts`
- **Purpose:** Contract aggregate root, hub dashboard, lifecycle transitions, party/stage-history.
- **Files:** `routes.py`, `service.py`, `models.py`, `schemas.py`, `access.py`, `lifecycle.py`.
- **Endpoints (router `/contracts`):** `list_contracts`, `upload_contract` (multipart), `get_contract`, `update_contract`, `transition_lifecycle`, `get_lifecycle_options`, `get_stage_history`, `get_contract_activity`.
- **Endpoints (router `/contract-hub`):** `contract_hub` (KPI summary).
- **Models:** `Contract` (PK string-36; soft-deletable; carries `lifecycle_stage`, `risk_level`, `value_amount`, `currency`, `effective_date`, `expiration_date`, `current_contract_file_id`, `current_authoritative_version_id`, free-form `metadata_json`), `ContractParty`, `ContractStageHistory`.
- **Lifecycle state machine** in `lifecycle.py` — `ALLOWED_TRANSITIONS` map between 12 `ContractLifecycleStage` enum values; `transition_contract_stage()` enforces the map (override requires `contract:lifecycle_override` permission) and writes `ContractStageHistory` + audit + timeline.
- **Access:** `access.py` — `accessible_contract_filter(user)` SQL expression (owner / creator / project member / project share); `user_can_access_contract` boolean check.
- **Depends on:** `core.audit`, `playbooks.models.PlaybookDeviation`, `approvals.models.ApprovalRequest`, `signatures.models.SignatureRequest`, `obligations.models.Obligation`, `renewals.models.RenewalEvent`.

### `debug`
- **Purpose:** Internal traces. `/health`, `/readiness` are public; everything else requires `is_org_admin`.
- **Endpoints:** `GET /debug/health`, `GET /debug/readiness`, `GET /debug/config-status`, `GET /debug/requests/{id}`, `GET /debug/jobs/{id}`, `GET /debug/ai-calls/{id}`, `GET /debug/ai-skill-runs/{id}`, `GET /debug/assistant-runs/{id}`, `GET /debug/resources/{type}/{id}/timeline`.

### `jobs`
- **Purpose:** Celery job runs.
- **Files:** `routes.py`, `service.py`, `models.py`, `tasks.py`, `celery_app.py`.
- **Endpoints:** `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/run`. `_reap_stuck_jobs` runs on every list (TTL `20m`).
- **Tasks (`tasks.py`):** Single `run_ai_job(job_id)` Celery task — dispatches by `job_type`: `metadata_extraction`, `clause_extraction`, `obligation_extraction`, `renewal_extraction`, `embeddings`, `contract_brain_ingestion`, `tabular_cell_extraction`. After clause/obligation/renewal jobs succeed, auto-queues a `contract_brain_ingestion` job (`_queue_contract_brain_ingestion`).
- **Models:** `JobRun` (status, progress, idempotency_key unique, celery_task_id, metadata_json with downstream refs).
- **Depends on:** `ai.controller`, `ai.embeddings`, `contract_brain.ingestion`.

### `notifications`
- **Purpose:** Per-user notification rows (mainly produced by service-layer Resend calls; read-only API).
- **Endpoints:** `GET /notifications`.

### `obligations` (recently modified — `routes.py`)
- **Purpose:** Per-contract obligations and reminders.
- **Endpoints:** `GET /obligations` (filter by `contract_id`, `status_filter`, `due_within_days`), `GET /obligations/{id}`, `PATCH /obligations/{id}`, `POST /obligations/{id}/complete`, `POST /obligations/extract` (202; queues a Celery `obligation_extraction` job), `POST /obligations/run-reminders` (async; computes overdue/due_soon, sends Resend reminder emails for `ObligationReminder` rows whose `remind_at <= today`).
- **Models:** `Obligation`, `ObligationReminder`.

### `organizations`
- **Endpoints:** `GET/PATCH /organizations/current`, `GET /organizations/join-requests`, `GET /organizations/invitations`.
- **Models:** `Organization` (id, name, slug, allowed_domains, default_role_name, setup_complete, settings JSON).

### `playbooks`
- **Purpose:** Versioned playbooks of rules; AI-driven runs over contracts with deviations & decisions.
- **Endpoints:** CRUD on playbooks, versions, rules; `POST /playbooks/{id}/publish`, `POST /playbooks/{id}/runs` (async — calls `ai_controller.run_structured_skill(skill_name="playbook_review")`), `GET /playbooks/runs/{id}`, `POST /playbooks/deviations/{id}/decisions`.
- **Models:** `Playbook`, `PlaybookVersion`, `PlaybookRule`, `PlaybookRun`, `PlaybookDeviation`, `PlaybookDecision`.

### `projects`
- **Purpose:** Project workspaces; members/shares/folders; binds contracts.
- **Files:** `routes.py`, `models.py`, `schemas.py`, `access.py`.
- **Endpoints:** Full CRUD plus folders, members, shares, and project↔contract binding (`PUT /projects/{id}/contracts`).
- **Models:** `Project`, `ProjectFolder`, `ProjectMember`, `ProjectShare`, `ProjectContract`, `ProjectActivity`.

### `renewals`
- **Endpoints:** `GET /renewals`, `GET /renewals/{id}`, `POST /renewals/{id}/decision`, `POST /renewals/run-window-check` (transitions ACTIVE→RENEWAL_DUE, sends Resend email).
- **Models:** `RenewalEvent`.

### `search`
- **Purpose:** Multi-target search via SQL `ILIKE`.
- **Endpoints:** `GET /search/contracts`, `GET /search/contract-text`, `GET /search/clauses`, `GET /search/projects`, `GET /search/versions`.

### `signatures`
- **Purpose:** DocuSign send + Connect webhook + manual sync.
- **Endpoints:** `GET /signatures`, `POST /signatures/requests`, `POST /signatures/requests/{id}/sync`, `POST /signatures/webhook/docusign` (HMAC-verified, idempotent).
- **Models:** `SignatureRequest`, `SignatureRecipient`, `SignatureEvent`.
- **Depends on:** `integrations.docusign` (`docusign_client.create_envelope`, `void_envelope`, `verify_connect_signature`), `integrations.resend`, `contracts.lifecycle`.

### `tabular_review`
- **Purpose:** Spreadsheet-style multi-contract Q&A grid (Goldfinch / "Mike"-style).
- **Endpoints:** `GET/POST /tabular-reviews`, `POST .../columns`, `POST .../contracts`, `GET /{id}`, `POST .../cells/{id}/rerun`, `GET/POST .../chat` (async — calls `tabular_review_chat` skill), `GET .../export` (XLSX).
- **Models:** `TabularReview`, `TabularReviewColumn`, `TabularReviewCell`, `TabularReviewChat`.
- **Stuck-cell reconciliation:** `_reconcile_review_status` re-checks pending cells past the 20-minute TTL and marks them FAILED.

### `workflows`
- **Endpoints:** `GET /workflows` (with `builtin_workflows()` appended), `POST /workflows`.
- **Models:** `Workflow`, `WorkflowRun`. Built-ins are read-only and shipped in `builtin_workflows.json`.

---

## 5. Critical paths (end-to-end traces)

### (a) Contract upload → text extraction → brain ingestion

1. Client `POST /api/v1/contracts/upload` (multipart) → `app/contracts/routes.py:43-60` `upload_contract`.
2. `app/contract_files/service.py:277` `create_contract_from_upload`:
   - Validates `content_type` against `settings.allowed_mime_types`.
   - `_read_upload_with_limit` — streams the body in `upload_stream_chunk_bytes` chunks, rejects past `max_upload_size_bytes`.
   - `_sniff_mime_type` — verifies magic bytes against the declared MIME (PDF, DOCX, DOC, PNG, JPEG).
   - `storage_service.save_bytes` (`app/integrations/storage.py:25`) — writes to `<storage_root>/<org_id>/<YYYY>/<MM>/<uuid>_<filename>`, returns sha256.
   - `_resolve_extracted_text` (`service.py:108`) — runs `extract_text` (`text_extraction.py:17`) which dispatches by MIME (pypdf for PDF, python-docx for DOCX, decode for text); if `needs_ocr=True` or `quality_score < 0.55`, calls `reducto_client.extract_text` (mock returns empty string when `mock_reducto=True`).
   - `_persist_intake_records` — inserts `StorageObject`, `Contract`, `ContractFile`, `ContractVersion(version_number=1, is_authoritative=True)`, `ContractTextSnapshot`; wires `current_authoritative_version_id`, `current_contract_file_id`, `text_snapshot_id`.
   - Optionally inserts `ProjectContract`.
   - `_queue_initial_contract_jobs` enqueues three `JobRun`s with `job_type ∈ {metadata_extraction, clause_extraction, embeddings}` and an idempotency key `f"{job_type}:{version.id}:{snapshot.id}"`.
   - `write_audit_log` ("contract.uploaded") + `write_timeline_event`.
   - `db.commit()`; on exception: `db.rollback()` and `storage_service.delete_bytes_permanently(storage_key)`.
3. `_dispatch_initial_jobs` calls `dispatch_job(db, job=job)` for each → `app/jobs/service.py:45` → `run_ai_job.delay(job.id)` (Celery → Redis).
4. Celery worker picks up the task (`app/jobs/tasks.py:21` `run_ai_job` → `_run_ai_job`):
   - Sets `JobRun.status = RUNNING`, calls `ai_controller.run_job_skill` which delegates to `run_structured_skill`.
   - Claude is called via `claude_client.complete_structured` (`app/integrations/claude.py:66-103`) → POST to `https://api.anthropic.com/v1/messages` with `tool_choice = {type: tool, name: return_<skill_name>}`. Tenacity wraps with 3 retries.
   - Structured output is validated against the skill's `output_model`; citations validated via `validate_citations`; `AISkillRun.output_payload`, `AICallLog`, `AICitation` rows persisted; usage rows; `_persist_skill_output` writes derived data (e.g. clauses → `ClauseExtraction`, obligations → `Obligation`+`ObligationReminder`).
5. After `clause_extraction`/`obligation_extraction`/`renewal_extraction` succeeds, `_queue_contract_brain_ingestion` (`tasks.py:216`) enqueues a `contract_brain_ingestion` job.
6. The brain-ingestion job calls `ingest_contract_brain` (`app/contract_brain/ingestion.py:14`) → marks prior `KnowledgeNode`/`KnowledgeEdge` rows as stale, walks `ContractParty`, `ClauseExtraction`, `Obligation`, `ApprovalRequest`, `SignatureRequest`, `PlaybookDeviation` and adds new nodes + edges keyed to the new authoritative version. Writes audit + timeline.

### (b) AI assistant request → tool execution → cited response

1. Client `POST /api/v1/assistant/sessions/{session_id}/stream` with `{message, contract_ids, project_id}` → `app/assistant/routes.py:250` `stream_session`.
2. Validates session ownership, project access, contract access; ensures an `AssistantContractHandle` exists for each contract; inserts `AssistantMessage(role="user")` and `AssistantRun(status=RUNNING)`.
3. Returns `StreamingResponse(event_stream(), media_type="text/event-stream")`. The generator calls `ai_controller.stream_assistant_run` (`app/ai/controller.py:93-325`).
4. `stream_assistant_run`:
   - Loads `assistant_streaming` `SkillSpec` from `skill_registry`.
   - Resolves prompt bundle (`prompt_versions.get_active_prompt_bundle`).
   - Builds tool schemas from `tool_registry.all()` filtered by `enabled_by_default` and the user's permissions (`_assistant_tool_schemas`).
   - Builds the user prompt (`_assistant_user_prompt`) containing handles, scope JSON, per-contract `contract_summaries` (via `build_contract_context`), and a 60-contract `contract_inventory`.
   - Persists initial `AISkillRun(status=RUNNING)` (commits).
   - Loops up to `settings.ai_max_tool_iterations = 8`:
     - Calls `claude_client.complete_with_tools(...)` → POST `/v1/messages` with `tool_choice: auto`.
     - Logs `AICallLog` (`_log_assistant_ai_call`), commits.
     - Yields `message_delta` for each text block.
     - If no `tool_use_blocks`, marks skill_run SUCCEEDED and returns.
     - Otherwise, for each tool use:
       - `tool_started` event.
       - `tool_runtime.execute(db, tool_name, tool_input, user, session_id, assistant_run_id, ...)` (`app/ai/tool_runtime.py:86`).
         - Validates input via spec's `input_model`.
         - Inserts `AssistantToolCall(status=RUNNING, idempotency_key)`.
         - If `spec.requires_confirmation`, creates `AIConfirmation`, returns `{confirmation_required: True}` — the controller flips the run to `WAITING_CONFIRMATION` and returns.
         - Otherwise calls `_execute_validated` which dispatches to the per-tool method (e.g. `_edit_contract`, `_send_for_signature`, `_my_attention_items`, `_find_contracts`, `_list_obligations`). Mutating tools call into the relevant service (`submit_contract_for_approval`, `transition_contract_stage`, `docusign_client.create_envelope`, etc.) and write audit + timeline events.
       - Result is passed through `_model_safe_result` (`controller.py:839`) which strips internal UUID keys (`text_snapshot_id`, `contract_version_id`, etc., from `INTERNAL_RESULT_KEYS` set) and substitutes contract IDs with stable session-scoped handles.
       - Tool result block is appended to the conversation; loop continues.
5. The route's generator accumulates `answer_parts`, `citations` (via `_citations_from_tool_result`), and ordered `blocks`, then persists an `AssistantMessage(role="assistant")` and calls `_validate_and_store_assistant_citations` which fuzz-matches each excerpt against the cited contract's `ContractTextSnapshot.text` and persists `AICitation` rows.
6. Yields final `done` SSE event.

**Confirmation resume path:** `POST /assistant/confirmations/{id}/confirm` → flips `AIConfirmation.status = CONFIRMED` and `AssistantToolCall.status = CONFIRMED`. Client then `POST /assistant/runs/{run_id}/resume?confirmation_id=...` → `ai_controller.resume_assistant_run` rebuilds the conversation from `AssistantRun.provider_state.messages`, executes the confirmed tool via `tool_runtime.execute_confirmed`, and re-enters the Claude tool loop.

### (c) Approval request lifecycle

1. `POST /api/v1/approvals/requests` → `app/approvals/routes.py:92` → `await submit_contract_for_approval(db, user, contract, contract_version_id, approver_user_id, approver_role)` (`app/approvals/service.py:61-166`).
2. `evaluate_routing` reads `ApprovalRoutingRule` rows, applies `_matches(rule, contract)` (matches by `min_value`, plus equality on `contract_type`/`risk_level`/etc.). If no rule matches, falls back to the caller's `approver_user_id`/`approver_role`.
3. For each target: insert `ApprovalRequest`; if `approver_user_id` resolves to a user, mint a 7-day single-use `ApprovalToken` (token hashed with SHA-256 via `hash_token`), and send the approver a Resend email containing `app_base_url/#approve?token=...&d=approve|reject` URLs (constructed in service code).
4. Writes `audit_log` (`approval.requested`).
5. Forces `transition_contract_stage(to_stage=APPROVAL_PENDING, override=True, override_authorized=True)`.
6. Decision path 1 — in-app: `POST /approvals/requests/{id}/decision` → `decide_in_app` → `_apply_decision` → sets `ApprovalRequest.status`, inserts `ApprovalDecision`, transitions contract to `APPROVED` or `INTERNAL_REVIEW`, writes audit + timeline.
7. Decision path 2 — token: `POST /approvals/token-decision` (no auth) → `redeem_token_decision` looks up `ApprovalToken` by `hash_token(token)`, checks `used_at` and `expires_at`, marks the token used, calls `_apply_decision` with `actor_user_id = approver.id if approver else None`. Audit row records `actor_label = f"token:{intended_approver_email}"`.

### (d) Obligation creation / listing (current state after recent edit)

Two creation paths into `Obligation`:
- **AI extraction** — Celery `run_ai_job(job_type="obligation_extraction")` → `ai_controller.run_structured_skill("obligation_extraction")` → `_persist_obligations` (`controller.py:1446-1542`) marks prior AI-sourced open obligations as soft-deleted, inserts new `Obligation` rows + `ObligationReminder(remind_at = due_date - 7 days)`, audit + timeline.
- **Manual extract trigger** — `POST /obligations/extract?contract_id=...` (recently modified, `app/obligations/routes.py:132-168`) creates a `JobRun(job_type="obligation_extraction")` (with idempotency key including a timestamp so manual reruns aren't deduped against the upload-time job) and dispatches via Celery.

Listing: `GET /obligations` (`obligations/routes.py:42`) — joins `Contract` and applies `accessible_contract_filter(current_user)`; supports `contract_id`, `status_filter`, `due_within_days` filters; orders by `due_date ASC`.

Update/complete: `PATCH /obligations/{id}` (Pydantic `ObligationUpdate` with regex-pattern status), `POST /obligations/{id}/complete` (sets status to "completed"). Both write audit logs.

`POST /obligations/run-reminders` (async) — recomputes `overdue`/`due_soon` for open obligations, finds unsent `ObligationReminder`s with `remind_at <= today`, calls `resend_client.send_email` per reminder, marks `sent_at`.

---

## 6. Data model summary

UUID PKs are strings of length 36 (`new_uuid()` → `str(uuid.uuid4())`). All org-scoped tables carry `org_id VARCHAR(36)` (no FK to `organization` table; it's an index-only column). Soft-deletable tables carry `deleted_at`, `deleted_by_user_id`, `legal_hold`.

### Identity / multi-tenancy
- `organization` — root tenant. PK string-36. `slug` unique. `allowed_domains` (JSON list).
- `user` — `email` unique global. `org_id` (string, no FK), `active_role_id` (FK→`role`), `status` (`UserStatus`), `hashed_password`.
- `role` — `org_id`+`name` unique. Many-to-many `permissions` via `role_permission`.
- `permission` — `value` unique.
- `user_role` (assoc), `role_permission` (assoc).
- `refresh_token` — `user_id`+token hash+`expires_at`+`revoked_at`.
- `revoked_access_token` — `jti` unique, `expires_at` (added in migration 0008).
- `password_reset_token`, `user_invitation`, `org_join_request`, `user_approval_decision`, `api_key`.

### Contract aggregate
- `contract` (`Contract`) — soft-deletable. Owns `current_contract_file_id` and `current_authoritative_version_id` (FKs with `use_alter=True` — circular reference). Carries lifecycle/risk/value/dates/`metadata_json`.
- `contract_party`, `contract_stage_history`.
- `contract_file` — `current_version_id` FK.
- `contract_version` — `(contract_id, contract_file_id, version_number)`; `is_authoritative` bool; `text_snapshot_id` FK; `source` (`ContractVersionSource` enum, 11 values).
- `contract_text_snapshot` — text + `page_map` + `extraction_method` + `ocr_provider` + `extraction_quality_score` + `validation_status`.
- `contract_edit` — anchored edit suggestions tied to a `contract_version_id`; `status` (proposed/accepted/rejected).
- `contract_share` — token+passcode hashed; access_mode; `expires_at`, `revoked_at`.
- `contract_embedding` — `pgvector.Vector(384)` per chunk; FK to `contract_text_snapshot`.
- `storage_object` — `storage_key` unique; sha256.

### AI execution spine
- `a_i_skill_run` (note: SQLAlchemy auto-snake produces `a_i_*` for `AISkillRun`/`AICallLog`/`AICitation`/`AIConfirmation`/`AIPromptVersion`) — skill_name, prompt_key+version+hash, model + model_config_hash, input/output_payload, status (`AISkillRunStatus`), `validation_status` (`AIValidationStatus`), `execution_mode`, links to `contract_id`/`contract_version_id`/`text_snapshot_id`/`job_id`/`session_id`/`assistant_run_id`/`tool_call_id`.
- `a_i_call_log` — single Claude call. Includes `provider_request_id`, `prompt_tokens`/`completion_tokens`/`total_tokens`, `raw_ai_output` (JSON), `validated_output`, `redaction_status`. Lifecycle: created per call by `_log_ai_call` or `_log_assistant_ai_call`.
- `a_i_citation` — quote + normalized_quote + `validation_status` ("valid"/"invalid"/"needs_review") + `similarity_score` (float). FKs to `a_i_skill_run`, `a_i_call_log`, `assistant_run`, `assistant_tool_call`, `contract`, `contract_version`, `contract_text_snapshot`.
- `a_i_confirmation` — `tool_name`, `tool_input` (JSON), `policy`, `expires_at`, `decided_by_user_id`, `provider_state` (carries the conversation so the resume path can replay messages).
- `a_i_prompt_version` — `prompt_key`+`version` per-org versioning with `prompt_hash` and `model_config_hash`.

### Assistant
- `assistant_session` (project_id/contract_id/tabular_review_id soft links, `session_type` enum, `status`).
- `assistant_message` (role + content + citations JSON).
- `assistant_run` (`status` from `AssistantRunStatus`, `provider_state` JSON, `context_manifest` JSON, `current_tool_iteration`).
- `assistant_tool_call` (`tool_name`, `category`, `arguments`, `result`, `status` from `AssistantToolCallStatus`, `confirmation_required`, `idempotency_key`).
- `assistant_contract_handle` (`session_id` + `contract_id` ↔ stable `handle` like `contract-0`).

### Operational tables
- `audit_log` — immutable (`event.listens_for` raises on `before_update`/`before_delete`). `prev_hash` + `row_hash` SHA-256 chain, advisory-lock serialized in Postgres (`pg_advisory_xact_lock(_AUDIT_CHAIN_LOCK_KEY)`).
- `request_log` — per-HTTP-request observability row (route, status, latency, query metadata). Written via batched background thread (`app/core/request_log_queue.py`).
- `resource_timeline_event` — per-resource ordered event feed (used for "what happened on this contract"). Foreign keys to assistant_run, ai_call, skill_run, job, request_id.
- `usage_record` — counts of `ai.call`, `ai.prompt_tokens`, etc.
- `admin_setting` — org-scoped KV with `is_secret` flag.
- `job_run` — Celery job + idempotency_key unique. Carries `celery_task_id`, `metadata_json` with downstream IDs.
- `notification` — user_id, channel, event_type, body, sent_at.

### Domain tables (skim)
- `approval_request`, `approval_decision`, `approval_routing_rule`, `approval_token`.
- `signature_request`, `signature_recipient`, `signature_event`.
- `playbook`, `playbook_version`, `playbook_rule`, `playbook_run`, `playbook_deviation`, `playbook_decision`.
- `project`, `project_folder`, `project_member`, `project_share`, `project_contract`, `project_activity`.
- `obligation`, `obligation_reminder`.
- `renewal_event`.
- `tabular_review`, `tabular_review_column`, `tabular_review_cell`, `tabular_review_chat`.
- `workflow`, `workflow_run`.
- `knowledge_node`, `knowledge_edge`, `clause_extraction`, `brain_query`.

### Migration history
- `0001_initial_contract_platform` — full schema, `CREATE EXTENSION IF NOT EXISTS vector`.
- `0002_ai_architecture_spine` — assistant_run/message/tool_call, ai_confirmation, ai_prompt_version, ai_citation, ai_skill_run additions.
- `0003_phase2_hardening` — schema hardening.
- `0004_phase1_auth_foundation` — auth backbone.
- `0005_schema_project_share` — project share table.
- `0006_audit_hash_chain` — `prev_hash`/`row_hash` columns + immutability triggers + backfill.
- `0007_drop_dead_contract_activity` — drops a legacy `contract_activity` table replaced by `resource_timeline_event`.
- `0008_revoked_access_token` — adds `revoked_access_token` table with `jti` unique + `expires_at`.

---

## 7. External dependencies & assumptions

### Claude (Anthropic)
- File: `backend/app/integrations/claude.py`. Mock switch: `settings.mock_claude` (default True).
- Real-mode endpoint: `POST https://api.anthropic.com/v1/messages` with header `anthropic-version: 2023-06-01`. Model from `settings.claude_model` (default `claude-3-5-sonnet-latest`).
- Two production-shaped methods: `complete_structured` (forces `tool_choice = {type: tool, name: return_<skill>}`) and `complete_with_tools` (`tool_choice: auto`, used by the assistant loop). `complete_text` is the fallback when no tools are supplied.
- Long-lived `httpx.AsyncClient` (`_CLAUDE_TIMEOUT = httpx.Timeout(600.0, connect=10.0)`). Closed at app shutdown via `aclose_claude_client()`.
- Retries: `tenacity.AsyncRetrying` with `stop_after_attempt(claude_max_retries=3)`, `wait_exponential(multiplier=1.0, max=30.0)`, predicate `_should_retry` matches 429 and 5xx and transport/timeout errors.
- Mock path: `_mock_structured_response` returns canned tool_use blocks from `_claude_mock.structured_payload_by_tool()`; `_mock_tool_response` calls `_claude_mock.select_mock_tool(user_text, tool_names)` for keyword-driven tool selection. Mock module is `backend/app/integrations/_claude_mock.py` (recently modified).

### DocuSign
- File: `backend/app/integrations/docusign.py`. Mock switch: `settings.mock_docusign`.
- JWT auth flow (`_jwt_access_token`): RS256-signs an assertion using the PEM at `settings.docusign_private_key_path` (cached in-process), POSTs to `<oauth_base>/oauth/token`.
- Envelope create: POSTs base64-encoded document to `<rest_base>/v2.1/accounts/<account_id>/envelopes`. `void_envelope` is best-effort on local-commit-failure rollback.
- Webhook: `verify_connect_signature(body, signature_header)` — HMAC-SHA256 base64, `hmac.compare_digest` against header `X-DocuSign-Signature-1`. Rejects when no `docusign_connect_hmac_key` is configured.
- Required env: `DOCUSIGN_INTEGRATION_KEY`, `DOCUSIGN_USER_ID`, `DOCUSIGN_ACCOUNT_ID`, `DOCUSIGN_PRIVATE_KEY_PATH`, `DOCUSIGN_CONNECT_HMAC_KEY` (optional but webhook rejects without it).

### Reducto (OCR)
- File: `backend/app/integrations/reducto.py`. Mock switch: `settings.mock_reducto`.
- Uses `reducto.AsyncReducto` SDK. Calls `client.upload(file=...)` then `client.parse.run(input=...)`. Returns concatenated chunk text with `quality_score=0.9 if text else 0.0`. Mock returns empty string.

### Resend (email)
- File: `backend/app/integrations/resend.py`. Mock switch: `settings.mock_resend`.
- POSTs to `https://api.resend.com/emails` with `{from, to, subject, html}`. Opens a fresh `httpx.AsyncClient(timeout=30)` per call.

### Storage
- File: `backend/app/integrations/storage.py`. **Local volume only** — `StorageBackend.LOCAL_VOLUME` is the only enum value.
- Layout: `<settings.storage_root>/<org_id>/<YYYY>/<MM>/<uuid>_<safe_filename>`.
- `_resolve_storage_key` checks `candidate.is_relative_to(self.root)` to defend against path traversal.
- `path_for_read` / `read_bytes` / `delete_bytes_permanently` round out the surface.

### Database
- Postgres (production) — pgvector extension required (`CREATE EXTENSION IF NOT EXISTS vector` in migrations 0001 and 0002). DSN: `postgresql+psycopg://...`.
- SQLite — appears in test posture; the audit advisory-lock call is guarded.

### Redis
- Single Redis instance shared between Celery broker (`broker=settings.redis_url`) and result backend.

### Embeddings model
- `BAAI/bge-small-en-v1.5` (384-dim) via `fastembed`. If `fastembed` is unimportable, a deterministic SHA-256-seeded random vector is generated (see `embeddings.py` head).

---

## 8. Seams & boundaries

Places where one module hands off via implicit contracts (descriptive, not critical):

- **AI tool result → controller → frontend SSE shape.** `tool_runtime` returns a plain `dict`. The controller passes it through `_model_safe_result` to strip `INTERNAL_RESULT_KEYS` (a hand-maintained set in `controller.py:43-70`) before re-injecting into the Claude conversation. The same dict is also yielded as the `tool_finished` SSE payload via the assistant route — so the frontend reads UUIDs from the un-redacted shape while Claude sees redacted handles. Two consumers, one return value, separated only by what they pick up.
- **`provider_state` JSON on `AssistantRun`.** Carries `messages`, `pending_tool_use`, `skill_run_id`, `confirmation_id`, and `resumed_at`. The schema is documented only by usage (no Pydantic). `resume_assistant_run` reads it back and replays the conversation. Schema version is hand-tagged as `"schema_version": 1`.
- **Contract handles.** `AssistantContractHandle.handle` ("contract-0", "contract-1") is a per-session opaque alias. Two writers exist: `controller._handle_for_contract` and `tool_runtime._find_contracts` — both increment a count by `len(...)` of existing handles in the session, which is the seam through which they stay consistent.
- **`Contract.metadata_json`.** Free-form JSON. AI controller writes `ai_suggestions.metadata`, `latest_metadata_skill_run_id`. Other modules read it as a dict without a schema.
- **`JobRun.metadata_json`.** Used as a sidechannel to pass `contract_version_id`, `text_snapshot_id`, `cell_id`, `triggered_by_job_id`, `trigger_reason`. Celery task reads these by string key.
- **`AICallLog.skill_run_id` vs `AICallLog.assistant_run_id`.** Both nullable. A single AI call may belong to one, the other, or both. There is no enforcing constraint.
- **`tool_call.confirmation_id` field on `AssistantToolCall`.** Set from `tool_runtime._execute -> confirmations.create_confirmation`. The reverse (`AIConfirmation.tool_call_id`) is the canonical FK; `assistant_tool_call.confirmation_id` is a denormalized copy.
- **`citations.py` is shared between three callers**: the structured-skill validator in the controller, the assistant-route `_validate_and_store_assistant_citations`, and the brain-route. Each computes the source text differently (skill: `contract_context.text`; assistant: per-citation `_source_for(contract_id)`; brain: `context["context_text"]`). The same fuzzy-match threshold logic applies but the haystack semantics differ.
- **`accessible_contract_filter(user)` is appended into many queries** (`obligations/routes.py`, `signatures/routes.py`, `search/routes.py`, `renewals/routes.py`, `playbooks/routes.py`, `contract_brain/retrieval.py`). It is a SQLAlchemy `or_(...)` expression — callers must remember to `Contract` join into the query.
- **Audit log autonomous session.** `write_audit_log` opens a fresh `SessionLocal()`, takes a Postgres advisory lock, commits, closes. Effectively a separate transaction running alongside the caller's DB session — the seam between "the operation" and "the audit row" is that they don't share a transaction.
- **Skill output → side effect dispatch.** `_persist_skill_output` is a long if/elif on `spec.name` (`controller.py:1239`). New skills that need persistence must be added here; the registry doesn't carry the persister.
- **Tool name → function dispatch.** Same shape in `tool_runtime._execute_validated` — string compare on `tool_name` to method call.
- **`builtin_workflows()`** — `app/workflows/builtin.py` loads `builtin_workflows.json` and returns synthetic in-memory `Workflow`-shaped objects (not DB rows). Listed alongside DB workflows in the route response. Read-only.

---

## 9. Auth, multi-tenancy & access control

- **Identification:** `Authorization: Bearer <token>` via FastAPI's `HTTPBearer(auto_error=False)` (`app/core/deps.py:14`). `get_current_user` (`deps.py:35-70`):
  1. Decodes a JWT (`decode_access_token`, HS256, must have `typ=access` and a `jti`).
  2. If decoding fails, falls back to API-key authentication (`authenticate_api_key` from `auth.service`).
  3. Checks `jti` against `RevokedAccessToken` via `is_access_token_revoked`.
  4. Confirms the user exists and `status == UserStatus.ACTIVE`.
  5. Confirms the token's `role_id` claim still matches `user.active_role_id`.
  6. Stashes user, jti, exp on `request.state`.
- **Tokens:** `create_access_token(subject, claims)` (`app/core/security.py:79-101`) — HS256, payload includes `sub`, `iat`, `exp`, `typ=access`, `jti`. Default lifetime 60 min (`settings.access_token_expire_minutes`).
- **Refresh tokens:** Issued as `Set-Cookie` HttpOnly via `_set_refresh_cookie` in `auth/routes.py:58-73`. Legacy `expose_refresh_token_in_body` flag still allows body-emitting for back-compat. The bcrypt-style `_bcrypt_secret` (`security.py:18-26`) SHA-256-pre-hashes passwords + base64-encodes to dodge bcrypt's 72-byte ceiling; legacy hashes are also accepted via `_legacy_bcrypt_secret` and flagged for rehash by `password_needs_rehash`.
- **RBAC:** `app/core/rbac.py` defines a fixed permission catalog (`ALL_PERMISSIONS`, ~30 strings like `"contract:read"`, `"approval:decide"`, `"playbook:run"`) and four default roles (`admin`, `member`, `legal_reviewer`, `approver`) with `DEFAULT_ROLE_PERMISSIONS`. `has_permission(user_permissions, required)` checks membership or `"*"` wildcard. Route-level enforcement via `Depends(require_permission("contract:read"))`.
- **Org scope:** Every domain model has `org_id VARCHAR(36)` (via `OrgScopedMixin`). There is no FK to `organization`; org isolation is enforced everywhere by explicit `Model.org_id == user.org_id` predicates.
- **Contract-level access:** `app/contracts/access.py` — `accessible_contract_filter(user)` returns either `true()` for org admins, or an `or_(owner, creator, project_member_exists, project_share_exists)`. `user_can_access_contract` is the per-row check used by `get_contract_for_user` (`contracts/service.py:27`). Project access goes through `app/projects/access.py` `get_project_for_user(db, project_id, user, access="read"|"update"|"share")`.
- **Admin check:** `app/core/access.py` `is_org_admin(user)` returns True if the user has `admin_panel:access` permission or the literal role name `"admin"`.
- **Approval-level access:** `app/approvals/routes.py:179-186` adds two role-name-based checks: `_can_decide_approval` (admin OR assigned user OR approver_role matches one of user's roles) and `_can_view_approval`.

---

## 10. Background work, jobs, queues

- **Celery 5.4** with Redis broker (`app/jobs/celery_app.py`). One worker container; tasks autoload from `app.jobs.tasks`.
- **Single task** `run_ai_job(job_id)` in `app/jobs/tasks.py`. `bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={max_retries: 3}`. Dispatch by `JobRun.job_type`:
  - `metadata_extraction`, `clause_extraction`, `obligation_extraction`, `renewal_extraction` → `ai_controller.run_job_skill(...)` → structured Claude skill.
  - `embeddings` → `generate_embeddings_for_snapshot`.
  - `contract_brain_ingestion` → `ingest_contract_brain`.
  - `tabular_cell_extraction` → `tabular_cell_extraction` skill, writes one `TabularReviewCell`.
- **Chaining:** After `clause_extraction`/`obligation_extraction`/`renewal_extraction` succeed, `_queue_contract_brain_ingestion` enqueues a follow-up job (deduped by `idempotency_key`).
- **FastAPI `BackgroundTasks`:** Not used (search did not surface usage; the in-request workers are awaited inline).
- **Background thread:** `app/core/request_log_queue.py` runs a daemon thread that drains a bounded queue and writes `RequestLog` rows in batches via `bulk_insert_mappings`. Started by app lifespan.
- **Reaper:** `app/jobs/routes.py` `_reap_stuck_jobs(STUCK_JOB_TTL = 20m)` runs on every list_jobs call. Tabular reviews have a parallel reconciler in `app/tabular_review/routes.py`.
- **Cron:** No application-level cron; renewal-window check (`POST /renewals/run-window-check`) and obligation reminders (`POST /obligations/run-reminders`) are explicit endpoints the caller must trigger.

---

## 11. Configuration & secrets

- **Config:** `app/core/config.py` — pydantic `Settings(BaseSettings)` with `env_file=".env"`, `extra="ignore"`. ~50 settings covering CORS, allowed hosts, DB pool, JWT TTLs, refresh-cookie params, rate limit strings, request-log redaction, allowed MIME list, Claude/Reducto/Resend/DocuSign API keys + mock flags, retry knobs, upload caps.
- **Loaded once** via `@lru_cache get_settings()` and re-exported as `settings`. Creates `storage_root` on import.
- **Runtime safety net:** `validate_runtime_settings(settings)` (`config.py:142-182`) refuses to start the app in non-local environments if any of `SECRET_KEY`/`SETUP_TOKEN` are defaults, any mock integration is on, `EXPOSE_PASSWORD_RESET_TOKEN_IN_RESPONSE` is set, `EXPOSE_REFRESH_TOKEN_IN_BODY` is set, `REFRESH_COOKIE_SECURE` is off, samesite is invalid, or `ALLOWED_HOSTS`/`CORS_ORIGINS` is wildcard.
- **Env files:** `backend/.env` (gitignored — but present in working tree as size ~3 KB), `backend/.env.example` (committed).
- **Secrets dir:** `backend/secrets/docusign_private_key.pem` (RSA private key file used by DocuSign JWT auth). `.gitignore` lists `**/*.pem` and `backend/secrets/`.

---

## 12. Tests

`backend/tests/` (8 test files, ~995 LOC):

- `test_phase1_auth_foundation.py` (152 LOC) — Auth backbone tests.
- `test_phase2_api_shapes.py` (79 LOC) — API contract shapes.
- `test_phase4_contract_lifecycle_hub.py` (72 LOC) — Lifecycle transitions and contract hub.
- `test_phase5_playbooks.py` (116 LOC) — Playbook flows.
- `test_phase6_9_integration.py` (117 LOC) — Cross-domain integration.
- `test_phase10_security_hardening.py` (152 LOC) — Security regressions (rate-limit, jti revocation, MIME sniff, etc.).
- `test_ai_architecture_wiring.py` (246 LOC) — AI controller/registry/runtime wiring.
- `test_migration_deployability.py` (60 LOC) — Alembic upgrade/downgrade smoke.

`tests/__init__.py` is empty. `pytest_asyncio` is configured to `asyncio_mode = "auto"`. Frontend has one test file (`markdown.test.tsx`) plus a Vitest config and `vitest.setup.ts`.

---

## 13. Recent-work observation

Current state of the 7 modified-but-uncommitted files:

- `backend/app/ai/controller.py` — Hosts `AIController` with `run_structured_skill`, `stream_assistant_run`, `resume_assistant_run`, the per-skill persisters (`_persist_metadata`/`_persist_clauses`/`_persist_obligations`/`_persist_renewal`), `_log_ai_call`/`_log_assistant_ai_call`, usage recording, and `_model_safe_result` (the UUID redactor that swaps `contract_id` → contract handle before re-injecting tool results into the Claude conversation). ~1680 LOC, the orchestrator.
- `backend/app/ai/tool_registry.py` — Declares 24 `ToolSpec` registrations including the three "Spec A" portfolio tools (`my_attention_items`, `find_contracts`, `list_obligations`) that let the assistant answer cross-contract questions; carries fairly long natural-language descriptions intended for Claude (e.g. the `edit_contract` spec contains instructions on how to behave).
- `backend/app/ai/tool_runtime.py` — Implements every assistant tool method, the DOCX redline rendering (native Word `w:ins`/`w:del` tracked changes via python-docx OOXML), suggestion anchoring (`_anchor_suggestions`, `_apply_anchored`), idempotency keying. Forces `HTTPException(422)` on `edit_contract` and `generate_contract_docx` when the AI returns no usable output, rather than degrading to a fake skeleton.
- `backend/app/approvals/service.py` — Implements `submit_contract_for_approval` (routing + email-token mint + approver email via Resend), `decide_in_app`, `redeem_token_decision`, `_apply_decision`. Uses a richly-styled HTML email body with embedded Approve/Reject buttons keyed by `app_base_url/#approve?token=...`.
- `backend/app/core/config.py` — Pydantic settings module with the full set of toggles, runtime guards in `validate_runtime_settings`, and tunables for the Claude retry layer and upload streaming.
- `backend/app/integrations/_claude_mock.py` — Extracted from `claude.py` per CODE_REVIEW_2026-05-19 item #37. Exposes `structured_payload_by_tool()` (canned tool-use payloads keyed by `return_<skill>`) and `select_mock_tool(user_text, tool_names)` (keyword-driven tool routing for the mock-claude assistant path).
- `backend/app/obligations/routes.py` — Provides the obligations API surface (`list_obligations` with `accessible_contract_filter` join, `get/patch/complete`, `extract` enqueues a Celery job with a timestamped idempotency key so manual reruns aren't deduped against the upload-time job, `run-reminders` recomputes overdue/due_soon and sends Resend reminder emails).

---

## Frontend (short treatment)

- **Stack:** Next.js 15 (app router), React 19, TanStack Query 5, Tailwind 3.4, react-markdown 9, recharts 2. Single test file (`markdown.test.tsx`). TypeScript.
- **Routes** (`frontend/src/app/(app)/`): `admin`, `approvals`, `assistant`, `brain`, `contract-hub`, `contracts`, `jobs`, `notifications`, `obligations`, `playbooks`, `projects`, `renewals`, `search`, `signatures`, `tabular-reviews`, `workflows`, plus a root `page.tsx`, `layout.tsx`, and `login/`.
- **API client:** `frontend/src/lib/api.ts` — `tokenStore` only stores `access_token` in localStorage; refresh tokens live in the HttpOnly cookie (`credentials: 'include'` on `/auth/refresh`). `apiFetch` and `apiDownload` helpers; `HttpError` class; SSE handling for the assistant stream.
- **Typed endpoints:** `frontend/src/lib/endpoints.ts` — Thin typed wrappers grouped by backend module (assistant, contracts, contract-files, contract-brain, jobs, playbooks, projects, search, signatures, obligations, renewals, tabular-reviews, admin, etc.). Types in `lib/types.ts`.
- **Demo mode:** `lib/demo.ts` (`isDemo`, `getMock`, `demoStream`) — the api client short-circuits to mock data when demo flags are on.
- **Auth context:** `lib/auth.tsx` (React provider).
- **Query layer:** `lib/query.tsx` (TanStack Query provider).
- **Components:** `app-shell.tsx`, `assistant-workspace.tsx`, `contract-document.tsx`, `create-review-modal.tsx`, `import-contract-modal.tsx`, `markdown.tsx`, `toast.tsx`, `ui.tsx`.
- **No state library beyond React Query + React state.** No Redux/Zustand.

---

## End of Agent 1 output
