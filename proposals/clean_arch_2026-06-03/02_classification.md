# AEGIS Legal CLM — Component → Clean-Architecture-Layer Classification

**Agent 2: The Classifier** · **Date:** 2026-06-03
**Scope:** Map every significant component to its correct Clean Architecture layer and name the inward-violating dependency it currently holds. Builds on `01_reality.md` (Agent 1). I classify and diagnose only — I do not design the target tree (Agent 3) or write code (Agent 4).

**Reading key for the "must STOP knowing about" column:** the concrete, framework/infra/cross-domain symbol that a component currently imports or calls *outward or sideways*, which the dependency rule forbids. Severing it = the migration unit.

The four buckets (innermost → outermost): **Domain** (pure, zero framework imports) · **Application** (use cases + abstract ports) · **Interface Adapters** (routes, presenters, tool dispatch, Celery shims, repository mapping) · **Infrastructure** (SQLAlchemy, httpx, Redis, Anthropic/DocuSign/Reducto/Resend SDKs, SSE transport).

---

## Corrections to Agent 1

Agent 1's reality doc is accurate. Two refinements, not corrections:

1. **`ai_max_tool_iterations = 8`** (`core/config.py:99`) is the literal value behind Agent 1's "up to 8 iterations" — both `stream_assistant_run` (`controller.py:170`) and `resume_assistant_run` (`controller.py:447`) loop `range(settings.ai_max_tool_iterations)`. The loop bound is a **configurable Application policy**, not a hard-coded constant.
2. Agent 1 names two events to replace inline side effects. The `_persist_skill_output` family raises **more** domain-significant side effects than just lifecycle/audit: `_persist_obligations` (`controller.py:1524`) and `_persist_renewal` raise their own `write_audit_log` + `write_timeline_event`, and `approvals/service.py`, `signatures/service.py`, `auth/service.py` (19 sites), `contract_brain/ingestion.py:179`, `renewals/routes.py:149`, `obligations/routes.py` all do too. The Domain-Event set in §C is therefore larger than the two Agent 1 named. Noted, not a contradiction.

---

## Layer tallies (summary — full table below)

| CA layer | Approx. component count* | Character of what lands here |
|---|---|---|
| **Domain** | ~20 | Lifecycle rules (`ALLOWED_TRANSITIONS`), citation/redline anchoring rules, access predicates, audit-hash-chain rule, the 24 tool *contracts*, skill *definitions*, domain events, value objects. |
| **Application** | ~30 | Use cases extracted from `AIController` (3), the tool *execution* orchestration (per tool group), every self-committing service fn, every fat route's orchestration body, the Celery job orchestration, ingestion/retrieval orchestration, event handlers. |
| **Interface Adapters** | ~28 | All `*/routes.py` (HTTP I/O only), `ai/routes.py` + `assistant/routes.py` SSE framing, `ToolRegistry`/`ToolRuntime` dispatch shim, `ToolUseTranslator`, repository *implementations*, Celery task shim, integration response→DTO mappers. |
| **Infrastructure** | ~22 | SQLAlchemy engine/session/models, all `integrations/*` SDK clients, `AnthropicAIClient`, SSE transport, `VectorStore` impl (pgvector), Redis/Celery app, repository SQL, `request_log_queue`. |

\*Counts are of the components in the mapping table that carry logic/state/side-effects; trivial getters folded in. Models themselves are one Infra row per domain.

---

## Primary mapping table

Grouped by current module. `path:symbol` cites a real component. "Correct CA layer" is decisive; genuinely-split components are split into named pieces and appear on multiple rows.

### Module: `core` (cross-cutting today)

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `core/database.py:engine`, `SessionLocal` | infra-ish | **Infrastructure** | Concrete SQLAlchemy engine + sessionmaker; the DB driver. | Nothing inward — but everything else must stop importing `SessionLocal` directly (esp. `audit.py`). |
| `core/database.py:IdMixin/TimestampMixin/OrgScopedMixin/ActorTrackedMixin/SoftDeleteMixin` | infra | **Infrastructure** | SQLAlchemy `DeclarativeBase` mixins — ORM mapping concerns. | — (already leaf) |
| `core/database.py:new_uuid`, `utcnow` | infra | **Domain** (pure helpers) | Pure ID/time generation, no framework. Domain entities need IDs/timestamps. | `sqlalchemy`/session — they don't import it; keep them framework-free. |
| `core/audit.py:write_audit_log` | cross-cutting (autonomous `SessionLocal`) | **SPLIT** → rule is **Domain** (`compute_audit_row_hash` + chain invariant); the write is **Infrastructure** (`AuditLogWriter` behind a port); the *decision to record* is **Application** (event handler) | The hash-chain + tamper-evidence is an enterprise rule; persisting it is a driver concern; *when* to record is a use-case concern. | **Its own `SessionLocal()` and `db.commit()` (`audit.py:47,82`)** + `pg_advisory_xact_lock`. The autonomous transaction must die (see §C). |
| `core/audit.py:compute_audit_row_hash` | cross-cutting | **Domain** | Pure SHA-256 over canonical fields — an enterprise integrity rule. | `AuditLog` ORM type in its signature → must take a plain value object/dict, not the SQLAlchemy row. |
| `core/audit.py:verify_audit_hash_chain` | cross-cutting | **SPLIT** → rule **Domain**, iteration over rows **Infrastructure** (repo `yield_per`) | The "prev_hash must match + recompute" check is Domain; streaming rows is repo/infra. | `select(AuditLog)` + `yield_per` SQLAlchemy execution options. |
| `core/audit.py:write_timeline_event` | cross-cutting | **SPLIT** → event is **Domain** (`ResourceTimelineEventRequested`); writer is **Infrastructure** (caller-session `db.add`) | Timeline emission is a domain signal; the `db.add` is infra. | `ResourceTimelineEvent` ORM + `Session`. |
| `core/rbac.py:has_permission`, `permission_values` | core | **Domain** (authorization rule) | Pure permission-set evaluation — enterprise rule. | Nothing — keep pure; callers pass permission values, not `User` ORM. |
| `core/deps.py:get_db` | infra | **Interface Adapter** (FastAPI wiring) → backed by **Infrastructure** session | A DI/transaction-boundary shim; FastAPI-specific. | After migration, transaction boundary moves to a Unit-of-Work; `get_db` stops being the de-facto committer-opener. |
| `core/config.py:Settings`, `ai_max_tool_iterations`, `mock_*` | core | **Infrastructure** (config), values **read by Application** | Pydantic-settings + env binding is infra; the *values* (iteration cap, feature flags) are policy consumed by use cases. | Pydantic `BaseSettings` must not be imported by Domain; inject plain values. |
| `core/models.py:AuditLog`, `ResourceTimelineEvent` | core | **Infrastructure** (ORM) | SQLAlchemy models. | — |
| `core/request_log_queue.py` | infra | **Infrastructure** | Batched background `RequestLog` writer thread. | — (already infra) |
| `core/security.py` (hashing/JWT) | core | **Infrastructure** (crypto adapter) behind a port; the *policy* (token TTL/revocation) is **Application** | bcrypt/JWT libs are drivers; "is this token revoked / what lifetime" is a use-case. | The library calls leak into `auth/service`; hide behind `PasswordHasher`/`TokenService` ports. |

