/* Aegis Word add-in — Legora-style assistant.
   Conversation flows above; the input is pinned at the bottom. The open
   document auto-links to a contract in the backend on open (no manual step). */
/* global Office, Word */

const API_BASE = "/api/v1";
const APP_BASE = "https://aegis.ctpsandbox.com";
const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const TOKEN_KEY = "aegis_token";
const USER_KEY = "aegis_user";
const THEME_KEY = "aegis_theme";
const LINK_KEY = "aegisContractId";
const SESSION_KEY = "aegisSessionId";
const SESSION_CONTRACT_KEY = "aegisSessionContractId";

let trackChangesSupported = false;
let commentsSupported = false;
let booted = false;
let trackOn = true;
let activeStreamAbort = null;

const SEV = { high: 0, medium: 1, low: 2 };
const OBLIGATIONS_Q = "Extract every obligation in this contract — deadlines, payments, and renewal duties — as a clear list.";
const SUMMARIZE_Q = "Summarize this contract in a concise brief, focusing on the key commercial terms, liability, and termination.";
const CHIPS = [
  { act: "review", label: "Review", icon: "shield" },
  { act: "playbook", label: "Playbook", icon: "book" },
  { act: "obligations", label: "Obligations", icon: "list" },
  { act: "summarize", label: "Summarize", icon: "file" },
  { act: "explain", label: "Explain selection", icon: "target" },
];

const ICONS = {
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
  list: '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  arrowLeft: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  chevron: '<path d="m9 18 6-6-6-6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="M6 6l12 12"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
};
function svg(name, cls) { return '<svg class="' + (cls || "") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (ICONS[name] || "") + "</svg>"; }
const SPINNER = '<svg class="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>';
const SPINNER_SM = '<svg class="spin-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>';
const $ = (id) => document.getElementById(id);

/* ---- bootstrap ---- */
function revealBody() { document.body.style.display = ""; document.body.style.visibility = "visible"; }
function showBanner(text) { const b = $("preview-banner"); if (b) { b.textContent = text; b.classList.remove("hidden"); } }
function setStatus(msg, kind = "info") { const el = $("status"); el.textContent = msg || ""; el.className = "status " + kind; }
function bootInWord() {
  if (booted) return; booted = true;
  trackChangesSupported = Office.context.requirements.isSetSupported("WordApi", "1.4");
  commentsSupported = trackChangesSupported;
  revealBody(); initTheme(); paintIcons(); paintChips(); wireUi(); restoreSession();
}
function bootOutsideWord(reason) {
  if (booted) return; booted = true;
  revealBody(); initTheme(); paintIcons(); paintChips(); wireUi();
  showBanner(reason || "Preview only — open this add-in inside Microsoft Word to use it.");
  restoreSession();
}

/* ---- theme ---- */
function initTheme() {
  let t = localStorage.getItem(THEME_KEY);
  if (t !== "light" && t !== "dark") t = isOfficeDark() ? "dark" : "light";
  applyTheme(t);
}
function luminance(hex) { const m = String(hex).replace("#", ""); if (m.length < 6) return 255; return 0.299 * parseInt(m.slice(0, 2), 16) + 0.587 * parseInt(m.slice(2, 4), 16) + 0.114 * parseInt(m.slice(4, 6), 16); }
function isOfficeDark() { try { const bg = Office.context && Office.context.officeTheme && Office.context.officeTheme.bodyBackgroundColor; if (bg) return luminance(bg) < 128; } catch (e) {} return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches); }
function applyTheme(t) { document.documentElement.classList.toggle("dark", t === "dark"); localStorage.setItem(THEME_KEY, t); updateThemeIcon(); }
function updateThemeIcon() { const b = $("theme-btn"); if (b) b.innerHTML = svg(document.documentElement.classList.contains("dark") ? "sun" : "moon"); }
function toggleTheme() { applyTheme(document.documentElement.classList.contains("dark") ? "light" : "dark"); }
function paintIcons() { $("logout-btn").innerHTML = svg("logout"); $("cmd-send").innerHTML = svg("arrowUp"); $("ct-back").innerHTML = svg("arrowLeft"); updateThemeIcon(); }
function paintChips() { const c = $("chips"); c.innerHTML = ""; CHIPS.forEach((ch) => { const b = document.createElement("button"); b.className = "chip"; b.innerHTML = svg(ch.icon) + "<span>" + ch.label + "</span>"; b.addEventListener("click", () => onChip(ch.act)); c.appendChild(b); }); }

/* ---- api ---- */
const getToken = () => localStorage.getItem(TOKEN_KEY) || "";

// The access token is short-lived; the refresh token lives in an HttpOnly
// cookie the backend sets on /auth/login (scoped to /api/v1/auth). Same-origin
// requests already carry it, so a silent POST /auth/refresh mints a new access
// token without the user ever seeing a sign-in prompt for an ordinary expiry.
// Mirrors frontend/src/lib/api.ts's tryRefresh — same endpoint, same one-retry
// contract, so both surfaces recover from an expired token the same way.
let refreshingToken = null;
async function tryRefreshToken() {
  if (!refreshingToken) {
    refreshingToken = (async () => {
      try {
        const res = await fetch(API_BASE + "/auth/refresh", {
          method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: "{}",
        });
        if (!res.ok) return false;
        const data = await res.json();
        if (data && data.access_token) { localStorage.setItem(TOKEN_KEY, data.access_token); return true; }
        return false;
      } catch (e) { return false; }
      finally { refreshingToken = null; }
    })();
  }
  return refreshingToken;
}

async function api(path, { method = "GET", body, form, auth = true, _retried = false } = {}) {
  const headers = {};
  if (auth && getToken()) headers["Authorization"] = "Bearer " + getToken();
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(API_BASE + path, { method, headers, body: payload, credentials: "include" });
  if (res.status === 401 && auth && !_retried) {
    if (await tryRefreshToken()) return api(path, { method, body, form, auth, _retried: true });
    doLogout();
    throw new Error("Your session expired — please sign in again.");
  }
  const raw = await res.text();
  let data; try { data = raw ? JSON.parse(raw) : {}; } catch (e) { data = { detail: raw }; }
  if (!res.ok) { const msg = (data && (data.detail || data.message)) || "HTTP " + res.status; throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg)); }
  return data;
}

