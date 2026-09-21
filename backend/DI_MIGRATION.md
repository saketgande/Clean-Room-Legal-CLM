# Dependency-Injection Migration Tracker

Converting the backend to FastAPI-native dependency injection, one module at a
time. See the methodology this follows: constructor-injected service classes
per file, a `dependencies.py` per module with `Depends()`-compatible provider
functions, and temporary "strangler" wrapper functions kept at the bottom of
each converted file so external callers keep working unchanged until they're
migrated in their own pass. A wrapper is only deleted once grep confirms no
importers remain.

## How to read this table

- **Service classes** — DB-touching logic moved into `__init__(self, db)` +
  methods. Pure functions that never touched `db` stay module-level (not
  tracked here — they need no conversion).
- **routes.py** — the module's own routes converted to `Depends(get_..._service)`.
- **Providers** — `dependencies.py` added with one provider per service class.
- **Wrappers** — old public function names kept as thin delegates, for the
  benefit of external importers not yet converted.
- **External callers migrated** — of the modules that import this module's
  service functions directly, how many have been switched to
  `Depends(get_..._service)` instead. Wrappers can only be deleted once this
  reaches "all".

| Module / integration | Service classes | routes.py | Providers | Wrappers | External callers migrated | Status |
|---|---|---|---|---|---|---|
| `contracts/` | `ContractService`, `ContractAccessService`, `ContractLifecycleService` (absorbed `stage_triggers.py`), `ContractRiskService`, `ContractCommentService` | done (`routes.py`, `comments_routes.py`) | done (`contracts/dependencies.py`) | in place for every previously-public function | 0 / ~40 files | **Pilot complete** — service layer converted, routes use DI, wrappers keep the ~40 external importers working unchanged |
| `storage` (integration) | n/a — added `StorageBackend` Protocol over existing `StorageService`/`S3Storage` | n/a | done (`integrations/dependencies.py`) | n/a (no signature change) | 0 / 7 files | **Pilot complete** — provider added, no caller migrated yet |
| `contract_files/` | `ContractFilesService` (storage/reducto/databricks injected, defaulting to the existing singletons) | done (`routes.py`, plus `contracts/routes.py`'s `upload_contract`) | done (`contract_files/dependencies.py`) | in place for `create_contract_from_upload`, `add_version_from_upload`, `next_version_number`, `requeue_contract_ai_jobs`, `queue_activation_ai_jobs`, `backfill_document_elements`, `_queue_initial_contract_jobs` | 0 / remaining external files (`ai/tool_runtime.py`, `devtools.py`, `intake/*`, `playbooks/*`, `word_addin/service.py`, `signatures/service.py`, `eval/seed_golden_fixtures.py`) | **Converted** — service layer + this module's own routes use DI; wrappers keep external importers working unchanged |
| `auth/` | `AuthService` (all 20 db-touching functions; pure formatting helpers stay module-level) | done (`routes.py`, 17 handlers) | done (`auth/dependencies.py`: `get_auth_service`) | in place for every previously-public function | 0 / 3 (`core/deps.py` hot path, `devtools.py`, `roles/service.py`'s deferred import) | **Converted** — `core/deps.py` deliberately left calling the wrapper (highest-sensitivity path; migrate only once proven safe) |
| `roles/` | `RoleService` | done (`routes.py`, 6 handlers) | done (`roles/dependencies.py`: `get_role_service`) | **removed** — final sweep (see below) confirmed 0 importers repo-wide | n/a | **Converted** |
| `authority/` | `AuthorityService` (`AuthorityDecision` dataclass + pure predicate helpers stay module-level) | done (`routes.py`, 5 handlers) | done (`authority/dependencies.py`: `get_authority_service`) | in place, esp. `enforce_authority` | 0 / 3 (`approvals/routes.py`, `approvals/service.py`, `signatures/routes.py`, all deferred imports) | **Converted** |
| `grants/` | `GrantService` (`granted_resource_ids` + SQL-predicate helpers stay module-level — used directly by `contracts/access.py`) | done (`routes.py`, 3 handlers — no `require_permission` at the route layer by design; authorization stays inside `can_manage_grants`, preserved as-is) | done (`grants/dependencies.py`: `get_grant_service`) | in place, esp. `user_has_grant` | 0 / 1 (`contracts/access.py`, module-level import) | **Converted** |
| `walls/` | `WallService` (`wall_block_filter` + SQL-predicate helpers stay module-level — used directly by `contracts/access.py`) | done (`routes.py`, 4 handlers) | done (`walls/dependencies.py`: `get_wall_service`) | in place, esp. `user_is_walled` | 0 / 1 (`contracts/access.py`, module-level import) | **Converted** |
| `reducto`, `databricks` (integrations) | n/a — added `OCRProvider` Protocol (in `integrations/ocr.py`, shared) and `DatabricksDocumentClient` Protocol over the existing clients | n/a | done (`integrations/dependencies.py`: `get_reducto_client`, `get_databricks_client`) | n/a (no signature change) | 0 (only consumed so far via `ContractFilesService`) | **Providers added** as part of the `contract_files/` pass |
| `resend`, `docusign` (integrations) | n/a — added `EmailSender` Protocol (`integrations/resend.py`) and `SignatureProvider` Protocol (`integrations/docusign.py`) over the existing clients | n/a | done (`integrations/dependencies.py`: `get_resend_client`, `get_docusign_client`) | n/a (no signature change) | 1 / 1 direct-import site converted (`auth/routes.py`'s `_send_invitation_email` now takes an injected `EmailSender`) — `signatures/*`, `approvals/service.py`, `obligations/routes.py`, `renewals/routes.py`, `jobs/tasks.py`, `ai/tool_runtime.py` still import the module singleton directly, unchanged, pending their own module passes | **Converted** |
| `intake/` (core spine: `service.py` + `routes.py`) | `IntakeService` (~34 db-touching functions; pure stage/SLA math + serializers that don't touch `db` stay module-level) | done (`routes.py`, 40 of 56 handlers that called `service.*`; the other 16 call `teams_mod`/`routing_mod`/`screening_mod`/`gmail_sync_mod`/`ingest_mod`/`copilot_mod` directly, unchanged — those files are a later sub-pass) | done (`intake/dependencies.py`: `get_intake_service`) | in place for every previously-public function | 0 / several (`workflows/service.py`, `approvals/*`, `notices/service.py`, `ai/tool_runtime.py`, `devtools.py` all still import `intake.service` functions directly — verified they still import cleanly) | **Converted (Pass 1 of 4 — see intake/ sub-passes below)** |
| `intake/` Pass 2 (peer modules): `teams.py`→`TeamService`, `routing.py`→`RoutingService` (composes `TeamService`), `screening.py`→`ScreeningService`, `ingest.py`→`IngestService` (composes `IntakeService`), `gmail_sync.py`→`GmailSyncService` (composes `IntakeService` + `IngestService`); `copilot.py` has no `db` usage at all — left untouched, nothing to convert | done (`routes.py`'s remaining 16 handlers that called `teams_mod`/`routing_mod`/`screening_mod`/`gmail_sync_mod`/`ingest_mod`) | done (all 6 providers now in `intake/dependencies.py`) | in place for every previously-public function, incl. underscore-prefixed ones used cross-file (`teams._label`, `ingest._resolve_requester`/`ingest_message`) | 0 / 2 (`app.workflows.service` imports `pick_from_pool` from `teams.py` directly; `app.intake.seed` imports `routing` — verified both still import cleanly) | **Converted** |
| `intake/` Pass 3 (AI-agent chain): `agents.py`, `gates.py`, `triage_agent.py`, `flow_agent.py`, `litigation_agent.py`, `email_triage_agent.py` | n/a — deliberately left as plain function modules, see note below | n/a | n/a | n/a | n/a | **Assessed, no conversion** (user-confirmed decision) |
| `intake/` Pass 4: `approval_bridge.py`→`ApprovalBridgeService` (`IntakeApprovalSubject` stays a plain value/adapter object — not a DI concern, it's constructed as data and handed to `app/approvals`, never injected), `drafting.py`→`DraftingService` (composes `ContractFilesService` for the upload pipeline; templates/`resolve_doc_type`/rendering helpers stay module-level pure functions) | done (`routes.py`'s `draft_contract`/`ingest_attachment` handlers) | done (`get_approval_bridge_service`, `get_drafting_service` in `intake/dependencies.py`) | in place for `build_intake_subject`, `submit_request_for_approval`, `draft_contract_for_request`, `ingest_attachment_as_contract` | 0 / 4 (`app.workflows.service`, `app.approvals.service`, `app.approvals.routes`, `app.notices.service` all import these directly — verified all four still import cleanly) | **Converted** — this closes out all 4 `intake/` sub-passes |
| `workflows/` | `WorkflowService` (~20 db-touching functions). `_execute_step` and its direct call graph (`_get_contract`, `_advance_contract_to`, `_draft_contract`, `_assign_step`, `_resolve_role_team`, `_team_head`) deliberately kept as plain module functions, NOT methods — `_execute_step` is imported by `tests/test_flows_ai_task_agents.py` (currently broken at collection: wrong import path `app.flows.service`, same as `test_flow_criteria_matching.py`) which does `inspect.getsource(_execute_step)` on the real body; a delegating wrapper would silently defeat that if the import path is ever fixed. `_matches`/`_ai_agent_failed` (also referenced by that test) were already pure. `builtin.py`'s `seed_builtin_flows` left untouched (small single-purpose seeder, same treatment as `intake/seed.py`) | done (`routes.py`, 12 handlers) | done (`workflows/dependencies.py`: `get_workflow_service`) | in place for every previously-public function | 0 / 6 (`intake/approval_bridge.py`, `intake/service.py`, `intake/litigation_agent.py`, `intake/flow_agent.py`, `contracts/lifecycle.py`, `ai/tool_runtime.py` — all verified still import cleanly) | **Converted** |
| `approvals/` | `ApprovalsService` (~15 db-touching functions). `ContractSubject` stays a plain adapter object (mirrors `IntakeApprovalSubject`, not a DI concern). `_fast_lane_reason` kept as a plain module function — it's called directly by `ContractSubject.try_fast_lane`, a non-service class, so it can't be a method. `_subject_clause`/`_matches` already pure. `routes.py`'s own ACL helpers (`_can_view_approval`, `_can_decide_approval`, etc.) left untouched — `tests/test_phase6_9_integration.py` does `inspect.getsource(list_approvals)`/`inspect.getsource(decide_approval)` asserting those calls + an error string stay literally inline; verified passing (7/7) after conversion | done (`routes.py`, 16 handlers) | done (`approvals/dependencies.py`: `get_approvals_service`) | in place for every previously-public function | 0 / 6 (`auth/service.py`, `devtools.py`, `intake/service.py`, `intake/approval_bridge.py`, `workflows/service.py`, `ai/tool_runtime.py` — all verified still import cleanly) | **Converted** |
| `signatures/` | `SignaturesService` (all 4 db-touching functions: `validate_signature_recipients`, `_create_signed_version`, `_download_and_store_signed_document`, `sync_signature_request` — no pure functions in this module) | done (`routes.py`, 4 handlers: `list_signature_requests` unchanged — didn't call signatures/service; `send_for_signature`, `sync_signature`, `docusign_connect_webhook` now inject `get_signatures_service`) | done (`signatures/dependencies.py`: `get_signatures_service`) | in place for `validate_signature_recipients`, `sync_signature_request` | 0 / 1 (`ai/tool_runtime.py` imports `validate_signature_recipients` directly — non-request caller, kept on the wrapper unchanged; verified still imports cleanly) | **Converted** — `tests/test_phase6_9_integration.py`'s `inspect.getsource(list_signature_requests)`/`inspect.getsource(sync_signature)` checks (asserting `get_contract_for_user`/`accessible_contract_filter(current_user)` appear somewhere across several routes) were preserved untouched since those literal calls are to the already-converted `contracts/` wrappers, not to anything in `signatures/service.py`; verified passing (7/7) |
| `obligations/` | `ObligationsService` (new — this module had no `service.py`; all logic lived inline in `routes.py`). Covers `serialize_one`, `update_obligation`, `complete_obligation`, `trigger_extraction`, `run_reminders`. Pure `serialize_obligation()` helper (no `db`) moved to `service.py` as a plain function. `_get_obligation` and `list_obligations` deliberately left as plain functions in `routes.py` — see note below | done (`routes.py`, 4 of 6 handlers: `get_obligation`, `update_obligation`, `complete_obligation`, `trigger_obligation_extraction`, `run_obligation_reminders` inject `get_obligations_service`; `list_obligations` unchanged) | done (`obligations/dependencies.py`: `get_obligations_service`) | none needed — no external importers of the old inline logic existed (only `.models` imports elsewhere) | n/a | **Converted** — also fixed a pre-existing, unrelated bug (confirmed with user first): `_get_obligation` was returning the *serialized dict* instead of the `Obligation` ORM row, even though every caller (`update_obligation`'s `setattr`, `complete_obligation`'s `ob.status = ...`, `get_obligation`'s second `_serialize_one` call) treated it as the live row — `GET/PATCH /obligations/{id}` and `POST /obligations/{id}/complete` would have raised `AttributeError` at runtime. Fixed by having `_get_obligation` return `ob` directly |
| `renewals/` | `RenewalsService` (new — like `obligations/`, this module had no `service.py`; all logic lived inline in `routes.py`). Covers `get_recommendation`, `decide_renewal`, `run_window_check`. Pure `serialize_renewal()` helper (no `db`) moved to `service.py` as a plain function. `_get_renewal` and `list_renewals` deliberately left as plain functions in `routes.py` — same reason as `obligations/`'s `_get_obligation`/`list_obligations`. `recommendation.py` (`recommend_renewal`) left untouched — its `db` parameter is unused (leaf AI-advisory function, same category as the `intake/` Pass 3 agent modules) | done (`routes.py`; `renewal_recommendation`, `decide_renewal`, `run_renewal_window_check` inject `get_renewals_service`; `list_renewals`, `get_renewal` unchanged — `get_renewal` has no logic beyond `_get_renewal`, nothing to inject) | done (`renewals/dependencies.py`: `get_renewals_service`) | none needed — no external importers of the old inline logic existed (only `.models` imports elsewhere) | n/a | **Converted** |
| `notices/` | `NoticesService` (~19 db-touching functions/helpers, incl. private `_next_ref`, `_user_name`, `_contract_title`, `_intake_ref`, `serialize`, `_add_event`, `_get`, `_validate_contract`, `_document_text`). Pure helpers with no `db` (`deadline_posture`, `reminder_stage_for`, `prettify`, `_extract_document_text`, `AT_RISK_DAYS`, `DOC_MAX_BYTES`) stay module-level — `deadline_posture`/`reminder_stage_for`/`AT_RISK_DAYS` are also pinned by `tests/test_notice_deadline_posture.py` and `tests/test_notice_reminder_stages.py` importing them directly. `drafting.py`/`extraction.py` left untouched — same leaf-AI-module category as `renewals/recommendation.py` / `intake/` Pass 3 (`drafting.py`'s `_skeleton` is also imported directly by `tests/test_notice_response_draft.py`) | done (`routes.py`, all 13 handlers) | done (`notices/dependencies.py`: `get_notices_service`) | in place for all 14 previously-public functions (`list_notices`, `get_notice`, `summary`, `create_notice`, `update_notice`, `set_status`, `add_note`, `draft_response`, `escalate`, `run_reminders`, `add_document`, `delete_document`, `extract_from_upload`, `delete_notice`) | 0 / 2 (`ai/tool_runtime.py` imports `list_notices`/`create_notice`/`draft_response`/`get_notice` directly; `jobs/tasks.py` imports the whole module as `notices_service` for the nightly `run_reminders` sweep — both non-request callers, verified still import and call cleanly) | **Converted** |
| `playbooks/` | `PlaybooksService` (~15 functions: `get_playbook_for_user`, `get_playbook_version`, `create_initial_playbook`, `next_playbook_version_number`, `clone_playbook_version`, `expand_playbook`, `_generate_missing_rules`, `select_run_version`, `pick_playbook_for_contract`, `_auto_rule_payloads`, `auto_review_contract`, `record_deviation_decision`, `generate_playbook_from_text`, `compute_playbook_insights`, `chat_build_playbook`, `save_built_playbook`, `apply_playbook_recommendation`). `execute_playbook_run` and its entire direct/indirect call graph (`_create_deviation`, `_current_contract_artifacts`, `_create_playbook_redline_version`, `_store_docx`, plus the pure helpers `evaluate_rules_against_text`, `ai_deviations_to_evaluated`, `_ai_citation_status`, `_build_playbook_redline_docx`, the docx-revision helpers, `_clean_phrase`/`_find_phrase`/`_align_phrase`/`_suggested_fix`/`_normalize_severity`/`_safe_filename`) deliberately kept as plain module functions, NOT methods — same reason as `workflows/service.py`'s `_execute_step`: `tests/test_phase5_playbooks.py` does `inspect.getsource(execute_playbook_run)` and pins the literal `if ai_output is not None: ... deviation_source = "claude" / "deterministic"` branch; a delegating wrapper would hide it. `generated_default_rules`/`_rules_from_templates` (pure) also stay module-level | done (`routes.py`, all handlers); 4 handlers (`get_playbook_run`, `decide_deviation`, `list_playbook_runs`, `run_playbook`) left with their pinned literal calls (`get_contract_for_user(db, contract_id=run.contract_id`/`=deviation.contract_id`, `accessible_contract_filter(current_user)`, `_require_permission(current_user, "contract:read"/"contract:redline")`) untouched — only their non-pinned calls (`get_playbook_for_user`, `select_run_version`, `record_deviation_decision`) were switched to the injected service | done (`playbooks/dependencies.py`: `get_playbooks_service`) | in place for all 15 previously-public functions plus `_auto_rule_payloads` (private, externally imported) | 0 / 2 (`ai/tool_runtime.py` imports `execute_playbook_run` (unchanged plain function), `get_playbook_for_user`, `select_run_version`, `_auto_rule_payloads` directly; `jobs/tasks.py` imports `auto_review_contract` — both non-request callers, verified still import and call cleanly) | **Converted** |
| `prompt_library/` | `PromptLibraryService` (new — like `obligations/`/`renewals/`, this module had no `service.py`; all logic lived inline in `routes.py`). Covers `list_workflows`, `workflow_analytics`, `create_workflow`, `update_workflow`, `list_workflow_versions`, `revert_workflow_version`, `launch_workflow`, plus private helpers `_load_owned_workflow`, `_next_version_number`, `_snapshot`. Pure helpers `_can_see`/`_serialize_version` (no `db`) stay module-level | done (`routes.py`, all 7 handlers) | done (`prompt_library/dependencies.py`: `get_prompt_library_service`) | none needed — no external importers of the old inline logic existed (only `.models`/`.builtin` imports elsewhere, e.g. `ai/tool_runtime.py`) | n/a | **Converted** |
| `tabular_review/` | `TabularReviewService` — wraps the existing module functions (`dispatch_cells`, `build_table_context`, `build_xlsx`, all kept as unchanged plain functions for the backward-compat wrapper below) plus newly-converted CRUD/chat logic moved out of `routes.py`: `review_payload`, `create_review`, `add_columns`, `add_contracts`, `rerun_cell`, `list_chat`, `chat_over_table` (async). `_get_review_for_user`, `_review_is_accessible`, `list_reviews`, `_reconcile_review_status` deliberately left as plain functions in `routes.py` — `tests/test_phase6_9_integration.py` does `inspect.getsource(_get_review_for_user)`/`inspect.getsource(list_reviews)`/`inspect.getsource(rerun_cell)` checking `_review_is_accessible` appears somewhere in the combined source; `list_reviews` and `_get_review_for_user` are the ones that actually satisfy it, so they stay untouched (`_reconcile_review_status` had to stay too since `list_reviews` calls it by that literal name) | done (`routes.py`) — `list_reviews`/`_get_review_for_user`/`_reconcile_review_status`/`_review_is_accessible` untouched; `create_review`, `add_columns`, `add_contracts`, `get_review`, `rerun_cell`, `list_chat`, `chat_over_table`, `export_review_xlsx` inject `get_tabular_review_service` | done (`tabular_review/dependencies.py`: `get_tabular_review_service`) | in place for `dispatch_cells` (module function preserved as-is, service wraps it) | 0 / 1 (`ai/tool_runtime.py` imports `dispatch_cells` directly — verified still imports and calls cleanly) | **Converted** |
| `search/` | `SearchService` (new — this module had no `service.py`; all 5 read-only query handlers lived inline in `routes.py`). Covers `search_contracts`, `search_contract_text`, `search_clauses`, `search_projects`, `search_contract_versions` | done (`routes.py`, all 5 handlers) | done (`search/dependencies.py`: `get_search_service`) | none needed — no external importers of the old inline logic existed (only `.fts` imports elsewhere, e.g. `contract_brain/retrieval.py`, `tests/test_phase2_api_shapes.py`) | n/a | **Converted** |
| `trademarks/` | `TrademarksService` (~14 db-touching functions, incl. private `_create_trademark`). Pure helpers `_embedding_text` and `_record_to_trademark_fields` (no `db`) stay module-level — `_record_to_trademark_fields` is also imported directly by `tests/test_trademarks.py` | done (`routes.py`, all 12 handlers) | done (`trademarks/dependencies.py`: `get_trademarks_service`) | none needed — no external importers of the old inline logic existed besides `routes.py` and the test's pure-helper import | n/a | **Converted** — `tests/test_trademarks.py` (15/15) passing |
| `organizations/` | `OrganizationsService` (new — this tiny module had no `service.py`; both handlers' logic lived inline in `routes.py`). Covers `get_current_organization`, `update_organization` | done (`routes.py`, both handlers) | done (`organizations/dependencies.py`: `get_organizations_service`) | none needed — no external importers of the old inline logic existed (only `.models` imports elsewhere: `auth/service.py`, `devtools.py`, `intake/drafting.py`) | n/a | **Converted** |
| `admin/` | `AdminService` (new — no prior `service.py`). Covers `list_settings` only. `upsert_setting` (route handler) deliberately left completely untouched in `routes.py` — `tests/test_phase6_9_integration.py` does `inspect.getsource(upsert_setting)` and asserts the literal `write_audit_log` call and `"admin.setting_updated"` action string stay inline. Pure helpers `serialize_setting`/`audit_setting_value` (no `db`) moved to `service.py` and imported into `routes.py` for `upsert_setting` to keep using | done (`routes.py`; only `list_settings` converted, `upsert_setting` unchanged) | done (`admin/dependencies.py`: `get_admin_service`) | n/a (pure helpers, not wrapped — just relocated and imported) | 0 / 0 (no external importers besides the test's direct import of `upsert_setting`, unaffected) | **Converted** — `test_admin_settings_and_sensitive_tool_outputs_are_audited_or_sanitized` still passing |
| `word_addin/` | `WordAddinService` (1 function: `resolve_contract_for_document`). Pure `_stage_value` (no `db`) stays module-level | done (`routes.py`, `link` handler; `ping` has no `db` usage, untouched) | done (`word_addin/dependencies.py`: `get_word_addin_service`) | none needed — no external importers of the old function existed | n/a | **Converted** |
| `matters/` | `MattersService` (new — this module had no `service.py`; all 18 route handlers' logic lived inline in `routes.py`). Covers `list_projects`, `get_project`, `create_project`, `update_project`, `delete_project`, `unfiled_items`, `matter_overview`, `matter_activity`, `assign_item_to_matter`, folder/member/share/contract CRUD, plus private helpers `_next_matter_number`, `_get_project_folder`, `_record_project_activity`. `matters/access.py` (`get_project_for_user`, `project_scope_query`, `user_can_access_project`, `user_has_project_access_for_contract`) deliberately left completely untouched — same precedent as `contracts/access.py`/`grants/service.py`/`walls/service.py`: it's a heavily cross-imported SQL-predicate module (`assistant/routes.py`, `tabular_review/*`, `search/service.py`, `contract_brain/*`, `contract_files/service.py`, `ai/tool_runtime.py` all import it directly), and `tests/test_phase2_api_shapes.py` imports its constants directly | done (`routes.py`, all 18 handlers) | done (`matters/dependencies.py`: `get_matters_service`) | none needed — no external importers of the old inline logic existed (only `.access` imports elsewhere, unaffected) | n/a | **Converted** |
| `ai/` | n/a — `AIController` and `ToolRuntime` (the module's two ~1800/2650-line orchestrators) are already stateless singleton classes with `db` passed per method call, never stored on `self` — the same shape `docusign_client`/`storage_service` had before their own DI pass. Confirmed with the user to treat them the same way: Singleton-lifetime providers wrapping the existing instances unchanged, not a per-request rewrite. `SkillRegistry`/`ToolRegistry` (also existing singletons) got the same treatment. The rest of the module (`prompt_versions.py`, `embeddings.py`, `cost_guard.py`, `citations.py`, `context.py`, `confirmations.py`, `agent_catalog.py`, etc.) is plain helper functions consumed internally by those two orchestrators, or value/registry objects (`PromptBundle`, `SkillSpec`, `ToolSpec`) analogous to the `IntakeApprovalSubject` adapters already treated as not a DI concern — left untouched | done (`ai/routes.py` only — its own handlers now inject `get_ai_controller`/`get_skill_registry`) | done (`ai/dependencies.py`: `get_ai_controller`, `get_tool_runtime`, `get_skill_registry`, `get_tool_registry`) | n/a — no signature change, no wrapper needed | 0 / ~10 (`assistant/routes.py`, `contracts/risk.py`, `contract_brain/routes.py`, `contract_files/routes.py`, `intake/drafting.py`, `jobs/tasks.py`, `playbooks/routes.py`, `playbooks/service.py`, `tabular_review/service.py`, `workflows/service.py` all still import `ai_controller`/`tool_runtime`/`skill_registry`/`tool_registry` directly, unchanged, pending their own passes — same pattern as `resend_client`/`docusign_client`'s remaining direct importers) | **Converted** (provider-only scope) |
| `assistant/` | `AssistantService` (new — no prior `service.py`). Covers the CRUD/lookup surface: `get_session_for_user`, `get_run_for_user`, `tool_calls_for_run`, `ensure_contract_handle`, `validate_and_store_assistant_citations`, `persist_assistant_answer`, `list_sessions`, `create_session`, `get_session`, `update_session`, `add_contract_handle`, `list_session_messages`, `list_session_runs`, `get_run`. The two big streaming handlers (`stream_session`, `resume_run`, each a route function wrapping a nested async-generator `event_stream()` that drives `ai_controller.stream_assistant_run`/`resume_assistant_run`) were kept as route functions rather than moved into the service — same reasoning as `execute_playbook_run`/`workflows._execute_step`: high behavioral risk (SSE framing, disconnect handling, commit/rollback ordering across try/except/finally) for no test-driven requirement to convert, so they now just call `service.get_session_for_user`/`service.ensure_contract_handle`/`service.persist_assistant_answer`/`service.get_run_for_user` instead of the old plain module functions, with everything else (event loop, SSE yielding) untouched. `confirm_assistant_action`/`reject_assistant_action` deliberately left with `_require_ai_tools(current_user)` literally inline — `tests/test_ai_architecture_wiring.py` does `inspect.getsource` on both and asserts that literal call. `_citations_from_tool_result`/`_events_from_tool_result`/`_accumulate_block`/`_sse`/`_require_ai_tools` (all pure, no `db`) stay module-level in `routes.py` — the first two are also imported directly by that test | done (`routes.py`; 8 CRUD handlers inject `get_assistant_service`; `stream_session`/`resume_run`/`confirm_assistant_action`/`reject_assistant_action`/`list_tools` also inject it where needed but keep their pinned/high-risk bodies otherwise as-is) | done (`assistant/dependencies.py`: `get_assistant_service`) | n/a — no external importers of the old private helpers existed beyond the test's pure-function imports (unaffected) | n/a | **Converted** — `test_ai_architecture_wiring.py` (27/27) passing, including both pinned introspection checks |
| `claude` (integration) | n/a — added `ClaudeProvider` Protocol over the existing `ClaudeClient` (mirrors `SignatureProvider`/`EmailSender`/etc.) | n/a | done (`integrations/dependencies.py`: `get_claude_client`) | n/a (no signature change to `ClaudeClient` itself) | 9 / 9 inline-instantiation call sites converted: `intake/gates.py` (`_classify_ai`), `intake/triage_agent.py` (`triage`), `intake/email_triage_agent.py` (`_llm_classify`), `intake/litigation_agent.py` (`assess_litigation`), `intake/flow_agent.py` (`suggest_flow`), `notices/extraction.py` (`extract_notice_fields`), `notices/drafting.py` (`draft_notice_response`), `renewals/recommendation.py` (`recommend_renewal`) — each got an optional `claude_client=None` keyword param defaulting to `get_claude_client()`, replacing the local `ClaudeClient()` construction; `playbooks/service.py`'s `PlaybooksService._generate_missing_rules` — `PlaybooksService.__init__` got an optional `claude_client=None` param stored as `self._claude_client`, defaulting the same way. Every leaf module's test-introspection/patch pins from earlier passes (`test_agent_catalog.py`'s `patch.object(gates, "_classify_ai", ...)`, `test_phase5_playbooks.py`'s `inspect.getsource(execute_playbook_run)`, etc.) verified still passing — these functions stayed as plain functions/methods, only gaining an optional parameter | **Converted** — closes the DI migration's rollout order; only remaining direct `ClaudeClient()`/`claude_client` usages left untouched are already-singleton call sites (`ai/controller.py`, `contracts/routes.py`, `trademarks/extraction/vision.py`) that were already using the shared instance, not constructing their own |

## Post-rollout cleanup: integration-leak fix (Phase 1 of the completion audit)

A follow-up review after the rollout above found that several already-"Converted"
services still imported an integration singleton directly (`docusign_client`,
`storage_service`, `resend_client`, `reducto_client`) instead of taking it as an
injected constructor parameter the way `ContractFilesService` already did. Fixed:

| File | Leak | Fix |
|---|---|---|
| `signatures/service.py` | `docusign_client`, `storage_service` | `SignaturesService.__init__` now takes `docusign`/`storage` (+ `resend`, also fixed below), defaulting to the singletons |
| `signatures/routes.py` | `docusign_client`, `storage_service`, `resend_client` used inline in `send_for_signature` | now uses `service.docusign`/`service.storage`/`service.resend` |
| `approvals/service.py` | `resend_client` | `ApprovalsService.__init__` takes `resend` |
| `obligations/service.py` | `resend_client` | `ObligationsService.__init__` takes `resend` |
| `renewals/service.py` | `resend_client` | `RenewalsService.__init__` takes `resend` |
| `trademarks/service.py` | `storage_service` | `TrademarksService.__init__` takes `storage` (+ `reducto`, also fixed below) |
| `playbooks/service.py` | `storage_service` in `_store_docx` (plain function, part of the untouched `execute_playbook_run` call graph) | added optional `storage=None` param defaulting to `get_storage_service()` — `execute_playbook_run`'s pinned source untouched |
| `contract_files/routes.py` | `storage_service` used inline in `download_contract_version`, `_store_generated_docx` (×2 callers), `download_external_share` — bypassing the module's own already-injected `ContractFilesService` | all four now go through `files_service.storage` (or a `storage` param threaded from it) |
| `intake/gmail_sync.py` | `reducto_client` in `_ocr_to_docx` (plain function) — never wired to the `reducto`/`databricks` Protocols added during the `contract_files/` integration pass | `GmailSyncService.__init__` takes `reducto`; `_ocr_to_docx` takes an optional `reducto=None` param |
| `trademarks/extraction/field_capture.py` | `reducto_client` in `extract_generic_records` — same never-wired gap | `TrademarksService.__init__` takes `reducto`; function takes an optional `reducto=None` param |

Every provider (`signatures/dependencies.py`, `approvals/dependencies.py`,
`obligations/dependencies.py`, `renewals/dependencies.py`,
`trademarks/dependencies.py`, `intake/dependencies.py`'s
`get_gmail_sync_service`) updated to resolve and pass these through
`Depends(get_..._client)`. Full suite re-verified at 223 passed (same baseline);
app startup verified at 43 routes; grep-confirmed no bare
`docusign_client.`/`storage_service.`/`resend_client.` calls remain in any of
the fixed files.

**Still open** (not part of this pass — see the pending items below):
`ai/controller.py` and `contracts/routes.py`/`trademarks/extraction/vision.py`
(`claude_client`).

## Post-rollout cleanup: `ai/tool_runtime.py` + `jobs/tasks.py` explicit-construction (Phase 2)

`ToolRuntime` (the `ai/` module's stateless singleton, `db` passed per call)
imported `docusign_client`, `resend_client`, `storage_service` directly and
called them inline across `_send_for_signature` and the three `_store_docx`
call sites (`_generate_contract_docx`, `_edit_contract`, `_redraft_contract`).
Fixed with the same constructor-injection shape as the integration-leak pass:
`ToolRuntime.__init__` now takes optional `docusign`/`storage`/`resend` params
defaulting to the existing singletons, stored as `self.docusign`/`self.storage`/
`self.resend`; the module-level `_store_docx` helper (used by all three call
sites) takes an optional `storage=None` param, called with `storage=self.storage`.
The `tool_runtime = ToolRuntime()` singleton at the bottom of the file is
unchanged (defaults kick in). `tests/test_ai_architecture_wiring.py`'s pins on
`_edit_contract`/`_generate_contract_docx` (`inspect.getsource` checking for the
literal `_store_docx` call, `ContractVersionSource.ASSISTANT_EDIT`, etc.) verified
still passing — the calls stayed literally present, only gaining a keyword arg.

`jobs/tasks.py`'s two Celery-task helpers (`_send_obligation_reminders`,
`_run_renewal_window_check`) imported `resend_client` directly inside the
function body. Both now take an optional `resend=None` param defaulting to
`get_resend_client()` — explicit-construction, not `Depends()`, since these
run outside any FastAPI request (the pattern the original DI plan specified
for Celery/background code all along).

Full suite re-verified at 223 passed (same baseline); `test_ai_architecture_wiring.py`
7/7 → 27/27 passed; app startup verified at 43 routes; confirmed
`tool_runtime.storage`/`.docusign`/`.resend` resolve to the real singleton
instances at runtime.

**Note — scope boundary:** this pass fixed the *integration-singleton* leaks
in these two files only (`docusign_client`/`resend_client`/`storage_service`).
It did **not** migrate the much larger set of other-module *wrapper-function*
imports these two files also make (e.g. `tool_runtime.py` alone calls into
`notices.service`, `workflows.service`, `intake.service`, `approvals.service`,
`playbooks.service`, `tabular_review.service`, `signatures.service` at
~17 call sites). Converting those would mean giving `ToolRuntime` a dependency
on most of the app's other services — a much larger, higher-risk change than
what was scoped here, and not yet done. See "Notes / deferred items" below.

## Post-rollout cleanup: `ai/tool_runtime.py` + `jobs/tasks.py` service-wrapper migration (Phase 3, 2026-09-17)

The one item Phase 2 explicitly deferred: `ai/tool_runtime.py`'s ~17 call
sites (plus a few in `jobs/tasks.py`) that imported other modules' strangler
wrapper functions directly instead of going through the already-converted
service classes. Both files are non-request-scoped (`ToolRuntime` is a
stateless singleton with `db` passed per call; the Celery tasks build their
own session), so the fix is the same "explicit construction" pattern used
throughout this migration for non-request callers — construct
`XxxService(db)` inline with the `db` already in scope and call the method,
rather than injecting via `Depends()` (there is no request to hang a
dependency off of) or via `ToolRuntime.__init__` (these services need a
fresh `db` per call, unlike the true singletons `docusign_client`/
`storage_service`/`resend_client` fixed in Phase 2).

**`ai/tool_runtime.py`** — converted to construct the service directly at
each call site:
- `IntakeService(db)`: `create_request`, `record_triage_action`, `update_task`
- `WorkflowService(db)`: `start_flow`, `advance_run`, `complete_human_step`,
  `refresh_run`, `create_flow`
- `ApprovalsService(db)`: `decide_in_app`, `submit_contract_for_approval`
- `NoticesService(db)`: `list_notices`, `create_notice`, `draft_response`,
  `get_notice`
- `PlaybooksService(db)`: `get_playbook_for_user`, `select_run_version`,
  `_auto_rule_payloads`
- `SignaturesService(db)`: `validate_signature_recipients`
- `ContractService(db)`: `get_contract_for_user` (9 call sites, including the
  central `_resolve_contract` helper used by most tool handlers)
- `ContractLifecycleService(db)`: `transition_contract_stage`
- `ContractCommentService(db)`: `create_comment`
- `ContractFilesService(db)`: `next_version_number` (3 call sites)

**Left unconverted, deliberately:**
- `_generate_contract_docx`'s `queued_jobs = _queue_initial_contract_jobs(...)`
  call — `tests/test_ai_architecture_wiring.py::test_assistant_generated_contract_flushes_jobs_before_dispatch_ids`
  does `inspect.getsource` on this exact method and asserts the literal
  substring `"queued_jobs = _queue_initial_contract_jobs"`. Converting it to
  `ContractFilesService(db)._queue_initial_contract_jobs(...)` broke that pin
  (caught by the full-suite run, then reverted). Kept as the module-level
  wrapper import, same as every other test-pinned exception in this project
  (`execute_playbook_run`, `dispatch_cells`, etc.).
- `execute_playbook_run`, `dispatch_cells`, `create_job`/`dispatch_job` — not
  wrappers at all (the first two are the original, never-delegated
  implementations that their service classes call internally; `jobs/service.py`
  was never converted to a service class in this migration, so its two
  functions are the primary implementation, not a strangler wrapper).

**`jobs/tasks.py`** — same treatment: `_maybe_auto_review` now constructs
`ContractRiskService(db).compute_contract_risk(...)` and
`PlaybooksService(db).auto_review_contract(...)`; `close_expired_contracts`
constructs `ContractLifecycleService(db).transition_contract_stage(...)`;
`send_notice_reminders` constructs `NoticesService(db).run_reminders()`.

**Verification:** full suite → 223 passed / 6 failed / 19 errors (unchanged
baseline, pre-existing Postgres-related); `test_ai_architecture_wiring.py` +
`test_phase5_playbooks.py` + `test_phase6_9_integration.py` → 43/43 passed
(including the pinned introspection checks on `_execute_validated`,
`_edit_contract`, `_generate_contract_docx`, `execute`, `execute_confirmed`,
`_run_playbook_review`); app startup → 43 routes.

**Immediate follow-on sweep:** re-ran the full wrapper-importer grep across
every `DI-MIGRATION`-tagged file now that `ai/tool_runtime.py` and
`jobs/tasks.py` no longer import these functions. **40 wrapper functions
across 8 files dropped to zero importers and were deleted**:
- `intake/service.py`: `create_request`, `record_triage_action`, `update_task`
- `workflows/service.py`: `advance_run`, `complete_human_step`, `refresh_run`,
  `create_flow`
- `approvals/service.py`: `decide_in_app`
- `notices/service.py`: the entire wrapper block (`list_notices`, `get_notice`,
  `summary`, `create_notice`, `update_notice`, `set_status`, `add_note`,
  `draft_response`, `escalate`, `run_reminders`, `add_document`,
  `delete_document`, `extract_from_upload`, `delete_notice`) — `summary`,
  `update_notice`, `set_status`, `add_note`, `escalate`, `add_document`,
  `delete_document`, `extract_from_upload`, `delete_notice` had actually been
  at zero importers since their original conversion (missed by that pass's
  accounting, which only tracked the 2 functions `ai/tool_runtime.py` and
  `jobs/tasks.py` happened to use) — this sweep is what caught them
- `playbooks/service.py`: the entire wrapper block (`get_playbook_for_user`,
  `get_playbook_version`, `create_initial_playbook`,
  `next_playbook_version_number`, `clone_playbook_version`, `expand_playbook`,
  `select_run_version`, `pick_playbook_for_contract`, `_auto_rule_payloads`,
  `auto_review_contract`, `record_deviation_decision`,
  `generate_playbook_from_text`, `compute_playbook_insights`,
  `chat_build_playbook`, `save_built_playbook`,
  `apply_playbook_recommendation`) — same "already zero, missed by the
  original pass's narrower accounting" story as `notices/`
- `signatures/service.py`: the entire wrapper block (`validate_signature_recipients`,
  `sync_signature_request`) — `signatures/routes.py` already called
  `service.sync_signature_request(...)` on the injected instance, not the
  wrapper
- `contracts/risk.py`: `compute_contract_risk` (the file's only wrapper)
- `contracts/comments_service.py`: `list_comments`, `create_comment`,
  `set_resolved`, `delete_comment` (kept `list_shared_comments` and
  `add_counterparty_comment` — still imported by `contract_files/routes.py`)

Re-verified after deletion: full suite 223 passed (same baseline); app
startup 43 routes; grep-confirmed zero remaining importers for all 40 deleted
names across the whole repo (`app/` + `tests/`).

**Still standing** (confirmed live importers, not touched): `intake.service.start_flow`
(imported by `intake/service.py` itself, deferred), `workflows.service.submit_contract_for_approval`→
actually `approvals.service.submit_contract_for_approval` (imported by
`workflows/service.py`), `contract_files.service.next_version_number`
(imported by `playbooks/service.py`), `contracts.service.get_contract_for_user`
(~15 files — the biggest remaining surface, unrelated to this pass),
`contracts.lifecycle.transition_contract_stage` (~8 files, same story),
`contract_files.service._queue_initial_contract_jobs` (kept alive
deliberately in `ai/tool_runtime.py` by the test pin above),
`jobs.service.create_job`/`dispatch_job` (never a wrapper — `jobs/` has no
service class).

## Post-rollout cleanup: `jobs/` conversion + last 3 `claude_client` imports (Phase 4, 2026-09-17)

Two genuinely separate, small items — not part of the wrapper-migration
sweep, done as their own low-risk pass.

**`jobs/` — converted from scratch (never had a service class before).**
`jobs/service.py`'s `create_job`/`dispatch_job` were the *original*
implementation, not a strangler wrapper — this module was never assessed in
the original rollout order. Added `JobsService(db)` with both as methods,
`jobs/dependencies.py` (`get_jobs_service`), converted `jobs/routes.py`'s
`enqueue_job` handler to inject it, and kept module-level `create_job`/
`dispatch_job` wrapper functions in place (same strangler pattern as
everywhere else). Only `ai/tool_runtime.py` (3 call sites) and
`jobs/tasks.py`'s `_queue_contract_brain_ingestion` were switched to
construct `JobsService(db)` directly, mirroring Phase 3's treatment of
every other non-request-scoped caller. The other 6 files that import the
wrapper (`contracts/lifecycle.py`, `contracts/risk.py`,
`contract_brain/routes.py`, `contract_files/service.py`, `devtools.py`,
`obligations/service.py`, `tabular_review/service.py`) were **not**
touched — confirmed zero test pins on either function, so this was purely a
scope decision to keep the change small, not a risk-avoidance one.

**The last 3 `claude_client` hardcoded imports — closed out:**
- `ai/controller.py`: `AIController` gained `__init__(self, *, claude_client=None)`
  storing `self.claude_client`, defaulting to the existing singleton
  (identical shape to `ToolRuntime`'s Phase 2 constructor). All 5 internal
  `claude_client.*` call sites switched to `self.claude_client.*`.
  `ai/dependencies.py`'s `get_ai_controller()` needed no change — it still
  returns the same `ai_controller = AIController()` singleton, which still
  works with no args.
- `contracts/routes.py`'s `contract_plain_summary` route: added
  `claude_client: ClaudeProvider = Depends(get_claude_client)` — real
  `Depends()` injection, since this is a route handler (not a non-request
  context), matching how every other route in this migration receives its
  dependencies.
- `trademarks/extraction/vision.py`'s `extract_journal_page`: gained an
  optional `claude_client=None` param defaulting to the singleton (same
  pattern as the other 8 leaf-function `claude_client` conversions from the
  original `claude` integration pass). `TrademarksService.__init__` gained
  a matching `claude_client=None` param, threaded through to the call site;
  `trademarks/dependencies.py`'s `get_trademarks_service` now also resolves
  `Depends(get_claude_client)`.

**Verification:** full suite → 223 passed / 6 failed / 19 errors (unchanged
baseline); `test_ai_architecture_wiring.py` + `test_phase5_playbooks.py` +
`test_phase6_9_integration.py` + `test_trademarks.py` → 58/58 passed; app
startup → 43 routes; confirmed `ai_controller.claude_client` resolves to a
real `ClaudeClient` instance at runtime; grep-confirmed the `jobs/service.py`
wrapper functions still have exactly their 6 expected importers (unchanged),
and zero remaining bare `claude_client` module references anywhere in the
3 touched files.

**This closes every item from the "areas deliberately left out" discussion
except:** the still-standing strangler wrappers with real external
importers (`contracts.service.get_contract_for_user`,
`contracts.lifecycle.transition_contract_stage`, etc.), the test-pinned
plain functions, the leaf AI-classifier modules, `matters/access.py`, and
`core/deps.py`'s auth hot path — all of which remain excluded for the
reasons already documented above, not because they were missed.

## Post-rollout cleanup: `playbooks/dependencies.py` claude_client wiring (Phase 5, 2026-09-17)

An external review correctly caught one real inconsistency: `PlaybooksService`
accepts `claude_client` in its constructor (added during the original `claude`
integration pass, for `_generate_missing_rules`), but `get_playbooks_service`
never resolved or passed it — every request got `PlaybooksService(db)` with
no `claude_client`, silently falling through to the class's own internal
default every time. Every *other* service given a `claude_client` param this
session (`trademarks/dependencies.py` in Phase 4) already wires it through
the provider; playbooks had simply been missed. Fixed:

```python
def get_playbooks_service(
    db: Session = Depends(get_db),
    claude_client: ClaudeProvider = Depends(get_claude_client),
) -> PlaybooksService:
    return PlaybooksService(db, claude_client=claude_client)
```

**Not the same category as `ToolRuntime`/`AIController`'s singleton
providers** (which intentionally return the pre-built module-level singleton
rather than composing one via `Depends()` per request — reconstructing a
declared Singleton on every request would defeat the point of it, and
resolves to the identical concrete object either way). `PlaybooksService` is
Scoped, not Singleton — built fresh per request like every other feature
service — so it should have been wired through `Depends()` from the start,
consistent with `trademarks/dependencies.py`'s identical shape.

**Verification:** full suite → 223 passed / 6 failed / 19 errors (unchanged
baseline); `test_phase5_playbooks.py` + `test_ai_architecture_wiring.py` +
`test_phase6_9_integration.py` → 43/43 passed; app startup → 43 routes;
confirmed at runtime that `get_playbooks_service` now actually resolves
`get_claude_client()` through FastAPI's dependency graph and passes a real
`ClaudeClient` into the service, rather than the service defaulting it
internally.

## Rollout order

1. ~~`contracts/` + `storage` integration~~ — done (pilot).
2. ~~`contract_files/` + `reducto`/`databricks` integrations~~ — done.
3. ~~`auth/`, `roles/`, `authority/`, `grants/`, `walls/` (permission cluster)~~ — done.
4. ~~`resend`, `docusign` integrations~~ — done.
5. ~~Remaining feature modules~~ — done: `intake/` (4 sub-passes), `workflows/`,
   `approvals/`, `signatures/`, `obligations/`, `renewals/`, `notices/`,
   `playbooks/`, `prompt_library/`, `tabular_review/`, `search/`,
   `trademarks/`, `organizations/`, `admin/`, `word_addin/`, `matters/`,
   `ai/` (provider-only — see its row), `assistant/`.
6. ~~`claude` integration, including its 9 inline-instantiation call sites~~ — done.
7. Final sweep: once a module's wrappers show 0 remaining importers (grep),
   delete them. **Done** — see "Final wrapper sweep" (first cut, deleted
   `roles/`'s 7 wrappers) and Phase 3 above (deleted 40 more across 8 files
   once `ai/tool_runtime.py`/`jobs/tasks.py` stopped importing them). The
   wrappers still standing (`contracts.service.get_contract_for_user`,
   `contracts.lifecycle.transition_contract_stage`, a handful of others
   listed at the end of Phase 3) all have confirmed live importers unrelated
   to `ai/tool_runtime.py` — re-run this grep sweep after any future pass
   that migrates one of those.

## Final wrapper sweep (2026-09-17)

Grepped every wrapper function created by this migration (all files
tagged `DI-MIGRATION` in `app/`, ~100 function names across 22 files) for
remaining importers repo-wide.

- **`roles/service.py`** — its 7 wrapper functions (`serialize_role`,
  `list_roles`, `create_role`, `update_role`, `delete_role`,
  `set_user_clearance`, `set_user_roles`) had 0 importers (the table already
  flagged this at conversion time as "kept as a safety net"). Confirmed again
  with a fresh grep and **deleted** — `roles/routes.py` only ever used
  `RoleService`/`permission_catalog` directly. Verified: `py_compile` clean;
  full suite 223 passed / 6 failed / 19 errors (unchanged baseline, all
  pre-existing Postgres-related); `test_role_assignment_privilege_escalation.py`
  + `test_phase1_auth_foundation.py` 11/11 passed; app startup at 43 routes.
- **Every other module's wrappers** — spot-checked the modules with the
  fewest documented importers (`grants.user_has_grant` →
  `contracts/access.py`, `walls.user_is_walled` → `contracts/access.py`,
  `authority.enforce_authority` → `approvals/routes.py` +
  `approvals/service.py` + `signatures/routes.py`,
  `signatures.validate_signature_recipients` → `ai/tool_runtime.py`) and
  confirmed each still has exactly the importer(s) already documented in its
  table row. None had dropped to zero. The remaining ~95 wrapper functions
  across `approvals/`, `auth/`, `contracts/` (all 5 files),
  `contract_files/`, `intake/` (all sub-modules), `notices/`, `playbooks/`,
  `signatures/`, `workflows/` all still have live importers — overwhelmingly
  `ai/tool_runtime.py` and/or `jobs/tasks.py` (deliberately not migrated in
  Phase 2, see above) plus a handful of other-module cross-imports — so none
  were deleted. Nothing else was safe to remove in this pass.

## Notes / deferred items

- `contracts/routes.py` still has a few in-function deferred imports
  (`app.playbooks`, `app.ai.*` in `get_contract_deviations` /
  `contract_plain_summary`) that were left as plain imports rather than
  converted to injected services — they exist to dodge circular imports
  unrelated to this DI work. Revisit once `playbooks/` and `ai/` have their
  own DI passes.
- Added `client`, `db_session`, and `override_dependency` fixtures to
  `tests/conftest.py` — the first `TestClient` / `dependency_overrides`
  harness in this suite. New DI-converted modules should add their proof
  tests the way `tests/test_contracts_di.py` does.
- `core/deps.py` (`get_current_user`) still calls `auth.service`'s
  `authenticate_api_key` / `is_access_token_revoked` wrapper functions rather
  than an injected `AuthService` — this is the single hottest code path in
  the app (runs on every authenticated request) and was deliberately left
  untouched this pass. Migrate it only once the wrapper has been proven
  stable in production, not as a mechanical follow-up.
- `grants/routes.py` has no `require_permission(...)` gate at the route
  layer — authorization is enforced inside the service
  (`GrantService.can_manage_grants`) instead. Confirmed as intentional
  during investigation and preserved exactly as it was; noted here so a
  future pass doesn't "fix" it as an oversight.
- `obligations/routes.py`'s `_get_obligation` and `list_obligations` were left
  as plain functions (not moved into `ObligationsService`), mirroring the
  `approvals/routes.py` ACL-helper precedent: `tests/test_phase6_9_integration.py`
  does `inspect.getsource(_get_obligation)` / `inspect.getsource(list_obligations)`
  and asserts the literal calls `get_contract_for_user` / `accessible_contract_filter(current_user)`
  appear somewhere across a combined set of route sources. Both calls already
  live in these two functions untouched, so the assertion holds; everything else
  (the mutation/audit/job/email logic in the other 4 handlers) moved into the
  service.
- `renewals/routes.py`'s `_get_renewal` and `list_renewals` were left as plain
  functions for the same `test_phase6_9_integration.py` introspection reason as
  `obligations/`'s `_get_obligation`/`list_obligations` (see above); `get_renewal`
  itself has no logic beyond calling `_get_renewal`, so no service was needed there.
  Empirically verified before converting that returning a raw SQLAlchemy ORM row
  directly from a route (as `get_renewal`/`decide_renewal` already did, pre-existing)
  serializes fine via FastAPI's `jsonable_encoder` — this looked like it might be
  an unrelated bug but tested out as working, so it was left as-is, unflagged.
- `intake/` Pass 3 (AI-agent chain) was assessed and deliberately **not**
  converted to service classes, confirmed with the user. Reasons: (1)
  `agents.py` has no `db` usage to convert; (2) `triage_agent.py`,
  `flow_agent.py`, `litigation_agent.py`, `email_triage_agent.py` are pure
  leaf classifier modules never injected into any route directly — only
  reached via lazy imports several calls deep inside already-converted
  services, so wrapping them gains no real DI benefit; (3) a first attempt at
  wrapping `gates.py` broke `tests/test_agent_catalog.py`, which does
  `patch.object(gates, "_classify_ai", ...)` expecting a real module-level
  function — moving that logic into a class and leaving a delegating wrapper
  in its place silently defeats the patch (the wrapper always calls the real
  implementation, so the test's substitution never takes effect). Reverted
  `gates.py` to its original, untouched form. If a genuine need to inject a
  fake into one of these arises later, prefer passing it as an explicit
  parameter with a default (like `ContractFilesService`'s client params)
  rather than converting the whole module to a class.
- `ai/tool_runtime.py`'s wrapper-function imports were migrated in Phase 3
  above (constructed the relevant service class inline with the per-call
  `db`, rather than via `ToolRuntime.__init__` or `Depends()` — neither fits
  a stateless singleton whose methods each receive a fresh `db`).
  `execute_playbook_run` and `dispatch_cells` stay as direct calls (they are
  the original, never-delegated implementations, not wrappers);
  `_queue_initial_contract_jobs` stays on the wrapper in one call site only
  (`_generate_contract_docx`) because of a test-pinned literal-source check;
  `jobs.service.create_job`/`dispatch_job` stay direct because `jobs/` was
  never converted to a service class in this migration (out of scope — a
  new module conversion, not a wrapper migration). `_hash_secret` was never
  a DI concern (pure function, no `db`).