### Module: `contracts`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `contracts/lifecycle.py:ALLOWED_TRANSITIONS` | service-adjacent | **Domain** | The state-machine of legal lifecycle stages — the canonical enterprise rule. | Nothing — it's already pure data; just move it inward. |
| `contracts/lifecycle.py:allowed_transitions_for` | service | **Domain** | Pure query over the state machine. | — |
| `contracts/lifecycle.py:transition_contract_stage` | service (5 callers) | **SPLIT** → guard logic + `Contract.transition_to(...)` is **Domain** (raises `ContractStageTransitioned`); the persistence (`db.add(ContractStageHistory)`, audit, timeline) is **Application**/**Infra** via handlers | Transition legality, signed-version-required-for-ACTIVE, override authorization are enterprise rules; the writes are not. | **`HTTPException` (`lifecycle.py:82`), `db.add`/`Session`, `write_audit_log`, `write_timeline_event`, `select(ContractVersion)`.** Domain must raise a domain error (not HTTP 409) and emit an event, not write rows. (See §C.) |
| `contracts/access.py:accessible_contract_filter` | core | **SPLIT** → rule **Domain**; SQLAlchemy filter expression **Infrastructure** (repo) | "Who may see a contract" is a Domain rule; expressing it as a SQLAlchemy `where` clause is a repository concern. | Returns a SQLAlchemy boolean clause → the *rule* must be expressible without `sqlalchemy`. |
| `contracts/access.py:user_can_access_contract` | core | **Domain** (predicate) | Pure access decision over contract + user attributes. | `Session`/`select` — push the lookups into a repository; keep the decision pure. |
| `contracts/service.py:get_contract_for_user`, `list_contracts_for_user` | service | **Application** (query use case) using **repo** | Authorization + fetch orchestration. | `Session`, `select` → repository port. |
| `contracts/service.py:update_contract_metadata` | service | **Application** | Use case; mutates + audits. | `write_audit_log`, `Session.commit`/`flush` → events + repo + UoW. |
| `contracts/service.py:contract_hub_summary`, `list_contract_stage_history`, `list_contract_activity` | service | **Application** (read models) → **Adapter** presenter for shaping | Aggregation/orchestration; the dict-shaping for HTTP is presenter. | `Session`/`select` → repo. |
| `contracts/models.py:Contract`, `ContractStageHistory`, `ContractParty` | models | **Infrastructure** (ORM) — mapped from **Domain entities** | SQLAlchemy persistence model. The pure `Contract` entity (with `transition_to`) is Domain; this is its table mapping. | — (mapping only) |
| `contracts/routes.py:transition_lifecycle` (`routes.py:90`) + all routes | route | **Interface Adapter** | HTTP I/O: parse, call use case, present, return. The `db.commit()` at `routes.py:112` moves to UoW. | `transition_contract_stage`, `db.commit`, `db.refresh`, ORM objects in responses → call use case, return DTOs. |

### Module: `ai` (hot — full decomposition in §A/§B)

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `ai/controller.py:AIController` (the class) | AI engine | **DISSOLVED** — see §A | God-object mixing 4 layers. No single home. | Everything outward it imports: see §A. |
| `ai/controller.py:run_structured_skill` | AI engine | **SPLIT** → `RunStructuredSkillUseCase` (**Application**) | Orchestrates fetch-prompt → call-provider → validate → cite → persist → commit. | `claude_client` (Infra), `db.commit()` (`controller.py:1053`), 7 domains' ORM models, `write_timeline_event`. |
| `ai/controller.py:stream_assistant_run` | AI engine | **SPLIT** → `AssistantRunStateMachine` (**Application**) + SSE in **Adapter/Infra** | The 8-iteration loop + confirmation-pause is a use-case state machine. | `claude_client.complete_with_tools` (Infra), `yield {...}` SSE events (Adapter), `db.commit()` per iteration (`206/238/259/291/325`). |
| `ai/controller.py:resume_assistant_run` | AI engine | **SPLIT** → resume branch of `AssistantRunStateMachine` (**Application**) | Resume-after-confirmation is the same state machine re-entered. | Same as stream + `write_audit_log` (`controller.py:400`). |
| `ai/controller.py:_persist_skill_output` (+ `_persist_metadata/_clauses/_obligations/_renewal`) | AI engine | **SPLIT** → *decision* (which writes happen) **Application**; *writes* **Infrastructure** via repositories | The `if spec.name ==` dispatch is a persistence policy (Application); the `db.add(Obligation/RenewalEvent/...)` is repo/Infra. | **Direct construction of `Contract`/`ClauseExtraction`/`Obligation`/`ObligationReminder`/`RenewalEvent` ORM rows (`controller.py:1256-1268, 1486, 1547+`)**, `write_audit_log`, `write_timeline_event`, `db.flush`. Persist through domain repositories, not ORM constructors. |
| `ai/controller.py:_validate_and_store_citations` | AI engine | **SPLIT** → validate is **Domain** (`validate_citations` rule); store is **Infra** (`AICitation` repo); the *gate* (set NEEDS_REVIEW) is **Application** | Citation fuzz-match-vs-source is an enterprise correctness rule; persisting `AICitation` is infra. | `db.add(AICitation)` ORM (`controller.py:1161`); validation rule must not touch `Session`. |
| `ai/controller.py:_extract_structured_output` | AI engine | **Interface Adapter** (`ToolUseTranslator`) | Maps Claude `tool_use` blocks → internal dict. Provider-shape translation. | `ClaudeProviderResponse` shape — fine to know provider DTO here; must not leak provider shape into Application. |
| `ai/controller.py:_log_ai_call`, `_log_assistant_ai_call`, `_record_usage` | AI engine | **SPLIT** → *what to record* **Application**; `AICallLog`/`UsageRecord` write **Infrastructure** | Observability persistence. | `db.add`/`db.flush`, `claude_client.provider` → repo + injected provider name. |
| `ai/controller.py:_maybe_contract_context` / `ai/context.py:build_contract_context` | AI engine | **Application** (loads context) using **repo** | Orchestrates loading Contract → authoritative version → snapshot text (truncated). | `Session`/`select`, ORM models → repository port returning a `ContractAIContext` value object. |
| `ai/controller.py:_ensure_skill_enabled` / `is_tool_enabled` | AI engine | **SPLIT** → flag *rule* **Application**; `AdminSetting` read **Infra** repo | "Is this skill/tool enabled for org" is a use-case guard. | `Session` read of `AdminSetting` → `FeatureFlagPort`. |
| `ai/registry.py:SkillRegistry`, `SkillSpec` | AI engine | **SPLIT** → `SkillSpec` *definition* (name, output_model, citation rules, return_tool_name) is **Domain**; registry lookup is **Application**/**Adapter** | A skill's contract (its validated output schema + citation requirement) is an enterprise definition; resolving by name is orchestration. | `SkillSpec.output_model.model_json_schema()` is Pydantic — acceptable as a Domain schema descriptor, but the registry must not import providers. |
| `ai/schemas.py` (`ContractMetadataOutput`, `ObligationExtractionOutput`, `RenewalExtractionOutput`, `TabularCellOutput`, edit/citation models) | AI engine | **Domain** (validated value objects) | These Pydantic models *are* the validation rules for AI output — enterprise contracts on what the AI may assert. | Nothing outward; they're already pure data+validation. Keep free of ORM. |
| `ai/citations.py:validate_citations`, `CitationInput` | AI engine | **Domain** | Pure fuzzy quote-vs-source matching + OCR tolerance — a correctness rule. | Nothing — already pure; ensure no `Session` creeps in. |
| `ai/fallback.py:_fallback_output` | AI engine | **Domain**/**Application** (degraded-result policy) | Metadata-skill fallback is a business policy for graceful degradation. | — (pure) |
| `ai/prompt_builder.py:build_structured_skill_prompt`, `BuiltPrompt` | AI engine | **Application** (assembles prompt) | Composes system+user prompt from spec + context. String assembly, no I/O. | — (keep free of provider SDK). |
| `ai/prompt_versions.py:get_active_prompt_bundle`, `BuiltPrompt` bundle | AI engine | **SPLIT** → resolution policy **Application**; DB-backed version read **Infra** repo | "Pick active prompt version for org" is a use case; reading the row is infra. | `Session`/`select` of prompt-version rows → `PromptVersionRepository`. |
| `ai/tool_policy.py`, `ai/confirmations.py:create_confirmation` | AI engine | **SPLIT** → confirmation *policy* (which categories need confirmation) **Domain**; `AIConfirmation` write **Infra** repo; orchestration **Application** | "External/destructive actions require confirmation" is an enterprise rule. | `db.add(AIConfirmation)` ORM, `Session`. |
| `ai/tool_registry.py:ToolSpec`, `ToolRegistry`, the 24 `_register(...)` | AI engine | **SPLIT** — see §B (spec=**Domain**, registry=**Application**, dispatch=**Adapter**) | A tool's contract is Domain; the registry is an Application catalog. | `ToolSpec.input_model` is Pydantic — OK as Domain schema; registry must not import services. |
| `ai/tool_runtime.py:ToolRuntime` + `_execute_validated` dispatch | AI engine (god-object) | **Interface Adapter** (`ToolDispatcher`) — see §B | Translates `tool_name` string → a use-case invocation; gates permission/flag/confirmation. | **`approvals.service`, `contract_files.service`, `playbooks.service`, `signatures.service`, `tabular_review.service`, `jobs.service`, `contract_brain.retrieval`, `contracts.lifecycle`, `integrations.{docusign,resend,storage}`, all domain ORM, `db.commit()` (`690/717`), `db.flush` (~40).** Each tool must call ONE Application use case, never reach into another domain's service or the DB. |
| `ai/embeddings.py:generate_embeddings_for_snapshot`, `_embed`, `EMBEDDING_MODEL/DIMENSIONS` | AI engine | **SPLIT** → embedding *generation* (model call) **Infrastructure** (`EmbeddingModel` port); persistence **Infra** repo (`VectorStore`); orchestration **Application** | Running the embedding model + writing `ContractEmbedding` is a driver concern. | `db.add`/`Session`, `ContractEmbedding` ORM, `pgvector.Vector` → behind `EmbeddingModel` + `VectorStore` ports (§D). |
| `ai/models.py:AISkillRun`, `AICitation`, `AICallLog`, `UsageRecord` | models | **Infrastructure** (ORM) — mapped from Domain `SkillRun`/`Citation` entities | SQLAlchemy persistence. The pure `SkillRun` state (RUNNING/SUCCEEDED/NEEDS_REVIEW/WAITING_CONFIRMATION transitions) is Domain. | — (mapping only) |
| `ai/routes.py` (rerun endpoints, `db.commit` 111/175) | route | **Interface Adapter** | HTTP entry to skill reruns. | `db.commit`, calling controller internals → call use case. |
| `integrations/claude.py:ClaudeClient`, `complete_structured/complete_with_tools/complete_text`, `ClaudeProviderResponse` | integration | **SPLIT** → `AnthropicAIClient` impl **Infrastructure**; `AbstractAIClient` port consumed by **Application**; `ClaudeProviderResponse`→internal DTO map is **Adapter** | The httpx POST + tenacity retry is a driver; the use case depends only on the port. | — (Infra is outermost). But Application must depend on the *port*, never on `claude_client` the singleton or `httpx`. |
| `integrations/_claude_mock.py` | integration | **Infrastructure** (test double of the port) | Canned provider payloads. | — |

### Module: `assistant`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `assistant/routes.py` (10 commits) — stream/resume SSE endpoints | route | **Interface Adapter** (SSE controller) | Owns the `StreamingResponse` / SSE framing; delegates the loop to `AssistantRunStateMachine`. | `db.commit` (10×), driving the controller loop → emit events produced by the Application state machine; do the framing only. |
| `assistant/models.py:AssistantRun`, `AssistantToolCall`, `AISession` | models | **Infrastructure** (ORM) — mapped from Domain `AssistantRun` aggregate | Persistence; `AssistantRun.status` (`waiting_confirmation`/`succeeded`/`failed`) transitions are the Domain aggregate. | — |
| `assistant/...:list_contract_handles` | service | **Application** read | Resolves session contract handles. | `Session` → repo. |

### Module: `approvals`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `approvals/service.py:evaluate_routing`, `_matches` | service | **Domain** (routing rule) | Pure rule: which approver/role a contract routes to. | `Session` if any lookup — push candidate rules in via repo; keep matching pure. |
| `approvals/service.py:submit_contract_for_approval` | service | **Application** (use case) | Orchestrates: create request → email → transition stage → audit. | `resend_client` (`service.py:118`), `transition_contract_stage` (`159`), `write_audit_log`, `Session.flush` → `EmailPort`, raise `ApprovalRequested` + `ContractStageTransitioned` events, repo. |
| `approvals/service.py:_apply_decision`, `decide_in_app`, `redeem_token_decision` | service | **Application** | Decision use cases; mutate + transition + audit. | `transition_contract_stage` (`203`), `write_audit_log`, `Session` → events + repo. |
| `approvals/models.py:ApprovalRequest`, `ApprovalRoutingRule` | models | **Infrastructure** (ORM) | Persistence. | — |
| `approvals/routes.py` (4 commits) | route | **Interface Adapter** | HTTP I/O. | `db.commit` → UoW. |

### Module: `signatures`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `signatures/service.py:validate_signature_recipients` | service | **Domain** (rule) | Pure recipient/role validity rule. | — |
| `signatures/service.py:_create_signed_version` | service | **Application** | Creates a SIGNED `ContractVersion`. | `contract_files.service`, `Session` → repo + use case. |
| `signatures/service.py:sync_signature_request` | service | **Application** | Reconciles DocuSign envelope status → request + lifecycle. | `transition_contract_stage` (`239`), `write_audit_log` → events; DocuSign read behind port. |
| `signatures/models.py:SignatureRequest`, recipients | models | **Infrastructure** (ORM) | Persistence. | — |
| `signatures/routes.py` (3 commits) + webhook | route | **Interface Adapter** | HTTP/webhook I/O; HMAC verify is Infra (DocuSign adapter). | `db.commit`; envelope-create call → `ESignaturePort`. |
| `integrations/docusign.py:DocuSignClient`, `verify_connect_signature` | integration | **Infrastructure** behind `AbstractESignatureClient` port | JWT/httpx/HMAC drivers. | — (Application depends on port). |

### Module: `contract_files`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `contract_files/service.py:create_contract_from_upload` | service (self-commits, 273) | **Application** (use case w/ compensation) | Orchestrates: store bytes → extract text → persist intake → queue jobs; rolls back + deletes blob on failure. | `storage_service` (`306`), `reducto_client` (`123`), `create_job`/`dispatch_job` (`257`), `write_audit_log`, `Session.commit`/`rollback` → `BlobStorePort`, `OcrPort`, `JobQueuePort`, events, UoW (the compensation becomes a saga step). |
| `contract_files/service.py:_resolve_extracted_text`, `text_extraction.py` | service | **SPLIT** → "needs OCR? quality threshold" rule **Domain**; native + Reducto extraction **Infrastructure** | The `quality_score < 0.55 → OCR` decision is a Domain rule; the extractors are drivers. | `reducto_client`, file-format libs → `OcrPort` + `TextExtractorPort`. |
| `contract_files/service.py:_persist_intake_records`, `_text_snapshot_validation_status`, `next_version_number` | service | **Application** + one **Domain** rule (`_text_snapshot_validation_status`) | Persistence orchestration; the validation-status threshold is a Domain rule. | `Session`/ORM → repo. |
| `contract_files/service.py:_dispatch_initial_jobs`, `_queue_initial_contract_jobs`, `queue_activation_ai_jobs`, `requeue_contract_ai_jobs` | service | **Application** | Decide which AI jobs to enqueue on intake/activation. | `create_job`/`dispatch_job` (`257/472/519`) → `JobQueuePort`. (Job-creation logic is duplicated with `tool_runtime` and `tabular_review` — Agent 3 should unify.) |
| `contract_files/models.py:ContractVersion`, `ContractTextSnapshot`, `StorageObject` | models | **Infrastructure** (ORM) | Persistence. | — |
| `contract_files/routes.py` (9 commits) | route | **Interface Adapter** | HTTP upload/download I/O. | `db.commit`, `storage_service` → use case + ports. |
| `integrations/reducto.py:ReductoClient` | integration | **Infrastructure** behind `OcrPort` | Reducto SDK driver. | — |
| `integrations/storage.py:storage_service` | integration | **Infrastructure** behind `BlobStorePort` | Blob storage driver. | — |

### Module: `playbooks`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `playbooks/service.py:evaluate_rules_against_text`, `ai_deviations_to_evaluated`, `_ai_citation_status`, `_normalize_severity`, `generated_default_rules`, `_proposed_text`, `_find_phrase`, `_suggested_fix` | service | **Domain** (playbook evaluation rules) | Pure: applying rules to text, deviation severity, default-rule generation, phrase anchoring. The enterprise rule engine. | `ContractTextSnapshot` ORM in `_ai_citation_status` → take plain text/snapshot value object. |
| `playbooks/service.py:execute_playbook_run`, `record_deviation_decision`, `select_run_version`, `create_initial_playbook`, `clone_playbook_version`, `next_playbook_version_number` | service | **Application** | Orchestrate a governed run: load version → evaluate → persist deviations → maybe build redline → audit. | `write_audit_log` (`490/546/700`), `storage_service` (`723`), `Session`/`flush` (~11) → events, `BlobStorePort`, repo. |
| `playbooks/service.py:_create_playbook_redline_version`, `_build_playbook_redline_docx`, `_enable_word_track_revisions`, `_append_*`, `_set_revision_attrs` | service | **SPLIT** → DOCX track-changes *generation* logic is **Domain**/pure; writing the `StorageObject` is **Infra** | Word-XML redline construction is pure transformation (no I/O); storing the file is infra. | `storage_service`, `Session` → return bytes; let an Application step persist via `BlobStorePort`. |
| `playbooks/models.py:Playbook`, `PlaybookVersion`, `PlaybookRule`, `PlaybookDeviation` | models | **Infrastructure** (ORM) | Persistence. | — |
| `playbooks/routes.py` (11 commits) | route | **Interface Adapter** | HTTP I/O. | `db.commit` → UoW. |

