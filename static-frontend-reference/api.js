// =============================================================
// AEGIS · API client (REST-only, no mock fallback)
//
// Every action calls the FastAPI backend at API_BASE.
// Auth: JWT access token (sessionStorage) + HttpOnly refresh
// cookie. Every request attaches Authorization: Bearer <token>.
// On a 401 we try /auth/refresh once, then re-issue the request.
// =============================================================

(function () {
  // Runtime-configurable so the same bundle works locally (localhost:8000)
  // and in production (same-origin "/api/v1" behind the reverse proxy).
  // config.js sets window.__AEGIS_CONFIG before this script loads.
  const API_BASE = (window.__AEGIS_CONFIG && window.__AEGIS_CONFIG.apiBase) || "/api/v1";
  const TOKEN_KEY = "aegis-access-token";

  // ---------- tiny reactive store ----------
  const listeners = {};
  const store = {
    on(event, fn) {
      (listeners[event] = listeners[event] || []).push(fn);
      return () => {
        listeners[event] = (listeners[event] || []).filter((f) => f !== fn);
      };
    },
    emit(event, payload) {
      (listeners[event] || []).forEach((fn) => fn(payload));
      (listeners["*"] || []).forEach((fn) => fn(event, payload));
    },
  };

  // Parse one Server-Sent Event block (event: NAME\ndata: JSON\n)
  function parseSSE(block) {
    if (!block || !block.trim()) return null;
    const lines = block.split(/\r?\n/);
    let name = "message";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    let parsed = null;
    try { parsed = data ? JSON.parse(data) : null; } catch { parsed = data; }
    return { name, data: parsed };
  }

  function getToken() {
    try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
  }
  function setToken(t) {
    try {
      if (t) sessionStorage.setItem(TOKEN_KEY, t);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch {}
  }

  let refreshInFlight = null;
  async function refreshAccessToken() {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = (async () => {
      const res = await fetch(API_BASE + "/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: "{}",
      });
      if (!res.ok) {
        setToken(null);
        throw new Error("session expired");
      }
      const body = await res.json();
      setToken(body.access_token);
      return body.access_token;
    })();
    try {
      return await refreshInFlight;
    } finally {
      refreshInFlight = null;
    }
  }

  async function http(path, opts = {}, retry = true) {
    const token = getToken();
    const headers = Object.assign(
      { "Content-Type": "application/json" },
      token ? { Authorization: `Bearer ${token}` } : {},
      opts.headers || {}
    );
    let res;
    try {
      res = await fetch(API_BASE + path, {
        ...opts,
        headers,
        credentials: "include",
      });
    } catch (netErr) {
      // A fetch rejection (TypeError "Failed to fetch") means the browser got
      // no usable response — server unreachable, or a 5xx returned without CORS
      // headers (e.g. an unhandled 500 outside the CORS middleware). Surface a
      // typed error so callers can show an honest message instead of "Failed to
      // fetch".
      const e = new Error(
        "No response from server — the request failed at the network layer " +
          "(server unreachable, or a 500 returned without CORS headers)."
      );
      e.status = 0;
      e.cause = netErr;
      throw e;
    }
    if (res.status === 401 && retry && token) {
      try {
        await refreshAccessToken();
        return http(path, opts, false);
      } catch (e) {
        store.emit("auth/expired");
        throw e;
      }
    }
    if (res.status === 204) return null;
    if (!res.ok) {
      let body = null;
      try { body = await res.json(); } catch {}
      // FastAPI 422 returns detail as a list of {loc,msg,type}; flatten it readably.
      let msg = `HTTP ${res.status}`;
      if (body) {
        if (typeof body.detail === "string") msg = body.detail;
        else if (Array.isArray(body.detail)) msg = body.detail.map((d) => (d.loc ? d.loc[d.loc.length - 1] + ": " : "") + (d.msg || "")).join("; ");
        else if (body.message) msg = body.message;
      }
      const err = new Error(msg);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    if (res.headers.get("content-length") === "0") return null;
    return res.json();
  }

  // ---------- response shape adapters ----------
  function adaptContract(c) {
    if (!c) return c;
    return {
      id: c.id,
      title: c.title,
      counterparty_name: c.counterparty_name || null,
      contract_type: c.contract_type || null,
      lifecycle_stage: c.lifecycle_stage,
      risk_level: c.risk_level || "ok",
      value_amount: c.value_amount,
      currency: c.currency,
      effective_date: c.effective_date,
      expiration_date: c.expiration_date,
      owner_user_id: c.owner_user_id,
      project_id: c.project_id || null,
      project_name: c.project_name || null,
      governing_law: c.jurisdiction || c.governing_law || null,
      meta: c.metadata_json || c.meta || {},
    };
  }

  function adaptUser(u) {
    if (!u) return u;
    const name = u.full_name || u.email || "User";
    return {
      id: u.id,
      full_name: name,
      email: u.email,
      role: u.active_role_name || (u.roles && u.roles[0]) || "Member",
      avatar: name.split(/\s+/).map((s) => s[0]).filter(Boolean).slice(0, 2).join("").toUpperCase(),
      org_id: u.org_id,
      status: u.status,
      active_role: u.active_role_name || null,
    };
  }

  // ---------- API surface — all REST, no mock ----------
  const api = {
    auth: {
      isLoggedIn() { return !!getToken(); },
      async login(email, password) {
        const r = await http("/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        }, false);
        if (r && r.access_token) setToken(r.access_token);
        store.emit("auth/login", r);
        return r;
      },
      async logout() {
        try {
          await http("/auth/logout", { method: "POST", body: "{}" }, false);
        } catch {}
        setToken(null);
        store.emit("auth/logout");
      },
    },

    me: {
      async get() {
        const u = await http("/auth/me");
        return adaptUser(u);
      },
    },

    org: {
      async get() {
        // No org/current endpoint — fall back to /auth/me org info
        const me = await http("/auth/me");
        return { id: me.org_id, name: "—" };
      },
    },

    metrics: {
      async hub() {
        return http("/contract-hub");
      },
    },

    contracts: {
      async list() {
        const rows = await http("/contracts");
        return rows.map(adaptContract);
      },
      async get(id) { return adaptContract(await http(`/contracts/${id}`)); },
      async update(id, patch) {
        return adaptContract(await http(`/contracts/${id}`, { method: "PATCH", body: JSON.stringify(patch) }));
      },
      async transition(id, to_stage, reason, opts = {}) {
        return adaptContract(await http(`/contracts/${id}/lifecycle`, {
          method: "POST",
          body: JSON.stringify({
            to_stage,
            reason,
            signed_confirmation: opts.signedConfirmation || to_stage === "active",
            override: opts.override || false,
          }),
        }));
      },
      async stageHistory(id) { return http(`/contracts/${id}/stage-history`); },
      async activity(id) { return http(`/contracts/${id}/activity`); },
      async versions(id) { return http(`/contracts/${id}/versions`); },
      async versionText(id, versionId) { return http(`/contracts/${id}/versions/${versionId}/text`); },
      async lifecycleOptions(id) { return http(`/contracts/${id}/lifecycle`); },
      async upload(file, fields = {}) {
        // multipart POST /contracts/upload
        const fd = new FormData();
        fd.append("file", file);
        if (fields.title) fd.append("title", fields.title);
        if (fields.counterparty_name) fd.append("counterparty_name", fields.counterparty_name);
        if (fields.project_id) fd.append("project_id", fields.project_id);
        const token = getToken();
        const res = await fetch(API_BASE + "/contracts/upload", {
          method: "POST",
          credentials: "include",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: fd, // browser sets multipart boundary
        });
        if (!res.ok) {
          let body = null; try { body = await res.json(); } catch {}
          const err = new Error((body && (body.detail || body.message)) || `HTTP ${res.status}`);
          err.status = res.status; err.body = body; throw err;
        }
        return res.json();
      },
      // AI redline edits (require contract:redline perm; 403 if not permitted)
      async edits(id) { return http(`/contracts/${id}/edits`); },
      async acceptEdit(id, editId) {
        return http(`/contracts/${id}/edits/${editId}/accept`, { method: "POST", body: "{}" });
      },
      async rejectEdit(id, editId) {
        return http(`/contracts/${id}/edits/${editId}/reject`, { method: "POST", body: "{}" });
      },
    },

    // AI extraction jobs (async — return {job_id, status})
    ai: {
      async metadataExtraction(contractId) {
        return http(`/ai/contracts/${contractId}/metadata-extraction`, { method: "POST", body: "{}" });
      },
      async clauseExtraction(contractId) {
        return http(`/ai/contracts/${contractId}/clause-extraction`, { method: "POST", body: "{}" });
      },
    },

    projects: {
      async list() { return http("/projects"); },
      async get(id) { return http(`/projects/${id}`); },
      async create(data) {
        return http("/projects", { method: "POST", body: JSON.stringify(data) });
      },
      async update(id, patch) {
        return http(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
      },
      async folders(id) { return http(`/projects/${id}/folders`); },
      async createFolder(id, data) {
        return http(`/projects/${id}/folders`, { method: "POST", body: JSON.stringify(data) });
      },
      async members(id) { return http(`/projects/${id}/members`); },
      async addMember(id, body) {
        // PUT /projects/{id}/members — body: { user_id, role? }
        return http(`/projects/${id}/members`, { method: "PUT", body: JSON.stringify(body) });
      },
      async contracts(id) { return http(`/projects/${id}/contracts`); },
      async addContract(id, contractId, folderId) {
        // PUT /projects/{id}/contracts — ProjectContractAdd { contract_id, folder_id? }
        return http(`/projects/${id}/contracts`, { method: "PUT", body: JSON.stringify({ contract_id: contractId, folder_id: folderId || null }) });
      },
      async moveContract(id, contractId, folderId) {
        // PATCH /projects/{id}/contracts/{contract_id} — file/move into a folder
        return http(`/projects/${id}/contracts/${contractId}`, { method: "PATCH", body: JSON.stringify({ folder_id: folderId || null }) });
      },
    },

    playbooks: {
      async list() { return http("/playbooks"); },
      async get(id) { return http(`/playbooks/${id}`); },
      // Run a playbook review against a contract (POST /playbooks/{id}/runs) + poll a run.
      async run(playbookId, contractId, opts = {}) {
        return http(`/playbooks/${playbookId}/runs`, { method: "POST", body: JSON.stringify({ contract_id: contractId, create_redline: opts.create_redline !== false, use_ai: opts.use_ai !== false }) });
      },
      async getRun(runId) { return http(`/playbooks/runs/${runId}`); },
      async create(data) {
        return http("/playbooks", { method: "POST", body: JSON.stringify(data) });
      },
      async generate(data) {
        return http("/playbooks/generate", { method: "POST", body: JSON.stringify(data) });
      },
      async update(id, patch) {
        return http(`/playbooks/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
      },
      // Versions
      async versions(id) { return http(`/playbooks/${id}/versions`); },
      async newVersion(id, data) {
        return http(`/playbooks/${id}/versions`, { method: "POST", body: JSON.stringify(data || { summary: "New draft" }) });
      },
      async publish(id, versionId) {
        return http(`/playbooks/${id}/publish`, { method: "POST", body: JSON.stringify({ version_id: versionId }) });
      },
      // Rules live under a version
      async rules(id, versionId) {
        return http(`/playbooks/${id}/versions/${versionId}/rules`);
      },
      async addRule(id, versionId, rule) {
        return http(`/playbooks/${id}/versions/${versionId}/rules`, { method: "POST", body: JSON.stringify(rule) });
      },
      async updateRule(id, versionId, ruleId, patch) {
        return http(`/playbooks/${id}/versions/${versionId}/rules/${ruleId}`, { method: "PATCH", body: JSON.stringify(patch) });
      },
      async deleteRule(id, versionId, ruleId) {
        await http(`/playbooks/${id}/versions/${versionId}/rules/${ruleId}`, { method: "DELETE" });
        return { ok: true };
      },
      // Runs
      async runs(id) { return http(`/playbooks/${id}/runs`); },
      async run(id, body) {
        return http(`/playbooks/${id}/runs`, { method: "POST", body: JSON.stringify(body) });
      },
      async runDetail(runId) { return http(`/playbooks/runs/${runId}`); },
    },

    approvals: {
      async list() { return http("/approvals"); },
      async submitForApproval(contractId, opts = {}) {
        return http("/approvals/requests", {
          method: "POST",
          body: JSON.stringify({
            contract_id: contractId,
            approver_user_id: opts.approver_user_id || null,
            approver_role: opts.approver_role || null,
          }),
        });
      },
      async decide(id, decision, comment) {
        // Backend ApprovalDecisionPayload.decision is constrained to ^(approve|reject)$ —
        // there is NO "request changes". Only approve|reject are honest.
        const map = { approved: "approve", rejected: "reject", approve: "approve", reject: "reject" };
        const wireDecision = map[decision] || decision;
        return http(`/approvals/requests/${id}/decision`, {
          method: "POST",
          body: JSON.stringify({ decision: wireDecision, comment }),
        });
      },
      async routingRules() { return http("/approvals/routing-rules"); },
      async addRoutingRule(rule) {
        return http("/approvals/routing-rules", { method: "POST", body: JSON.stringify(rule) });
      },
      // Token-authenticated decision from the email link (no login required —
      // the single-use, expiring token is the credential).
      async tokenDecision(token, decision, comment) {
        return http("/approvals/token-decision", {
          method: "POST",
          body: JSON.stringify({ token, decision, comment: comment || null }),
        });
      },
    },

    obligations: {
      async list(contractId) {
        const qs = contractId ? `?contract_id=${encodeURIComponent(contractId)}` : "";
        return http("/obligations" + qs);
      },
      async extract(contractId) {
        // contract_id is a QUERY param; returns {job_id, status} (202)
        return http(`/obligations/extract?contract_id=${encodeURIComponent(contractId)}`, {
          method: "POST",
          body: "{}",
        });
      },
      async complete(id) {
        return http(`/obligations/${id}/complete`, { method: "POST", body: "{}" });
      },
      async update(id, patch) {
        return http(`/obligations/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
      },
    },

    renewals: {
      async list() { return http("/renewals"); },
      async decide(id, decision, note) {
        // RenewalDecisionPayload.decision ∈ renew|terminate|renegotiate
        return http(`/renewals/${id}/decision`, {
          method: "POST",
          body: JSON.stringify({ decision, note: note || null }),
        });
      },
    },

    workflows: {
      async list() { return http("/workflows"); },
      // NOTE: there is NO /workflows/runs route on the backend — removed.
      async create(data) {
        return http("/workflows", { method: "POST", body: JSON.stringify(data) });
      },
    },

    assistant: {
      async listThreads() {
        // Maps to /assistant/sessions in the real backend
        const sessions = await http("/assistant/sessions");
        return sessions.map((s) => ({
          id: s.id,
          title: s.title || "Untitled",
          time: (s.updated_at || s.created_at || "").slice(0, 16).replace("T", " "),
          tag: s.session_type || "Session",
          preview: "",
          meta: "",
        }));
      },
      async getOrCreateSession() {
        const sessions = await http("/assistant/sessions");
        if (sessions && sessions.length) return sessions[0];
        return http("/assistant/sessions", {
          method: "POST",
          body: JSON.stringify({ session_type: "chat", title: "Quick ask" }),
        });
      },
      async sendMessage(text, scope) {
        // Real flow: ensure session → POST /sessions/{id}/stream (SSE).
        // Honor scope so the assistant can be grounded in a contract/project
        // (was hardcoded to [] / null, which blinded it to context).
        scope = scope || {};
        const contractIds = scope.contractIds || (scope.contractId ? [scope.contractId] : []);
        const session = await this.getOrCreateSession();
        const url = API_BASE + `/assistant/sessions/${session.id}/stream`;
        const token = getToken();
        const res = await fetch(url, {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            message: text,
            contract_ids: contractIds,
            project_id: scope.projectId || null,
            client_event_id: `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          }),
        });
        if (!res.ok) {
          const err = new Error(`assistant stream HTTP ${res.status}`);
          err.status = res.status;
          throw err;
        }
        // Consume the SSE stream and accumulate the assistant content
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let content = "";
        const citations = [];
        const tools = [];
        let confirmation = null;   // confirmation_required → {confirmation_id, tool, args, run_id, ...}
        let generated = null;      // contract_generated → {contract_id, title, ...}
        let runId = null;
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const block = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const event = parseSSE(block);
            if (!event) continue;
            const d = event.data || {};
            if (event.name === "message_delta" && d.text) {
              content += d.text;
            } else if (event.name === "message_complete" && d.text) {
              content = d.text;
            } else if (event.name === "citation" || event.name === "citations") {
              if (Array.isArray(event.data)) citations.push(...event.data);
              else if (event.data) citations.push(event.data);
            } else if (event.name === "session_started" || event.name === "run_started") {
              runId = d.assistant_run_id || d.run_id || runId;
            } else if (event.name === "tool_started" || event.name === "tool_finished") {
              tools.push({ phase: event.name, name: d.tool || d.name, status: d.status });
            } else if (event.name === "confirmation_required") {
              // A guarded action is awaiting the user — surface it instead of dropping it.
              confirmation = { ...d, run_id: d.run_id || runId };
            } else if (event.name === "contract_generated") {
              generated = d;
            } else if (event.name === "error") {
              throw new Error(d.message || "Assistant error");
            }
          }
        }
        return { content, citations, tools, confirmation, generated, runId };
      },
      // Confirm/reject a guarded action, then resume the run (returns same shape as sendMessage).
      async confirmAction(confirmationId, runId, approve = true) {
        await http(`/assistant/confirmations/${confirmationId}/${approve ? "confirm" : "reject"}`, { method: "POST", body: "{}" });
        if (!approve || !runId) return { content: "", citations: [] };
        return http(`/assistant/runs/${runId}/resume`, { method: "POST", body: "{}" });
      },
    },

    notifications: {
      async list() { return http("/notifications"); },
      async markRead(id) { return http(`/notifications/${id}/read`, { method: "POST", body: "{}" }); },
      async markAllRead() { return http("/notifications/read-all", { method: "POST", body: "{}" }); },
    },

    jobs: {
      async list() { return http("/jobs"); },
      // Real per-job controls. cancel: 409 if terminal; run: 409 unless queued|failed.
      async cancel(id) { return http(`/jobs/${id}/cancel`, { method: "POST", body: "{}" }); },
      async run(id) { return http(`/jobs/${id}/run`, { method: "POST", body: "{}" }); },
    },

    search: {
      // Backend has split paths per resource; fan out and merge results.
      async query(q) {
        if (!q) return [];
        const qs = `?q=${encodeURIComponent(q)}`;
        const fetches = [
          http("/search/contracts" + qs).then((r) => (r || []).map((x) => ({ ...x, kind: "contract" }))).catch(() => []),
          http("/search/clauses" + qs).then((r) => (r || []).map((x) => ({ ...x, kind: "clause" }))).catch(() => []),
          http("/search/projects" + qs).then((r) => (r || []).map((x) => ({ ...x, kind: "project" }))).catch(() => []),
        ];
        const all = (await Promise.all(fetches)).flat();
        return all;
      },
    },

    admin: {
      members: {
        async list() { return http("/users"); },
        async invite({ email, role }) {
          return http("/users/invitations", {
            method: "POST",
            body: JSON.stringify({ email, role_name: role || "member" }),
          });
        },
      },
    },

    brain: {
      // opts: a scope string ("portfolio"|"project"|"contract") OR
      // { scope, projectId, contractId }. Backend field is query_scope.
      async ask(question, opts) {
        if (typeof opts === "string") opts = { scope: opts };
        opts = opts || {};
        const scope = opts.scope === "global" || !opts.scope ? "portfolio" : opts.scope;
        const body = { question, query_scope: scope };
        if (opts.projectId) body.project_id = opts.projectId;
        if (opts.contractId) body.contract_id = opts.contractId;
        return http("/contract-brain/ask", { method: "POST", body: JSON.stringify(body) });
      },
      async queries() { return http("/contract-brain/queries"); },
      async precedents(query, contractId, limit) {
        // `query` is required by the backend; contract_id/limit optional.
        const params = new URLSearchParams();
        params.set("query", query || "similar precedent clauses");
        if (contractId) params.set("contract_id", contractId);
        if (limit) params.set("limit", String(limit));
        return http("/contract-brain/precedents?" + params.toString());
      },
    },

    admin: {
      members: {
        async list() { return http("/users"); },
        async invite({ email, role }) {
          return http("/users/invitations", {
            method: "POST",
            body: JSON.stringify({ email, role_name: role || "member" }),
          });
        },
      },
      settings: {
        async get() { return http("/admin/settings"); },
        // PUT upserts ONE key per call: AdminSettingUpsert{key, value, is_secret}
        async set(key, value, is_secret = false) {
          return http("/admin/settings", {
            method: "PUT",
            body: JSON.stringify({ key, value, is_secret }),
          });
        },
      },
    },

    signatures: {
      async list() { return http("/signatures"); },
      async send(contractId, opts = {}) {
        // Only backend-valid recipient fields go on the wire (name, email, role?).
        // routing_order is server-assigned and rejected — drop it.
        const recipients = (opts.recipients || []).map((r) => ({
          name: r.name,
          email: r.email,
          ...(r.role ? { role: r.role } : {}),
        }));
        return http("/signatures/requests", {
          method: "POST",
          body: JSON.stringify({
            contract_id: contractId,
            recipients,
            override_lifecycle: !!opts.override_lifecycle,
          }),
        });
      },
    },

    tabularReviews: {
      async list() { return http("/tabular-reviews"); },
      // GET returns nested { review, columns, cells }
      async get(id) { return http(`/tabular-reviews/${id}`); },
      async create(data) {
        // TabularReviewCreate { name, project_id?, contract_ids[], columns[] }
        return http("/tabular-reviews", { method: "POST", body: JSON.stringify(data) });
      },
      async addColumn(id, col) {
        // Endpoint expects { columns: [TabularColumnCreate] } (a list)
        return http(`/tabular-reviews/${id}/columns`, { method: "POST", body: JSON.stringify({ columns: [col] }) });
      },
      async addContracts(id, contractIds) {
        return http(`/tabular-reviews/${id}/contracts`, {
          method: "POST",
          body: JSON.stringify({ contract_ids: contractIds }),
        });
      },
      async rerunCell(id, cellId) {
        return http(`/tabular-reviews/${id}/cells/${cellId}/rerun`, { method: "POST", body: "{}" });
      },
      async exportXlsx(id) {
        // XLSX binary — must use an authenticated fetch (a bare <a href> drops the Bearer token)
        const token = getToken();
        const res = await fetch(API_BASE + `/tabular-reviews/${id}/export`, {
          method: "GET",
          credentials: "include",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
          const err = new Error(`HTTP ${res.status}`);
          err.status = res.status;
          throw err;
        }
        return res.blob();
      },
    },
  };

  // Strip the now-duplicate `admin` further down in the original layout (the
  // one above with members.invite). We only need this fresh block.
  delete api.admin.members.deactivate;

  // ---------- expose globally ----------
  window.aegis = {
    api,
    store,
    API_BASE,
    setToken,
    getToken,
  };
})();
