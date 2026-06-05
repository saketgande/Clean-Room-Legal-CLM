# AEGIS Legal CLM — Target Architecture & Migration Sequence

**Agent 3: The Architect** · **Date:** 2026-06-03
**Scope:** The complete target Clean Architecture for the AEGIS backend and the sequenced, behavior-preserving migration to reach it. Builds on `01_reality.md` (Agent 1) and `02_classification.md` (Agent 2). I design the target tree, the full port contracts, and the 5-sprint plan; Agent 4 writes the production implementation.
**Builds on:** `proposals/clean_arch_2026-06-03/01_reality.md`, `proposals/clean_arch_2026-06-03/02_classification.md`.

## Corrections to prior agents

None. I re-verified the load-bearing facts against source: `transition_contract_stage` signature (`contracts/lifecycle.py:66`), `run_structured_skill` signature (`controller.py:885`), `ClaudeClient.{complete_structured,complete_with_tools,stream_with_tools}` + `ClaudeProviderResponse` (`integrations/claude.py:22,66,141,181`), `ContractAIContext` (`ai/context.py:14`), the pgvector `cosine_distance` read (`contract_brain/retrieval.py:63`) + `_embed` (`ai/embeddings.py:53`), the Celery entry (`jobs/tasks.py:20-37`), `get_db` (`core/deps.py:17`), the ORM mixins (`core/database.py:33-66`), `ToolSpec` (`tool_registry.py:118`), `write_audit_log`/`write_timeline_event` (`core/audit.py:34,135`), `ai_max_tool_iterations = 8` and `MAX_FULL_TEXT_CHARS = 25_000`. Both prior documents match the code. Two facts I pin down that Agents 1-2 left implicit and that the design depends on:

1. **`expire_on_commit=False`** (`core/database.py:85`) is load-bearing: the route returns ORM objects after commit. The target still commits *inside the request* (via the UoW), so this stays unchanged — the UoW does not detach instances mid-request.
2. **The provider already has a `stream_with_tools` async-iterator stub** (`claude.py:181`) that yields `{"event": ..., ...}` dicts. The target `AbstractAIClient.stream_run` formalizes that surface into typed, provider-neutral DTOs; the existing stub is its seed.

---

# Part 1 — Target directory tree (file-level)

The target package is a new top-level `aegis/` beside the existing `backend/app/`. The dependency rule points strictly inward: `infrastructure → adapters → application → domain`. **An import the other direction is structurally impossible** because (a) `domain/` and `application/` contain zero `import sqlalchemy`, `import httpx`, `import fastapi`, `import celery`, `import anthropic`, `import pgvector` statements — enforced by an import-linter contract in CI (`pyproject.toml`, see Sprint 1 done-condition); and (b) `application/ports/` defines only `Protocol`/`ABC` abstractions, so the Application layer names *interfaces*, never concrete adapters. Composition (wiring concretes to ports) happens exclusively in `infrastructure/container.py` and the FastAPI/Celery entrypoints.

**Multi-tenancy enforcement layer:** `org_id` is enforced in **two** layers, defense-in-depth. (1) **Application** use cases receive `org_id` as an explicit parameter (never inferred) and pass it to every repository call — the use case is the policy authority that an operation is org-scoped. (2) **Infrastructure** repositories *physically* apply `WHERE org_id = :org_id` to every read and stamp `org_id` on every write; a repository method that touches an org-scoped aggregate without an `org_id` argument does not exist. The Domain layer holds the *rule* (`accessible_contract_filter` decomposed into a pure predicate) but never the SQL. This mirrors today's `OrgScopedMixin` (`core/database.py:42`) but moves the *guarantee* from "every call site remembers to filter" to "the repository signature makes forgetting impossible."