### Module: `tabular_review`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `tabular_review/service.py:dispatch_cells` | service (2 commits) | **Application** | Fan out one AI job per cell. | `create_job`/`dispatch_job` (`52`), `Session.commit` → `JobQueuePort`, UoW. |
| `tabular_review/service.py:build_table_context` | service | **Application** read | Assembles per-table context for the cell skill. | `Session`/`select` → repo. |
| `tabular_review/service.py:build_xlsx` | service | **SPLIT** → spreadsheet shaping is **Adapter** (presenter); cell-status→display rule is Domain | XLSX export is presentation. | `Session` → take a read-model in. |
| `tabular_review/models.py:TabularReview`, `TabularReviewColumn`, `TabularReviewCell` | models | **Infrastructure** (ORM) | Persistence; `TabularCellStatus` transitions are Domain. | — |
| `tabular_review/routes.py` (8 commits) | route | **Interface Adapter** | HTTP I/O. | `db.commit` → UoW. |

### Module: `obligations`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `obligations/routes.py:update_obligation`, `complete_obligation` | route (fat) | **SPLIT** → orchestration to **Application** use case; HTTP shell stays **Adapter** | Mutating + auditing an obligation is a use case currently inlined in the route. | `write_audit_log` (`95/118`), `db.commit` (5×) → events + use case + UoW. |
| `obligations/routes.py:trigger_obligation_extraction` | route | **SPLIT** → enqueue use case **Application**; route **Adapter** | Deciding to enqueue an extraction job is a use case. | `create_job`/`dispatch_job` (`154/166`) → `JobQueuePort`. |
| `obligations/routes.py:run_obligation_reminders` | route | **SPLIT** → reminder-window policy **Domain**/**Application**; route **Adapter** | "Which reminders are due, send them" is a use case; the due-date rule is Domain. | `resend_client` (`212`), `write_audit_log` (`220`), `db.commit` → `EmailPort`, events. |
| `obligations/models.py:Obligation`, `ObligationReminder` | models | **Infrastructure** (ORM) | Persistence; `status` transitions (open/completed/cancelled) are Domain. | — |

### Module: `renewals`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `renewals/routes.py:decide_renewal` | route (fat) | **SPLIT** → decision use case **Application**; route **Adapter** | Renewal decision + audit is a use case. | `write_audit_log` (`80`), `db.commit` → events. |
| `renewals/routes.py:run_renewal_window_check` | route | **SPLIT** → renewal-window policy **Domain**; orchestration **Application**; route **Adapter** | "Contract enters RENEWAL_DUE within window; notify owner" is a Domain rule + use case. | `transition_contract_stage` (`130`), `resend_client` (`140`), `write_audit_log` (`149`) → `ContractStageTransitioned`/`RenewalDue` events, `EmailPort`. |
| `renewals/models.py:RenewalEvent` | models | **Infrastructure** (ORM) | Persistence. | — |

### Module: `workflows`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `workflows/routes.py:list_workflows`, `create_workflow`, run endpoints | route (logic in route) | **SPLIT** → orchestration **Application**; route **Adapter** | Workflow CRUD + run orchestration inlined in routes. | `db.commit` (`50`), ORM → use case + repo. |
| `workflows/models.py:Workflow` | models | **Infrastructure** (ORM) | Persistence. | — |

### Module: `projects`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `projects/access.py:user_can_access_project`, `user_has_project_access_for_contract`, `_active_share_level` | core | **Domain** (access predicates) | Pure project-membership/share-level rules. | `Session`/`select` → push lookups into repo, keep decision pure. |
| `projects/access.py:project_scope_query`, `get_project_for_user` | core | **SPLIT** → rule **Domain**; SQLAlchemy query **Infra** (repo) | Scope rule is Domain; the `select` is infra. | Returns SQLAlchemy query. |
| `projects/routes.py` (13 commits — most of any route file) | route (very fat) | **SPLIT** → many small **Application** use cases; routes **Adapter** | 13 inlined orchestrations (create/share/membership/etc.). | `db.commit` (13×), ORM, audit → use cases + UoW + events. |
| `projects/models.py:Project`, `ProjectContract`, share models | models | **Infrastructure** (ORM) | Persistence. | — |

### Module: `auth`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `auth/service.py:*` (19 self-commits: `login_user`, `register_user`, `refresh_login_tokens`, `revoke_refresh_token`, `create_user_invitation`, `accept_user_invitation`, `decide_join_request`, `decide_user_approval`, `create_first_admin`, `bootstrap_roles`, password reset, API keys, …) | service (self-commits) | **Application** (use cases) — each emits an `AuditLogRequested` event | Auth flows are orchestration: validate → mutate → audit → issue tokens. | **`write_audit_log` (≈14 sites), `db.commit` (19 sites), `core.security` crypto calls** → `PasswordHasher`/`TokenService` ports, `AuditLogRequested` events, UoW. |
| `auth/service.py:_token_response`, `_user_response`, `_public_*` | service | **Interface Adapter** (presenters) | Shape ORM → API dicts. | ORM in → take read-models/DTOs. |
| `auth/service.py:is_access_token_revoked`, `_find_refresh_token`, `_role_by_name` | service | **Application** read using repo | Token/role lookups. | `Session`/`select` → repo. |
| `auth/models.py:User`, `Role`, `RefreshToken`, `ApiKey`, `UserInvitation`, `OrgJoinRequest` | models | **Infrastructure** (ORM) | Persistence; `User.permission_values` derivation is a Domain rule. | — |
| `auth/routes.py`, `auth/deps.py:require_permission/get_current_user` | route/deps | **Interface Adapter** | HTTP auth wiring; permission *rule* (`core/rbac.has_permission`) stays Domain. | `db.commit`, ORM in responses → use cases + DTOs. |

### Module: `contract_brain`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `contract_brain/ingestion.py:ingest_contract_brain` | service | **Application** (use case) | Orchestrates: mark prior nodes/edges stale → walk parties/clauses/obligations/approvals/signatures/deviations → build new graph → audit. | `Session`/`flush` (`47/63`), 6 domains' ORM, `write_audit_log` (`179`), `write_timeline_event` → `KnowledgeGraphRepository`, events. |
| `contract_brain/retrieval.py:_vector_chunks` | service | **SPLIT** → query *intent* **Application**; vector search **Infrastructure** (`AbstractVectorStore`) | Top-k semantic retrieval is a use-case step; the cosine search is a driver. | **`ContractEmbedding.embedding.cosine_distance(query_vec)` (`retrieval.py:63,240`) — the pgvector SQLAlchemy operator** + `_embed` + `select`. Must go behind `VectorStorePort.search(...)` (§D). |
| `contract_brain/retrieval.py:_fulltext_clauses`, `_graph_facts` | service | **SPLIT** → ranking *rule* **Domain**; the `select` **Infra** (repo) | Term-overlap scoring is a Domain ranking rule; the query is infra. | `Session`/`select`, ORM → repo returning value objects. |
| `contract_brain/retrieval.py:resolve_scope_contract_ids`, `assemble_context`, `precedent_contracts` | service | **Application** (read orchestration) | Combine vector + fulltext + graph into a context bundle. | `Session`, `accessible_contract_filter` SQLAlchemy clause → repos + Domain access rule. |
| `contract_brain/models.py:KnowledgeNode`, `KnowledgeEdge`, `ContractEmbedding` | models | **Infrastructure** (ORM); `ContractEmbedding.embedding` is `pgvector.Vector(384)` | Persistence + vector column. | — (the pgvector type is contained here, never escapes to Domain/Application). |
| `contract_brain/routes.py` (3 commits) | route | **Interface Adapter** | HTTP I/O. | `db.commit` → UoW. |

### Module: `jobs`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `jobs/tasks.py:run_ai_job` (the `@celery_app.task`) | Celery task | **Interface Adapter** (thin shim) | The Celery binding + `asyncio.run` is a driver shim that should call ONE use case. | After §E extraction: nothing but `RunAiJobUseCase`. Today it transitively imports `ai.controller`, `contract_brain.ingestion`, `ai.embeddings`. |
| `jobs/tasks.py:_run_ai_job` | Celery task | **Application** (`RunAiJobUseCase`) — see §E | The job-type dispatch + session ownership + status lifecycle + 11 commits is orchestration. | **`SessionLocal()` (`tasks.py:26`), 11 `db.commit()`, direct ORM (`db.get(JobRun/Contract/...)`).** Session/UoW injected; commits collapse to UoW boundaries. |
| `jobs/tasks.py:_queue_contract_brain_ingestion` | Celery task | **Application** (auto-chaining policy) — see §E | The "after clause/obligation/renewal extraction succeeds → idempotently enqueue brain ingestion" rule is a use-case orchestration. | **Lazy `from app.jobs.service import create_job, dispatch_job` (`tasks.py:227`) papering a circular edge**, `db.commit` (`246/251`) → `JobQueuePort`. |
| `jobs/tasks.py:_sync_job_from_skill_runs`, `_mark_job_succeeded` | Celery task | **Application**; `_mark_job_succeeded` is a Domain `JobRun` state transition | Reconcile job status from skill runs. | `Session`/`select`, `db.commit` (`205`) → repo + UoW. |
| `jobs/service.py:create_job`, `dispatch_job` | service | **SPLIT** → `dispatch_job`'s `run_ai_job.delay()` is **Infrastructure** (`JobQueuePort` impl); `create_job` persistence is **Infra** repo; the *decision* to dispatch is **Application** | Enqueue-to-Celery is a driver. | `run_ai_job.delay(...)` (Celery), `Session` → `JobQueuePort`. |
| `jobs/celery_app.py:celery_app` | infra | **Infrastructure** | Celery/Redis config. | — |
| `jobs/models.py:JobRun` | models | **Infrastructure** (ORM); `JobStatus` transitions are Domain | Persistence. | — |
| `jobs/routes.py:_reap_stuck_jobs` + routes (3 commits) | route | **SPLIT** → stale-job (20-min TTL) reaping *policy* **Domain**/**Application**; route **Adapter** | The TTL-based reaping is a use case; HTTP listing is adapter. | `db.commit`, ORM → use case + repo. |

