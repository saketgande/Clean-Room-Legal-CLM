// Typed endpoint functions, grouped by backend module.
import { apiFetch, apiDownload } from "./api";
import type {
  AdminSetting,
  AiUsageSummary,
  ApprovalChainStep,
  ApprovalRequest,
  ApprovalReviewContext,
  ApprovalRoutingRule,
  ApproverGroup,
  ApproverBrief,
  AssistantMessage,
  AssistantRun,
  AssistantSession,
  BrainQuery,
  BrainSearchResponse,
  BrainScope,
  ClauseSearchResult,
  ConfigStatus,
  ContractActivityResponse,
  ContractEditResponse,
  ContractComment,
  ContractParty,
  ContractResponse,
  ContractRiskSummary,
  ExternalComment,
  Workflow,
  WorkflowRun,
  SignerOption,
  ExternalShareView,
  ContractDeviation,
  ReviewStatusResponse,
  VersionDiffResponse,
  ContractShareCreateResponse,
  ContractShareResponse,
  ContractStageHistoryResponse,
  ContractTextSearchResult,
  ContractTextSnapshotResponse,
  ContractUploadResponse,
  ContractVersionResponse,
  ContractLifecycleStage,
  JobRun,
  LifecycleOptionsResponse,
  Notification,
  Notice,
  NoticeExtraction,
  NoticeReminderRun,
  NoticeSummary,
  Obligation,
  OrganizationResponse,
  BuildChatResponse,
  ExtractedDoc,
  PlaybookDraftRule,
  PlaybookInsights,
  PlaybookResponse,
  PlaybookRuleResponse,
  PlaybookRunDetailResponse,
  PlaybookRunResponse,
  PlaybookVersionResponse,
  MatterContractResponse,
  MatterFolderResponse,
  MatterMemberResponse,
  MatterActivityItem,
  MatterOverview,
  MatterResponse,
  MatterShareResponse,
  UnfiledItem,
  RegistrationResponse,
  RenewalEvent,
  RenewalRecommendation,
  SignatureRecipient,
  SignatureRequest,
  TabularReview,
  TabularReviewChat,
  TabularReviewDetail,
  TokenResponse,
  UserInvitationResponse,
  UserResponse,
  RoleResponse,
  PermissionInfo,
  GrantResponse,
  WallResponse,
  AuthorityGrantResponse,
  Prompt,
  PromptVersion,
  PromptUsage,
  IntakeRequest,
  IntakeRequestType,
  IntakeApprovalRung,
  IntakeTask,
  IntakeHandoff,
  IntakeAssignee,
  IntakeMyWork,
  IntakeSlaLegs,
  IntakeSlaOps,
  IntakeTeam,
  IntakeRule,
  IntakeKbArticle,
  IntakePoolOps,
  IntakeDocument,
  CopilotTurn,
  Trademark,
  TrademarkCreatePayload,
  TrademarkUpdatePayload,
  IntakeSubmitPayload,
  TrademarkDashboardMetrics,
  RenewalCalendarEntry,
  SearchSimilarRequest,
  SearchSimilarResponse,
  UploadDocumentResponse,
  ExtractRequest,
  ExtractResponse,
  IngestRequest,
  IngestResponse,
  IntegrationStatusResponse,
  IntegrationTestResponse,
} from "./types";

const qs = (params: Record<string, unknown>) => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
};

// ---- Auth ----------------------------------------------------------------
export const authApi = {
  login: (email: string, password: string) =>
    apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: { email, password },
      noRetry: true,
    }),
  register: (email: string, full_name: string, password: string, message?: string) =>
    apiFetch<RegistrationResponse>("/auth/register", {
      method: "POST",
      body: { email, full_name, password, message },
      noRetry: true,
    }),
  setupFirstAdmin: (payload: {
    setup_token: string;
    organization_name: string;
    organization_slug: string;
    allowed_domains?: string[];
    email: string;
    full_name: string;
    password: string;
  }) =>
    apiFetch<UserResponse>("/auth/setup/first-admin", {
      method: "POST",
      body: payload,
      noRetry: true,
    }),
  me: () => apiFetch<UserResponse>("/auth/me"),
  // The refresh token is read by the backend from the HttpOnly cookie; no
  // need (and no way) to pass it from JS. The optional body field is kept
  // for legacy clients that haven't migrated yet.
  logout: (refresh_token?: string) =>
    apiFetch<void>("/auth/logout", {
      method: "POST",
      body: refresh_token ? { refresh_token } : {},
    }),
  switchRole: (role_id?: string, role_name?: string) =>
    apiFetch<UserResponse>("/auth/active-role", {
      method: "POST",
      body: { role_id, role_name },
    }),
  acceptInvitation: (token: string, full_name: string, password: string) =>
    apiFetch<TokenResponse>("/auth/invitations/accept", {
      method: "POST",
      body: { token, full_name, password },
      noRetry: true,
    }),
};

// ---- Users / org ---------------------------------------------------------
// ---- Resource grants (object-level access) -------------------------------
export const grantsApi = {
  list: (resourceType: string, resourceId: string) =>
    apiFetch<GrantResponse[]>(
      `/grants?resource_type=${encodeURIComponent(resourceType)}&resource_id=${encodeURIComponent(resourceId)}`,
    ),
  create: (payload: {
    principal_type: string;
    principal_id: string;
    resource_type: string;
    resource_id: string;
    access_level: string;
    valid_until?: string | null;
    note?: string | null;
  }) => apiFetch<GrantResponse>("/grants", { method: "POST", body: payload }),
  revoke: (id: string) =>
    apiFetch<void>(`/grants/${id}`, { method: "DELETE" }),
};

// ---- Roles (custom RBAC role management) ---------------------------------
export const rolesApi = {
  list: () => apiFetch<RoleResponse[]>("/roles"),
  permissions: () => apiFetch<PermissionInfo[]>("/roles/permissions"),
  create: (payload: {
    name: string;
    description?: string | null;
    permissions: string[];
  }) => apiFetch<RoleResponse>("/roles", { method: "POST", body: payload }),
  update: (
    id: string,
    payload: {
      name?: string;
      description?: string | null;
      permissions?: string[];
    },
  ) => apiFetch<RoleResponse>(`/roles/${id}`, { method: "PATCH", body: payload }),
  remove: (id: string) =>
    apiFetch<void>(`/roles/${id}`, { method: "DELETE" }),
  setUserRoles: (
    userId: string,
    payload: { role_ids: string[]; active_role_id?: string | null },
  ) =>
    apiFetch<UserResponse>(`/roles/user/${userId}`, {
      method: "PUT",
      body: payload,
    }),
  setUserClearance: (userId: string, clearance: string) =>
    apiFetch<UserResponse>(`/roles/user/${userId}/clearance`, {
      method: "PUT",
      body: { clearance },
    }),
};

