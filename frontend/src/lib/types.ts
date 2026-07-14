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
  clearance: string;
  permissions: string[];
}

// ---- Legal Intake ---------------------------------------------------------
export type IntakeStatus =
  | "awaiting_triage" | "in_review" | "escalated" | "approved" | "closed";
export type IntakeSlaPosture = "on_track" | "at_risk" | "overdue";

export interface IntakeWorkflowStep {
  label: string; stage: string; done: boolean; active: boolean;
}
export interface IntakeFieldSpec {
  key: string; label: string;
  kind: "text" | "textarea" | "select" | "date" | "number" | "boolean";
  required: boolean; sort_order: number; options?: { value: string; label: string }[] | null;
}
export interface IntakeRequestType {
  id: ID; key: string; name: string; workstream: string | null;
  description: string | null; active: boolean; stages: string[] | null;
  sort_order: number; fields: IntakeFieldSpec[];
}
export interface IntakeRequest {
  id: ID; ref: string; source: string;
  requester_user_id: ID; requester_name: string | null; department: string | null;
  request_type_id: ID | null; type_label: string; description: string;
  field_values: Record<string, unknown> | null;
  priority: "Critical" | "High" | "Medium" | "Low";
  status: IntakeStatus; stage: string; work_status: string | null;
  assigned_to_user_id: ID | null; assigned_to_label: string | null;
  approval_gate_user_id: ID | null;
  sla_hours: number; sla_status: IntakeSlaPosture; sla_pct: number;
  submitted_at: string | null; closed_at: string | null;
  triaged_by_user_id: ID | null; triage_action: string | null;
  agent_outcome: string | null;
  ai_triage: Record<string, unknown> | null;
  screening?: Record<string, unknown> | null;
  parties?: IntakeParty[];
  fired_rules: Record<string, unknown> | null;
  handoff_holder: string | null; handoff_user_id: ID | null;
  project_id: ID | null; contract_id: ID | null; contract_title?: string | null;
  workflow: IntakeWorkflowStep[]; created_at: string | null;
}
export interface IntakeTask {
  id: ID; request_id: ID; title: string; description: string | null;
  assignee_user_id: ID | null; assignee_label: string | null;
  status: "open" | "in_progress" | "blocked" | "done"; sort_order: number; effort_minutes: number;
}
export interface IntakeHandoff {
  id: ID; from_holder: string | null; to_holder: string; to_user_id: ID | null;
  to_label: string | null; reason: string | null; actor_type: string; created_at: string | null;
}
export interface IntakeAssignee { id: ID; name: string; email: string; }
export interface IntakeMyWork {
  awaiting_review: (IntakeRequest & { recommendation_id: string; agent_id: string; confidence: number })[];
  my_tickets: IntakeRequest[];
  my_tasks: IntakeTask[];
}
export interface IntakeRecommendation {
  id: ID; request_id: ID; agent_id: string; confidence: number;
  suggested_action: "approve_and_send" | "flag_for_review" | "escalate";
  drafted_response: string; reasoning: string; concerns: string[];
  citations: { id: string; title: string }[]; degraded: boolean;
  status: "pending" | "approved" | "edited" | "rejected";
  reviewed_by_user_id: ID | null; can_auto_send: boolean;
}
export interface IntakeSlaLeg {
  holder: string; holder_user_id: ID | null; holder_label: string;
  start_ts: number; end_ts: number; elapsed_ms: number; pct_of_sla: number;
  breached_during_leg: boolean; active?: boolean;
}
export interface IntakeSlaLegs {
  legs: IntakeSlaLeg[]; sla_ms: number; breach_ts: number; total_elapsed_ms: number;
  breached: boolean; closed: boolean; paused: boolean;
}
export interface IntakeSlaOps {
  generated_at: string; open_total: number; awaiting_triage: number; escalated: number;
  on_track: number; at_risk: number; overdue: number; paused: number;
  avg_elapsed_pct: number; breaches_7d: number;
  by_holder: { agent: number; human: number; queue: number };
  oldest_open: string | null;
  workload: { user_id: ID; name: string | null; open: number; overdue: number }[];
  rule_effectiveness: { id: ID; name: string; times_fired: number; last_fired_at: string | null }[];
}
export interface IntakeTeamMember {
  id?: ID; user_id: ID; name?: string; capacity: number; active: boolean; open_count?: number;
}
export interface IntakeTeam {
  id: ID; key: string; name: string; description: string | null; active: boolean;
  strategy: "least_loaded" | "round_robin"; overflow_team_id: ID | null;
  overflow_team_name: string | null; sort_order: number; members: IntakeTeamMember[];
}
export interface IntakeKbArticle {
  id: ID; source_ref: string; title: string; body: string; tags: string[]; active: boolean;
}
export interface IntakePoolMember {
  user_id: ID; name: string | null; capacity: number; utilization: number | null;
  open: number; overdue: number; at_risk: number; closed_7d: number; closed_30d: number; effort: number;
}
export interface IntakePoolTier {
  id: ID; name: string; strategy: string; overflow_team_name: string | null;
  members: IntakePoolMember[]; open: number; overdue: number; closed_30d: number; effort: number;
}
export interface IntakePoolOps {
  generated_at: string; days: number; tiers: IntakePoolTier[];
  totals: { open: number; overdue: number; closed_30d: number; effort_minutes: number; overflow_events: number };
  complexity_mix: { simple: number; standard: number; complex: number };
}
export interface CopilotTurn {
  reply: string; extracted: Record<string, string>; ready: boolean; suggested_type_label: string | null;
}
export interface IntakeRule {
  id: ID; name: string; description: string | null; enabled: boolean; eval_order: number;
  match_type: string | null; match_priority: string | null; match_department: string | null;
  match_keyword: string | null; match_complexity: string | null;
  set_assignee_user_id: ID | null; set_assignee_name: string | null;
  set_priority: string | null; set_sla_hours: number | null;
  set_team_id: ID | null; set_team_name: string | null;
  escalate_to_user_id: ID | null; escalate_to_name: string | null;
  require_approval_from_user_id: ID | null; require_approval_from_name: string | null;
  times_fired: number; last_fired_at: string | null;
}