### Module: `organizations`, `admin`

| Component (`path:symbol`) | Current layer | Correct CA layer | Why it belongs there | Must STOP knowing about |
|---|---|---|---|---|
| `organizations/routes.py` (1 commit) | route | **SPLIT** → org use case **Application**; route **Adapter** | Org mutation inlined. | `db.commit` (`55`) → use case. |
| `admin/routes.py` (1 commit), `AdminSetting` feature flags | route | **SPLIT** → feature-flag *read/write* use case **Application** (consumed via `FeatureFlagPort`); route **Adapter** | Admin toggling skills/tools is a use case. | `db.commit` (`66`) → `FeatureFlagPort` + use case. |

### Module: `integrations` (summary — all Infrastructure behind ports)

| Component (`path:symbol`) | Correct CA layer | Port the Application sees |
|---|---|---|
| `integrations/claude.py:claude_client` | **Infrastructure** | `AbstractAIClient` (`complete_structured`, `complete_with_tools`, `complete_text`) |
| `integrations/docusign.py:docusign_client` | **Infrastructure** | `AbstractESignatureClient` |
| `integrations/reducto.py:reducto_client` | **Infrastructure** | `OcrPort` |
| `integrations/resend.py:resend_client` | **Infrastructure** | `EmailPort` |
| `integrations/storage.py:storage_service` | **Infrastructure** | `BlobStorePort` |