// ---- Authority (Delegation of Authority / ABAC action gate) ---------------
export const authorityApi = {
  list: () => apiFetch<AuthorityGrantResponse[]>("/authority-grants"),
  create: (payload: {
    principal_type: "user" | "role";
    principal_id: string;
    action: "contract:approve" | "contract:sign";
    max_value?: number | null;
    currency?: string | null;
    allowed_contract_types?: string[];
    allowed_jurisdictions?: string[];
    max_risk_band?: string | null;
    delegated_by_user_id?: string | null;
    note?: string | null;
    valid_until?: string | null;
  }) =>
    apiFetch<AuthorityGrantResponse>("/authority-grants", {
      method: "POST",
      body: payload,
    }),
  revoke: (id: string) =>
    apiFetch<void>(`/authority-grants/${id}`, { method: "DELETE" }),
};

// ---- Ethical walls (conflict-of-interest screens / deny-override) ---------
export const wallsApi = {
  list: () => apiFetch<WallResponse[]>("/ethical-walls"),
  create: (payload: {
    name: string;
    reason?: string | null;
    scope_type: "contract" | "project";
    scope_id: string;
    principals: { principal_type: "user" | "role"; principal_id: string }[];
  }) => apiFetch<WallResponse>("/ethical-walls", { method: "POST", body: payload }),
  update: (
    id: string,
    payload: {
      name?: string;
      reason?: string | null;
      active?: boolean;
      principals?: { principal_type: "user" | "role"; principal_id: string }[];
    },
  ) => apiFetch<WallResponse>(`/ethical-walls/${id}`, { method: "PATCH", body: payload }),
  remove: (id: string) =>
    apiFetch<void>(`/ethical-walls/${id}`, { method: "DELETE" }),
};

export const usersApi = {
  // List org users, optionally filtered by status (e.g. "pending_approval"
  // surfaces the in-domain self-registration queue for the admin UI).
  list: (params?: { status?: string }) => {
    const qs = params?.status ? `?status=${encodeURIComponent(params.status)}` : "";
    return apiFetch<UserResponse[]>(`/users${qs}`);
  },
  decideApproval: (
    userId: string,
    decision: "approve" | "reject",
    role_name = "member",
    reason?: string,
  ) =>
    apiFetch<UserResponse>(`/users/${userId}/approval`, {
      method: "POST",
      body: { decision, role_name, reason },
    }),
  listInvitations: () =>
    apiFetch<UserInvitationResponse[]>("/users/invitations"),
  createInvitation: (email: string, role_name = "member", expires_in_days = 7) =>
    apiFetch<UserInvitationResponse>("/users/invitations", {
      method: "POST",
      body: { email, role_name, expires_in_days },
    }),
  revokeInvitation: (id: string) =>
    apiFetch<UserInvitationResponse>(`/users/invitations/${id}/revoke`, {
      method: "POST",
    }),
};

export const orgApi = {
  current: () => apiFetch<OrganizationResponse>("/organizations/current"),
  update: (payload: Partial<{
    name: string;
    allowed_domains: string[];
    default_role_name: string;
  }>) =>
    apiFetch<OrganizationResponse>("/organizations/current", {
      method: "PATCH",
      body: payload,
    }),
};

// ---- Matters ------------------------------------------------------------
export const mattersApi = {
  list: () => apiFetch<MatterResponse[]>("/matters"),
  create: (payload: {
    name: string;
    description?: string;
    matter_type?: string;
    client_name?: string;
    status?: string;
    metadata_json?: Record<string, unknown>;
  }) => apiFetch<MatterResponse>("/matters", { method: "POST", body: payload }),
  overview: (id: string) =>
    apiFetch<MatterOverview>(`/matters/${id}/overview`),
  activity: (id: string) =>
    apiFetch<MatterActivityItem[]>(`/matters/${id}/activity`),
  unfiled: (item_type?: "contract" | "intake") =>
    apiFetch<UnfiledItem[]>(`/matters/unfiled${qs({ item_type })}`),
  assign: (id: string, item_type: "contract" | "intake", item_id: string) =>
    apiFetch<{ status: string }>(`/matters/${id}/items`, {
      method: "POST",
      body: { item_type, item_id },
    }),
  get: (id: string) => apiFetch<MatterResponse>(`/matters/${id}`),
  folders: (id: string) =>
    apiFetch<MatterFolderResponse[]>(`/matters/${id}/folders`),
  createFolder: (id: string, name: string, parent_folder_id?: string) =>
    apiFetch<MatterFolderResponse>(`/matters/${id}/folders`, {
      method: "POST",
      body: { name, parent_folder_id },
    }),
  members: (id: string) =>
    apiFetch<MatterMemberResponse[]>(`/matters/${id}/members`),
  upsertMember: (id: string, user_id: string, role = "member") =>
    apiFetch<MatterMemberResponse>(`/matters/${id}/members`, {
      method: "PUT",
      body: { user_id, role },
    }),
  removeMember: (id: string, userId: string) =>
    apiFetch<void>(`/matters/${id}/members/${userId}`, { method: "DELETE" }),
  shares: (id: string) =>
    apiFetch<MatterShareResponse[]>(`/matters/${id}/shares`),
  createShare: (
    id: string,
    user_id: string,
    access_level = "read",
    expires_at?: string,
  ) =>
    apiFetch<MatterShareResponse>(`/matters/${id}/shares`, {
      method: "POST",
      body: { user_id, access_level, expires_at },
    }),
  contracts: (id: string) =>
    apiFetch<MatterContractResponse[]>(`/matters/${id}/contracts`),
  addContract: (id: string, contract_id: string, folder_id?: string) =>
    apiFetch<MatterContractResponse>(`/matters/${id}/contracts`, {
      method: "PUT",
      body: { contract_id, folder_id },
    }),
  removeContract: (id: string, contractId: string) =>
    apiFetch<void>(`/matters/${id}/contracts/${contractId}`, {
      method: "DELETE",
    }),
};