export interface RoleResponse {
  id: ID;
  name: string;
  description: string | null;
  is_builtin: boolean;
  permissions: string[];
  user_count: number;
}

export interface PermissionInfo {
  value: string;
  group: string;
  description: string | null;
}

export interface GrantResponse {
  id: ID;
  principal_type: string;
  principal_id: string;
  principal_label: string;
  resource_type: string;
  resource_id: string;
  access_level: string;
  note: string | null;
  valid_until: string | null;
  revoked_at: string | null;
  active: boolean;
  created_at: string | null;
}

export interface WallPrincipal {
  id?: ID;
  principal_type: "user" | "role";
  principal_id: string;
  principal_label?: string;
}

export interface WallResponse {
  id: ID;
  name: string;
  reason: string | null;
  scope_type: "contract" | "project";
  scope_id: string;
  scope_label: string | null;
  active: boolean;
  principals: WallPrincipal[];
  created_at: string | null;
}

export interface AuthorityGrantResponse {
  id: ID;
  principal_type: "user" | "role";
  principal_id: string;
  principal_label: string;
  action: "contract:approve" | "contract:sign";
  max_value: number | null;
  currency: string | null;
  allowed_contract_types: string[];
  allowed_jurisdictions: string[];
  max_risk_band: string | null;
  delegated_by_user_id: string | null;
  delegated_by_label: string | null;
  note: string | null;
  valid_until: string | null;
  revoked_at: string | null;
  active: boolean;
  created_at: string | null;
}

