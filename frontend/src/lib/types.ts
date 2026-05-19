// Types mirroring the FastAPI backend schemas (/api/v1).

export type ID = string;
export type ISODate = string;
export type ISODateTime = string;

// ---------------------------------------------------------------------------
// Auth / users / org
// ---------------------------------------------------------------------------

export type UserStatus =
  | "pending_approval"
  | "active"
  | "rejected"
  | "suspended"
  | "deactivated";

export interface UserResponse {
  id: ID;
  org_id: ID;
  email: string;
  full_name: string;
  status: UserStatus;
  roles: string[];
  active_role_id: ID | null;
  active_role_name: string | null;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user_id: ID;
  org_id: ID;
}

export interface RegistrationResponse {
  status: string;
  message: string;
  user: UserResponse | null;
}

export interface OrganizationResponse {
  id: ID;
  name: string;
  slug: string;
  allowed_domains: string[];
  default_role_name: string;
}

export interface UserInvitationResponse {
  id: ID;
  email: string;
  role_name: string;
  expires_at: ISODateTime;
  accepted_at: ISODateTime | null;
  revoked_at: ISODateTime | null;
  token: string | null;
}

export interface OrgJoinRequestResponse {
  id: ID;
  org_id: ID | null;
  email: string;
  full_name: string;
  requested_domain: string | null;
  message: string | null;
  status: string;
  decision_reason: string | null;
  invitation_token: string | null;
}