/* Consumes an SSE endpoint (POST body -> text/event-stream) a chunk at a time.
   Mirrors frontend/src/lib/api.ts's apiStream — same event framing, same
   split-on-blank-line parsing — so the Word add-in and the web app agree on
   what an assistant-stream event looks like. Returns an abort function. */
function apiStreamPost(path, body, { onEvent, onError, onClose }) {
  const controller = new AbortController();
  (async () => {
    try {
      const doFetch = () => {
        const headers = { "Content-Type": "application/json" };
        if (getToken()) headers["Authorization"] = "Bearer " + getToken();
        return fetch(API_BASE + path, { method: "POST", headers, body: JSON.stringify(body || {}), credentials: "include", signal: controller.signal });
      };
      let res = await doFetch();
      if (res.status === 401 && (await tryRefreshToken())) res = await doFetch();
      if (!res.ok || !res.body) {
        let msg = "HTTP " + res.status;
        try { const d = await res.json(); msg = (d && (d.detail || d.message)) || msg; } catch (e) {}
        if (onError) onError(new Error(typeof msg === "string" ? msg : JSON.stringify(msg)));
        if (onClose) onClose();
        return;
      }
      const reader = res.body.getReader(), decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split(/\r?\n\r?\n/);
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          let eventName = "message"; const dataLines = [];
          for (const line of chunk.split(/\r?\n/)) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;
          let parsed; try { parsed = JSON.parse(dataLines.join("\n")); } catch (e) { parsed = { raw: dataLines.join("\n") }; }
          onEvent(eventName, parsed);
        }
      }
      if (onClose) onClose();
    } catch (err) {
      if (err.name !== "AbortError" && onError) onError(err);
      if (onClose) onClose();
    }
  })();
  return () => controller.abort();
}

/* ---- auth ---- */
function restoreSession() { const u = localStorage.getItem(USER_KEY); if (getToken() && u) onSignedIn(); else showView("auth"); }
async function doLogin() {
  const email = $("email").value.trim(), password = $("password").value;
  if (!email || !password) { setStatus("Enter your email and password.", "warn"); return; }
  setStatus("Signing in…"); $("login-btn").disabled = true;
  try {
    const tokens = await api("/auth/login", { method: "POST", auth: false, body: { email, password } });
    localStorage.setItem(TOKEN_KEY, tokens.access_token);
    const who = await api("/word/ping"); localStorage.setItem(USER_KEY, JSON.stringify(who));
    setStatus(""); onSignedIn();
  } catch (e) { localStorage.removeItem(TOKEN_KEY); setStatus("Sign-in failed: " + e.message, "error"); }
  finally { $("login-btn").disabled = false; }
}
function doLogout() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); $("topbar").classList.add("hidden"); setStatus(""); showView("auth"); }
function onSignedIn() { $("topbar").classList.remove("hidden"); renderWelcome(); showView("main"); autoLink(); }
function showView(name) { ["auth", "main", "contract"].forEach((v) => $("view-" + v).classList.toggle("hidden", v !== name)); $("ctx-chip").classList.toggle("hidden", name !== "main" || !getToken()); }

/* ---- auto-link status chip ---- */
function setCtx(opts) {
  const chip = $("ctx-chip"); chip.classList.remove("hidden");
  const lead = opts.state === "loading" ? SPINNER_SM : '<span class="st-dot' + (opts.state === "linked" ? " on" : "") + '"></span>';
  let html = lead + '<span class="st-main"></span>';
  if (opts.sub) html += '<span class="st-sub"></span>';
  html += svg("chevron", "st-chev");
  chip.innerHTML = html;
  chip.querySelector(".st-main").textContent = opts.main;
  if (opts.sub) chip.querySelector(".st-sub").textContent = opts.sub;
  chip._onclick = opts.onClick || null;
}
async function autoLink() {
  let id = getLinkedContractId();
  if (id) {
    try { const c = await api("/contracts/" + id); showLinked(c); return; }
    catch (e) { clearLinkedContractId(); }
  }
  let text = "";
  try { text = await getDocumentText(); }
  catch (e) { setCtx({ state: "idle", main: "Open a document in Word to link it." }); return; }
  if (!text || text.trim().length < 40) { setCtx({ state: "idle", main: "No contract detected yet", onClick: openContract }); return; }
  setCtx({ state: "loading", main: "Linking this document to Aegis…" });
  try {
    const res = await api("/word/link", { method: "POST", form: await docxForm(getDocTitle() || "contract") });
    await setLinkedContractId(res.contract_id);
    showLinked(res);
    setStatus(res.created ? "Linked — saved to Aegis." : "Linked to your contract in Aegis.", "ok");
  } catch (e) { setCtx({ state: "error", main: "Couldn't link — tap to retry", onClick: autoLink }); }
}
function showLinked(c) { setCtx({ state: "linked", main: c.title || "Linked contract", sub: prettyStage(c.lifecycle_stage || ""), onClick: openContract }); ensureSession().catch(() => {}); }
async function refreshCtx() {
  const id = getLinkedContractId();
  if (!id) { setCtx({ state: "idle", main: "No contract linked", onClick: openContract }); return; }
  try { const c = await api("/contracts/" + id); showLinked(c); } catch (e) { setCtx({ state: "idle", main: "Linked contract", onClick: openContract }); }
}

/* ---- conversation ---- */
function renderWelcome() {
  const r = $("results"); r.innerHTML = "";
  const w = document.createElement("div"); w.className = "welcome";
  w.innerHTML = '<div class="w-dot"></div><p>Ask anything about this contract, or use a quick action below to review, run a playbook, or summarize.</p>';
  r.appendChild(w);
}
function clearWelcome() { const w = $("results").querySelector(".welcome"); if (w) w.remove(); }
function scrollResults() { const r = $("results"); r.scrollTop = r.scrollHeight; }
function appendUser(text) { clearWelcome(); const d = document.createElement("div"); d.className = "turn-user"; d.textContent = text; $("results").appendChild(d); scrollResults(); }
function appendAssistant() { clearWelcome(); const d = document.createElement("div"); d.className = "turn-ai"; $("results").appendChild(d); scrollResults(); return d; }