export interface TokenResponse {
  access_token: string;
  // Backend only returns refresh_token in the body when
  // EXPOSE_REFRESH_TOKEN_IN_BODY is true (legacy clients). Default in prod
  // is HttpOnly cookie only — the field is null/absent.
  refresh_token?: string | null;
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
  email_sent?: boolean | null;
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
  | "review"
  | "approval"
  | "signature"
  | "active"
  | "closed";

export interface ReviewChecklistItem {
  key: string;
  label: string;
  status: "done" | "todo" | "blocked" | "in_progress";
  count: number;
  detail: string | null;
}

export interface ReviewStatusResponse {
  contract_id: ID;
  lifecycle_stage: ContractLifecycleStage;
  ai_reviewed: boolean;
  open_issues: number;
  high_severity_issues: number;
  pending_redlines: number;
  open_comments: number;
  counterparty_active: boolean;
  ready_for_approval: boolean;
  next_step: string;
  next_action:
    | "run_ai"
    | "move_to_drafting"
    | "move_to_review"
    | "resolve_issues"
    | "resolve_redlines"
    | "resolve_comments"
    | "submit_approval"
    | null;
  checklist: ReviewChecklistItem[];
}

export interface ContractComment {
  id: ID;
  contract_id: ID;
  contract_version_id: ID | null;
  parent_comment_id: ID | null;
  visibility: "internal" | "shared";
  author_kind: "user" | "counterparty";
  author_user_id: ID | null;
  author_name: string;
  body: string;
  anchor: Record<string, unknown> | null;
  mentioned_user_ids: ID[];
  resolved: boolean;
  resolved_at: ISODateTime | null;
  created_at: ISODateTime;
}

// Public (counterparty) external-share view — no account required.
export interface ExternalShareView {
  contract_id: ID;
  contract_version_id: ID | null;
  title: string;
  filename: string | null;
  access_mode: string;
  download_allowed: boolean;
  text_excerpt: string | null;
  text_truncated: boolean;
}

export interface ExternalComment {
  id: ID;
  author_name: string;
  author_kind: "user" | "counterparty";
  body: string;
  resolved: boolean;
  created_at: ISODateTime;
}

export interface ContractParty {
  id: ID;
  contract_id: ID;
  name: string;
  party_type: string | null;
  contact_email: string | null;
}

// A selectable signature recipient (a contract party or an org user).
export interface SignerOption {
  name: string;
  email: string;
  kind: "party" | "user";
}

export interface PlaybookRecommendation {
  clause_type: string;
  rule_id: string | null;
  change_summary: string;
  proposed_preferred_position: string | null;
  proposed_fallback_position: string | null;
  proposed_negotiation_guidance: string | null;
  rationale: string;
  risk_direction: "more_protected" | "less_protected" | "neutral";
  confidence: "high" | "medium" | "low";
}

export interface PlaybookInsightsStat {
  clause_type: string;
  total: number;
  concessions: number;
  held: number;
}

export interface PlaybookInsights {
  ready: boolean;
  decided_count: number;
  min_required: number;
  stats: PlaybookInsightsStat[];
  recommendations: PlaybookRecommendation[];
  summary: string | null;
}

export interface PlaybookDraftRule {
  clause_type: string;
  rule_type?: string;
  preferred_position?: string | null;
  fallback_position?: string | null;
  prohibited_language?: string | null;
  required_language?: string | null;
  risk_level?: string | null;
  rationale?: string | null;
  sample_clause?: string | null;
  negotiation_guidance?: string | null;
  approval_required?: boolean;
}

export interface ExtractedDoc {
  filename: string;
  content: string;
  chars: number;
}

export interface BuildChatResponse {
  reply: string;
  suggested_name: string | null;
  rules: PlaybookDraftRule[];
}

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

export interface RiskDriver {
  clause_type: string;
  label: string;
  weight: number;
  risk: "low" | "medium" | "high";
  rationale: string;
  quote?: string | null;
  contribution: number;
}

export interface ContractRiskSummary {
  score: number | null;
  band: "low" | "medium" | "high" | "unknown" | string;
  drivers: RiskDriver[];
  counts: { high: number; medium: number; low: number };
  clause_count: number;
  summary?: string | null;
  computed_at?: string | null;
  note?: string | null;
}

export interface ContractResponse {
  id: ID;
  org_id: ID;
  title: string;
  contract_type: string | null;
  lifecycle_stage: ContractLifecycleStage;
  renewal_due: boolean;
  archived: boolean;
  owner_user_id: ID;
  counterparty_name: string | null;
  jurisdiction: string | null;
  confidentiality: string;
  risk_level: string | null;
  risk_score?: number | null;
  risk_band?: string | null;
  risk_summary?: ContractRiskSummary | null;
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
  created_at: ISODateTime;
}

export interface VersionDiffLine {
  type: "context" | "add" | "remove";
  text: string;
}

export interface VersionDiffResponse {
  base_version_id: ID;
  base_version_number: number;
  target_version_id: ID;
  target_version_number: number;
  added: number;
  removed: number;
  truncated: boolean;
  lines: VersionDiffLine[];
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
  created_at: ISODateTime;
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
  days_in_stage?: number;
  stage_sla_days?: number | null;
  sla_breached?: boolean;
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
  shared_user_ids?: string[] | null;
}

export interface WorkflowVersion {
  id: ID;
  version_number: number;
  name: string;
  description: string | null;
  definition: Record<string, unknown>;
  visibility: string;
  note: string | null;
  edited_by_user_id: ID | null;
  created_at: ISODateTime | null;
}

export interface WorkflowUsage {
  run_count: number;
  last_run_at: ISODateTime | null;
  distinct_users: number;
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
  status: "pending" | "approved" | "rejected" | "cancelled" | "waiting";
  requested_by_user_id: ID;
  approver_user_id: ID | null;
  approver_role: string | null;
  approver_group_id?: ID | null;
  approver_group_name?: string | null;
  routing_rule_id?: ID | null;
  step_order?: number;
  due_at: ISODateTime | null;
  overdue?: boolean;
  metadata_json: Record<string, unknown>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  // Server-computed: whether the current user may decide this request (covers
  // approver-group membership, which the client can't determine on its own).
  can_decide?: boolean;
}

export interface ApprovalChainStep {
  approval_request_id: ID;
  step_order: number;
  status: "pending" | "approved" | "rejected" | "cancelled" | "waiting";
  approver_label: string;
  due_at: ISODateTime | null;
  overdue: boolean;
  decided_at: ISODateTime | null;
  decided_by: string | null;
  comment: string | null;
}

// Public token-authenticated review context for the emailed approver.
export interface ApprovalReviewContext {
  contract_title: string;
  requester_name: string;
  status: string;
  can_decide: boolean;
  due_at: ISODateTime | null;
  document_text: string;
  document_truncated: boolean;
}

// A user as surfaced for approver/member dropdowns.
export interface ApproverBrief {
  id: ID;
  full_name: string;
  email: string;
  roles: string[];
}

// A named pool of approvers for a function (Legal Counsel, Finance, …).
export interface ApproverGroup {
  id: ID;
  org_id: ID;
  name: string;
  description: string | null;
  is_active: boolean;
  members: ApproverBrief[];
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

// One ordered step of a routing rule's chain.
export interface ApprovalRoutingStep {
  id?: ID;
  step_order: number;
  approver_group_id: ID | null;
  approver_group_name?: string | null;
  approver_user_id: ID | null;
  approver_user_name?: string | null;
  approver_role: string | null;
  mode: string;
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
  steps: ApprovalRoutingStep[];
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
  contract_title: string | null;
  counterparty_name: string | null;
  owner_user_id: ID | null;
  owner_name: string | null;
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

export interface BrainSearchSemanticHit {
  contract_id: ID;
  contract_title: string;
  text: string;
  score: number;
}

export interface BrainSearchClauseHit {
  clause_id: ID;
  contract_id: ID;
  contract_title: string;
  clause_type: string;
  heading: string | null;
  excerpt: string;
}

export interface BrainSearchTextHit {
  contract_id: ID;
  contract_title: string;
  matches: { start_char: number; end_char: number; excerpt: string }[];
}

export interface BrainSearchResponse {
  query: string;
  semantic: BrainSearchSemanticHit[];
  clauses: BrainSearchClauseHit[];
  text: BrainSearchTextHit[];
}

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
    sources?: BrainSearchResponse;
    grounding?: number;
    verified_citations?: number;
    total_citations?: number;
    model_confidence?: "high" | "medium" | "low";
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
  read_at: ISODateTime | null;
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

// ---- Command console (Contracts Hub) ----
export interface ConsoleQueueItem {
  kind: string;
  action: string;
  contract_id: ID;
  title: string;
  counterparty: string | null;
  stage: string;
  days_in_stage: number;
  sla_days: number | null;
  sla_breached: boolean;
  issues: number;
  high_issues: number;
  redlines: number;
}

export interface ConsoleResponse {
  strip: {
    live_value: number;
    contracts: number;
    needs_you: number;
    sla_breaches: number;
    approvals_pending: number;
    approvals_overdue: number;
    to_sign: number;
    obligations_due_14d: number;
    obligations_total: number;
    renewals_90d: number;
    automated_pct: number | null;
    cycle_median_days: number | null;
  };
  action_queue: ConsoleQueueItem[];
  approvals_in_flight: {
    contract_id: ID;
    title: string;
    steps: { label: string; status: string; overdue: boolean; due_at: string | null }[];
  }[];
  deadlines: {
    kind: "obligation" | "renewal" | "expiry";
    what: string;
    contract_id: ID;
    contract_title: string;
    due: string;
    days: number;
  }[];
  pipeline: {
    stage: string;
    count: number;
    avg_days: number | null;
    sla_days: number | null;
    breached: number;
  }[];
  risk_board: {
    contract_id: ID;
    title: string;
    score: number | null;
    band: string | null;
    top_driver: string | null;
  }[];
  engine_log: { ts: string; event: string; auto: boolean; contract_id?: string }[];
  triage: {
    rank: number;
    kind: "send" | "work" | "decide" | "classify" | "nudge" | "move";
    title: string;
    detail: string;
    reason: string;
    urgency: number;
    contract_id: ID;
    stage: string;
    sla_breached: boolean;
  }[];
  friction: { counterparty: string; rounds: number; contracts: number }[];
  register: {
    contract_id: ID;
    title: string;
    stage: string;
    risk_score: number | null;
    risk_band: string | null;
    days_in_stage: number;
    sla_days: number | null;
    sla_breached: boolean;
    health: "critical" | "working" | "moving" | "healthy" | "idle";
    counterparty: string | null;
    value: number | null;
    issues: number;
    redlines: number;
    obligations: number;
  }[];
  recent_activity: { ts: string; title: string; contract_id: ID; contract_title: string }[];
  value_by_stage: { stage: string; value: number }[];
}

export interface IntakeDocument {
  id: string; filename: string; mime_type: string; size_bytes: number;
  extracted_chars: number; extracted_text?: string | null;
  extraction_quality: number | null; created_at: string | null;
}

export interface IntakeAgentMetric {
  agent_id: string; name: string; short_name: string; icon: string; description: string;
  production_ready: boolean; active: boolean; produced: number;
  accepted: number; rejected: number; pending: number;
  accept_rate: number | null; avg_confidence: number | null; degraded_rate: number;
  avg_review_minutes: number | null;
}

export interface IntakeAgentMetrics {
  agents: IntakeAgentMetric[];
  summary: { recommendations: number; pending_review: number; accept_rate: number | null; degraded: number };
}

export interface IntakeParty { name: string; role: string; is_person?: boolean; }