```
aegis/
├── __init__.py                                  # package marker
│
├── domain/                                      # PURE. No framework/IO imports. Entities, value objects, events, domain services.
│   │
│   ├── __init__.py
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── identifiers.py                        # new_uuid(): pure UUID-string generator (moved from core/database.py:10)
│   │   ├── clock.py                              # utcnow() pure default + Clock protocol re-export point; no tz lib beyond stdlib
│   │   ├── errors.py                             # DomainError base; IllegalStateError, AccessDeniedError, ValidationFailed — NO HTTPException
│   │   ├── events.py                             # DomainEvent base dataclass (event_id, occurred_at, org_id); EventList collector mixin
│   │   └── value_objects.py                      # OrgId, ActorId, ResourceRef NewType/value objects shared across aggregates
│   │
│   ├── contracts/
│   │   ├── __init__.py
│   │   ├── entities.py                           # Contract aggregate w/ transition_to(); ContractStageHistory, ContractParty entities (pure)
│   │   ├── lifecycle.py                          # ALLOWED_TRANSITIONS dict + allowed_transitions_for() (moved from contracts/lifecycle.py:13-63)
│   │   ├── stage_rules.py                         # transition legality + signed-version-required-for-ACTIVE + override rule (pure, raises IllegalStageTransition)
│   │   ├── access.py                             # user_can_access_contract() pure predicate + AccessScope value object (rule half of contracts/access.py)
│   │   ├── value_objects.py                      # LifecycleStage, ContractMetadata, RiskTier value objects
│   │   └── events.py                             # ContractStageTransitioned, ContractMetadataUpdated, ContractArchived
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── skill_definition.py                   # SkillDefinition VO (name, output_model, return_tool_name, prompt_key, citation_required, temperature, max_tokens) — from SkillSpec
│   │   ├── skill_run.py                          # SkillRun aggregate: status transitions RUNNING→{SUCCEEDED,NEEDS_REVIEW,FAILED}; validation_status
│   │   ├── assistant_run.py                      # AssistantRun aggregate: status RUNNING→(TOOL_CALL)*→{SUCCEEDED,WAITING_CONFIRMATION,FAILED}; provider_state holder
│   │   ├── tool_spec.py                          # ToolSpec VO (moved from tool_registry.py:118) + requires_confirmation rule + AssistantToolCategory
│   │   ├── tool_catalog.py                       # the 24 ToolSpec definitions as pure data (moved from the _register(...) calls)
│   │   ├── skill_catalog.py                      # the 13 SkillDefinition instances as pure data (from ai/registry.py SkillSpec list)
│   │   ├── output_validator.py                   # SkillOutputValidator: schema-conformance rule (wraps output_model.model_validate)
│   │   ├── citation_validator.py                 # validate_citations() fuzzy quote-vs-source rule + CitationInput/CitationResult VOs (from ai/citations.py)
│   │   ├── context.py                            # ContractAIContext VO + 25_000-char truncation policy (rule half of ai/context.py)
│   │   ├── fallback.py                           # metadata-skill degraded-output policy (from ai/fallback.py)
│   │   ├── edit_anchoring.py                     # AnchorEdits domain service: locate clause / anchor tracked-change spans (rule behind edit_contract)
│   │   ├── stream_events.py                      # provider-NEUTRAL run-step DTOs: MessageDelta, ToolStarted, ToolFinished, ConfirmationRequired, RunSucceeded, RunFailed
│   │   └── events.py                             # SkillSucceeded, SkillFailed, CitationsValidated, AssistantRunCompleted
│   │
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── job_run.py                            # JobRun aggregate: JobStatus transitions; mark_running/mark_succeeded/mark_failed (from _mark_job_succeeded)
│   │   ├── chaining_policy.py                    # "extraction SUCCEEDED → enqueue brain ingestion" idempotency-key rule (from _queue_contract_brain_ingestion)
│   │   ├── reaping_policy.py                     # 20-min stale-job TTL rule (from jobs/routes.py:_reap_stuck_jobs)
│   │   └── events.py                             # JobSucceeded, JobFailed
│   │
│   ├── approvals/
│   │   ├── __init__.py
│   │   ├── entities.py                           # ApprovalRequest, ApprovalRoutingRule (pure)
│   │   ├── routing.py                            # evaluate_routing() + _matches() pure routing rule (from approvals/service.py)
│   │   └── events.py                             # ApprovalRequested, ApprovalDecided
│   │
│   ├── signatures/
│   │   ├── __init__.py
│   │   ├── entities.py                           # SignatureRequest, SignatureRecipient (pure)
│   │   ├── recipients.py                         # validate_signature_recipients() pure rule (from signatures/service.py)
│   │   └── events.py                             # SignatureSynced, SignedVersionCreated
│   │
│   ├── obligations/
│   │   ├── __init__.py
│   │   ├── entities.py                           # Obligation, ObligationReminder (pure); status transitions open/completed/cancelled
│   │   ├── reminder_window.py                    # "which reminders are due" due-date rule (from obligations/routes.py:run_obligation_reminders)
│   │   └── events.py                             # ObligationsExtracted, ObligationCompleted
│   │
│   ├── renewals/
│   │   ├── __init__.py
│   │   ├── entities.py                           # RenewalEvent (pure)
│   │   ├── renewal_window.py                     # "contract enters RENEWAL_DUE within window" rule (from renewals/routes.py:run_renewal_window_check)
│   │   └── events.py                             # RenewalExtracted, RenewalDecided, RenewalDue
│   │
│   ├── playbooks/
│   │   ├── __init__.py
│   │   ├── entities.py                           # Playbook, PlaybookVersion, PlaybookRule, PlaybookDeviation (pure)
│   │   ├── evaluation.py                          # evaluate_rules_against_text, severity, default-rule gen, phrase anchoring (from playbooks/service.py rule fns)
│   │   ├── redline.py                            # DOCX track-changes construction returning bytes (pure transform; from _build_playbook_redline_docx)
│   │   └── events.py                             # PlaybookRunCompleted, DeviationDecided
│   │
│   ├── tabular/
│   │   ├── __init__.py
│   │   ├── entities.py                           # TabularReview, TabularReviewColumn, TabularReviewCell (pure); TabularCellStatus transitions
│   │   └── events.py                             # TabularReviewCreated
│   │
│   ├── contract_brain/
│   │   ├── __init__.py
│   │   ├── entities.py                           # KnowledgeNode, KnowledgeEdge as pure graph value objects
│   │   ├── ranking.py                            # term-overlap fulltext ranking + graph-fact ranking rules (from retrieval.py _fulltext_clauses/_graph_facts)
│   │   ├── retrieval_types.py                    # ScoredChunk, GraphFact, RetrievalContext VOs (provider-neutral results)
│   │   └── events.py                             # ContractBrainIngested
│   │
│   ├── audit/
│   │   ├── __init__.py
│   │   ├── audit_entry.py                        # AuditEntry VO (action, resource, before/after, actor, request_id) — plain dict-equivalent, NO ORM
│   │   ├── hash_chain.py                         # compute_audit_row_hash() + chain-link invariant (pure SHA-256; from audit.py:93)
│   │   └── timeline.py                           # TimelineEntry VO (from write_timeline_event args)
│   │
│   ├── authz/
│   │   ├── __init__.py
│   │   ├── permissions.py                        # has_permission(), permission_values derivation (pure; from core/rbac.py)
│   │   └── token_policy.py                       # token TTL / revocation-window rule (policy half of core/security.py)
│   │
│   └── events/
│       ├── __init__.py
│       └── registry.py                           # canonical event-name → DomainEvent class map (for outbox serialization later)
│
├── application/                                  # Use cases + abstract ports + event handlers + DTOs. Depends ONLY on domain/ and own ports/.
│   │
│   ├── __init__.py
│   │
│   ├── ports/                                     # The contracts Infrastructure implements. Protocol/ABC only. (FULL signatures in Part 2.)
│   │   ├── __init__.py
│   │   ├── unit_of_work.py                        # AbstractUnitOfWork — context manager exposing repos + commit/rollback + event collection
│   │   ├── ai_client.py                           # AbstractAIClient — complete_structured + stream_run (async iterator) + resume
│   │   ├── event_bus.py                           # AbstractEventBus — register/publish; in-process now, outbox-swappable
│   │   ├── vector_store.py                        # AbstractVectorStore (upsert_chunks/search) + EmbeddingModel
│   │   ├── tool_dispatcher.py                     # AbstractToolDispatcher — execute(tool_name, input, ctx) → ToolResult (the port the state machine awaits)
│   │   ├── job_queue.py                           # JobQueuePort — enqueue(job_id)
│   │   ├── esignature.py                          # ESignaturePort — create/void/sync envelope
│   │   ├── blob_store.py                          # BlobStorePort — put/get/delete bytes
│   │   ├── ocr.py                                 # OcrPort + TextExtractorPort — extract text from uploaded bytes
│   │   ├── email.py                               # EmailPort — send()
│   │   ├── feature_flags.py                       # FeatureFlagPort — is_enabled(flag, org_id)
│   │   ├── prompt_versions.py                     # PromptVersionPort — active prompt bundle for (org, key)
│   │   ├── clock.py                               # ClockPort — now()
│   │   └── repositories.py                        # ALL Abstract*Repository protocols (one per aggregate); imported by use cases & UoW
│   │
│   ├── dto/
│   │   ├── __init__.py
│   │   ├── contracts.py                           # ContractView, StageTransitionResult, ContractHubSummary (input/output DTOs; no ORM)
│   │   ├── ai.py                                  # SkillRunResult, AssistantRunView, StructuredSkillInput
│   │   ├── jobs.py                                # JobView, RunJobInput
│   │   ├── approvals.py / signatures.py / obligations.py / renewals.py / playbooks.py / tabular.py / brain.py
│   │   │                                          #   (one DTO module per domain; request/response shapes the use cases speak)
│   │   └── common.py                              # PageRequest, ActorContext (org_id, actor_user_id, request_id, permission_values)
│   │
│   ├── use_cases/
│   │   ├── __init__.py
│   │   ├── contracts/
│   │   │   ├── __init__.py
│   │   │   ├── transition_contract_stage.py       # TransitionContractStageUseCase  [SPRINT 1 SLICE]
│   │   │   ├── get_contract.py                     # GetContractForUserUseCase (from contracts/service.py:get_contract_for_user)
│   │   │   ├── list_contracts.py                   # ListContractsForUserUseCase
│   │   │   ├── update_metadata.py                  # UpdateContractMetadataUseCase
│   │   │   ├── contract_hub_summary.py             # ContractHubSummaryUseCase (read model)
│   │   │   └── archive_contract.py                 # ArchiveContractUseCase
│   │   ├── ai/
│   │   │   ├── __init__.py
│   │   │   ├── run_structured_skill.py            # RunStructuredSkillUseCase  [SPRINT 1 SLICE] (from controller.py:885)
│   │   │   ├── assistant_run_state_machine.py     # AssistantRunStateMachine (stream + resume; generator over domain stream_events)  [SPRINT 3]
│   │   │   ├── persist_skill_output_policy.py     # PersistSkillOutputPolicy (the if-spec.name dispatch → repo writes)  [SPRINT 3]
│   │   │   ├── resolve_contract_context.py        # ResolveContractContextUseCase (returns ContractAIContext via repo)
│   │   │   ├── build_assistant_tool_schemas.py    # BuildAssistantToolSchemasPolicy (permission+flag-gated tool list)
│   │   │   ├── feature_flag_gate.py               # FeatureFlagGate (skill/tool enabled-for-org guard)
│   │   │   └── ask_contract_brain.py              # AskContractBrainUseCase (vector + fulltext + graph compose)
│   │   ├── jobs/
│   │   │   ├── __init__.py
│   │   │   ├── run_ai_job.py                      # RunAiJobUseCase (job-type dispatch; from _run_ai_job)  [SPRINT 5]
│   │   │   ├── chain_brain_ingestion.py           # ChainBrainIngestionUseCase (from _queue_contract_brain_ingestion)  [SPRINT 5]
│   │   │   ├── sync_job_from_skill_runs.py        # SyncJobFromSkillRunsUseCase (from _sync_job_from_skill_runs)
│   │   │   ├── dispatch_job.py                    # DispatchJobUseCase (create + enqueue decision; wraps JobQueuePort)
│   │   │   └── reap_stuck_jobs.py                 # ReapStuckJobsUseCase
│   │   ├── approvals/
│   │   │   ├── __init__.py
│   │   │   ├── submit_for_approval.py             # SubmitForApprovalUseCase
│   │   │   └── decide_approval.py                 # DecideApprovalUseCase (in-app + token-redeem)
│   │   ├── signatures/
│   │   │   ├── __init__.py
│   │   │   ├── send_for_signature.py              # SendForSignatureUseCase (ESignaturePort)
│   │   │   ├── sync_signature_request.py          # SyncSignatureRequestUseCase
│   │   │   └── create_signed_version.py           # CreateSignedVersionUseCase
│   │   ├── contract_files/
│   │   │   ├── __init__.py
│   │   │   ├── create_contract_from_upload.py     # CreateContractFromUploadUseCase (saga w/ blob compensation)
│   │   │   ├── extract_text.py                    # ExtractContractTextUseCase (OcrPort + threshold rule)
│   │   │   └── queue_intake_jobs.py               # QueueIntakeAiJobsUseCase (unifies job-queueing dup'd across intake/tool/tabular)
│   │   ├── obligations/
│   │   │   ├── __init__.py
│   │   │   ├── update_obligation.py               # UpdateObligationUseCase
│   │   │   ├── complete_obligation.py             # CompleteObligationUseCase
│   │   │   ├── queue_extraction.py                # QueueObligationExtractionUseCase
│   │   │   └── run_reminders.py                   # RunObligationRemindersUseCase (EmailPort + window rule)
│   │   ├── renewals/
│   │   │   ├── __init__.py
│   │   │   ├── decide_renewal.py                  # DecideRenewalUseCase
│   │   │   └── run_window_check.py                # RunRenewalWindowCheckUseCase
│   │   ├── playbooks/
│   │   │   ├── __init__.py
│   │   │   ├── execute_playbook_run.py            # ExecutePlaybookRunUseCase
│   │   │   ├── record_deviation_decision.py       # RecordDeviationDecisionUseCase
│   │   │   └── manage_playbook_versions.py        # Create/Clone/SelectVersion use cases
│   │   ├── tabular/
│   │   │   ├── __init__.py
│   │   │   ├── create_tabular_review.py           # CreateTabularReviewUseCase
│   │   │   ├── dispatch_cells.py                  # DispatchTabularCellsUseCase (JobQueuePort)
│   │   │   └── build_table_context.py             # BuildTableContextUseCase (read)
│   │   ├── contract_brain/
│   │   │   ├── __init__.py
│   │   │   ├── ingest_contract_brain.py           # IngestContractBrainUseCase (graph rebuild)
│   │   │   └── generate_embeddings.py             # GenerateEmbeddingsUseCase (EmbeddingModel + VectorStore)
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── login.py / register.py / refresh_tokens.py / revoke_token.py
│   │   │   ├── invitations.py / join_requests.py / approvals.py
│   │   │   ├── api_keys.py / password_reset.py / bootstrap.py
│   │   │   └── token_queries.py                   # is_access_token_revoked etc. (read use cases)
│   │   ├── organizations/
│   │   │   ├── __init__.py
│   │   │   └── manage_organization.py
│   │   ├── admin/
│   │   │   ├── __init__.py
│   │   │   └── toggle_feature_flag.py             # ToggleFeatureFlagUseCase (writes via FeatureFlagPort)
│   │   └── workflows/
│   │       ├── __init__.py
│   │       ├── manage_workflows.py                # List/Create workflow use cases
│   │       └── run_workflow.py                    # RunWorkflowUseCase
│   │
│   └── event_handlers/
│       ├── __init__.py
│       ├── audit_handler.py                       # AuditLogRequested → AuditLogWriterPort.append (kills audit.py autonomous session)
│       ├── timeline_handler.py                    # ResourceTimelineEventRequested → TimelineRepository.add
│       ├── stage_history_handler.py               # ContractStageTransitioned → ContractStageHistory repo write + audit + timeline cascade
│       ├── email_handler.py                       # ApprovalRequested/RenewalDue → EmailPort.send
│       ├── lifecycle_cascade_handler.py           # SignatureSynced/RenewalDue → TransitionContractStageUseCase
│       ├── brain_chain_handler.py                 # JobSucceeded(extraction) → ChainBrainIngestionUseCase
│       └── registrations.py                       # wires every event class → its handler(s) into the AbstractEventBus
│
├── adapters/                                      # Driving + driven adapters that still speak the framework. Depend on application/ + domain/.
│   │
│   ├── __init__.py
│   │
│   ├── api/                                        # FastAPI routers (HTTP I/O only) + presenters (DTO ↔ wire). ZERO db.commit, ZERO ORM in responses.
│   │   ├── __init__.py
│   │   ├── deps.py                                 # get_uow(), get_current_actor(), require_permission() — DI wiring (replaces core/deps.py)
│   │   ├── contracts.py                            # contract routers incl. POST /{id}/lifecycle  [SPRINT 1 SLICE: refactored route]
│   │   ├── ai.py                                   # rerun-skill endpoints (from ai/routes.py)
│   │   ├── assistant.py                            # SSE stream/resume endpoints (framing only; drives AssistantRunStateMachine)
│   │   ├── approvals.py / signatures.py / contract_files.py / obligations.py / renewals.py
│   │   ├── playbooks.py / tabular_review.py / contract_brain.py / workflows.py / projects.py
│   │   ├── auth.py / organizations.py / admin.py / jobs.py
│   │   └── presenters/
│   │       ├── __init__.py
│   │       ├── contract_presenter.py               # ContractView/StageTransitionResult → JSON body
│   │       ├── assistant_sse_presenter.py          # domain stream_events → SSE event:/data: frames (AssistantSSEPresenter)
│   │       ├── auth_presenter.py                    # _token_response/_user_response/_public_* (from auth/service.py)
│   │       └── tabular_xlsx_presenter.py            # build_xlsx shaping (from tabular_review/service.py)
│   │
│   ├── ai_tools/                                    # The ToolDispatcher: tool_name → ONE use case. Validates input, gates, persists call record.
│   │   ├── __init__.py
│   │   ├── tool_dispatcher.py                       # ToolDispatcher (impl of AbstractToolDispatcher) — from ToolRuntime  [SPRINT 4]
│   │   ├── tool_use_translator.py                   # ToolUseTranslator: provider blocks ↔ internal dict; _model_safe_result/_json_tool_result
│   │   ├── confirmation_gate.py                     # branch on ToolSpec.confirmation_policy; persist AIConfirmation via repo
│   │   └── handlers/
│   │       ├── __init__.py
│   │       ├── read_only_handlers.py                # read_contract, find_in_contract, list_project_contracts, get_contract_status,
│   │       │                                        #   list_workflows, list_playbooks, read_table_cells, my_attention_items,
│   │       │                                        #   find_contracts, list_obligations → query use cases
│   │       ├── brain_handlers.py                    # ask_contract_brain → AskContractBrainUseCase
│   │       ├── draft_handlers.py                    # generate_contract_docx, edit_contract, replicate_contract_version,
│   │       │                                        #   run_playbook_review, redline_against_playbook → propose/draft use cases
│   │       ├── mutating_handlers.py                 # run_workflow, submit_for_approval, extract_obligations, create_tabular_review
│   │       ├── external_handlers.py                 # send_for_signature, external_share (confirmation=required)
│   │       └── destructive_handlers.py              # archive_contract (confirmation=required)
│   │
│   └── jobs/
│       ├── __init__.py
│       └── celery_tasks.py                          # run_ai_job @task shim: asyncio.run(run_ai_job_use_case.execute(job_id))  [SPRINT 5]
│
└── infrastructure/                                # Outermost. Concrete drivers. Implements every port. May import everything inward.
    │
    ├── __init__.py
    ├── container.py                                # Composition root: builds concrete adapters, wires ports, registers event handlers
    ├── config.py                                   # Settings (pydantic-settings); reads env incl. ai_max_tool_iterations, mock_* (from core/config.py)
    │
    ├── postgres/
    │   ├── __init__.py
    │   ├── engine.py                                # create_engine + SessionLocal (expire_on_commit=False) (from core/database.py:72-86)
    │   ├── base.py                                  # DeclarativeBase + NAMING_CONVENTION + all mixins (IdMixin/Timestamp/OrgScoped/ActorTracked/SoftDelete/TableName)
    │   ├── unit_of_work.py                          # PostgresUnitOfWork (impl of AbstractUnitOfWork): owns Session, repos, commit→dispatch events  [SPRINT 1 SLICE]
    │   ├── models/                                  # ALL SQLAlchemy ORM models (one module per current domain models.py)
    │   │   ├── __init__.py                          # imports every model module so metadata is fully registered (replaces app/models.py)
    │   │   ├── contracts.py                         # Contract, ContractStageHistory, ContractParty
    │   │   ├── contract_files.py                    # ContractVersion, ContractTextSnapshot, StorageObject, ContractEmbedding (Vector(384) lives HERE)
    │   │   ├── ai.py                                # AISkillRun, AICitation, AICallLog, UsageRecord
    │   │   ├── assistant.py                         # AssistantRun, AssistantToolCall, AISession, AssistantContractHandle, AIConfirmation
    │   │   ├── jobs.py                              # JobRun
    │   │   ├── approvals.py                         # ApprovalRequest, ApprovalRoutingRule
    │   │   ├── signatures.py                        # SignatureRequest, recipients
    │   │   ├── obligations.py                       # Obligation, ObligationReminder
    │   │   ├── renewals.py                          # RenewalEvent
    │   │   ├── playbooks.py                         # Playbook, PlaybookVersion, PlaybookRule, PlaybookDeviation
    │   │   ├── tabular.py                           # TabularReview, TabularReviewColumn, TabularReviewCell
    │   │   ├── contract_brain.py                    # KnowledgeNode, KnowledgeEdge
    │   │   ├── auth.py                              # User, Role, RefreshToken, ApiKey, UserInvitation, OrgJoinRequest
    │   │   ├── projects.py                          # Project, ProjectContract, share models
    │   │   ├── prompts.py                           # PromptVersion (DB-backed prompt bundles)
    │   │   ├── workflows.py                         # Workflow
    │   │   ├── organizations.py                     # Organization, AdminSetting
    │   │   └── audit.py                             # AuditLog, ResourceTimelineEvent
    │   ├── repositories/                            # SQLAlchemy impls. EVERY org-scoped method takes org_id and applies WHERE org_id.
    │   │   ├── __init__.py
    │   │   ├── _mapping.py                          # ORM-row ↔ domain-entity mappers (Contract row ↔ Contract aggregate, etc.)
    │   │   ├── contract_repository.py              # SQLAlchemyContractRepository  [SPRINT 1 SLICE]
    │   │   ├── contract_version_repository.py
    │   │   ├── stage_history_repository.py         # ContractStageHistory writes  [SPRINT 1 SLICE]
    │   │   ├── skill_run_repository.py / citation_repository.py / ai_call_log_repository.py / usage_repository.py  [skill_run SPRINT 1 SLICE]
    │   │   ├── assistant_run_repository.py / confirmation_repository.py
    │   │   ├── job_repository.py
    │   │   ├── approval_repository.py / signature_repository.py / obligation_repository.py / renewal_repository.py
    │   │   ├── playbook_repository.py / tabular_repository.py
    │   │   ├── knowledge_graph_repository.py / clause_repository.py
    │   │   ├── auth_repository.py / project_repository.py / workflow_repository.py / organization_repository.py
    │   │   ├── prompt_version_repository.py        # impl of PromptVersionPort
    │   │   ├── feature_flag_repository.py          # impl of FeatureFlagPort (AdminSetting reads)
    │   │   ├── audit_repository.py                 # AuditLogWriter: hash-chain append + pg_advisory_xact_lock (from audit.py)  [SPRINT 2]
    │   │   └── timeline_repository.py              # ResourceTimelineEvent add (caller-session)  [SPRINT 1 SLICE for AuditLogRequested]
    │   └── access_filters.py                       # accessible_contract_filter SQLAlchemy clause (SQL half of contracts/access.py)
    │
    ├── anthropic/
    │   ├── __init__.py
    │   ├── client.py                               # AnthropicHTTPClient (impl of AbstractAIClient): complete_structured + stream_run + resume  [SPRINT 1 SLICE]
    │   ├── transport.py                            # long-lived httpx.AsyncClient + tenacity retry + aclose (from claude.py _client/_post_messages)
    │   ├── provider_dto.py                         # ClaudeProviderResponse (internal, infra-only)
    │   ├── event_mapper.py                         # Claude content/tool_use blocks → domain stream_events DTOs
    │   └── mock.py                                 # mock provider (from _claude_mock.py): canned structured + tool responses
    │
    ├── vector/
    │   ├── __init__.py
    │   ├── pgvector_store.py                        # PgVectorStore (impl of AbstractVectorStore): cosine_distance query, upsert (from retrieval/embeddings)
    │   └── fastembed_model.py                       # FastEmbedModel (impl of EmbeddingModel): _embed + BAAI/bge-small-en-v1.5, 384 dims (from embeddings.py)
    │
    ├── docusign/
    │   ├── __init__.py
    │   └── client.py                               # DocuSignESignatureClient (impl of ESignaturePort): JWT, envelope, verify_connect_signature (from docusign.py)
    │
    ├── reducto/
    │   ├── __init__.py
    │   └── client.py                               # ReductoOcrClient (impl of OcrPort) (from reducto.py)
    │
    ├── resend/
    │   ├── __init__.py
    │   └── client.py                               # ResendEmailClient (impl of EmailPort) (from resend.py)
    │
    ├── storage/
    │   ├── __init__.py
    │   └── blob_store.py                            # BlobStore (impl of BlobStorePort) (from storage.py)
    │
    ├── redis/
    │   ├── __init__.py
    │   ├── celery_app.py                            # celery_app config (from jobs/celery_app.py)
    │   └── job_queue.py                             # CeleryJobQueue (impl of JobQueuePort): run_ai_job.delay(job_id) (from dispatch_job)
    │
    ├── events/
    │   ├── __init__.py
    │   ├── in_process_bus.py                        # InProcessEventBus (impl of AbstractEventBus): sync dispatch after UoW commit  [SPRINT 2]
    │   └── outbox.py                                # OutboxEventBus (future: write to outbox table same-tx, separate dispatcher) — stub interface only
    │
    ├── clock.py                                     # SystemClock (impl of ClockPort): utcnow()
    │
    └── web/
        ├── __init__.py
        ├── main.py                                 # create_app(): mounts adapters/api routers, lifespan opens/closes container + httpx client
        └── middleware.py                           # SlowAPI / RequestContext / CORS / TrustedHost (from main.py)
```