async function runReview() {
  appendUser("Review this contract");
  const c = appendAssistant(); c.appendChild(loadingRow("Reviewing the contract…"));
  const id = getLinkedContractId();
  if (!id) { c.innerHTML = ""; c.appendChild(infoBlock("This document isn't linked to a contract yet — it links automatically once it has content.")); c.appendChild(mkBtnIcon("Open contract", "link", "outline mini", openContract)); return; }
  let risk, deviations;
  try {
    [risk, deviations] = await Promise.all([
      api("/contracts/" + id + "/risk", { method: "POST", body: {} }),
      api("/contracts/" + id + "/deviations"),
    ]);
  } catch (e) { c.innerHTML = ""; c.appendChild(errInline("Review failed: " + e.message)); return; }
  if (!risk.band || risk.band === "unknown") {
    c.innerHTML = "";
    c.appendChild(infoBlock("Still analyzing this document — clause extraction runs in the background right after linking. Try Review again in a few seconds."));
    c.appendChild(mkBtn("Try again", "outline mini", runReview));
    return;
  }
  c.innerHTML = ""; c.appendChild(reviewContent(risk, deviations));
  scrollResults();
}
function onChip(act) {
  if (act === "review") runReview();
  else if (act === "playbook") chipPlaybook();
  else if (act === "obligations") runAssistantTurn(OBLIGATIONS_Q, "Extract obligations");
  else if (act === "summarize") runAssistantTurn(SUMMARIZE_Q, "Summarize this contract");
  else if (act === "explain") explainSelection();
}

// Selection-aware ask: explain whatever clause the user has highlighted in
// the document, without them having to type a question about it.
async function explainSelection() {
  let text = "";
  try {
    text = await Word.run(async (ctx) => {
      const sel = ctx.document.getSelection(); sel.load("text"); await ctx.sync();
      return sel.text || "";
    });
  } catch (e) { setStatus("Couldn't read the selection: " + e.message, "error"); return; }
  text = text.trim();
  if (!text) { setStatus("Select some text in the document, then try again.", "warn"); return; }
  const quoted = text.length > 600 ? text.slice(0, 600) + "…" : text;
  runAssistantTurn('Explain this clause and flag any risk:\n\n"' + quoted + '"', "Explain the selected clause");
}

/* ---- streaming Ask (real Ask Aegis: tools, citations, multi-turn memory) ---- */
const TOOL_LABELS = {
  read_contract: "Reading the contract…", find_in_contract: "Searching the contract…",
  ask_contract_brain: "Checking Contract Brain…", list_playbooks: "Looking up playbooks…",
  run_playbook_review: "Running the playbook…", redline_against_playbook: "Drafting redlines…",
  edit_contract: "Preparing an edit…", redraft_contract: "Redrafting…",
  extract_obligations: "Extracting obligations…", get_contract_status: "Checking status…",
};
function toolLabel(name) { return TOOL_LABELS[name] || ("Using " + prettyStage(name || "a tool") + "…"); }

function confirmationBlock(data, turn) {
  const confirmationId = data.confirmation_id, assistantRunId = data.assistant_run_id, toolName = data.tool_name;
  const box = document.createElement("div"); box.className = "muted-block";
  const p = document.createElement("p"); p.textContent = "This needs your confirmation: " + prettyStage(toolName || "action") + ".";
  box.appendChild(p);
  const actions = document.createElement("div"); actions.className = "finding-actions";
  const disableAll = () => actions.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  actions.appendChild(mkBtn("Confirm", "primary mini", async () => {
    disableAll();
    try {
      await api("/assistant/confirmations/" + confirmationId + "/confirm", { method: "POST", body: {} });
      box.remove();
      streamIntoTurn("/assistant/runs/" + assistantRunId + "/resume?confirmation_id=" + confirmationId, {}, turn);
    } catch (e) { box.appendChild(errInline("Confirm failed: " + e.message)); }
  }));
  actions.appendChild(mkBtn("Cancel", "ghost mini", async () => {
    disableAll();
    try {
      await api("/assistant/confirmations/" + confirmationId + "/reject", { method: "POST", body: { reason: "Rejected in Word" } });
      box.remove(); turn.appendChild(infoBlock("Cancelled."));
    } catch (e) { box.appendChild(errInline("Cancel failed: " + e.message)); }
  }));
  box.appendChild(actions);
  return box;
}

// Drives one SSE call (either the initial stream or a post-confirmation
// resume) into an existing assistant turn: streams answer text in, shows a
// transient status row per tool call, and renders a Confirm/Cancel affordance
// when the assistant needs one — the same event contract Ask Aegis's web UI
// consumes (message_delta/tool_started/tool_finished/confirmation_required/error).
function streamIntoTurn(path, body, turn) {
  const thinking = loadingRow("Thinking…"); turn.appendChild(thinking); scrollResults();
  let answerEl = null, buffer = ""; const toolRows = new Map();
  function ensureAnswerEl() {
    if (!answerEl) { if (thinking.parentNode) thinking.remove(); answerEl = document.createElement("div"); answerEl.className = "answer"; turn.appendChild(answerEl); }
    return answerEl;
  }
  function onEvent(event, data) {
    if (event === "message_delta") {
      buffer += String(data.text || ""); ensureAnswerEl().innerHTML = renderMarkdown(buffer); scrollResults();
    } else if (event === "tool_started") {
      if (thinking.parentNode) thinking.remove();
      const row = loadingRow(toolLabel(data.tool_name));
      turn.insertBefore(row, answerEl || null);
      if (data.tool_use_id) toolRows.set(data.tool_use_id, row);
      scrollResults();
    } else if (event === "tool_finished") {
      const row = data.tool_use_id && toolRows.get(data.tool_use_id);
      if (row && row.parentNode) row.remove();
    } else if (event === "confirmation_required") {
      if (thinking.parentNode) thinking.remove();
      turn.appendChild(confirmationBlock(data, turn));
    } else if (event === "error") {
      if (thinking.parentNode) thinking.remove();
      turn.appendChild(errInline(String(data.message || "The assistant hit an error.")));
    }
  }
  function onError(err) { if (thinking.parentNode) thinking.remove(); turn.appendChild(errInline("Failed: " + err.message)); }
  function onClose() { if (thinking.parentNode) thinking.remove(); activeStreamAbort = null; scrollResults(); }
  activeStreamAbort = apiStreamPost(path, body, { onEvent, onError, onClose });
}