// ---- Contracts -----------------------------------------------------------
export const contractsApi = {
  list: () => apiFetch<ContractResponse[]>("/contracts"),
  get: (id: string) => apiFetch<ContractResponse>(`/contracts/${id}`),
  risk: (id: string) =>
    apiFetch<ContractRiskSummary>(`/contracts/${id}/risk`),
  computeRisk: (id: string) =>
    apiFetch<ContractRiskSummary>(`/contracts/${id}/risk`, { method: "POST" }),
  upload: (
    file: File,
    extra: { title?: string; counterparty_name?: string; matter_id?: string } = {},
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (extra.title) form.append("title", extra.title);
    if (extra.counterparty_name)
      form.append("counterparty_name", extra.counterparty_name);
    if (extra.matter_id) form.append("matter_id", extra.matter_id);
    return apiFetch<ContractUploadResponse>("/contracts/upload", {
      method: "POST",
      form,
    });
  },
  update: (id: string, payload: Record<string, unknown>) =>
    apiFetch<ContractResponse>(`/contracts/${id}`, {
      method: "PATCH",
      body: payload,
    }),
  lifecycleOptions: (id: string) =>
    apiFetch<LifecycleOptionsResponse>(`/contracts/${id}/lifecycle`),
  transition: (
    id: string,
    to_stage: ContractLifecycleStage,
    opts: { reason?: string; override?: boolean; signed_confirmation?: boolean } = {},
  ) =>
    apiFetch<ContractResponse>(`/contracts/${id}/lifecycle`, {
      method: "POST",
      body: { to_stage, ...opts },
    }),
  stageHistory: (id: string) =>
    apiFetch<ContractStageHistoryResponse[]>(`/contracts/${id}/stage-history`),
  reviewStatus: (id: string) =>
    apiFetch<ReviewStatusResponse>(`/contracts/${id}/review-status`),
  deviations: (id: string) =>
    apiFetch<ContractDeviation[]>(`/contracts/${id}/deviations`),
  plainSummary: (id: string) =>
    apiFetch<{ summary: string; generated: boolean }>(`/contracts/${id}/plain-summary`),
  logCounterpartyRevision: (id: string, file: File, change_summary?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (change_summary) form.append("change_summary", change_summary);
    return apiFetch<ContractVersionResponse>(`/contracts/${id}/counterparty-revision`, {
      method: "POST",
      form,
    });
  },
  logNegotiationRevision: (
    id: string,
    file: File,
    party: "counterparty" | "internal" | "us",
    opts: { party_label?: string; change_summary?: string } = {},
  ) => {
    const form = new FormData();
    form.append("file", file);
    form.append("party", party);
    if (opts.party_label) form.append("party_label", opts.party_label);
    if (opts.change_summary) form.append("change_summary", opts.change_summary);
    return apiFetch<ContractVersionResponse>(`/contracts/${id}/negotiation-revision`, {
      method: "POST",
      form,
    });
  },
  notifyTeam: (id: string, team_id: string, message?: string) =>
    apiFetch<{ notified: number; team: string }>(`/contracts/${id}/notify-team`, {
      method: "POST",
      body: { team_id, ...(message ? { message } : {}) },
    }),
  addParty: (
    id: string,
    payload: { name: string; contact_email?: string; party_type?: string },
  ) => apiFetch<ContractParty>(`/contracts/${id}/parties`, { method: "POST", body: payload }),
  signers: (id: string) => apiFetch<SignerOption[]>(`/contracts/${id}/signers`),
  comments: (id: string) =>
    apiFetch<ContractComment[]>(`/contracts/${id}/comments`),
  addComment: (
    id: string,
    payload: {
      body: string;
      visibility?: "internal" | "shared";
      contract_version_id?: string;
      parent_comment_id?: string;
      mentioned_user_ids?: string[];
      anchor?: { start: number; end: number; quote: string } | null;
    },
  ) =>
    apiFetch<ContractComment>(`/contracts/${id}/comments`, {
      method: "POST",
      body: payload,
    }),
  resolveComment: (id: string, commentId: string, resolved: boolean) =>
    apiFetch<ContractComment>(`/contracts/${id}/comments/${commentId}/resolve`, {
      method: "POST",
      body: { resolved },
    }),
  deleteComment: (id: string, commentId: string) =>
    apiFetch<void>(`/contracts/${id}/comments/${commentId}`, { method: "DELETE" }),
  activity: (id: string, limit = 100) =>
    apiFetch<ContractActivityResponse[]>(
      `/contracts/${id}/activity${qs({ limit })}`,
    ),
  versions: (id: string) =>
    apiFetch<ContractVersionResponse[]>(`/contracts/${id}/versions`),
  versionText: (id: string, versionId: string) =>
    apiFetch<ContractTextSnapshotResponse>(
      `/contracts/${id}/versions/${versionId}/text`,
    ),
  versionDiff: (id: string, baseVersionId: string, targetVersionId: string) =>
    apiFetch<VersionDiffResponse>(
      `/contracts/${id}/versions/${baseVersionId}/diff/${targetVersionId}`,
    ),
  downloadVersion: (id: string, versionId: string, name?: string) =>
    apiDownload(`/contracts/${id}/versions/${versionId}/download`, name),
  proposeEdit: (
    id: string,
    payload: {
      original_text: string;
      replacement_text: string;
      rationale?: string;
      start_hint?: number;
    },
  ) =>
    apiFetch<ContractEditResponse>(`/contracts/${id}/edits/propose`, {
      method: "POST",
      body: payload,
    }),
  aiRedline: (id: string, instructions: string) =>
    apiFetch<{ edits: number; summary?: string | null }>(
      `/contracts/${id}/edits/ai-redline`,
      { method: "POST", body: { instructions } },
    ),
  exportDocx: (id: string, name?: string) =>
    apiDownload(`/contracts/${id}/export-docx`, name),
  updateText: (
    id: string,
    payload: { text: string; change_summary?: string },
  ) =>
    apiFetch<ContractVersionResponse>(`/contracts/${id}/text`, {
      method: "PUT",
      body: payload,
    }),
  restoreVersion: (id: string, versionId: string) =>
    apiFetch<ContractVersionResponse>(
      `/contracts/${id}/versions/${versionId}/restore`,
      { method: "POST" },
    ),
  edits: (id: string, status_filter?: string) =>
    apiFetch<ContractEditResponse[]>(
      `/contracts/${id}/edits${qs({ status_filter })}`,
    ),
  acceptEdit: (id: string, editId: string, comment?: string) =>
    apiFetch<ContractEditResponse>(`/contracts/${id}/edits/${editId}/accept`, {
      method: "POST",
      body: { comment },
    }),
  rejectEdit: (id: string, editId: string, comment?: string) =>
    apiFetch<ContractEditResponse>(`/contracts/${id}/edits/${editId}/reject`, {
      method: "POST",
      body: { comment },
    }),
  shares: (id: string) =>
    apiFetch<ContractShareResponse[]>(`/contracts/${id}/shares`),
  createShare: (
    id: string,
    payload: {
      contract_version_id?: string;
      access_mode?: string;
      expires_at?: string;
      passcode?: string;
      download_allowed?: boolean;
    },
  ) =>
    apiFetch<ContractShareCreateResponse>(`/contracts/${id}/shares`, {
      method: "POST",
      body: payload,
    }),
};