**Where the load-bearing pieces land (explicit map):**

- **The 24 tools:** *contract* (`ToolSpec`) → `domain/ai/tool_spec.py` + `domain/ai/tool_catalog.py`. *dispatch* → `adapters/ai_tools/tool_dispatcher.py` + `adapters/ai_tools/handlers/*`. *work* → the Application use case each handler calls. Each tool method ends as a validate-then-call-one-use-case shim with zero DB/HTTP.
- **The 13 skills:** *definition* → `domain/ai/skill_definition.py` + `domain/ai/skill_catalog.py`. *execution* → `application/use_cases/ai/run_structured_skill.py` (one use case, parameterized by `SkillDefinition`).
- **Lifecycle state machine:** rule → `domain/contracts/lifecycle.py` + `domain/contracts/stage_rules.py` + `Contract.transition_to()` in `domain/contracts/entities.py`. Orchestration → `application/use_cases/contracts/transition_contract_stage.py`.
- **AI use cases:** `application/use_cases/ai/` (`run_structured_skill`, `assistant_run_state_machine`, `persist_skill_output_policy`, `resolve_contract_context`, `build_assistant_tool_schemas`, `feature_flag_gate`, `ask_contract_brain`).
- **Repositories (one per aggregate):** `infrastructure/postgres/repositories/*`.
- **ORM models:** `infrastructure/postgres/models/*` — and **only** there. `Vector(384)` lives solely in `infrastructure/postgres/models/contract_files.py`.

