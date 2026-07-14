// Typed endpoint functions, grouped by backend module.
import { apiFetch, apiDownload } from "./api";
import type {
  AISkillRunResponse,
  AIPromptVersionResponse,
  AdminSetting,
  ApiKeyResponse,
  ApprovalChainStep,
  ApprovalRequest,
  ApprovalReviewContext,
  ApprovalRoutingRule,
  ApproverGroup,
  ApproverBrief,
  AssistantMessage,
  AssistantRun,
  AssistantSession,
  AssistantToolCall,
  BrainQuery,
  BrainSearchResponse,
  BrainScope,
  ClauseSearchResult,
  ConfigStatus,
  ContractActivityResponse,
  ContractEditResponse,
  ContractFileResponse,
  ConsoleResponse,
  ContractHubResponse,
  ContractComment,
  ContractParty,
  ContractResponse,
  ContractRiskSummary,
  ExternalComment,
  SignerOption,
  ExternalShareView,
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
  Obligation,
  OrganizationResponse,
  OrgJoinRequestResponse,
  BuildChatResponse,
  ExtractedDoc,
  PlaybookDraftRule,
  PlaybookInsights,
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
  RoleResponse,
  PermissionInfo,
  GrantResponse,
  WallResponse,
  AuthorityGrantResponse,
  Workflow,
  WorkflowVersion,
  WorkflowUsage,
  IntakeRequest,
  IntakeRequestType,
  IntakeTask,
  IntakeHandoff,
  IntakeAssignee,
  IntakeMyWork,
  IntakeRecommendation,
  IntakeSlaLegs,
  IntakeSlaOps,
  IntakeTeam,
  IntakeRule,
  IntakeKbArticle,
  IntakePoolOps,
  IntakeDocument,
  IntakeAgentMetrics,
  CopilotTurn,
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
  self: () =>
    apiFetch<Record<string, { gated: boolean; grants: AuthorityGrantResponse[] }>>(
      "/authority-grants/self",
    ),
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
  update: (id: string, payload: Record<string, unknown>) =>
    apiFetch<AuthorityGrantResponse>(`/authority-grants/${id}`, {
      method: "PATCH",
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
  risk: (id: string) =>
    apiFetch<ContractRiskSummary>(`/contracts/${id}/risk`),
  computeRisk: (id: string) =>
    apiFetch<ContractRiskSummary>(`/contracts/${id}/risk`, { method: "POST" }),
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
  reviewStatus: (id: string) =>
    apiFetch<ReviewStatusResponse>(`/contracts/${id}/review-status`),
  logCounterpartyRevision: (id: string, file: File, change_summary?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (change_summary) form.append("change_summary", change_summary);
    return apiFetch<ContractVersionResponse>(`/contracts/${id}/counterparty-revision`, {
      method: "POST",
      form,
    });
  },
  parties: (id: string) => apiFetch<ContractParty[]>(`/contracts/${id}/parties`),
  addParty: (
    id: string,
    payload: { name: string; contact_email?: string; party_type?: string },
  ) => apiFetch<ContractParty>(`/contracts/${id}/parties`, { method: "POST", body: payload }),
  deleteParty: (id: string, partyId: string) =>
    apiFetch<void>(`/contracts/${id}/parties/${partyId}`, { method: "DELETE" }),
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
  files: (id: string) =>
    apiFetch<ContractFileResponse[]>(`/contracts/${id}/files`),
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
  revokeShare: (id: string, shareId: string) =>
    apiFetch<ContractShareResponse>(
      `/contracts/${id}/shares/${shareId}/revoke`,
      { method: "POST" },
    ),
  hub: () => apiFetch<ContractHubResponse>("/contract-hub"),
  console: () => apiFetch<ConsoleResponse>("/contract-hub/console"),
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
  ) => apiFetch<Workflow>(`/workflows/${id}`, { method: "PATCH", body: payload }),
  versions: (id: string) =>
    apiFetch<WorkflowVersion[]>(`/workflows/${id}/versions`),
  revert: (id: string, versionId: string) =>
    apiFetch<Workflow>(`/workflows/${id}/versions/${versionId}/revert`, {
      method: "POST",
    }),
  launch: (id: string, payload: { mode?: string; contract_id?: string } = {}) =>
    apiFetch<void>(`/workflows/${id}/launch`, { method: "POST", body: payload }),
  analytics: () =>
    apiFetch<Record<string, WorkflowUsage>>("/workflows/analytics"),
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
  updateGroup: (
    id: string,
    payload: { name?: string; description?: string; is_active?: boolean },
  ) => apiFetch<ApproverGroup>(`/approvals/groups/${id}`, { method: "PATCH", body: payload }),
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
  search: (q: string, limit = 8) =>
    apiFetch<BrainSearchResponse>(
      `/contract-brain/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
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
  unreadCount: () => apiFetch<{ count: number }>("/notifications/unread-count"),
  markRead: (id: string) =>
    apiFetch<Notification>(`/notifications/${id}/read`, { method: "POST" }),
  markAllRead: () =>
    apiFetch<{ marked: number }>("/notifications/read-all", { method: "POST" }),
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

  // recommendation / verdicts / promote
  recommendation: (id: string) =>
    apiFetch<IntakeRecommendation | null>(`/intake/requests/${id}/recommendation`),
  bulkTriage: (ids: string[], action: string) =>
    apiFetch<{ results: { id: string; ok: boolean; error?: string }[] }>(
      "/intake/requests/bulk-triage", { method: "POST", body: { ids, action } }),
  promote: (id: string, target: "project" | "contract", targetId: string) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/promote`, {
      method: "POST", body: { target, target_id: targetId } }),
  draftContract: (id: string) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/draft-contract`, { method: "POST" }),

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
  agentMetrics: () => apiFetch<IntakeAgentMetrics>("/intake/agent-metrics"),
  setParties: (id: string, parties: { name: string; role: string; is_person?: boolean }[]) =>
    apiFetch<IntakeRequest>(`/intake/requests/${id}/parties`, { method: "PUT", body: { parties } }),
  sanctionsRefresh: () =>
    apiFetch<Record<string, unknown>>("/intake/sanctions/refresh", { method: "POST" }),
  copilotTurn: (messages: { role: string; content: string }[], userMessage: string) =>
    apiFetch<CopilotTurn>("/intake/copilot/turn", { method: "POST", body: { messages, user_message: userMessage } }),
  copilotFile: (payload: Record<string, unknown>) =>
    apiFetch<IntakeRequest>("/intake/copilot/file", { method: "POST", body: payload }),
};