// ---- Assistant -----------------------------------------------------------
export const assistantApi = {
  sessions: (params: Record<string, unknown> = {}) =>
    apiFetch<AssistantSession[]>(`/assistant/sessions${qs(params)}`),
  createSession: (payload: {
    session_type?: string;
    title?: string;
    matter_id?: string;
    contract_id?: string;
    tabular_review_id?: string;
  }) =>
    apiFetch<AssistantSession>("/assistant/sessions", {
      method: "POST",
      body: payload,
    }),
  session: (id: string) =>
    apiFetch<{ session: AssistantSession; contract_handles: unknown[] }>(
      `/assistant/sessions/${id}`,
    ),
  updateSession: (id: string, payload: { title?: string; status?: string }) =>
    apiFetch<AssistantSession>(`/assistant/sessions/${id}`, {
      method: "PATCH",
      body: payload,
    }),
  messages: (id: string, limit = 100) =>
    apiFetch<AssistantMessage[]>(
      `/assistant/sessions/${id}/messages${qs({ limit })}`,
    ),
  confirm: (confirmationId: string) =>
    apiFetch<{
      confirmation_id: string;
      status: string;
      assistant_run_id: string;
      resume_required: boolean;
    }>(`/assistant/confirmations/${confirmationId}/confirm`, { method: "POST" }),
  reject: (confirmationId: string, reason?: string) =>
    apiFetch<{
      confirmation_id: string;
      status: string;
      assistant_run_id: string;
      resume_required: boolean;
    }>(`/assistant/confirmations/${confirmationId}/reject`, {
      method: "POST",
      body: { reason },
    }),
};

// ---- AI ------------------------------------------------------------------
export const aiApi = {
  rerunMetadata: (contractId: string) =>
    apiFetch(`/ai/contracts/${contractId}/metadata-extraction`, {
      method: "POST",
      body: {},
    }),
  rerunClauses: (contractId: string) =>
    apiFetch(`/ai/contracts/${contractId}/clause-extraction`, {
      method: "POST",
      body: {},
    }),
};

// ---- Workflows -----------------------------------------------------------
export const promptsApi = {
  list: () => apiFetch<Prompt[]>("/prompt-library"),
  create: (payload: {
    name: string;
    workflow_type?: string;
    visibility?: string;
    description?: string;
    definition?: Record<string, unknown>;
  }) => apiFetch<Prompt>("/prompt-library", { method: "POST", body: payload }),
  update: (
    id: string,
    payload: {
      name?: string;
      description?: string | null;
      definition?: Record<string, unknown>;
      visibility?: string;
      shared_user_ids?: string[];
      note?: string;
    },
  ) => apiFetch<Prompt>(`/prompt-library/${id}`, { method: "PATCH", body: payload }),
  versions: (id: string) =>
    apiFetch<PromptVersion[]>(`/prompt-library/${id}/versions`),
  revert: (id: string, versionId: string) =>
    apiFetch<Prompt>(`/prompt-library/${id}/versions/${versionId}/revert`, {
      method: "POST",
    }),
  launch: (id: string, payload: { mode?: string; contract_id?: string } = {}) =>
    apiFetch<void>(`/prompt-library/${id}/launch`, { method: "POST", body: payload }),
  analytics: () =>
    apiFetch<Record<string, PromptUsage>>("/prompt-library/analytics"),
};

// ---- Flows (workflow engine) ---------------------------------------------
type FlowBody = {
  name?: string;
  description?: string | null;
  enabled?: boolean;
  eval_order?: number;
  criteria?: Workflow["criteria"];
  steps?: Array<{
    id?: string;
    type: string;
    name: string;
    config?: Record<string, unknown>;
    parallel?: boolean;
    cond?: { field: string; op: string; value: string } | null;
  }>;
};

export const workflowsApi = {
  listFlows: () => apiFetch<Workflow[]>("/workflows"),
  getFlow: (id: string) => apiFetch<Workflow>(`/workflows/${id}`),
  seedFlows: () =>
    apiFetch<{ added: number; flows: Workflow[] }>("/workflows/seed", { method: "POST", body: {} }),
  createFlow: (body: FlowBody) =>
    apiFetch<Workflow>("/workflows", { method: "POST", body }),
  updateFlow: (id: string, body: FlowBody) =>
    apiFetch<Workflow>(`/workflows/${id}`, { method: "PATCH", body }),
  startFlow: (request_id: string, flow_id?: string) =>
    apiFetch<WorkflowRun>("/workflows/start", {
      method: "POST",
      body: flow_id ? { request_id, flow_id } : { request_id },
    }),
  runForRequest: (request_id: string) =>
    apiFetch<WorkflowRun | null>(`/workflows/runs/by-request/${request_id}`),
  runForContract: (contract_id: string) =>
    apiFetch<WorkflowRun | null>(`/workflows/runs/by-contract/${contract_id}`),
  completeStep: (run_id: string, note?: string, step_idx?: number) =>
    apiFetch<WorkflowRun>(`/workflows/runs/${run_id}/complete-step`, {
      method: "POST",
      body: { ...(note ? { note } : {}), ...(step_idx != null ? { step_idx } : {}) },
    }),
  refreshRun: (run_id: string) =>
    apiFetch<WorkflowRun>(`/workflows/runs/${run_id}/refresh`, { method: "POST", body: {} }),
  returnStep: (run_id: string, to_idx?: number, note?: string) =>
    apiFetch<WorkflowRun>(`/workflows/runs/${run_id}/return`, { method: "POST", body: { ...(to_idx != null ? { to_idx } : {}), ...(note ? { note } : {}) } }),
  comment: (run_id: string, text: string, idx?: number) =>
    apiFetch<WorkflowRun>(`/workflows/runs/${run_id}/comment`, { method: "POST", body: { text, ...(idx != null ? { idx } : {}) } }),
};