---

# Part 2 — Port interface definitions (full signatures)

These are the final contracts Agent 4 implements. All live under `aegis/application/ports/`. Every file opens with `from __future__ import annotations` so forward references in domain-DTO type hints never require importing concrete types.

### `aegis/application/ports/clock.py`

```python
from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ClockPort(Protocol):
    """Provides the current UTC time; injected so use cases and aggregates are deterministically testable."""

    def now(self) -> datetime: ...
```

### `aegis/application/ports/repositories.py`

One repository per aggregate. Every method on an org-scoped aggregate **requires** `org_id` and the repository physically applies `WHERE org_id = :org_id`. Repositories return/accept **domain entities or DTOs**, never ORM rows. Reads that today rode on a pgvector query are split out: pure-domain reads live here; vector search lives behind `AbstractVectorStore` (below).

```python
from __future__ import annotations

from typing import Protocol, Sequence

from aegis.domain.contracts.entities import Contract, ContractStageHistory
from aegis.domain.ai.skill_run import SkillRun
from aegis.domain.ai.assistant_run import AssistantRun
from aegis.domain.jobs.job_run import JobRun
from aegis.domain.obligations.entities import Obligation, ObligationReminder
from aegis.domain.renewals.entities import RenewalEvent
from aegis.domain.approvals.entities import ApprovalRequest
from aegis.domain.audit.audit_entry import AuditEntry
from aegis.domain.audit.timeline import TimelineEntry
from aegis.domain.contract_brain.entities import KnowledgeNode, KnowledgeEdge


class AbstractContractRepository(Protocol):
    """Persistence boundary for the Contract aggregate; every method is org-scoped and pgvector-free."""

    def get(self, *, org_id: str, contract_id: str) -> Contract | None: ...
    def get_for_actor(
        self, *, org_id: str, contract_id: str, actor_user_id: str, permission_values: Sequence[str]
    ) -> Contract | None: ...
    def list_for_actor(
        self, *, org_id: str, actor_user_id: str, permission_values: Sequence[str], limit: int, offset: int
    ) -> list[Contract]: ...
    def add(self, contract: Contract) -> None: ...
    def update(self, contract: Contract) -> None: ...
    def has_signed_version(self, *, org_id: str, contract_id: str) -> bool: ...
    def list_stage_history(self, *, org_id: str, contract_id: str) -> list[ContractStageHistory]: ...


class AbstractStageHistoryRepository(Protocol):
    """Appends ContractStageHistory rows for a stage transition; org-scoped."""

    def add(self, history: ContractStageHistory) -> None: ...


class AbstractSkillRunRepository(Protocol):
    """Persistence for the SkillRun aggregate (status + validation + output payload); org-scoped."""

    def add(self, run: SkillRun) -> None: ...
    def update(self, run: SkillRun) -> None: ...
    def get(self, *, org_id: str, skill_run_id: str) -> SkillRun | None: ...
    def list_for_org(self, *, org_id: str, limit: int, offset: int) -> list[SkillRun]: ...


class AbstractAssistantRunRepository(Protocol):
    """Persistence for the AssistantRun aggregate, including the opaque provider_state for resume; org-scoped."""

    def add(self, run: AssistantRun) -> None: ...
    def update(self, run: AssistantRun) -> None: ...
    def get(self, *, org_id: str, assistant_run_id: str) -> AssistantRun | None: ...


class AbstractCitationRepository(Protocol):
    """Persists validated AICitation rows produced by the citation validator; org-scoped."""

    def add_all(self, *, org_id: str, skill_run_id: str, citations: Sequence[object]) -> None: ...


class AbstractAICallLogRepository(Protocol):
    """Persists AICallLog observability rows; org-scoped."""

    def add(self, *, org_id: str, call_log: object) -> None: ...


class AbstractUsageRepository(Protocol):
    """Persists UsageRecord token/cost rows; org-scoped."""

    def add(self, *, org_id: str, usage: object) -> None: ...


class AbstractJobRepository(Protocol):
    """Persistence for the JobRun aggregate and idempotency-key dedupe; org-scoped."""

    def get(self, *, org_id: str, job_id: str) -> JobRun | None: ...
    def add(self, job: JobRun) -> None: ...
    def update(self, job: JobRun) -> None: ...
    def exists_with_idempotency_key(self, *, org_id: str, idempotency_key: str) -> bool: ...
    def list_stuck(self, *, ttl_seconds: int) -> list[JobRun]: ...
    def latest_skill_run_for_job(self, *, org_id: str, job_id: str) -> SkillRun | None: ...


class AbstractObligationRepository(Protocol):
    """Persistence for Obligation + ObligationReminder; org-scoped."""

    def add_all(self, *, org_id: str, obligations: Sequence[Obligation]) -> None: ...
    def get(self, *, org_id: str, obligation_id: str) -> Obligation | None: ...
    def update(self, obligation: Obligation) -> None: ...
    def list_due_reminders(self, *, org_id: str, as_of, window_days: int) -> list[ObligationReminder]: ...


class AbstractRenewalRepository(Protocol):
    """Persistence for RenewalEvent; org-scoped."""

    def add_all(self, *, org_id: str, events: Sequence[RenewalEvent]) -> None: ...
    def get(self, *, org_id: str, renewal_id: str) -> RenewalEvent | None: ...
    def update(self, event: RenewalEvent) -> None: ...


class AbstractApprovalRepository(Protocol):
    """Persistence for ApprovalRequest + routing-rule reads; org-scoped."""

    def add(self, request: ApprovalRequest) -> None: ...
    def get(self, *, org_id: str, approval_id: str) -> ApprovalRequest | None: ...
    def update(self, request: ApprovalRequest) -> None: ...
    def routing_rules(self, *, org_id: str) -> list[object]: ...


class AbstractKnowledgeGraphRepository(Protocol):
    """Persistence for the contract-brain knowledge graph; org-scoped."""

    def mark_stale(self, *, org_id: str, contract_id: str) -> None: ...
    def add_nodes(self, *, org_id: str, nodes: Sequence[KnowledgeNode]) -> None: ...
    def add_edges(self, *, org_id: str, edges: Sequence[KnowledgeEdge]) -> None: ...
    def graph_facts(self, *, org_id: str, contract_ids: Sequence[str], limit: int) -> list[object]: ...


class AbstractClauseRepository(Protocol):
    """Reads ClauseExtraction rows for fulltext retrieval ranking; org-scoped, pgvector-free."""

    def candidate_clauses(self, *, org_id: str, contract_ids: Sequence[str], pool_size: int) -> list[object]: ...
    def add_all(self, *, org_id: str, clauses: Sequence[object]) -> None: ...


class AbstractAuditLogRepository(Protocol):
    """Appends a hash-chained audit row; the impl owns the advisory-lock + prev_hash linkage, NOT a separate session."""

    def append(self, entry: AuditEntry) -> None: ...


class AbstractTimelineRepository(Protocol):
    """Adds a ResourceTimelineEvent on the active UoW session; org-scoped."""

    def add(self, entry: TimelineEntry) -> None: ...


# Additional aggregate repositories (signatures, playbooks, tabular, auth, projects,
# workflows, organizations, prompts) follow the identical shape: every org-scoped
# method takes org_id and applies it; accept/return domain entities, never ORM rows.
```

### `aegis/application/ports/unit_of_work.py`

The UoW is the single transactional owner. It exposes the repositories, owns `commit`/`rollback`, and is the place where domain events collected during the transaction are dispatched **after** a successful commit. The 135 scattered `db.commit()` calls collapse into one `uow.commit()` per use case.

```python
from __future__ import annotations

from types import TracebackType
from typing import Protocol

from aegis.application.ports.event_bus import AbstractEventBus
from aegis.application.ports.repositories import (
    AbstractContractRepository,
    AbstractStageHistoryRepository,
    AbstractSkillRunRepository,
    AbstractAssistantRunRepository,
    AbstractCitationRepository,
    AbstractAICallLogRepository,
    AbstractUsageRepository,
    AbstractJobRepository,
    AbstractObligationRepository,
    AbstractRenewalRepository,
    AbstractApprovalRepository,
    AbstractKnowledgeGraphRepository,
    AbstractClauseRepository,
    AbstractAuditLogRepository,
    AbstractTimelineRepository,
)
from aegis.domain.shared.events import DomainEvent


class AbstractUnitOfWork(Protocol):
    """One transactional boundary exposing all repositories; collects domain events and dispatches them on commit."""

    # Repository handles, valid only inside the context-manager block.
    contracts: AbstractContractRepository
    stage_history: AbstractStageHistoryRepository
    skill_runs: AbstractSkillRunRepository
    assistant_runs: AbstractAssistantRunRepository
    citations: AbstractCitationRepository
    ai_call_logs: AbstractAICallLogRepository
    usage: AbstractUsageRepository
    jobs: AbstractJobRepository
    obligations: AbstractObligationRepository
    renewals: AbstractRenewalRepository
    approvals: AbstractApprovalRepository
    knowledge_graph: AbstractKnowledgeGraphRepository
    clauses: AbstractClauseRepository
    audit: AbstractAuditLogRepository
    timeline: AbstractTimelineRepository

    def __enter__(self) -> "AbstractUnitOfWork": ...
    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> bool | None: ...

    def collect_event(self, event: DomainEvent) -> None:
        """Buffer a domain event raised by an aggregate; dispatched after commit() succeeds."""
        ...

    def commit(self) -> None:
        """Flush + commit the transaction, then publish all collected events via the event bus."""
        ...

    def rollback(self) -> None:
        """Roll back the transaction and discard all collected events (no event fires for rolled-back work)."""
        ...
```

> **Async note.** `RunStructuredSkillUseCase` and the assistant state machine are `async` (they await the AI client). The concrete `PostgresUnitOfWork` wraps a synchronous SQLAlchemy `Session`; the await points are the AI/port calls, not the DB calls, exactly as today (`controller.py` is `async` over a sync `Session`). The UoW context-manager is therefore synchronous (`with uow:`) and the use case awaits ports *between* repo touches — identical to the current control flow, so behavior is preserved.

### `aegis/application/ports/event_bus.py`