---

## A. Decomposing `AIController` (~1,680 LOC, `ai/controller.py`)

The class is a god-object straddling all four layers. It is **dissolved**, not relocated. The decomposition yields the following named sub-components:

### A.1 `run_structured_skill` (`controller.py:885`)

| Slice | Destination | What it is |
|---|---|---|
| The orchestration: resolve skill → resolve prompt version → load contract context → build prompt → create `SkillRun` → call provider → validate → cite → persist derived rows → record usage → finish job → commit | **Application** → `RunStructuredSkillUseCase` | The use-case sequence. Holds the `commit=True` decision (becomes a UoW boundary). Depends on `AbstractAIClient`, `PromptVersionRepository`, `ContractContextRepository`, the domain repos, and the citation/skill Domain rules — never on httpx or ORM directly. |
| `await claude_client.complete_structured(... input_schema=spec.output_model.model_json_schema() ...)` (`969`) | **Infrastructure** → `AnthropicAIClient.complete_structured` (impl of `AbstractAIClient`) | The httpx POST to `/v1/messages`, `tool_choice={type:tool,name:return_<skill>}`, tenacity retry, 600s timeout. |
| `SkillSpec` (output_model, `return_tool_name`, citation requirement, temperature/max_tokens) | **Domain** → `SkillDefinition` value object | The enterprise contract of what a skill asserts + how it must be validated. |
| `spec.output_model.model_validate(raw_output)` (Pydantic validation) + `validate_citations` (`citations.py`) | **Domain** → `SkillOutputValidator` / `CitationValidator` rules | Enterprise correctness rules: the AI's structured output must conform to schema; every quote must fuzzy-match source text (OCR-tolerant). |
| `_extract_structured_output` (`1133`) — pull the `tool_use` block matching `return_tool_name` | **Interface Adapter** → `ToolUseTranslator.extract_structured_output` | Maps Claude provider blocks ↔ internal dict. |
| `_persist_skill_output` dispatch decision (`1242`) | **Application** (within the use case) | *Which* domain rows to write per skill — a persistence policy. |
| `_persist_metadata/_clauses/_obligations/_renewal` ORM writes (`1270/1342/1449/1547`) | **Infrastructure** via repositories (`ContractRepository`, `ClauseRepository`, `ObligationRepository`, `RenewalRepository`) | The actual `db.add(...)`. |
| `_validate_and_store_citations` store half (`1161` `db.add(AICitation)`) | **Infrastructure** → `CitationRepository` | Persist validated citations. |
| `_log_ai_call` / `_record_usage` writes | **Infrastructure** → `AICallLogRepository` / `UsageRepository` | Observability persistence. |
| `_maybe_contract_context` / `context.build_contract_context` | **Application** read + **Infra** repo | Returns a `ContractAIContext` value object (text truncated to 25 000 chars — that truncation cap is a Domain/Application policy). |