// ---- Playbooks -----------------------------------------------------------
export const playbooksApi = {
  list: () => apiFetch<PlaybookResponse[]>("/playbooks"),
  create: (name: string, description?: string) =>
    apiFetch<PlaybookResponse>("/playbooks", {
      method: "POST",
      body: { name, description },
    }),
  generateFromDocument: (form: FormData) =>
    apiFetch<PlaybookResponse>("/playbooks/generate-from-document", { form }),
  buildExtract: (form: FormData) =>
    apiFetch<ExtractedDoc[]>("/playbooks/build/extract", { form }),
  buildChat: (payload: {
    message: string;
    conversation: { role: string; content: string }[];
    current_rules: PlaybookDraftRule[];
    documents: { filename: string; content: string }[];
    name?: string;
  }) =>
    apiFetch<BuildChatResponse>("/playbooks/build/chat", { method: "POST", body: payload }),
  buildSave: (payload: { name: string; description?: string; rules: PlaybookDraftRule[] }) =>
    apiFetch<PlaybookResponse>("/playbooks/build/save", { method: "POST", body: payload }),
  insights: (id: string) =>
    apiFetch<PlaybookInsights>(`/playbooks/${id}/insights`, { method: "POST" }),
  applyInsight: (
    id: string,
    payload: {
      clause_type: string;
      preferred_position?: string | null;
      fallback_position?: string | null;
      negotiation_guidance?: string | null;
      summary?: string;
    },
  ) =>
    apiFetch<PlaybookVersionResponse>(`/playbooks/${id}/insights/apply`, {
      method: "POST",
      body: payload,
    }),
  get: (id: string) => apiFetch<PlaybookResponse>(`/playbooks/${id}`),
  versions: (id: string) =>
    apiFetch<PlaybookVersionResponse[]>(`/playbooks/${id}/versions`),
  createVersion: (id: string, source_version_id?: string, summary?: string) =>
    apiFetch<PlaybookVersionResponse>(`/playbooks/${id}/versions`, {
      method: "POST",
      body: { source_version_id, summary },
    }),
  expand: (id: string) =>
    apiFetch<PlaybookVersionResponse>(`/playbooks/${id}/expand`, {
      method: "POST",
    }),
  publish: (id: string, version_id?: string) =>
    apiFetch<PlaybookResponse>(`/playbooks/${id}/publish`, {
      method: "POST",
      body: { version_id },
    }),
  rules: (id: string, versionId: string) =>
    apiFetch<PlaybookRuleResponse[]>(
      `/playbooks/${id}/versions/${versionId}/rules`,
    ),
  createRule: (id: string, versionId: string, payload: Record<string, unknown>) =>
    apiFetch<PlaybookRuleResponse>(
      `/playbooks/${id}/versions/${versionId}/rules`,
      { method: "POST", body: payload },
    ),
  updateRule: (
    id: string,
    versionId: string,
    ruleId: string,
    payload: Record<string, unknown>,
  ) =>
    apiFetch<PlaybookRuleResponse>(
      `/playbooks/${id}/versions/${versionId}/rules/${ruleId}`,
      { method: "PATCH", body: payload },
    ),
  deleteRule: (id: string, versionId: string, ruleId: string) =>
    apiFetch<void>(`/playbooks/${id}/versions/${versionId}/rules/${ruleId}`, {
      method: "DELETE",
    }),
  runs: (id: string) =>
    apiFetch<PlaybookRunResponse[]>(`/playbooks/${id}/runs`),
  createRun: (
    id: string,
    payload: {
      contract_id: string;
      playbook_version_id?: string;
      create_redline?: boolean;
      test_mode?: boolean;
      use_ai?: boolean;
    },
  ) =>
    apiFetch<PlaybookRunResponse>(`/playbooks/${id}/runs`, {
      method: "POST",
      body: payload,
    }),
  runDetail: (runId: string) =>
    apiFetch<PlaybookRunDetailResponse>(`/playbooks/runs/${runId}`),
  decideDeviation: (
    deviationId: string,
    decision: string,
    rationale?: string,
  ) =>
    apiFetch(`/playbooks/deviations/${deviationId}/decisions`, {
      method: "POST",
      body: { decision, rationale },
    }),
};

// ---- Approvals -----------------------------------------------------------
export const approvalsApi = {
  chain: (contractId: string) =>
    apiFetch<{ steps: ApprovalChainStep[] }>(
      `/approvals/contracts/${contractId}/chain`,
    ),
  list: () => apiFetch<ApprovalRequest[]>("/approvals"),
  routingRules: () =>
    apiFetch<ApprovalRoutingRule[]>("/approvals/routing-rules"),
  createRoutingRule: (payload: Record<string, unknown>) =>
    apiFetch<ApprovalRoutingRule>("/approvals/routing-rules", {
      method: "POST",
      body: payload,
    }),
  updateRoutingRule: (id: string, payload: Record<string, unknown>) =>
    apiFetch<ApprovalRoutingRule>(`/approvals/routing-rules/${id}`, {
      method: "PATCH",
      body: payload,
    }),
  deleteRoutingRule: (id: string) =>
    apiFetch<void>(`/approvals/routing-rules/${id}`, { method: "DELETE" }),
  submit: (payload: {
    contract_id: string;
    contract_version_id?: string;
    approver_user_id?: string;
    approver_role?: string;
  }) =>
    apiFetch<ApprovalRequest[]>("/approvals/requests", {
      method: "POST",
      body: payload,
    }),
  decide: (
    id: string,
    decision: "approve" | "reject",
    comment?: string,
  ) =>
    apiFetch<ApprovalRequest>(`/approvals/requests/${id}/decision`, {
      method: "POST",
      body: { decision, comment },
    }),
  reassign: (id: string, to_user_id: string, kind: "delegate" | "escalate") =>
    apiFetch<ApprovalRequest>(`/approvals/requests/${id}/reassign`, {
      method: "POST",
      body: { to_user_id, kind },
    }),
  reviewByToken: (token: string) =>
    apiFetch<ApprovalReviewContext>(`/approvals/review/${token}`, { noRetry: true }),
  tokenDecide: (token: string, decision: "approve" | "reject", comment?: string) =>
    apiFetch<{ approval_request_id: string; status: string; contract_id: string }>(
      "/approvals/token-decision",
      { method: "POST", body: { token, decision, comment }, noRetry: true },
    ),

  // --- Approver groups (pools the routing-step dropdowns pick from) ---
  groups: () => apiFetch<ApproverGroup[]>("/approvals/groups"),
  createGroup: (payload: {
    name: string;
    description?: string;
    is_active?: boolean;
  }) => apiFetch<ApproverGroup>("/approvals/groups", { method: "POST", body: payload }),
  setGroupMembers: (id: string, userIds: string[]) =>
    apiFetch<ApproverGroup>(`/approvals/groups/${id}/members`, {
      method: "PUT",
      body: { user_ids: userIds },
    }),
  eligibleApprovers: () => apiFetch<ApproverBrief[]>("/approvals/eligible-approvers"),
};