```python
from __future__ import annotations

from typing import Callable, Protocol, Sequence

from aegis.domain.shared.events import DomainEvent

EventHandler = Callable[[DomainEvent], None]


class AbstractEventBus(Protocol):
    """Registers handlers per event type and publishes events; in-process synchronous now, outbox-swappable later."""

    def register(self, event_type: type[DomainEvent], handler: EventHandler) -> None: ...
    def publish(self, events: Sequence[DomainEvent]) -> None: ...
```

### `aegis/application/ports/ai_client.py`

The single most important port. It covers **structured** completion, **streaming** tool-use, and **resume** — all in provider-neutral domain DTOs, so `AssistantRunStateMachine` drives the 8-iteration loop and the confirmation pause **without importing httpx or SSE**. The streaming method is an async iterator yielding `domain.ai.stream_events` DTOs; the state machine consumes them and decides when to pause/finish. Resume is modeled as a *continuation*: the state machine hands back the opaque `provider_state` it persisted, and `stream_run` resumes from it.

```python
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, Sequence

from aegis.domain.ai.stream_events import RunStreamEvent  # MessageDelta|ToolStarted|ToolFinished|ConfirmationRequired|RunSucceeded|RunFailed
from aegis.domain.ai.skill_definition import SkillDefinition


class StructuredCompletion(Protocol):
    """A provider-neutral structured-skill result: the validated-shape dict plus token/latency telemetry."""

    output: dict[str, Any]
    stop_reason: str | None
    token_usage: dict[str, int | None]
    latency_ms: float
    provider_request_id: str | None
    model: str


class ToolInvocation(Protocol):
    """A provider-neutral request from the model to run a tool: the tool name, its input dict, and the provider's call id."""

    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]


class ToolOutcome(Protocol):
    """A provider-neutral tool result fed back into the run: the originating tool_use_id and a JSON-able payload."""

    tool_use_id: str
    payload: dict[str, Any]
    is_error: bool


class AbstractAIClient(Protocol):
    """Provider-neutral Claude boundary: structured completion + streaming tool-use loop + resume. No httpx/SSE leaks."""

    async def complete_structured(
        self,
        *,
        skill: SkillDefinition,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> StructuredCompletion:
        """Run a single structured-skill call (tool_choice forced to skill.return_tool_name) and return the typed result."""
        ...

    def stream_run(
        self,
        *,
        system_prompt: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[RunStreamEvent]:
        """Stream one assistant turn as provider-neutral events; yields ToolInvocation-bearing events for the caller to satisfy."""
        ...

    def resume_run(
        self,
        *,
        provider_state: dict[str, Any],
        tool_outcomes: Sequence[ToolOutcome],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[RunStreamEvent]:
        """Resume a paused run from persisted provider_state, feeding back confirmed tool outcomes; yields the next turn's events."""
        ...
```