### A.2 `stream_assistant_run` (`controller.py:94`) and A.3 `resume_assistant_run` (`controller.py:328`)

Both are the **same** use case entered at two points. Decomposition:

| Slice | Destination | What it is |
|---|---|---|
| The `for iteration in range(settings.ai_max_tool_iterations)` loop (max 8), the "no `tool_use_blocks` → finish", the confirmation-pause (`confirmation_required` → persist `WAITING_CONFIRMATION` + `provider_state`, stop), the resume re-entry from `provider_state` | **Application** → `AssistantRunStateMachine` | A pure use-case state machine over states `RUNNING → (TOOL_CALL → RUNNING)* → {SUCCEEDED, WAITING_CONFIRMATION, FAILED}`. **It survives without httpx/SSE** by being driven as a generator/coroutine that *yields domain step-results* (e.g. `MessageDelta`, `ToolStarted`, `ToolFinished`, `ConfirmationRequired`, `RunSucceeded`) and *awaits* injected ports: it calls `AbstractAIClient.complete_with_tools(messages, tools)` (a port, returns a provider-neutral response DTO) and `ToolDispatcher.execute(tool_name, input)` (a port). The 8-iteration bound is `settings.ai_max_tool_iterations` injected as a plain int. The confirmation-pause is just the state machine persisting its `provider_state` (messages + pending tool_use + skill_run_id) via a repository and returning the `ConfirmationRequired` step — no transport knowledge. Resume reconstructs the machine from persisted `provider_state` and continues the loop. |
| `await claude_client.complete_with_tools(...)` (`171/448`) | **Infrastructure** → `AnthropicAIClient.complete_with_tools` (impl of `AbstractAIClient`) | httpx streaming/non-streaming call, `tool_choice:auto`. |
| `yield {"event": "...", "payload": {...}}` SSE dicts (`198/218/260/271/...`), the SSE event names/framing | **Interface Adapter** → `AssistantSSEPresenter` (in `assistant/routes.py`) | Translates the Application's domain step-results → SSE `event:`/`data:` frames over the `StreamingResponse`. |
| The actual SSE transport (`StreamingResponse`, chunked HTTP) | **Infrastructure** | The ASGI streaming mechanism. |
| Mapping `provider_response.content_blocks` / `tool_use_blocks` ↔ internal messages list; building `tool_result` blocks; `_model_safe_result` / `_json_tool_result` | **Interface Adapter** → `ToolUseTranslator` | Provider-event ↔ internal-shape translation. |
| `db.commit()` per iteration (`206/238/259/291/325` and `486/516/528/559/592`) | **Application** boundary realized by **Infra** UoW | Becomes explicit UoW commit points; the per-iteration durability (so a long run isn't lost) is an Application policy, executed by the UoW. |
| `_assistant_tool_schemas` (`595`) — build the advertised tool list honoring permission + feature flag | **Application** (policy) → produces schemas from the Domain `ToolSpec`s | "Which tools are advertised to Claude" is a use-case gate. |
| `write_audit_log` in resume (`400`) | **Application** event (`AuditLogRequested`) → **Infra** writer | See §C. |

### A.4 Concrete sub-component piece-list (what Agent 4 builds)

- **Application:** `RunStructuredSkillUseCase`, `AssistantRunStateMachine` (handles both stream + resume), `BuildAssistantToolSchemasPolicy`, `PersistSkillOutputPolicy`, `ResolveContractContextUseCase`, `FeatureFlagGate`.
- **Domain:** `SkillDefinition`, `SkillOutputValidator`, `CitationValidator` (`validate_citations`), `SkillRun` aggregate (status transitions), `AssistantRun` aggregate (status + provider_state), `ContractAIContext` value object, the 25 000-char context cap policy.
- **Interface Adapter:** `ToolUseTranslator` (`_extract_structured_output`, content-block↔message mapping, `_model_safe_result`/`_json_tool_result`), `AssistantSSEPresenter`.
- **Infrastructure:** `AnthropicAIClient` (impl of `AbstractAIClient`), all repositories (`SkillRunRepository`, `AICallLogRepository`, `CitationRepository`, `UsageRepository`, plus the domain repos), the UoW, SSE transport.

---

## B. `ToolRegistry` + the 24 `ToolSpec` items

- **`ToolSpec`** (`tool_registry.py:118`, frozen dataclass: name, description, category, `required_permission`, `input_model`, `output_model`, `confirmation_policy`, `feature_flag`, `exposed_session_types`, `idempotency_strategy`) → **Domain** value object. It is the *contract* of a tool: its permission gate, its confirmation requirement, its input/output schema, its idempotency strategy. `requires_confirmation` (`:133`) is a Domain rule. The `input_model` being a Pydantic class is acceptable as a Domain schema descriptor.
- **`ToolRegistry`** (`tool_registry.py:137`, `register`/`get`/`all`/`exposed_for_session_type`) → **Application** catalog (a registry/lookup service). It holds no I/O. `exposed_for_session_type` (`:155`) is an Application policy.
- **The 24 `_register(...)` calls** (`tool_registry.py:195-309`) → **Domain** data (the catalog content). Each is a `SkillDefinition`-adjacent tool contract.
- **`ToolRuntime`** (`tool_runtime.py:86`) → **Interface Adapter** (`ToolDispatcher`). Its job is: validate input against `spec.input_model`, gate permission + feature-flag, persist the `AssistantToolCall` record, branch on `confirmation_policy`, then dispatch `tool_name` → the matching **Application use case**. The current `_execute_validated` if-chain (`tool_runtime.py:213-271`) is the dispatch; each `_<tool>` method's *body* must be emptied into an Application use case so the tool becomes a one-line call.

### The 24 tools by group

End state Agent 4 needs: **each tool's contract (`ToolSpec`) is Domain; each tool's execution is an Adapter method that validates + calls one Application use case; zero DB/HTTP touched in the tool method itself.** The schema (input/output model) is Domain; the dispatch is Adapter; the work is an Application use case backed by ports.

| Group | Tools | Schema layer | Execution layer | The use case each calls |
|---|---|---|---|---|
| **Read-only / query** | `read_contract`, `find_in_contract`, `list_project_contracts`, `get_contract_status`, `list_workflows`, `list_playbooks`, `read_table_cells`, `my_attention_items`, `find_contracts`, `list_obligations` | **Domain** (`ToolSpec` + input model) | **Adapter** → **Application** read use case via repos | `ReadContractQuery`, `FindInContractQuery`, `ListPortfolioObligationsQuery`, `MyAttentionItemsQuery`, etc. Today they do `db.get`/`select` inline (`tool_runtime.py:273-526`) — must move behind repos. |
| **AI-retrieval** | `ask_contract_brain` | **Domain** | **Adapter** → **Application** | `AskContractBrainUseCase` (wraps retrieval; uses `VectorStorePort`, §D). |
| **AI-compute / draft (read-mostly, produces proposals)** | `generate_contract_docx`, `edit_contract`, `replicate_contract_version`, `run_playbook_review`, `redline_against_playbook` | **Domain** | **Adapter** → **Application** | `GenerateContractDocxUseCase`, `ProposeContractEditsUseCase`, `RunPlaybookUseCase`. The redline/anchoring transforms are Domain; the DOCX store is `BlobStorePort`. `edit_contract` (`tool_runtime.py:727`) calls `run_structured_skill` + anchors edits — splits into the structured-skill use case + the `AnchorEditsDomainService`. |
| **Mutating (writes / state change)** | `run_workflow`, `submit_for_approval`, `extract_obligations`, `create_tabular_review` | **Domain** | **Adapter** → **Application** | `RunWorkflowUseCase`, `SubmitForApprovalUseCase` (today `tool_runtime.py:1107` calls `approvals.service.submit_contract_for_approval` directly — must call the use case), `QueueObligationExtractionUseCase`, `CreateTabularReviewUseCase`. |
| **External-action (network side effects)** | `send_for_signature`, `external_share` | **Domain** | **Adapter** → **Application** w/ ports | `SendForSignatureUseCase` (`ESignaturePort`), `CreateExternalShareUseCase` (`BlobStorePort`). `confirmation_policy="required"`. |
| **Destructive** | `archive_contract` | **Domain** | **Adapter** → **Application** | `ArchiveContractUseCase`. `confirmation_policy="required"`. |

Every cross-domain `import`/call currently inside `tool_runtime.py` (`approvals.service`, `signatures.service`, `playbooks.service`, `tabular_review.service`, `contract_files.service`, `jobs.service`, `contract_brain.retrieval`, `contracts.lifecycle`, `integrations.*`) is severed: the dispatcher depends only on the Application use-case layer.

---

## C. `write_audit_log` & `transition_contract_stage` → Domain Events

### Current inline side effects

- `transition_contract_stage` (`lifecycle.py:66`): mutates `contract.lifecycle_stage`, `db.add(ContractStageHistory)`, calls `write_audit_log` (autonomous commit) + `write_timeline_event` (caller-session add) — all inline. Raises `HTTPException` for illegal transitions.
- `write_audit_log` (`audit.py:34`): opens its **own** `SessionLocal()`, takes `pg_advisory_xact_lock`, computes the hash chain, `durable_db.commit()` — **independent of the caller's transaction**, so an audit row persists even when the recorded operation later rolls back (and vice-versa).

### Domain Events that replace them

| Event | Raised where (Domain entity method) | Handled by (Application handler → Infra writer) |
|---|---|---|
| `ContractStageTransitioned` | `Contract.transition_to(to_stage, actor, override, ...)` — after validating against `ALLOWED_TRANSITIONS` and the signed-version-for-ACTIVE rule (raising a **domain** `IllegalStageTransition` error, not HTTP). | `RecordStageHistoryHandler` (writes `ContractStageHistory` via repo) + `AuditLogHandler` + `TimelineHandler`. |
| `AuditLogRequested` (carries action, resource, before/after, metadata, actor, request_id) | Any Domain entity method or Application use case that today calls `write_audit_log` (lifecycle, obligations, renewals, approvals, signatures, auth ×~14, brain ingestion). | `AuditLogHandler` → `AuditLogWriter` (Infra). The handler runs **inside the same UoW** as the operation. |
| `ResourceTimelineEventRequested` | Same sites that call `write_timeline_event`. | `TimelineHandler` → caller-session `db.add` via repo (already same-transaction today). |
| `ObligationsExtracted` | `_persist_obligations` (`controller.py:1524`) currently raises audit+timeline inline. | Audit + timeline handlers. |
| `RenewalExtracted` / `RenewalDecision` / `RenewalDue` | `_persist_renewal` and `renewals/routes.py` (`80/149`). | Audit + timeline + (for `RenewalDue`) a `ContractStageTransitioned` cascade. |
| `ApprovalRequested` / `ApprovalDecided` | `approvals/service.py` (`142/215`). | Audit + email handler. |
| `SignatureSynced` / `SignedVersionCreated` | `signatures/service.py` (`177/247`). | Audit + lifecycle cascade. |
| `ContractBrainIngested` | `contract_brain/ingestion.py:179`. | Audit + timeline. |

### How this removes the autonomous-commit coupling

Today `write_audit_log` self-commits in a separate `SessionLocal()` specifically so audit survives rollback. Under events: the Domain entity *raises* `AuditLogRequested` (pure, no I/O). The Application use case collects raised events and, **after the business transaction commits via the UoW**, dispatches them to handlers. The `AuditLogWriter` (Infra) writes the audit row — but the hash-chain integrity rule (`compute_audit_row_hash`, the `prev_hash` linkage, the advisory-lock serialization) becomes a property of the writer/repository, configurable as either same-transaction (atomic with the operation — the *correct* CA posture: an audit row exists iff the operation committed) or, if "audit-survives-rollback" must be preserved as an explicit business requirement, an *outbox*: events are written to an outbox table in the same transaction and a separate dispatcher commits the audit row. Either way, **no Domain or Application code opens a `SessionLocal()` or calls `db.commit()`** — the `audit.py:47,82` autonomous session is deleted, and the "audit can disagree with reality" anomaly becomes a deliberate, single-owner policy decision rather than an accidental property of an inline helper called from 30+ sites.

---

## D. `pgvector` operations → Repository / VectorStore

**Where it lives:** `contract_brain/retrieval.py:_vector_chunks` (`:58`) and `precedent_contracts` path (`:240`) call `ContractEmbedding.embedding.cosine_distance(query_vec)` — a `pgvector.sqlalchemy.Vector` operator — directly in a `select(...).order_by(distance).limit(...)`. The vector column is `ContractEmbedding.embedding = Vector(384)` (`contract_brain/models.py`). Generation is `ai/embeddings.py:_embed` + `generate_embeddings_for_snapshot` (writes `ContractEmbedding` rows).

**Classification:** pgvector is an **Infrastructure** detail. It must hide behind an **`AbstractVectorStore`** port so **no `pgvector` type, no `cosine_distance`, no `Vector(384)`, and no `select(...)` appears in Domain or Application.** The `ContractEmbedding` ORM model + its `Vector(384)` column stay in Infrastructure and never escape.

**Port shape (conceptual — Agent 3 writes signatures):**
- `upsert_chunks(contract_id, version_id, chunks: list[Chunk])` — store embeddings for a contract version (the impl runs `_embed` via an injected `EmbeddingModel` and writes rows). Called by the embeddings use case.
- `search(query_text | query_vector, *, contract_ids, top_k, scoped_to_authoritative_version) -> list[ScoredChunk]` — top-k semantic search; the impl owns the cosine-distance query and returns plain `(contract_id, text, score)` value objects (the `round(1.0 - distance, 4)` similarity normalization is a Domain/Application concern, computed from the port's raw distance or returned as a normalized score). The recoverable-failure-returns-empty behavior (`retrieval.py:82`) becomes the port impl's contract.
- A companion **`EmbeddingModel`** port: `embed(texts: list[str]) -> list[Vector]` — wraps `_embed`/`EMBEDDING_MODEL`; the 384-dimension fact lives only in the impl.

Application's `AskContractBrainUseCase` / `assemble_context` then depends on `AbstractVectorStore.search(...)` + a `KnowledgeGraphRepository` + a `ClauseRepository` (for `_fulltext_clauses`/`_graph_facts`), composing their value-object results via Domain ranking rules — with zero SQLAlchemy/pgvector imports.

---

## E. Celery tasks (`jobs/tasks.py`)

There is one Celery task, but it carries heavy Application logic. Classification:

| Component | Classification | Action |
|---|---|---|
| `run_ai_job` (`tasks.py:20`, the `@celery_app.task` + `asyncio.run`) | **Interface Adapter** (thin trigger) | Keep as a shim: `def run_ai_job(self, job_id): return asyncio.run(run_ai_job_use_case.execute(job_id))`. The Celery binding (`bind=True, autoretry_for, retry_backoff, max_retries=3`) is driver config. |
| `_run_ai_job` (`tasks.py:25`) | **Application logic to be EXTRACTED** → `RunAiJobUseCase` | This is **not** a thin trigger today. It (a) opens its own `SessionLocal()` (`:26`), (b) owns the job-status lifecycle (`RUNNING` → `SUCCEEDED`/`FAILED`, `attempt_count`, `progress`), (c) **dispatches on `job.job_type`** across 7 branches (metadata/clause/obligation/renewal extraction, embeddings, brain ingestion, tabular cell), (d) commits 11 times at phase boundaries, (e) marks failure + re-raises for Celery retry. All of that is orchestration → `RunAiJobUseCase`, with the session/UoW injected and commits collapsed to UoW boundaries. The `job_type` branch table becomes a strategy/dispatch within the use case (each branch already maps to a skill use case). |
| `_queue_contract_brain_ingestion` (`tasks.py:216`) | **Application** (auto-chaining policy) — EXTRACTED | Agent 1's "auto-chains brain ingestion" lives here. The rule — *"if extraction job SUCCEEDED, build `idempotency_key`, dedupe against existing `JobRun`, create + dispatch a `contract_brain_ingestion` job"* — is an Application orchestration. The **lazy `from app.jobs.service import create_job, dispatch_job` (`:227`) that papers over the `jobs.tasks ↔ jobs.service` circular edge** disappears once dispatch goes through a `JobQueuePort`. |
| `_sync_job_from_skill_runs` (`tasks.py:182`) | **Application** | Reconciles `JobRun` from the latest `AISkillRun`. Move into the use case; `db.commit` (`:205`) → UoW. |
| `_mark_job_succeeded` (`tasks.py:208`) | **Domain** | Pure `JobRun` state transition (status/progress/finished_at/clear errors). Becomes a `JobRun.mark_succeeded()` entity method. |

**Net:** the task file becomes a one-line Adapter shim; everything below it becomes `RunAiJobUseCase` (Application) calling the per-skill use cases and a `JobQueuePort`, with `JobRun`/`JobStatus` transitions as Domain. The "owns its session" and "11 commits" facts both resolve into a single injected UoW.

---

## The single most-violated dependency rule

**`ai/tool_runtime.py` (the `ToolDispatcher`) importing and calling concrete *service functions* of ≥6 sibling domains plus 3 integration SDK singletons, while also owning a DB `Session` and committing.** It is simultaneously an Interface Adapter, an Application orchestrator, and an Infrastructure transaction owner — a god-object that makes every one of the 24 tools un-testable without a live DB and real cross-domain services. Severing it (each tool → one Application use case behind ports) is the highest-leverage single move in the migration. The close runner-up is `ai/controller.py::_persist_skill_output` constructing `Contract`/`ClauseExtraction`/`Obligation`/`RenewalEvent` ORM rows directly — the AI engine writing into 7 domains' tables with no repository indirection.

---

*End of Component → Layer Classification. Classification + diagnosis only; target tree and code are Agents 3 & 4.*