async function runAssistantTurn(message, label) {
  if (activeStreamAbort) return; // one in-flight turn at a time
  appendUser(label || message);
  const turn = appendAssistant();
  let sessionId;
  try { sessionId = await ensureSession(); }
  catch (e) { turn.appendChild(errInline("Couldn't start a conversation: " + e.message)); return; }
  const contractId = getLinkedContractId();
  streamIntoTurn("/assistant/sessions/" + sessionId + "/stream", { message, contract_ids: contractId ? [contractId] : [] }, turn);
}

function reviewContent(risk, deviations) {
  const frag = document.createDocumentFragment();
  frag.appendChild(riskHero(risk));
  const devs = (deviations || []).slice().sort((a, b) => SEV[mapSeverity(a.severity)] - SEV[mapSeverity(b.severity)]);
  if (devs.length) { const list = document.createElement("div"); list.className = "findings"; devs.forEach((d) => list.appendChild(deviationCard(d))); frag.appendChild(list); }
  else frag.appendChild(emptyInline("No open playbook deviations."));
  return frag;
}
function riskHero(risk) {
  const counts = risk.counts || { high: 0, medium: 0, low: 0 };
  const total = risk.clause_count || counts.high + counts.medium + counts.low;
  const band = risk.band || "unrated";
  const level = band === "high" ? "High risk" : band === "medium" ? "Moderate risk" : band === "low" ? "Low risk" : "Unrated";
  const card = document.createElement("div"); card.className = "risk-hero";
  card.innerHTML = '<div class="rh-eyebrow">Review</div><div class="rh-level"></div><div class="rh-meter"></div><div class="rh-counts"></div>';
  card.querySelector(".rh-level").textContent = risk.score != null ? level + " · " + risk.score + "/100" : level;
  const meter = card.querySelector(".rh-meter"); let any = false;
  [["high", counts.high], ["medium", counts.medium], ["low", counts.low]].forEach((p) => { if (p[1] > 0) { any = true; const s = document.createElement("div"); s.className = "rh-seg " + p[0]; s.style.flex = String(p[1]); meter.appendChild(s); } });
  if (!any) { const s = document.createElement("div"); s.className = "rh-seg none"; s.style.flex = "1"; meter.appendChild(s); }
  const parts = []; if (counts.high) parts.push(counts.high + " high"); if (counts.medium) parts.push(counts.medium + " medium"); if (counts.low) parts.push(counts.low + " low");
  card.querySelector(".rh-counts").textContent = total ? (total + " clause" + (total !== 1 ? "s" : "") + " reviewed" + (parts.length ? " · " + parts.join(" · ") : "")) : "No clauses extracted yet.";
  if (risk.summary) { const s = document.createElement("div"); s.className = "rh-summary"; s.textContent = risk.summary; card.appendChild(s); }
  return card;
}

