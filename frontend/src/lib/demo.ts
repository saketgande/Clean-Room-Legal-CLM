// Demo mode: serve coherent in-memory fixtures so the entire UI can be
// explored with no backend. Toggled by a localStorage flag set on the login
// screen ("Explore demo").

const DEMO_KEY = "aegis.demo";

export function isDemo(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(DEMO_KEY) === "1";
}

export function enableDemo() {
  window.localStorage.setItem(DEMO_KEY, "1");
}

export function disableDemo() {
  window.localStorage.removeItem(DEMO_KEY);
}

const now = Date.now();
const iso = (offsetDays = 0) =>
  new Date(now + offsetDays * 86400000).toISOString();
const date = (offsetDays = 0) => iso(offsetDays).slice(0, 10);

const USER = {
  id: "u-1",
  org_id: "org-1",
  email: "jane.counsel@acme-legal.com",
  full_name: "Jane Counsel",
  status: "active",
  roles: ["org_admin", "counsel"],
  active_role_id: "r-admin",
  active_role_name: "org_admin",
};

const CONTRACTS = [
  {
    id: "c-1",
    org_id: "org-1",
    title: "Acme ↔ Globex Master Services Agreement",
    contract_type: "msa",
    lifecycle_stage: "counterparty_review",
    owner_user_id: "u-1",
    counterparty_name: "Globex Corporation",
    jurisdiction: "Delaware, USA",
    risk_level: "high",
    value_amount: 480000,
    currency: "USD",
    effective_date: date(-20),
    expiration_date: date(345),
    current_contract_file_id: "cf-1",
    current_authoritative_version_id: "v-2",
    metadata_json: {},
  },
  {
    id: "c-2",
    org_id: "org-1",
    title: "Mutual NDA — Initech Partnership",
    contract_type: "nda",
    lifecycle_stage: "active",
    owner_user_id: "u-1",
    counterparty_name: "Initech LLC",
    jurisdiction: "California, USA",
    risk_level: "low",
    value_amount: null,
    currency: null,
    effective_date: date(-90),
    expiration_date: date(275),
    current_contract_file_id: "cf-2",
    current_authoritative_version_id: "v-5",
    metadata_json: {},
  },
  {
    id: "c-3",
    org_id: "org-1",
    title: "Cloud Infrastructure SaaS Subscription — Hooli",
    contract_type: "saas",
    lifecycle_stage: "approval_pending",
    owner_user_id: "u-1",
    counterparty_name: "Hooli Inc.",
    jurisdiction: "New York, USA",
    risk_level: "medium",
    value_amount: 1250000,
    currency: "USD",
    effective_date: date(-5),
    expiration_date: date(360),
    current_contract_file_id: "cf-3",
    current_authoritative_version_id: "v-7",
    metadata_json: {},
  },
  {
    id: "c-4",
    org_id: "org-1",
    title: "Vendor Data Processing Addendum — Soylent",
    contract_type: "dpa",
    lifecycle_stage: "renewal_due",
    owner_user_id: "u-1",
    counterparty_name: "Soylent Corp",
    jurisdiction: "EU (GDPR)",
    risk_level: "critical",
    value_amount: 92000,
    currency: "EUR",
    effective_date: date(-330),
    expiration_date: date(35),
    current_contract_file_id: "cf-4",
    current_authoritative_version_id: "v-9",
    metadata_json: {},
  },
];

const VERSIONS = [
  { id: "v-1", contract_id: "c-1", contract_file_id: "cf-1", version_number: 1, storage_object_id: "s-1", text_snapshot_id: "t-1", source: "upload", change_summary: "Original counterparty paper", is_authoritative: false },
  { id: "v-2", contract_id: "c-1", contract_file_id: "cf-1", version_number: 2, storage_object_id: "s-2", text_snapshot_id: "t-2", source: "playbook_redline", change_summary: "AEGIS playbook redline — 7 deviations addressed", is_authoritative: true },
];

const EDITS = [
  { id: "e-1", contract_id: "c-1", contract_version_id: "v-2", edit_type: "clause_replacement", status: "proposed", original_text: "Either party may terminate for convenience upon 90 days notice.", replacement_text: "Either party may terminate for convenience upon 30 days written notice.", rationale: "Playbook requires ≤30 day termination-for-convenience window.", citation: null },
  { id: "e-2", contract_id: "c-1", contract_version_id: "v-2", edit_type: "clause_insertion", status: "proposed", original_text: null, replacement_text: "Vendor shall maintain cyber-liability insurance of no less than $5,000,000.", rationale: "Required insurance language missing from counterparty draft.", citation: null },
];

const ACTIVITY = [
  { id: "a-1", event_type: "contract.uploaded", title: "Contract uploaded from Globex paper", details: {}, request_id: null, job_id: null, skill_run_id: null, assistant_run_id: null, ai_call_id: null, created_at: iso(-7) },
  { id: "a-2", event_type: "playbook.run", title: "MSA Playbook v3 executed — 7 deviations", details: {}, request_id: null, job_id: null, skill_run_id: "sr-1", assistant_run_id: null, ai_call_id: "ai-1", created_at: iso(-5) },
  { id: "a-3", event_type: "contract.lifecycle_changed", title: "Stage: internal_review → counterparty_review", details: {}, request_id: null, job_id: null, skill_run_id: null, assistant_run_id: null, ai_call_id: null, created_at: iso(-2) },
];