// ---- Signatures ----------------------------------------------------------
export const signaturesApi = {
  list: () => apiFetch<SignatureRequest[]>("/signatures"),
  send: (payload: {
    contract_id: string;
    contract_version_id?: string;
    recipients: SignatureRecipient[];
    override_lifecycle?: boolean;
  }) =>
    apiFetch<SignatureRequest>("/signatures/requests", {
      method: "POST",
      body: payload,
    }),
  sync: (id: string, completed = true, declined = false) =>
    apiFetch<SignatureRequest>(`/signatures/requests/${id}/sync`, {
      method: "POST",
      body: { completed, declined },
    }),
};

// ---- Obligations ---------------------------------------------------------
export const noticesApi = {
  list: (
    params: {
      status_filter?: string; direction?: string; notice_type?: string;
      owner_user_id?: string; contract_id?: string; overdue_only?: boolean; q?: string;
    } = {},
  ) => apiFetch<Notice[]>(`/notices${qs(params)}`),
  summary: () => apiFetch<NoticeSummary>("/notices/summary"),
  /** Chase this org's near/past-deadline notices now. The nightly Celery sweep
   *  does the same across every org; this is the manual equivalent. */
  runReminders: () =>
    apiFetch<NoticeReminderRun>("/notices/run-reminders", { method: "POST" }),
  get: (id: string) => apiFetch<Notice>(`/notices/${id}`),
  create: (payload: Record<string, unknown>) =>
    apiFetch<Notice>("/notices", { method: "POST", body: payload }),
  update: (id: string, payload: Record<string, unknown>) =>
    apiFetch<Notice>(`/notices/${id}`, { method: "PATCH", body: payload }),
  setStatus: (id: string, payload: { status: string; note?: string; response_summary?: string }) =>
    apiFetch<Notice>(`/notices/${id}/status`, { method: "POST", body: payload }),
  addNote: (id: string, body: string) =>
    apiFetch<Notice>(`/notices/${id}/notes`, { method: "POST", body: { body } }),
  remove: (id: string) => apiFetch<void>(`/notices/${id}`, { method: "DELETE" }),
  /** Reads a notice document and proposes field values. Creates nothing. */
  extract: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<NoticeExtraction>("/notices/extract", { method: "POST", form });
  },
  addDocument: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<Notice>(`/notices/${id}/documents`, { method: "POST", form });
  },
  deleteDocument: (id: string, documentId: string) =>
    apiFetch<Notice>(`/notices/${id}/documents/${documentId}`, { method: "DELETE" }),
  /** Draft a reply from the notice + its attachments. Stored, never sent. */
  draftResponse: (id: string) =>
    apiFetch<Notice>(`/notices/${id}/draft-response`, { method: "POST" }),
  escalate: (id: string, payload: { reason: string; type_label?: string; priority?: string }) =>
    apiFetch<Notice>(`/notices/${id}/escalate`, { method: "POST", body: payload }),
};

export const obligationsApi = {
  list: (params: { contract_id?: string; status_filter?: string } = {}) =>
    apiFetch<Obligation[]>(`/obligations${qs(params)}`),
  update: (id: string, payload: Record<string, unknown>) =>
    apiFetch<Obligation>(`/obligations/${id}`, {
      method: "PATCH",
      body: payload,
    }),
  complete: (id: string) =>
    apiFetch<Obligation>(`/obligations/${id}/complete`, { method: "POST" }),
  extract: (contract_id: string) =>
    apiFetch<{ job_id: string; status: string }>(
      `/obligations/extract${qs({ contract_id })}`,
      { method: "POST" },
    ),
  runReminders: () =>
    apiFetch<{
      reminders_sent: number;
      marked_overdue: number;
      marked_due_soon: number;
    }>("/obligations/run-reminders", { method: "POST" }),
};

// ---- Renewals ------------------------------------------------------------
export const renewalsApi = {
  list: (contract_id?: string) =>
    apiFetch<RenewalEvent[]>(`/renewals${qs({ contract_id })}`),
  decide: (
    id: string,
    decision: "renew" | "terminate" | "renegotiate",
    note?: string,
  ) =>
    apiFetch<RenewalEvent>(`/renewals/${id}/decision`, {
      method: "POST",
      body: { decision, note },
    }),
  runWindowCheck: () =>
    apiFetch<{ contracts_moved_to_renewal_due: number }>(
      "/renewals/run-window-check",
      { method: "POST" },
    ),
  recommendation: (id: string) =>
    apiFetch<RenewalRecommendation>(`/renewals/${id}/recommendation`),
};

// ---- Trademarks -----------------------------------------------------------
export const trademarksApi = {
  list: () => apiFetch<Trademark[]>("/trademarks"),
  get: (id: string) => apiFetch<Trademark>(`/trademarks/${id}`),
  create: (payload: TrademarkCreatePayload) =>
    apiFetch<Trademark>("/trademarks", { method: "POST", body: payload }),
  update: (id: string, payload: TrademarkUpdatePayload) =>
    apiFetch<Trademark>(`/trademarks/${id}`, { method: "PATCH", body: payload }),
  intake: (payload: IntakeSubmitPayload) =>
    apiFetch<Trademark>("/trademarks/intake", { method: "POST", body: payload }),
  dashboard: () => apiFetch<TrademarkDashboardMetrics>("/trademarks/dashboard"),
  calendar: (within_days?: number) =>
    apiFetch<RenewalCalendarEntry[]>(`/trademarks/calendar${qs({ within_days })}`),
  searchSimilar: (payload: SearchSimilarRequest) =>
    apiFetch<SearchSimilarResponse>("/trademarks/search-similar", {
      method: "POST",
      body: payload,
    }),
  uploadDocument: (form: FormData) =>
    apiFetch<UploadDocumentResponse>("/trademarks/documents/upload", { form }),
  extractFields: (payload: ExtractRequest) =>
    apiFetch<ExtractResponse>("/trademarks/documents/extract", {
      method: "POST",
      body: payload,
    }),
  ingest: (payload: IngestRequest) =>
    apiFetch<IngestResponse>("/trademarks/documents/ingest", {
      method: "POST",
      body: payload,
    }),
  integrationsStatus: () =>
    apiFetch<IntegrationStatusResponse>("/trademarks/integrations/status"),
  testIntegration: (service: string) =>
    apiFetch<IntegrationTestResponse>(`/trademarks/integrations/test/${service}`, {
      method: "POST",
    }),
};

