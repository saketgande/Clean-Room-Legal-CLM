// Typed endpoint functions, grouped by backend module.
import { apiFetch, apiDownload } from "./api";
import type {
  AISkillRunResponse,
  AIPromptVersionResponse,
  AdminSetting,
  ApiKeyResponse,
  ApprovalRequest,
  ApprovalRoutingRule,
  AssistantMessage,
  AssistantRun,
  AssistantSession,
  AssistantToolCall,
  BrainQuery,
  BrainScope,
  ClauseSearchResult,
  ConfigStatus,
  ContractActivityResponse,
  ContractEditResponse,
  ContractFileResponse,
  ContractHubResponse,
  ContractResponse,
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
  Obligation,
  OrganizationResponse,
  OrgJoinRequestResponse,
  PlaybookResponse,
  PlaybookRuleResponse,
  PlaybookRunDetailResponse,
  PlaybookRunResponse,
  PlaybookVersionResponse,
  ProjectContractResponse,
  ProjectFolderResponse,
  ProjectMemberResponse,
  ProjectResponse,
  ProjectShareResponse,
  RegistrationResponse,
  RenewalEvent,
  SignatureRecipient,
  SignatureRequest,
  SkillInfo,
  TabularReview,
  TabularReviewChat,
  TabularReviewDetail,
  TokenResponse,
  ToolInfo,
  UserInvitationResponse,
  UserResponse,
  Workflow,
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
export const usersApi = {
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
  listJoinRequests: () =>
    apiFetch<OrgJoinRequestResponse[]>("/users/join-requests"),
  decideJoinRequest: (
    id: string,
    decision: "approve" | "reject",
    role_name = "member",
    reason?: string,
  ) =>
    apiFetch<OrgJoinRequestResponse>(`/users/join-requests/${id}/decision`, {
      method: "POST",
      body: { decision, role_name, reason },
    }),
  listApiKeys: () => apiFetch<ApiKeyResponse[]>("/users/api-keys"),
  createApiKey: (name: string) =>
    apiFetch<ApiKeyResponse>("/users/api-keys", {
      method: "POST",
      body: { name },
    }),
  revokeApiKey: (id: string) =>
    apiFetch<ApiKeyResponse>(`/users/api-keys/${id}/revoke`, { method: "POST" }),
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

// ---- Projects ------------------------------------------------------------
export const projectsApi = {
  list: () => apiFetch<ProjectResponse[]>("/projects"),
  create: (payload: {
    name: string;
    description?: string;
    project_type?: string;
    metadata_json?: Record<string, unknown>;
  }) => apiFetch<ProjectResponse>("/projects", { method: "POST", body: payload }),
  get: (id: string) => apiFetch<ProjectResponse>(`/projects/${id}`),
  update: (id: string, payload: Record<string, unknown>) =>
    apiFetch<ProjectResponse>(`/projects/${id}`, {
      method: "PATCH",
      body: payload,
    }),
  remove: (id: string) =>
    apiFetch<void>(`/projects/${id}`, { method: "DELETE" }),
  folders: (id: string) =>
    apiFetch<ProjectFolderResponse[]>(`/projects/${id}/folders`),
  createFolder: (id: string, name: string, parent_folder_id?: string) =>
    apiFetch<ProjectFolderResponse>(`/projects/${id}/folders`, {
      method: "POST",
      body: { name, parent_folder_id },
    }),
  members: (id: string) =>
    apiFetch<ProjectMemberResponse[]>(`/projects/${id}/members`),
  upsertMember: (id: string, user_id: string, role = "member") =>
    apiFetch<ProjectMemberResponse>(`/projects/${id}/members`, {
      method: "PUT",
      body: { user_id, role },
    }),
  removeMember: (id: string, userId: string) =>
    apiFetch<void>(`/projects/${id}/members/${userId}`, { method: "DELETE" }),
  shares: (id: string) =>
    apiFetch<ProjectShareResponse[]>(`/projects/${id}/shares`),
  createShare: (
    id: string,
    user_id: string,
    access_level = "read",
    expires_at?: string,
  ) =>
    apiFetch<ProjectShareResponse>(`/projects/${id}/shares`, {
      method: "POST",
      body: { user_id, access_level, expires_at },
    }),
  contracts: (id: string) =>
    apiFetch<ProjectContractResponse[]>(`/projects/${id}/contracts`),
  addContract: (id: string, contract_id: string, folder_id?: string) =>
    apiFetch<ProjectContractResponse>(`/projects/${id}/contracts`, {
      method: "PUT",
      body: { contract_id, folder_id },
    }),
  removeContract: (id: string, contractId: string) =>
    apiFetch<void>(`/projects/${id}/contracts/${contractId}`, {
      method: "DELETE",
    }),
};

// ---- Contracts -----------------------------------------------------------
export const contractsApi = {
  list: () => apiFetch<ContractResponse[]>("/contracts"),
  get: (id: string) => apiFetch<ContractResponse>(`/contracts/${id}`),
  upload: (
    file: File,
    extra: { title?: string; counterparty_name?: string; project_id?: string } = {},
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (extra.title) form.append("title", extra.title);
    if (extra.counterparty_name)
      form.append("counterparty_name", extra.counterparty_name);
    if (extra.project_id) form.append("project_id", extra.project_id);
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
  activity: (id: string, limit = 100) =>
    apiFetch<ContractActivityResponse[]>(
      `/contracts/${id}/activity${qs({ limit })}`,
    ),
  files: (id: string) =>
    apiFetch<ContractFileResponse[]>(`/contracts/${id}/files`),
  versions: (id: string) =>
    apiFetch<ContractVersionResponse[]>(`/contracts/${id}/versions`),
  versionText: (id: string, versionId: string) =>
    apiFetch<ContractTextSnapshotResponse>(
      `/contracts/${id}/versions/${versionId}/text`,
    ),
  downloadVersion: (id: string, versionId: string, name?: string) =>
    apiDownload(`/contracts/${id}/versions/${versionId}/download`, name),
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
  revokeShare: (id: string, shareId: string) =>
    apiFetch<ContractShareResponse>(
      `/contracts/${id}/shares/${shareId}/revoke`,
      { method: "POST" },
    ),
  hub: () => apiFetch<ContractHubResponse>("/contract-hub"),
};

// ---- Assistant -----------------------------------------------------------
export const assistantApi = {
  sessions: (params: Record<string, unknown> = {}) =>
    apiFetch<AssistantSession[]>(`/assistant/sessions${qs(params)}`),
  createSession: (payload: {
    session_type?: string;
    title?: string;
    project_id?: string;
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
  addContract: (id: string, contract_id: string, handle?: string) =>
    apiFetch(`/assistant/sessions/${id}/contracts`, {
      method: "POST",
      body: { contract_id, handle },
    }),
  messages: (id: string, limit = 100) =>
    apiFetch<AssistantMessage[]>(
      `/assistant/sessions/${id}/messages${qs({ limit })}`,
    ),
  runs: (id: string, limit = 50) =>
    apiFetch<AssistantRun[]>(`/assistant/sessions/${id}/runs${qs({ limit })}`),
  run: (runId: string) =>
    apiFetch<{ assistant_run: AssistantRun; tool_calls: AssistantToolCall[] }>(
      `/assistant/runs/${runId}`,
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
  tools: () => apiFetch<ToolInfo[]>("/assistant/tools"),
};

// ---- AI ------------------------------------------------------------------
export const aiApi = {
  skills: () => apiFetch<SkillInfo[]>("/ai/skills"),
  skillRuns: () => apiFetch<AISkillRunResponse[]>("/ai/skill-runs"),
  promptVersions: () =>
    apiFetch<AIPromptVersionResponse[]>("/ai/prompt-versions"),
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
  rerunEmbeddings: (contractId: string) =>
    apiFetch(`/ai/contracts/${contractId}/embeddings`, {
      method: "POST",
      body: {},
    }),
};

// ---- Workflows -----------------------------------------------------------
export const workflowsApi = {
  list: () => apiFetch<Workflow[]>("/workflows"),
  create: (payload: {
    name: string;
    workflow_type?: string;
    visibility?: string;
    description?: string;
    definition?: Record<string, unknown>;
  }) => apiFetch<Workflow>("/workflows", { method: "POST", body: payload }),
};

// ---- Playbooks -----------------------------------------------------------
export const playbooksApi = {
  list: () => apiFetch<PlaybookResponse[]>("/playbooks"),
  create: (name: string, description?: string) =>
    apiFetch<PlaybookResponse>("/playbooks", {
      method: "POST",
      body: { name, description },
    }),
  generate: (payload: {
    name: string;
    description?: string;
    contract_type?: string;
    focus_areas?: string[];
  }) =>
    apiFetch<PlaybookResponse>("/playbooks/generate", {
      method: "POST",
      body: payload,
    }),
  get: (id: string) => apiFetch<PlaybookResponse>(`/playbooks/${id}`),
  update: (id: string, payload: { name?: string; description?: string }) =>
    apiFetch<PlaybookResponse>(`/playbooks/${id}`, {
      method: "PATCH",
      body: payload,
    }),
  remove: (id: string) =>
    apiFetch<void>(`/playbooks/${id}`, { method: "DELETE" }),
  versions: (id: string) =>
    apiFetch<PlaybookVersionResponse[]>(`/playbooks/${id}/versions`),
  createVersion: (id: string, source_version_id?: string, summary?: string) =>
    apiFetch<PlaybookVersionResponse>(`/playbooks/${id}/versions`, {
      method: "POST",
      body: { source_version_id, summary },
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
  list: () => apiFetch<ApprovalRequest[]>("/approvals"),
  routingRules: () =>
    apiFetch<ApprovalRoutingRule[]>("/approvals/routing-rules"),
  createRoutingRule: (payload: Record<string, unknown>) =>
    apiFetch<ApprovalRoutingRule>("/approvals/routing-rules", {
      method: "POST",
      body: payload,
    }),
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
  tokenDecide: (token: string, decision: "approve" | "reject", comment?: string) =>
    apiFetch<{ approval_request_id: string; status: string; contract_id: string }>(
      "/approvals/token-decision",
      { method: "POST", body: { token, decision, comment }, noRetry: true },
    ),
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
export const obligationsApi = {
  list: (params: { contract_id?: string; status_filter?: string } = {}) =>
    apiFetch<Obligation[]>(`/obligations${qs(params)}`),
  get: (id: string) => apiFetch<Obligation>(`/obligations/${id}`),
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
  get: (id: string) => apiFetch<RenewalEvent>(`/renewals/${id}`),
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
};

// ---- Contract Brain ------------------------------------------------------
export const brainApi = {
  ask: (payload: {
    question: string;
    query_scope?: BrainScope;
    contract_id?: string;
    project_id?: string;
  }) =>
    apiFetch<BrainQuery>("/contract-brain/ask", {
      method: "POST",
      body: payload,
    }),
  queries: (params: { contract_id?: string; limit?: number } = {}) =>
    apiFetch<BrainQuery[]>(`/contract-brain/queries${qs(params)}`),
  precedents: (query: string, contract_id?: string, limit = 5) =>
    apiFetch<ContractResponse[]>(
      `/contract-brain/precedents${qs({ query, contract_id, limit })}`,
    ),
  ingest: (contract_id: string) =>
    apiFetch<{ job_id: string; status: string }>(
      `/contract-brain/ingest${qs({ contract_id })}`,
      { method: "POST" },
    ),
};

// ---- Tabular review ------------------------------------------------------
export const tabularApi = {
  list: () => apiFetch<TabularReview[]>("/tabular-reviews"),
  create: (payload: {
    name: string;
    project_id?: string;
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
  text: (params: { q: string; contract_id?: string; project_id?: string; limit?: number }) =>
    apiFetch<ContractTextSearchResult[]>(`/search/contract-text${qs(params)}`),
  clauses: (params: Record<string, unknown>) =>
    apiFetch<ClauseSearchResult[]>(`/search/clauses${qs(params)}`),
  projects: (params: Record<string, unknown>) =>
    apiFetch<ProjectResponse[]>(`/search/projects${qs(params)}`),
};

// ---- Notifications / jobs / admin / debug --------------------------------
export const notificationsApi = {
  list: () => apiFetch<Notification[]>("/notifications"),
};

export const jobsApi = {
  list: () => apiFetch<JobRun[]>("/jobs"),
  get: (id: string) => apiFetch<JobRun>(`/jobs/${id}`),
  cancel: (id: string) =>
    apiFetch<JobRun>(`/jobs/${id}/cancel`, { method: "POST" }),
  run: (id: string) => apiFetch<JobRun>(`/jobs/${id}/run`, { method: "POST" }),
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
  health: () => apiFetch<{ status: string; app: string; environment: string }>(
    "/debug/health",
  ),
  configStatus: () => apiFetch<ConfigStatus>("/debug/config-status"),
};
