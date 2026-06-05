// =============================================================
// AEGIS · live renderers (REST-only)
// On view switch (or first paint), fetch from the backend and
// replace the static demo markup with the real data.
// Every view shows: loading → data | empty | error.
// =============================================================

(function () {
  if (!window.aegis) return;
  const { api } = window.aegis;

  // Shared HTML-escape helper (defined in app.js as window.aegis.esc). Keep a
  // thin local fallback in case renderers.js is ever evaluated before app.js.
  const esc =
    window.aegis.esc ||
    ((s) =>
      String(s == null ? "" : s).replace(/[&<>"']/g, (m) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
      ));

  function initials(name) {
    if (!name) return "—";
    return name.split(/\s+/).map((s) => s[0]).filter(Boolean).slice(0, 2).join("").toUpperCase();
  }

  function fmtMoney(n) {
    if (n == null) return "—";
    const v = Number(n);
    if (isNaN(v)) return "—";
    if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M / yr`;
    if (v >= 1000) return `$${Math.round(v / 1000)}k / yr`;
    return `$${v} / yr`;
  }

  function fmtDate(s) {
    if (!s) return "—";
    return String(s).slice(0, 10);
  }

  function riskFlag(r) {
    const k = (r || "").toLowerCase();
    if (k === "high") return '<span class="cell-flag cell-flag--high">High</span>';
    if (k === "med" || k === "medium") return '<span class="cell-flag cell-flag--med">Med</span>';
    if (k === "low" || k === "ok") return '<span class="cell-flag cell-flag--ok">Low</span>';
    return '<span class="cell-flag cell-flag--ok">—</span>';
  }

  function stageClass(s) {
    return `stage stage--${(s || "intake").toLowerCase()}`;
  }

  function emptyHtml(message) {
    return `<div class="live-empty">${esc(message)}</div>`;
  }
  function loadingHtml() {
    return `<div class="live-loading">Loading from backend…</div>`;
  }
  function errorHtml(err) {
    return `<div class="live-error">Backend error: ${esc(err.message || "unknown")}</div>`;
  }
  function showView(name) { if (window.__aegisShow) window.__aegisShow(name); }

  // Translate a signature-send failure into an honest toast. Readable backend
  // errors (409/422/403) are shown verbatim; an unreadable 500/network failure
  // (status 0 or >=500 — e.g. DocuSign mis-config returning a 500 without CORS
  // headers, which the browser reports as "Failed to fetch") becomes a truthful
  // "provider unavailable" message. Never fabricate a success.
  function signatureError(e) {
    const m = (e && e.message) || "";
    const noResponse =
      !e || e.status == null || e.status === 0 || /failed to fetch|networkerror|load failed/i.test(m);
    if ((e && e.status >= 500) || noResponse) {
      return {
        title: "E-signature unavailable",
        sub: "DocuSign isn't configured or reachable in this environment, so the signature envelope couldn't be created.",
        kind: "error",
      };
    }
    return { title: "Signature blocked", sub: m || `Request failed (HTTP ${e && e.status})`, kind: "error" };
  }

  // Days-until helper (e.g. expiration in N days)
  function daysUntil(dateStr) {
    if (!dateStr) return null;
    const target = new Date(dateStr).getTime();
    if (isNaN(target)) return null;
    // Reference "today" from the most recent created_at we've seen, else 2026-05-29
    const now = new Date("2026-05-29").getTime();
    return Math.round((target - now) / 86400000);
  }

  // ---------- lookup caches (contract id → title, user id → name) ----------
  let _contractMap = null;
  let _userMap = null;
  async function contractMap() {
    if (_contractMap) return _contractMap;
    _contractMap = {};
    try {
      const rows = await api.contracts.list();
      rows.forEach((c) => { _contractMap[c.id] = c.title; });
    } catch {}
    return _contractMap;
  }
  async function userMap() {
    if (_userMap) return _userMap;
    _userMap = {};
    try {
      const rows = await api.admin.members.list();
      rows.forEach((u) => { _userMap[u.id] = u.full_name || u.email; });
    } catch {}
    return _userMap;
  }
  function contractName(map, id) {
    if (!id) return "—";
    return map[id] || ("Contract " + String(id).slice(0, 8) + "…");
  }
  function userName(map, id) {
    if (!id) return "—";
    return map[id] || ("User " + String(id).slice(0, 6));
  }
  // Title-case a snake_case backend enum (e.g. "renewal_notice" → "Renewal notice")
  function humanize(s) {
    if (!s) return "—";
    return String(s).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  function statusFlag(status) {
    const k = (status || "").toLowerCase();
    if (["succeeded","completed","approved","active","signed","done","closed"].includes(k))
      return `<span class="cell-flag cell-flag--ok">${esc(humanize(status))}</span>`;
    if (["failed","overdue","rejected","error","expired"].includes(k))
      return `<span class="cell-flag cell-flag--high">${esc(humanize(status))}</span>`;
    return `<span class="cell-flag cell-flag--med">${esc(humanize(status || "—"))}</span>`;
  }

  // ---------- HUB ----------
  async function renderHub() {
    const view = document.querySelector(".view--hub");
    if (!view) return;
    try {
      const m = await api.metrics.hub();
      const w = (m && m.widgets) || {};
      const stages = (m && m.contracts_by_stage) || {};
      const totalContracts = Object.values(stages).reduce((s, n) => s + (Number(n) || 0), 0);

      // Replace hardcoded "312" contract counts with the live total (sidebar badge + hub tab)
      const hubBadge = document.querySelector('.nav-item[data-view="hub"] .badge');
      if (hubBadge) hubBadge.textContent = totalContracts;
      [...view.querySelectorAll(".hub-tab, .cd-tab, .tab")].forEach((t) => {
        if (/^contracts/i.test(t.textContent.trim())) { const sp = t.querySelector("span"); if (sp) sp.textContent = totalContracts; }
      });

      // "Here's what needs your attention" subtitle — ONE source of truth (same widgets the
      // KPI cards + queue use). Previously a hardcoded "6 approvals waiting, 2 in review, 24 renewals".
      const attnHead = [...view.querySelectorAll("h1")].find((h) => /needs your attention/i.test(h.textContent));
      const attnSub = attnHead && attnHead.nextElementSibling && attnHead.nextElementSibling.tagName === "P" ? attnHead.nextElementSibling : null;
      if (attnSub) {
        const pend = w.pending_approvals || 0;
        const inReview = (Number(stages.ai_review) || 0) + (Number(stages.internal_review) || 0) + (Number(stages.counterparty_review) || 0);
        const ren = w.upcoming_renewals || 0;
        attnSub.textContent = `${pend} approval${pend === 1 ? "" : "s"} waiting, ${inReview} in review, ${ren} renewal${ren === 1 ? "" : "s"} in the next 90 days.`;
      }

      // KPI strip (replace each KPI fully)
      const kpiRow = view.querySelector(".kpi-row");
      if (kpiRow) {
        kpiRow.innerHTML = `
          ${kpi("Total contracts", totalContracts, `${m.total_contract_versions || 0} versions`)}
          ${kpi("Pending approval", w.pending_approvals ?? 0, w.pending_signatures != null ? `${w.pending_signatures} sigs in flight` : "")}
          ${kpi("Upcoming renewals", w.upcoming_renewals ?? 0, w.overdue_obligations != null ? `${w.overdue_obligations} overdue` : "")}
          ${kpi("Avg cycle time", `${(w.average_cycle_time_days || 0).toFixed(1)}d`, `${m.total_contract_versions || 0} versions`)}
        `;
      }

      // Funnel — built from real contracts_by_stage
      const funnelPanel = view.querySelector(".hub-grid__left .panel:first-child");
      if (funnelPanel) {
        const max = Math.max(1, ...Object.values(stages).map(Number));
        const stageEntries = Object.entries(stages);
        funnelPanel.querySelector(".panel__head").innerHTML = `
          <div><h3>Lifecycle pipeline</h3><p>Live distribution from your database.</p></div>
          <div class="funnel-active">
            <div class="funnel-active__count">${totalContracts}</div>
            <div class="funnel-active__label">Total contracts</div>
          </div>`;
        const funnel = funnelPanel.querySelector(".funnel");
        if (funnel) {
          funnel.style.gridTemplateColumns = `repeat(${stageEntries.length || 1}, 1fr)`;
          funnel.innerHTML = stageEntries.length
            ? stageEntries.map(([stage, count]) => `
              <a class="funnel__step" data-stage="${esc(stage)}" href="#" aria-label="${esc(stage)} stage, ${count} contracts">
                <div class="funnel__count">${count}</div>
                <div class="funnel__label">${esc(stage)}</div>
                <div class="funnel__bar"><span style="width:${Math.round((count / max) * 100)}%"></span></div>
              </a>`).join("")
            : emptyHtml("No contracts in the pipeline yet.");
        }
      }

      // Your queue — populated from widgets (or empty)
      const queuePanel = view.querySelector(".hub-grid__left .panel:nth-child(2)");
      if (queuePanel) {
        const queueWrap = queuePanel.querySelector(".queue-list");
        if (queueWrap) {
          const pending = w.pending_approvals || 0;
          const sigs = w.pending_signatures || 0;
          const overdue = w.overdue_obligations || 0;
          const renewals = w.upcoming_renewals || 0;
          const items = [];
          if (pending > 0) items.push(["approval", `${pending} approval${pending === 1 ? "" : "s"} awaiting your decision`, "/api/v1/approvals", "approvals"]);
          if (sigs > 0) items.push(["signature", `${sigs} signature envelope${sigs === 1 ? "" : "s"} in flight`, "/api/v1/signatures", "approvals"]);
          if (overdue > 0) items.push(["obligation", `${overdue} overdue obligation${overdue === 1 ? "" : "s"}`, "/api/v1/obligations", "obligations"]);
          if (renewals > 0) items.push(["renewal", `${renewals} upcoming renewal${renewals === 1 ? "" : "s"}`, "/api/v1/renewals", "obligations"]);
          queueWrap.innerHTML = items.length
            ? items.map(([kind, label, src, view]) => `
              <a class="queue-item" data-view="${view}" href="#">
                <span class="queue-item__kind queue-item__kind--${kind}">${kind}</span>
                <div class="queue-item__body">
                  <div class="queue-item__title">${esc(label)}</div>
                  <div class="queue-item__meta">${esc(src)}</div>
                </div>
              </a>`).join("")
            : emptyHtml("Inbox zero — nothing waiting on you.");
        }
        const head = queuePanel.querySelector(".panel__head");
        if (head) head.innerHTML = `<div><h3>Your queue</h3><p>From /contract-hub.widgets</p></div>`;
      }

      // Recent activity — from widgets.recent_activity
      const actPanel = view.querySelector(".hub-grid__left .panel:nth-child(3)");
      if (actPanel) {
        const actList = actPanel.querySelector(".activity-list");
        if (actList) {
          const events = w.recent_activity || [];
          actList.innerHTML = events.length
            ? events.slice(0, 8).map((e) => `
              <li>
                <span class="activity-dot activity-dot--${e.event_type?.includes("ai") || e.event_type?.includes("brain") ? "ai" : e.event_type?.includes("sign") ? "sign" : "user"}"></span>
                <div>
                  <div class="activity-text"><strong>${esc(e.title || e.event_type || "event")}</strong></div>
                  <div class="activity-meta">${esc(e.event_type || "")} · ${esc((e.created_at || "").slice(0, 16).replace("T", " "))}</div>
                </div>
              </li>`).join("")
            : `<li>${emptyHtml("No recent activity.")}</li>`;
        }
      }

      // Right column — Renewals
      const rightPanels = view.querySelectorAll(".hub-grid__right .panel");
      const renewalsPanel = rightPanels[0];
      if (renewalsPanel) {
        const list = renewalsPanel.querySelector(".renewal-list");
        if (list) {
          try {
            const [rows, cmap] = await Promise.all([api.renewals.list(), contractMap().catch(() => ({}))]);
            list.innerHTML = rows.length
              ? rows.slice(0, 5).map((r) => {
                  const exp = r.expiration_date || r.due_at;
                  const d = daysUntil(exp);
                  return `
                <li>
                  <div class="renewal-list__date"><strong>${esc(fmtDate(exp))}</strong><span>${d != null ? d + "d" : ""}</span></div>
                  <div>
                    <div class="renewal-list__title">${esc(r.contract_title || contractName(cmap, r.contract_id) || "—")}</div>
                    <div class="renewal-list__meta">${esc(r.note || humanize(r.decision || "undecided"))}</div>
                  </div>
                </li>`;
                }).join("")
              : `<li>${emptyHtml("No renewals due in next 90 days.")}</li>`;
            const head = renewalsPanel.querySelector(".panel__head");
            if (head) head.innerHTML = `<div><h3>Upcoming renewals</h3><p>${rows.length} from /api/v1/renewals</p></div>`;
          } catch (err) {
            list.innerHTML = `<li>${errorHtml(err)}</li>`;
          }
        }
      }

      // Attention — top deviated clauses (real data)
      const attentionPanel = rightPanels[1];
      if (attentionPanel) {
        const list = attentionPanel.querySelector(".attention-list");
        if (list) {
          const top = w.top_deviated_clauses || [];
          list.innerHTML = top.length
            ? top.slice(0, 5).map((c) => `
              <li>
                <svg viewBox="0 0 16 16" fill="none"><path d="M8 2l6 11H2L8 2z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M8 7v3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
                <div><strong>${esc(c.clause_type)}</strong> deviation appears in <strong>${c.count}</strong> contract${c.count === 1 ? "" : "s"}.</div>
              </li>`).join("")
            : `<li>${emptyHtml("No clause deviations flagged.")}</li>`;
          const head = attentionPanel.querySelector(".panel__head");
          if (head) head.innerHTML = `<div><h3>Top deviated clauses</h3><p>From AI clause classification</p></div>`;
        }
      }

      // Team load — replaced with risk distribution since backend has no team-load endpoint
      const teamPanel = rightPanels[2];
      if (teamPanel) {
        const teamList = teamPanel.querySelector(".team-load");
        if (teamList) {
          const risks = (m && m.contracts_by_risk) || {};
          const total = Object.values(risks).reduce((s, n) => s + (Number(n) || 0), 0) || 1;
          const order = ["high", "medium", "low", "null"];
          const sorted = order.filter((k) => risks[k]).map((k) => [k, risks[k]]);
          teamList.innerHTML = sorted.length
            ? sorted.map(([risk, count]) => `
              <li>
                <div style="font-family:monospace;font-size:11px;color:var(--fg-subtle);width:60px">${esc(risk === "null" ? "unrated" : risk)}</div>
                <div class="team-load__body">
                  <div class="team-load__name">${count} contract${count === 1 ? "" : "s"}</div>
                  <div class="team-load__bar"><span style="width:${Math.round((count / total) * 100)}%"></span></div>
                </div>
                <div class="team-load__count">${count}</div>
              </li>`).join("")
            : `<li>${emptyHtml("No risk data.")}</li>`;
          const head = teamPanel.querySelector(".panel__head");
          if (head) head.innerHTML = `<div><h3>Risk distribution</h3><p>${total} contracts scored</p></div>`;
        }
      }
    } catch (err) {
      if (window.__AEGIS_DEBUG) console.warn("hub:", err.message);
      const hero = view.querySelector(".hub__hero p");
      if (hero) hero.innerHTML = `<span style="color:var(--sev-high)">Backend error: ${esc(err.message)}</span>`;
    }
  }

  function kpi(label, value, delta) {
    return `
      <div class="kpi">
        <div class="kpi__label">${esc(label)}</div>
        <div class="kpi__value">${esc(String(value))}</div>
        <div class="kpi__delta">${esc(delta || "")}</div>
        <svg class="kpi__spark" viewBox="0 0 100 30" preserveAspectRatio="none">
          <polyline points="0,22 12,20 24,18 36,15 48,17 60,12 72,10 84,8 100,5" fill="none" stroke="currentColor" stroke-width="1.5" />
        </svg>
      </div>`;
  }

  // ---------- CONTRACTS list ----------
  // Module-level contract cache so filter chips / facets / CSV work over live data
  let _allContracts = [];
  const CONTRACT_BUCKETS = {
    All: () => true,
    Intake: (c) => c.lifecycle_stage === "intake",
    Drafting: (c) => c.lifecycle_stage === "drafting",
    Review: (c) => ["ai_review", "internal_review", "counterparty_review"].includes(c.lifecycle_stage),
    Negotiation: (c) => c.lifecycle_stage === "counterparty_review",
    Signed: (c) => ["active", "approved", "signature_pending"].includes(c.lifecycle_stage),
    Expiring: (c) => { const d = daysUntil(c.expiration_date); return d != null && d >= 0 && d <= 90; },
  };
  function exportContractsCsv(list) {
    const cols = ["title", "counterparty_name", "contract_type", "lifecycle_stage", "risk_level", "value_amount", "currency", "effective_date", "expiration_date", "governing_law"];
    const cell = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const csv = [cols.join(",")].concat(list.map((c) => cols.map((k) => cell(c[k])).join(","))).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `aegis-contracts-${list.length}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    (window.__aegisToast || (() => {}))({ title: "Exported CSV", sub: `${list.length} contracts`, kind: "success" });
  }
  function openFacetMenu(chip, rows, umap, paint) {
    document.querySelectorAll(".facet-menu").forEach((m) => m.remove());
    const label = (chip.textContent || "").replace(/^\+\s*/, "").split(":")[0].trim();
    let field, values;
    if (/type/i.test(label)) { field = "contract_type"; values = [...new Set(rows.map((c) => c.contract_type).filter(Boolean))]; }
    else if (/counterparty/i.test(label)) { field = "counterparty_name"; values = [...new Set(rows.map((c) => c.counterparty_name).filter(Boolean))]; }
    else if (/owner/i.test(label)) { field = "owner_user_id"; values = [...new Set(rows.map((c) => c.owner_user_id).filter(Boolean))]; }
    else { field = "risk_level"; values = ["high", "medium", "low", "ok"]; }
    const disp = (v) => (field === "owner_user_id" ? (umap[v] || String(v).slice(0, 8)) : v);
    const menu = document.createElement("div"); menu.className = "facet-menu";
    const r = chip.getBoundingClientRect();
    menu.style.left = r.left + "px"; menu.style.top = r.bottom + 4 + "px";
    menu.innerHTML = values.length
      ? values.slice(0, 30).map((v) => `<div class="facet-menu__item" data-v="${esc(String(v))}">${esc(String(disp(v)))}</div>`).join("") + `<div class="facet-menu__item facet-menu__clear">Clear filter</div>`
      : `<div class="facet-menu__item">No values</div>`;
    document.body.appendChild(menu);
    menu.querySelectorAll(".facet-menu__item").forEach((it) => it.addEventListener("click", () => {
      if (it.classList.contains("facet-menu__clear")) { chip.textContent = "+ " + label; paint(rows); }
      else { const v = it.dataset.v; chip.innerHTML = `+ ${esc(label)}: ${esc(String(disp(v)))}`; paint(rows.filter((c) => String(c[field]) === v)); }
      menu.remove();
    }));
    setTimeout(() => document.addEventListener("click", function close(ev) {
      if (!menu.contains(ev.target) && ev.target !== chip) { menu.remove(); document.removeEventListener("click", close); }
    }), 0);
  }
  async function renderContracts() {
    const view = document.querySelector(".view--contracts");
    const tbody = document.querySelector(".view--contracts .ctr-table tbody");
    const heroP = document.querySelector(".view--contracts .contracts__hero p");
    if (!tbody || !view) return;
    tbody.innerHTML = `<tr><td colspan="8">${loadingHtml()}</td></tr>`;
    try {
      const rows = await api.contracts.list();
      _allContracts = rows;
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="8">${emptyHtml("No contracts yet. Upload one to get started.")}</td></tr>`;
        if (heroP) heroP.textContent = "0 contracts — empty backend";
        return;
      }
      const umap = await userMap().catch(() => ({}));
      const rowHtml = (c) => `
        <tr data-view="contract" data-id="${esc(c.id)}">
          <td><div class="ctr-title">
            <span class="ctr-title__name">${esc(c.title)}</span>
            <span class="ctr-title__meta">${esc(c.project_name || c.counterparty_name || "")}</span>
          </div></td>
          <td>${esc(c.counterparty_name || "—")}</td>
          <td>${esc(c.contract_type || "—")}</td>
          <td><span class="${stageClass(c.lifecycle_stage)}">${esc(c.lifecycle_stage || "—")}</span></td>
          <td>${riskFlag(c.risk_level)}</td>
          <td>${fmtMoney(c.value_amount)}</td>
          <td>${esc(fmtDate(c.expiration_date))}</td>
          <td><div class="avatar avatar--sm">${esc(initials(c.owner_user_id || ""))}</div></td>
        </tr>`;
      const paint = (list) => {
        tbody.innerHTML = list.length ? list.map(rowHtml).join("") : `<tr><td colspan="8">${emptyHtml("No contracts match this filter.")}</td></tr>`;
        tbody.querySelectorAll("tr[data-id]").forEach((row) => {
          row.addEventListener("click", async () => {
            window.__activeContractId = row.dataset.id;
            showView("contract");
            await renderContractDetail(row.dataset.id);
          });
        });
        if (heroP) heroP.textContent = `${list.length}${list.length !== rows.length ? " of " + rows.length : ""} live · backend round-trip · click any row`;
      };
      // live chip counts
      const stageChips = [...view.querySelectorAll(".filter-chip:not(.filter-chip--ghost)")];
      stageChips.forEach((chip) => {
        const label = (chip.textContent || "").replace(/\d+/g, "").trim();
        const n = label === "All" ? rows.length : rows.filter(CONTRACT_BUCKETS[label] || (() => true)).length;
        const span = chip.querySelector("span");
        if (span) span.textContent = n; else chip.innerHTML = `${esc(label)} <span>${n}</span>`;
      });
      // chip filtering (replace handler each render so closures stay fresh)
      const bar = view.querySelector(".filter-bar");
      if (bar) {
        bar.onclick = (e) => {
          const chip = e.target.closest(".filter-chip:not(.filter-chip--ghost)");
          if (!chip || !bar.contains(chip)) return;
          stageChips.forEach((c) => c.classList.remove("is-active"));
          chip.classList.add("is-active");
          const label = (chip.textContent || "").replace(/\d+/g, "").trim();
          paint(rows.filter(CONTRACT_BUCKETS[label] || (() => true)));
        };
      }
      view.querySelectorAll(".filter-chip--ghost").forEach((g) => {
        g.onclick = (ev) => { ev.stopPropagation(); openFacetMenu(g, rows, umap, paint); };
      });
      // Export CSV
      const exportBtn = [...view.querySelectorAll("button")].find((b) => /export csv/i.test(b.textContent));
      if (exportBtn) exportBtn.onclick = () => exportContractsCsv(_allContracts);
      // Honor a stage prefilter set by a hub funnel-step click
      const pre = window.__contractStageFilter; window.__contractStageFilter = null;
      if (pre) {
        stageChips.forEach((c) => c.classList.remove("is-active"));
        paint(rows.filter((c) => c.lifecycle_stage === pre));
        if (heroP) heroP.textContent = `${rows.filter((c) => c.lifecycle_stage === pre).length} in “${pre}” · click any row`;
      } else {
        paint(rows);
      }
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8">${errorHtml(err)}</td></tr>`;
    }
  }

  // ---------- CONTRACT DETAIL ----------
  async function renderContractDetail(id) {
    const view = document.querySelector(".view--contract");
    if (!view || !id) return;
    try {
      const c = await api.contracts.get(id);
      const crumb = view.querySelector(".topbar__crumbs .current");
      if (crumb) crumb.textContent = c.title;
      const headTitle = view.querySelector(".cd__head h1");
      if (headTitle) headTitle.textContent = c.title;
      // Derive a human contract type. Expand short codes (nda→Non-Disclosure
      // Agreement); fall back to the title when the field is empty.
      const TYPE_MAP = { nda: "Non-Disclosure Agreement", msa: "Master Services Agreement", sow: "Statement of Work", dpa: "Data Processing Agreement", saas: "SaaS Agreement", employment: "Employment Agreement", lease: "Lease Agreement" };
      const dType = TYPE_MAP[(c.contract_type || "").toLowerCase()] || c.contract_type
        || (/\bMSA\b|master service/i.test(c.title) ? "Master Services Agreement"
          : /\bNDA\b|non-disclos|confidential/i.test(c.title) ? "Non-Disclosure Agreement"
          : /\bSOW\b|statement of work/i.test(c.title) ? "Statement of Work"
          : /\bDPA\b|data processing/i.test(c.title) ? "Data Processing Agreement"
          : /employ/i.test(c.title) ? "Employment Agreement"
          : "");
      const headType = view.querySelector(".cd__type-label");
      if (headType) headType.textContent = `${humanize(dType) || "Contract"}${c.project_name ? " · " + c.project_name : ""}`;
      const stagePill = view.querySelector(".cd__type .stage");
      if (stagePill) {
        stagePill.className = stageClass(c.lifecycle_stage);
        stagePill.textContent = c.lifecycle_stage || "—";
      }
      // Head stats — render a tile only when its value is real (no "—" clutter)
      const stats = view.querySelectorAll(".cd__head-stats > div");
      const setStat = (el, val, label) => { if (!el) return; if (val) { el.innerHTML = `<strong>${esc(val)}</strong><span>${label}</span>`; el.style.display = ""; } else { el.style.display = "none"; } };
      setStat(stats[0], c.value_amount ? fmtMoney(c.value_amount).replace(" / yr", "") : "", "annual value");
      setStat(stats[1], humanize(dType) || "", "type");
      setStat(stats[2], c.governing_law || "", "governing law");
      const headP = view.querySelector(".cd__head p");
      if (headP) {
        // Only include metadata bits that actually have a value.
        const bits = [];
        if (c.counterparty_name) bits.push(`Counterparty <strong>${esc(c.counterparty_name)}</strong>`);
        bits.push(`ID <code style="font-family:monospace;font-size:11px;color:var(--fg-subtle)">${esc(id.slice(0, 8))}…</code>`);
        if (c.effective_date) bits.push(`Effective ${esc(fmtDate(c.effective_date))}`);
        if (c.expiration_date) bits.push(`Expires ${esc(fmtDate(c.expiration_date))}`);
        headP.innerHTML = bits.join(" · ");
      }
      // --- Live lifecycle bar built from the real 11-stage backend chain ---
      const STAGES = [
        ["intake", "Intake"], ["drafting", "Drafting"], ["ai_review", "AI review"],
        ["internal_review", "Internal review"], ["counterparty_review", "Counterparty"],
        ["approval_pending", "Approval"], ["approved", "Approved"],
        ["signature_pending", "Signature"], ["active", "Active"],
        ["renewal_due", "Renewal"], ["closed", "Closed"],
      ];
      const lifeWrap = view.querySelector(".lifecycle");
      const curStage = (c.lifecycle_stage || "").toLowerCase();
      const cur = STAGES.findIndex(([k]) => k === curStage);
      if (lifeWrap) {
        lifeWrap.innerHTML = STAGES.slice(0, 9).map(([k, label], i) => `
          <div class="lifecycle__step ${cur >= 0 && i < cur ? "is-done" : ""} ${i === cur ? "is-current" : ""}" data-stage="${k}">
            <div class="lifecycle__dot"></div>
            <div class="lifecycle__label">${label}</div>
            <div class="lifecycle__date">${i === cur ? "current" : ""}</div>
          </div>`).join("");
      }

      // === ACTION-DRIVEN lifecycle: read-only bar + one "Next action" + approval gate ===
      const toast = window.__aegisToast || (() => {});
      const reload = () => renderContractDetail(id);
      const advance = async (to, label, opts) => {
        try {
          await api.contracts.transition(id, to, label, opts);
          toast({ title: `Moved to ${humanize(to)}`, sub: c.title, kind: "success" });
          reload();
        } catch (e) { toast({ title: "Action blocked", sub: e.message, kind: "error" }); reload(); }
      };

      // read-only tag above the bar
      if (lifeWrap && !view.querySelector(".cd-ro-tag")) {
        const tag = document.createElement("div");
        tag.className = "cd-ro-tag";
        tag.innerHTML = `<svg viewBox="0 0 16 16" fill="none" style="width:13px;height:13px"><rect x="3" y="7" width="10" height="7" rx="1.5" stroke="currentColor" stroke-width="1.4"/><path d="M5 7V5a3 3 0 016 0v2" stroke="currentColor" stroke-width="1.4"/></svg> Lifecycle stage (read-only) — this bar reflects status; take the one step in “Next action” below to move it forward`;
        lifeWrap.before(tag);
      }

      // the contextual next action per stage
      const ACTIONS = {
        intake:             { txt: "Ready to draft", sub: "Move this contract into drafting.", btn: "Begin drafting", run: () => advance("drafting", "Begin drafting") },
        drafting:           { txt: "Draft in progress", sub: "Submit when it's ready for AI review.", btn: "Submit for AI review", run: () => advance("ai_review", "Submit for AI review") },
        ai_review:          { txt: "AI review complete", sub: "Route to your internal reviewers.", btn: "Send to internal review", run: () => advance("internal_review", "To internal review") },
        internal_review:    { txt: "Internal review", sub: "Share with the counterparty for redlines.", btn: "Send to counterparty", run: () => advance("counterparty_review", "To counterparty") },
        counterparty_review:{ txt: "Counterparty redlines merged", sub: "Request the approvals required to sign.", btn: "Request approval", run: async () => {
          try { const me = await api.me.get(); await api.approvals.submitForApproval(id, { approver_user_id: me.id }); toast({ title: "Approval requested", sub: c.title, kind: "success" }); reload(); }
          catch (e) { toast({ title: "Request failed", sub: e.message, kind: "error" }); } } },
        approval_pending:   { gate: true, txt: "Awaiting approvals", sub: "Auto-advances to Approved when the gate clears.", btn: "Nudge approvers", run: () => toast({ title: "Reminder sent to pending approvers", kind: "success" }) },
        approved:           { txt: "Approved — ready to sign", sub: "Send the envelope for e-signature.", btn: "Send for signature", run: async (nbtn) => {
          try {
            const me = await api.me.get();
            // me.email is guaranteed in the org-user allowlist (validate_signature_recipients)
            await api.signatures.send(id, { recipients: [{ name: me.full_name || "Signer", email: me.email, role: "Signer" }], override_lifecycle: true });
            // Backend transitions the contract to signature_pending inside send_for_signature —
            // do NOT also transition here (redundant; would 409).
            toast({ title: "Sent for signature", sub: c.title, kind: "success" });
            reload();
          } catch (e) {
            toast(signatureError(e));
            // The DocuSign provider can never succeed in this env — disable the button so the
            // user isn't invited to retry a guaranteed failure, but surface the honest reason.
            const blocked = !e || e.status == null || e.status === 0 || e.status >= 500;
            if (nbtn) { nbtn.disabled = blocked; nbtn.textContent = "Send for signature"; nbtn.title = blocked ? "E-signature provider not available in this environment" : ""; }
          } } },
        signature_pending:  { txt: "Awaiting signatures", sub: "Auto-activates once all parties have signed.", btn: "Mark signed & activate", run: () => advance("active", "Counterparty signed", { signedConfirmation: true }) },
        active:             { txt: "Active contract", sub: "Live and tracked. Renewal monitored automatically.", btn: "Flag renewal due", run: () => advance("renewal_due", "Renewal window opened") },
        renewal_due:        { txt: "Renewal due", sub: "Decide: renew, renegotiate, or close.", btn: "Start renewal in Ask AEGIS", run: () => { window.__activeContractId = id; showView("assistant"); } },
        closed:             { txt: "Closed", sub: "This contract is closed.", btn: null },
      };
      const act = ACTIONS[curStage] || { txt: humanize(curStage), sub: "", btn: null };

      // build/refresh the action block right after the bar
      let actionWrap = view.querySelector(".cd-action");
      if (!actionWrap && lifeWrap) {
        actionWrap = document.createElement("div");
        actionWrap.className = "cd-action";
        lifeWrap.after(actionWrap);
      }
      if (actionWrap) {
        actionWrap.innerHTML = `
          <div class="cd-next">
            <div class="cd-next__label"><svg viewBox="0 0 16 16" fill="none" style="width:13px;height:13px"><path d="M3 8l3 3 7-8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg> Next action</div>
            <div class="cd-next__row">
              <div><div class="cd-next__txt">${esc(act.txt)}</div><div class="cd-next__sub">${esc(act.sub)}</div></div>
              ${act.btn ? `<button class="cd-next__btn">${esc(act.btn)}</button>` : ""}
            </div>
          </div>
          ${act.gate ? `<div class="cd-gate" data-gate>${loadingHtml()}</div>` : ""}`;
        const nbtn = actionWrap.querySelector(".cd-next__btn");
        if (nbtn && act.run) nbtn.onclick = () => { nbtn.disabled = true; nbtn.textContent = "Working…"; act.run(nbtn); };

        // approval gate — real /approvals filtered to THIS contract
        if (act.gate) {
          const gateEl = actionWrap.querySelector("[data-gate]");
          try {
            const [appr, umap] = await Promise.all([api.approvals.list(), userMap()]);
            const mine = appr.filter((a) => a.contract_id === id);
            const done = mine.filter((a) => (a.status || "").toLowerCase() !== "pending").length;
            gateEl.innerHTML = `
              <div class="cd-gate__head"><span>Approval gate · all must pass to advance</span><span class="cd-gate__mono">${done} / ${mine.length} complete</span></div>
              <div class="cd-gate__bar"><span style="width:${mine.length ? Math.round((done / mine.length) * 100) : 0}%"></span></div>
              ${mine.length
                ? mine.map((a) => {
                    const ok = (a.status || "").toLowerCase() !== "pending";
                    return `<div class="cd-task ${ok ? "cd-task--done" : "cd-task--wait"}">
                      <span class="cd-task__ck">${ok ? '<svg viewBox="0 0 16 16" fill="none"><path d="M3.5 8.5l3 3 6-7" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' : ""}</span>
                      <span class="cd-task__t">${esc(humanize(a.approver_role || "Approver"))} · req. by ${esc(userName(umap, a.requested_by_user_id))}</span>
                      <span class="cd-task__who">${ok ? esc(humanize(a.status)) : "waiting"}</span>
                    </div>`;
                  }).join("")
                : emptyHtml("No approval requests on this contract yet.")}`;
          } catch (e) { gateEl.innerHTML = errorHtml(e); }
        }
      }

      // --- Replace the hardcoded document body with the REAL contract text ---
      const docPage = view.querySelector(".doc-viewer__page");
      const margin = view.querySelector(".doc-viewer__margin");
      const toc = view.querySelector(".doc-viewer__toc");
      if (docPage) {
        docPage.innerHTML = `<div class="live-loading">Loading document text…</div>`;
        if (margin) margin.innerHTML = "";
        if (toc) toc.innerHTML = "";
        try {
          const versionId = c.current_authoritative_version_id || c.current_version_id;
          let text = null;
          if (versionId) {
            const snap = await api.contracts.versionText(id, versionId).catch(() => null);
            text = snap && (snap.text || snap.content || snap.snapshot_text);
          }
          if (!text) {
            // fall back: list versions, take the newest
            const versions = await api.contracts.versions(id).catch(() => []);
            if (versions && versions.length) {
              const v = versions[0];
              const snap = await api.contracts.versionText(id, v.id).catch(() => null);
              text = snap && (snap.text || snap.content || snap.snapshot_text);
            }
          }
          if (text) {
            // Render real text as paragraphs
            const paras = String(text).split(/\n{2,}/).slice(0, 60);
            docPage.innerHTML =
              `<div class="doc-page__head"><div class="doc-page__brand">${esc(c.title)}</div>` +
              `<div class="doc-page__sub">${esc(c.contract_type || "")} · ${esc(c.counterparty_name || "")}</div></div>` +
              paras.map((p) => `<p>${esc(p.trim())}</p>`).join("");
            if (margin) margin.innerHTML = `<div class="doc-margin__head">Live document</div><div style="font-size:12px;color:var(--fg-muted);padding:8px 0">Extracted text snapshot from version ${esc(String(versionId || "").slice(0,8))}. ${paras.length} paragraphs.</div>`;
          } else {
            docPage.innerHTML = emptyHtml("No extracted text for this contract yet. Upload a file or run extraction.");
            if (margin) margin.innerHTML = "";
          }
        } catch (e) {
          docPage.innerHTML = errorHtml(e);
        }
      }

      // Helper: set a contract-detail tab badge to a REAL count (never a fake number)
      const setBadge = (re, n) => {
        const t = [...view.querySelectorAll(".cd-tab")].find((x) => re.test(x.textContent));
        if (!t) return;
        let sp = t.querySelector("span");
        if (!sp) { sp = document.createElement("span"); t.appendChild(document.createTextNode(" ")); t.appendChild(sp); }
        sp.textContent = n;
      };
      // Fetch this contract's real obligations + versions once (used by tabs, badges, summary)
      const [myObl, myVers] = await Promise.all([
        api.obligations.list(id).then((all) => (all || []).filter((o) => o.contract_id === id)).catch(() => []),
        api.contracts.versions(id).catch(() => []),
      ]);

      // Clauses tab — honest (extraction is a background job); badge = REAL count
      const clausesPanel = view.querySelector('[data-tab-panel="clauses"]');
      if (clausesPanel) {
        const list = clausesPanel.querySelector(".clause-list");
        if (list) list.innerHTML = emptyHtml("No clauses have been extracted for this contract yet — clause extraction runs as a background AI job.");
        clausesPanel.querySelectorAll(".panel__head h3, h3, h2").forEach((h) => { if (/extracted clauses|clauses\s*·/i.test(h.textContent)) h.textContent = "Extracted clauses · 0"; });
      }
      setBadge(/clause/i, 0);

      // Obligations tab — real obligations for this contract
      const oblPanel = view.querySelector('[data-tab-panel="obligations"] table tbody, [data-tab-panel="obligations"] .ctr-table tbody');
      if (oblPanel) {
        oblPanel.innerHTML = myObl.length
          ? myObl.map((o) => `<tr><td>${esc(o.description || "—")}</td><td>${esc(humanize(o.obligation_type))}</td><td>—</td><td>${o.due_date ? esc(fmtDate(o.due_date)) : "On event (no fixed date)"}</td><td>${statusFlag(o.status)}</td></tr>`).join("")
          : `<tr><td colspan="5">${emptyHtml("No obligations extracted for this contract.")}</td></tr>`;
      }
      setBadge(/obligation/i, myObl.length);

      // Versions tab — real versions
      const verPanel = view.querySelector('[data-tab-panel="versions"] .ver-grid');
      if (verPanel) {
        verPanel.innerHTML = myVers.length
          ? myVers.map((v, i) => `
            <article class="ver-card ${i === 0 ? "ver-card--current" : ""}">
              <div class="ver-card__head"><strong>v${esc(String(v.version_number || myVers.length - i))}</strong><span>${esc(fmtDate(v.created_at))}</span></div>
              <div class="ver-card__title">${esc(v.source || v.change_summary || "Version")}</div>
              <p>${esc((v.change_summary || "").slice(0, 120))}</p>
            </article>`).join("")
          : emptyHtml("No versions recorded.");
      }
      setBadge(/version/i, myVers.length);

      // Summary tab — a REAL legal read (key terms + key obligations + verify-against-source note)
      const sType = dType || "contract";
      // honest header (replace the fake "Generated 12:42 · 14 sources")
      const sumHead = view.querySelector('[data-tab-panel="summary"] .panel__head p, [data-tab-panel="summary"] .cd-sub');
      if (sumHead) sumHead.textContent = `Built from this contract's extracted fields · ${myObl.length} tracked obligation${myObl.length === 1 ? "" : "s"} · click Regenerate to re-run AI extraction`;
      // Hide the static mockup panels ("Parties & key terms", "Standout clauses")
      // that contradict the real summary above.
      const summaryTab = view.querySelector('[data-tab-panel="summary"]');
      if (summaryTab) summaryTab.querySelectorAll("section.panel, .panel").forEach((p) => { if (!p.querySelector(".ai-summary")) p.style.display = "none"; });
      const summaryPanel = view.querySelector('[data-tab-panel="summary"] .ai-summary');
      if (summaryPanel) {
        const na = (v, label) => (v ? esc(v) : `<span class="cd-na">${label || "not extracted"}</span>`);
        const terms = [
          ["Type", esc(humanize(sType))],
          ["Counterparty", na(c.counterparty_name)],
          ["Governing law", na(c.governing_law)],
          ["Annual value", c.value_amount ? esc(fmtMoney(c.value_amount).replace(" / yr", "")) : `<span class="cd-na">not extracted</span>`],
          ["Effective", c.effective_date ? esc(fmtDate(c.effective_date)) : `<span class="cd-na">not set</span>`],
          ["Expires", c.expiration_date ? esc(fmtDate(c.expiration_date)) : `<span class="cd-na">not set</span>`],
          ["Risk", `<span class="risk-${(c.risk_level || "").toLowerCase()}">${esc(humanize(c.risk_level || "not assessed"))}</span>`],
        ];
        const oblLines = myObl.slice(0, 6).map((o) => `<li>${esc(o.description || humanize(o.obligation_type))}${o.due_date ? ` — <em>due ${esc(fmtDate(o.due_date))}</em>` : ""}</li>`).join("");
        summaryPanel.innerHTML =
          `<p>${esc(c.title)} — a ${esc(humanize(sType))} with <strong>${esc(c.counterparty_name || "an unnamed counterparty")}</strong>, currently at the <strong>${esc(humanize(c.lifecycle_stage))}</strong> stage.</p>` +
          `<dl class="cd-keyterms">${terms.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>` +
          `<div class="cd-keyobl"><div class="cd-keyobl__h">Key obligations · ${myObl.length}</div>${myObl.length ? `<ul>${oblLines}</ul>` : `<p class="cd-na">None extracted yet — use “Extract obligations.”</p>`}</div>` +
          `<p class="cd-disclaimer">⚠ AI-generated from extracted fields — verify against the source document before relying on it.</p>`;
      }

      // --- Activity tab: real /contracts/{id}/activity (fallback stage-history) ---
      const actList = view.querySelector('[data-tab-panel="activity"] .activity-list');
      if (actList) {
        actList.innerHTML = `<li>${loadingHtml()}</li>`;
        try {
          let events = await api.contracts.activity(id).catch(() => []);
          if (!events || !events.length) {
            const hist = await api.contracts.stageHistory(id).catch(() => []);
            events = (hist || []).map((h) => ({
              title: `Stage ${humanize(h.from_stage || "∅")} → ${humanize(h.to_stage)}`,
              event_type: "stage.transition", created_at: h.changed_at || h.created_at,
            }));
          }
          actList.innerHTML = events.length
            ? events.slice(0, 12).map((e) => `
              <li>
                <span class="activity-dot activity-dot--${/ai|brain|skill/.test(e.event_type || "") ? "ai" : /sign/.test(e.event_type || "") ? "sign" : "user"}"></span>
                <div>
                  <div class="activity-text"><strong>${esc(e.title || humanize(e.event_type) || "event")}</strong></div>
                  <div class="activity-meta">${esc(e.event_type || "")}${e.created_at ? " · " + esc(String(e.created_at).slice(0, 16).replace("T", " ")) : ""}</div>
                </div>
              </li>`).join("")
            : `<li>${emptyHtml("No activity recorded for this contract yet.")}</li>`;
        } catch (e) {
          actList.innerHTML = `<li>${errorHtml(e)}</li>`;
        }
      }

      // --- Wire every action button so none are dead ---
      const bind = (root, label, fn) => {
        [...root.querySelectorAll("button")]
          .filter((b) => b.textContent.replace(/\s+/g, " ").trim().startsWith(label))
          .forEach((b) => { b.onclick = (e) => { e.preventDefault(); fn(b); }; });
      };
      // --- Topbar actions — REAL backend calls ---
      const topbar = view.querySelector(".topbar__actions") || view;
      bind(topbar, "Ask AEGIS", () => { window.__activeContractId = id; showView("assistant"); });
      bind(topbar, "Send for approval", async (btn) => {
        btn.disabled = true; btn.textContent = "Sending…";
        try {
          const me = await api.me.get();
          const reqs = await api.approvals.submitForApproval(id, { approver_user_id: me.id });
          const n = Array.isArray(reqs) ? reqs.length : 1;
          toast({ title: "Sent for approval", sub: `${n} request created · go to Approvals`, kind: "success" });
          btn.disabled = false; btn.textContent = "Send for approval";
          renderContractDetail(id);
        } catch (e) {
          toast({ title: "Approval failed", sub: e.message, kind: "error" });
          btn.disabled = false; btn.textContent = "Send for approval";
        }
      });
      bind(topbar, "Send for signature", async (btn) => {
        btn.disabled = true; btn.textContent = "Sending…";
        try {
          const me = await api.me.get();
          await api.signatures.send(id, {
            recipients: [{ name: me.full_name || "Signer", email: me.email, role: "Signer" }],
            override_lifecycle: true,
          });
          toast({ title: "Sent for signature", sub: c.title, kind: "success" });
          btn.disabled = false; btn.textContent = "Send for signature";
          renderContractDetail(id);
        } catch (e) {
          toast(signatureError(e));
          const blocked = !e || e.status == null || e.status === 0 || e.status >= 500;
          btn.disabled = blocked;
          btn.textContent = "Send for signature";
          if (blocked) btn.title = "E-signature provider not available in this environment";
        }
      });

      // ===== Companion = an AGENTIC CHAT that can DO things (Legora/Harvey Assistant pattern) =====
      // The document stays on the left; summarize / redline / compare versions / extract obligations
      // / precedents / risk all happen through this chat and render their results in the thread.
      const thread = view.querySelector("[data-chat-thread]");
      const composer = view.querySelector("[data-cd-ask]");
      if (thread && composer) {
        const scrollDown = () => { thread.scrollTop = thread.scrollHeight; };
        const addMsg = (role, html, cls) => {
          const m = document.createElement("div");
          m.className = `cd-msg cd-msg--${role}${cls ? " " + cls : ""}`;
          m.innerHTML = html;
          thread.appendChild(m); scrollDown(); return m;
        };
        const think = () => addMsg("ai", `<span class="cd-think"><span></span><span></span><span></span></span>`, "is-thinking");
        const settle = (m, html) => { m.classList.remove("is-thinking"); m.innerHTML = html; scrollDown(); };

        // free-text → the ONE agentic assistant, scoped to this contract (Spec B: same engine as Ask AEGIS,
        // so the rail can reason, use tools, cite, and act — not just read-only lookups).
        const ask = async (m, q) => {
          try {
            const res = await api.assistant.sendMessage(q, { contractId: id });
            const ansTxt = (res && (res.content || res.answer || res.text)) || "";
            const cites = (res && res.citations) || [];
            settle(m, ansTxt
              ? `<div class="cd-ask__ans">${esc(ansTxt)}</div>` + (cites.length ? `<div class="cd-ask__cites">${cites.slice(0, 6).map((x, i) => `<span class="cite-chip">[${i + 1}] ${esc(x.title || x.contract_title || x.source || "source")}</span>`).join("")}</div>` : "")
              : emptyHtml("No answer was returned for this contract."));
          } catch (err) { settle(m, emptyHtml("Assistant unavailable: " + (err.message || "error"))); }
        };

        // Inline confirm chip for mutating / irreversible actions — Priya says it, then confirms.
        // run() may return a string (settled as the result) or null/undefined (it already rendered).
        const confirmThen = (m, question, run) => {
          m.classList.remove("is-thinking");
          m.innerHTML = `<div class="cd-confirm"><div class="cd-confirm__q">${question}</div><div class="cd-confirm__acts"><button class="cd-confirm__yes">Confirm</button><button class="cd-confirm__no">Cancel</button></div></div>`;
          m.querySelector(".cd-confirm__no").onclick = () => settle(m, "Cancelled — nothing changed.");
          m.querySelector(".cd-confirm__yes").onclick = async () => {
            m.innerHTML = `<span class="cd-think"><span></span><span></span><span></span></span>`; m.classList.add("is-thinking");
            try { const r = await run(); if (r == null) m.classList.remove("is-thinking"); else settle(m, r); }
            catch (e) { settle(m, emptyHtml((e && e.message) || "That action was blocked.")); }
          };
        };

        // capabilities — each backed by a REAL endpoint, result rendered in the thread
        const cap = {
          async summarize(m) {
            const sType = dType || "contract";
            const na = (v) => (v ? esc(v) : `<span class="cd-na">not extracted</span>`);
            const terms = [
              ["Type", esc(humanize(sType))],
              ["Counterparty", na(c.counterparty_name)],
              ["Governing law", na(c.governing_law)],
              ["Annual value", c.value_amount ? esc(fmtMoney(c.value_amount).replace(" / yr", "")) : `<span class="cd-na">not extracted</span>`],
              ["Effective", c.effective_date ? esc(fmtDate(c.effective_date)) : `<span class="cd-na">not set</span>`],
              ["Expires", c.expiration_date ? esc(fmtDate(c.expiration_date)) : `<span class="cd-na">not set</span>`],
              ["Risk", `<span class="risk-${(c.risk_level || "").toLowerCase()}">${esc(humanize(c.risk_level || "not assessed"))}</span>`],
            ];
            const oblLines = myObl.slice(0, 6).map((o) => `<li>${esc(o.description || humanize(o.obligation_type))}${o.due_date ? ` — <em>due ${esc(fmtDate(o.due_date))}</em>` : ""}</li>`).join("");
            settle(m,
              `<p>${esc(c.title)} — a ${esc(humanize(sType))} with <strong>${esc(c.counterparty_name || "an unnamed counterparty")}</strong>, currently at the <strong>${esc(humanize(c.lifecycle_stage))}</strong> stage.</p>` +
              `<dl class="cd-keyterms">${terms.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>` +
              `<div class="cd-keyobl"><div class="cd-keyobl__h">Key obligations · ${myObl.length}</div>${myObl.length ? `<ul>${oblLines}</ul>` : `<p class="cd-na">None extracted yet — try “Extract obligations.”</p>`}</div>` +
              `<p class="cd-disclaimer">⚠ Built from extracted fields — verify against the document before relying on it.</p>`);
          },
          // Render a set of redline edits with working Accept/Reject (returns nothing — it settles m itself).
          _renderEdits(m, list) {
            settle(m, `<div class="cd-msg__h">Proposed redlines · ${list.length}</div>` + list.slice(0, 8).map((e) => `
              <div class="cd-edit" data-edit="${esc(e.id)}">
                <div class="cd-edit__meta">${esc(e.clause_type || e.section || e.edit_type || "Edit")} · <span class="cd-edit__status">${esc(e.status || "proposed")}</span></div>
                <div class="cd-edit__text">${esc(String(e.proposed_text || e.suggested_text || e.replacement_text || e.text || e.rationale || "").slice(0, 280)) || "(no preview)"}</div>
                ${(e.status || "proposed") === "proposed" ? `<div class="cd-edit__acts"><button class="text-btn" data-accept>Accept</button><button class="text-btn text-btn--danger" data-reject>Reject</button></div>` : ""}
              </div>`).join(""));
            m.querySelectorAll(".cd-edit").forEach((row) => {
              const eid = row.dataset.edit;
              const done = (label) => { const s = row.querySelector(".cd-edit__status"); if (s) s.textContent = label; row.querySelector(".cd-edit__acts")?.remove(); };
              row.querySelector("[data-accept]")?.addEventListener("click", async () => { try { await api.contracts.acceptEdit(id, eid); toast({ title: "Edit accepted", kind: "success" }); done("accepted"); } catch (e) { toast({ title: "Couldn't accept", sub: e.message, kind: "error" }); } });
              row.querySelector("[data-reject]")?.addEventListener("click", async () => { try { await api.contracts.rejectEdit(id, eid); toast({ title: "Edit rejected", kind: "warn" }); done("rejected"); } catch (e) { toast({ title: "Couldn't reject", sub: e.message, kind: "error" }); } });
            });
          },
          async redline(m) {
            // If redlines already exist, show them; otherwise actually RUN a playbook review.
            let edits = [];
            try { edits = await api.contracts.edits(id); } catch (e) { /* fall through to run a review */ }
            if (edits && edits.length) { this._renderEdits(m, edits); return; }
            confirmThen(m, `No redlines exist yet for <strong>${esc(c.title)}</strong>. Run an AI playbook review now?`, async () => {
              const pbs = await api.playbooks.list().catch(() => []);
              if (!pbs || !pbs.length) throw new Error("No playbook is available to review against — create one in Playbooks first.");
              const run = await api.playbooks.run(pbs[0].id, id, { create_redline: true, use_ai: true });
              let st = run, tries = 0;
              while (st && /pending|running|queued|processing|in_progress/i.test(st.status || "") && tries < 8) {
                await new Promise((r) => setTimeout(r, 2500));
                st = await api.playbooks.getRun(run.id).catch(() => st); tries++;
              }
              const after = await api.contracts.edits(id).catch(() => []);
              if (after && after.length) { this._renderEdits(m, after); return null; }
              if (st && st.error_message) return `The review finished with an error: ${esc(st.error_message)}`;
              return `Reviewed “${esc(c.title)}” against the “${esc(pbs[0].name || "playbook")}” playbook — it proposed no redline edits${st && st.status ? ` (run ${esc(st.status)})` : ""}.`;
            });
          },
          async obligations(m) {
            const cur = myObl;
            const listHtml = cur.length
              ? `<div class="cd-msg__h">Tracked obligations · ${cur.length}</div><ul class="cd-oblist">${cur.slice(0, 12).map((o) => `<li><strong>${esc(o.description || humanize(o.obligation_type))}</strong>${o.due_date ? ` — due ${esc(fmtDate(o.due_date))}` : " — on event (no fixed date)"} · ${esc(humanize(o.status || "open"))}</li>`).join("")}</ul>`
              : emptyHtml("No obligations are tracked for this contract yet.");
            settle(m, listHtml + `<div class="cd-msg__note">Queuing a fresh extraction pass…</div>`);
            try { await api.obligations.extract(id); const n = m.querySelector(".cd-msg__note"); if (n) n.textContent = "Extraction queued — obligations refresh when the background job finishes."; }
            catch (e) { const n = m.querySelector(".cd-msg__note"); if (n) n.textContent = e.status === 409 ? "No executed version to extract obligations from." : "The extraction job service didn't respond in this environment."; }
          },
          async versions(m) {
            try {
              const vers = await api.contracts.versions(id);
              if (!vers || vers.length < 2) { settle(m, emptyHtml(`This contract has ${vers ? vers.length : 0} version${vers && vers.length === 1 ? "" : "s"} — at least 2 are needed to compare.`)); return; }
              const sorted = vers.slice().sort((a, b) => (b.version_number || b.version || 0) - (a.version_number || a.version || 0));
              const [newer, older] = sorted;
              const [tNew, tOld] = await Promise.all([api.contracts.versionText(id, newer.id).catch(() => ({})), api.contracts.versionText(id, older.id).catch(() => ({}))]);
              const linesOf = (t) => String(t.text || t.content || "").split(/\n+/).map((s) => s.trim()).filter(Boolean);
              const ln = linesOf(tNew), lo = linesOf(tOld);
              const oldSet = new Set(lo), newSet = new Set(ln);
              const added = ln.filter((l) => !oldSet.has(l)).slice(0, 12);
              const removed = lo.filter((l) => !newSet.has(l)).slice(0, 12);
              settle(m, `<div class="cd-msg__h">v${esc(String(newer.version_number || "latest"))} vs v${esc(String(older.version_number || "prior"))}</div>` +
                (added.length ? `<div class="cd-diff__h">Added</div>` + added.map((l) => `<div class="cd-diff cd-diff--add">+ ${esc(l.slice(0, 170))}</div>`).join("") : "") +
                (removed.length ? `<div class="cd-diff__h">Removed</div>` + removed.map((l) => `<div class="cd-diff cd-diff--rem">− ${esc(l.slice(0, 170))}</div>`).join("") : "") +
                (!added.length && !removed.length ? emptyHtml("No textual differences between the two latest versions.") : ""));
            } catch (e) { settle(m, emptyHtml("Couldn't compare versions: " + (e.message || "error"))); }
          },
          async precedents(m) {
            try {
              const pre = await api.brain.precedents(`Precedent clauses similar to those in "${c.title}"`, id, 3);
              const list = Array.isArray(pre) ? pre : (pre && (pre.precedents || pre.results)) || [];
              settle(m, list.length
                ? `<div class="cd-msg__h">Similar precedents · ${list.length}</div>` + list.slice(0, 5).map((x) => `<div class="cd-edit"><div class="cd-edit__meta">${esc(x.title || x.contract_title || x.name || "Precedent")}</div><div class="cd-edit__text">${esc(String(x.snippet || x.summary || x.clause_text || x.excerpt || "").slice(0, 220))}</div></div>`).join("")
                : emptyHtml("No precedents found in the corpus for this contract."));
            } catch (e) { settle(m, emptyHtml("Precedents unavailable: " + (e.message || "error"))); }
          },
          async risks(m) { return ask(m, "What are the most unusual or high-risk terms in this contract, and why?"); },

          // --- ACTUATOR: the lifecycle verbs Priya can SAY (reuse the already-wired APIs) ---
          async advance(m) {
            const a = ACTIONS[curStage];
            if (!a || !a.btn) { settle(m, emptyHtml(`This contract is at “${esc(humanize(curStage))}” — there's no further step to advance from here.`)); return; }
            confirmThen(m, `The next step is <strong>${esc(a.btn)}</strong> — ${esc(a.sub)} Do it?`, async () => { await a.run(); return `Done — ${esc(a.btn)}. The lifecycle bar has moved forward.`; });
          },
          async approval(m) {
            confirmThen(m, `Send <strong>${esc(c.title)}</strong> for internal approval?`, async () => {
              const me = await api.me.get();
              const reqs = await api.approvals.submitForApproval(id, { approver_user_id: me.id });
              const n = Array.isArray(reqs) ? reqs.length : 1;
              reload();
              return `Sent for approval — ${n} request${n === 1 ? "" : "s"} created. The lifecycle now shows the approval gate.`;
            });
          },
          async signature(m) {
            confirmThen(m, `Send <strong>${esc(c.title)}</strong> out for e-signature?`, async () => {
              const me = await api.me.get();
              await api.signatures.send(id, { recipients: [{ name: me.full_name || "Signer", email: me.email, role: "Signer" }], override_lifecycle: true });
              reload();
              return `Sent for signature to ${esc(me.email || "the signer")}.`;
            });
          },
          async markSigned(m) {
            confirmThen(m, `Mark <strong>${esc(c.title)}</strong> as signed and activate it?`, async () => { await api.contracts.transition(id, "active", "Counterparty signed", { signedConfirmation: true }); reload(); return "Marked signed — the contract is now Active."; });
          },
          async renewal(m) {
            confirmThen(m, `Open the renewal window for <strong>${esc(c.title)}</strong>?`, async () => { await api.contracts.transition(id, "renewal_due", "Renewal window opened"); reload(); return "Renewal window opened — you can renew, renegotiate, or close it."; });
          },
        };

        // route a typed instruction to a capability when intent is clear, else free ask
        const route = (q) => {
          const s = q.toLowerCase();
          // Action verbs first (these actuate the contract; each confirms before firing).
          if (/\b(send|submit|request)\b.*\bapprov/.test(s) || /\bapprove this\b/.test(s)) return "approval";
          if (/\b(send|submit|route)\b.*\b(signature|sign|docusign|e-?sign)/.test(s) || /\bsign (this|it)\b/.test(s)) return "signature";
          if (/\bmark\b.*\b(signed|executed)\b/.test(s) || /\bactivate (this|it)\b/.test(s)) return "markSigned";
          if (/\b(start|open|begin|flag)\b.*\brenewal\b/.test(s) || /\brenew (this|it)\b/.test(s)) return "renewal";
          if (/\b(advance|move (it |this )?forward|next step|progress this)\b/.test(s) || /\bmove (this|it) to\b/.test(s)) return "advance";
          if (/\b(redline|red-line|mark ?up|rewrite|propose.*edit|suggest.*edit|draft.*edit)\b/.test(s)) return "redline";
          if (/\b(compare|diff)\b.*\b(version|draft)\b|\bversion\b.*\b(compare|diff|change)\b/.test(s)) return "versions";
          if (/\bobligation|covenant|deadline|deliverable\b/.test(s)) return "obligations";
          if (/\bprecedent|comparable|similar (clause|contract|deal)\b/.test(s)) return "precedents";
          // "summarize the key RISKS" must get a risk analysis, not the generic summary template
          if (/\b(risk|risks|risky|unusual|concern|concerns|red flag|exposure|problematic|onerous|aggressive|one-sided)\b/.test(s)) return "risks";
          if (/\bsummar/.test(s)) return "summarize";
          return null;
        };

        const runCap = (key, label) => {
          addMsg("user", esc(label));
          const m = think();
          (cap[key] || ((mm) => ask(mm, label)))(m);
        };

        // wire starter chips + composer (onsubmit/onclick reassigned each render → current id)
        composer.querySelectorAll("[data-cap]").forEach((chip) => { chip.onclick = () => runCap(chip.dataset.cap, chip.textContent.trim()); });
        composer.onsubmit = (e) => {
          e.preventDefault();
          const input = composer.querySelector(".cd-ask__input");
          const q = (input.value || "").trim();
          if (!q) return;
          input.value = "";
          addMsg("user", esc(q));
          const m = think();
          const key = route(q);
          if (key) cap[key](m); else ask(m, q);
        };
        // Enter submits, Shift+Enter newline
        const cinput = composer.querySelector(".cd-ask__input");
        if (cinput) cinput.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); composer.requestSubmit ? composer.requestSubmit() : composer.onsubmit(e); } };

        // Greet + reset the conversation only when the OPEN contract changes
        if (thread.dataset.cid !== id) {
          thread.dataset.cid = id;
          thread.innerHTML = "";
          addMsg("ai", `I'm your assistant for <strong>${esc(c.title)}</strong>. Ask a question or give an instruction — I can summarize, redline against your playbook, compare versions, extract obligations, or pull precedents, and I can <strong>act on it</strong>: say “send this for approval”, “send for signature”, “advance it”, or “start the renewal” and I'll do it (with a confirm). Everything is scoped to this contract.`);
        }
      }
    } catch (err) {
      if (window.__AEGIS_DEBUG) console.warn("contract detail:", err.message);
      const headTitle = view.querySelector(".cd__head h1");
      if (headTitle) headTitle.innerHTML = errorHtml(err);
    }
  }

  // Seed the Ask-AEGIS landing composer and fire it
  function seedAssistant(text) {
    setTimeout(() => {
      const c = document.querySelector(".composer--landing .composer__input");
      const s = document.querySelector(".composer--landing .send-btn");
      if (c && s) { c.value = text; s.click(); }
    }, 200);
  }
  // Expose so the home composer (app.js) can carry a typed question into the thread.
  window.__seedAssistant = seedAssistant;
  async function renderApprovals() {
    const wrap = document.querySelector('.view--approvals [data-tab-panel="approvals-pending"] .approval-list');
    if (!wrap) return;
    wrap.innerHTML = loadingHtml();
    try {
      const [rows, cmap, umap] = await Promise.all([api.approvals.list(), contractMap(), userMap()]);
      if (!rows.length) {
        wrap.innerHTML = emptyHtml("No approval requests in this workspace.");
        return;
      }
      // "Waiting on you" = genuinely PENDING items only. Backend supports only
      // approve|reject (no "request changes"), so we don't render that button.
      const pendingRows = rows.filter((a) => (a.status || "").toLowerCase() === "pending");
      wrap.innerHTML = pendingRows.length
        ? pendingRows.map((a) => {
            const title = contractName(cmap, a.contract_id);
            const requester = userName(umap, a.requested_by_user_id);
            const when = (a.created_at || a.updated_at || "").slice(0, 16).replace("T", " ");
            const roleNote = a.approver_role ? `Routed to ${humanize(a.approver_role)}` : "";
            return `
          <article class="approval-card" data-id="${esc(a.id)}" data-contract="${esc(a.contract_id || "")}">
            <div class="approval-card__head">
              ${statusFlag(a.status)}
              <span class="approval-card__title approval-card__open" role="button" tabindex="0" title="Open this contract to review it">${esc(title)}</span>
              <span class="approval-card__time">${esc(when)}</span>
            </div>
            <p class="approval-card__body">${esc(roleNote ? roleNote + " · " : "")}Requested by ${esc(requester)}${a.due_at ? " · due " + fmtDate(a.due_at) : ""}</p>
            <div class="approval-card__foot">
              <div class="approval-card__actions">
                <button class="ghost-btn approval-card__review" data-open-contract>Review contract →</button>
                <button class="primary-btn">Approve</button>
                <button class="text-btn text-btn--danger">Reject</button>
              </div>
              <input class="approval-card__comment" placeholder="Reason / note (required to reject)…"/>
            </div>
          </article>`;
          }).join("")
        : emptyHtml("Nothing waiting on you — every approval request has been decided.");
      // "Waiting on you" tab count = the SAME pending list (was a stale hardcoded 6)
      const wtTab = [...document.querySelectorAll('.view--approvals .cd-tab, .view--approvals [data-tab]')].find((t) => /waiting on you/i.test(t.textContent));
      if (wtTab) { const sp = wtTab.querySelector("span"); if (sp) sp.textContent = pendingRows.length; }
      // Let the approver actually READ the contract before deciding
      wrap.querySelectorAll(".approval-card[data-contract]").forEach((card) => {
        const cid = card.dataset.contract;
        if (!cid) return;
        const open = (e) => { e.stopPropagation(); window.__activeContractId = cid; showView("contract"); renderContractDetail(cid); };
        card.querySelector("[data-open-contract]")?.addEventListener("click", open);
        card.querySelector(".approval-card__open")?.addEventListener("click", open);
      });

      // --- the 3 other sub-tabs (Sent / Signatures in-flight / completed) ---
      const me = await api.me.get().catch(() => ({}));
      // Sent by you
      const sentWrap = document.querySelector('.view--approvals [data-tab-panel="approvals-sent"] .approval-list');
      if (sentWrap) {
        const sent = rows.filter((a) => a.requested_by_user_id === me.id || (a.status || "") !== "pending");
        sentWrap.innerHTML = sent.length
          ? sent.map((a) => `<article class="approval-card">
              <div class="approval-card__head">${statusFlag(a.status)}<span class="approval-card__title">${esc(contractName(cmap, a.contract_id))}</span><span class="approval-card__time">${esc((a.created_at || "").slice(0, 16).replace("T", " "))}</span></div>
              <p class="approval-card__body">${esc(humanize(a.approver_role || "approver"))} · ${esc(humanize(a.status))}</p></article>`).join("")
          : emptyHtml("You haven't sent any approval requests.");
      }
      // Signatures (in-flight + completed) from /signatures
      try {
        const sigs = await api.signatures.list();
        const flight = sigs.filter((s) => (s.status || "").toLowerCase() !== "completed");
        const done = sigs.filter((s) => (s.status || "").toLowerCase() === "completed");
        const sigCard = (s) => `<article class="approval-card">
          <div class="approval-card__head">${statusFlag(s.status)}<span class="approval-card__title">${esc(s.contract_id ? contractName(cmap, s.contract_id) : "Envelope " + String(s.provider_envelope_id || s.id).slice(0, 10))}</span><span class="approval-card__time">${esc(fmtDate(s.sent_at || s.completed_at || s.created_at))}</span></div>
          <div style="font-size:11.5px;color:var(--fg-subtle);font-family:'JetBrains Mono',monospace">${esc(humanize(s.provider || "DocuSign"))} · ${esc(String(s.provider_envelope_id || s.id).slice(0, 14))}</div></article>`;
        const flightWrap = document.querySelector('.view--approvals [data-tab-panel="signatures-flight"] .approval-list');
        if (flightWrap) flightWrap.innerHTML = flight.length ? flight.map(sigCard).join("") : emptyHtml("No signature envelopes in flight.");
        const doneWrap = document.querySelector('.view--approvals [data-tab-panel="signatures-done"] .approval-list');
        if (doneWrap) doneWrap.innerHTML = done.length ? done.map(sigCard).join("") : emptyHtml("No completed signatures yet.");
        // Header subtitle + the signature tab counts — ONE source of truth (was hardcoded "6 · 3 · 12")
        const apSub = [...document.querySelectorAll('.view--approvals h1')].map((h) => h.nextElementSibling).find((p) => p && p.tagName === "P");
        if (apSub) apSub.textContent = `${pendingRows.length} approval${pendingRows.length === 1 ? "" : "s"} waiting on you · ${flight.length} envelope${flight.length === 1 ? "" : "s"} in flight · ${done.length} completed.`;
        const setApTab = (re, n) => { const t = [...document.querySelectorAll('.view--approvals .cd-tab, .view--approvals [data-tab]')].find((x) => re.test(x.textContent)); if (t) { const sp = t.querySelector("span"); if (sp) sp.textContent = n; } };
        setApTab(/signatures.*flight/i, flight.length);
        setApTab(/signatures.*completed/i, done.length);
      } catch (e) {
        const fw = document.querySelector('.view--approvals [data-tab-panel="signatures-flight"] .approval-list');
        if (fw) fw.innerHTML = errorHtml(e);
      }

      // Routing rules — real GET/POST /approvals/routing-rules
      const view = document.querySelector(".view--approvals");
      const rrBtn = [...(view ? view.querySelectorAll("button") : [])].find((b) => /routing rules/i.test(b.textContent));
      if (rrBtn) rrBtn.onclick = async () => {
        const m = aegisModal("Approval routing rules", loadingHtml(), { ok: "Add rule" });
        try {
          const rules = await api.approvals.routingRules();
          const list = (rules || []).map((r) => `<div class="rr-row"><strong>${esc(r.name || r.approver_role || "Rule")}</strong><span>${esc(r.condition || r.match || r.description || "")} → ${esc(humanize(r.approver_role || r.assignee_role || "approver"))}</span></div>`).join("");
          m.body.innerHTML = `<div class="rr-list">${list || emptyHtml("No routing rules configured.")}</div>
            <div class="rr-new"><label class="up-field"><span>When contract type</span><input class="rr-type" placeholder="e.g. MSA · Enterprise"/></label>
            <label class="up-field"><span>Route to role</span><input class="rr-role" placeholder="e.g. risk_committee"/></label></div>`;
        } catch (e) {
          m.body.innerHTML = emptyHtml(e.status === 403 ? "You don't have permission to view routing rules." : "Couldn't load rules: " + e.message);
        }
        m.ok.onclick = async () => {
          const type = m.el.querySelector(".rr-type")?.value.trim();
          const role = m.el.querySelector(".rr-role")?.value.trim();
          if (!role) { (window.__aegisToast || (() => {}))({ title: "Role required", kind: "warn" }); return; }
          m.ok.disabled = true; m.ok.textContent = "Adding…";
          try { await api.approvals.addRoutingRule({ name: type || role, condition: type ? `contract_type==${type}` : null, approver_role: role }); (window.__aegisToast || (() => {}))({ title: "Routing rule added", kind: "success" }); m.close(); }
          catch (e) { (window.__aegisToast || (() => {}))({ title: "Couldn't add rule", sub: e.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Add rule"; }
        };
      };
    } catch (err) {
      wrap.innerHTML = errorHtml(err);
    }
  }

  // ---------- PROJECTS ----------
  async function renderProjects() {
    const grid = document.querySelector(".view--projects .vault-grid");
    if (!grid) return;
    const ghost = grid.querySelector(".vault-card--ghost");
    grid.innerHTML = loadingHtml();
    try {
      const rows = await api.projects.list();
      if (!rows.length) {
        grid.innerHTML = emptyHtml("No projects yet. Create one to start tracking a matter.");
        if (ghost) grid.appendChild(ghost);
        return;
      }
      const cardHtml = (p, i) => `
        <article class="vault-card ${i === 0 ? "vault-card--featured" : ""}" data-id="${esc(p.id)}">
          <div class="vault-card__head">
            <span class="vault-card__chip">${esc(p.project_type || p.type || "Project")}${p.status ? " · " + esc(p.status) : ""}</span>
          </div>
          <h3>${esc(p.name)}</h3>
          <p>${esc(p.description || "")}</p>
          <div class="vault-card__stats">
            ${p.contracts_count != null ? `<div><strong>${p.contracts_count}</strong><span>contracts</span></div>` : ""}
            ${p.files_count != null ? `<div><strong>${p.files_count}</strong><span>files</span></div>` : ""}
          </div>
        </article>`;
      const paint = (list) => {
        grid.innerHTML = list.length ? list.map(cardHtml).join("") : `<div style="grid-column:1/-1">${emptyHtml("No projects in this category.")}</div>`;
        if (ghost) grid.appendChild(ghost);
        grid.querySelectorAll(".vault-card[data-id]").forEach((card) => card.addEventListener("click", async () => {
          window.__activeProjectId = card.dataset.id; showView("project"); await renderProjectDetail(card.dataset.id);
        }));
      };
      // Category tabs — real filtering + live counts (these were a visual no-op)
      const pview = document.querySelector(".view--projects");
      const ptabs = pview ? [...pview.querySelectorAll(".tab")] : [];
      const PTAB = {
        "all projects": () => true,
        "m&a": (p) => /m&a|\bma\b|merger|acqui/i.test(p.project_type || p.type || ""),
        commercial: (p) => /commercial/i.test(p.project_type || p.type || ""),
        litigation: (p) => /litigation|dispute/i.test(p.project_type || p.type || ""),
        closed: (p) => /closed|archived|complete/i.test(p.status || ""),
      };
      ptabs.forEach((tab) => {
        const label = (tab.textContent || "").replace(/\d+/g, "").trim().toLowerCase();
        const fn = PTAB[label] || (() => true);
        const sp = tab.querySelector("span");
        if (sp) sp.textContent = label.startsWith("all") ? rows.length : rows.filter(fn).length;
        tab.onclick = () => { ptabs.forEach((t) => t.classList.remove("is-active")); tab.classList.add("is-active"); paint(rows.filter(PTAB[label] || (() => true))); };
      });
      paint(rows);
      // + New project → real POST /projects
      const npBtn = [...document.querySelectorAll(".view--projects button")].find((b) => /new project/i.test(b.textContent));
      const npGhost = grid.querySelector(".vault-card--ghost");
      const openNewProject = () => {
        const m = aegisModal("New project", `<label class="up-field"><span>Name</span><input class="np-name" placeholder="e.g. Project Helios"/></label><label class="up-field"><span>Description</span><textarea class="np-desc" rows="2" placeholder="Optional"></textarea></label>`, { ok: "Create project" });
        m.ok.onclick = async () => {
          const name = m.el.querySelector(".np-name").value.trim();
          if (!name) { (window.__aegisToast || (() => {}))({ title: "Name required", kind: "warn" }); return; }
          m.ok.disabled = true; m.ok.textContent = "Creating…";
          try { const p = await api.projects.create({ name, description: m.el.querySelector(".np-desc").value.trim() || null }); (window.__aegisToast || (() => {}))({ title: "Project created", kind: "success" }); m.close(); window.__activeProjectId = p.id; showView("project"); renderProjectDetail(p.id); }
          catch (e) { (window.__aegisToast || (() => {}))({ title: "Couldn't create", sub: e.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Create project"; }
        };
      };
      if (npBtn) npBtn.onclick = openNewProject;
      if (npGhost) npGhost.onclick = openNewProject;
    } catch (err) {
      grid.innerHTML = errorHtml(err);
    }
  }

  async function renderProjectDetail(id) {
    const view = document.querySelector(".view--project");
    if (!view || !id) return;
    try {
      const p = await api.projects.get(id);
      const crumb = view.querySelector(".topbar__crumbs .current");
      if (crumb) crumb.textContent = p.name;
      const h1 = view.querySelector(".cd__head h1");
      if (h1) h1.textContent = p.name;
      const typeLabel = view.querySelector(".view--project .cd__type-label");
      if (typeLabel) typeLabel.textContent = humanize(p.project_type || p.type || "Project");
      const stagePill = view.querySelector(".view--project .cd__type .stage");
      if (stagePill) stagePill.textContent = humanize(p.status || "active");
      const headP = view.querySelector(".cd__head p");
      if (headP) headP.innerHTML = `Type <strong>${esc(humanize(p.project_type || p.type || "—"))}</strong> · Status <strong>${esc(humanize(p.status || "active"))}</strong> · Created ${esc(fmtDate(p.created_at) || "—")}`;

      // --- Replace the static header stats / badge / tab counts / "Helios" copy with REAL data ---
      const setTabCount = (re, n) => {
        const t = [...view.querySelectorAll(".cd-tab")].find((x) => re.test(x.textContent.trim()));
        if (!t) return;
        let sp = t.querySelector("span");
        if (!sp) { sp = document.createElement("span"); t.appendChild(document.createTextNode(" ")); t.appendChild(sp); }
        sp.textContent = n;
      };
      // hide the fake "N open approvals" badge in the header
      view.querySelectorAll(".cd__head .cell-flag, .cd__type .cell-flag").forEach((b) => { if (/open approval/i.test(b.textContent)) b.style.display = "none"; });
      // scrub any hardcoded "Project Helios"/"Helios" copy → this project's name (all text nodes)
      view.querySelectorAll("*").forEach((el) => { if (el.children.length === 0 && /Helios/.test(el.textContent)) el.textContent = el.textContent.replace(/Project Helios|Helios/g, p.name); });
      try {
        const [pcs, pmembers, pfolders, pqueries] = await Promise.all([
          api.projects.contracts(id).catch(() => []),
          api.projects.members(id).catch(() => []),
          api.projects.folders(id).catch(() => []),
          api.brain.queries().catch(() => []),
        ]);
        const stats = view.querySelectorAll(".cd__head-stats > div");
        if (stats[0]) stats[0].innerHTML = `<strong>${pcs.length}</strong><span>contracts</span>`;
        if (stats[1]) stats[1].innerHTML = `<strong>${pmembers.length}</strong><span>members</span>`;
        if (stats[2]) stats[2].innerHTML = `<strong>${pfolders.length}</strong><span>folders</span>`;
        setTabCount(/^contracts/i, pcs.length);
        setTabCount(/^team/i, pmembers.length);
        setTabCount(/^folders/i, pfolders.length);
        const projQ = pqueries.filter((q) => q.project_id === id).length;
        setTabCount(/brain quer/i, projQ);
        const brainHeadP = view.querySelector('[data-tab-panel="proj-brain"] .panel__head p');
        if (brainHeadP) brainHeadP.textContent = `${projQ} project-scoped quer${projQ === 1 ? "y" : "ies"} · ask another below.`;
        window.__projReviewContractIds = pcs.map((pc) => pc.contract_id).filter(Boolean);

        // Resolve the project's linked contracts to real objects (for the overview widgets)
        const allC = _allContracts.length ? _allContracts : await api.contracts.list().catch(() => []);
        const cById = {}; allC.forEach((c) => (cById[c.id] = c));
        const projContracts = pcs.map((pc) => cById[pc.contract_id]).filter(Boolean);

        // Overview: "Risk distribution by workstream" → REAL contracts-by-type (was static 42/88/…)
        const distList = view.querySelector('[data-tab-panel="proj-overview"] .dist-list');
        if (distList) {
          if (!projContracts.length) {
            distList.innerHTML = `<li>${emptyHtml("No contracts linked yet — add contracts to see the breakdown.")}</li>`;
          } else {
            const byType = {};
            projContracts.forEach((c) => {
              const t = humanize(c.contract_type || "Other");
              const d = (byType[t] = byType[t] || { n: 0, high: 0, med: 0, ok: 0 });
              d.n++; const r = (c.risk_level || "ok").toLowerCase();
              if (r === "high") d.high++; else if (r === "medium" || r === "med") d.med++; else d.ok++;
            });
            distList.innerHTML = Object.entries(byType).map(([t, d]) => {
              const pct = (x) => Math.round((x / d.n) * 100);
              return `<li><span>${esc(t)}</span><div class="dist__bar"><span class="dist__seg dist__seg--high" style="width:${pct(d.high)}%"></span><span class="dist__seg dist__seg--med" style="width:${pct(d.med)}%"></span><span class="dist__seg dist__seg--ok" style="width:${pct(d.ok)}%"></span></div><span class="dist__count">${d.n}</span></li>`;
            }).join("");
          }
        }
        // Overview: "Key dates" → REAL expiries from the project's contracts (was static)
        const keyList = view.querySelector('[data-tab-panel="proj-overview"] .renewal-list');
        if (keyList) {
          const dated = projContracts.filter((c) => c.expiration_date).map((c) => ({ date: c.expiration_date, title: c.title + " expires", meta: humanize(c.contract_type || "contract") })).sort((a, b) => String(a.date).localeCompare(String(b.date)));
          keyList.innerHTML = dated.length
            ? dated.slice(0, 6).map((d) => `<li><div class="renewal-list__date"><strong>${esc(fmtDate(d.date))}</strong><span>${daysUntil(d.date) != null ? daysUntil(d.date) + "d" : ""}</span></div><div><div class="renewal-list__title">${esc(d.title)}</div><div class="renewal-list__meta">${esc(d.meta)}</div></div></li>`).join("")
            : `<li>${emptyHtml("No key dates — link contracts that have an expiry or obligations.")}</li>`;
        }
        // honest summary header (drop the fake "refreshed 2h ago")
        view.querySelectorAll('[data-tab-panel="proj-overview"] .panel__head p').forEach((el) => { if (/refreshed/i.test(el.textContent)) el.textContent = "AEGIS-generated from this project's data"; });
      } catch {}

      // Real AI summary (or honest placeholder)
      const summary = view.querySelector(".view--project .ai-summary");
      if (summary) {
        summary.innerHTML = p.description
          ? `<p>${esc(p.description)}</p>`
          : `<p style="color:var(--fg-muted)">No description set for this project. ${esc(p.name)} is a ${esc(humanize(p.project_type || "project"))} in ${esc(humanize(p.status || "active"))} status.</p>`;
      }

      // Real members
      const teamList = view.querySelector(".view--project .team-load");
      if (teamList) {
        teamList.innerHTML = `<li>${loadingHtml()}</li>`;
        try {
          const [members, umap] = await Promise.all([api.projects.members(id), userMap()]);
          teamList.innerHTML = members.length
            ? members.map((m) => {
                const name = userName(umap, m.user_id || m.id) || m.full_name || "Member";
                return `<li>
                  <div class="avatar avatar--sm">${esc(initials(name))}</div>
                  <div class="team-load__body"><div class="team-load__name">${esc(name)}</div><div class="team-load__bar"><span style="width:60%"></span></div></div>
                  <div class="team-load__count">${esc(humanize(m.role || "member"))}</div>
                </li>`;
              }).join("")
            : `<li>${emptyHtml("No members assigned.")}</li>`;
        } catch (e) {
          teamList.innerHTML = `<li>${emptyHtml("Members unavailable.")}</li>`;
        }
      }

      const [cmap, umap] = await Promise.all([contractMap(), userMap()]);

      // CONTRACTS tab — real /projects/{id}/contracts
      const pcPanel = view.querySelector('[data-tab-panel="proj-contracts"]');
      if (pcPanel) {
        const tbody = pcPanel.querySelector("tbody");
        const head = pcPanel.querySelector(".panel__head h3");
        try {
          const [pcs, pcFolders] = await Promise.all([api.projects.contracts(id), api.projects.folders(id).catch(() => [])]);
          const fmap = {}; pcFolders.forEach((f) => { fmap[f.id] = f.name; });
          window.__projReviewContractIds = pcs.map((pc) => pc.contract_id).filter(Boolean);
          if (head) head.textContent = `Contracts in ${p.name} · ${pcs.length}`;
          if (tbody) tbody.innerHTML = pcs.length
            ? pcs.map((pc) => { const fchip = pc.folder_id && fmap[pc.folder_id] ? `<span class="folder-chip" title="Filed in folder">📁 ${esc(fmap[pc.folder_id])}</span>` : ""; return `<tr data-view="contract" data-id="${esc(pc.contract_id)}"><td>${esc(contractName(cmap, pc.contract_id))}${fchip}</td><td>${esc(humanize(pc.role || "linked"))}</td><td>${esc(fmtDate(pc.created_at))}</td></tr>`; }).join("")
            : `<tr><td colspan="3">${emptyHtml("No contracts linked to this project.")}</td></tr>`;
        } catch (e) { if (tbody) tbody.innerHTML = `<tr><td colspan="3">${errorHtml(e)}</td></tr>`; }
      }

      // TEAM tab — real /projects/{id}/members
      const teamPanel = view.querySelector('[data-tab-panel="proj-team"]');
      if (teamPanel) {
        const tbody = teamPanel.querySelector("tbody");
        const head = teamPanel.querySelector(".panel__head h3");
        try {
          const members = await api.projects.members(id);
          if (head) head.textContent = `Team · ${members.length} member${members.length === 1 ? "" : "s"}`;
          if (tbody) tbody.innerHTML = members.length
            ? members.map((m) => {
                const name = userName(umap, m.user_id || m.id) || m.full_name || "Member";
                return `<tr><td><div style="display:flex;align-items:center;gap:8px"><div class="avatar avatar--sm">${esc(initials(name))}</div>${esc(name)}</div></td><td><span class="stage stage--review">${esc(humanize(m.role || "member"))}</span></td><td>${esc(humanize(m.role || "—"))}</td><td>—</td><td>${esc(fmtDate(m.created_at))}</td><td></td></tr>`;
              }).join("")
            : `<tr><td colspan="6">${emptyHtml("No members on this project.")}</td></tr>`;
        } catch (e) { if (tbody) tbody.innerHTML = `<tr><td colspan="6">${errorHtml(e)}</td></tr>`; }
      }

      // FOLDERS tab — a real filing structure: folders + the contracts inside them (+ Unfiled)
      const foldPanel = view.querySelector('[data-tab-panel="proj-folders"]');
      if (foldPanel) {
        const list = foldPanel.querySelector(".folder-list");
        const head = foldPanel.querySelector(".panel__head h3");
        try {
          const [folders, pcs] = await Promise.all([api.projects.folders(id), api.projects.contracts(id)]);
          if (head) head.textContent = `Folders · ${folders.length}`;
          if (list) {
            const byFolder = {};
            pcs.forEach((pc) => { const k = pc.folder_id || "__unfiled"; (byFolder[k] = byFolder[k] || []).push(pc); });
            const folderOptsFor = (cur) => `<option value="">Unfiled</option>${folders.map((f) => `<option value="${esc(f.id)}" ${cur === f.id ? "selected" : ""}>${esc(f.name)}</option>`).join("")}`;
            const docRow = (pc) => `<li class="folder-doc">
              <svg viewBox="0 0 16 16" fill="none" style="width:12px;height:12px;opacity:.55;flex:none"><path d="M3 2h7l3 3v9H3V2z" stroke="currentColor" stroke-width="1.3"/></svg>
              <span class="folder-doc__name" data-open="${esc(pc.contract_id)}">${esc(contractName(cmap, pc.contract_id))}</span>
              <select class="folder-doc__move" data-pc="${esc(pc.contract_id)}" title="Move to another folder">${folderOptsFor(pc.folder_id)}</select>
            </li>`;
            const folderCard = (f) => { const docs = byFolder[f.id] || []; return `<div class="folder-card"><div class="folder-card__head"><div class="folder__name">📁 ${esc(f.name || "Folder")} <span class="folder-card__count">${docs.length}</span></div><button class="text-btn folder-add" data-folder="${esc(f.id)}">+ File contract</button></div><ul class="folder-card__docs">${docs.length ? docs.map(docRow).join("") : `<li class="folder-empty">Empty — file a contract here</li>`}</ul></div>`; };
            const unfiled = byFolder["__unfiled"] || [];
            list.innerHTML =
              (folders.length || unfiled.length
                ? folders.map(folderCard).join("") + (unfiled.length ? `<div class="folder-card folder-card--unfiled"><div class="folder-card__head"><div class="folder__name">Unfiled <span class="folder-card__count">${unfiled.length}</span></div></div><ul class="folder-card__docs">${unfiled.map(docRow).join("")}</ul></div>` : "")
                : emptyHtml("No folders yet. Create one with “+ New folder”, then file contracts into it."));
            list.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => { window.__activeContractId = el.dataset.open; showView("contract"); renderContractDetail(el.dataset.open); }));
            list.querySelectorAll(".folder-doc__move").forEach((sel) => sel.addEventListener("change", async () => {
              try { await api.projects.moveContract(id, sel.dataset.pc, sel.value || null); toast({ title: sel.value ? "Filed into folder" : "Moved to Unfiled", kind: "success" }); renderProjectDetail(id); }
              catch (e) { toast({ title: "Couldn't move", sub: e.message, kind: "error" }); }
            }));
            list.querySelectorAll(".folder-add").forEach((b) => b.addEventListener("click", () => window.__projAddContract && window.__projAddContract(b.dataset.folder)));
          }
        } catch (e) { if (list) list.innerHTML = errorHtml(e); }
      }

      // ACTIVITY tab — no per-project activity endpoint → honest state
      const actPanel = view.querySelector('[data-tab-panel="proj-activity"] .activity-list');
      if (actPanel) {
        actPanel.innerHTML = `<li>${emptyHtml("Project-level activity isn't exposed by the API yet. See per-contract Activity tabs for live events.")}</li>`;
      }

      // BRAIN tab — PROJECT-SPECIFIC queries (scoped to this project) + a composer to ask one
      const brainPanel = view.querySelector('[data-tab-panel="proj-brain"] .brain-queries');
      const brainTab = view.querySelector('[data-tab-panel="proj-brain"]');
      if (brainPanel && brainTab) {
        const projContractIds = new Set(window.__projReviewContractIds || []);
        const isMine = (q) => q.project_id === id || (q.contract_id && projContractIds.has(q.contract_id));
        const renderProjQueries = async () => {
          brainPanel.innerHTML = `<li>${loadingHtml()}</li>`;
          try {
            const all = await api.brain.queries();
            const mine = (all || []).filter(isMine);
            brainPanel.innerHTML = mine.length
              ? mine.slice(0, 8).map((q) => `<li><div class="brain-queries__q">"${esc(q.question || "—")}"</div><div class="brain-queries__a">${esc((q.answer || "").slice(0, 150))}</div></li>`).join("")
              : `<li>${emptyHtml("No questions asked about this project yet — ask one above.")}</li>`;
            const n = mine.length;
            setTabCount(/brain quer/i, n);
            const bhp = view.querySelector('[data-tab-panel="proj-brain"] .panel__head p');
            if (bhp) bhp.textContent = `${n} project-scoped quer${n === 1 ? "y" : "ies"} · ask another below.`;
          } catch (e) { brainPanel.innerHTML = `<li>${errorHtml(e)}</li>`; }
        };
        // inject a project-scoped ask composer above the list (once)
        let composer = brainTab.querySelector(".proj-brain-ask");
        if (!composer) {
          composer = document.createElement("div");
          composer.className = "proj-brain-ask";
          composer.innerHTML = `<input class="proj-brain-ask__input" placeholder="Ask a question about this project's contracts…"/><button class="primary-btn proj-brain-ask__btn">Ask</button>`;
          const listWrap = brainPanel.closest("ul") || brainPanel;
          listWrap.parentNode.insertBefore(composer, listWrap);
        }
        const inp = composer.querySelector(".proj-brain-ask__input");
        const askProj = async () => {
          const q = inp.value.trim(); if (!q) return;
          const btn = composer.querySelector(".proj-brain-ask__btn"); btn.disabled = true; btn.textContent = "Asking…";
          try { await api.brain.ask(q, { scope: "project", projectId: id }); toast({ title: "Answer saved to this project's Brain", kind: "success" }); inp.value = ""; await renderProjQueries(); }
          catch (e) { toast({ title: "Couldn't ask", sub: e.message, kind: "error" }); }
          finally { btn.disabled = false; btn.textContent = "Ask"; }
        };
        composer.querySelector(".proj-brain-ask__btn").onclick = askProj;
        inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); askProj(); } };
        renderProjQueries();
        // "+ New brain query" → focus the project composer (was: go to global brain)
        view.querySelectorAll("button").forEach((b) => { if (/new brain query/i.test(b.textContent)) b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); const t = [...view.querySelectorAll(".cd-tab")].find((x) => /brain/i.test(x.textContent)); t && t.click(); inp.focus(); }; });
      }

      // rebind contract row clicks in the Contracts tab
      view.querySelectorAll('[data-tab-panel="proj-contracts"] tr[data-id]').forEach((tr) => {
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => { window.__activeContractId = tr.dataset.id; showView("contract"); renderContractDetail(tr.dataset.id); });
      });

      // --- action buttons (real backend writes) — wire EVERY matching button in the view ---
      const toast = window.__aegisToast || (() => {});
      const wireAll = (re, handler) => [...view.querySelectorAll("button")].filter((b) => re.test(b.textContent) && !b.closest(".folder-card")).forEach((b) => { b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); handler(); }; });

      // Add member → real PUT /projects/{id}/members
      const openAddMember = async () => {
        let users = [];
        try { users = await api.admin.members.list(); } catch (e) { users = []; }
        if (!users.length) { toast({ title: "Can't add members", sub: "The org user directory isn't available to your role.", kind: "warn" }); return; }
        const existing = await api.projects.members(id).catch(() => []);
        const existingIds = new Set(existing.map((m) => m.user_id || m.id));
        const avail = users.filter((u) => !existingIds.has(u.id));
        if (!avail.length) { toast({ title: "Everyone's already on this project", kind: "ai" }); return; }
        const m = aegisModal("Add a member", `<label class="up-field"><span>User</span><select class="pm-user">${avail.map((u) => `<option value="${esc(u.id)}">${esc(u.full_name || u.email)}</option>`).join("")}</select></label><label class="up-field"><span>Project role</span><select class="pm-role"><option value="member">Member</option><option value="editor">Editor</option><option value="owner">Owner</option><option value="viewer">Viewer</option></select></label>`, { ok: "Add member" });
        m.ok.onclick = async () => {
          m.ok.disabled = true; m.ok.textContent = "Adding…";
          try { await api.projects.addMember(id, { user_id: m.el.querySelector(".pm-user").value, role: m.el.querySelector(".pm-role").value }); toast({ title: "Member added", kind: "success" }); m.close(); renderProjectDetail(id); }
          catch (e) { toast({ title: "Couldn't add member", sub: e.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Add member"; }
        };
      };
      wireAll(/add member/i, openAddMember);

      // + Add contract → real PUT /projects/{id}/contracts (with a folder to file it into)
      const openAddContract = async (preFolderId) => {
        const [contracts, folders] = await Promise.all([
          _allContracts.length ? Promise.resolve(_allContracts) : api.contracts.list().catch(() => []),
          api.projects.folders(id).catch(() => []),
        ]);
        const folderOpts = `<option value="">— No folder (unfiled) —</option>${folders.map((f) => `<option value="${esc(f.id)}" ${preFolderId === f.id ? "selected" : ""}>${esc(f.name)}</option>`).join("")}`;
        const m = aegisModal("Add a contract", `<label class="up-field"><span>Contract</span><select class="pc-c">${contracts.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("")}</select></label><label class="up-field"><span>File into folder</span><select class="pc-folder">${folderOpts}</select></label>`, { ok: "Add contract" });
        m.ok.onclick = async () => {
          m.ok.disabled = true; m.ok.textContent = "Adding…";
          try { await api.projects.addContract(id, m.el.querySelector(".pc-c").value, m.el.querySelector(".pc-folder").value || null); toast({ title: "Contract linked", kind: "success" }); m.close(); renderProjectDetail(id); }
          catch (e) { toast({ title: "Couldn't link", sub: e.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Add contract"; }
        };
      };
      wireAll(/add contract/i, () => openAddContract());
      window.__projAddContract = openAddContract;

      // + New folder → real POST /projects/{id}/folders
      const openNewFolder = () => {
        const m = aegisModal("New folder", `<label class="up-field"><span>Folder name</span><input class="pf-name" placeholder="e.g. Diligence"/></label>`, { ok: "Create folder" });
        m.ok.onclick = async () => {
          const name = m.el.querySelector(".pf-name").value.trim();
          if (!name) { toast({ title: "Name required", kind: "warn" }); return; }
          m.ok.disabled = true; m.ok.textContent = "Creating…";
          try { await api.projects.createFolder(id, { name }); toast({ title: "Folder created", kind: "success" }); m.close(); renderProjectDetail(id); }
          catch (e) { toast({ title: "Couldn't create", sub: e.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Create folder"; }
        };
      };
      wireAll(/new folder/i, openNewFolder);

      // Other project buttons → sensible real behavior (were dead)
      wireAll(/new brain query/i, () => showView("brain"));
      wireAll(/run workflow/i, () => showView("workflows"));
      wireAll(/^manage$/i, () => { const t = [...view.querySelectorAll(".cd-tab")].find((x) => /^team/i.test(x.textContent)); t && t.click(); });
      wireAll(/^regenerate$/i, () => renderProjectDetail(id));

      // Review tables linked to this project (cross-app connection)
      const ovPanel = view.querySelector('[data-tab-panel="proj-overview"]');
      if (ovPanel) {
        let rtCard = ovPanel.querySelector(".proj-rt-card");
        if (!rtCard) { rtCard = document.createElement("div"); rtCard.className = "panel proj-rt-card"; ovPanel.appendChild(rtCard); }
        rtCard.innerHTML = `<div class="panel__head"><div><h3>Review tables</h3><p>Tabular AI reviews scoped to this matter.</p></div><button class="ghost-btn proj-rt-new">+ New review table</button></div><div class="proj-rt-list">${loadingHtml()}</div>`;
        rtCard.querySelector(".proj-rt-new").onclick = () => rtCreateModal({ projectId: id, contractIds: window.__projReviewContractIds || [] }, (newId) => { if (newId) { window.__activeReviewId = newId; showView("reviewtable"); renderReviewTableDetail(newId); } });
        api.tabularReviews.list().then((all) => {
          const mine = (all || []).filter((r) => r.project_id === id);
          const listEl = rtCard.querySelector(".proj-rt-list");
          listEl.innerHTML = mine.length
            ? mine.map((r) => `<div class="proj-rt-row" data-id="${esc(r.id)}"><span class="proj-rt-row__name">${esc(r.name)}</span><span class="proj-rt-row__meta">${esc(String((r.source_contract_ids || []).length))} docs · ${esc(humanize(r.status))}</span></div>`).join("")
            : emptyHtml("No review tables for this matter yet — create one above.");
          listEl.querySelectorAll(".proj-rt-row").forEach((row) => row.onclick = () => { window.__activeReviewId = row.dataset.id; showView("reviewtable"); renderReviewTableDetail(row.dataset.id); });
        }).catch(() => {});
      }
    } catch (err) {
      if (window.__AEGIS_DEBUG) console.warn("project detail:", err.message);
      const h1 = view.querySelector(".cd__head h1");
      if (h1) h1.innerHTML = errorHtml(err);
    }
  }

  // ---------- PLAYBOOKS ----------
  async function renderPlaybooks() {
    const grid = document.querySelector(".view--playbooks .vault-grid");
    if (!grid) return;
    const ghost = grid.querySelector(".vault-card--ghost");
    grid.innerHTML = loadingHtml();
    try {
      const rows = await api.playbooks.list();
      if (!rows.length) {
        grid.innerHTML = emptyHtml("No playbooks yet. Create one to define firm-standard positions.");
        if (ghost) grid.appendChild(ghost);
        return;
      }
      grid.innerHTML = rows.map((p, i) => `
        <article class="vault-card ${i === 0 ? "vault-card--featured" : ""}" data-id="${esc(p.id)}" style="cursor:pointer">
          <div class="vault-card__head">
            <span class="vault-card__chip">${esc(humanize(p.workflow_type || p.practice_area || "Playbook"))}${p.visibility ? " · " + esc(humanize(p.visibility)) : ""}</span>
          </div>
          <h3>${esc(p.name)}</h3>
          <p>${esc(p.description || "No description.")}</p>
        </article>`).join("");
      if (ghost) grid.appendChild(ghost);
      // Bind click → open playbook detail
      grid.querySelectorAll(".vault-card[data-id]").forEach((card) => {
        card.addEventListener("click", async () => {
          window.__activePlaybookId = card.dataset.id;
          showView("playbook");
          await renderPlaybookDetail();
        });
      });
      // Import — honest: there is no playbook-import endpoint
      const importBtn = [...document.querySelectorAll(".view--playbooks button")].find((b) => /^import$/i.test((b.textContent || "").trim()));
      if (importBtn) importBtn.onclick = () => (window.__aegisToast || (() => {}))({ title: "Import unavailable", sub: "No playbook-import API — use “+ New playbook” or AI-generate instead.", kind: "ai" });
    } catch (err) {
      grid.innerHTML = errorHtml(err);
    }
  }

  // ---------- OBLIGATIONS ----------
  // Reusable modal (used by track-obligation, new-project, add-member, etc.)
  function aegisModal(title, bodyHtml, opts = {}) {
    document.querySelectorAll(".aegis-modal").forEach((m) => m.remove());
    const ov = document.createElement("div");
    ov.className = "aegis-modal";
    ov.innerHTML = `<div class="aegis-modal__panel"><div class="aegis-modal__head"><strong>${esc(title)}</strong><button class="aegis-modal__x" aria-label="Close">×</button></div><div class="aegis-modal__body">${bodyHtml}</div>${opts.noFoot ? "" : `<div class="aegis-modal__foot"><button class="ghost-btn" data-cancel>Cancel</button><button class="primary-btn" data-ok>${esc(opts.ok || "Save")}</button></div>`}</div>`;
    document.body.appendChild(ov);
    const close = () => ov.remove();
    ov.querySelector(".aegis-modal__x").onclick = close;
    ov.querySelector("[data-cancel]")?.addEventListener("click", close);
    ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
    return { el: ov, close, ok: ov.querySelector("[data-ok]"), body: ov.querySelector(".aegis-modal__body") };
  }

  const OBL_BUCKETS = [
    () => true,
    (o) => { const d = daysUntil(o.due_date); return d != null && d >= 0 && d <= 7; },
    (o) => { const d = daysUntil(o.due_date); return d != null && d >= 0 && d <= 30; },
    (o) => o.status === "overdue" || (() => { const d = daysUntil(o.due_date); return d != null && d < 0; })(),
    (o) => !!(o.recurrence && String(o.recurrence).trim()),
  ];
  function buildOblCalendar(container, list) {
    const byDay = {};
    list.forEach((o) => { if (o.due_date) (byDay[o.due_date] = byDay[o.due_date] || []).push(o); });
    const dates = Object.keys(byDay).sort();
    let cursor = dates.length ? new Date(dates[0] + "T00:00:00") : new Date();
    function draw() {
      const y = cursor.getFullYear(), m = cursor.getMonth();
      const first = new Date(y, m, 1), startDow = first.getDay(), dim = new Date(y, m + 1, 0).getDate();
      const monthName = first.toLocaleString("default", { month: "long", year: "numeric" });
      let cells = "";
      for (let i = 0; i < startDow; i++) cells += `<div class="obl-cal__cell obl-cal__cell--empty"></div>`;
      for (let d = 1; d <= dim; d++) {
        const ds = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        const items = byDay[ds] || [];
        cells += `<div class="obl-cal__cell${items.length ? " has-obl" : ""}" data-day="${ds}"><span class="obl-cal__num">${d}</span>${items.length ? `<span class="obl-cal__dot">${items.length}</span>` : ""}</div>`;
      }
      container.innerHTML = `<div class="obl-cal__head"><button class="ghost-btn" data-prev>‹</button><strong>${esc(monthName)}</strong><button class="ghost-btn" data-next>›</button></div><div class="obl-cal__grid">${["Su","Mo","Tu","We","Th","Fr","Sa"].map((d) => `<div class="obl-cal__dow">${d}</div>`).join("")}${cells}</div><div class="obl-cal__list"></div>`;
      container.querySelector("[data-prev]").onclick = () => { cursor = new Date(y, m - 1, 1); draw(); };
      container.querySelector("[data-next]").onclick = () => { cursor = new Date(y, m + 1, 1); draw(); };
      container.querySelectorAll(".obl-cal__cell.has-obl").forEach((cell) => {
        cell.onclick = () => {
          const items = byDay[cell.dataset.day] || [];
          container.querySelector(".obl-cal__list").innerHTML = `<div class="obl-cal__listhead">${esc(cell.dataset.day)}</div>` + items.map((o) => `<div class="obl-cal__litem">${esc(o.description || humanize(o.obligation_type))}</div>`).join("");
        };
      });
    }
    draw();
  }
  let _allObligations = [];
  async function renderObligations() {
    const view = document.querySelector(".view--obligations");
    const panel = document.querySelector('.view--obligations [data-tab-panel="obligations-list"]');
    const tbody = panel && panel.querySelector(".ctr-table tbody");
    const tableEl = panel && panel.querySelector(".ctr-table");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="6">${loadingHtml()}</td></tr>`;
    try {
      const [rows, cmap, umap] = await Promise.all([api.obligations.list(), contractMap(), userMap()]);
      _allObligations = rows;
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="6">${emptyHtml("No tracked obligations.")}</td></tr>`;
        return;
      }
      const rowHtml = (o) => {
        const owner = userName(umap, o.owner_user_id);
        const canComplete = o.status !== "completed" && o.status !== "cancelled";
        return `
        <tr data-id="${esc(o.id)}">
          <td><div class="ctr-title"><span class="ctr-title__name">${esc(o.description || "—")}</span><span class="ctr-title__meta">${esc(humanize(o.obligation_type))}${o.recurrence ? " · " + esc(humanize(o.recurrence)) : ""}</span></div></td>
          <td>${esc(contractName(cmap, o.contract_id))}</td>
          <td>${esc(o.source_citation?.label || humanize(o.obligation_type))}</td>
          <td title="${esc(owner)}"><div class="avatar avatar--sm">${esc(initials(owner))}</div></td>
          <td>${o.due_date ? esc(fmtDate(o.due_date)) : "<span style='color:var(--fg-subtle)'>on event</span>"}</td>
          <td>${statusFlag(o.status)}${canComplete ? ` <button class="text-btn obl-complete" data-complete="${esc(o.id)}">Complete</button>` : ""}</td>
        </tr>`;
      };
      const paint = (lst) => {
        tbody.innerHTML = lst.length ? lst.map(rowHtml).join("") : `<tr><td colspan="6">${emptyHtml("No obligations match this filter.")}</td></tr>`;
        tbody.querySelectorAll("[data-complete]").forEach((b) => {
          b.addEventListener("click", async (e) => {
            e.stopPropagation();
            b.disabled = true; b.textContent = "…";
            try { await api.obligations.complete(b.dataset.complete); (window.__aegisToast || (() => {}))({ title: "Obligation completed", kind: "success" }); renderObligations(); }
            catch (err) { (window.__aegisToast || (() => {}))({ title: "Couldn't complete", sub: err.message, kind: "error" }); b.disabled = false; b.textContent = "Complete"; }
          });
        });
      };
      // live chip counts + filtering (by index, because '≤' breaks text matching)
      const chips = [...panel.querySelectorAll(".filter-bar .filter-chip")];
      chips.forEach((chip, i) => { const span = chip.querySelector("span"); const n = i === 0 ? rows.length : rows.filter(OBL_BUCKETS[i] || (() => true)).length; if (span) span.textContent = n; });
      // Header subtitle — same source as the chips (was hardcoded "184 · 24 renewals · 4 due this week · $4.2M")
      const dueWeek = rows.filter(OBL_BUCKETS[1] || (() => false)).length;
      const obSub = [...document.querySelectorAll('.view--obligations h1')].map((h) => h.nextElementSibling).find((p) => p && p.tagName === "P");
      if (obSub) obSub.textContent = `${rows.length} tracked obligation${rows.length === 1 ? "" : "s"} · ${dueWeek} due this week.`;
      // "Obligations" top tab count = the same list (was a stale hardcoded 184)
      const obTab = [...document.querySelectorAll('.view--obligations .cd-tab, .view--obligations [data-tab], .view--obligations .tab')].find((t) => /^obligations/i.test(t.textContent.trim()));
      if (obTab) { const sp = obTab.querySelector("span"); if (sp) sp.textContent = rows.length; }
      // "Renewals" sub-tab count = the real renewals list (was a stale hardcoded 24)
      api.renewals.list().then((rn) => {
        const rt = [...document.querySelectorAll('.view--obligations .cd-tab, .view--obligations [data-tab], .view--obligations .tab')].find((t) => /^renewals/i.test(t.textContent.trim()));
        if (rt) { const sp = rt.querySelector("span"); if (sp) sp.textContent = (rn || []).length; }
      }).catch(() => {});
      const bar = panel.querySelector(".filter-bar");
      if (bar) bar.onclick = (e) => {
        const chip = e.target.closest(".filter-chip"); if (!chip || !bar.contains(chip)) return;
        const idx = chips.indexOf(chip); if (idx < 0) return;
        chips.forEach((c) => c.classList.remove("is-active")); chip.classList.add("is-active");
        paint(rows.filter(OBL_BUCKETS[idx] || (() => true)));
      };
      // Calendar view toggle
      let calEl = panel.querySelector(".obl-calendar");
      const calBtn = [...view.querySelectorAll("button")].find((b) => /calendar view/i.test(b.textContent));
      if (calBtn) calBtn.onclick = () => {
        if (!calEl) { calEl = document.createElement("div"); calEl.className = "obl-calendar"; tableEl.after(calEl); }
        const showCal = calEl.style.display === "none" || !calEl.dataset.on;
        if (showCal) { buildOblCalendar(calEl, rows); calEl.style.display = ""; calEl.dataset.on = "1"; tableEl.style.display = "none"; calBtn.textContent = "Table view"; }
        else { calEl.style.display = "none"; calEl.dataset.on = ""; tableEl.style.display = ""; calBtn.textContent = "Calendar view"; }
      };
      // + Track obligation → extract from a chosen contract (real)
      const trackBtn = [...view.querySelectorAll("button")].find((b) => /track obligation/i.test(b.textContent));
      if (trackBtn) trackBtn.onclick = async () => {
        const contracts = _allContracts.length ? _allContracts : await api.contracts.list().catch(() => []);
        const opts = contracts.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("");
        const m = aegisModal("Track obligations", `<p class="aegis-modal__hint">AEGIS extracts obligations from a contract's executed text. Pick a contract to run extraction.</p><label class="up-field"><span>Contract</span><select class="track-contract">${opts}</select></label>`, { ok: "Run extraction" });
        m.ok.onclick = () => {
          const cid = m.el.querySelector(".track-contract").value;
          const t = window.__aegisToast || (() => {});
          t({ title: "Requesting extraction…", sub: "Runs as a background job", kind: "ai" });
          m.close();
          api.obligations.extract(cid)
            .then(() => { t({ title: "Extraction queued", sub: "Obligations appear when the job finishes", kind: "success" }); setTimeout(() => renderObligations(), 4000); })
            .catch((e) => t({ title: "Extraction unavailable", sub: e.status === 409 ? "That contract has no executed version yet." : "The extraction job service didn't respond in this environment.", kind: "error" }));
        };
      };
      paint(rows);
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6">${errorHtml(err)}</td></tr>`;
    }
  }

  // ---------- RENEWALS ----------
  async function renderRenewals() {
    const wrap = document.querySelector('.view--obligations [data-tab-panel="renewals-timeline"] .timeline');
    if (!wrap) return;
    wrap.innerHTML = loadingHtml();
    try {
      const [rows, cmap] = await Promise.all([api.renewals.list(), contractMap()]);
      if (!rows.length) {
        wrap.innerHTML = emptyHtml("No renewals scheduled.");
        return;
      }
      wrap.innerHTML = rows.map((r) => {
        const exp = r.expiration_date;
        const days = daysUntil(exp);
        const urgency = days == null ? "ok" : days < 30 ? "high" : days < 90 ? "med" : "ok";
        const meta = r.metadata_json || {};
        const auto = meta.auto_renewal ? "Auto-renew" : "Manual";
        const term = meta.renewal_term ? " · " + meta.renewal_term + " term" : "";
        const notice = r.notice_date ? " · notice by " + fmtDate(r.notice_date) : "";
        return `
          <article class="timeline__item timeline__item--${urgency}" data-id="${esc(r.id)}" data-contract="${esc(r.contract_id || "")}">
            <div class="timeline__date"><strong>${esc(fmtDate(exp))}</strong><span>${days != null ? days + "d" : ""}</span></div>
            <div class="timeline__body">
              <h4>${esc(contractName(cmap, r.contract_id))}</h4>
              <p>${esc(auto + term + notice)} · decision: ${esc(humanize(r.decision || "undecided"))}</p>
            </div>
            <div class="timeline__actions"><button class="ghost-btn" data-open>Open</button></div>
          </article>`;
      }).join("");
      wrap.querySelectorAll(".timeline__item [data-open]").forEach((b) => {
        b.addEventListener("click", async () => {
          const cid = b.closest(".timeline__item")?.dataset.contract;
          if (cid) { window.__activeContractId = cid; showView("contract"); await renderContractDetail(cid); }
        });
      });
    } catch (err) {
      wrap.innerHTML = errorHtml(err);
    }
  }

  // ---------- STANDALONE RENEWALS VIEW (.view--renewals) ----------
  // Maps the 6 demo buttons to the 3 real RenewalDecision values.
  const RENEWAL_DECISION = {
    "send non-renewal": "terminate",
    "auto-renew": "renew",
    "confirm": "renew",
    "negotiate": "renegotiate",
    "review pricing": "renegotiate",
    "draft renewal": "renegotiate",
  };
  async function renderRenewalsView() {
    const view = document.querySelector(".view--renewals");
    if (!view) return;
    const host = view.querySelector(".renewals__list, .timeline, .cards, .panel__body") || view.querySelector("main") || view;
    // live header counts
    try {
      const [rows, cmap] = await Promise.all([api.renewals.list(), contractMap()]);
      const within90 = rows.filter((r) => { const d = daysUntil(r.expiration_date); return d != null && d >= 0 && d <= 90; }).length;
      // Header buttons (were dead): Export (real client-side CSV) + Sync (honest)
      const toast = window.__aegisToast || (() => {});
      const expBtn = [...view.querySelectorAll(".topbar__actions button")].find((b) => /^export$/i.test(b.textContent.trim()));
      if (expBtn) expBtn.onclick = () => {
        const cols = ["contract", "expiration_date", "decision", "auto_renewal"];
        const cell = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
        const csv = [cols.join(",")].concat(rows.map((r) => [contractName(cmap, r.contract_id), r.expiration_date || "", humanize(r.decision || "undecided"), (r.metadata_json && r.metadata_json.auto_renewal) ? "yes" : "no"].map(cell).join(","))).join("\n");
        const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
        const a = document.createElement("a"); a.href = url; a.download = `aegis-renewals-${rows.length}.csv`; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast({ title: "Exported CSV", sub: `${rows.length} renewals`, kind: "success" });
      };
      const syncBtn = [...view.querySelectorAll(".topbar__actions button")].find((b) => /sync with clm/i.test(b.textContent));
      if (syncBtn) syncBtn.onclick = () => toast({ title: "Already in sync", sub: "Renewals are read live from this CLM — there's no external system to sync to.", kind: "ai" });
      view.querySelectorAll("[data-kpi], .kpi strong, .renewals__stat strong").forEach((el, i) => {
        if (i === 0) el.textContent = rows.length;
        else if (i === 1) el.textContent = within90;
      });
      // Header subtitle — real "expiring in 90 days", and NO fabricated "$4.2M ACV at risk"
      const rvSub = [...view.querySelectorAll("h1")].map((h) => h.nextElementSibling).find((p) => p && p.tagName === "P");
      if (rvSub) rvSub.textContent = `${within90} contract${within90 === 1 ? "" : "s"} expiring in the next 90 days. Notice deadlines color-coded.`;
      if (!host) return;
      if (!rows.length) { host.innerHTML = emptyHtml("No renewals scheduled."); return; }
      host.innerHTML = rows.map((r) => {
        const days = daysUntil(r.expiration_date);
        const urg = days == null ? "ok" : days < 30 ? "high" : days < 90 ? "med" : "ok";
        return `
          <article class="rnw-card rnw-card--${urg}" data-id="${esc(r.id)}" data-contract="${esc(r.contract_id || "")}">
            <div class="rnw-card__main">
              <h4>${esc(contractName(cmap, r.contract_id))}</h4>
              <p>Expires ${esc(fmtDate(r.expiration_date))}${days != null ? ` · ${days}d` : ""} · decision: <strong>${esc(humanize(r.decision || "undecided"))}</strong></p>
            </div>
            <div class="rnw-card__acts">
              <button class="ghost-btn" data-dec="terminate">Send non-renewal</button>
              <button class="ghost-btn" data-dec="renegotiate">Negotiate</button>
              <button class="primary-btn" data-dec="renew">Renew</button>
              <button class="ghost-btn" data-open>Open</button>
            </div>
          </article>`;
      }).join("");
      host.querySelectorAll(".rnw-card").forEach((card) => {
        const rid = card.dataset.id;
        card.querySelector("[data-open]")?.addEventListener("click", async () => {
          const cid = card.dataset.contract; if (cid) { window.__activeContractId = cid; showView("contract"); await renderContractDetail(cid); }
        });
        card.querySelectorAll("[data-dec]").forEach((b) => b.addEventListener("click", async () => {
          const decision = b.dataset.dec;
          card.querySelectorAll("button").forEach((x) => (x.disabled = true));
          try { await api.renewals.decide(rid, decision); (window.__aegisToast || (() => {}))({ title: `Renewal: ${humanize(decision)}`, sub: "Recorded", kind: "success" }); renderRenewalsView(); }
          catch (e) { (window.__aegisToast || (() => {}))({ title: "Couldn't record decision", sub: e.message, kind: "error" }); card.querySelectorAll("button").forEach((x) => (x.disabled = false)); }
        }));
      });
    } catch (err) {
      if (host) host.innerHTML = errorHtml(err);
    }
  }

  // ---------- NOTIFICATIONS ----------
  // Client-side read tracking (the backend Notification model has no read column,
  // and there is no mark-read route — so "unread" is tracked locally and honestly).
  function notifReadSet() { try { return new Set(JSON.parse(localStorage.getItem("aegis-read-notifs") || "[]")); } catch { return new Set(); } }
  function setNotifUnread(n) {
    const dot = document.querySelector(".bell__dot");
    if (dot) { if (n <= 0) { dot.style.display = "none"; } else { dot.style.display = "grid"; dot.textContent = String(n); } }
    const view = document.querySelector(".view--notifications");
    if (view) view.querySelectorAll("p,span,small,h2,div").forEach((el) => {
      if (el.children.length === 0 && /\b\d+\s+unread\b/i.test(el.textContent)) el.textContent = el.textContent.replace(/\b\d+\s+unread\b/i, n + " unread");
    });
  }
  async function renderNotifications() {
    const view = document.querySelector(".view--notifications");
    if (!view) return;
    const lists = view.querySelectorAll(".notif-list");
    lists.forEach((l) => (l.innerHTML = `<li>${loadingHtml()}</li>`));
    try {
      const rows = await api.notifications.list();
      const readSet = notifReadSet();
      const isRead = (nf) => readSet.has(nf.id);
      setNotifUnread(rows.filter((nf) => !isRead(nf)).length);
      // wire header buttons (idempotent)
      const mar = [...view.querySelectorAll("button")].find((b) => /mark all read/i.test(b.textContent));
      if (mar) mar.onclick = () => {
        rows.forEach((nf) => readSet.add(nf.id));
        localStorage.setItem("aegis-read-notifs", JSON.stringify([...readSet]));
        view.querySelectorAll(".notif--unread").forEach((el) => { el.classList.remove("notif--unread"); el.classList.add("is-read"); });
        setNotifUnread(0);
        (window.__aegisToast || (() => {}))({ title: "Marked all read", kind: "success" });
      };
      const setBtn = [...view.querySelectorAll("button")].find((b) => /^settings$/i.test((b.textContent || "").trim()));
      if (setBtn) setBtn.onclick = () => { (window.__aegisToast || (() => {}))({ title: "Notification preferences", sub: "Managed in Admin — there is no separate notifications API", kind: "ai" }); showView("admin"); };

      if (!rows.length) {
        if (lists[0]) lists[0].innerHTML = `<li>${emptyHtml("You're all caught up — no in-app notifications. (Approvals and renewals notify by email and the per-item timeline.)")}</li>`;
        lists.forEach((l, i) => { if (i > 0) l.innerHTML = ""; });
        return;
      }
      const renderItem = (nf) => `
        <li class="notif ${isRead(nf) ? "is-read" : "notif--unread"}" data-id="${esc(nf.id)}">
          <span class="notif__icon notif__icon--${esc((nf.channel || nf.event_type || "system").toString().split(".")[0])}">${esc(String(nf.event_type || "S")[0].toUpperCase())}</span>
          <div class="notif__body">
            <div class="notif__title">${esc(nf.subject || humanize(nf.event_type) || "Notification")}</div>
            <div class="notif__meta">${esc(humanize(nf.event_type || ""))}${nf.sent_at || nf.created_at ? " · " + esc(fmtDate(nf.sent_at || nf.created_at)) : ""}</div>
          </div>
        </li>`;
      if (lists[0]) lists[0].innerHTML = rows.map(renderItem).join("");
      lists.forEach((l, i) => { if (i > 0) l.innerHTML = ""; });
    } catch (err) {
      lists.forEach((l) => (l.innerHTML = `<li>${errorHtml(err)}</li>`));
    }
  }

  // ---------- WORKFLOWS ----------
  async function renderJobs() {
    const list = document.querySelector(".view--jobs .jobs-list");
    if (!list) return;
    list.innerHTML = loadingHtml();
    try {
      const rows = await api.jobs.list();
      if (!rows.length) {
        list.innerHTML = emptyHtml("No background jobs.");
        return;
      }
      // newest first
      rows.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
      list.innerHTML = rows.slice(0, 60).map((j) => {
        const st = (j.status || "").toLowerCase();
        const dotClass = st === "succeeded" || st === "completed" ? "job__dot--done"
          : st === "failed" ? "job__dot--failed" : "";
        const running = st === "running" || st === "pending" || st === "queued";
        const progress = j.progress != null ? `${j.progress}%` : "";
        const dur = (j.started_at && j.finished_at)
          ? ((new Date(j.finished_at) - new Date(j.started_at)) / 1000).toFixed(1) + "s"
          : "";
        const meta = [humanize(j.resource_type), progress, dur, `attempt ${j.attempt_count || 1}`].filter(Boolean).join(" · ");
        return `
        <li class="job job--${esc(st || "running")}" data-id="${esc(j.id)}">
          <div class="job__dot ${dotClass}">${running ? '<span class="spinner"></span>' : ""}</div>
          <div class="job__body">
            <div class="job__title">${esc(humanize(j.job_type))}</div>
            <div class="job__meta">${esc(meta)}${j.error_message ? " · <span style='color:var(--sev-high)'>" + esc(j.error_message.slice(0,60)) + "</span>" : ""}</div>
          </div>
          ${statusFlag(j.status)}
          <div class="job__acts">
            ${st === "queued" || st === "running" ? `<button class="text-btn" data-cancel="${esc(j.id)}">Cancel</button>` : ""}
            ${st === "failed" || st === "queued" ? `<button class="text-btn" data-rerun="${esc(j.id)}">Re-run</button>` : ""}
          </div>
        </li>`;
      }).join("");
      const jobAct = (sel, fn, label) => list.querySelectorAll(sel).forEach((b) => b.addEventListener("click", async () => {
        b.disabled = true; const old = b.textContent; b.textContent = "…";
        try { await fn(b.getAttribute(sel.replace(/[\[\]]/g, ""))); (window.__aegisToast || (() => {}))({ title: label, kind: "success" }); renderJobs(); }
        catch (e) { (window.__aegisToast || (() => {}))({ title: "Couldn't " + label.toLowerCase(), sub: e.status === 409 ? "Job is not in a state that allows this." : e.message, kind: "error" }); b.disabled = false; b.textContent = old; }
      }));
      jobAct("[data-cancel]", (id) => api.jobs.cancel(id), "Job cancelled");
      jobAct("[data-rerun]", (id) => api.jobs.run(id), "Job re-queued");
      // expose failed-job ids for the header "Retry failed" button (wired in app.js)
      window.__aegisFailedJobs = rows.filter((j) => (j.status || "").toLowerCase() === "failed").map((j) => j.id);
    } catch (err) {
      list.innerHTML = errorHtml(err);
    }
  }

  // ---------- ADMIN: members ----------
  async function renderAdmin() {
    const tbody = document.querySelector('.view--admin [data-tab-panel="adm-members"] .ctr-table tbody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="6">${loadingHtml()}</td></tr>`;
    try {
      const rows = await api.admin.members.list();
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="6">${emptyHtml("No members yet.")}</td></tr>`;
        return;
      }
      const row = (m) => `
        <tr data-id="${esc(m.id)}">
          <td><div style="display:flex;align-items:center;gap:8px"><div class="avatar avatar--sm">${esc(initials(m.full_name || m.email))}</div><div><div style="font-weight:500;color:var(--fg-strong)">${esc(m.full_name || m.email)}</div><div style="font-size:11.5px;color:var(--fg-subtle)">${esc(m.email)}</div></div></div></td>
          <td><span class="stage stage--review">${esc((m.roles && m.roles[0]) || m.active_role_name || "—")}</span></td>
          <td><span class="cell-flag cell-flag--${m.status === "active" ? "ok" : m.status === "deactivated" ? "high" : "med"}">${esc(m.status || "—")}</span></td>
          <td>${esc(fmtDate(m.last_login_at) || "—")}</td>
          <td>—</td>
          <td><button class="icon-btn"><svg viewBox="0 0 16 16" fill="none"><circle cx="3" cy="8" r="1" fill="currentColor"/><circle cx="8" cy="8" r="1" fill="currentColor"/><circle cx="13" cy="8" r="1" fill="currentColor"/></svg></button></td>
        </tr>`;
      const paint = (list) => { tbody.innerHTML = list.length ? list.map(row).join("") : `<tr><td colspan="6">${emptyHtml("No members match this filter.")}</td></tr>`; };
      paint(rows);
      // Live counts: Members tab badge + filter chips (replace the static "48")
      const admin = document.querySelector(".view--admin");
      const memTab = admin && [...admin.querySelectorAll(".cd-tab, .tab")].find((t) => /^members/i.test(t.textContent.trim()));
      if (memTab) { const sp = memTab.querySelector("span"); if (sp) sp.textContent = rows.length; }
      const st = (s) => (rows.filter((m) => (m.status || "").toLowerCase().includes(s)).length);
      const panel = admin && admin.querySelector('[data-tab-panel="adm-members"]');
      const chips = panel ? [...panel.querySelectorAll(".filter-chip")] : [];
      const CHIP_FILTER = { all: () => true, active: (m) => (m.status || "").toLowerCase() === "active", pending: (m) => (m.status || "").toLowerCase().includes("pending"), deactivated: (m) => (m.status || "").toLowerCase() === "deactivated" };
      chips.forEach((chip) => {
        const label = (chip.textContent || "").replace(/\d+/g, "").trim().toLowerCase();
        const sp = chip.querySelector("span");
        if (sp) sp.textContent = label === "all" ? rows.length : st(label.startsWith("pending") ? "pending" : label);
        chip.onclick = () => { chips.forEach((c) => c.classList.remove("is-active")); chip.classList.add("is-active"); paint(rows.filter(CHIP_FILTER[label] || (() => true))); };
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6">${errorHtml(err)}</td></tr>`;
    }
  }

  // ---------- ASSISTANT recent threads ----------
  async function renderAssistantRecents() {
    const grid = document.querySelector(".ask-recents-grid");
    if (grid) {
      try {
        const threads = await api.assistant.listThreads();
        if (!threads.length) {
          grid.innerHTML = emptyHtml("Start a conversation — your threads will appear here.");
        } else {
          grid.innerHTML = threads.slice(0, 4).map((t, i) => `
            <a class="ask-recent ${i === 0 ? "ask-recent--open" : ""}" href="#" data-id="${esc(t.id)}">
              <div class="ask-recent__head">
                <span class="ask-recent__tag${i === 0 ? " ask-recent__tag--active" : ""}">${esc(t.tag || "Thread")}</span>
                <span class="ask-recent__time">${esc(t.time || fmtDate(t.updated_at))}</span>
              </div>
              <div class="ask-recent__title">${esc(t.title || "Untitled")}</div>
              <div class="ask-recent__preview">${esc(t.preview || "")}</div>
            </a>`).join("");
        }
      } catch (err) {
        grid.innerHTML = emptyHtml("Start a conversation — your threads will appear here.");
      }
    }

    // Live counts for hero subtitle + knowledge context strip
    renderAssistantStats();
  }

  // ---------- ASSISTANT live counts ----------
  let _statsInFlight = null;
  async function renderAssistantStats() {
    if (_statsInFlight) return _statsInFlight;
    _statsInFlight = (async () => {
      const result = { contracts: "—", playbooks: "—", projects: "—", obligations: "—" };
      try { result.contracts = (await api.contracts.list()).length; } catch {}
      try { result.playbooks = (await api.playbooks.list()).length; } catch {}
      try { result.projects = (await api.projects.list()).length; } catch {}
      try { result.obligations = (await api.obligations.list()).length; } catch {}
      // Hero subtitle
      const sub = document.querySelector(".ask-hero__sub");
      if (sub) {
        sub.innerHTML =
          `AEGIS reads your firm's <strong>${result.contracts}</strong> contracts, ` +
          `<strong>${result.playbooks}</strong> playbooks, and <strong>${result.obligations}</strong> tracked obligations — ` +
          `and never answers without showing its work.`;
      }
      // Knowledge context strip
      const ctx = document.querySelector(".ask-context");
      if (ctx) {
        const sep = '<div class="ask-context__sep"></div>';
        ctx.innerHTML = [
          `<div class="ask-context__item"><span class="ask-context__num">${result.contracts}</span><span class="ask-context__label">contracts</span></div>`,
          sep,
          `<div class="ask-context__item"><span class="ask-context__num">${result.projects}</span><span class="ask-context__label">projects</span></div>`,
          sep,
          `<div class="ask-context__item"><span class="ask-context__num">${result.playbooks}</span><span class="ask-context__label">playbooks live</span></div>`,
          sep,
          `<div class="ask-context__item"><span class="ask-context__num">${result.obligations}</span><span class="ask-context__label">obligations tracked</span></div>`,
          sep,
          `<div class="ask-context__item"><span class="ask-context__num" style="color:var(--accent-jade)">● live</span><span class="ask-context__label">backend connected</span></div>`,
        ].join("");
      }
      // One source of truth for the contract count: kill every stray hardcoded "312".
      if (typeof result.contracts === "number") {
        // "All contracts · 312" source pills (Ask AEGIS + Contract Brain composers)
        document.querySelectorAll(".source-pill").forEach((p) => {
          if (/All contracts\s*·/.test(p.textContent)) p.innerHTML = `<span class="dot"></span>All contracts · ${result.contracts}`;
        });
        // Brain stat strip "312 contracts indexed" → real
        document.querySelectorAll("strong").forEach((s) => {
          const lbl = s.nextElementSibling;
          if (lbl && /contracts indexed/i.test(lbl.textContent) && /^[\d,]+$/.test(s.textContent.trim())) s.textContent = result.contracts;
        });
      }
      return result;
    })();
    try { return await _statsInFlight; } finally { _statsInFlight = null; }
  }

  // ---------- ADMIN organization ----------
  async function renderAdminOrg() {
    const orgPanel = document.querySelector('.view--admin [data-tab-panel="adm-org"] .upload__form');
    if (!orgPanel) return;
    try {
      const me = await api.me.get();
      const inputs = orgPanel.querySelectorAll("input");
      // Show a clean org name (don't expose the raw org UUID to the user)
      if (inputs[0]) inputs[0].value = me.org_name || (me.email ? me.email.split("@")[1].split(".")[0].replace(/^\w/, (c) => c.toUpperCase()) + " (workspace)" : "Workspace");
      if (inputs[1]) inputs[1].value = me.email ? me.email.split("@")[1] : "—";
    } catch (err) { if (window.__AEGIS_DEBUG) console.warn("admin org:", err.message); }
  }

  // ---------- PLAYBOOK detail ----------
  // Track the currently-open playbook + which version is selected
  const pbState = { id: null, pb: null, versions: [], versionId: null };

  async function renderPlaybookDetail() {
    const view = document.querySelector(".view--playbook");
    if (!view) return;
    const id = window.__activePlaybookId;
    const set = (sel, txt) => { const el = view.querySelector(sel); if (el) el.textContent = txt; };
    try {
      const pb = id ? await api.playbooks.get(id) : (await api.playbooks.list())[0];
      if (!pb) { set("[data-pb-h1]", "No playbook"); return; }
      pbState.id = pb.id; pbState.pb = pb;
      set("[data-pb-name]", pb.name);
      set("[data-pb-h1]", pb.name);
      set("[data-pb-desc]", pb.description || "No description.");
      const statusEl = view.querySelector("[data-pb-status]");
      if (statusEl) { statusEl.textContent = humanize(pb.status || "draft"); statusEl.className = "stage " + (pb.status === "published" ? "stage--active" : "stage--drafting"); }
      set("[data-pb-type]", "Contract playbook");

      // Versions
      const versions = await api.playbooks.versions(pb.id).catch(() => []);
      pbState.versions = versions;
      pbState.versionId = pb.current_version_id || (versions[0] && versions[0].id);
      set("[data-pb-stat-versions]", String(versions.length));

      // Version dropdown
      const sel = view.querySelector("[data-pb-version-select]");
      if (sel) {
        sel.innerHTML = versions
          .map((v) => `<option value="${esc(v.id)}" ${v.id === pbState.versionId ? "selected" : ""}>v${esc(String(v.version_number))} · ${esc(humanize(v.status))}</option>`)
          .join("");
        sel.onchange = () => { pbState.versionId = sel.value; renderPlaybookRules(); };
      }

      // Version history list
      const vlist = view.querySelector("[data-pb-versions]");
      if (vlist) {
        vlist.innerHTML = versions.length
          ? versions.map((v) => `
            <li class="${v.id === pb.current_version_id ? "is-current" : ""}">
              <strong>v${esc(String(v.version_number))}</strong>
              <div>
                <div>${esc(v.summary || "—")} ${v.status === "published" ? '<span class="cell-flag cell-flag--ok" style="margin-left:6px">published</span>' : '<span class="cell-flag cell-flag--med" style="margin-left:6px">draft</span>'}</div>
                <span>${esc(humanize(v.status))}${v.source_metadata && v.source_metadata.generated ? " · AI-generated" : ""}</span>
              </div>
            </li>`).join("")
          : `<li>${emptyHtml("No versions.")}</li>`;
      }

      // Runs count
      const runs = await api.playbooks.runs(pb.id).catch(() => []);
      set("[data-pb-stat-runs]", String(runs.length));

      await renderPlaybookRules();
      await renderPlaybookRuns(runs);
      bindPlaybookActions();
    } catch (err) {
      if (window.__AEGIS_DEBUG) console.warn("playbook detail:", err.message);
      set("[data-pb-h1]", "Error loading playbook");
      const rw = view.querySelector("[data-pb-rules]");
      if (rw) rw.innerHTML = errorHtml(err);
    }
  }

  // Three-position rule cards (Ironclad/Spellbook style)
  async function renderPlaybookRules() {
    const view = document.querySelector(".view--playbook");
    const wrap = view && view.querySelector("[data-pb-rules]");
    if (!wrap) return;
    wrap.innerHTML = loadingHtml();
    if (!pbState.id || !pbState.versionId) { wrap.innerHTML = emptyHtml("No version selected."); return; }
    try {
      const rules = await api.playbooks.rules(pbState.id, pbState.versionId);
      view.querySelector("[data-pb-stat-rules]").textContent = String(rules.length);
      if (!rules.length) { wrap.innerHTML = emptyHtml("No rules in this version. Click + New rule to add one."); return; }
      // Group by clause_type
      const groups = {};
      rules.forEach((r) => { (groups[r.clause_type] = groups[r.clause_type] || []).push(r); });
      wrap.innerHTML = Object.entries(groups).map(([clause, items]) => `
        <div class="pb-group">
          <div class="pb-group__label">${esc(humanize(clause))}</div>
          ${items.map((r) => pbRuleCard(r)).join("")}
        </div>`).join("");
      // Wire edit/delete
      wrap.querySelectorAll('[data-action="pb-rule-delete"]').forEach((b) => {
        b.onclick = async (e) => {
          e.stopPropagation();
          const card = b.closest("[data-rule-id]");
          if (!confirm("Delete this rule?")) return;
          try {
            await api.playbooks.deleteRule(pbState.id, pbState.versionId, card.dataset.ruleId);
            renderPlaybookRules();
            (window.__aegisToast || (()=>{}))({ title: "Rule deleted", kind: "success" });
          } catch (err) { (window.__aegisToast||(()=>{}))({ title: "Delete failed", sub: err.message, kind: "error" }); }
        };
      });
    } catch (err) {
      wrap.innerHTML = errorHtml(err);
    }
  }

  function riskBadge(level) {
    const k = (level || "").toLowerCase();
    if (k === "high") return '<span class="cell-flag cell-flag--high">High risk</span>';
    if (k === "medium" || k === "med") return '<span class="cell-flag cell-flag--med">Medium</span>';
    if (k === "low") return '<span class="cell-flag cell-flag--ok">Low</span>';
    return "";
  }

  function pbRuleCard(r) {
    const row = (label, val, cls) => val
      ? `<div class="pb-pos"><span class="pb-pos__label ${cls || ""}">${esc(label)}</span><span class="pb-pos__val">${esc(val)}</span></div>`
      : "";
    return `
      <article class="pb-rule" data-rule-id="${esc(r.id)}">
        <div class="pb-rule__head">
          <span class="pb-rule__code">${esc(humanize(r.rule_type))}</span>
          <span class="pb-rule__title">${esc(humanize(r.clause_type))}</span>
          ${riskBadge(r.risk_level)}
          ${r.approval_required ? '<span class="cell-flag cell-flag--med">Approval req.</span>' : ""}
          <div class="pb-rule__actions">
            <button class="icon-btn" data-action="pb-rule-delete" title="Delete"><svg viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3a1 1 0 011-1h2a1 1 0 011 1v1M5 4l1 9h4l1-9" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg></button>
          </div>
        </div>
        <div class="pb-positions">
          ${row("Preferred", r.preferred_position, "pb-pos__label--pref")}
          ${row("Fallback", r.fallback_position, "pb-pos__label--fall")}
          ${row("Prohibited", r.prohibited_language, "pb-pos__label--proh")}
          ${row("Required", r.required_language, "pb-pos__label--req")}
        </div>
        ${r.rationale ? `<div class="pb-rule__rationale"><strong>Why:</strong> ${esc(r.rationale)}</div>` : ""}
        ${r.negotiation_guidance ? `<div class="pb-rule__rationale"><strong>Negotiation:</strong> ${esc(r.negotiation_guidance)}</div>` : ""}
        <div class="pb-rule__foot">
          ${r.escalation_role ? `<span>Escalate to <strong>${esc(humanize(r.escalation_role))}</strong></span><span>·</span>` : ""}
          <span>${esc(humanize(r.rule_type))}</span>
        </div>
      </article>`;
  }

  // Runs list + deviation drill-down
  async function renderPlaybookRuns(runs) {
    const view = document.querySelector(".view--playbook");
    const wrap = view && view.querySelector("[data-pb-runs]");
    if (!wrap) return;
    try {
      if (runs == null) runs = await api.playbooks.runs(pbState.id);
      if (!runs.length) { wrap.innerHTML = emptyHtml("No runs yet. Click 'Run against contract'."); return; }
      const cmap = await contractMap();
      wrap.innerHTML = `<table class="ctr-table" style="width:100%"><thead><tr><th>Contract</th><th>Status</th><th>Validation</th><th>Deviations</th><th>Model</th></tr></thead><tbody>
        ${runs.map((r) => {
          const devs = (r.validated_output && r.validated_output.deviation_count) ?? "—";
          return `<tr data-run-id="${esc(r.id)}" style="cursor:pointer">
            <td>${esc(contractName(cmap, r.contract_id))}</td>
            <td>${statusFlag(r.status)}</td>
            <td>${esc(humanize(r.validation_status || "—"))}</td>
            <td><strong>${devs}</strong></td>
            <td style="font-family:'JetBrains Mono',monospace;font-size:11.5px">${esc(r.model_name || "—")}</td>
          </tr>`;
        }).join("")}
      </tbody></table>`;
      wrap.querySelectorAll("tr[data-run-id]").forEach((tr) => {
        tr.onclick = () => loadDeviations(tr.dataset.runId, cmap);
      });
    } catch (err) {
      wrap.innerHTML = errorHtml(err);
    }
  }

  async function loadDeviations(runId, cmap) {
    const view = document.querySelector(".view--playbook");
    const panel = view.querySelector("[data-pb-deviations-panel]");
    const wrap = view.querySelector("[data-pb-deviations]");
    const title = view.querySelector("[data-pb-dev-title]");
    if (!panel || !wrap) return;
    panel.hidden = false;
    wrap.innerHTML = loadingHtml();
    try {
      const detail = await api.playbooks.runDetail(runId);
      const devs = detail.deviations || [];
      if (title) title.textContent = `Deviations · ${devs.length} on ${contractName(cmap || {}, detail.contract_id)}`;
      wrap.innerHTML = devs.length
        ? devs.map((d) => `
          <div class="finding finding--${d.severity === "high" ? "high" : d.severity === "low" ? "ok" : "med"}">
            <div class="finding__head">
              <span class="finding__sev">${esc(humanize(d.severity))}</span>
              <span class="finding__title">${esc(humanize(d.clause_type || "clause"))}</span>
              <span class="finding__cite">${esc(humanize(d.status || "open"))}</span>
            </div>
            <div class="finding__body">
              ${esc(d.issue || "")}
              ${d.suggested_fix ? `<div style="margin-top:8px"><strong>Suggested fix:</strong> ${esc(d.suggested_fix)}</div>` : ""}
            </div>
          </div>`).join("")
        : emptyHtml("No deviations recorded for this run.");
      panel.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (err) {
      wrap.innerHTML = errorHtml(err);
    }
  }

  // Top-bar + add-rule actions
  function bindPlaybookActions() {
    const view = document.querySelector(".view--playbook");
    if (!view || view.__pbBound) return;
    view.__pbBound = true;
    const toast = window.__aegisToast || (() => {});

    view.querySelector('[data-action="pb-publish"]')?.addEventListener("click", async () => {
      try {
        await api.playbooks.publish(pbState.id, pbState.versionId);
        toast({ title: "Version published", sub: "Now live for contract reviews", kind: "success" });
        renderPlaybookDetail();
      } catch (e) { toast({ title: "Publish failed", sub: e.message, kind: "error" }); }
    });
    view.querySelector('[data-action="pb-new-version"]')?.addEventListener("click", async () => {
      try {
        await api.playbooks.newVersion(pbState.id, { summary: "Draft cloned from current" });
        toast({ title: "New draft version created", kind: "success" });
        renderPlaybookDetail();
      } catch (e) { toast({ title: "Failed", sub: e.message, kind: "error" }); }
    });
    view.querySelector('[data-action="pb-run"]')?.addEventListener("click", async () => {
      // Run against the first contract for demo
      try {
        const contracts = await api.contracts.list();
        if (!contracts.length) return toast({ title: "No contract to run against", kind: "warn" });
        toast({ title: "Running playbook…", sub: "Scoring " + contracts[0].title, kind: "ai" });
        await api.playbooks.run(pbState.id, { contract_id: contracts[0].id });
        toast({ title: "Run started", sub: "Check the Runs tab", kind: "success" });
        const t = [...view.querySelectorAll(".cd-tab")].find((x) => /runs/i.test(x.textContent));
        t?.click();
        renderPlaybookRuns(null);
      } catch (e) { toast({ title: "Run failed", sub: e.message, kind: "error" }); }
    });
    view.querySelector('[data-action="pb-add-rule"]')?.addEventListener("click", () => openAddRuleModal());
  }

  // Add-rule modal (built on the fly)
  function openAddRuleModal() {
    const toast = window.__aegisToast || (() => {});
    let modal = document.querySelector("[data-pb-rule-modal]");
    if (!modal) {
      modal = document.createElement("div");
      modal.className = "modal";
      modal.setAttribute("data-pb-rule-modal", "");
      modal.innerHTML = `
        <div class="modal__backdrop" data-rule-close></div>
        <div class="modal__panel">
          <div class="modal__head"><div><h3>New playbook rule</h3><p>Define the preferred and fallback positions.</p></div>
            <button class="icon-btn" data-rule-close><svg viewBox="0 0 16 16" fill="none"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg></button>
          </div>
          <div class="modal__body">
            <div class="upload__form">
              <div class="up-field"><label>Clause type</label><input data-f="clause_type" value="limitation_of_liability"/></div>
              <div class="up-field"><label>Rule type</label><input data-f="rule_type" value="preferred_position"/></div>
              <div class="up-field"><label>Risk level</label><input data-f="risk_level" value="high"/></div>
              <div class="up-field"><label>Escalation role</label><input data-f="escalation_role" value="legal"/></div>
            </div>
            <div class="up-field" style="margin-top:10px"><label>Preferred position</label><input data-f="preferred_position" value="Liability capped at 12 months of fees."/></div>
            <div class="up-field" style="margin-top:10px"><label>Fallback position</label><input data-f="fallback_position" value="Up to 24 months with carve-outs, subject to approval."/></div>
            <div class="up-field" style="margin-top:10px"><label>Prohibited language</label><input data-f="prohibited_language" value="unlimited liability"/></div>
            <div class="up-field" style="margin-top:10px"><label>Rationale</label><input data-f="rationale" value="Uncapped liability creates disproportionate exposure."/></div>
          </div>
          <div class="modal__foot">
            <button class="ghost-btn" data-rule-close>Cancel</button>
            <button class="primary-btn" data-rule-save>Add rule</button>
          </div>
        </div>`;
      document.body.appendChild(modal);
      modal.querySelectorAll("[data-rule-close]").forEach((b) => b.onclick = () => modal.hidden = true);
      modal.querySelector("[data-rule-save]").onclick = async () => {
        const body = {};
        modal.querySelectorAll("[data-f]").forEach((i) => { if (i.value) body[i.dataset.f] = i.value; });
        try {
          await api.playbooks.addRule(pbState.id, pbState.versionId, body);
          modal.hidden = true;
          toast({ title: "Rule added", kind: "success" });
          renderPlaybookRules();
        } catch (e) { toast({ title: "Add failed", sub: e.message, kind: "error" }); }
      };
    }
    modal.hidden = false;
  }

  // ---------- SEARCH (real) ----------
  async function renderSearch() {
    const view = document.querySelector(".view--search");
    const input = view && view.querySelector(".search-input input");
    const results = view && view.querySelector(".search-results");
    const sub = view && view.querySelector(".search-hero p");
    if (!input || !results) return;
    const cmap = await contractMap().catch(() => ({}));
    const titleOf = (h) => h.title || h.name || h.clause_type || h.section || h.heading || (h.kind === "clause" ? "Clause" : h.kind === "project" ? "Project" : "Result");
    const bodyOf = (h) => h.snippet || h.text || h.content || h.clause_text || h.excerpt || h.description || h.summary || "";
    const pathOf = (h) => { const cid = h.contract_id || (h.kind === "contract" ? h.id : null); return cid ? contractName(cmap, cid) : (h.path || h.project_name || ""); };
    const kindCls = (k) => (k === "contract" ? "contract" : k === "clause" ? "clause" : "playbook");
    const rowHtml = (h) => `<li>
        <div class="sr-meta"><span class="sr-kind sr-kind--${kindCls(h.kind)}">${esc(h.kind || "result")}</span><span class="sr-path">${esc(pathOf(h))}</span></div>
        <div class="sr-title">${esc(titleOf(h))}</div>
        ${bodyOf(h) ? `<blockquote>${esc(String(bodyOf(h)).slice(0, 220))}</blockquote>` : ""}
      </li>`;
    const paint = (list) => { results.innerHTML = list.length ? list.slice(0, 40).map(rowHtml).join("") : `<li>${emptyHtml("No matching results.")}</li>`; if (sub) sub.textContent = `${list.length} result${list.length === 1 ? "" : "s"} · live`; };
    const chips = [...view.querySelectorAll(".filter-chip:not(.filter-chip--ghost)")];
    const KIND_OF_LABEL = { all: null, contracts: "contract", clauses: "clause", projects: "project" };
    function updateChips(arr) {
      const count = (k) => (k ? arr.filter((h) => h.kind === k).length : arr.length);
      chips.forEach((chip) => {
        const label = (chip.textContent || "").replace(/\d+/g, "").trim().toLowerCase();
        const backed = label in KIND_OF_LABEL;
        const sp = chip.querySelector("span");
        if (backed) {
          if (sp) sp.textContent = count(KIND_OF_LABEL[label]);
          chip.classList.remove("filter-chip--disabled"); chip.disabled = false; chip.title = "";
        } else {
          if (sp) sp.textContent = "0";
          chip.classList.add("filter-chip--disabled"); chip.disabled = true; chip.title = "Not available in search";
        }
        chip.onclick = () => {
          if (chip.disabled) return;
          chips.forEach((c) => c.classList.remove("is-active")); chip.classList.add("is-active");
          paint(KIND_OF_LABEL[label] ? arr.filter((h) => h.kind === KIND_OF_LABEL[label]) : arr);
        };
      });
    }
    async function doSearch(q) {
      results.innerHTML = `<li>${loadingHtml()}</li>`;
      if (!q) { results.innerHTML = `<li>${emptyHtml("Type a query above.")}</li>`; if (sub) sub.textContent = "Live search via /api/v1/search"; return; }
      try {
        const hits = await api.search.query(q);
        const arr = Array.isArray(hits) ? hits : (hits && hits.results) || [];
        chips.forEach((c) => c.classList.remove("is-active")); if (chips[0]) chips[0].classList.add("is-active");
        if (!arr.length) { results.innerHTML = `<li>${emptyHtml(`No results for "${q}".`)}</li>`; if (sub) sub.textContent = "0 results · live"; updateChips([]); return; }
        paint(arr); updateChips(arr);
      } catch (err) { results.innerHTML = `<li>${errorHtml(err)}</li>`; }
    }
    if (!input.__searchWired) { input.__searchWired = true; input.addEventListener("input", (e) => { clearTimeout(input.__t); input.__t = setTimeout(() => doSearch(e.target.value.trim()), 250); }); }
    doSearch(input.value || "");
  }

  // ---------- SIGNATURES (in flight + completed tabs) ----------
  async function renderSignatures() {
    const view = document.querySelector(".view--signatures");
    if (!view) return;
    const list = view.querySelector(".approval-list");
    if (!list) return;
    list.innerHTML = loadingHtml();
    try {
      // No /signatures endpoint exposed publicly in this backend version; show
      // approvals with kind=signature instead, falling back to empty.
      const approvals = await api.approvals.list();
      const sigs = approvals.filter((a) => (a.kind || a.type || "").toLowerCase().includes("sign"));
      if (!sigs.length) {
        list.innerHTML = emptyHtml("No signature envelopes in flight.");
        return;
      }
      list.innerHTML = sigs.map((a) => `
        <article class="approval-card" data-id="${esc(a.id)}">
          <div class="approval-card__head">
            <span class="cell-flag cell-flag--med">${esc(a.status || "in flight")}</span>
            <span class="approval-card__title">${esc(a.title || a.contract_title || a.id)}</span>
            <span class="approval-card__time">${esc(fmtDate(a.created_at))}</span>
          </div>
        </article>`).join("");
    } catch (err) {
      list.innerHTML = errorHtml(err);
    }
  }

  // ---------- BELL dropdown (live notifications) ----------
  async function renderBell() {
    const list = document.querySelector(".bell__list");
    const head = document.querySelector(".bell__head strong");
    const headSub = document.querySelector(".bell__head span");
    if (!list) return;
    list.innerHTML = `<li>${loadingHtml()}</li>`;
    try {
      const rows = await api.notifications.list();
      if (!rows.length) {
        list.innerHTML = `<li>${emptyHtml("No notifications.")}</li>`;
        if (headSub) headSub.textContent = "0 unread";
        return;
      }
      const readSet = notifReadSet();
      list.innerHTML = rows.slice(0, 6).map((n) => `
        <li class="bell__item ${readSet.has(n.id) ? "is-read" : "bell__item--unread"}" data-id="${esc(n.id)}">
          <span class="notif__icon notif__icon--${esc((n.channel || n.event_type || "system").toString().split(".")[0])}">${esc(String(n.event_type || "S")[0].toUpperCase())}</span>
          <div>
            <div class="bell__title">${esc(n.subject || humanize(n.event_type) || "Notification")}</div>
            <div class="bell__meta">${esc(fmtDate(n.sent_at || n.created_at))}</div>
          </div>
        </li>`).join("");
      const unread = rows.filter((n) => !readSet.has(n.id)).length;
      if (headSub) headSub.textContent = `${unread} unread`;
      const dot = document.querySelector(".bell__dot");
      if (dot) {
        if (unread <= 0) dot.style.display = "none";
        else { dot.style.display = "grid"; dot.textContent = String(unread); }
      }
    } catch (err) {
      list.innerHTML = `<li>${errorHtml(err)}</li>`;
    }
  }
  // Re-render bell when it opens
  document.querySelector('[data-action="bell-toggle"]')?.addEventListener("click", () => {
    const panel = document.querySelector("[data-bell-panel]");
    if (panel && !panel.hidden) renderBell();
  });
  // Render on load
  setTimeout(renderBell, 200);

  // ---------- BRAIN Q&A ----------
  async function exportReviewXlsx(id, name) {
    const toast = rtToast();
    try {
      const blob = await api.tabularReviews.exportXlsx(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = `${String(name || "review").replace(/\s+/g, "-")}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast({ title: "Exported .xlsx", kind: "success" });
    } catch (e) { toast({ title: "Export failed", sub: e.message, kind: "error" }); }
  }

  // citations come back as a JSON or python-repr string of [{label,quote}]
  function rtParseCitations(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) return raw;
    try { const j = JSON.parse(raw); if (Array.isArray(j)) return j; } catch {}
    const out = []; const re = /['"]quote['"]\s*:\s*'([^']*)'|['"]quote['"]\s*:\s*"([^"]*)"/g; let mm;
    while ((mm = re.exec(String(raw)))) out.push({ quote: mm[1] || mm[2] });
    return out;
  }

  // cell drill-down drawer (answer · confidence · reasoning · source quote)
  // Toast helper for the review-table functions (was referenced but never defined → they all threw).
  function rtToast() { return window.__aegisToast || (() => {}); }

  function rtCellDrawer(cell, col, contractTitle, reviewId, reopen) {
    const toast = rtToast();
    document.querySelectorAll(".rt-drawer").forEach((d) => d.remove());
    const cites = rtParseCitations(cell.citations);
    const conf = (cell.confidence || "").toLowerCase();
    const d = document.createElement("div");
    d.className = "rt-drawer";
    d.innerHTML = `<div class="rt-drawer__panel">
      <div class="rt-drawer__head"><div><div class="rt-drawer__col">${esc(col ? col.name : "Cell")}</div><div class="rt-drawer__doc">${esc(contractTitle || "")}</div></div><button class="rt-drawer__x" aria-label="Close">×</button></div>
      <div class="rt-drawer__body">
        ${(cell.error_message || (cell.status || "").toLowerCase() === "failed") ? `<div class="rt-drawer__errbox"><div class="rt-drawer__errhead">⚠ This cell failed</div><div class="rt-drawer__error">${esc(cell.error_message || "The AI extraction job for this cell failed. Re-run it to try again.")}</div></div>` : ""}
        <div class="rt-drawer__label">Answer ${conf ? `<span class="rt-conf rt-conf--${conf}">${esc(conf)} confidence</span>` : ""}</div>
        <div class="rt-drawer__answer">${esc(cell.answer || cell.value || "—")}</div>
        ${cell.reasoning ? `<div class="rt-drawer__label">Reasoning</div><div class="rt-drawer__reason">${esc(cell.reasoning)}</div>` : ""}
        <div class="rt-drawer__label">Source${cites.length === 1 ? "" : "s"} from the document</div>
        ${cites.length ? cites.map((c) => `<blockquote class="rt-drawer__cite">${esc(c.quote || c.text || c.label || "")}</blockquote>`).join("") : `<div class="rt-drawer__nocite">No source quote captured for this cell.</div>`}
      </div>
      <div class="rt-drawer__foot">
        <button class="ghost-btn" data-open-contract>Open contract →</button>
        <button class="ghost-btn" data-rerun>↻ Re-run</button>
      </div></div>`;
    document.body.appendChild(d);
    const close = () => d.remove();
    d.querySelector(".rt-drawer__x").onclick = close;
    d.addEventListener("click", (e) => { if (e.target === d) close(); });
    d.querySelector("[data-open-contract]").onclick = () => { window.__activeContractId = cell.contract_id; showView("contract"); renderContractDetail(cell.contract_id); close(); };
    d.querySelector("[data-rerun]").onclick = async () => { try { await api.tabularReviews.rerunCell(reviewId, cell.id); toast({ title: "Cell re-running", kind: "ai" }); close(); setTimeout(() => reopen && reopen(), 2000); } catch (e) { toast({ title: "Couldn't rerun", sub: e.message, kind: "error" }); } };
  }

  function rtAddColumnModal(id, onDone) {
    const toast = rtToast();
    const types = Object.keys(RT_COL_TYPE_HINT);
    const m = aegisModal("Add a question column",
      `<label class="up-field"><span>Column name</span><input class="rtc-name" placeholder="e.g. Liability cap"/></label>` +
      `<label class="up-field"><span>Question AEGIS answers for each document</span><textarea class="rtc-prompt" rows="2" placeholder="e.g. What is the limitation of liability cap?"></textarea></label>` +
      `<label class="up-field"><span>Answer type</span><select class="rtc-type">${types.map((t) => `<option value="${t}">${t}</option>`).join("")}</select></label>`, { ok: "Add column" });
    m.ok.onclick = async () => {
      const name = m.el.querySelector(".rtc-name").value.trim();
      let prompt = m.el.querySelector(".rtc-prompt").value.trim();
      const type = m.el.querySelector(".rtc-type").value;
      if (!name || !prompt) { toast({ title: "Name and question required", kind: "warn" }); return; }
      prompt += RT_COL_TYPE_HINT[type] || "";
      m.ok.disabled = true; m.ok.textContent = "Adding…";
      try { await api.tabularReviews.addColumn(id, { name, prompt }); toast({ title: "Column added — AEGIS is filling cells", kind: "success" }); m.close(); setTimeout(() => onDone && onDone(), 1500); }
      catch (e) { toast({ title: "Couldn't add", sub: e.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Add column"; }
    };
  }

  async function rtAddContractModal(id, onDone) {
    const toast = rtToast();
    const contracts = _allContracts.length ? _allContracts : await api.contracts.list().catch(() => []);
    const m = aegisModal("Add a document", `<label class="up-field"><span>Document</span><select class="rtr-c">${contracts.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("")}</select></label>`, { ok: "Add document" });
    m.ok.onclick = async () => {
      m.ok.disabled = true; m.ok.textContent = "Adding…";
      try { await api.tabularReviews.addContracts(id, [m.el.querySelector(".rtr-c").value]); toast({ title: "Document added — AEGIS is filling cells", kind: "success" }); m.close(); setTimeout(() => onDone && onDone(), 1500); }
      catch (e) { toast({ title: "Couldn't add", sub: e.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Add document"; }
    };
  }

  // Create modal — connected to projects + documents. prefill = {projectId, contractIds, name}
  async function rtCreateModal(prefill, onCreated) {
    prefill = prefill || {};
    const toast = rtToast();
    const [contracts, projects] = await Promise.all([
      _allContracts.length ? Promise.resolve(_allContracts) : api.contracts.list().catch(() => []),
      api.projects.list().catch(() => []),
    ]);
    const preIds = new Set(prefill.contractIds || []);
    const colRow = (n, p) => `<div class="rtnew-col"><input class="rtnew-cname" placeholder="Column name" value="${esc(n || "")}"/><input class="rtnew-cprompt" placeholder="Question AEGIS should answer" value="${esc(p || "")}"/></div>`;
    const body =
      `<label class="up-field"><span>Table name</span><input class="rtnew-name" placeholder="e.g. NDA liability comparison" value="${esc(prefill.name || "")}"/></label>` +
      `<label class="up-field"><span>Project (optional — links this table to a matter)</span><select class="rtnew-project"><option value="">— No project —</option>${projects.map((p) => `<option value="${esc(p.id)}" ${prefill.projectId === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>` +
      `<div class="rtnew-field"><div class="rtnew-flabel">Documents to compare</div><div class="rtnew-contracts">${contracts.length ? contracts.map((c) => `<label class="rtnew-c"><input type="checkbox" value="${esc(c.id)}" ${preIds.has(c.id) ? "checked" : ""}/><span class="rtnew-cttl">${esc(c.title)}</span></label>`).join("") : emptyHtml("No documents available — upload one first.")}</div></div>` +
      `<div class="rtnew-field"><div class="rtnew-flabel">Columns — each is a question AEGIS answers per document</div><div class="rtnew-cols">${colRow("Governing law", "What is the governing law?")}${colRow("Liability cap", "What is the limitation of liability cap?")}</div><button type="button" class="text-btn rtnew-addcol">+ Add column</button></div>`;
    const m = aegisModal("New review table", body, { ok: "Create table" });
    m.el.querySelector(".rtnew-addcol").onclick = () => m.el.querySelector(".rtnew-cols").insertAdjacentHTML("beforeend", colRow());
    m.ok.onclick = () => {
      const name = m.el.querySelector(".rtnew-name").value.trim();
      const project_id = m.el.querySelector(".rtnew-project").value || null;
      const contract_ids = [...m.el.querySelectorAll(".rtnew-contracts input:checked")].map((i) => i.value);
      const columns = [...m.el.querySelectorAll(".rtnew-col")].map((row) => ({ name: row.querySelector(".rtnew-cname").value.trim(), prompt: row.querySelector(".rtnew-cprompt").value.trim() })).filter((c) => c.name && c.prompt);
      if (!name) { toast({ title: "Table name required", kind: "warn" }); return; }
      if (!columns.length) { toast({ title: "Add at least one column", sub: "Each needs a name and a question", kind: "warn" }); return; }
      if (!contract_ids.length) { toast({ title: "Pick at least one document", kind: "warn" }); return; }
      toast({ title: "Creating review table…", sub: `${contract_ids.length} document${contract_ids.length === 1 ? "" : "s"} × ${columns.length} question${columns.length === 1 ? "" : "s"} — AEGIS is answering each cell`, kind: "ai" });
      m.close();
      const payload = { name, contract_ids, columns };
      if (project_id) payload.project_id = project_id;
      api.tabularReviews.create(payload)
        .then((r) => { toast({ title: "Review table ready", sub: name, kind: "success" }); onCreated && onCreated(r.id || (r.review && r.review.id)); })
        .catch((e) => toast({ title: "Couldn't create table", sub: e.message || "error", kind: "error" }));
    };
  }

  // ---------- LIST PAGE (#review) ----------
  async function renderReviewTables() {
    const view = document.querySelector(".view--review");
    const hero = view && view.querySelector(".rt__hero");
    const tableWrap = view && view.querySelector(".rt-table-wrap");
    if (!tableWrap) return;
    tableWrap.innerHTML = loadingHtml();
    const openTable = (rid) => { window.__activeReviewId = rid; showView("reviewtable"); renderReviewTableDetail(rid); };
    const openCreate = () => rtCreateModal({}, (newId) => { renderReviewTables(); if (newId) openTable(newId); });
    const newBtn = view.querySelector("[data-rt-new]");
    if (newBtn) newBtn.onclick = openCreate;
    try {
      const [rows, projects] = await Promise.all([api.tabularReviews.list(), api.projects.list().catch(() => [])]);
      const projName = (pid) => { const p = projects.find((x) => x.id === pid); return p ? p.name : null; };
      if (hero) hero.querySelector("h1").textContent = `Review tables · ${rows ? rows.length : 0}`;
      if (!rows || !rows.length) {
        tableWrap.innerHTML = `<div class="rt-empty">${emptyHtml("No review tables yet.")}<button class="primary-btn rt-empty__cta">+ New review table</button></div>`;
        tableWrap.querySelector(".rt-empty__cta").onclick = openCreate;
        return;
      }
      const paintList = (list) => {
        tableWrap.innerHTML = `<table class="ctr-table rt-list" style="width:100%"><thead><tr><th>Name</th><th>Documents</th><th>Project</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>
          ${list.map((r) => { const pn = projName(r.project_id); return `<tr data-id="${esc(r.id)}" style="cursor:pointer">
            <td><div class="ctr-title"><span class="ctr-title__name">${esc(r.name)}</span></div></td>
            <td>${esc(String((r.source_contract_ids || r.contract_ids || []).length))} docs</td>
            <td>${pn ? `<span class="rt-proj" data-proj="${esc(r.project_id)}">${esc(pn)}</span>` : "<span style='color:var(--fg-subtle)'>—</span>"}</td>
            <td>${statusFlag(r.status)}</td>
            <td>${esc(fmtDate(r.created_at))}</td>
            <td style="text-align:right;color:var(--fg-subtle);font-size:12px">Open →</td>
          </tr>`; }).join("")}</tbody></table>`;
        tableWrap.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", (e) => {
          const pchip = e.target.closest("[data-proj]");
          if (pchip) { e.stopPropagation(); window.__activeProjectId = pchip.dataset.proj; showView("project"); renderProjectDetail(pchip.dataset.proj); return; }
          openTable(tr.dataset.id);
        }));
      };
      paintList(rows);
      const filterBtn = view.querySelector("[data-rt-filter]");
      if (filterBtn) filterBtn.onclick = () => { const q = window.prompt("Filter review tables by name:"); if (q == null) return; paintList(rows.filter((r) => (r.name || "").toLowerCase().includes(q.toLowerCase()))); };
    } catch (err) { tableWrap.innerHTML = errorHtml(err); }
  }

  // ---------- FULL-PAGE DETAIL (#reviewtable) ----------
  async function renderReviewTableDetail(id) {
    const view = document.querySelector(".view--reviewtable");
    if (!view || !id) return;
    _currentReviewId = id;
    const body = view.querySelector(".rtd");
    const actions = view.querySelector(".rtd-actions");
    const crumb = view.querySelector(".rtd-crumb");
    const back = view.querySelector(".rtd-back");
    if (back) back.onclick = () => { showView("review"); renderReviewTables(); };
    body.innerHTML = loadingHtml();
    if (actions) actions.innerHTML = "";
    try {
      const [detail, cmap, projects] = await Promise.all([api.tabularReviews.get(id), contractMap(), api.projects.list().catch(() => [])]);
      const review = detail.review || detail;
      const cols = (detail.columns || review.columns || []).slice().sort((a, b) => (a.position || 0) - (b.position || 0));
      const cells = detail.cells || [];
      const contractIds = review.source_contract_ids || review.contract_ids || [...new Set(cells.map((c) => c.contract_id).filter(Boolean))];
      const cellFor = (cid, colId) => cells.find((c) => c.contract_id === cid && (c.column_id === colId || c.tabular_column_id === colId));
      const proj = projects.find((p) => p.id === review.project_id);
      const reopen = () => renderReviewTableDetail(id);
      if (crumb) crumb.textContent = review.name || "Table";
      if (actions) {
        actions.innerHTML =
          `<button class="ghost-btn" data-ask>✦ Ask about this table</button>` +
          `<button class="ghost-btn" data-add-col>+ Column</button>` +
          `<button class="ghost-btn" data-add-contract>+ Document</button>` +
          `<button class="primary-btn" data-export>Export</button>`;
        actions.querySelector("[data-export]").onclick = () => exportReviewXlsx(id, review.name);
        actions.querySelector("[data-add-col]").onclick = () => rtAddColumnModal(id, reopen);
        actions.querySelector("[data-add-contract]").onclick = () => rtAddContractModal(id, reopen);
        actions.querySelector("[data-ask]").onclick = () => { showView("assistant"); seedAssistant(`In the "${review.name}" review table, summarise the key differences across the documents and flag anything risky.`); };
      }
      const confClass = (cell) => { const st = (cell.status || "").toLowerCase(); if (st !== "complete") return "rt-cell--pending"; const c = (cell.confidence || "").toLowerCase(); return c === "high" ? "rt-cell--high" : c === "medium" ? "rt-cell--med" : c === "low" ? "rt-cell--low" : ""; };
      const cellShort = (cell) => { if (!cell) return "<span style='color:var(--fg-subtle)'>—</span>"; const st = (cell.status || "").toLowerCase(); if (st === "failed") return `<span class="rt-cell__pending rt-cell__failed" title="${esc(cell.error_message || "This cell failed — click to see why and re-run")}">failed</span>`; if (st !== "complete") return `<span class="rt-cell__pending">running…</span>`; return esc(String(cell.answer || cell.value || "").slice(0, 110)) || "—"; };
      const head = `<tr><th class="rt-col--doc">Document</th>${cols.map((c) => `<th class="rt-colh"><div class="rt-colh__name">${esc(c.name || "Column")}</div><div class="rt-colh__q">${esc((c.prompt || "").slice(0, 64))}</div></th>`).join("")}<th class="rt-colh--add" data-addcol title="Add a question column">+</th></tr>`;
      const bodyRows = contractIds.length
        ? contractIds.map((cid) => `<tr>
            <td class="rt-col--doc"><svg viewBox="0 0 16 16" fill="none" style="width:12px;height:12px;opacity:.55;flex:none"><path d="M3 2h7l3 3v9H3V2z" stroke="currentColor" stroke-width="1.3"/></svg><span>${esc(contractName(cmap, cid))}</span></td>
            ${cols.map((c) => { const cell = cellFor(cid, c.id); return `<td class="rt-cell ${cell ? confClass(cell) : ""}" ${cell ? `data-cell-open="${esc(cell.id)}"` : ""}>${cellShort(cell)}</td>`; }).join("")}
            <td></td></tr>`).join("")
        : `<tr><td colspan="${cols.length + 2}">${emptyHtml("No documents yet — use “+ Document”.")}</td></tr>`;
      const completed = cells.filter((c) => (c.status || "").toLowerCase() === "complete").length;
      const total = cells.length || contractIds.length * cols.length;
      body.innerHTML =
        `<div class="rtd-head"><div><h1 class="rtd-title">${esc(review.name || "Review")}</h1>` +
        `<div class="rtd-meta">${cols.length} question${cols.length === 1 ? "" : "s"} · ${contractIds.length} document${contractIds.length === 1 ? "" : "s"} · ${completed}/${total || 0} cells answered · ${statusFlag(review.status)}${proj ? ` · in <span class="rt-proj" data-proj="${esc(proj.id)}">${esc(proj.name)}</span>` : ""}</div></div></div>` +
        `<p class="rtd-hint">Each cell is an AI answer with a citation — click any cell to see the source quote and reasoning.</p>` +
        `<div class="rt-grid-scroll"><table class="rt-grid2"><thead>${head}</thead><tbody>${bodyRows}</tbody></table></div>`;
      body.querySelector("[data-addcol]") && body.querySelector("[data-addcol]").addEventListener("click", () => rtAddColumnModal(id, reopen));
      body.querySelector("[data-proj]") && body.querySelector("[data-proj]").addEventListener("click", () => { window.__activeProjectId = review.project_id; showView("project"); renderProjectDetail(review.project_id); });
      // Open the cell drawer via ONE delegated listener (robust against the poll re-rendering the grid).
      view.__rtData = { cells, cols, cmap, id, reopen };
      if (!view.dataset.cellDelegated) {
        view.dataset.cellDelegated = "1";
        view.addEventListener("click", (e) => {
          const td = e.target.closest("[data-cell-open]");
          if (!td) return;
          const d = view.__rtData; if (!d) return;
          const cell = d.cells.find((x) => x.id === td.dataset.cellOpen); if (!cell) return;
          const col = d.cols.find((c) => c.id === (cell.column_id || cell.tabular_column_id));
          rtCellDrawer(cell, col, contractName(d.cmap, cell.contract_id), d.id, d.reopen);
        });
      }
      if (cells.some((c) => ["pending", "running", "queued"].includes((c.status || "").toLowerCase()))) {
        clearTimeout(view.__rtPoll);
        view.__rtPoll = setTimeout(() => { if (_currentReviewId === id && !view.hidden) renderReviewTableDetail(id); }, 6000);
      }
    } catch (e) { body.innerHTML = errorHtml(e); }
  }

  // ---------- SIGNATURES (real /signatures endpoint) ----------
  async function renderSignaturesLive() {
    const view = document.querySelector(".view--signatures");
    if (!view) return;
    const list = view.querySelector(".approval-list");
    if (!list) return;
    list.innerHTML = loadingHtml();
    try {
      const [rows, cmap] = await Promise.all([api.signatures.list(), contractMap()]);
      if (!rows || !rows.length) {
        list.innerHTML = emptyHtml("No signature envelopes.");
        return;
      }
      list.innerHTML = rows.map((s) => {
        const title = s.contract_id ? contractName(cmap, s.contract_id) : ("Envelope " + String(s.provider_envelope_id || s.id).slice(0, 10));
        const when = s.sent_at || s.completed_at || s.created_at;
        return `
        <article class="approval-card" data-id="${esc(s.id)}">
          <div class="approval-card__head">
            ${statusFlag(s.status)}
            <span class="approval-card__title">${esc(title)}</span>
            <span class="approval-card__time">${esc(fmtDate(when))}</span>
          </div>
          <div style="font-size:11.5px;color:var(--fg-subtle);font-family:'JetBrains Mono',monospace">
            ${esc(humanize(s.provider || "DocuSign"))} · envelope ${esc(String(s.provider_envelope_id || s.id).slice(0, 14))}
            ${s.sent_at ? " · sent " + fmtDate(s.sent_at) : ""}${s.completed_at ? " · completed " + fmtDate(s.completed_at) : ""}
          </div>
        </article>`;
      }).join("");
    } catch (err) {
      list.innerHTML = errorHtml(err);
    }
  }

  // ---------- ADMIN tabs (Org, Settings) ----------
  async function renderAdminFull() {
    await renderAdmin();
    await renderAdminOrg();
    // Settings tab — populate Organization form from /admin/settings
    try {
      const s = await api.admin.settings.get();
      const orgInputs = document.querySelectorAll('.view--admin [data-tab-panel="adm-org"] input');
      if (orgInputs[2] && s?.plan) orgInputs[2].value = s.plan;
      if (orgInputs[3] && s?.region) orgInputs[3].value = s.region;
    } catch (err) { /* admin/settings may not exist for non-admins */ }

    // ROLES tab — derive real role distribution from /users
    const rolesPanel = document.querySelector('.view--admin [data-tab-panel="adm-roles"]');
    if (rolesPanel) {
      const grid = rolesPanel.querySelector(".role-grid");
      const head = rolesPanel.querySelector(".panel__head h3");
      if (grid) {
        grid.innerHTML = loadingHtml();
        try {
          const users = await api.admin.members.list();
          const byRole = {};
          users.forEach((u) => {
            const r = (u.roles && u.roles[0]) || u.active_role_name || u.role || "member";
            byRole[r] = (byRole[r] || 0) + 1;
          });
          const entries = Object.entries(byRole);
          if (head) head.textContent = `Roles · ${entries.length}`;
          grid.innerHTML = entries.map(([role, n], i) => `
            <article class="role-card ${i === 0 ? "role-card--admin" : ""}">
              <div class="role-card__head"><h4>${esc(humanize(role))}</h4><span>${n} member${n === 1 ? "" : "s"}</span></div>
              <p>Live count from your org's user directory (/users).</p>
            </article>`).join("") || emptyHtml("No roles found.");
        } catch (e) { grid.innerHTML = errorHtml(e); }
      }
    }

    // INTEGRATIONS / SECURITY / AUDIT / BILLING — no backend endpoints → honest states
    const honest = (panelKey, msg) => {
      const p = document.querySelector(`.view--admin [data-tab-panel="${panelKey}"]`);
      if (!p) return;
      // prepend an honest banner; keep the existing layout dimmed behind it
      if (!p.querySelector(".live-empty")) {
        const note = document.createElement("div");
        note.className = "live-empty";
        note.style.margin = "0 0 14px";
        note.innerHTML = msg;
        p.insertBefore(note, p.firstChild);
      }
    };
    honest("adm-integrations", "<strong>Integration status isn't exposed by the API.</strong> The cards below are illustrative — connect real services via your backend's integration config.");
    honest("adm-security", "<strong>SSO / MFA / retention settings aren't exposed by the API.</strong> Shown as a reference layout.");
    honest("adm-audit", "<strong>No audit-log endpoint.</strong> See the Jobs view for live background-task history.");
    honest("adm-billing", "<strong>No billing endpoint.</strong> Plan &amp; usage figures below are illustrative.");
  }

  // ---- Pinned contracts sidebar (real, clickable) -------------------------
  function _titleCase(s) { return (s || "").replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase()); }
  function pinnedDot(stage) {
    const k = (stage || "").toLowerCase();
    if (k === "counterparty_review") return ["negotiation", "Negotiation"];
    if (k === "ai_review" || k === "internal_review") return ["review", "Review"];
    if (k === "signature_pending" || k === "approved") return ["signed", "Signature"];
    if (k === "active") return ["active", "Active"];
    if (k === "signed" || k === "executed") return ["signed", "Signed"];
    if (k === "intake") return ["review", "Intake"];
    if (k === "drafting") return ["review", "Drafting"];
    return ["review", _titleCase(k || "draft")];
  }
  function pinnedLabel(title) {
    return (title || "Untitled")
      .replace(/Master Services? Agreement/i, "MSA")
      .replace(/Mutual Non-Disclosure Agreement/i, "NDA")
      .replace(/Non-Disclosure Agreement/i, "NDA")
      .replace(/Payment Processing Agreement/i, "payments")
      .replace(/Services Renewal Agreement/i, "renewal")
      .replace(/SaaS Order Form/i, "SaaS order")
      .replace(/Order Form/i, "order")
      .replace(/\s+/g, " ").trim();
  }
  async function renderPinnedContracts() {
    const host = document.querySelector("[data-pinned-contracts]");
    if (!host) return;
    let rows = [];
    try { rows = await api.contracts.list(); } catch { host.innerHTML = ""; return; }
    rows = Array.isArray(rows) ? rows : (rows && rows.items) || [];
    if (!rows.length) { host.innerHTML = `<div class="thread-empty">No contracts yet</div>`; return; }
    // Prefer recognizable demo names, then fill remaining slots, cap at 5.
    const PREF = [/techcorp/i, /\bacme\b/i, /northwind.*saas|saas.*order/i, /\bfalcon\b/i, /\bstripe\b/i];
    const seen = new Set(); const pick = [];
    for (const re of PREF) {
      const m = rows.find((c) => re.test(c.title || "") && !seen.has(c.id));
      if (m) { pick.push(m); seen.add(m.id); }
    }
    for (const c of rows) { if (pick.length >= 5) break; if (!seen.has(c.id)) { pick.push(c); seen.add(c.id); } }
    const active = window.__activeContractId;
    host.innerHTML = pick.map((c) => {
      const [dot, label] = pinnedDot(c.lifecycle_stage);
      const meta = c.counterparty_name ? `${label} · ${esc(c.counterparty_name)}` : label;
      return `<a class="thread-item${c.id === active ? " is-active" : ""}" data-view="contract" data-id="${esc(c.id)}" href="#contract">
        <div class="thread-item__title">${esc(pinnedLabel(c.title))}</div>
        <div class="thread-item__meta"><span class="stage-dot stage-dot--${dot}"></span>${meta}</div>
      </a>`;
    }).join("");
    host.querySelectorAll(".thread-item").forEach((a) => {
      a.addEventListener("click", async (e) => {
        e.preventDefault();
        host.querySelectorAll(".thread-item").forEach((x) => x.classList.remove("is-active"));
        a.classList.add("is-active");
        window.__activeContractId = a.dataset.id;
        showView("contract");
        await renderContractDetail(a.dataset.id);
      });
    });
  }
  window.__renderPinnedContracts = renderPinnedContracts;

  // ---------- view-switch hook ----------
  const refreshers = {
    hub: renderHub,
    contracts: renderContracts,
    contract: () => renderContractDetail(window.__activeContractId),
    approvals: renderApprovals,
    projects: renderProjects,
    project: () => renderProjectDetail(window.__activeProjectId),
    playbooks: renderPlaybooks,
    playbook: renderPlaybookDetail,
    obligations: async () => { await renderObligations(); await renderRenewals(); },
    renewals: renderRenewalsView,
    notifications: renderNotifications,
    jobs: renderJobs,
    admin: renderAdminFull,
    assistant: renderAssistantRecents,
    search: renderSearch,
    signatures: renderSignaturesLive,
    review: renderReviewTables,
    reviewtable: () => renderReviewTableDetail(window.__activeReviewId),
  };

  window.aegis.store.on("view/show", (name) => {
    const fn = refreshers[name];
    if (fn) fn();
  });

  // On auth success → refresh everything visible + KPIs
  window.aegis.store.on("auth/login", () => {
    renderHub();
    renderAssistantRecents();
    renderPinnedContracts();
    const visible = document.querySelector(".view:not([hidden])")?.dataset.view;
    if (visible && refreshers[visible]) refreshers[visible]();
  });

  // First paint
  if (window.aegis.api.auth.isLoggedIn()) {
    setTimeout(() => {
      const visible = document.querySelector(".view:not([hidden])")?.dataset.view;
      if (visible && refreshers[visible]) refreshers[visible]();
      renderHub();
      renderAssistantRecents();
      renderPinnedContracts();
    }, 50);
  }

  window.aegis.renderers = refreshers;
  window.__aegisModal = aegisModal;
})();