// ---- Contract Brain ------------------------------------------------------
export const brainApi = {
  search: (q: string, limit = 8) =>
    apiFetch<BrainSearchResponse>(
      `/contract-brain/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  ask: (payload: {
    question: string;
    query_scope?: BrainScope;
    contract_id?: string;
    matter_id?: string;
  }) =>
    apiFetch<BrainQuery>("/contract-brain/ask", {
      method: "POST",
      body: payload,
    }),
  queries: (params: { contract_id?: string; limit?: number } = {}) =>
    apiFetch<BrainQuery[]>(`/contract-brain/queries${qs(params)}`),
};

// ---- Tabular review ------------------------------------------------------
export const tabularApi = {
  list: () => apiFetch<TabularReview[]>("/tabular-reviews"),
  create: (payload: {
    name: string;
    matter_id?: string;
    contract_ids?: string[];
    columns: { name: string; prompt: string }[];
  }) =>
    apiFetch<TabularReview>("/tabular-reviews", {
      method: "POST",
      body: payload,
    }),
  get: (id: string) =>
    apiFetch<TabularReviewDetail>(`/tabular-reviews/${id}`),
  addColumns: (id: string, columns: { name: string; prompt: string }[]) =>
    apiFetch<TabularReviewDetail>(`/tabular-reviews/${id}/columns`, {
      method: "POST",
      body: { columns },
    }),
  addContracts: (id: string, contract_ids: string[]) =>
    apiFetch<TabularReviewDetail>(`/tabular-reviews/${id}/contracts`, {
      method: "POST",
      body: { contract_ids },
    }),
  rerunCell: (id: string, cellId: string) =>
    apiFetch(`/tabular-reviews/${id}/cells/${cellId}/rerun`, {
      method: "POST",
    }),
  chat: (id: string) =>
    apiFetch<TabularReviewChat[]>(`/tabular-reviews/${id}/chat`),
  sendChat: (id: string, message: string) =>
    apiFetch<TabularReviewChat>(`/tabular-reviews/${id}/chat`, {
      method: "POST",
      body: { message },
    }),
  exportXlsx: (id: string) =>
    apiDownload(
      `/tabular-reviews/${id}/export`,
      `tabular-review-${id}.xlsx`,
    ),
};

// ---- Search --------------------------------------------------------------
export const searchApi = {
  contracts: (params: Record<string, unknown>) =>
    apiFetch<ContractResponse[]>(`/search/contracts${qs(params)}`),
  text: (params: { q: string; contract_id?: string; matter_id?: string; limit?: number }) =>
    apiFetch<ContractTextSearchResult[]>(`/search/contract-text${qs(params)}`),
  clauses: (params: Record<string, unknown>) =>
    apiFetch<ClauseSearchResult[]>(`/search/clauses${qs(params)}`),
  projects: (params: Record<string, unknown>) =>
    apiFetch<MatterResponse[]>(`/search/projects${qs(params)}`),
};

// ---- Notifications / jobs / admin / debug --------------------------------
export const notificationsApi = {
  list: () => apiFetch<Notification[]>("/notifications"),
  unreadCount: () => apiFetch<{ count: number }>("/notifications/unread-count"),
  markRead: (id: string) =>
    apiFetch<Notification>(`/notifications/${id}/read`, { method: "POST" }),
  markAllRead: () =>
    apiFetch<{ marked: number }>("/notifications/read-all", { method: "POST" }),
};

export const jobsApi = {
  list: () => apiFetch<JobRun[]>("/jobs"),
  cancel: (id: string) =>
    apiFetch<JobRun>(`/jobs/${id}/cancel`, { method: "POST" }),
  run: (id: string) => apiFetch<JobRun>(`/jobs/${id}/run`, { method: "POST" }),
};

export const aiUsageApi = {
  summary: (days = 30) =>
    apiFetch<AiUsageSummary>(`/analytics/ai-usage${qs({ days })}`),
};

export const adminApi = {
  settings: () => apiFetch<AdminSetting[]>("/admin/settings"),
  upsert: (key: string, value: unknown, is_secret = false) =>
    apiFetch<AdminSetting>("/admin/settings", {
      method: "PUT",
      body: { key, value, is_secret },
    }),
};

export const debugApi = {
  configStatus: () => apiFetch<ConfigStatus>("/debug/config-status"),
};

// ---- External share (public, counterparty — no account) ------------------
function shareQs(passcode?: string) {
  return passcode ? `?passcode=${encodeURIComponent(passcode)}` : "";
}
export const externalShareApi = {
  view: (token: string, passcode?: string) =>
    apiFetch<ExternalShareView>(`/external-shares/${token}${shareQs(passcode)}`, {
      noRetry: true,
    }),
  comments: (token: string, passcode?: string) =>
    apiFetch<ExternalComment[]>(`/external-shares/${token}/comments${shareQs(passcode)}`, {
      noRetry: true,
    }),
  addComment: (
    token: string,
    payload: { author_name?: string; body: string },
    passcode?: string,
  ) =>
    apiFetch<ExternalComment>(`/external-shares/${token}/comments${shareQs(passcode)}`, {
      method: "POST",
      body: payload,
      noRetry: true,
    }),
};

// ---- Legal Intake ---------------------------------------------------------
const intakeQs = (o: Record<string, string | undefined>) => {
  const p = Object.entries(o).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v!)}`);
  return p.length ? `?${p.join("&")}` : "";
};