> **How the state machine stays transport-free.** `AssistantRunStateMachine.run(...)` is itself an `async` generator over `RunStreamEvent`. Per iteration it: (1) `async for event in ai_client.stream_run(...)`; (2) on a `ToolInvocation`-style event, awaits `tool_dispatcher.execute(...)` (another port) and accumulates a `ToolOutcome`; (3) re-enters via `ai_client.resume_run(provider_state, outcomes)`; (4) bounds the loop by `max_iterations: int` (the injected `settings.ai_max_tool_iterations = 8`). On a `ConfirmationRequired` event it persists `AssistantRun.provider_state` via the repository, yields `ConfirmationRequired`, and **returns** — no SSE, no commit logic of its own (the route's UoW commits). The route's `AssistantSSEPresenter` is the *only* thing that turns these domain events into `event:`/`data:` frames. This is exactly the decomposition Agent 2 §A.2 specified.

### `aegis/application/ports/tool_dispatcher.py`

```python
from __future__ import annotations

from typing import Any, Protocol

from aegis.application.dto.common import ActorContext


class ToolResult(Protocol):
    """Provider-neutral result of executing one tool: a JSON-able payload and an error flag."""

    payload: dict[str, Any]
    is_error: bool
    requires_confirmation: bool


class AbstractToolDispatcher(Protocol):
    """Routes a tool_name + input to exactly one application use case; the state machine awaits this, never a service."""

    async def execute(
        self, *, tool_name: str, tool_input: dict[str, Any], actor: ActorContext, session_id: str, assistant_run_id: str
    ) -> ToolResult: ...
```

### `aegis/application/ports/vector_store.py`

No SQLAlchemy / pgvector / `Vector(384)` types in any signature. `search` returns plain `ScoredChunk` value objects; the cosine query and the 384-dim fact live only in the impl.

```python
from __future__ import annotations

from typing import Protocol, Sequence

from aegis.domain.contract_brain.retrieval_types import ScoredChunk


class TextChunk(Protocol):
    """A unit of contract text to embed and store: its index, character span, and text."""

    chunk_index: int
    start_char: int
    end_char: int
    text: str


class EmbeddingModel(Protocol):
    """Turns texts into dense vectors; the embedding model name and dimensionality live only in the impl."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class AbstractVectorStore(Protocol):
    """Stores and semantically searches contract-text chunks; hides pgvector entirely behind plain value objects."""

    def upsert_chunks(
        self, *, org_id: str, contract_id: str, contract_version_id: str, text_snapshot_id: str, chunks: Sequence[TextChunk]
    ) -> None:
        """Embed and (re)store chunks for one contract version, replacing any prior chunks for that snapshot."""
        ...

    def search(
        self, *, org_id: str, query_text: str, contract_ids: Sequence[str], top_k: int, authoritative_version_only: bool = True
    ) -> list[ScoredChunk]:
        """Return the top-k most similar chunks as (contract_id, text, score) value objects; empty list on recoverable failure."""
        ...
```

### `aegis/application/ports/job_queue.py`

```python
from __future__ import annotations

from typing import Protocol


class JobQueuePort(Protocol):
    """Hands a persisted JobRun id to the async worker; the only enqueue entrypoint (replaces run_ai_job.delay)."""

    def enqueue(self, *, job_id: str) -> None: ...
```

### `aegis/application/ports/esignature.py`

```python
from __future__ import annotations

from typing import Any, Protocol, Sequence


class EnvelopeStatus(Protocol):
    """Provider-neutral e-signature envelope status: the provider id and a normalized status string."""

    envelope_id: str
    status: str
    recipient_statuses: list[dict[str, Any]]


class ESignaturePort(Protocol):
    """E-signature boundary (DocuSign behind it); create/void/sync envelopes + verify inbound webhooks."""

    async def create_envelope(
        self, *, org_id: str, document_bytes: bytes, document_name: str, recipients: Sequence[dict[str, Any]]
    ) -> EnvelopeStatus: ...
    async def void_envelope(self, *, envelope_id: str, reason: str) -> None: ...
    async def sync_envelope(self, *, envelope_id: str) -> EnvelopeStatus: ...
    def verify_webhook_signature(self, *, payload: bytes, signature_header: str) -> bool: ...
```

### `aegis/application/ports/blob_store.py`

```python
from __future__ import annotations

from typing import Protocol


class BlobStorePort(Protocol):
    """Binary object storage boundary; put/get/delete by key, org-scoped for tenancy isolation."""

    def put_bytes(self, *, org_id: str, key: str, data: bytes, content_type: str) -> str: ...
    def get_bytes(self, *, org_id: str, key: str) -> bytes: ...
    def delete_bytes_permanently(self, *, org_id: str, key: str) -> None: ...
```

### `aegis/application/ports/ocr.py`

```python
from __future__ import annotations

from typing import Protocol


class ExtractedText(Protocol):
    """Result of text extraction: the text, a 0..1 quality score, and whether OCR was used."""

    text: str
    quality_score: float
    used_ocr: bool


class TextExtractorPort(Protocol):
    """Native (non-OCR) text extraction from an uploaded document's bytes."""

    def extract(self, *, data: bytes, filename: str, content_type: str) -> ExtractedText: ...


class OcrPort(Protocol):
    """OCR fallback (Reducto behind it) invoked when native extraction is poor quality."""

    async def extract(self, *, data: bytes, filename: str) -> ExtractedText: ...
```

### `aegis/application/ports/email.py`

```python
from __future__ import annotations

from typing import Protocol


class EmailPort(Protocol):
    """Transactional email boundary (Resend behind it); send one HTML message."""

    async def send(self, *, to: list[str], subject: str, html: str, from_address: str | None = None) -> None: ...
```

### `aegis/application/ports/feature_flags.py`

```python
from __future__ import annotations

from typing import Protocol


class FeatureFlagPort(Protocol):
    """Resolves per-org feature flags (AdminSetting behind it); gates skills and tools."""

    def is_enabled(self, *, org_id: str, flag: str, default: bool = True) -> bool: ...
    def set_enabled(self, *, org_id: str, flag: str, enabled: bool) -> None: ...
```

### `aegis/application/ports/prompt_versions.py`

```python
from __future__ import annotations

from typing import Any, Protocol


class PromptBundle(Protocol):
    """A resolved prompt version: its key/version/hash, the system+user templates, and model config."""

    prompt_key: str
    version: int
    prompt_hash: str
    system_template: str
    model_name: str
    model_config_hash: str


class PromptVersionPort(Protocol):
    """Resolves the active DB-backed prompt bundle for an (org, prompt_key); read-only from the use case's view."""

    def active_bundle(
        self, *, org_id: str, prompt_key: str, default_version: int, model_config: dict[str, Any]
    ) -> PromptBundle: ...
```

**Port set is complete for the 5 sprints:** `ClockPort`, the aggregate repositories, `AbstractUnitOfWork`, `AbstractEventBus`, `AbstractAIClient`, `AbstractToolDispatcher`, `AbstractVectorStore` + `EmbeddingModel`, `JobQueuePort`, `ESignaturePort`, `BlobStorePort`, `OcrPort`/`TextExtractorPort`, `EmailPort`, `FeatureFlagPort`, `PromptVersionPort`. Nothing else is required; nothing here is unused by the sprint plan.

---

# Part 3 — Migration sequence (5 sprints)

**Global invariant (stated once, true for every sprint):** the migration is **structural only**. No Alembic migration is written, no column/table/index/constraint changes, no enum value changes, no business-rule changes. The `aegis/` ORM models in `infrastructure/postgres/models/` map to the **same physical tables** as today's `app/*/models.py` (same `__tablename__`, same `NAMING_CONVENTION`, same `Vector(384)`); they are a second mapping over an unchanged schema during coexistence, then the canonical one. **Therefore: zero schema/DB-migration concerns in every sprint below.** Where a sprint says "blast radius," DB schema is explicitly *not* in it.

**Strangler-fig posture (global).** `aegis/` is built alongside `backend/app/`. The live app keeps importing `app/*` until a route/task is explicitly cut over. Cutover is per-endpoint and feature-flag-gated by `settings.use_aegis_<area>` (a new boolean group in `infrastructure/config.py`, default `False`) read in `adapters/api/deps.py`: when off, the legacy `app/*` router handles the request; when on, the `aegis` router does. Both share the *same* `SessionLocal`/engine (re-exported from `infrastructure/postgres/engine.py` which simply *is* the object from `core/database.py` during coexistence), so a request never spans two engines. This lets each sprint land, ship dark, flip the flag for one area, and roll back by flipping it off — with behavior unchanged.

---

## Sprint 1 — Repository + Unit of Work; routes stop committing

**Goal:** Introduce the Repository and Unit-of-Work abstractions and move transaction ownership out of route functions into the UoW, starting with the contracts area.

**Exact files changed/created:**
- *Create (ports):* `aegis/application/ports/{unit_of_work,repositories,clock,event_bus,ai_client,prompt_versions,feature_flags}.py` (full signatures from Part 2; `event_bus` registered but no-op handlers yet — events arrive Sprint 2).
- *Create (infra):* `aegis/infrastructure/postgres/{engine,base,unit_of_work}.py`; `aegis/infrastructure/postgres/models/{contracts,contract_files,ai,jobs,audit}.py` (re-map the tables touched by the slice); `aegis/infrastructure/postgres/repositories/{contract_repository,stage_history_repository,skill_run_repository,timeline_repository,_mapping}.py`; `aegis/infrastructure/clock.py`; `aegis/infrastructure/events/in_process_bus.py` (registers nothing yet).
- *Create (application):* `aegis/application/use_cases/contracts/{transition_contract_stage,get_contract}.py`, `aegis/application/dto/{contracts,common}.py`.
- *Create (adapters):* `aegis/adapters/api/{deps,contracts}.py` (the refactored lifecycle route), `aegis/adapters/api/presenters/contract_presenter.py`.
- *Touch (legacy, behind flag):* `backend/app/contracts/routes.py:112` — when `settings.use_aegis_contracts` is on, delegate `POST /{id}/lifecycle` to the aegis router; otherwise unchanged. No edits to `app/contracts/lifecycle.py` yet (its events come in Sprint 2 — Sprint 1 keeps calling it through an adapter that owns the commit at the UoW boundary).

**Blast radius.** Only the contracts lifecycle endpoint changes behaviorally-equivalent wiring; everything else still runs on `app/*`. Of the **135** commit sites, Sprint 1 removes the commit from **1** route (`contracts/routes.py:112`) by relocating it into `PostgresUnitOfWork.commit()` — the remaining 134 are untouched and still legal because the legacy path is unchanged. `get_db` (`core/deps.py:17`) is unchanged; the new `adapters/api/deps.py:get_uow()` wraps the *same* `SessionLocal`. **DB/schema: none — structural only.**

**Dependencies.** None precede it; this is the foundation. (Sprints 3 and 5 need the UoW + repos from here.)

**Done condition.** `aegis/adapters/api/contracts.py`'s lifecycle route contains **zero** `db.commit()`/`db.flush()` — it does `with uow: uc.execute(...)` and the commit lives in `uow.commit()`. `TransitionContractStageUseCase` runs green against a **fake in-memory UoW** with **no live `Session`** (the Sprint-4 unit test pattern, delivered here for this slice). An import-linter contract in `pyproject.toml` (`forbidden: aegis.application -> sqlalchemy|httpx|fastapi|celery; aegis.domain -> *infra*`) passes in CI, structurally proving Application/Domain cannot import Infrastructure.

**Strangler-fig note.** Old and new coexist via `settings.use_aegis_contracts`. Off by default → legacy route + `app/contracts/lifecycle.py` + its `db.commit()` handle production unchanged. Flip on → aegis route + UoW handle it, same observable result (same JSON, same rows, same stage history). Both use the one shared engine, so no dual-transaction hazard.

---

## Sprint 2 — Domain Models + Domain Events; kill the autonomous audit commit

**Goal:** Extract pure domain entities/events and replace inline `write_audit_log` / `write_timeline_event` / stage-transition side effects with event dispatch through the UoW, deleting `audit.py`'s autonomous `SessionLocal`.

**Exact files changed/created:**
- *Create (domain):* `aegis/domain/contracts/{entities,lifecycle,stage_rules,access,events}.py` (`Contract.transition_to()` raises `ContractStageTransitioned`; `IllegalStageTransition` domain error replaces `HTTPException`); `aegis/domain/shared/{events,errors,identifiers,clock}.py`; `aegis/domain/audit/{audit_entry,hash_chain,timeline}.py` (`compute_audit_row_hash` moved verbatim, pure); `aegis/domain/{obligations,renewals,approvals,signatures}/events.py`; `aegis/domain/contract_brain/events.py`.
- *Create (application):* `aegis/application/event_handlers/{audit_handler,timeline_handler,stage_history_handler,registrations}.py`.
- *Create (infra):* `aegis/infrastructure/postgres/repositories/audit_repository.py` (the hash-chain append + `pg_advisory_xact_lock`, now running **on the UoW session**, not a private one); register handlers in `aegis/infrastructure/events/in_process_bus.py` via `registrations.py`.
- *Edit (use case):* `aegis/application/use_cases/contracts/transition_contract_stage.py` — now calls `Contract.transition_to()`, collects the raised events into the UoW, lets `uow.commit()` dispatch them to the audit/timeline/stage-history handlers.
- *Touch (legacy):* `backend/app/core/audit.py` — **not deleted yet**; gains a module-level guard so that when `settings.use_aegis_events` is on, `write_audit_log` is a no-op stub (the event path owns it). Legacy `app/contracts/lifecycle.py` keeps calling the old `write_audit_log` only while the flag is off.

**Blast radius.** The autonomous-session anomaly (`audit.py:47,82` — audit persists on rollback) is resolved: under the event path the audit row is written **inside the same UoW transaction** as the operation (Agent 2 §C's "correct CA posture"; the outbox alternative in `infrastructure/events/outbox.py` is stubbed but not wired). This is a deliberate, documented behavior change *for the audit-survives-rollback edge case only* — every successful operation still produces an identical audit row; the only difference is a rolled-back operation no longer leaves an orphan audit row. **This is the one place the plan touches observable behavior, and it is the intended fix, gated behind `use_aegis_events`.** Commit-site impact: removes the autonomous commit (`audit.py:82`, the **1** cross-cutting site) for the contracts area; the ~14 `write_audit_log` call sites across domains are migrated incrementally as each area cuts over (most land in Sprints 3-5). **DB/schema: none.**

**Dependencies.** Needs Sprint 1's UoW + `AbstractEventBus` (events are collected on the UoW and dispatched on its commit). Must precede Sprint 3 because the decomposed AI use cases raise the same events.

**Done condition.** `Contract.transition_to()` raises `ContractStageTransitioned` and contains **zero** imports of `sqlalchemy`, `fastapi`, or `app.core.audit`. With `use_aegis_events` on, a forced rollback in the lifecycle use case leaves **no** audit row (proven by test) — the autonomous-commit anomaly is gone. `verify_audit_hash_chain` still returns `True` over rows written by the new `audit_repository`.

**Strangler-fig note.** `settings.use_aegis_events` gates the whole event path. Off → `app/core/audit.py` behaves exactly as today (autonomous session). On → domain entities raise events, handlers write audit/timeline on the UoW session, and the legacy `write_audit_log` stubs out so no double-write occurs. Flipping the two flags (`use_aegis_contracts`, `use_aegis_events`) together cuts the contracts area fully onto the new path; either off rolls back instantly.

---

## Sprint 3 — Decompose `AIController` into Application use cases + Infra adapters

**Goal:** Dissolve the `AIController` god-object: `run_structured_skill` → `RunStructuredSkillUseCase`; `stream/resume_assistant_run` → `AssistantRunStateMachine`; the provider call → `AnthropicHTTPClient` behind `AbstractAIClient`.

**Exact files changed/created:**
- *Create (domain):* `aegis/domain/ai/{skill_definition,skill_run,assistant_run,output_validator,citation_validator,context,fallback,stream_events,skill_catalog,events}.py` (`validate_citations` moved from `ai/citations.py`; `ContractAIContext` + 25 000-char cap moved from `ai/context.py`; the 13 skills as data).
- *Create (application):* `aegis/application/use_cases/ai/{run_structured_skill,assistant_run_state_machine,persist_skill_output_policy,resolve_contract_context,build_assistant_tool_schemas,feature_flag_gate}.py`.
- *Create (infra):* `aegis/infrastructure/anthropic/{client,transport,provider_dto,event_mapper,mock}.py` (impl of `AbstractAIClient`: `complete_structured` + `stream_run` + `resume_run`; `transport.py` is the long-lived `httpx.AsyncClient` + tenacity from `claude.py`; `mock.py` from `_claude_mock.py`); repositories `skill_run_repository` (extend), `citation_repository`, `ai_call_log_repository`, `usage_repository`, `assistant_run_repository`, `prompt_version_repository`, `feature_flag_repository`.
- *Create (adapters):* `aegis/adapters/ai_tools/tool_use_translator.py` (`_extract_structured_output`, content-block↔message mapping); `aegis/adapters/api/{ai,assistant}.py` + `presenters/assistant_sse_presenter.py` (SSE framing).
- *Touch (legacy):* `backend/app/ai/routes.py:125,147` and `backend/app/assistant/routes.py` — flag-delegate to aegis routers under `settings.use_aegis_ai`. `backend/app/ai/controller.py` stays in place for non-cut paths until Sprint 5 (the Celery task still imports `ai_controller` until then).

**Blast radius.** The AI engine's commit sites collapse: `controller.py`'s **16** commits + per-iteration assistant commits (`206/238/259/291/325`, `486/516/528/559/592`) become UoW boundaries inside the new use cases; `ai/routes.py`'s **2** route commits move to the UoW. That is **18** of the 135 sites retired for the cut paths. `_persist_skill_output` writing 7 domains' models directly (Agent 2's runner-up violation) is replaced by `PersistSkillOutputPolicy` calling per-aggregate repositories. The per-iteration durability of long assistant runs is preserved by the state machine committing the UoW at each iteration boundary (an explicit Application policy, executed by the UoW) — same durability behavior, no SSE/httpx in the Application layer. **DB/schema: none.**

**Dependencies.** Needs Sprint 1 (UoW + repos + `AbstractAIClient` port) and Sprint 2 (the events `SkillSucceeded`/`ObligationsExtracted`/etc. that the persist policy raises). The state machine's `tool_dispatcher` port is *declared* here but its concrete impl arrives in Sprint 4 — until then the assistant cut-over flag stays off (structured-skill reruns can cut over independently, since they don't need the dispatcher).

**Done condition.** `RunStructuredSkillUseCase.execute(...)` runs green with a **fake `AbstractAIClient`** (returning a canned `StructuredCompletion`) and a **fake UoW**, asserting a `SkillRun` reaches `SUCCEEDED`/`NEEDS_REVIEW` and citations validate — **no httpx, no live `Session`**. `AssistantRunStateMachine` drives a mock multi-tool run to `SUCCEEDED` and a confirmation case to `WAITING_CONFIRMATION` (persisting `provider_state`) using fake `AbstractAIClient` + fake `AbstractToolDispatcher`, asserting it never imports `httpx`/SSE. `grep -r "import httpx\|StreamingResponse" aegis/application aegis/domain` returns nothing.

**Strangler-fig note.** `settings.use_aegis_ai` gates skill reruns; a separate `settings.use_aegis_assistant` gates the SSE stream/resume (flipped only after Sprint 4 lands the dispatcher). Off → `app/ai/controller.py` + `integrations/claude.py` singleton handle production. On → the use cases + `AnthropicHTTPClient` handle it, emitting identical skill-run rows, citations, and SSE frames. Both share the engine and the same long-lived httpx client (re-exported during coexistence).

---

## Sprint 4 — Isolate `ToolRegistry` execution: every tool testable without DB/HTTP

**Goal:** Turn `ToolRuntime` (the god-object dispatcher) into a thin `ToolDispatcher` (impl of `AbstractToolDispatcher`) whose handlers each validate input and call exactly **one** Application use case — severing all cross-domain service imports.

**Exact files changed/created:**
- *Create (domain):* `aegis/domain/ai/{tool_spec,tool_catalog}.py` (`ToolSpec` moved from `tool_registry.py:118`; the 24 `_register(...)` calls become pure catalog data); `aegis/domain/ai/edit_anchoring.py` (the `edit_contract` anchoring rule).
- *Create (application):* the use cases each tool calls, per Agent 2 §B groups — `aegis/application/use_cases/contracts/{get_contract,archive_contract}.py`, `aegis/application/use_cases/contract_brain/...`, `aegis/application/use_cases/playbooks/execute_playbook_run.py`, `aegis/application/use_cases/approvals/submit_for_approval.py`, `aegis/application/use_cases/signatures/send_for_signature.py`, `aegis/application/use_cases/obligations/queue_extraction.py`, `aegis/application/use_cases/tabular/create_tabular_review.py`, `aegis/application/use_cases/workflows/run_workflow.py`, plus the read-query use cases (`find_contracts`, `my_attention_items`, `list_obligations`, etc.).
- *Create (adapters):* `aegis/adapters/ai_tools/{tool_dispatcher,confirmation_gate}.py` + `handlers/{read_only_handlers,brain_handlers,draft_handlers,mutating_handlers,external_handlers,destructive_handlers}.py` (impl of `AbstractToolDispatcher`).
- *Create (infra):* the remaining aggregate repositories the new use cases need (`approval_repository`, `signature_repository`, `obligation_repository`, `renewal_repository`, `playbook_repository`, `tabular_repository`, `knowledge_graph_repository`, `clause_repository`, `workflow_repository`); `aegis/infrastructure/{docusign,reducto,resend,storage}/client.py` (impls of `ESignaturePort`/`OcrPort`/`EmailPort`/`BlobStorePort`); `aegis/infrastructure/vector/{pgvector_store,fastembed_model}.py` (impls of `AbstractVectorStore`/`EmbeddingModel`, owning `cosine_distance` + 384 dims).
- *Touch (legacy):* `backend/app/ai/tool_runtime.py` — flag-delegate execution to the aegis `ToolDispatcher` under `settings.use_aegis_tools`; flip `use_aegis_assistant` on once green (the state machine now has its concrete dispatcher).

**Blast radius.** `ai/tool_runtime.py`'s **2** commits (`690/717`) + its ~40 flushes retire for cut paths (folded into the use cases' UoW). The widest fan-out in the codebase (Agent 1 §4.2: `tool_runtime` importing `approvals/contract_files/playbooks/signatures/tabular_review` services + `jobs.service` + `contract_brain.retrieval` + `contracts.lifecycle` + 3 integration singletons) is fully severed — the dispatcher depends only on Application use cases + the `AbstractToolDispatcher`/port set. The pgvector operator (`retrieval.py:63`) disappears behind `AbstractVectorStore.search`; `Vector(384)` is confined to `infrastructure/postgres/models/contract_files.py`. The duplicated job-queueing logic (intake vs tool vs tabular) unifies into `QueueIntakeAiJobsUseCase` + `DispatchJobUseCase`. **DB/schema: none.**

**Dependencies.** Needs Sprint 1 (UoW/repos), Sprint 2 (events the mutating tools raise), and Sprint 3 (the `AssistantRunStateMachine` that awaits this dispatcher; `edit_contract`/`run_playbook_review` reuse `RunStructuredSkillUseCase`).

**Done condition.** Each of the 24 tool handlers runs green in a unit test with a **fake UoW + fake ports** and **no live `Session`/httpx** — e.g. `read_contract` against a fake `AbstractContractRepository`, `send_for_signature` against a fake `ESignaturePort`, `ask_contract_brain` against a fake `AbstractVectorStore`. `grep -r "from app\.\(approvals\|signatures\|playbooks\|tabular_review\|contract_files\|contract_brain\)\.\(service\|retrieval\)\|integrations\." aegis/adapters/ai_tools` returns nothing. `requires_confirmation` tools (`send_for_signature`, `external_share`, `archive_contract`) return `requires_confirmation=True` without performing the side effect until resumed.

**Strangler-fig note.** `settings.use_aegis_tools` gates tool execution; the dispatcher delegation lets the legacy `ToolRuntime` and the new `ToolDispatcher` coexist tool-by-tool if needed (the flag can be a set of enabled tool names during rollout). Off → legacy `tool_runtime` + cross-domain services handle the assistant's tool calls. On → handlers + use cases + ports handle them, with identical persisted `AssistantToolCall` rows and identical confirmation behavior.

---

## Sprint 5 — Celery tasks become thin Interface Adapters

**Goal:** Reduce `jobs/tasks.py` to a one-line shim calling `RunAiJobUseCase`; move job-type dispatch, the 11 commits, and the auto-chaining policy into the Application layer behind `JobQueuePort`.

**Exact files changed/created:**
- *Create (domain):* `aegis/domain/jobs/{job_run,chaining_policy,reaping_policy,events}.py` (`JobRun.mark_running/succeeded/failed` from `_mark_job_succeeded`; the idempotency-key rule from `_queue_contract_brain_ingestion`; the 20-min TTL rule).
- *Create (application):* `aegis/application/use_cases/jobs/{run_ai_job,chain_brain_ingestion,sync_job_from_skill_runs,dispatch_job,reap_stuck_jobs}.py`; `aegis/application/use_cases/contract_brain/{ingest_contract_brain,generate_embeddings}.py`; `aegis/application/event_handlers/brain_chain_handler.py` (`JobSucceeded(extraction)` → `ChainBrainIngestionUseCase`).
- *Create (infra):* `aegis/infrastructure/redis/{celery_app,job_queue}.py` (`CeleryJobQueue` impl of `JobQueuePort`, the only `run_ai_job.delay` site); `aegis/infrastructure/postgres/repositories/job_repository.py` (extend).
- *Create (adapters):* `aegis/adapters/jobs/celery_tasks.py` — `@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3}) def run_ai_job(self, job_id): return asyncio.run(container.run_ai_job_use_case.execute(job_id))`.
- *Touch (legacy):* `backend/app/jobs/tasks.py` — under `settings.use_aegis_jobs`, the task body delegates to the aegis use case; the lazy `from app.jobs.service import ...` circular-edge papering (`tasks.py:227`) is deleted on cutover (dispatch now goes through `JobQueuePort`). `backend/app/jobs/service.py:dispatch_job` delegates enqueue to `CeleryJobQueue`.

**Blast radius.** `jobs/tasks.py`'s **11** commits collapse into UoW boundaries inside `RunAiJobUseCase` (session ownership moves from the task to an injected UoW); the `jobs.tasks ↔ jobs.service` circular import is eliminated. The Celery task no longer transitively imports `ai.controller`/`contract_brain.ingestion`/`ai.embeddings` — it imports only the use case via the container. With Sprints 1-4 done, this retires the last large commit cluster; the auto-chaining ("after clause/obligation/renewal extraction succeeds, enqueue brain ingestion") becomes the `brain_chain_handler` reacting to a `JobSucceeded` event, preserving the exact idempotency-key dedupe. **DB/schema: none.**

**Dependencies.** Needs Sprint 1 (UoW/repos), Sprint 2 (`JobSucceeded` event + handler dispatch), Sprint 3 (`RunStructuredSkillUseCase` — every job branch calls it), and Sprint 4 (the per-skill use cases and `JobQueuePort` impl).

**Done condition.** `backend/app/jobs/tasks.py` (or its `aegis` replacement) contains **zero** `db.commit()` and **zero** `SessionLocal()`; the task body is a single `asyncio.run(...use_case.execute(job_id))`. `RunAiJobUseCase` runs green for a `clause_extraction` job with a **fake UoW + fake `RunStructuredSkillUseCase` + fake `JobQueuePort`**, asserting the chained `contract_brain_ingestion` job is enqueued exactly once (idempotency-key dedupe holds) — **no Celery, no live `Session`**. Across the whole `aegis/` tree, `grep -rc "db.commit()" aegis/` returns 0 (all commits live in `PostgresUnitOfWork.commit()`).

**Strangler-fig note.** `settings.use_aegis_jobs` gates the worker path. Off → the legacy `_run_ai_job` (own session, 11 commits) processes jobs. On → the shim + `RunAiJobUseCase` + UoW process them, with identical `JobRun` lifecycle, identical chaining, and identical Celery retry semantics (`max_retries=3`). Because enqueue goes through `JobQueuePort` either way and both share Redis + the engine, in-flight jobs are unaffected by the flip.

---

**End-state commit accounting.** After all 5 sprints, the 135 `db.commit()` sites + the 1 autonomous `audit.py` commit are gone from the cut-over `aegis/` paths; the single transactional owner is `PostgresUnitOfWork.commit()`, which also dispatches domain events. The legacy `backend/app/` tree is deleted in a final non-sprint cleanup once every `use_aegis_*` flag is permanently on and burned in.

---

# Part 4 — Sprint-1 vertical-slice spec for Agent 4

Agent 4 builds **one representative end-to-end slice** proving the pattern: a contract lifecycle transition and a structured-skill run, fully on the new stack, behind `use_aegis_contracts` + `use_aegis_ai`. This spec is exhaustive — build exactly these files, no more, no less. (It deliberately reaches slightly into Sprint-2 domain entities/events for the two named events, because the slice must demonstrate the event path end-to-end; that is intentional and bounded to the two events below.)

**Checklist (each item → its target file):**

1. **Domain — Contract aggregate + events**
   - `aegis/domain/contracts/entities.py` — `Contract` with `transition_to(to_stage, *, actor_user_id, override, override_authorized, signed_confirmation, has_signed_version, now)` that validates against `ALLOWED_TRANSITIONS` + the signed-version-for-ACTIVE rule and **raises** `ContractStageTransitioned` (collected by the use case); raises `IllegalStageTransition` (domain error) instead of `HTTPException`.
   - `aegis/domain/contracts/lifecycle.py` — `ALLOWED_TRANSITIONS` + `allowed_transitions_for()` (verbatim from `contracts/lifecycle.py:13-63`).
   - `aegis/domain/contracts/stage_rules.py` — the pure guard functions used by `transition_to`.
   - `aegis/domain/contracts/events.py` — `ContractStageTransitioned`.
   - `aegis/domain/audit/{audit_entry.py (AuditEntry), hash_chain.py (compute_audit_row_hash, verbatim from audit.py:93), timeline.py (TimelineEntry)}`; `aegis/domain/audit/events.py` is not needed — `AuditLogRequested` lives in `aegis/domain/shared/events.py` as a generic event (carries action/resource/before/after/actor/request_id).
   - `aegis/domain/shared/{events.py (DomainEvent, AuditLogRequested, ResourceTimelineEventRequested), errors.py (DomainError, IllegalStageTransition, AccessDeniedError), identifiers.py (new_uuid), clock.py (utcnow)}`.

2. **Domain — AI skill primitives (slice subset)**
   - `aegis/domain/ai/skill_definition.py` — `SkillDefinition` VO.
   - `aegis/domain/ai/skill_run.py` — `SkillRun` aggregate with status transitions → `SUCCEEDED`/`NEEDS_REVIEW`/`FAILED`.
   - `aegis/domain/ai/output_validator.py` — `SkillOutputValidator` (wraps `output_model.model_validate`).
   - `aegis/domain/ai/citation_validator.py` — `validate_citations` (verbatim rule from `ai/citations.py`).
   - `aegis/domain/ai/context.py` — `ContractAIContext` VO + 25 000-char cap (from `ai/context.py:11,14`).
   - `aegis/domain/ai/skill_catalog.py` — the one skill the slice exercises (`contract_metadata_extraction`) as data; the full 13 land in Sprint 3.

3. **Application — ports** (the subset the slice needs; full signatures from Part 2): `aegis/application/ports/{unit_of_work,repositories,ai_client,event_bus,clock,prompt_versions,feature_flags}.py`.

4. **Application — DTOs:** `aegis/application/dto/contracts.py` (`StageTransitionInput`, `StageTransitionResult`, `ContractView`), `aegis/application/dto/common.py` (`ActorContext`), `aegis/application/dto/ai.py` (`StructuredSkillInput`, `SkillRunResult`).

5. **Application — use cases (the two named):**
   - `aegis/application/use_cases/contracts/transition_contract_stage.py` — `TransitionContractStageUseCase.execute(*, uow, actor: ActorContext, contract_id, to_stage, reason, override, signed_confirmation)`: loads `Contract` via `uow.contracts.get_for_actor`, calls `contract.transition_to(...)` (reading `uow.contracts.has_signed_version` for the ACTIVE rule), `uow.contracts.update`, `uow.stage_history.add`, collects the raised `ContractStageTransitioned` + an `AuditLogRequested`. Commit + dispatch happen in the caller's `with uow:`.
   - `aegis/application/use_cases/ai/run_structured_skill.py` — `RunStructuredSkillUseCase.execute(*, uow, ai_client, actor, skill_name, input_payload, resource_type, resource_id, job_id=None, assistant_run_id=None)`: resolve `SkillDefinition` from catalog; `FeatureFlagGate` check via `FeatureFlagPort`; `PromptVersionPort.active_bundle`; build prompt; `uow.skill_runs.add(SkillRun(RUNNING))`; `await ai_client.complete_structured(...)`; `SkillOutputValidator` + `validate_citations`; set status; `uow.citations.add_all`; `uow.ai_call_logs.add`; `uow.usage.add`; collect `SkillSucceeded`/`ObligationsExtracted` as applicable; persist derived rows via repos (`PersistSkillOutputPolicy` stub for the metadata skill writing `Contract.metadata_json`). No `commit` inside — the route/task owns `with uow:`.
   - `aegis/application/use_cases/ai/{resolve_contract_context,feature_flag_gate,persist_skill_output_policy}.py` — supporting pieces the use case calls.

6. **Application — event handlers:** `aegis/application/event_handlers/{audit_handler,timeline_handler,stage_history_handler,registrations}.py`.

7. **Infrastructure — postgres:**
   - `aegis/infrastructure/postgres/engine.py` — `engine` + `SessionLocal` (`expire_on_commit=False`); during coexistence this re-exports the objects from `app/core/database.py` so there is one engine.
   - `aegis/infrastructure/postgres/base.py` — `Base` + `NAMING_CONVENTION` + all mixins.
   - `aegis/infrastructure/postgres/models/{contracts,contract_files,ai,audit}.py` — ORM mappings for `Contract`/`ContractStageHistory`/`ContractParty`, `ContractVersion`/`ContractTextSnapshot`, `AISkillRun`/`AICitation`/`AICallLog`/`UsageRecord`, `AuditLog`/`ResourceTimelineEvent` (same tables as today).
   - `aegis/infrastructure/postgres/unit_of_work.py` — **`PostgresUnitOfWork`** (impl of `AbstractUnitOfWork`): owns a `Session`, lazily exposes the slice repositories, `commit()` = `session.flush(); session.commit(); event_bus.publish(self._events)`, `rollback()` clears events, `__exit__` rolls back on exception.
   - `aegis/infrastructure/postgres/repositories/{_mapping,contract_repository,stage_history_repository,skill_run_repository,citation_repository,ai_call_log_repository,usage_repository,timeline_repository,audit_repository,prompt_version_repository,feature_flag_repository}.py` — **`SQLAlchemyContractRepository`** is the named deliverable (org-scoped `get`/`get_for_actor`/`list_for_actor`/`add`/`update`/`has_signed_version`/`list_stage_history`); `audit_repository.py` performs the hash-chain append on the UoW session (no private `SessionLocal`).
   - `aegis/infrastructure/postgres/access_filters.py` — `accessible_contract_filter` SQL clause.

8. **Infrastructure — Anthropic:** **`AnthropicHTTPClient`** (`aegis/infrastructure/anthropic/client.py`, impl of `AbstractAIClient`) — `complete_structured` (port of `claude.py:66`), `stream_run` (formalizes `claude.py:181` into typed `RunStreamEvent`s), `resume_run`; `aegis/infrastructure/anthropic/{transport.py (long-lived httpx + tenacity from claude.py), provider_dto.py (ClaudeProviderResponse), event_mapper.py, mock.py (from _claude_mock.py)}`. Streaming + structured + resume are all delivered here (the port demands all three).

9. **Infrastructure — supporting:** `aegis/infrastructure/clock.py` (`SystemClock`), `aegis/infrastructure/events/in_process_bus.py` (`InProcessEventBus`), `aegis/infrastructure/config.py` (Settings incl. the `use_aegis_*` flags + `ai_max_tool_iterations`), `aegis/infrastructure/container.py` (composition root wiring the above to ports + registering handlers).

10. **Adapters — refactored route + Celery task:**
    - `aegis/adapters/api/contracts.py` — the refactored **`POST /api/v1/contracts/{id}/lifecycle`** route (`run_structured_skill`'s sibling for the slice): `Depends(require_permission("contract:update"))`, parse body → `StageTransitionInput`, `with uow: result = TransitionContractStageUseCase().execute(uow=uow, ...)`, present via `contract_presenter`, return DTO. **Zero `db.commit`.**
    - `aegis/adapters/api/ai.py` — the refactored **`run_structured_skill` rerun route** (from `ai/routes.py:125`): `with uow: result = RunStructuredSkillUseCase().execute(uow=uow, ai_client=container.ai_client, ...)`. **Zero `db.commit`.**
    - `aegis/adapters/api/{deps,presenters/contract_presenter}.py` — `get_uow()`, `get_current_actor()`, `require_permission()`; the presenter.
    - `aegis/adapters/jobs/celery_tasks.py` — the refactored **Celery task** shim for the metadata-extraction job: `def run_ai_job(self, job_id): return asyncio.run(container.run_ai_job_use_case.execute(job_id))` — but for the Sprint-1 slice it may instead expose `run_structured_skill_job` calling `RunStructuredSkillUseCase` directly inside `with uow:`, demonstrating the task→use-case pattern without waiting for Sprint 5's full `RunAiJobUseCase`.

11. **The 2 unit tests** (`backend/tests/aegis/`):
    - `test_transition_contract_stage_use_case.py` — drives `TransitionContractStageUseCase` against a **fake in-memory UoW** (fake `contracts`/`stage_history` repos + a fake event bus that records published events). Asserts: a legal transition updates the contract + adds one `ContractStageHistory` + publishes `ContractStageTransitioned` + `AuditLogRequested` on commit; an illegal transition raises `IllegalStageTransition` (not `HTTPException`); activating without a signed version and without `signed_confirmation` raises; **no live `Session` is constructed**.
    - `test_run_structured_skill_use_case.py` — drives `RunStructuredSkillUseCase` against a **fake `AbstractAIClient`** (returns a canned `StructuredCompletion` for `contract_metadata_extraction`), a **fake UoW**, fake `PromptVersionPort`/`FeatureFlagPort`. Asserts: the `SkillRun` reaches `SUCCEEDED` (or `NEEDS_REVIEW` when a citation fails validation), `output_payload` is set, citations are persisted via the fake repo, and **no httpx call and no live `Session`** occur.

**Out of scope for Agent 4's slice (do NOT build):** the `AssistantRunStateMachine`, the `ToolDispatcher` + 24 handlers, the vector store / embeddings, the `RunAiJobUseCase` full job-type dispatch, the DocuSign/Reducto/Resend/storage adapters, and the remaining 12 skills. Those are Sprints 3-5. The slice proves the spine: **Domain entity raises events → Application use case orchestrates through ports → Infrastructure UoW commits and dispatches → Adapter route/task holds no transaction.**

---

*End of Target Architecture & Migration Sequence. Design + full port signatures only; production implementation is Agent 4.*