const PROJECTS = [
  { id: "p-1", org_id: "org-1", name: "Globex Vendor Onboarding", description: "MSA, DPA and SOW review for the Globex partnership.", project_type: "contract_review", owner_user_id: "u-1", metadata_json: {} },
  { id: "p-2", org_id: "org-1", name: "Q3 M&A Due Diligence", description: "Target company contract portfolio review.", project_type: "due_diligence", owner_user_id: "u-1", metadata_json: {} },
];

const PLAYBOOKS = [
  { id: "pb-1", name: "Master Services Agreement Playbook", description: "Standard positions for vendor MSAs.", status: "published", current_version_id: "pv-3" },
  { id: "pb-2", name: "NDA Fast-Track Playbook", description: "Green/yellow/red triage rules for inbound NDAs.", status: "draft", current_version_id: "pv-1n" },
];

const ASSISTANT_SESSIONS = [
  { id: "as-1", org_id: "org-1", session_type: "contract", title: "Globex MSA review", project_id: "p-1", contract_id: "c-1", tabular_review_id: null, status: "active", created_by_user_id: "u-1", created_at: iso(-3), updated_at: iso(-1) },
  { id: "as-3", org_id: "org-1", session_type: "project", title: "Globex onboarding diligence", project_id: "p-1", contract_id: null, tabular_review_id: null, status: "active", created_by_user_id: "u-1", created_at: iso(-2), updated_at: iso(0) },
  { id: "as-2", org_id: "org-1", session_type: "general", title: "Portfolio risk questions", project_id: null, contract_id: null, tabular_review_id: null, status: "active", created_by_user_id: "u-1", created_at: iso(-6), updated_at: iso(-4) },
];

const TABULAR = [
  { id: "tr-1", org_id: "org-1", name: "Vendor MSA comparison", project_id: "p-1", source_contract_ids: ["c-1", "c-3"], status: "complete", metadata_json: {}, created_by_user_id: "u-1", created_at: iso(-4), updated_at: iso(-1) },
  { id: "tr-2", org_id: "org-1", name: "DPA GDPR coverage matrix", project_id: null, source_contract_ids: ["c-4"], status: "running", metadata_json: {}, created_by_user_id: "u-1", created_at: iso(-1), updated_at: iso(0) },
];

const cTitle = (id: string) =>
  CONTRACTS.find((c) => c.id === id)?.title ?? id;

function ok<T>(data: T): T {
  return data;
}