/* ---- playbook ---- */
function mapSeverity(s) { s = String(s || "").toLowerCase(); if (s.includes("high") || s.includes("crit")) return "high"; if (s.includes("med")) return "medium"; return "low"; }
function citationText(d) { const c = d.citation; if (!c || typeof c !== "object") return null; return c.quote || c.text || c.original_text || c.clause_text || null; }
async function chipPlaybook() {
  const id = getLinkedContractId();
  appendUser("Run a playbook");
  const c = appendAssistant();
  if (!id) { c.appendChild(infoBlock("This document isn't linked to a contract yet — it links automatically once it has content. You can also open the contract to link it.")); c.appendChild(mkBtnIcon("Open contract", "link", "outline mini", openContract)); return; }
  c.appendChild(loadingRow("Loading playbooks…"));
  let pbs = []; try { pbs = await api("/playbooks"); } catch (e) { c.innerHTML = ""; c.appendChild(errInline("Couldn't load playbooks: " + e.message)); return; }
  c.innerHTML = "";
  if (!pbs.length) { c.appendChild(emptyInline("No playbooks yet. Create one in the Aegis app.")); return; }
  c.appendChild(infoBlock("Choose a playbook for this contract:"));
  const list = document.createElement("div"); list.className = "pick-list";
  pbs.forEach((p) => { const it = document.createElement("button"); it.className = "pick-item"; it.innerHTML = '<span><span class="pi-title"></span><span class="pi-sub"></span></span>'; it.querySelector(".pi-title").textContent = p.name || "Untitled playbook"; it.querySelector(".pi-sub").textContent = p.description || ""; it.addEventListener("click", () => choosePlaybookAction(p, id, c)); list.appendChild(it); });
  c.appendChild(list);
}
function choosePlaybookAction(p, contractId, c) {
  c.innerHTML = "";
  c.appendChild(infoBlock(p.name || "Playbook"));
  const stack = document.createElement("div"); stack.className = "stack";
  stack.appendChild(mkBtn("Run this playbook", "primary block", () => runPlaybook(p.id, contractId, c)));
  stack.appendChild(mkBtn("Browse its clause library", "outline block", () => browseClauseLibrary(p, c)));
  c.appendChild(stack);
}
async function browseClauseLibrary(p, c) {
  c.innerHTML = "";
  if (!p.current_version_id) { c.appendChild(emptyInline("This playbook has no published version yet.")); return; }
  c.appendChild(loadingRow("Loading clause library…"));
  let rules = [];
  try { rules = await api("/playbooks/" + p.id + "/versions/" + p.current_version_id + "/rules"); }
  catch (e) { c.innerHTML = ""; c.appendChild(errInline("Couldn't load the clause library: " + e.message)); return; }
  c.innerHTML = "";
  c.appendChild(infoBlock((p.name || "Playbook") + " — clause library:"));
  const usable = rules.filter((r) => r.sample_clause || r.required_language);
  if (!usable.length) { c.appendChild(emptyInline("No insertable clauses in this playbook yet.")); return; }
  const list = document.createElement("div"); list.className = "findings";
  usable.forEach((r) => list.appendChild(clauseLibraryCard(r)));
  c.appendChild(list);
}
function clauseLibraryCard(r) {
  const text = r.sample_clause || r.required_language;
  const sev = mapSeverity(r.risk_level);
  const card = document.createElement("div"); card.className = "finding";
  const head = document.createElement("div"); head.className = "finding-head";
  const badge = document.createElement("span"); badge.className = "badge " + sev; badge.textContent = (r.risk_level || sev).toUpperCase();
  const title = document.createElement("span"); title.className = "finding-title"; title.textContent = prettyStage(r.clause_type || "Clause");
  head.append(badge, title); card.appendChild(head);
  if (r.rationale) { const p2 = document.createElement("p"); p2.className = "finding-issue"; p2.textContent = r.rationale; card.appendChild(p2); }
  const s = document.createElement("div"); s.className = "suggestion"; s.textContent = clip(text, 400); card.appendChild(s);
  const actions = document.createElement("div"); actions.className = "finding-actions";
  actions.appendChild(mkBtn("Insert clause", "primary mini", () => insertClause({ suggested_text: text })));
  actions.appendChild(mkBtn("Copy", "ghost mini", () => copyText(text)));
  card.appendChild(actions);
  return card;
}
async function runPlaybook(playbookId, contractId, c) {
  c.innerHTML = ""; c.appendChild(loadingRow("Running playbook — this can take a moment…"));
  try {
    const run = await api("/playbooks/" + playbookId + "/runs", { method: "POST", body: { contract_id: contractId, create_redline: true, use_ai: true } });
    let detail = run; try { detail = await api("/playbooks/runs/" + run.id); } catch (e) {}
    c.innerHTML = "";
    const devs = (detail && detail.deviations) || [];
    c.appendChild(infoBlock(devs.length + " deviation(s) from the playbook. Redlines saved to the contract."));
    if (!devs.length) { c.appendChild(emptyInline("No deviations — the contract matches the playbook.")); return; }
    const list = document.createElement("div"); list.className = "findings";
    devs.slice().sort((a, b) => SEV[mapSeverity(a.severity)] - SEV[mapSeverity(b.severity)]).forEach((d) => list.appendChild(deviationCard(d)));
    c.appendChild(list);
  } catch (e) { c.innerHTML = ""; c.appendChild(errInline("Run failed: " + e.message)); }
}
function deviationCard(d) {
  const sev = mapSeverity(d.severity);
  const card = document.createElement("div"); card.className = "finding";
  const head = document.createElement("div"); head.className = "finding-head";
  const badge = document.createElement("span"); badge.className = "badge " + sev; badge.textContent = sev.toUpperCase();
  const title = document.createElement("span"); title.className = "finding-title"; title.textContent = prettyStage(d.clause_type || "Deviation");
  head.append(badge, title); card.appendChild(head);
  if (d.issue) { const p = document.createElement("p"); p.className = "finding-issue"; p.textContent = d.issue; card.appendChild(p); }
  if (d.suggested_fix) { const s = document.createElement("div"); s.className = "suggestion"; s.textContent = d.suggested_fix; card.appendChild(s); }
  const actions = document.createElement("div"); actions.className = "finding-actions";
  const locate = citationText(d);
  if (locate) actions.appendChild(mkBtnIcon("Locate", "target", "outline mini", () => locateInDoc(locate)));
  if (d.suggested_fix) actions.appendChild(mkBtn("Insert redline", "primary mini", () => applyRedline({ original_text: locate, suggested_text: d.suggested_fix })));
  card.appendChild(actions);
  return card;
}