export interface ApiKeyResponse {
  id: ID;
  name: string;
  last_used_at: ISODateTime | null;
  revoked_at: ISODateTime | null;
  created_at: ISODateTime;
  api_key: string | null;
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export type ProjectType =
  | "general"
  | "contract_review"
  | "due_diligence"
  | "regulatory";

export interface ProjectResponse {
  id: ID;
  org_id: ID;
  name: string;
  description: string | null;
  project_type: ProjectType;
  owner_user_id: ID;
  metadata_json: Record<string, unknown>;
}

export interface ProjectFolderResponse {
  id: ID;
  project_id: ID;
  parent_folder_id: ID | null;
  name: string;
}

export interface ProjectMemberResponse {
  id: ID;
  project_id: ID;
  user_id: ID;
  role: string;
}

export interface ProjectShareResponse {
  id: ID;
  project_id: ID;
  shared_with_user_id: ID;
  access_level: "read" | "update" | "share";
  expires_at: ISODateTime | null;
  revoked_at: ISODateTime | null;
}

export interface ProjectContractResponse {
  id: ID;
  project_id: ID;
  contract_id: ID;
  folder_id: ID | null;
}

// ---------------------------------------------------------------------------
// Contracts
// ---------------------------------------------------------------------------

export type ContractLifecycleStage =
  | "intake"
  | "drafting"
  | "ai_review"
  | "internal_review"
  | "counterparty_review"
  | "approval_pending"
  | "approved"
  | "signature_pending"
  | "active"
  | "renewal_due"
  | "closed"
  | "archived";

export type RiskLevel = "low" | "medium" | "high" | "critical";

export type ContractVersionSource =
  | "upload"
  | "manual_upload"
  | "assistant_generated"
  | "assistant_edit"
  | "playbook_redline"
  | "counterparty_revision"
  | "approved_clean"
  | "signed"
  | "restored"
  | "template_generated";

export interface ContractResponse {
  id: ID;
  org_id: ID;
  title: string;
  contract_type: string | null;
  lifecycle_stage: ContractLifecycleStage;
  owner_user_id: ID;
  counterparty_name: string | null;
  jurisdiction: string | null;
  risk_level: string | null;
  value_amount: number | null;
  currency: string | null;
  effective_date: ISODate | null;
  expiration_date: ISODate | null;
  current_contract_file_id: ID | null;
  current_authoritative_version_id: ID | null;
  metadata_json: Record<string, unknown>;
}

export interface ContractUploadResponse {
  contract: ContractResponse;
  contract_file_id: ID;
  contract_version_id: ID;
  text_snapshot_id: ID | null;
  extraction_method: string;
  extraction_quality_score: number;
  queued_jobs: string[];
}

export interface ContractFileResponse {
  id: ID;
  contract_id: ID;
  current_version_id: ID | null;
  file_label: string;
}

export interface ContractVersionResponse {
  id: ID;
  contract_id: ID;
  contract_file_id: ID;
  version_number: number;
  storage_object_id: ID;
  text_snapshot_id: ID | null;
  source: ContractVersionSource;
  change_summary: string | null;
  is_authoritative: boolean;
}

export interface ContractTextSnapshotResponse {
  id: ID;
  contract_id: ID;
  contract_version_id: ID;
  extraction_method: string;
  extraction_quality_score: number;
  ocr_provider: string | null;
  validation_status: string | null;
  text?: string;
}

export interface ContractEditResponse {
  id: ID;
  contract_id: ID;
  contract_version_id: ID;
  edit_type: string;
  status: "proposed" | "accepted" | "rejected";
  original_text: string | null;
  replacement_text: string | null;
  rationale: string | null;
  citation: unknown[] | null;
}

export interface ContractShareResponse {
  id: ID;
  contract_id: ID;
  contract_version_id: ID | null;
  access_mode: "view_only" | "download_allowed";
  expires_at: ISODateTime | null;
  revoked_at: ISODateTime | null;
  download_allowed: boolean;
}

export interface ContractShareCreateResponse {
  share: ContractShareResponse;
  token: string;
}

export interface ContractActivityResponse {
  id: ID;
  event_type: string;
  title: string;
  details: Record<string, unknown> | null;
  request_id: ID | null;
  job_id: ID | null;
  skill_run_id: ID | null;
  assistant_run_id: ID | null;
  ai_call_id: ID | null;
  created_at: ISODateTime;
}

export interface ContractStageHistoryResponse {
  id: ID;
  contract_id: ID;
  from_stage: string | null;
  to_stage: string;
  reason: string | null;
  changed_by_user_id: ID | null;
  changed_at: ISODateTime;
  override_used: boolean;
}

export interface LifecycleOptionsResponse {
  current_stage: ContractLifecycleStage;
  allowed_transitions: ContractLifecycleStage[];
}

export interface ContractHubResponse {
  contracts_by_stage: Record<string, number>;
  contracts_by_risk: Record<string, number>;
  total_contract_versions: number;
  widgets: {
    pending_approvals: number;
    pending_signatures: number;
    upcoming_renewals: number;
    overdue_obligations: number;
    top_deviated_clauses: { clause_type: string; count: number }[];
    average_cycle_time_days: number | null;
    counterparty_friction: { counterparty_name: string; count: number }[];
    recent_activity: {
      id: ID;
      resource_id: ID;
      event_type: string;
      title: string;
      details: Record<string, unknown>;
      created_at: ISODateTime;
    }[];
  };
}

// ---------------------------------------------------------------------------
// Assistant
// ---------------------------------------------------------------------------

export type AssistantSessionType =
  | "general"
  | "project"
  | "contract"
  | "tabular_review";

export interface AssistantSession {
  id: ID;
  org_id: ID;
  session_type: string;
  title: string | null;
  project_id: ID | null;
  contract_id: ID | null;
  tabular_review_id: ID | null;
  status: "active" | "archived";
  created_by_user_id: ID;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface AssistantContractHandle {
  id: ID;
  session_id: ID;
  contract_id: ID;
  handle: string;
}

export interface Citation {
  type?: string;
  contract_id?: ID;
  text_snapshot_id?: ID;
  start_char?: number;
  end_char?: number;
  excerpt?: string;
  quote?: string;
  label?: string | null;
  validation_status?: "valid" | "invalid" | "needs_review";
  similarity_score?: number | null;
}

export interface AssistantMessage {
  id: ID;
  session_id: ID;
  org_id: ID;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
  created_by_user_id: ID | null;
}

export interface AssistantRun {
  id: ID;
  session_id: ID;
  org_id: ID;
  status: "running" | "succeeded" | "failed" | "waiting_confirmation";
  user_message_id: ID | null;
  assistant_message_id: ID | null;
  model: string | null;
  current_tool_iteration: number;
  error_message: string | null;
  created_at: ISODateTime;
  completed_at: ISODateTime | null;
}

export interface AssistantToolCall {
  id: ID;
  session_id: ID;
  assistant_run_id: ID;
  org_id: ID;
  tool_name: string;
  category: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  status: string;
  confirmation_required: boolean;
  confirmation_id: ID | null;
  resource_type: string | null;
  resource_id: ID | null;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  error_message: string | null;
}

export interface ToolInfo {
  name: string;
  description: string;
  category: string;
  permission: string;
  confirmation_policy: "none" | "required";
  feature_flag: string | null;
  enabled_by_default: boolean;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
}

export type AssistantStreamEventType =
  | "session_started"
  | "message_delta"
  | "tool_started"
  | "tool_finished"
  | "citation"
  | "contract_generated"
  | "tracked_change_created"
  | "confirmation_required"
  | "error"
  | "done";

export interface AssistantStreamEvent {
  type: AssistantStreamEventType;
  data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// AI skills
// ---------------------------------------------------------------------------

export interface SkillInfo {
  name: string;
  version: string;
  execution_mode: string;
  prompt_key: string;
  prompt_version: string;
  output_schema_name: string;
  required_permission: string | null;
  feature_flag: string | null;
  enabled_by_default: boolean;
  requires_citations: boolean;
  allows_mutation: boolean;
}

export interface AISkillRunResponse {
  id: ID;
  org_id: ID;
  skill_name: string;
  skill_version: string;
  execution_mode: string;
  status: string;
  resource_type: string | null;
  resource_id: ID | null;
  validation_status: string | null;
  error_message: string | null;
}

export interface AIPromptVersionResponse {
  id: ID | null;
  prompt_key: string;
  version: string;
  status: string;
  prompt_hash: string;
  description: string | null;
  model_name: string | null;
  model_config_hash: string | null;
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

export interface Workflow {
  id: ID;
  org_id: ID;
  name: string;
  workflow_type: string;
  visibility: string;
  description: string | null;
  definition: Record<string, unknown>;
  created_at: ISODateTime;
  created_by_user_id: ID;
  practice?: string | null;
  is_builtin?: boolean;
}

// ---------------------------------------------------------------------------
// Playbooks
// ---------------------------------------------------------------------------

export interface PlaybookResponse {
  id: ID;
  name: string;
  description: string | null;
  status: "draft" | "published" | "archived";
  current_version_id: ID | null;
}

export interface PlaybookVersionResponse {
  id: ID;
  playbook_id: ID;
  version_number: number;
  status: "draft" | "published" | "archived";
  summary: string | null;
  source_metadata: Record<string, unknown> | null;
}

export interface PlaybookRuleResponse {
  id: ID;
  playbook_version_id: ID;
  clause_type: string;
  rule_type: string;
  preferred_position: string | null;
  fallback_position: string | null;
  prohibited_language: string | null;
  required_language: string | null;
  risk_level: RiskLevel;
  rationale: string | null;
  escalation_role: string | null;
  approval_required: boolean;
  sample_clause: string | null;
  negotiation_guidance: string | null;
}

export interface PlaybookRunResponse {
  id: ID;
  playbook_id: ID;
  playbook_version_id: ID;
  contract_id: ID;
  contract_version_id: ID | null;
  status: string;
  validation_status: string | null;
  model_name: string | null;
  error_message: string | null;
  validated_output: Record<string, unknown> | null;
}

export interface PlaybookDeviationResponse {
  id: ID;
  playbook_run_id: ID;
  playbook_rule_id: ID | null;
  contract_id: ID;
  severity: RiskLevel;
  clause_type: string;
  issue: string;
  suggested_fix: string | null;
  citation: Record<string, unknown> | null;
  status: string;
}

export interface PlaybookRunDetailResponse extends PlaybookRunResponse {
  deviations: PlaybookDeviationResponse[];
}

export interface PlaybookDecisionResponse {
  id: ID;
  playbook_deviation_id: ID;
  decision: string;
  rationale: string | null;
  decided_by_user_id: ID;
  created_at: ISODateTime;
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export interface ApprovalRequest {
  id: ID;
  org_id: ID;
  contract_id: ID;
  contract_version_id: ID | null;
  status: "pending" | "approved" | "rejected" | "cancelled";
  requested_by_user_id: ID;
  approver_user_id: ID | null;
  approver_role: string | null;
  due_at: ISODateTime | null;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface ApprovalRoutingRule {
  id: ID;
  org_id: ID;
  name: string;
  priority: string;
  criteria: Record<string, unknown>;
  approver_role: string | null;
  approver_user_id: ID | null;
  is_active: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

// ---------------------------------------------------------------------------
// Signatures
// ---------------------------------------------------------------------------

export interface SignatureRequest {
  id: ID;
  org_id: ID;
  contract_id: ID;
  contract_version_id: ID;
  provider: string;
  provider_envelope_id: string | null;
  status: "draft" | "sent" | "delivered" | "completed" | "declined" | "voided";
  sent_by_user_id: ID | null;
  sent_at: ISODateTime | null;
  completed_at: ISODateTime | null;
  signed_contract_version_id: ID | null;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  recipients?: SignatureRecipient[];
}

export interface SignatureRecipient {
  id?: ID;
  name: string;
  email: string;
  role: string | null;
  routing_order?: number;
  status?: string;
}

// ---------------------------------------------------------------------------
// Obligations / renewals
// ---------------------------------------------------------------------------

export type ObligationStatus =
  | "open"
  | "due_soon"
  | "overdue"
  | "completed"
  | "cancelled";

export interface Obligation {
  id: ID;
  org_id: ID;
  contract_id: ID;
  contract_version_id: ID | null;
  owner_user_id: ID | null;
  responsible_party: string | null;
  obligation_type: string | null;
  description: string;
  due_date: ISODate | null;
  recurrence: string | null;
  status: ObligationStatus;
  source_citation: Record<string, unknown> | null;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface RenewalEvent {
  id: ID;
  org_id: ID;
  contract_id: ID;
  contract_version_id: ID | null;
  expiration_date: ISODate | null;
  notice_date: ISODate | null;
  renewal_window_starts_at: ISODate | null;
  owner_user_id: ID | null;
  decision: "undecided" | "renew" | "terminate" | "renegotiate";
  decision_note: string | null;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

// ---------------------------------------------------------------------------
// Contract Brain
// ---------------------------------------------------------------------------

export type BrainScope = "contract" | "project" | "portfolio";

export interface BrainQuery {
  id: ID;
  org_id: ID;
  query_scope: BrainScope;
  question: string;
  contract_id: ID | null;
  project_id: ID | null;
  answer: string;
  citations: Citation[];
  retrieval_metadata: {
    scope: string;
    source_count: number;
    graph_facts: number;
    vector_chunks: number;
    fulltext_clauses: number;
    contract_ids: string[];
    confidence: "high" | "medium" | "low";
    citation_review: string;
    limitations: string | null;
  };
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

// ---------------------------------------------------------------------------
// Tabular review
// ---------------------------------------------------------------------------

export interface TabularReview {
  id: ID;
  org_id: ID;
  name: string;
  project_id: ID | null;
  source_contract_ids: ID[];
  status: "draft" | "running" | "complete";
  metadata_json: Record<string, unknown>;
  created_by_user_id: ID;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface TabularReviewColumn {
  id: ID;
  tabular_review_id: ID;
  name: string;
  prompt: string;
  position: number;
  metadata_json: Record<string, unknown>;
}

export type TabularCellStatus =
  | "pending"
  | "running"
  | "complete"
  | "failed"
  | "needs_review";

export interface TabularReviewCell {
  id: ID;
  tabular_review_id: ID;
  column_id: ID;
  contract_id: ID;
  status: TabularCellStatus;
  answer: string | null;
  reasoning: string | null;
  citations: Citation[] | null;
  confidence: "high" | "medium" | "low" | null;
  error_message: string | null;
}

export interface TabularReviewDetail {
  review: TabularReview;
  columns: TabularReviewColumn[];
  cells: TabularReviewCell[];
}

export interface TabularReviewChat {
  id: ID;
  tabular_review_id: ID;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  created_at: ISODateTime;
}

// ---------------------------------------------------------------------------
// Notifications / jobs / admin / debug / search
// ---------------------------------------------------------------------------

export interface Notification {
  id: ID;
  user_id: ID;
  org_id: ID;
  channel: string;
  event_type: string;
  subject: string | null;
  body: string | null;
  status: string;
  provider_message_id: string | null;
  sent_at: ISODateTime | null;
  error_message: string | null;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
}

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface JobRun {
  id: ID;
  org_id: ID;
  job_type: string;
  resource_type: string;
  resource_id: ID;
  idempotency_key: string | null;
  status: JobStatus;
  progress: number;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  error_message: string | null;
  error_stack: string | null;
  attempt_count: number;
  celery_task_id: string | null;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface AdminSetting {
  id: ID;
  org_id: ID;
  key: string;
  value: unknown;
  is_secret: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface ConfigStatus {
  claude: { configured: boolean; mock: boolean };
  reducto: { configured: boolean; mock: boolean };
  resend: { configured: boolean; mock: boolean };
  docusign: { configured: boolean; mock: boolean };
  storage_root: string;
  debug: boolean;
}

export interface ContractTextSearchResult {
  contract_id: ID;
  contract_title: string;
  text_snapshot_id: ID;
  contract_version_id: ID;
  matches: { start_char: number; end_char: number; excerpt: string }[];
}

export interface ClauseSearchResult {
  clause_id: ID;
  contract_id: ID;
  contract_title: string;
  contract_version_id: ID;
  text_snapshot_id: ID;
  clause_type: string;
  heading: string;
  confidence: number;
  excerpt: string;
}

export interface ApiError {
  status: number;
  message: string;
  detail?: unknown;
}