export const intakeApi = {
  // request types
  listTypes: (includeInactive = false) =>
    apiFetch<IntakeRequestType[]>(`/intake/request-types${includeInactive ? "?include_inactive=true" : ""}`),
  createType: (payload: Record<string, unknown>) =>
    apiFetch<IntakeRequestType>("/intake/request-types", { method: "POST", body: payload }),
  updateType: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeRequestType>(`/intake/request-types/${id}`, { method: "PATCH", body: payload }),
  deleteType: (id: string) => apiFetch<void>(`/intake/request-types/${id}`, { method: "DELETE" }),

  // requests
  list: (statusFilter?: string) =>
    apiFetch<IntakeRequest[]>(`/intake/requests${intakeQs({ status_filter: statusFilter })}`),
  mine: () => apiFetch<IntakeRequest[]>("/intake/requests/mine"),
  get: (id: string) => apiFetch<IntakeRequest>(`/intake/requests/${id}`),
  create: (payload: Record<string, unknown>) =>
    apiFetch<IntakeRequest>("/intake/requests", { method: "POST", body: payload }),
  update: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}`, { method: "PATCH", body: payload }),
  triage: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/triage`, { method: "POST", body: payload }),
  suggestFlow: (id: string) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/suggest-flow`, { method: "POST" }),

  // handoff / custody
  handoff: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/handoff`, { method: "POST", body: payload }),
  handoffs: (id: string) => apiFetch<IntakeHandoff[]>(`/intake/requests/${id}/handoffs`),

  // tasks
  tasks: (id: string) => apiFetch<IntakeTask[]>(`/intake/requests/${id}/tasks`),
  createTask: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeTask>(`/intake/requests/${id}/tasks`, { method: "POST", body: payload }),
  updateTask: (taskId: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeTask>(`/intake/tasks/${taskId}`, { method: "PATCH", body: payload }),
  deleteTask: (taskId: string) => apiFetch<void>(`/intake/tasks/${taskId}`, { method: "DELETE" }),
  logEffort: (taskId: string, minutes: number) =>
    apiFetch<IntakeTask>(`/intake/tasks/${taskId}/effort?minutes=${minutes}`, { method: "POST" }),

  // work + people
  myWork: () => apiFetch<IntakeMyWork>("/intake/my-work"),
  assignees: () => apiFetch<IntakeAssignee[]>("/intake/assignees"),

  // approval ladder + Tier-0 gates
  approvalChain: (id: string) =>
    apiFetch<IntakeApprovalRung[]>(`/intake/requests/${id}/approval-chain`),
  submitForApproval: (id: string, body?: { approver_user_id?: string; approver_role?: string }) =>
    apiFetch<{ request: IntakeRequest; chain: IntakeApprovalRung[] }>(
      `/intake/requests/${id}/submit-for-approval`, { method: "POST", body: body ?? {} }),
  overrideGate: (id: string, body: { gate_key: string; action: "add" | "remove"; reason?: string }) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/gates`, { method: "POST", body }),

  // channel sync — Gmail intake polling (email → request)
  gmailSync: () =>
    apiFetch<{ status: string; fetched?: number; filed?: unknown[]; skipped?: unknown[]; note?: string }>(
      "/intake/gmail-sync", { method: "POST" }),

  // promote
  promote: (id: string, target: "project" | "contract", targetId: string) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/promote`, {
      method: "POST", body: { target, target_id: targetId } }),
  draftContract: (id: string) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/draft-contract`, { method: "POST" }),
  ingestAttachment: (id: string) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/ingest-attachment`, { method: "POST" }),

  // SLA
  slaLegs: (id: string) => apiFetch<IntakeSlaLegs>(`/intake/requests/${id}/sla`),
  pause: (id: string, paused: boolean) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/pause?paused=${paused}`, { method: "POST" }),
  slaOps: () => apiFetch<IntakeSlaOps>("/intake/sla-ops"),
  slaScan: () => apiFetch<{ escalated: number; breached: number; downgraded: number }>(
    "/intake/sla-scan", { method: "POST" }),

  // teams + rules
  teams: () => apiFetch<IntakeTeam[]>("/intake/teams"),
  createTeam: (payload: Record<string, unknown>) =>
    apiFetch<IntakeTeam>("/intake/teams", { method: "POST", body: payload }),
  updateTeam: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeTeam>(`/intake/teams/${id}`, { method: "PATCH", body: payload }),
  deleteTeam: (id: string) => apiFetch<void>(`/intake/teams/${id}`, { method: "DELETE" }),
  rules: () => apiFetch<IntakeRule[]>("/intake/routing-rules"),
  createRule: (payload: Record<string, unknown>) =>
    apiFetch<IntakeRule>("/intake/routing-rules", { method: "POST", body: payload }),
  updateRule: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeRule>(`/intake/routing-rules/${id}`, { method: "PATCH", body: payload }),
  deleteRule: (id: string) => apiFetch<void>(`/intake/routing-rules/${id}`, { method: "DELETE" }),

  // knowledge base + pool ops + copilot (Phase 2)
  kb: () => apiFetch<IntakeKbArticle[]>("/intake/kb"),
  kbAll: () => apiFetch<IntakeKbArticle[]>("/intake/kb/all"),
  createKb: (payload: Record<string, unknown>) =>
    apiFetch<IntakeKbArticle>("/intake/kb", { method: "POST", body: payload }),
  updateKb: (id: string, payload: Record<string, unknown>) =>
    apiFetch<IntakeKbArticle>(`/intake/kb/${id}`, { method: "PATCH", body: payload }),
  deleteKb: (id: string) => apiFetch<void>(`/intake/kb/${id}`, { method: "DELETE" }),
  poolOps: (days = 30) => apiFetch<IntakePoolOps>(`/intake/pool-ops?days=${days}`),
  screen: (id: string) =>
    apiFetch<Record<string, unknown>>(`/intake/requests/${id}/screen`, { method: "POST" }),
  documents: (id: string) =>
    apiFetch<IntakeDocument[]>(`/intake/requests/${id}/documents`),
  uploadDocument: (id: string, payload: { filename: string; mime_type: string; content_b64: string }) =>
    apiFetch<IntakeDocument>(`/intake/requests/${id}/documents`, { method: "POST", body: payload }),
  setParties: (id: string, parties: { name: string; role: string; is_person?: boolean }[]) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/parties`, { method: "PUT", body: { parties } }),
  sanctionsRefresh: () =>
    apiFetch<Record<string, unknown>>("/intake/sanctions/refresh", { method: "POST" }),
  copilotTurn: (messages: { role: string; content: string }[], userMessage: string) =>
    apiFetch<CopilotTurn>("/intake/copilot/turn", { method: "POST", body: { messages, user_message: userMessage } }),
  copilotFile: (payload: Record<string, unknown>) =>
    apiFetch<IntakeRequest>("/intake/copilot/file", { method: "POST", body: payload }),
};