/** Returns mock data for a path, or undefined if unmatched. */
export function getMock(
  path: string,
  method: string,
  body?: unknown,
): unknown {
  const p = path.split("?")[0];
  const m = method.toUpperCase();

  // Auth / org / users
  if (p === "/auth/me") return ok(USER);
  if (p === "/organizations/current")
    return ok({ id: "org-1", name: "Acme Legal", slug: "acme-legal", allowed_domains: ["acme-legal.com"], default_role_name: "member" });
  if (p === "/users/invitations")
    return ok([
      { id: "inv-1", email: "newcounsel@acme-legal.com", role_name: "counsel", expires_at: iso(5), accepted_at: null, revoked_at: null, token: null },
    ]);
  if (p === "/users/join-requests")
    return ok([
      { id: "jr-1", org_id: "org-1", email: "contractor@partner.com", full_name: "Sam Partner", requested_domain: "partner.com", message: "Need access for the Globex deal.", status: "pending", decision_reason: null, invitation_token: null },
    ]);

  // Contract hub
  if (p === "/contract-hub")
    return ok({
      contracts_by_stage: { intake: 3, drafting: 2, ai_review: 4, internal_review: 5, counterparty_review: 6, approval_pending: 3, approved: 2, signature_pending: 1, active: 14, renewal_due: 3, closed: 4, archived: 7 },
      contracts_by_risk: { low: 18, medium: 21, high: 9, critical: 4 },
      total_contract_versions: 142,
      widgets: {
        pending_approvals: 3,
        pending_signatures: 1,
        upcoming_renewals: 3,
        overdue_obligations: 2,
        top_deviated_clauses: [
          { clause_type: "limitation_of_liability", count: 12 },
          { clause_type: "indemnification", count: 9 },
          { clause_type: "termination", count: 7 },
          { clause_type: "data_protection", count: 5 },
        ],
        average_cycle_time_days: 18.4,
        counterparty_friction: [
          { counterparty_name: "Globex Corporation", count: 4 },
          { counterparty_name: "Hooli Inc.", count: 2 },
        ],
        recent_activity: ACTIVITY.map((a) => ({
          id: a.id,
          resource_id: "c-1",
          event_type: a.event_type,
          title: a.title,
          details: {},
          created_at: a.created_at,
        })),
      },
    });

  // Contracts
  if (p === "/contracts" && m === "GET") return ok(CONTRACTS);
  const cMatch = /^\/contracts\/([^/]+)$/.exec(p);
  if (cMatch && m === "GET")
    return ok(CONTRACTS.find((c) => c.id === cMatch[1]) ?? CONTRACTS[0]);
  if (/^\/contracts\/[^/]+$/.test(p) && m === "PATCH")
    return ok({ ...CONTRACTS[0], ...(body as object) });
  if (/^\/contracts\/[^/]+\/lifecycle$/.test(p) && m === "GET")
    return ok({ current_stage: "counterparty_review", allowed_transitions: ["ai_review", "internal_review", "approval_pending"] });
  if (/^\/contracts\/[^/]+\/lifecycle$/.test(p) && m === "POST")
    return ok(CONTRACTS[0]);
  if (/^\/contracts\/[^/]+\/stage-history$/.test(p))
    return ok([
      { id: "sh-1", contract_id: "c-1", from_stage: null, to_stage: "intake", reason: "Uploaded", changed_by_user_id: "u-1", changed_at: iso(-7), override_used: false },
      { id: "sh-2", contract_id: "c-1", from_stage: "intake", to_stage: "ai_review", reason: "Auto", changed_by_user_id: "u-1", changed_at: iso(-6), override_used: false },
      { id: "sh-3", contract_id: "c-1", from_stage: "ai_review", to_stage: "internal_review", reason: "Playbook complete", changed_by_user_id: "u-1", changed_at: iso(-5), override_used: false },
      { id: "sh-4", contract_id: "c-1", from_stage: "internal_review", to_stage: "counterparty_review", reason: "Sent to Globex", changed_by_user_id: "u-1", changed_at: iso(-2), override_used: false },
    ]);
  if (/^\/contracts\/[^/]+\/activity$/.test(p)) return ok(ACTIVITY);
  if (/^\/contracts\/[^/]+\/files$/.test(p))
    return ok([{ id: "cf-1", contract_id: "c-1", current_version_id: "v-2", file_label: "Main agreement" }]);
  if (/^\/contracts\/[^/]+\/versions$/.test(p) && m === "GET")
    return ok(VERSIONS);
  if (/\/versions\/[^/]+\/text$/.test(p))
    return ok({ id: "t-2", contract_id: "c-1", contract_version_id: "v-2", extraction_method: "text_extraction", extraction_quality_score: 0.97, ocr_provider: null, validation_status: "valid", text: "MASTER SERVICES AGREEMENT\n\nThis Master Services Agreement is entered into between Acme and Globex Corporation…" });
  if (/^\/contracts\/[^/]+\/edits$/.test(p)) return ok(EDITS);
  if (/\/edits\/[^/]+\/(accept|reject)$/.test(p))
    return ok({ ...EDITS[0], status: p.endsWith("accept") ? "accepted" : "rejected" });
  if (/^\/contracts\/[^/]+\/shares$/.test(p) && m === "GET")
    return ok([{ id: "csh-1", contract_id: "c-1", contract_version_id: "v-2", access_mode: "view_only", expires_at: iso(14), revoked_at: null, download_allowed: false }]);
  if (/^\/contracts\/[^/]+\/shares$/.test(p) && m === "POST")
    return ok({ share: { id: "csh-2", contract_id: "c-1", contract_version_id: "v-2", access_mode: "view_only", expires_at: iso(7), revoked_at: null, download_allowed: false }, token: "demo-share-token-abc123" });
  if (p === "/contracts/upload") return ok({ contract: CONTRACTS[0], contract_file_id: "cf-1", contract_version_id: "v-1", text_snapshot_id: "t-1", extraction_method: "text_extraction", extraction_quality_score: 0.95, queued_jobs: ["job-ocr-1"] });

  // Projects
  if (p === "/projects" && m === "GET") return ok(PROJECTS);
  if (p === "/projects" && m === "POST")
    return ok({ ...PROJECTS[0], id: "p-new", ...(body as object) });
  const pMatch = /^\/projects\/([^/]+)$/.exec(p);
  if (pMatch && m === "GET")
    return ok(PROJECTS.find((x) => x.id === pMatch[1]) ?? PROJECTS[0]);
  if (/^\/projects\/[^/]+\/folders$/.test(p) && m === "GET")
    return ok([
      { id: "fld-1", project_id: "p-1", parent_folder_id: null, name: "Master agreements" },
      { id: "fld-2", project_id: "p-1", parent_folder_id: null, name: "Addenda & SOWs" },
    ]);
  if (/^\/projects\/[^/]+\/members$/.test(p) && m === "GET")
    return ok([
      { id: "pm-1", project_id: "p-1", user_id: "u-1", role: "owner" },
      { id: "pm-2", project_id: "p-1", user_id: "u-2", role: "member" },
    ]);
  if (/^\/projects\/[^/]+\/shares$/.test(p) && m === "GET")
    return ok([{ id: "ps-1", project_id: "p-1", shared_with_user_id: "u-3", access_level: "read", expires_at: iso(30), revoked_at: null }]);
  if (/^\/projects\/[^/]+\/contracts$/.test(p) && m === "GET")
    return ok([
      { id: "pc-1", project_id: "p-1", contract_id: "c-1", folder_id: "fld-1" },
      { id: "pc-2", project_id: "p-1", contract_id: "c-4", folder_id: "fld-2" },
    ]);

  // Assistant
  if (p === "/assistant/sessions" && m === "GET") {
    const query = path.includes("?")
      ? new URLSearchParams(path.split("?")[1])
      : null;
    const projectId = query?.get("project_id");
    const contractId = query?.get("contract_id");
    let sessions = ASSISTANT_SESSIONS;
    if (projectId) sessions = sessions.filter((s) => s.project_id === projectId);
    if (contractId)
      sessions = sessions.filter((s) => s.contract_id === contractId);
    return ok(sessions);
  }
  if (p === "/assistant/sessions" && m === "POST") {
    const b = (body ?? {}) as Record<string, unknown>;
    return ok({
      id: "as-new",
      org_id: "org-1",
      session_type: (b.session_type as string) ?? "general",
      title: (b.title as string) ?? "New conversation",
      project_id: (b.project_id as string) ?? null,
      contract_id: (b.contract_id as string) ?? null,
      tabular_review_id: (b.tabular_review_id as string) ?? null,
      status: "active",
      created_by_user_id: "u-1",
      created_at: iso(0),
      updated_at: iso(0),
    });
  }
  const asMatch = /^\/assistant\/sessions\/([^/]+)$/.exec(p);
  if (asMatch && m === "GET")
    return ok({ session: ASSISTANT_SESSIONS.find((s) => s.id === asMatch[1]) ?? ASSISTANT_SESSIONS[0], contract_handles: [{ id: "h-1", session_id: "as-1", contract_id: "c-1", handle: "contract-0" }] });
  if (/^\/assistant\/sessions\/[^/]+\/messages$/.test(p))
    return ok([
      { id: "msg-1", session_id: "as-1", org_id: "org-1", role: "user", content: "Summarise the key risks in the Globex MSA.", citations: null, metadata_json: {}, created_at: iso(-1), created_by_user_id: "u-1" },
      { id: "msg-2", session_id: "as-1", org_id: "org-1", role: "assistant", content: "The Globex MSA has three high-risk areas:\n\n1. **Uncapped indemnification** for IP claims (§9.2).\n2. **Termination for convenience** favouring the counterparty at 90 days (§14.1).\n3. **Missing cyber-insurance** requirement.\n\nI recommend running the MSA Playbook to generate redlines.", citations: [{ type: "text_snapshot", contract_id: "c-1", excerpt: "Supplier's indemnification obligations under this Section shall not be subject to the limitation of liability in Section 10.", quote: "Supplier's indemnification obligations … shall not be subject to the limitation of liability." }], metadata_json: {}, created_at: iso(-1), created_by_user_id: null },
    ]);
  if (/^\/assistant\/sessions\/[^/]+\/runs$/.test(p)) return ok([]);
  if (p === "/assistant/tools")
    return ok([
      { name: "read_contract", description: "Read the full text of a contract.", category: "READ_ONLY", permission: "contract:read", confirmation_policy: "none", feature_flag: null, enabled_by_default: true, input_schema: {}, output_schema: {} },
      { name: "redline_against_playbook", description: "Generate tracked-change redlines against a playbook.", category: "MUTATING", permission: "contract:redline", confirmation_policy: "required", feature_flag: null, enabled_by_default: true, input_schema: {}, output_schema: {} },
      { name: "send_for_signature", description: "Send the approved version to DocuSign.", category: "EXTERNAL_ACTION", permission: "contract:sign", confirmation_policy: "required", feature_flag: null, enabled_by_default: true, input_schema: {}, output_schema: {} },
    ]);

  // AI
  if (p === "/ai/skills") return ok([]);
  if (p === "/ai/skill-runs") return ok([]);

  // Workflows
  if (p === "/workflows" && m === "GET")
    return ok([
      { id: "wf-1", org_id: "org-1", name: "Inbound NDA triage", workflow_type: "assistant", visibility: "org_wide", description: "Classify an inbound NDA as green / yellow / red and list the issues.", definition: { prompt: "Act as in-house counsel. Triage the attached NDA. Classify it GREEN (standard, sign), YELLOW (counsel review) or RED (escalate). List every non-standard term with the clause reference and a recommended position.", builtin: true }, created_at: iso(-30), created_by_user_id: "u-1" },
      { id: "wf-2", org_id: "org-1", name: "MSA risk extraction", workflow_type: "tabular_review", visibility: "private", description: "Column template extracting liability and indemnity posture across MSAs.", definition: { columns: [ { name: "Governing law", prompt: "What is the governing law and jurisdiction?" }, { name: "Liability cap", prompt: "Summarise the limitation of liability, including any carve-outs." }, { name: "Indemnification", prompt: "Describe the indemnification obligations and any caps." }, { name: "Termination", prompt: "What are the termination rights and notice periods?" } ] }, created_at: iso(-12), created_by_user_id: "u-1" },
      { id: "wf-3", org_id: "org-1", name: "Redline against MSA playbook", workflow_type: "assistant", visibility: "org_wide", description: "Generate tracked-change redlines for the displayed contract against the published MSA playbook.", definition: { prompt: "Run the published Master Services Agreement playbook against this contract. For every deviation, propose a tracked-change redline using the playbook's preferred position, and explain the business rationale.", builtin: true }, created_at: iso(-20), created_by_user_id: "u-1" },
      { id: "wf-4", org_id: "org-1", name: "DPA / GDPR coverage matrix", workflow_type: "tabular_review", visibility: "org_wide", description: "Column template checking GDPR data-processing coverage.", definition: { columns: [ { name: "Sub-processors", prompt: "Are sub-processors permitted and what approval is required?" }, { name: "Breach notice", prompt: "What is the personal-data-breach notification window?" }, { name: "SCCs", prompt: "Are Standard Contractual Clauses incorporated for international transfers?" } ] }, created_at: iso(-15), created_by_user_id: "u-1" },
    ]);
  if (p === "/workflows" && m === "POST")
    return ok({ id: "wf-new", org_id: "org-1", name: "New workflow", workflow_type: "assistant", visibility: "private", description: "", definition: {}, created_at: iso(0), created_by_user_id: "u-1", ...(body as object) });

  // Playbooks
  if (p === "/playbooks" && m === "GET") return ok(PLAYBOOKS);
  if (p === "/playbooks" && (m === "POST"))
    return ok({ id: "pb-new", name: "New playbook", description: "", status: "draft", current_version_id: null, ...(body as object) });
  if (p === "/playbooks/generate")
    return ok({ id: "pb-gen", name: (body as { name?: string })?.name ?? "Generated playbook", description: "AI-generated rulebook", status: "draft", current_version_id: "pv-gen" });
  const pbMatch = /^\/playbooks\/([^/]+)$/.exec(p);
  if (pbMatch && m === "GET")
    return ok(PLAYBOOKS.find((x) => x.id === pbMatch[1]) ?? PLAYBOOKS[0]);
  if (/^\/playbooks\/[^/]+\/versions$/.test(p) && m === "GET")
    return ok([
      { id: "pv-3", playbook_id: "pb-1", version_number: 3, status: "published", summary: "Current published version", source_metadata: null },
      { id: "pv-2", playbook_id: "pb-1", version_number: 2, status: "archived", summary: "Prior version", source_metadata: null },
    ]);
  if (/\/versions\/[^/]+\/rules$/.test(p) && m === "GET")
    return ok([
      { id: "rl-1", playbook_version_id: "pv-3", clause_type: "limitation_of_liability", rule_type: "preferred", preferred_position: "Liability capped at 12 months fees.", fallback_position: "Cap at 2× annual fees.", prohibited_language: "uncapped", required_language: null, risk_level: "high", rationale: "Protects against catastrophic exposure.", escalation_role: "general_counsel", approval_required: true, sample_clause: "In no event shall either party's aggregate liability exceed…", negotiation_guidance: "Hold firm on the 12-month cap for deals < $1M." },
      { id: "rl-2", playbook_version_id: "pv-3", clause_type: "termination", rule_type: "preferred", preferred_position: "30-day termination for convenience.", fallback_position: "60 days.", prohibited_language: null, required_language: "written notice", risk_level: "medium", rationale: "Maintain exit flexibility.", escalation_role: null, approval_required: false, sample_clause: "Either party may terminate for convenience upon thirty (30) days…", negotiation_guidance: "Accept up to 60 days if counterparty pushes." },
    ]);
  if (/^\/playbooks\/[^/]+\/runs$/.test(p) && m === "GET")
    return ok([
      { id: "pr-1", playbook_id: "pb-1", playbook_version_id: "pv-3", contract_id: "c-1", contract_version_id: "v-2", status: "succeeded", validation_status: "valid", model_name: "claude-3-5-sonnet", error_message: null, validated_output: {} },
    ]);
  if (/^\/playbooks\/runs\/[^/]+$/.test(p))
    return ok({
      id: "pr-1", playbook_id: "pb-1", playbook_version_id: "pv-3", contract_id: "c-1", contract_version_id: "v-2", status: "succeeded", validation_status: "valid", model_name: "claude-3-5-sonnet", error_message: null, validated_output: {},
      deviations: [
        { id: "dv-1", playbook_run_id: "pr-1", playbook_rule_id: "rl-1", contract_id: "c-1", severity: "high", clause_type: "limitation_of_liability", issue: "Indemnification carved out of liability cap entirely.", suggested_fix: "Bring IP indemnity within a 2× fees super-cap.", citation: { excerpt: "shall not be subject to the limitation of liability" }, status: "open" },
        { id: "dv-2", playbook_run_id: "pr-1", playbook_rule_id: "rl-2", contract_id: "c-1", severity: "medium", clause_type: "termination", issue: "Termination for convenience is 90 days, exceeds 60-day fallback.", suggested_fix: "Reduce to 30 days.", citation: { excerpt: "upon ninety (90) days notice" }, status: "open" },
      ],
    });

  // Approvals
  if (p === "/approvals" && m === "GET")
    return ok([
      { id: "ap-1", org_id: "org-1", contract_id: "c-3", contract_version_id: "v-7", status: "pending", requested_by_user_id: "u-1", approver_user_id: null, approver_role: "general_counsel", due_at: iso(3), metadata_json: {}, created_at: iso(-1), updated_at: iso(-1) },
      { id: "ap-2", org_id: "org-1", contract_id: "c-1", contract_version_id: "v-2", status: "approved", requested_by_user_id: "u-1", approver_user_id: "u-2", approver_role: "finance", due_at: iso(-1), metadata_json: {}, created_at: iso(-4), updated_at: iso(-2) },
    ]);
  if (p === "/approvals/routing-rules")
    return ok([
      { id: "rr-1", org_id: "org-1", name: "High-value to GC", priority: "10", criteria: { value_gt: 1000000 }, approver_role: "general_counsel", approver_user_id: null, is_active: true, created_at: iso(-60), updated_at: iso(-60) },
      { id: "rr-2", org_id: "org-1", name: "Critical risk escalation", priority: "5", criteria: { risk: "critical" }, approver_role: "general_counsel", approver_user_id: null, is_active: true, created_at: iso(-60), updated_at: iso(-60) },
    ]);

  // Signatures
  if (p === "/signatures")
    return ok([
      { id: "sg-1", org_id: "org-1", contract_id: "c-3", contract_version_id: "v-7", provider: "docusign", provider_envelope_id: "env-9981", status: "sent", sent_by_user_id: "u-1", sent_at: iso(-1), completed_at: null, signed_contract_version_id: null, metadata_json: {}, created_at: iso(-1), updated_at: iso(-1), recipients: [{ name: "Jane Counsel", email: "jane@acme-legal.com", role: "signer" }, { name: "Hooli Legal", email: "legal@hooli.com", role: "signer" }] },
      { id: "sg-2", org_id: "org-1", contract_id: "c-2", contract_version_id: "v-5", provider: "docusign", provider_envelope_id: "env-7742", status: "completed", sent_by_user_id: "u-1", sent_at: iso(-30), completed_at: iso(-28), signed_contract_version_id: "v-5", metadata_json: {}, created_at: iso(-30), updated_at: iso(-28) },
    ]);

  // Obligations
  if (p === "/obligations" || p.startsWith("/obligations?"))
    return ok([
      { id: "ob-1", org_id: "org-1", contract_id: "c-2", contract_version_id: "v-5", owner_user_id: "u-1", responsible_party: "Acme Finance", obligation_type: "payment", description: "Quarterly subscription payment of $31,250.", due_date: date(7), recurrence: "quarterly", status: "due_soon", source_citation: null, metadata_json: {}, created_at: iso(-30), updated_at: iso(-1) },
      { id: "ob-2", org_id: "org-1", contract_id: "c-4", contract_version_id: "v-9", owner_user_id: "u-1", responsible_party: "Security", obligation_type: "compliance", description: "Annual SOC 2 report delivery to Soylent.", due_date: date(-3), recurrence: "annually", status: "overdue", source_citation: null, metadata_json: {}, created_at: iso(-300), updated_at: iso(-3) },
      { id: "ob-3", org_id: "org-1", contract_id: "c-1", contract_version_id: "v-2", owner_user_id: "u-1", responsible_party: "Legal Ops", obligation_type: "notice", description: "Provide renewal notice 60 days before expiry.", due_date: date(280), recurrence: null, status: "open", source_citation: null, metadata_json: {}, created_at: iso(-5), updated_at: iso(-5) },
      { id: "ob-4", org_id: "org-1", contract_id: "c-2", contract_version_id: "v-5", owner_user_id: "u-1", responsible_party: "Acme Finance", obligation_type: "payment", description: "Prior quarter payment.", due_date: date(-60), recurrence: "quarterly", status: "completed", source_citation: null, metadata_json: {}, created_at: iso(-120), updated_at: iso(-58) },
    ]);

  // Renewals
  if (p === "/renewals" || p.startsWith("/renewals?"))
    return ok([
      { id: "rn-1", org_id: "org-1", contract_id: "c-4", contract_version_id: "v-9", expiration_date: date(35), notice_date: date(5), renewal_window_starts_at: date(0), owner_user_id: "u-1", decision: "undecided", decision_note: null, metadata_json: {}, created_at: iso(-30), updated_at: iso(-1) },
      { id: "rn-2", org_id: "org-1", contract_id: "c-2", contract_version_id: "v-5", expiration_date: date(275), notice_date: date(215), renewal_window_starts_at: date(210), owner_user_id: "u-1", decision: "renew", decision_note: "Auto-renew, good standing.", metadata_json: {}, created_at: iso(-90), updated_at: iso(-30) },
    ]);

  // Contract Brain
  if (p === "/contract-brain/queries")
    return ok([
      { id: "bq-1", org_id: "org-1", query_scope: "portfolio", question: "Which active contracts have uncapped liability?", contract_id: null, project_id: null, answer: "Two active contracts carry uncapped liability exposure: the Globex MSA (IP indemnity carve-out) and the Soylent DPA (data-breach liability).", citations: [{ quote: "indemnification obligations shall not be subject to the limitation of liability", validation_status: "valid", similarity_score: 0.91 }], retrieval_metadata: { scope: "portfolio", source_count: 2, graph_facts: 6, vector_chunks: 11, fulltext_clauses: 4, contract_ids: ["c-1", "c-4"], confidence: "high", citation_review: "valid", limitations: null }, created_at: iso(-2), updated_at: iso(-2) },
      { id: "bq-2", org_id: "org-1", query_scope: "contract", question: "What is the governing law of the Globex MSA?", contract_id: "c-1", project_id: null, answer: "The Globex MSA is governed by the laws of the State of Delaware, USA.", citations: [{ quote: "governed by and construed in accordance with the laws of the State of Delaware", validation_status: "valid", similarity_score: 0.97 }], retrieval_metadata: { scope: "contract", source_count: 1, graph_facts: 2, vector_chunks: 3, fulltext_clauses: 1, contract_ids: ["c-1"], confidence: "high", citation_review: "valid", limitations: null }, created_at: iso(-3), updated_at: iso(-3) },
    ]);
  if (p === "/contract-brain/ask")
    return ok({ id: "bq-new", org_id: "org-1", query_scope: (body as { query_scope?: string })?.query_scope ?? "portfolio", question: (body as { question?: string })?.question ?? "", contract_id: (body as { contract_id?: string })?.contract_id ?? null, project_id: null, answer: "Based on the indexed contract portfolio: the Globex MSA and Soylent DPA represent the highest aggregate risk. The Globex MSA carves IP indemnification out of the liability cap, and the Soylent DPA is GDPR-critical with a breach-notification SLA of 24 hours.", citations: [{ quote: "Supplier's indemnification obligations under this Section shall not be subject to the limitation of liability in Section 10.", validation_status: "valid", similarity_score: 0.93 }, { quote: "Processor shall notify Controller without undue delay and in any event within 24 hours.", validation_status: "valid", similarity_score: 0.88 }], retrieval_metadata: { scope: "portfolio", source_count: 2, graph_facts: 7, vector_chunks: 14, fulltext_clauses: 5, contract_ids: ["c-1", "c-4"], confidence: "high", citation_review: "valid", limitations: null }, created_at: iso(0), updated_at: iso(0) });
  if (p.startsWith("/contract-brain/precedents"))
    return ok(CONTRACTS.slice(0, 2));

  // Tabular review
  if (p === "/tabular-reviews" && m === "GET") return ok(TABULAR);
  if (p === "/tabular-reviews" && m === "POST")
    return ok({ ...TABULAR[0], id: "tr-new", ...(body as object) });
  const trMatch = /^\/tabular-reviews\/([^/]+)$/.exec(p);
  if (trMatch && m === "GET") {
    const cols = [
      { id: "col-1", tabular_review_id: "tr-1", name: "Governing law", prompt: "What is the governing law?", position: 0, metadata_json: {} },
      { id: "col-2", tabular_review_id: "tr-1", name: "Liability cap", prompt: "What is the limitation of liability?", position: 1, metadata_json: {} },
      { id: "col-3", tabular_review_id: "tr-1", name: "Termination notice", prompt: "Termination for convenience notice period?", position: 2, metadata_json: {} },
    ];
    const rows = ["c-1", "c-3"];
    const cells = rows.flatMap((cid) =>
      cols.map((c, i) => ({
        id: `cell-${cid}-${c.id}`,
        tabular_review_id: "tr-1",
        column_id: c.id,
        contract_id: cid,
        status: i === 2 && cid === "c-3" ? "running" : "complete",
        answer:
          i === 0
            ? cid === "c-1"
              ? "Delaware, USA"
              : "New York, USA"
            : i === 1
              ? cid === "c-1"
                ? "Uncapped for IP indemnity; 12-month fees otherwise"
                : "2× annual fees"
              : cid === "c-1"
                ? "90 days"
                : null,
        reasoning:
          "Extracted from the governing-law and liability sections of the authoritative version.",
        citations: [{ quote: "governed by the laws of the State of Delaware", validation_status: "valid", similarity_score: 0.95 }],
        confidence: "high",
        error_message: null,
      })),
    );
    return ok({ review: TABULAR.find((t) => t.id === trMatch[1]) ?? TABULAR[0], columns: cols, cells });
  }
  if (/^\/tabular-reviews\/[^/]+\/chat$/.test(p) && m === "GET")
    return ok([
      { id: "tc-1", tabular_review_id: "tr-1", role: "user", content: "Which contract has the worse liability position?", citations: null, created_at: iso(-1) },
      { id: "tc-2", tabular_review_id: "tr-1", role: "assistant", content: "The Globex MSA (c-1) has the weaker position — its IP indemnification is carved out of the liability cap entirely, whereas the Hooli SaaS agreement caps liability at 2× annual fees.", citations: [{ quote: "shall not be subject to the limitation of liability", validation_status: "valid" }], created_at: iso(-1) },
    ]);
  if (/^\/tabular-reviews\/[^/]+\/chat$/.test(p) && m === "POST")
    return ok({ id: "tc-new", tabular_review_id: "tr-1", role: "assistant", content: "Across the reviewed contracts, the Globex MSA carries the highest residual risk driven by its uncapped IP indemnity.", citations: [{ quote: "shall not be subject to the limitation of liability", validation_status: "valid" }], created_at: iso(0) });

  // Search
  if (p.startsWith("/search/contracts")) return ok(CONTRACTS);
  if (p.startsWith("/search/contract-text"))
    return ok([
      { contract_id: "c-1", contract_title: cTitle("c-1"), text_snapshot_id: "t-2", contract_version_id: "v-2", matches: [{ start_char: 1820, end_char: 1980, excerpt: "…the indemnification obligations under this Section shall not be subject to the limitation of liability set forth in Section 10…" }] },
    ]);
  if (p.startsWith("/search/clauses"))
    return ok([
      { clause_id: "cl-1", contract_id: "c-1", contract_title: cTitle("c-1"), contract_version_id: "v-2", text_snapshot_id: "t-2", clause_type: "limitation_of_liability", heading: "Section 10 — Limitation of Liability", confidence: 0.94, excerpt: "In no event shall either party's aggregate liability exceed the fees paid in the twelve (12) months preceding the claim…" },
      { clause_id: "cl-2", contract_id: "c-4", contract_title: cTitle("c-4"), contract_version_id: "v-9", text_snapshot_id: "t-9", clause_type: "data_protection", heading: "Annex II — Security Measures", confidence: 0.9, excerpt: "Processor shall notify Controller without undue delay and within 24 hours of becoming aware of a Personal Data Breach…" },
    ]);
  if (p.startsWith("/search/projects")) return ok(PROJECTS);

  // Notifications
  if (p === "/notifications")
    return ok([
      { id: "n-1", user_id: "u-1", org_id: "org-1", channel: "email", event_type: "approval.requested", subject: "Approval needed: Hooli SaaS Subscription", body: "A contract valued at $1,250,000 requires your approval.", status: "sent", provider_message_id: "rs-1", sent_at: iso(-1), error_message: null, metadata_json: {}, created_at: iso(-1) },
      { id: "n-2", user_id: "u-1", org_id: "org-1", channel: "email", event_type: "obligation.overdue", subject: "Overdue: SOC 2 report to Soylent", body: "An obligation is 3 days overdue.", status: "sent", provider_message_id: "rs-2", sent_at: iso(-3), error_message: null, metadata_json: {}, created_at: iso(-3) },
      { id: "n-3", user_id: "u-1", org_id: "org-1", channel: "email", event_type: "renewal.window", subject: "Renewal window open: Soylent DPA", body: "The renewal window for the Soylent DPA is now open.", status: "queued", provider_message_id: null, sent_at: null, error_message: null, metadata_json: {}, created_at: iso(0) },
    ]);

  // Jobs
  if (p === "/jobs")
    return ok([
      { id: "j-1", org_id: "org-1", job_type: "playbook_review", resource_type: "contract", resource_id: "c-1", idempotency_key: null, status: "succeeded", progress: 100, started_at: iso(-5), finished_at: iso(-5), error_message: null, error_stack: null, attempt_count: 1, celery_task_id: "t1", metadata_json: {}, created_at: iso(-5), updated_at: iso(-5) },
      { id: "j-2", org_id: "org-1", job_type: "contract_brain_ingestion", resource_type: "contract", resource_id: "c-3", idempotency_key: null, status: "running", progress: 62, started_at: iso(0), finished_at: null, error_message: null, error_stack: null, attempt_count: 1, celery_task_id: "t2", metadata_json: {}, created_at: iso(0), updated_at: iso(0) },
      { id: "j-3", org_id: "org-1", job_type: "ocr", resource_type: "contract", resource_id: "c-4", idempotency_key: null, status: "failed", progress: 40, started_at: iso(-1), finished_at: iso(-1), error_message: "Reducto timeout after 3 attempts", error_stack: null, attempt_count: 3, celery_task_id: "t3", metadata_json: {}, created_at: iso(-1), updated_at: iso(-1) },
      { id: "j-4", org_id: "org-1", job_type: "obligation_extraction", resource_type: "contract", resource_id: "c-2", idempotency_key: null, status: "queued", progress: 0, started_at: null, finished_at: null, error_message: null, error_stack: null, attempt_count: 0, celery_task_id: null, metadata_json: {}, created_at: iso(0), updated_at: iso(0) },
    ]);

  // Admin
  if (p === "/admin/settings")
    return ok([
      { id: "set-1", org_id: "org-1", key: "max_upload_size_bytes", value: 52428800, is_secret: false, created_at: iso(-90), updated_at: iso(-90) },
      { id: "set-2", org_id: "org-1", key: "ocr_enabled", value: true, is_secret: false, created_at: iso(-90), updated_at: iso(-90) },
      { id: "set-3", org_id: "org-1", key: "retention_days", value: 2555, is_secret: false, created_at: iso(-90), updated_at: iso(-90) },
    ]);
  if (p === "/admin/settings" && m === "PUT")
    return ok({ id: "set-new", org_id: "org-1", key: (body as { key?: string })?.key ?? "key", value: (body as { value?: unknown })?.value ?? null, is_secret: false, created_at: iso(0), updated_at: iso(0) });

  // Debug
  if (p === "/debug/config-status")
    return ok({ claude: { configured: true, mock: true }, reducto: { configured: true, mock: true }, resend: { configured: true, mock: true }, docusign: { configured: true, mock: true }, storage_root: "/data/contracts", debug: true });
  if (p === "/debug/health")
    return ok({ status: "ok", app: "Clean Room Legal CLM (demo)", environment: "demo" });

  // Generic fallbacks so list pages never crash.
  if (m === "GET") return ok([]);
  return ok({});
}