/* markdown-lite */
function escapeHtml(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function renderMarkdown(src) {
  const inline = (t) => escapeHtml(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", inList = false;
  for (const raw of String(src).replace(/\r/g, "").split("\n")) {
    const b = raw.match(/^\s*[-•*]\s+(.*)$/);
    if (b) { if (!inList) { html += "<ul>"; inList = true; } html += "<li>" + inline(b[1]) + "</li>"; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (raw.trim() !== "") html += "<p>" + inline(raw.trim()) + "</p>";
  }
  if (inList) html += "</ul>";
  return html || "<p></p>";
}

/* ---- Word helpers ---- */
function getDocumentText() { return Word.run(async (ctx) => { const b = ctx.document.body; b.load("text"); await ctx.sync(); return b.text || ""; }); }
function getDocTitle() { try { const url = Office.context.document && Office.context.document.url; return url ? url.split(/[\\/]/).pop() : null; } catch (e) { return null; } }
async function ensureTrackChanges() { if (!trackChangesSupported) return; await Word.run(async (ctx) => { ctx.document.changeTrackingMode = trackOn ? Word.ChangeTrackingMode.trackAll : Word.ChangeTrackingMode.off; await ctx.sync(); }); }
async function locateInDoc(text) {
  const key = (text || "").trim().replace(/\s+/g, " ").slice(0, 255);
  if (!key) { setStatus("No text to locate.", "warn"); return; }
  try {
    const found = await Word.run(async (ctx) => { const h = ctx.document.body.search(key, { matchCase: false, ignoreSpace: true }); h.load("items"); await ctx.sync(); if (!h.items.length) return false; const r = h.items[0]; r.select(); r.font.highlightColor = "#FFF1A8"; await ctx.sync(); return true; });
    if (!found) { setStatus("Couldn't find that text — it may have changed.", "warn"); return; }
    setStatus("Located in document.", "ok");
    setTimeout(() => { Word.run(async (ctx) => { const h = ctx.document.body.search(key, { matchCase: false, ignoreSpace: true }); h.load("items"); await ctx.sync(); h.items.forEach((r) => { r.font.highlightColor = null; }); await ctx.sync(); }).catch(() => {}); }, 1500);
  } catch (e) { setStatus("Locate failed: " + e.message, "error"); }
}
async function applyRedline(f) {
  const replacement = f.suggested_text || "", original = (f.original_text || "").trim();
  try {
    await ensureTrackChanges();
    const outcome = await Word.run(async (ctx) => {
      if (original && original.length <= 255 && !original.includes("\n")) { const h = ctx.document.body.search(original, { matchCase: false, ignoreSpace: true }); h.load("items"); await ctx.sync(); if (h.items.length) { h.items[0].insertText(replacement, Word.InsertLocation.replace); await ctx.sync(); return "replaced"; } }
      const sel = ctx.document.getSelection(); sel.load("text"); await ctx.sync();
      if (sel.text && sel.text.trim()) { sel.insertText(replacement, Word.InsertLocation.replace); await ctx.sync(); return "selection"; }
      return "notfound";
    });
    if (outcome === "replaced") setStatus("Redline inserted as a tracked change.", "ok");
    else if (outcome === "selection") setStatus("Replaced your selection (tracked).", "ok");
    else setStatus("Couldn't auto-locate that text. Select the clause, then click again.", "warn");
  } catch (e) { setStatus("Edit failed: " + e.message, "error"); }
}
async function insertClause(f) {
  const t = f.suggested_text || ""; if (!t) { setStatus("Nothing to insert.", "warn"); return; }
  try { await ensureTrackChanges(); await Word.run(async (ctx) => { const sel = ctx.document.getSelection(); sel.insertParagraph(t, Word.InsertLocation.after); await ctx.sync(); }); setStatus("Clause inserted at the cursor (tracked).", "ok"); }
  catch (e) { setStatus("Insert failed: " + e.message, "error"); }
}
async function commentOnSelection(f) {
  try { await Word.run(async (ctx) => { const sel = ctx.document.getSelection(); sel.load("text"); await ctx.sync(); sel.insertComment([f.title, f.issue, f.rationale].filter(Boolean).join(" — ")); await ctx.sync(); }); setStatus("Comment added to your selection.", "ok"); }
  catch (e) { setStatus("Select some text first, then add the comment.", "warn"); }
}
async function copyText(t) { try { await navigator.clipboard.writeText(t); setStatus("Copied to clipboard.", "ok"); } catch (e) { setStatus("Couldn't copy.", "warn"); } }
function getDocxBytes() {
  return new Promise((resolve, reject) => {
    if (!Office.context || !Office.context.document || !Office.context.document.getFileAsync) return reject(new Error("Not running in Word"));
    Office.context.document.getFileAsync(Office.FileType.Compressed, { sliceSize: 65536 }, (res) => {
      if (res.status !== Office.AsyncResultStatus.Succeeded) return reject(new Error((res.error && res.error.message) || "Could not read the document"));
      const file = res.value, count = file.sliceCount, slices = new Array(count); let received = 0, failed = false;
      const done = (err) => { try { file.closeAsync(() => {}); } catch (e) {} if (err) reject(err); else assemble(); };
      const assemble = () => { let total = 0; for (let i = 0; i < count; i++) total += slices[i].length; const out = new Uint8Array(total); let off = 0; for (let i = 0; i < count; i++) { out.set(slices[i], off); off += slices[i].length; } resolve(out); };
      for (let i = 0; i < count; i++) file.getSliceAsync(i, (s) => { if (failed) return; if (s.status !== Office.AsyncResultStatus.Succeeded) { failed = true; return done(new Error("Could not read document slice")); } slices[s.value.index] = s.value.data; if (++received === count) done(null); });
    });
  });
}
async function docxForm(filename) { const bytes = await getDocxBytes(); const name = filename || "contract"; const fname = name.toLowerCase().endsWith(".docx") ? name : name + ".docx"; const form = new FormData(); form.append("file", new Blob([bytes], { type: DOCX_MIME }), fname); return form; }

/* ---- document <-> contract link ---- */
function getLinkedContractId() { try { return Office.context.document.settings.get(LINK_KEY) || null; } catch (e) { return null; } }
function setLinkedContractId(id) { return new Promise((r) => { try { Office.context.document.settings.set(LINK_KEY, id); Office.context.document.settings.saveAsync(() => r()); } catch (e) { r(); } }); }
function clearLinkedContractId() { return setLinkedContractId(null); }
function openContract() { showView("contract"); renderContract(); }

/* ---- assistant session (one per linked contract, cached on the document) ---- */
function getSessionId() { try { return Office.context.document.settings.get(SESSION_KEY) || null; } catch (e) { return null; } }
function getSessionContractId() { try { return Office.context.document.settings.get(SESSION_CONTRACT_KEY) || null; } catch (e) { return null; } }
function setSession(id, contractId) { return new Promise((r) => { try { Office.context.document.settings.set(SESSION_KEY, id); Office.context.document.settings.set(SESSION_CONTRACT_KEY, contractId || null); Office.context.document.settings.saveAsync(() => r()); } catch (e) { r(); } }); }
async function ensureSession() {
  const contractId = getLinkedContractId();
  if (getSessionId() && getSessionContractId() === contractId) return getSessionId();
  const s = await api("/assistant/sessions", {
    method: "POST",
    body: { session_type: contractId ? "contract" : "general", contract_id: contractId || undefined, title: (getDocTitle() || "Word document").slice(0, 60) },
  });
  await setSession(s.id, contractId);
  return s.id;
}

/* ---- contract panel ---- */
async function renderContract() {
  const body = $("contract-body"); body.innerHTML = "";
  const id = getLinkedContractId();
  if (!id) { renderUnlinked(body); return; }
  body.appendChild(loadingRow("Loading contract…"));
  try { const c = await api("/contracts/" + id); body.innerHTML = ""; renderLinked(body, c); await renderVersions(body, id); await renderEdits(body, id); }
  catch (e) { body.innerHTML = ""; if (/not found|404/i.test(e.message)) { body.appendChild(infoBlock("The linked contract no longer exists in Aegis.")); body.appendChild(mkBtn("Unlink", "outline", async () => { await clearLinkedContractId(); refreshCtx(); renderContract(); })); } else body.appendChild(errInline("Couldn't load the contract: " + e.message)); }
}
function renderUnlinked(body) {
  body.appendChild(sectionTitle("This document"));
  body.appendChild(infoBlock("Not linked yet. The document links automatically once it has content — or link it now:"));
  const stack = document.createElement("div"); stack.className = "stack";
  stack.appendChild(mkBtnIcon("Link automatically", "link", "primary block", async () => { showView("main"); await autoLink(); }));
  stack.appendChild(mkBtnIcon("Link to an existing contract", "link", "outline block", showLinkPicker));
  body.appendChild(stack);
}
async function showLinkPicker() {
  const body = $("contract-body"); body.innerHTML = "";
  body.appendChild(sectionTitle("Link to a contract"));
  const search = document.createElement("input"); search.className = "input"; search.placeholder = "Search contracts…"; body.appendChild(search);
  const listWrap = document.createElement("div"); listWrap.className = "pick-list"; body.appendChild(listWrap);
  body.appendChild(mkBtn("Cancel", "ghost", () => renderContract()));
  listWrap.appendChild(loadingRow("Loading contracts…"));
  let all = []; try { all = await api("/contracts"); } catch (e) { listWrap.innerHTML = ""; listWrap.appendChild(errInline("Couldn't load contracts: " + e.message)); return; }
  const draw = (items) => {
    listWrap.innerHTML = ""; if (!items.length) { listWrap.appendChild(emptyInline("No contracts found.")); return; }
    items.slice(0, 60).forEach((c) => { const it = document.createElement("button"); it.className = "pick-item"; it.innerHTML = '<span><span class="pi-title"></span><span class="pi-sub"></span></span>'; it.querySelector(".pi-title").textContent = c.title || "Untitled"; it.querySelector(".pi-sub").textContent = [c.counterparty_name, prettyStage(c.lifecycle_stage)].filter(Boolean).join(" · "); it.addEventListener("click", async () => { await setLinkedContractId(c.id); setStatus("Linked to Aegis.", "ok"); refreshCtx(); renderContract(); }); listWrap.appendChild(it); });
  };
  draw(all);
  search.addEventListener("input", () => { const q = search.value.trim().toLowerCase(); draw(!q ? all : all.filter((c) => (c.title || "").toLowerCase().includes(q) || (c.counterparty_name || "").toLowerCase().includes(q))); });
}
function renderLinked(body, contract) {
  const card = document.createElement("div"); card.className = "meta-card";
  const title = document.createElement("div"); title.className = "meta-title"; title.textContent = contract.title || "Untitled contract"; card.appendChild(title);
  const row = document.createElement("div"); row.className = "meta-row";
  if (contract.lifecycle_stage) { const s = document.createElement("span"); s.className = "stage-badge"; s.textContent = prettyStage(contract.lifecycle_stage); row.appendChild(s); }
  if (contract.counterparty_name) { const c = document.createElement("span"); c.textContent = contract.counterparty_name; row.appendChild(c); }
  card.appendChild(row);
  const actions = document.createElement("div"); actions.className = "meta-actions";
  const open = document.createElement("a"); open.className = "btn outline mini"; open.href = APP_BASE + "/contracts/" + contract.id; open.target = "_blank"; open.rel = "noopener noreferrer"; open.innerHTML = svg("external") + "<span>Open in Aegis</span>"; actions.appendChild(open);
  actions.appendChild(mkBtn("Unlink", "ghost mini", async () => { await clearLinkedContractId(); setStatus("Unlinked.", "ok"); refreshCtx(); renderContract(); }));
  card.appendChild(actions); body.appendChild(card);
}
async function renderVersions(body, id) {
  body.appendChild(sectionTitle("Versions"));
  const cpLabel = document.createElement("label"); cpLabel.className = "muted-block";
  cpLabel.style.display = "flex"; cpLabel.style.alignItems = "center"; cpLabel.style.gap = "7px"; cpLabel.style.cursor = "pointer";
  const cpCheckbox = document.createElement("input"); cpCheckbox.type = "checkbox";
  cpLabel.append(cpCheckbox, document.createTextNode("This is the counterparty's redline coming back"));
  body.appendChild(cpLabel);
  body.appendChild(mkBtnIcon("Save current draft as new version", "save", "primary block", () => saveAsNewVersion(id, cpCheckbox.checked)));
  const wrap = document.createElement("div"); wrap.className = "stack"; body.appendChild(wrap); wrap.appendChild(loadingRow("Loading versions…"));
  try { const vs = await api("/contracts/" + id + "/versions"); wrap.innerHTML = ""; if (!vs.length) { wrap.appendChild(emptyInline("No versions yet.")); return; } vs.slice().reverse().forEach((v) => wrap.appendChild(versionRow(v))); }
  catch (e) { wrap.innerHTML = ""; wrap.appendChild(errInline("Couldn't load versions: " + e.message)); }
}
function versionRow(v) {
  const row = document.createElement("div"); row.className = "version-row";
  const num = document.createElement("span"); num.className = "version-num"; num.textContent = "v" + v.version_number;
  const meta = document.createElement("div"); meta.className = "version-meta";
  const top = document.createElement("div"); top.className = "vm-top"; top.textContent = prettyStage(v.source || "version");
  const sub = document.createElement("div"); sub.className = "vm-sub"; sub.textContent = fmtDate(v.created_at);
  meta.append(top, sub); row.append(num, meta);
  if (v.is_authoritative) { const p = document.createElement("span"); p.className = "auth-pill"; p.textContent = "Current"; row.appendChild(p); }
  return row;
}
async function saveAsNewVersion(id, isCounterpartyRevision) {
  setStatus(isCounterpartyRevision ? "Logging the counterparty's revision…" : "Saving current draft as a new version…");
  try {
    const form = await docxForm(getDocTitle() || "contract");
    const path = isCounterpartyRevision ? "/contracts/" + id + "/counterparty-revision" : "/contracts/" + id + "/versions";
    const v = await api(path, { method: "POST", form });
    setStatus(
      isCounterpartyRevision
        ? "Logged as the counterparty's revision — Aegis will re-review it automatically once analysis finishes."
        : "Saved as version " + v.version_number + ".",
      "ok",
    );
    renderContract();
  } catch (e) { setStatus("Save failed: " + e.message, "error"); }
}
async function renderEdits(body, id) {
  body.appendChild(sectionTitle("Proposed redlines"));
  const wrap = document.createElement("div"); wrap.className = "stack"; body.appendChild(wrap); wrap.appendChild(loadingRow("Loading redlines…"));
  try { const edits = await api("/contracts/" + id + "/edits?status_filter=proposed"); wrap.innerHTML = ""; if (!edits.length) { wrap.appendChild(emptyInline("No proposed redlines. Run a playbook or review to create some.")); return; } edits.forEach((e) => wrap.appendChild(editCard(e, id))); }
  catch (e) { wrap.innerHTML = ""; wrap.appendChild(errInline("Couldn't load redlines: " + e.message)); }
}
function editCard(edit, id) {
  const card = document.createElement("div"); card.className = "edit-card";
  if (edit.rationale) { const r = document.createElement("div"); r.className = "finding-issue"; r.textContent = edit.rationale; card.appendChild(r); }
  const diff = document.createElement("div"); diff.className = "diff";
  if (edit.original_text) { const o = document.createElement("div"); o.className = "diff-line diff-old"; o.textContent = clip(edit.original_text); diff.appendChild(o); }
  if (edit.replacement_text) { const n = document.createElement("div"); n.className = "diff-line diff-new"; n.textContent = clip(edit.replacement_text); diff.appendChild(n); }
  card.appendChild(diff);
  const actions = document.createElement("div"); actions.className = "finding-actions";
  if (edit.original_text) actions.appendChild(mkBtnIcon("Locate", "target", "outline mini", () => locateInDoc(edit.original_text)));
  actions.appendChild(mkBtn("Apply in Word", "primary mini", () => applyRedline({ original_text: edit.original_text, suggested_text: edit.replacement_text })));
  actions.appendChild(mkBtnIcon("Accept", "check", "outline mini", () => decideEdit(id, edit.id, "accept")));
  actions.appendChild(mkBtnIcon("Reject", "x", "ghost mini", () => decideEdit(id, edit.id, "reject")));
  card.appendChild(actions);
  return card;
}
async function decideEdit(id, editId, decision) {
  setStatus(decision === "accept" ? "Accepting redline…" : "Rejecting redline…");
  try { await api("/contracts/" + id + "/edits/" + editId + "/" + decision, { method: "POST", body: {} }); setStatus("Redline " + decision + "ed in Aegis.", "ok"); renderContract(); }
  catch (e) { setStatus("Failed: " + e.message, "error"); }
}

/* ---- builders ---- */
function mkBtn(label, variant, onClick) { const b = document.createElement("button"); b.className = "btn " + variant; b.textContent = label; b.addEventListener("click", onClick); return b; }
function mkBtnIcon(label, icon, variant, onClick) { const b = document.createElement("button"); b.className = "btn " + variant; b.innerHTML = svg(icon) + "<span>" + escapeHtml(label) + "</span>"; b.addEventListener("click", onClick); return b; }
function sectionTitle(t) { const p = document.createElement("p"); p.className = "section-title"; p.textContent = t; return p; }
function infoBlock(t) { const d = document.createElement("div"); d.className = "muted-block"; d.textContent = t; return d; }
function emptyInline(t) { const p = document.createElement("p"); p.className = "empty"; p.textContent = t; return p; }
function errInline(t) { const d = document.createElement("div"); d.className = "muted-block"; d.style.color = "var(--high)"; d.textContent = t; return d; }
function loadingRow(t) { const d = document.createElement("div"); d.className = "thinking"; d.innerHTML = SPINNER + "<span></span>"; d.querySelector("span").textContent = t || "Thinking…"; return d; }
function clip(t, n) { t = String(t || ""); n = n || 220; return t.length > n ? t.slice(0, n) + "…" : t; }
function prettyStage(s) { return String(s || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()); }
function fmtDate(iso) { try { const d = new Date(iso); if (isNaN(d.getTime())) return ""; return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); } catch (e) { return ""; } }

/* ---- wiring ---- */
function autoGrow(el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 120) + "px"; }
function sendCommand() { const v = $("cmd-input").value.trim(); if (!v) return; $("cmd-input").value = ""; autoGrow($("cmd-input")); runAssistantTurn(v); }
function wireUi() {
  $("login-btn").addEventListener("click", doLogin);
  $("password").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("logout-btn").addEventListener("click", doLogout);
  $("theme-btn").addEventListener("click", toggleTheme);
  $("ctx-chip").addEventListener("click", () => { const f = $("ctx-chip")._onclick; if (f) f(); });
  $("ct-back").addEventListener("click", () => { showView("main"); refreshCtx(); });
  $("cmd-send").addEventListener("click", sendCommand);
  $("cmd-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendCommand(); } });
  $("cmd-input").addEventListener("input", (e) => autoGrow(e.target));
  const t = $("track"); t.addEventListener("change", () => { trackOn = t.checked; });
  if (!trackChangesSupported) { trackOn = false; t.checked = false; t.disabled = true; $("track-label").title = "Track changes needs a Word version that supports WordApi 1.4."; }
}

/* ---- start ---- */
if (typeof Office === "undefined") {
  const f = () => bootOutsideWord("Office.js didn't load — this add-in only runs inside Microsoft Word.");
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", f); else f();
} else {
  const fb = setTimeout(() => bootOutsideWord(), 2500);
  Office.onReady((info) => { clearTimeout(fb); if (info && info.host === Office.HostType.Word) bootInWord(); else bootOutsideWord(); });
}