/** Simulated assistant SSE for demo mode. */
export function demoStream(
  onEvent: (e: string, d: Record<string, unknown>) => void,
  onClose?: () => void,
): () => void {
  let cancelled = false;
  const timers: ReturnType<typeof setTimeout>[] = [];
  const at = (ms: number, fn: () => void) => {
    timers.push(setTimeout(() => !cancelled && fn(), ms));
  };
  const answer =
    "Looking at the Globex MSA, the three highest-risk items are the uncapped IP indemnity, the 90-day termination-for-convenience clause, and the missing cyber-insurance requirement. I can generate playbook redlines to address all three.";
  at(150, () => onEvent("session_started", { session_id: "as-1", assistant_run_id: "run-demo" }));
  at(400, () => onEvent("tool_started", { tool_name: "read_contract", tool_use_id: "tu-1" }));
  at(1100, () => onEvent("tool_finished", { tool_name: "read_contract", tool_use_id: "tu-1", result: {} }));
  const words = answer.split(" ");
  words.forEach((w, i) =>
    at(1300 + i * 45, () => onEvent("message_delta", { text: w + " " })),
  );
  const end = 1300 + words.length * 45 + 200;
  at(end, () =>
    onEvent("citation", { type: "text_snapshot", contract_id: "c-1", excerpt: "Supplier's indemnification obligations shall not be subject to the limitation of liability.", quote: "indemnification obligations shall not be subject to the limitation of liability" }),
  );
  at(end + 250, () => {
    onEvent("done", { assistant_run_id: "run-demo", run_status: "succeeded" });
    onClose?.();
  });
  return () => {
    cancelled = true;
    timers.forEach(clearTimeout);
  };
}
