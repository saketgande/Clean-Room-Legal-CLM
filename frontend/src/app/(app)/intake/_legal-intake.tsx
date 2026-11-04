"use client";

// Legal Intake board — a 1:1 port of the Screen-1 mockup (its own palette, table
// header style, tiles, segmented stage bars, pills), wired to the real intake API.
// All CSS is scoped under `.li-board` so it cannot leak into the rest of the app;
// dark mode is driven by the app's `.dark` class.

import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Mail, MessageSquare, FileText, Search, ChevronDown, Clock, X, ChevronRight, ShieldCheck, Handshake, ClipboardList, Cloud, Lock, Briefcase, RefreshCw, HelpCircle, Gavel, Stamp, FileSignature, AlertTriangle, Users, Inbox, Hourglass, Workflow, UserX } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { intakeApi } from "@/lib/endpoints";

import { useToast } from "@/components/toast";
import { PageCrumbs } from "@/components/page-crumbs";
import { CenterSpinner, LoadError } from "@/components/ui";

import { isAwaitingTriage, isOpenRequest } from "@/lib/request-status";
import { initials } from "@/lib/utils";
import { LIFECYCLE } from "@/components/lifecycle-view";
import type { IntakeRequest, WorkflowSuggestion } from "@/lib/types";

// Refetch on window focus rather than poll — see the note on LIVE_POLL in
// intake/page.tsx for the trade-off (sla_status won't advance on its own
// while the tab sits unfocused).
const POLL = { refetchOnWindowFocus: true } as const;

// ---------- data helpers (all from real request fields) ----------
function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(id); }, [ms]);
  return now;
}
const isOpenReq = isOpenRequest;
/** Where a request is in the contract lifecycle: the step running now, else the furthest step done; no workflow yet is Intake, a closed request is Closed. */
export function stageOf(r: IntakeRequest): string {
  const wf = r.workflow ?? [];
  const live = wf.find((s) => s.active);
  if (live) return live.stage;
  const done = wf.filter((s) => s.done);
  if (wf.length && done.length === wf.length) return isOpenReq(r) ? "active" : "closed"; // every step finished
  if (done.length) return done[done.length - 1].stage;
  if (wf.length) return wf[0].stage;
  return isOpenReq(r) ? "intake" : "closed";
}
function channelOf(r: IntakeRequest): { key: string; label: string; Icon: LucideIcon } {
  const s = (r.source ?? "").toLowerCase();
  if (/mail|gmail|email|imap/.test(s)) return { key: "email", label: "Email", Icon: Mail };
  if (/slack|chat|teams|message|dm/.test(s)) return { key: "chat", label: "Chat", Icon: MessageSquare };
  if (/form|portal|self|web|intake/.test(s)) return { key: "form", label: "Form", Icon: FileText };
  return { key: "other", label: s || "other", Icon: FileText };
}
export function counterpartyOf(r: IntakeRequest): string | null {
  const parties = (r.parties ?? []) as { name?: string; role?: string }[];
  const cp = parties.find((p) => /counter|vendor|supplier|opposing|third|other/i.test(p.role ?? "")) ?? parties[0];
  if (cp?.name) return cp.name;
  const fv = (r.field_values ?? {}) as Record<string, unknown>;
  for (const k of ["counterparty", "company", "vendor", "party", "organisation", "organization"]) {
    if (typeof fv[k] === "string" && fv[k]) return fv[k] as string;
  }
  return null;
}
function flowSuggestion(r: IntakeRequest): WorkflowSuggestion | undefined {
  return (r.ai_triage as { flow_suggestion?: WorkflowSuggestion } | null)?.flow_suggestion;
}
function lowConfidence(r: IntakeRequest): boolean {
  const fs = flowSuggestion(r);
  return !!fs && (fs.needs_human || (fs.confidence ?? 1) < 0.72);
}
/** When the SLA clock runs out: filed time + SLA hours. Null when either is missing. */
/** "New agreement · NDA" → ["New agreement", "NDA"]; a label with no kind is its own kind. */
export function agreementOf(label: string): [string | null, string] {
  const parts = label.split(" · ");
  return parts.length > 1 ? [parts[0], shortKind(parts.slice(1).join(" · "))] : [null, shortKind(label)];
}
/** One short word per agreement kind so every badge is the same size; the full name stays in the tooltip. */
function shortKind(k: string): string {
  if (/statement of work|\bsow\b/i.test(k)) return "SOW";
  if (/\(msa\)|master serv|\bmsa\b/i.test(k)) return "MSA";
  if (/\bnda\b|confidential/i.test(k)) return "NDA";
  if (/\bdpa\b|data process/i.test(k)) return "DPA";
  if (/saas|software/i.test(k)) return "SaaS";
  if (/vendor|purchase/i.test(k)) return "Vendor";
  if (/consult/i.test(k)) return "Consultancy";
  return k.replace(/\s+request$/i, "");
}
const kindTone = (k: string) => (/nda|confiden/i.test(k) ? "vio" : /msa|master/i.test(k) ? "ind" : /sow|statement|work/i.test(k) ? "amb" : /dpa|data|privacy/i.test(k) ? "emr" : /vendor|purchase|saas|software/i.test(k) ? "sky" : /amend|renew|terminat|novat/i.test(k) ? "red" : "mute");

export function dueOf(r: IntakeRequest, filed: string): { date: Date; left: string; late: boolean } | null {
  const t = filed ? new Date(filed).getTime() : NaN;
  if (!Number.isFinite(t) || !(r.sla_hours > 0)) return null;
  const date = new Date(t + r.sla_hours * 3_600_000);
  const hrs = Math.round((date.getTime() - Date.now()) / 3_600_000);
  const days = Math.round(Math.abs(hrs) / 24);
  const unit = Math.abs(hrs) < 48 ? `${Math.abs(hrs)}h` : `${days}d`;
  return { date, late: hrs < 0, left: hrs < 0 ? `${unit} overdue` : `${unit} left` };
}

function ageOf(iso: string | null, now: number): string {
  if (!iso) return "";
  const m = Math.floor((now - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}
// One recognisable icon + colour per kind of request, so the list can be scanned by shape.
export function kindOf(r: IntakeRequest): { Icon: LucideIcon; tone: string; label: string } {
  const t = `${r.type_label} ${r.subject ?? ""}`.toLowerCase();
  if (/\bnda\b|non-?disclosure|confidential/.test(t)) return { Icon: ShieldCheck, tone: "vio", label: "NDA" };
  if (/\bmsa\b|master|services/.test(t)) return { Icon: Handshake, tone: "ind", label: "Services" };
  if (/statement of work|\bsow\b/.test(t)) return { Icon: ClipboardList, tone: "sky", label: "SOW" };
  if (/saas|software/.test(t)) return { Icon: Cloud, tone: "emr", label: "Software" };
  if (/\bdpa\b|data processing|privacy/.test(t)) return { Icon: Lock, tone: "red", label: "Data" };
  if (/consult/.test(t)) return { Icon: Briefcase, tone: "amb", label: "Consultancy" };
  if (/renew|amend|terminat|novat/.test(t)) return { Icon: RefreshCw, tone: "amb", label: "Change" };
  if (/trademark/.test(t)) return { Icon: Stamp, tone: "vio", label: "Trademark" };
  if (/litigation|dispute|suit|notice/.test(t)) return { Icon: Gavel, tone: "red", label: "Dispute" };
  if (/question|advice|can our|is a |do we/.test(t)) return { Icon: HelpCircle, tone: "sky", label: "Question" };
  if (/agreement|contract/.test(t)) return { Icon: FileSignature, tone: "ind", label: "Agreement" };
  return { Icon: FileText, tone: "ind", label: "Request" };
}
const isMsa = (s: string) => /msa|master|service/i.test(s);

// status filter tokens
function statusMatch(r: IntakeRequest, token: string): boolean {
  switch (token) {
    case "new": return isAwaitingTriage(r);
    case "running": return isOpenReq(r) && (r.workflow ?? []).some((s) => s.active);
    case "stuck": return r.sla_status === "overdue";
    case "at_risk": return r.sla_status === "at_risk";
    case "unassigned": return isOpenReq(r) && !r.assigned_to_user_id;
    case "lowconf": return lowConfidence(r);
    default: return true;
  }
}
const TILE_ICON: Record<string, LucideIcon> = { "": Inbox, new: Hourglass, running: Workflow, at_risk: AlertTriangle, stuck: Clock, unassigned: UserX };
const STATUS_FILTERS = [
  { value: "new", label: "Awaiting triage", dot: "var(--accent)" },
  { value: "running", label: "In workflow", dot: "var(--good)" },
  { value: "stuck", label: "Stuck / overdue", dot: "var(--crit)" },
  { value: "at_risk", label: "At risk", dot: "var(--warn)" },
  { value: "unassigned", label: "Unassigned", dot: "var(--ink-3)" },
  { value: "lowconf", label: "Low AI confidence", dot: "var(--warn)" },
];

// row flags → mockup pill classes
function rowFlags(r: IntakeRequest): { label: string; cls: string }[] {
  const f: { label: string; cls: string }[] = [];
  if (r.sla_status === "overdue") f.push({ label: "overdue", cls: "risk" });
  else if (r.sla_status === "at_risk") f.push({ label: "at risk", cls: "low" });
  if (isOpenReq(r) && !r.assigned_to_user_id) f.push({ label: "unassigned", cls: "dupe" });
  if (lowConfidence(r)) f.push({ label: "low confidence", cls: "low" });
  if (r.filed_by_agent) f.push({ label: `agent · ${r.filed_by_agent_name ?? "unknown"}`, cls: "dupe" });
  return f;
}

// Page numbers with ellipses: 1 … 4 5 6 … 20 (-1 = gap).
export function pageList(count: number, cur: number): number[] {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i);
  const keep = new Set([0, count - 1, cur - 1, cur, cur + 1]);
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    if (keep.has(i)) out.push(i); else if (out[out.length - 1] !== -1) out.push(-1);
  }
  return out;
}

// ---------- compact dropdown (mockup .dd / .ddmenu) ----------
export function FilterMenu({ label, options, selected, onToggle }: { label: string; options: { value: string; label: string; dot?: string }[]; selected: Set<string>; onToggle: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);
  const n = selected.size;
  return (
    <div className={`dd${open ? " open" : ""}`} ref={ref}>
      <button type="button" className={`ddbtn${n ? " active" : ""}`} onClick={() => setOpen((o) => !o)}>
        <span>{label}</span>{n ? <span className="cnt">{n}</span> : null}
        <ChevronDown className="caret" />
      </button>
      <div className="ddmenu">
        {options.map((o) => (
          <label key={o.value}>
            <input type="checkbox" checked={selected.has(o.value)} onChange={() => onToggle(o.value)} />
            {o.dot ? <span className="dot" style={{ background: o.dot }} /> : null}{o.label}
          </label>
        ))}
      </div>
    </div>
  );
}

// ---------- stage / progress cell (mockup .stagewrap) ----------
export function StageCell({ r }: { r: IntakeRequest }) {
  const steps = r.workflow ?? [];
  if (steps.length) {
    const done = steps.filter((s) => s.done).length;
    const active = steps.find((s) => s.active);
    const [first, ...rest] = (active?.label ?? "").split(" · ");
    const phase = !active ? (done === steps.length ? "Complete" : "In progress") : rest.length ? first : (active.stage ?? "Step").replace(/^./, (c) => c.toUpperCase());
    const stepName = !active ? (done === steps.length ? "All steps finished" : "Waiting to start") : rest.length ? rest.join(" · ") : active.label;
    return (
      <div className="stagewrap">
        <div className="stagetop">
          <span className={`phase stg-${active?.stage ?? "done"}`}>{phase}</span>
          <span className="stepno">Step {Math.min(done + 1, steps.length)} of {steps.length}</span>
        </div>
        <div className="stagenm">{stepName}</div>
        <div className="steps" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done} aria-label="Workflow progress">
          {steps.map((st, k) => {
            const late = st.active && r.sla_status === "overdue"; const risk = st.active && r.sla_status === "at_risk";
            const state = st.done ? "Completed" : st.active ? (late ? "In progress · overdue" : risk ? "In progress · at risk" : "In progress") : "Not started";
            const tone = st.done ? "d" : st.active ? (late ? "c late" : risk ? "c risk" : "c") : "";
            const pos = k === 0 ? " first" : k === steps.length - 1 ? " last" : "";
            return (
              <Fragment key={k}>
                <span className={`nd ${tone}${pos}`} tabIndex={0} aria-label={`${st.label}: ${state}`}>
                  <span className="tip" role="tooltip">
                    <b>{st.label}</b>
                    <span className={`tst tst-${st.done ? "d" : st.active ? (late ? "l" : risk ? "r" : "c") : "n"}`}><i />{state}</span>
                    <em>Step {k + 1} of {steps.length}</em>
                  </span>
                </span>
                {k < steps.length - 1 ? <i className={`ln${st.done ? " d" : ""}`} /> : null}
              </Fragment>
            );
          })}
        </div>
      </div>
    );
  }
  const fs = flowSuggestion(r);
  if (fs?.flow_name) {
    return (
      <div className="stagewrap">
        <div className="recmark"><Clock className="ic" style={{ width: 12, height: 12 }} />Not started · recommended</div>
        <div style={{ marginTop: 5 }}><span className={`wf ${isMsa(fs.flow_name) ? "msa" : ""}`}>{fs.flow_name}</span></div>
      </div>
    );
  }
  return <span className="dim">—</span>;
}

// ---------- the board ----------
export function LegalIntakeBoard({ onOpen, mode: baseMode = "all" }: { onOpen: (id: string) => void; mode?: "all" | "pool" }) {
  // The queue lists open requests; the "Show" filter swaps it for the finished ones.
  const [showDone, setShowDone] = useState(false);
  const [allOwners, setAllOwners] = useState(false);
  const mode = baseMode === "pool" && showDone ? "completed" : baseMode;
  const now = useNow(30_000);
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: baseMode === "pool" ? ["intake-pool"] : ["intake-list"],
    queryFn: async () => (baseMode === "pool" ? intakeApi.pool() : intakeApi.list()),
    ...POLL,
  });
  // The pool holds open requests only; a finished one (approved, or closed once its contract is signed) is found in the full list.
  // The overview and side panels always describe the open queue, so only the table swaps to these.
  const done = useQuery({
    queryKey: ["intake-completed"],
    queryFn: async () => (await intakeApi.list()).filter((r) => !isOpenReq(r)),
    enabled: mode === "completed",
    ...POLL,
  });
  const openList = useMemo(() => data ?? [], [data]);
  const rows = useMemo(() => (mode === "completed" ? done.data ?? [] : openList), [mode, done.data, openList]);
  const assignToMe = useMutation({
    mutationFn: (id: string) => intakeApi.assignToMe(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["intake-pool"] });
      qc.invalidateQueries({ queryKey: ["intake-mywork"] });
      notify("Assigned to you", "success");
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Could not assign", "error"),
  });

  const [search, setSearch] = useState("");
  // A card with the full details of the row under the pointer, shown a beat after it rests on a cell.
  const [tip, setTip] = useState<{ r: IntakeRequest; x: number; y: number } | null>(null);
  const tipTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const showTip = (e: React.MouseEvent<HTMLElement>, r: IntakeRequest) => {
    const b = e.currentTarget.getBoundingClientRect();
    clearTimeout(tipTimer.current);
    tipTimer.current = setTimeout(() => setTip({ r, x: Math.max(12, Math.min(b.left + 16, window.innerWidth - 372)), y: Math.min(b.bottom - 4, window.innerHeight - 250) }), 220);
  };
  const hideTip = () => { clearTimeout(tipTimer.current); setTip(null); };
  const [channelSel, setChannelSel] = useState<Set<string>>(new Set());
  const [typeSel, setTypeSel] = useState<Set<string>>(new Set());
  const [stageSel, setStageSel] = useState<Set<string>>(new Set());
  const [statusSel, setStatusSel] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState("new");
  const [ownerSel, setOwnerSel] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  // A new search/filter/sort starts from page 1; otherwise the user can land on an empty page.
  useEffect(() => { setPage(0); }, [search, channelSel, typeSel, stageSel, statusSel, ownerSel, sort, showDone, pageSize]);
  // Picking a queue status (a card, a tab, an owner) means the open queue again, not the finished list.
  useEffect(() => { if (showDone && (statusSel.size || ownerSel)) setShowDone(false); }, [statusSel, ownerSel]); // eslint-disable-line react-hooks/exhaustive-deps
  // After a card changes the filters, bring the table into view so the click visibly does something.
  const showTable = () => requestAnimationFrame(() => document.querySelector(".li-board .tcard")?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, v: string) => {
    const n = new Set(set); if (n.has(v)) n.delete(v); else n.add(v); setter(n);
  };

  const channelOpts = useMemo(() => {
    const m = new Map<string, string>();
    rows.forEach((r) => { const c = channelOf(r); m.set(c.key, c.label.charAt(0).toUpperCase() + c.label.slice(1)); });
    return [...m].map(([value, label]) => ({ value, label }));
  }, [rows]);
  const typeOpts = useMemo(() => [...new Set(rows.map((r) => r.type_label))].sort().map((t) => ({ value: t, label: t })), [rows]);

  // overview figures
  const openRows = openList.filter(isOpenReq);
  const overdue = openRows.filter((r) => r.sla_status === "overdue");
  const atRisk = openRows.filter((r) => r.sla_status === "at_risk");
  const unassigned = openRows.filter((r) => !r.assigned_to_user_id);
  const awaiting = openRows.filter(isAwaitingTriage);
  const inWorkflow = openRows.filter((r) => (r.workflow ?? []).some((s) => s.active));
  // Most-consumed SLA first, so the queue reads worst-to-least-bad.
  const priority = [...overdue, ...atRisk].sort((x, y) => (y.sla_pct ?? 0) - (x.sla_pct ?? 0));
  const loadMap = new Map<string, { total: number; stuck: number; risk: number }>();
  openRows.filter((r) => (r.workflow ?? []).some((s) => s.active) || r.status !== "open").forEach((r) => {
    const t = r.assigned_to_label ?? "Unassigned";
    const e = loadMap.get(t) ?? { total: 0, stuck: 0, risk: 0 };
    e.total += 1; if (r.sla_status === "overdue") e.stuck += 1; if (r.sla_status === "at_risk") e.risk += 1;
    loadMap.set(t, e);
  });
  const loadAll = [...loadMap.entries()].sort((a, b) => b[1].total - a[1].total);
  const load = loadAll.slice(0, 8);
  const maxLoad = Math.max(1, ...load.map(([, v]) => v.total));
  const tiles: { token: string; n: number; l: string; c?: string }[] = [
    { token: "", n: openRows.length, l: "Open requests" },
    { token: "new", n: awaiting.length, l: "Awaiting triage", c: "a" },
    { token: "running", n: inWorkflow.length, l: "In workflow" },
    { token: "at_risk", n: atRisk.length, l: "At risk", c: "w" },
    { token: "stuck", n: overdue.length, l: "Overdue", c: "c" },
    { token: "unassigned", n: unassigned.length, l: "Unassigned" },
  ];
  const applyTile = (token: string) => { setOwnerSel(null); showTable(); setStatusSel(token ? (statusSel.size === 1 && statusSel.has(token) ? new Set() : new Set([token])) : new Set()); };

  // filter + sort
  const q = search.trim().toLowerCase();
  const created = (r: IntakeRequest) => r.created_at ?? r.submitted_at ?? "";
  const filtered = rows.filter((r) => {
    if (channelSel.size && !channelSel.has(channelOf(r).key)) return false;
    if (typeSel.size && !typeSel.has(r.type_label)) return false;
    if (stageSel.size && !stageSel.has(stageOf(r))) return false;
    if (statusSel.size && ![...statusSel].some((t) => statusMatch(r, t))) return false;
    if (ownerSel && (r.assigned_to_label ?? "Unassigned") !== ownerSel) return false;
    if (q && !`${r.ref} ${r.requester_name ?? ""} ${r.type_label} ${r.subject ?? ""} ${r.description ?? ""} ${counterpartyOf(r) ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const shown = [...filtered].sort((a, b) => {
    if (sort === "cp") return (counterpartyOf(a) ?? "~").localeCompare(counterpartyOf(b) ?? "~");
    if (sort === "old") return created(a).localeCompare(created(b));
    return created(b).localeCompare(created(a));
  });
  const anyFilter = !!(q || channelSel.size || typeSel.size || stageSel.size || statusSel.size || ownerSel);

  // A queue that could not be loaded is not an empty queue: the zeros and "everything's assigned" below
  // would be a confident answer to a question nobody got an answer to.
  if (isLoading) return <div className="li-board"><CenterSpinner label="Loading requests…" /></div>;
  if (error && !data) return <div className="li-board" style={{ padding: "14px 0" }}><LoadError error={error} what="the request queue" onRetry={() => void refetch()} /></div>;

  const chips: { key: string; label: string; clear: () => void }[] = [
    ...[...channelSel].map((v) => ({ key: `c-${v}`, label: channelOpts.find((o) => o.value === v)?.label ?? v, clear: () => toggle(channelSel, setChannelSel, v) })),
    ...[...typeSel].map((v) => ({ key: `t-${v}`, label: v, clear: () => toggle(typeSel, setTypeSel, v) })),
    ...[...statusSel].map((v) => ({ key: `s-${v}`, label: STATUS_FILTERS.find((o) => o.value === v)?.label ?? v, clear: () => toggle(statusSel, setStatusSel, v) })),
  ];
  const pageCount = Math.max(1, Math.ceil(shown.length / pageSize));
  const cur = Math.min(page, pageCount - 1);
  const visible = shown.slice(cur * pageSize, (cur + 1) * pageSize);
  const pageNums = pageList(pageCount, cur);

  return (
    <div className="li-board">
      <style dangerouslySetInnerHTML={{ __html: LI_CSS }} />

      <div className="split">
      <div className="maincol">
      {/* status tabs filter the table; insight cards sit on the right */}
      {(
        <section className="ov" aria-label="Operational overview">
          <div className="kcards">
            {[
              { token: "", n: openRows.length, l: "Open requests", I: FileText, t: "ind" },
              { token: "new", n: awaiting.length, l: "Awaiting triage", I: Clock, t: "vio" },
              { token: "running", n: inWorkflow.length, l: "In workflow", I: Workflow, t: "sky" },
              { token: "at_risk", n: atRisk.length, l: "At risk", I: AlertTriangle, t: "amb" },
              { token: "stuck", n: overdue.length, l: "Overdue", I: Hourglass, t: "red" },
            ].map((k) => (
              <button key={k.l} type="button" className={`kcard kc-${k.t}`} onClick={() => applyTile(k.token)} tabIndex={-1} aria-hidden="true" title={`Show ${k.l.toLowerCase()}`}>
                <span className="kic"><k.I className="ic" aria-hidden /></span>
                <span className="ktx"><span className="kl">{k.l}</span><b className="kn num">{k.n}</b>
                  <span className="ks">{k.token ? `${openRows.length ? Math.round((k.n / openRows.length) * 100) : 0}% of open` : "all in flight"}</span></span>
              </button>
            ))}
          </div>
          <div className="stabs" role="group" aria-label="Filter by status">
            {tiles.map((t) => {
              const on = t.token ? statusSel.size === 1 && statusSel.has(t.token) : statusSel.size === 0;
              return (
                <button key={t.l} type="button" aria-pressed={on} onClick={() => applyTile(t.token)} className={`stab ${t.c ?? ""}${on ? " on" : ""}`}>
                  <span className="tl">{t.l}</span>
                  <span className="tn num">{t.n}</span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      <div className="tcard">
      {/* toolbar */}
      <div className="toolbar">
        <div className="search">
          <Search className="ic" />
          <input aria-label="Search requests" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search counterparty, subject or reference…" />
        </div>
        <FilterMenu label="Channel" options={channelOpts} selected={channelSel} onToggle={(v) => toggle(channelSel, setChannelSel, v)} />
        <FilterMenu label="Type" options={typeOpts} selected={typeSel} onToggle={(v) => toggle(typeSel, setTypeSel, v)} />
        <FilterMenu label="Stage" options={LIFECYCLE.map((l) => ({ value: l.key, label: `${l.label} (${rows.filter((r) => stageOf(r) === l.key).length})` }))} selected={stageSel} onToggle={(v) => toggle(stageSel, setStageSel, v)} />
        {baseMode === "pool" && <FilterMenu label="Show" options={[{ value: "done", label: "Completed requests", dot: "var(--good)" }]} selected={new Set(showDone ? ["done"] : [])} onToggle={() => setShowDone((d) => !d)} />}
        <FilterMenu label="Status" options={STATUS_FILTERS} selected={statusSel} onToggle={(v) => toggle(statusSel, setStatusSel, v)} />
        <span className="sortwrap"><select aria-label="Sort requests" className="sortsel" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="new">Newest first</option>
          <option value="old">Oldest first</option>
          <option value="cp">Counterparty A–Z</option>
        </select></span>
        <span className="sp" />
        <span className="dim num count">{shown.length} of {rows.length}</span>
      </div>
      {anyFilter && (
        <div className="chips">
          {q && <span className="chip">“{search.trim()}”<button type="button" aria-label="Clear search" onClick={() => setSearch("")}><X className="ic" /></button></span>}
          {ownerSel && <span className="chip">Owner: {ownerSel}<button type="button" aria-label="Remove owner filter" onClick={() => setOwnerSel(null)}><X className="ic" /></button></span>}
          {chips.map((c) => <span key={c.key} className="chip">{c.label}<button type="button" aria-label={`Remove filter ${c.label}`} onClick={c.clear}><X className="ic" /></button></span>)}
          <button type="button" className="clear" onClick={() => { setSearch(""); setChannelSel(new Set()); setTypeSel(new Set()); setStageSel(new Set()); setStatusSel(new Set()); setOwnerSel(null); }}>Clear all</button>
        </div>
      )}

      {/* table */}
      <div className="tablewrap">
        <table className="lit">
          <thead><tr>
            <th aria-sort={sort === "new" ? "descending" : sort === "old" ? "ascending" : "none"}><button type="button" className="sorth" onClick={() => setSort(sort === "new" ? "old" : "new")}>Request{sort === "new" ? <ChevronDown className="ic" /> : sort === "old" ? <ChevronDown className="ic up" /> : null}</button></th>
            <th>Agreement</th>
            <th aria-sort={sort === "cp" ? "ascending" : "none"}><button type="button" className="sorth" onClick={() => setSort("cp")}>Counterparty{sort === "cp" ? <ChevronDown className="ic up" /> : null}</button></th>
            <th>Stage</th>
            <th>Status</th>
            <th className="cdue">Due</th>
            <th><span className="sr-only">Actions</span></th>
          </tr></thead>
          <tbody>
            {visible.map((r, ri) => {
              const ch = channelOf(r); const kind = kindOf(r); const Icon = kind.Icon;
              const desc = (r.subject || r.description || "").split("\n")[0].trim() || r.type_label;
              const cp = counterpartyOf(r);
              const fs = flowSuggestion(r);
              const isNew = !(r.workflow ?? []).length && !!fs;
              return (
                <tr key={r.id} style={{ ["--ri" as string]: Math.min(ri, 12) }} onClick={() => onOpen(r.id)} onMouseLeave={hideTip}>
                  <td onMouseEnter={(e) => showTip(e, r)}>
                    <div className="req">
                      <div className={`chan k-${kind.tone}`} title={kind.label}><Icon className="ic" /></div>
                      <div style={{ minWidth: 0 }}>
                        <button type="button" className="nm" onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>{desc}</button>
                        <div className="prev"><span className="ref">{r.ref}</span> · requested by {r.requester_name ?? "—"} · {ageOf(created(r), now)} ago via {ch.label.toLowerCase()}</div>
                      </div>
                    </div>
                  </td>
                  <td onMouseEnter={(e) => showTip(e, r)}>{(() => { const [cat, kind] = agreementOf(r.type_label); return <div className="atw"><span className={`atype a-${kindTone(kind)}`}>{kind}</span>{cat && <span className="acat">{cat}</span>}</div>; })()}</td>
                  <td onMouseEnter={(e) => showTip(e, r)}>{cp ? <div className="cp">{cp}</div> : <span className="dim">—</span>}</td>
                  <td><StageCell r={r} /></td>
                  <td><div className="stbox"><div className="flags"><span className={`spill ${r.sla_status === "overdue" ? "crit" : r.sla_status === "at_risk" ? "risk" : isOpenReq(r) ? "ok" : "done"}`}>{r.sla_status === "overdue" ? "Overdue" : r.sla_status === "at_risk" ? "At risk" : isOpenReq(r) ? "On track" : "Done"}</span></div>{r.assigned_to_label ? <span className="own"><i aria-hidden>{r.assigned_to_label.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}</i><span><em>Owner</em><b>{r.assigned_to_label}</b></span></span> : <span className="own none"><i aria-hidden>?</i><span><em>Owner</em><b>Not assigned</b></span></span>}</div></td>
                  <td className="cdue">{(() => { const d = dueOf(r, created(r)); return d ? <><div className="age num">{d.date.toLocaleDateString([], { day: "numeric", month: "short" })}</div><div className={`from${d.late ? " late" : ""}`}>{d.left}</div></> : <span className="dim">—</span>; })()}</td>
                  <td><div className="rowact">
                    {mode === "pool" && !r.assigned_to_user_id ? (
                      <button className="btn pri sm" disabled={assignToMe.isPending} onClick={(e) => { e.stopPropagation(); assignToMe.mutate(r.id); }}>
                        {assignToMe.isPending && assignToMe.variables === r.id ? "Assigning…" : "Assign to me"}
                      </button>
                    ) : (
                      <button className={`btn sm${isNew ? " pri" : ""}`} onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>{isNew ? "Start" : "Open"}</button>
                    )}
                  </div></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {shown.length === 0 && <div className="empty">{rows.length === 0 ? "No requests yet. New requests land here." : "No requests match these filters."}</div>}
        {tip && (
          <div className="rtip" role="tooltip" key={tip.r.id} style={{ left: tip.x, top: tip.y }}>
            <div className="rt-top"><span className="rt-ref">{tip.r.ref}</span><span className={`atype a-${kindTone(agreementOf(tip.r.type_label)[1])}`}>{agreementOf(tip.r.type_label)[1]}</span></div>
            <b className="rt-title">{(tip.r.subject || tip.r.description || "").split("\n")[0].trim() || tip.r.type_label}</b>
            <dl className="rt-grid">
              <dt>Agreement</dt><dd>{tip.r.type_label}</dd>
              <dt>Counterparty</dt><dd>{counterpartyOf(tip.r) ?? "—"}</dd>
              <dt>Requested by</dt><dd>{tip.r.requester_name ?? "—"} · {ageOf(created(tip.r), now)} ago via {channelOf(tip.r).label.toLowerCase()}</dd>
              <dt>Owner</dt><dd>{tip.r.assigned_to_label ?? "Not assigned"}</dd>
              <dt>Due</dt><dd>{(() => { const d = dueOf(tip.r, created(tip.r)); return d ? `${d.date.toLocaleDateString([], { day: "numeric", month: "short" })} · ${d.left}` : "—"; })()}</dd>
            </dl>
          </div>
        )}
      </div>
      <div className="tfoot">
        <span className="num">{shown.length === 0 ? "0 results" : `${cur * pageSize + 1}–${Math.min((cur + 1) * pageSize, shown.length)} of ${shown.length}`}</span>
        <nav className="pager" aria-label="Pagination">
          <button type="button" className="pgb" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="Previous page"><ChevronDown className="ic prev" /></button>
          {pageNums.map((n, i) => n === -1
            ? <span key={`e${i}`} className="pge">…</span>
            : <button key={n} type="button" className={`pgb${n === cur ? " on" : ""}`} aria-current={n === cur ? "page" : undefined} onClick={() => setPage(n)}>{n + 1}</button>)}
          <button type="button" className="pgb" disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} aria-label="Next page"><ChevronDown className="ic next" /></button>
        </nav>
        <label className="rpp">Rows <select aria-label="Rows per page" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>{[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
      </div>
      </div>
      </div>
      {(
        <aside className="side" aria-label="Queue insights">
          <section className="scard attn">
            <header>
              <span className="sic crit"><AlertTriangle className="ic" /></span>
              <h3>Needs attention</h3>
              {priority.length > 4 && <button type="button" className="slink" onClick={() => { setOwnerSel(null); setStatusSel(new Set(["stuck", "at_risk"])); showTable(); }}>View all</button>}
            </header>
            {priority.length === 0 ? <p className="allclear">Nothing overdue or at risk. The queue is healthy.</p> : (
              <>
                <div className="hero"><span className="hn crit num">{overdue.length}</span><span className="hl">overdue</span>{atRisk.length > 0 && <span className="hchip">{atRisk.length} at risk</span>}</div>
                <ul className="alist">
                  {priority.slice(0, 4).map((r) => (
                    <li key={r.id} className="arow">
                      <button type="button" onClick={() => onOpen(r.id)} className="amainbtn" aria-label={`Open ${r.ref}: ${r.subject || r.type_label}`}>
                        <span className={`av av${Math.max(0, loadAll.findIndex(([n]) => n === r.assigned_to_label)) % 6}`} aria-hidden>{initials(r.assigned_to_label ?? "?")}</span>
                        <span className="amain">
                          <span className="pt" title={r.subject || r.type_label}>{r.subject || r.type_label}</span>
                          <span className="ps">{r.ref} · {r.assigned_to_label ?? "Unassigned"}</span>
                        </span>
                      </button>
                      <span className="aside2">
                        <span className={`apct ${r.sla_status === "overdue" ? "crit" : "risk"}`} title="Share of the SLA time already used">{typeof r.sla_pct === "number" ? `${Math.round(r.sla_pct)}%` : ""}</span>
                        <button type="button" className="abtn" onClick={() => onOpen(r.id)}>Open</button>
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
          <section className="scard work">
            <header>
              <span className="sic"><Users className="ic" /></span>
              <h3>Who has the work</h3>
              <span className="ssub">{loadAll.length} owners</span>
            </header>
            {load.length === 0 ? <p className="allclear">No work is in flight.</p> : (
              <ul className={`wlist${allOwners ? " scroll" : ""}`}>
                {(allOwners ? loadAll : loadAll.slice(0, 5)).map(([name, v], i) => {
                  const on = ownerSel === name;
                  return (
                    <li key={name} className={on ? "on" : ""}>
                      <span className={`av av${i % 6}`} aria-hidden>{initials(name)}</span>
                      <span className="winfo">
                        <span className="pn" title={name}>{name}</span>
                        <span className={`wsub ${v.stuck ? "crit" : v.risk ? "risk" : "ok"}`}>{v.total} in flight · {v.stuck ? `${v.stuck} overdue` : v.risk ? `${v.risk} at risk` : "on track"}</span>
                      </span>
                      <button type="button" className={`abtn${on ? " on" : ""}`} aria-pressed={on} title={on ? "Show everyone's requests" : `Show only ${name}'s requests`} onClick={() => { setStatusSel(new Set()); setOwnerSel(on ? null : name); if (!on) showTable(); }}>{on ? "Clear" : "View"}</button>
                    </li>
                  );
                })}
              </ul>
            )}
            {loadAll.length > 5 && <button type="button" className="more moreb" aria-expanded={allOwners} onClick={() => setAllOwners((o) => !o)}>{allOwners ? "Show fewer" : `+${loadAll.length - 5} more owners`}</button>}
          </section>
        </aside>
      )}
      </div>
    </div>
  );
}

// The page header + content frame for the intake screens. Navigation lives in
// the app shell's sidebar (this used to draw its own, hence the old
// `active`/`onMyWork` props, now removed).
export function MockupShell({ title, subtitle, icon, stats, actions, hideHeading, children }: {
  title: string; subtitle?: string; icon?: ReactNode; stats?: ReactNode; actions?: ReactNode; hideHeading?: boolean; children: ReactNode;
}) {
  // Breadcrumb, bell and theme live in the app's top nav; this is the page's own heading, under it.
  return (
    <div className="li-board li-shell">
      <style dangerouslySetInnerHTML={{ __html: LI_CSS }} />
      <div className="main">
        {hideHeading ? null : <div className="top">
          <div className="toprow">
            {icon ? <span className="pico" aria-hidden>{icon}</span> : null}
            <div className="ttlblk">
              <h1>{title}</h1>
              <PageCrumbs className="crumb" />
              {subtitle ? <p className="subttl">{subtitle}</p> : null}
            </div>
            <span className="sp" />
            {stats}
            {actions}
          </div>
        </div>}
        <div className={`scrollarea${hideHeading ? " nohead" : ""}`}>{children}</div>
      </div>
    </div>
  );
}

// ---------- scoped mockup CSS (Screen-1 palette, verbatim, prefixed) ----------
const LI_CSS = `
.li-board{
  --bg:#f4f6f9;
  
  
  
  --ext:#75589f;--ext-soft:#efe8f7;--ai:#3f6699;--ai-soft:#e7eef8;
  --shadow:0 1px 2px rgba(20,26,40,.05),0 10px 26px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:var(--font-sans);
  color:var(--ink);font:400 13px/1.5 var(--sans);-webkit-font-smoothing:antialiased;
}
.dark .li-board{
  --bg:#0c0f16;
  
  
  
  --ext:#ab90d2;--ext-soft:#221b31;--ai:#6f9bd6;--ai-soft:#151f2e;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 30px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55);
}
.li-board *{box-sizing:border-box}
/* :where() keeps this reset at element specificity (0,0,1): it still strips the
   browser's default button chrome, but any class (Tailwind utilities on shared
   <Button>s, the shell's own .btn/.primary) now wins. As .li-board button it
   beat single-class utilities, so primary Buttons inside the shell rendered as
   plain text. */
:where(.li-board) button{cursor:pointer;border:0;background:none;color:inherit;font:inherit}
.li-board .num{font-variant-numeric:tabular-nums} .li-board .dim{color:var(--ink-3)}
.li-board .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.li-board .sp{flex:1}

.li-board .toolbar{display:flex;align-items:center;gap:8px;margin-top:12px}
.li-board .search{display:flex;align-items:center;gap:8px;background:var(--inset);border:1px solid var(--border-strong);border-radius:8px;padding:6px 10px;min-width:240px;color:var(--ink-3)}
.li-board .search input{border:0;background:none;outline:none;color:var(--ink);width:100%;font-size:12.5px}
.li-board .dd{position:relative}
.li-board .ddbtn{display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink-2);font-weight:600;font-size:12.5px}
.li-board .ddbtn:hover{background:var(--surface-2)}
.li-board .ddbtn.active{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.li-board .ddbtn .caret{width:12px;height:12px;stroke:currentColor;stroke-width:2;fill:none}
.li-board .ddbtn .cnt{font:700 10px var(--mono);background:var(--accent);color:var(--accent-ink);border-radius:99px;padding:1px 6px}
.li-board .ddmenu{position:absolute;top:calc(100% + 5px);left:0;min-width:190px;background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:var(--pop);padding:6px;display:none;z-index:15;max-height:280px;overflow:auto}
.li-board .dd.open .ddmenu{display:block}
.li-board .ddmenu label{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:7px;font-size:12.5px;cursor:pointer;color:var(--ink)}
.li-board .ddmenu label:hover{background:var(--surface-2)}
.li-board .ddmenu input{width:15px;height:15px;accent-color:var(--accent)}
.li-board .ddmenu .dot{width:8px;height:8px;border-radius:2px;flex:none}
.li-board .sortsel{padding:7px 9px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font-size:12.5px;font-weight:600}
.li-board .clear{font-size:12px;color:var(--accent);font-weight:600;padding:6px 4px;cursor:pointer}

.li-board .brief{margin:16px 0 0;border:1px solid color-mix(in srgb,var(--ai) 22%,var(--border));border-radius:13px;background:var(--surface);box-shadow:var(--shadow);overflow:hidden}
.li-board .briefhd{display:flex;align-items:center;gap:10px;padding:11px 15px}
.li-board .aiglyph{width:28px;height:28px;border-radius:8px;background:var(--ai-soft);color:var(--ai);display:grid;place-items:center;font-size:15px}
.li-board .briefhd b{font-size:13px} .li-board .briefhd .when{color:var(--ink-3);font-size:11px}
.li-board .briefhd .tog{margin-left:auto;font-size:12px;font-weight:600;color:var(--accent);padding:5px 8px;border-radius:7px}
.li-board .briefhd .tog:hover{background:var(--surface-2)}
.li-board .brief.collapsed .briefbody{display:none}
.li-board .briefbody{padding:0 15px 15px;display:flex;flex-direction:column;gap:13px}
.li-board .oneline{display:none;padding:0 15px 12px;font-size:12.5px;color:var(--ink-2)}
.li-board .brief.collapsed .oneline{display:block} .li-board .oneline b{color:var(--ink)}
.li-board .summary{margin:0;font-size:13.5px;line-height:1.62;color:var(--ink)}
.li-board .summary b{font-weight:680} .li-board .summary .c{color:var(--crit);font-weight:680} .li-board .summary .w{color:var(--warn);font-weight:680}
.li-board .tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}
.li-board .tile{border:1px solid var(--border);border-radius:10px;padding:9px 11px;background:var(--inset)}
.li-board .tile .tn{font:700 20px/1 var(--mono);letter-spacing:-.02em;color:var(--ink)}
.li-board .tile .tl{font-size:10.5px;color:var(--ink-3);margin-top:4px}
.li-board .tile.a .tn{color:var(--accent)} .li-board .tile.g .tn{color:var(--good)} .li-board .tile.w .tn{color:var(--warn)} .li-board .tile.c .tn{color:var(--crit)}
.li-board .load{display:flex;flex-direction:column;gap:7px}
.li-board .loadhd{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
.li-board .loadgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px 22px}
.li-board .loadrow{display:grid;grid-template-columns:118px 1fr 58px;align-items:center;gap:10px}
.li-board .ldname{font-size:12px;font-weight:600;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.li-board .ldbar{height:9px;border-radius:5px;background:var(--surface-2);overflow:hidden;display:flex}
.li-board .ldfill{background:var(--good);height:100%} .li-board .ldstuck{background:var(--crit);height:100%}
.li-board .ldn{font:600 11px var(--mono);color:var(--ink-2);text-align:right} .li-board .ldn .s{color:var(--crit)}
.li-board .attention{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.li-board .attlbl{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
.li-board .attchip{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:99px;border:1px solid var(--border-strong);background:var(--surface);font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}
.li-board .attchip:hover{filter:brightness(.98);background:var(--surface-2)}
.li-board .attchip.c{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit);background:var(--crit-soft)}
.li-board .attchip.w{border-color:color-mix(in srgb,var(--warn) 40%,var(--border));color:var(--warn);background:var(--warn-soft)}
@media (max-width:1100px){.li-board .tiles{grid-template-columns:repeat(3,1fr)}.li-board .loadgrid{grid-template-columns:1fr}}

.li-board .tablewrap{margin-top:12px;border:1px solid var(--border);border-radius:12px;overflow-x:auto;background:var(--surface);box-shadow:var(--shadow)}
.li-board table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.li-board thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:9px 12px;border-bottom:1px solid var(--border);white-space:nowrap;background:var(--inset)}
.li-board tbody td{padding:9px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
.li-board thead th{padding-left:10px;padding-right:10px}
.li-board tbody tr:last-child td{border-bottom:0}
.li-board tbody tr:hover td{background:var(--inset)}
.li-board td.c-check,.li-board th.c-check{width:34px;padding-left:16px;padding-right:2px}
.li-board .cbx{width:15px;height:15px;accent-color:var(--accent);cursor:pointer;vertical-align:middle}
.li-board td.c-idx,.li-board th.c-idx{width:30px;text-align:right;padding-right:6px}
.li-board .idx{font:600 11px var(--mono);color:var(--ink-3)}
.li-board .req{display:flex;gap:10px;align-items:flex-start;min-width:0}
.li-board .chan{width:30px;height:30px;border-radius:8px;flex:none;display:grid;place-items:center;background:var(--surface-2);color:var(--ink-2)}
.li-board .req .nm{font-weight:650;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:300px;display:inline-block;vertical-align:bottom}
.li-board .ref{font:600 10.5px var(--mono);color:var(--ink-3);flex:none}
.li-board .prev{color:var(--ink-3);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px;margin-top:1px}
.li-board .cp{font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px} .li-board .ty{color:var(--ink-2);font-size:11.5px}
.li-board .from{max-width:130px}
.li-board .wf{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border-radius:6px;background:var(--accent-soft);color:var(--accent);font-weight:600;font-size:11px;white-space:nowrap}
.li-board .wf.msa{background:var(--ext-soft);color:var(--ext)}
.li-board .confbar{display:flex;align-items:center;gap:6px;margin-top:4px}
.li-board .track{width:74px;height:5px;border-radius:3px;background:var(--surface-2);overflow:hidden;flex:none}
.li-board .fill{height:100%;border-radius:3px} .li-board .pct{font:600 10.5px var(--mono);color:var(--ink-3)}
.li-board .stagewrap{min-width:150px}
.li-board .recmark{display:inline-flex;align-items:center;gap:5px;font:600 10px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)}
.li-board .stagehead{display:flex;align-items:center;gap:7px}
.li-board .sdot{width:8px;height:8px;border-radius:50%;flex:none} .li-board .sdot.ok{background:var(--good)} .li-board .sdot.stuck{background:var(--crit)}
.li-board .stagenm{font-weight:660;color:var(--ink);font-size:13px;letter-spacing:-.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .stepno{margin-left:auto;font:600 11px var(--mono);color:var(--ink-2)}
.li-board .stepbar{display:flex;gap:2px;margin-top:7px;max-width:190px}
.li-board .seg{height:7px;flex:1 1 0;min-width:3px;border-radius:2px;background:var(--surface-2)}
.li-board .seg.done{background:color-mix(in srgb,var(--good) 42%,transparent)} .li-board .seg.cur{background:var(--good)}
.li-board .seg.done.st{background:color-mix(in srgb,var(--crit) 42%,transparent)} .li-board .seg.cur.st{background:var(--crit)}
.li-board .wflabel{margin-top:6px;font-size:10.5px;color:var(--ink-3)} .li-board .stucktag{color:var(--crit);font-weight:700}
.li-board .flags{display:flex;gap:5px;flex-wrap:wrap}
.li-board .pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;font:600 10.5px var(--sans);white-space:nowrap}
.li-board .pill.new{background:var(--accent-soft);color:var(--accent)}
.li-board .pill.miss,.li-board .pill.low{background:var(--warn-soft);color:var(--warn)}
.li-board .pill.dupe{background:var(--surface-2);color:var(--ink-2)}
.li-board .pill.risk{background:var(--crit-soft);color:var(--crit)} .li-board .pill.ok{background:var(--good-soft);color:var(--good)}
.li-board .from{font-size:11.5px;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .li-board .age{font-size:11px;color:var(--ink-3);margin-top:1px}
.li-board .rowact{display:flex;gap:6px;justify-content:flex-end;white-space:nowrap}
.li-board .btn{padding:6px 11px;border-radius:8px;font-size:12px}

 
.li-board .btn.sm{padding:5px 8px}
.li-board .empty{padding:60px;text-align:center;color:var(--ink-3)}

/* ---- full mockup shell (sidebar + topbar) ---- */
.li-shell{display:grid;grid-template-columns:1fr;height:100%;background:var(--bg);color:var(--ink);font:400 13px/1.5 var(--sans)}
.li-shell *{box-sizing:border-box}
.li-shell .rail{background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0}
.li-shell .brand{display:flex;align-items:center;gap:9px;padding:14px 16px 12px;border-bottom:1px solid var(--border)}
.li-shell .brand .mark{width:26px;height:26px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700}
.li-shell .brand b{font-size:15px;letter-spacing:-.02em}
.li-shell .nav{padding:8px;display:flex;flex-direction:column;gap:1px;overflow:auto;flex:1}
.li-shell .nav .sec{padding:11px 8px 4px;font:600 10px/1.4 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.li-shell .nav a{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;color:var(--ink-2);font-weight:500;text-decoration:none;cursor:pointer}
.li-shell .nav a:hover{background:var(--surface-2);color:var(--ink)}
.li-shell .nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.li-shell .nav a .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.li-shell .nav a .ct{margin-left:auto;font:600 11px var(--mono);color:var(--ink-3)} .li-shell .nav a.on .ct{color:var(--accent)}
.li-shell .me{border-top:1px solid var(--border);padding:10px 13px;display:flex;align-items:center;gap:9px}
.li-shell .me .av{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:12px}
.li-shell .me .nm{font-weight:600;font-size:12.5px} .li-shell .me .rl{color:var(--ink-3);font-size:11px}
.li-shell .main{min-width:0;display:flex;flex-direction:column;min-height:0}
.li-shell .top{flex:none;border-bottom:1px solid var(--border);background:var(--surface);padding:12px 22px}
.li-shell .toprow{display:flex;align-items:center;gap:12px}
.li-shell .top h1{font-size:16px;font-weight:660;letter-spacing:-.015em;margin:0}
.li-shell .top .subttl{color:var(--ink-3);font-weight:500;font-size:12.5px}
.li-shell .sp{flex:1}
.li-shell .stat{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--ink-2)}
.li-shell .stat b{color:var(--ink);font-weight:650} .li-shell .stat .warn{color:var(--warn)} .li-shell .stat .crit{color:var(--crit)}
.li-shell .iconbtn{width:32px;height:32px;border-radius:8px;display:grid;place-items:center;color:var(--ink-2);cursor:pointer;border:0;background:none} .li-shell .iconbtn:hover{background:var(--surface-2)}
.li-shell .iconbtn .ic{width:15px;height:15px;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.li-shell .primary{display:inline-flex;align-items:center;gap:7px;padding:8px 13px;border-radius:9px;background:var(--accent);color:var(--accent-ink);font-weight:600;font-size:12.5px;border:0;cursor:pointer}
.li-shell .primary:hover{filter:brightness(1.06)}
.li-shell .primary .ic{width:15px;height:15px;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round;stroke-linejoin:round}
.li-shell .scrollarea{flex:1;min-height:0;overflow:auto;padding:0 22px 26px}
.li-shell .scrollarea .brief{margin-top:14px}

/* ---- premium pass (overrides the mockup port above) ---- */
.li-board .ov{margin-top:18px}
.li-board .ovhd,.li-board .panelhd{display:flex;align-items:center;justify-content:space-between;gap:10px}
.li-board .ovhd h2{font:600 11px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3);margin:0}
.li-board .tog,.li-board .link{font-size:12px;font-weight:600;color:var(--accent);padding:3px 6px;border-radius:6px}
.li-board .tog:hover,.li-board .link:hover{background:var(--accent-soft)}
.li-board .tiles{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:8px}
.li-board .tile{display:flex;flex-direction:column;gap:6px;text-align:left;border:1px solid var(--border);border-radius:10px;padding:12px 14px;background:var(--surface);transition:border-color .12s,box-shadow .12s}
.li-board .tile:hover{border-color:var(--border-strong);box-shadow:0 1px 3px rgba(20,26,40,.06)}
.li-board .tile.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent);background:var(--accent-soft)}
.li-board .tile .tl{font-size:12px;font-weight:500;color:var(--ink-2)}
.li-board .tile .tn{font:600 26px/1 var(--sans);letter-spacing:-.025em;color:var(--ink);font-variant-numeric:tabular-nums}
.li-board .tile.w .tn{color:var(--warn)} .li-board .tile.c .tn{color:var(--crit)} .li-board .tile.a .tn{color:var(--accent)}
.li-board .ovgrid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:12px;margin-top:12px}
.li-board .panel{border:1px solid var(--border);border-radius:12px;background:var(--surface);padding:14px 16px;min-width:0}
.li-board .panelhd h3{margin:0;font-size:13px;font-weight:600}
.li-board .legend{display:inline-flex;align-items:center;font-size:11px;color:var(--ink-3)}
.li-board .lg{display:inline-block;width:8px;height:8px;border-radius:2px;margin:0 5px 0 10px}
.li-board .lg.ok,.li-board .ldbar .ok{background:var(--accent)} .li-board .lg.risk,.li-board .ldbar .risk{background:var(--warn)} .li-board .lg.crit,.li-board .ldbar .crit{background:var(--crit)}
.li-board .loadgrid{list-style:none;margin:12px 0 0;padding:0;display:flex;flex-direction:column;gap:9px}
.li-board .loadrow{display:grid;grid-template-columns:130px minmax(0,1fr) auto;align-items:center;gap:12px}
.li-board .loadrow .ldbar{height:8px;border-radius:4px;overflow:hidden;display:flex;min-width:6px;background:none}
.li-board .ldn{font:500 12px var(--sans);color:var(--ink-2);text-align:right;white-space:nowrap}
.li-board .more{margin:8px 0 0;font-size:11.5px;color:var(--ink-3)}
.li-board .allclear{margin:12px 0 0;font-size:12.5px;color:var(--ink-3);padding:10px 12px;border-radius:8px;background:var(--inset)}
.li-board .prio{list-style:none;margin:8px -8px 0;padding:0}
.li-board .prow{display:flex;align-items:center;gap:10px;width:100%;text-align:left;padding:8px;border-radius:8px}
.li-board .prow:hover{background:var(--surface-2)}
.li-board .pdot{width:8px;height:8px;border-radius:50%;flex:none} .li-board .pdot.crit{background:var(--crit)} .li-board .pdot.risk{background:var(--warn)}
.li-board .pmain{min-width:0;flex:1;display:flex;flex-direction:column}
.li-board .pt{font-size:12.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .ps{font-size:11.5px;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .toolbar{margin-top:20px;flex-wrap:wrap}
.li-board .search{background:var(--surface);min-width:280px;height:34px;padding-top:0;padding-bottom:0;transition:border-color .12s,box-shadow .12s}
.li-board .search:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.li-board .ddbtn,.li-board .sortsel{height:34px;font-weight:500}
.li-board .count{font-size:12px}
.li-board .chips{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:10px}
.li-board .chip{display:inline-flex;align-items:center;gap:4px;padding:2px 4px 2px 10px;border-radius:99px;background:var(--accent-soft);color:var(--accent);font-size:12px;font-weight:600}
.li-board .chip button{display:grid;place-items:center;width:18px;height:18px;border-radius:50%} .li-board .chip button:hover{background:var(--accent);color:var(--accent-ink)}
.li-board .chip .ic{width:11px;height:11px;stroke-width:2.2}
.li-board .clear{padding:2px 6px}
.li-board .selbar{display:flex;align-items:center;gap:12px;margin-top:10px;padding:7px 12px;border-radius:10px;background:var(--accent-soft);color:var(--accent);font-size:12.5px}
.li-board .tablewrap{margin-top:12px;box-shadow:none;overflow:auto}
.li-board thead th{position:sticky;top:0;z-index:2;padding:10px 12px}
.li-board tbody td{padding:12px}
.li-board tbody tr{transition:background .1s}
.li-board tbody tr.sel td{background:var(--accent-soft)}
.li-board .req .nm{display:block;font-weight:600;font-size:13px;text-align:left;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .req .nm:hover{color:var(--accent)}
.li-board .prev{max-width:320px;margin-top:2px}
.li-board .ref{font:500 11px var(--mono);color:var(--ink-3)}
.li-board .chan{width:28px;height:28px}
.li-board .stagewrap{min-width:150px;max-width:200px}
.li-board .stepbar{display:block;height:4px;border-radius:2px;background:var(--surface-2);margin-top:7px;overflow:hidden;max-width:none}
.li-board .stepbar span{display:block;height:100%;border-radius:2px;background:var(--accent)} .li-board .stepbar span.st{background:var(--crit)}
.li-board .stepno{font:500 11.5px var(--sans);color:var(--ink-3);font-variant-numeric:tabular-nums}
.li-board .stagenm{font-weight:600;font-size:12.5px}
.li-board .owner{font-size:12.5px;color:var(--ink);display:inline-block;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}
.li-board .flags{display:flex;flex-direction:column;gap:3px}
.li-board .flag{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;color:var(--ink-2);white-space:nowrap}
.li-board .flag i{width:6px;height:6px;border-radius:50%;background:var(--ink-3)}
.li-board .flag.risk{color:var(--crit)} .li-board .flag.risk i{background:var(--crit)}
.li-board .flag.low{color:var(--warn)} .li-board .flag.low i{background:var(--warn)}
.li-board .age{font-size:12.5px;color:var(--ink)} .li-board .from{font-size:11.5px;color:var(--ink-3);text-transform:capitalize}
.li-board .btn{border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;transition:background .12s}
.li-board .btn:hover{background:var(--surface-2)}
.li-board .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .li-board .btn.pri:hover{filter:brightness(1.08)}
.li-board .btn:disabled{opacity:.55;pointer-events:none}
.li-board .morewrap{display:flex;justify-content:center;margin-top:12px}
.li-board .empty{padding:48px 24px;font-size:13px}
.li-shell .top{padding:18px 28px 16px}
.li-shell .ttlblk h1{font-size:20px;font-weight:650;letter-spacing:-.02em;line-height:1.25}
.li-shell .ttlblk .subttl{margin:2px 0 0;font-size:13px;color:var(--ink-3);font-weight:400}
.li-shell .scrollarea{padding:0 28px 32px}
.li-shell .primary{height:34px;padding:0 14px;box-shadow:inset 0 1px 0 rgba(255,255,255,.14),0 1px 2px rgba(20,26,40,.14)}
.li-shell .primary:disabled{opacity:.5;cursor:not-allowed}
.li-shell .iconbtn{width:34px;height:34px}
.li-board button:focus-visible,.li-board .cbx:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (max-width:1100px){.li-board .tiles{grid-template-columns:repeat(3,minmax(0,1fr))}.li-board .ovgrid{grid-template-columns:1fr}}
@media (max-width:640px){.li-board .tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.li-board .search{min-width:0;flex:1 1 100%}.li-shell .top{padding:14px 16px}.li-shell .scrollarea{padding:0 16px 24px}.li-board .loadrow{grid-template-columns:90px minmax(0,1fr) auto}}

/* ---- colour pass: status tabs, people, tinted rows (replaces tiles/bars/panels) ---- */
.li-board{--c-ind:#4f5bd5;--c-vio:#8b5cf6;--c-emr:#10a37f;--c-amb:#d98a0b;--c-red:#d6453d;--c-sky:#1d8fd6}
.dark .li-board{--c-ind:#8290e6;--c-vio:#a78bfa;--c-emr:#4fc79d;--c-amb:#e3a64a;--c-red:#ee7a6e;--c-sky:#5db4ee}
.li-board .stabs{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:14px}
.li-board .stab{--c:var(--c-ind);display:inline-flex;align-items:center;gap:8px;padding:7px 12px;border-radius:99px;background:color-mix(in srgb,var(--c) 11%,transparent);color:var(--ink);font-size:13px;font-weight:500;transition:background .12s,box-shadow .12s}
.li-board .stab i{width:8px;height:8px;border-radius:50%;background:var(--c)}
.li-board .stab .tn{font:700 13px var(--sans);color:var(--c);font-variant-numeric:tabular-nums}
.li-board .stab:hover{background:color-mix(in srgb,var(--c) 20%,transparent)}
.li-board .stab.on{background:var(--c);color:#fff;box-shadow:0 2px 8px color-mix(in srgb,var(--c) 38%,transparent)}
.li-board .stab.on i{background:#fff} .li-board .stab.on .tn{color:#fff}
.li-board .stab.a{--c:var(--c-vio)} .li-board .stab.w{--c:var(--c-amb)} .li-board .stab.c{--c:var(--c-red)}
.li-board .stab.g{--c:var(--c-emr)}
.li-board .stabs .tog{margin-left:auto}
.li-board .ovgrid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px;margin-top:18px;padding:0 2px}
.li-board .people h3,.li-board .attn h3{margin:0 0 8px;font:600 11px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)}
.li-board .people ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:8px}
.li-board .person{display:inline-flex;align-items:center;gap:8px;padding:4px 12px 4px 4px;border-radius:99px;background:var(--surface);border:1px solid var(--border)}
.li-board .av{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font:700 10.5px var(--sans);color:#fff}
.li-board .av0{background:var(--c-ind)} .li-board .av1{background:var(--c-vio)} .li-board .av2{background:var(--c-emr)} .li-board .av3{background:var(--c-amb)} .li-board .av4{background:var(--c-sky)} .li-board .av5{background:var(--c-red)}
.li-board .pn{font-size:12.5px;font-weight:600;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .pc{font-size:12px;font-weight:700;color:var(--ink-2)}
.li-board .po,.li-board .pr{font-size:11px;font-weight:600;padding:1px 7px;border-radius:99px}
.li-board .po{background:color-mix(in srgb,var(--c-red) 14%,transparent);color:var(--c-red)} .li-board .pr{background:color-mix(in srgb,var(--c-amb) 16%,transparent);color:var(--c-amb)}
.li-board .attn .panelhd{margin-bottom:0}
.li-board .attn .prio{margin:0;display:flex;flex-direction:column;gap:6px}
.li-board .prow{padding:9px 12px;border-radius:10px;border-left:3px solid var(--c-red);background:color-mix(in srgb,var(--c-red) 8%,transparent)}
.li-board .prow.risk{border-left-color:var(--c-amb);background:color-mix(in srgb,var(--c-amb) 10%,transparent)}
.li-board .prow:hover{background:color-mix(in srgb,var(--c-red) 15%,transparent)} .li-board .prow.risk:hover{background:color-mix(in srgb,var(--c-amb) 18%,transparent)}
.li-board .allclear{background:color-mix(in srgb,var(--c-emr) 10%,transparent);color:var(--c-emr);font-weight:500;margin:0}
.li-board .more{margin:8px 0 0}
.li-board .sdot{background:var(--c-ind)} .li-board .sdot.stuck{background:var(--c-red)}
.li-board .sdot.stg-drafting{background:var(--c-vio)} .li-board .sdot.stg-review{background:var(--c-ind)} .li-board .sdot.stg-approval{background:var(--c-amb)} .li-board .sdot.stg-signature{background:var(--c-emr)} .li-board .sdot.stg-active{background:var(--c-sky)}
.li-board .stepbar span{background:var(--c-ind)} .li-board .stepbar span.st{background:var(--c-red)}
.li-board .chan{background:color-mix(in srgb,var(--c-ind) 12%,transparent);color:var(--c-ind)}
.li-board .ty{display:inline-block;margin-top:2px;padding:0 7px;border-radius:99px;background:color-mix(in srgb,var(--c-vio) 13%,transparent);color:var(--c-vio);font-size:11px;font-weight:600}
.li-board thead th{background:transparent;border-bottom:1px solid var(--border-strong)}
.li-board .tablewrap{border:0;background:none;border-radius:0;overflow:visible}
.li-board table{background:none}
.li-board tbody td{background:var(--surface)} .li-board tbody tr:hover td{background:color-mix(in srgb,var(--c-ind) 5%,var(--surface))}
@media (max-width:900px){.li-board .ovgrid{grid-template-columns:1fr}}

/* ---- enterprise grid ---- */
.li-board .tablewrap{border:1px solid var(--border);border-radius:12px 12px 0 0;background:var(--surface);overflow:auto;max-height:calc(100vh - 230px);margin-top:14px}
.li-board thead th{background:var(--inset);border-bottom:1px solid var(--border-strong);padding:9px 12px;font-size:10.5px}
.li-board tbody td{padding:0 12px;height:56px;background:var(--surface);border-bottom:1px solid var(--border)}
.li-board tbody tr:last-child td{border-bottom:0}
.li-board tbody tr:nth-child(even) td{background:color-mix(in srgb,var(--inset) 55%,var(--surface))}
.li-board tbody tr:hover td{background:color-mix(in srgb,var(--c-ind) 7%,var(--surface))}
.li-board tbody tr.sel td{background:var(--accent-soft)}
.li-board td.c-check,.li-board th.c-check{width:40px;padding-left:14px}
.li-board .sorth{display:inline-flex;align-items:center;gap:4px;font:inherit;letter-spacing:inherit;text-transform:inherit;color:inherit}
.li-board .sorth:hover{color:var(--ink)}
.li-board .sorth .ic{width:12px;height:12px;stroke-width:2.2} .li-board .sorth .up{transform:rotate(180deg)}
.li-board .flags{flex-direction:row;align-items:center;flex-wrap:wrap;gap:6px}
.li-board .spill{display:inline-flex;align-items:center;padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;white-space:nowrap}
.li-board .spill.ok{background:color-mix(in srgb,var(--c-emr) 14%,transparent);color:var(--c-emr)}
.li-board .spill.risk{background:color-mix(in srgb,var(--c-amb) 16%,transparent);color:var(--c-amb)}
.li-board .spill.crit{background:color-mix(in srgb,var(--c-red) 14%,transparent);color:var(--c-red)}
.li-board .spill.done{background:var(--surface-2);color:var(--ink-2)}
.li-board .rowact .btn{opacity:.85} .li-board tr:hover .rowact .btn{opacity:1}
.li-board .tfoot{display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border:1px solid var(--border);border-top:0;border-radius:0 0 12px 12px;background:var(--inset);font-size:12px;color:var(--ink-3)}
.li-board .morewrap{margin:0;display:inline}
.li-board .ty{white-space:nowrap;max-width:170px;overflow:hidden;text-overflow:ellipsis;vertical-align:top}
.li-board .req .nm,.li-board .prev{max-width:250px}
.li-board .stagewrap{min-width:130px;max-width:170px}
.li-board .cp{max-width:170px}

/* ---- alignment pass: two equal columns, equal-height rows ---- */
.li-board .ovgrid{align-items:start;gap:32px}
.li-board .people h3,.li-board .attn .panelhd{display:flex;align-items:center;justify-content:space-between;height:28px;margin:0 0 8px}
.li-board .attn .panelhd h3{margin:0}
.li-board .people ul{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.li-board .person{display:flex;align-items:center;gap:10px;height:52px;padding:0 12px;border-radius:10px;background:var(--surface);border:1px solid var(--border)}
.li-board .pn{flex:1;min-width:0;max-width:none}
.li-board .pc{width:18px;text-align:right}
.li-board .po,.li-board .pr{min-width:72px;text-align:center;padding:2px 0}
.li-board .attn .prio{gap:8px}
.li-board .prow{height:52px;border-left:0;border-radius:10px;box-shadow:inset 3px 0 0 var(--c-red);padding:0 14px 0 16px}
.li-board .prow.risk{box-shadow:inset 3px 0 0 var(--c-amb)}
.li-board .ovgrid .more{margin:8px 0 0}
@media (max-width:900px){.li-board .people ul{grid-template-columns:1fr}}
@media (max-width:1100px){.li-board .ovgrid{grid-template-columns:1fr}}

/* ---- stage cell: phase chip, full step name, stepper ---- */
.li-board .stagewrap{min-width:170px;max-width:230px}
.li-board .stagetop{display:flex;align-items:center;justify-content:space-between;gap:8px}
.li-board .phase{padding:1px 8px;border-radius:99px;font-size:11px;font-weight:700;letter-spacing:.01em;background:color-mix(in srgb,var(--c-ind) 14%,transparent);color:var(--c-ind)}
.li-board .phase.stg-drafting{background:color-mix(in srgb,var(--c-vio) 14%,transparent);color:var(--c-vio)}
.li-board .phase.stg-approval{background:color-mix(in srgb,var(--c-amb) 16%,transparent);color:var(--c-amb)}
.li-board .phase.stg-signature{background:color-mix(in srgb,var(--c-emr) 14%,transparent);color:var(--c-emr)}
.li-board .phase.stg-active,.li-board .phase.stg-done{background:color-mix(in srgb,var(--c-sky) 14%,transparent);color:var(--c-sky)}
.li-board .stagewrap .stagenm{display:block;margin-top:3px;font-size:12.5px;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .steps{display:flex;gap:3px;margin-top:5px}
.li-board .steps span{flex:1;height:4px;border-radius:2px;background:var(--surface-2)}
.li-board .steps .d{background:var(--c-ind)} .li-board .steps .c{background:color-mix(in srgb,var(--c-ind) 45%,var(--surface-2))}

/* ---- connected stepper ---- */
.li-board .steps{display:flex;align-items:center;gap:0;margin-top:7px}
.li-board .steps span{all:unset}
.li-board .steps .nd{flex:none;position:relative;width:12px;height:12px;border-radius:50%;box-sizing:border-box;border:2px solid var(--border-strong);background:var(--surface)}
.li-board .steps .nd.d{border-color:var(--c-ind);background:var(--c-ind)}
.li-board .steps .nd.d::after{content:"";position:absolute;left:3px;top:0.5px;width:3px;height:6px;border:solid #fff;border-width:0 1.6px 1.6px 0;transform:rotate(45deg)}
.li-board .steps .nd.c{border-color:var(--c-ind);background:var(--surface);box-shadow:0 0 0 3px color-mix(in srgb,var(--c-ind) 20%,transparent)}
.li-board .steps .nd.c::after{content:"";position:absolute;inset:2px;border-radius:50%;background:var(--c-ind)}
.li-board .steps .ln{flex:1;height:2px;min-width:6px;background:var(--border-strong)}
.li-board .steps .ln.d{background:var(--c-ind)}
.li-board tbody td{height:68px;padding-top:8px;padding-bottom:8px}

/* ---- split layout: table left, insight cards right ---- */
.li-board .split{display:grid;grid-template-columns:minmax(0,1fr) 316px;gap:20px;align-items:start;margin-top:14px}
.li-board .split.solo{grid-template-columns:minmax(0,1fr)}
.li-board .maincol{min-width:0}
.li-board .side{position:sticky;top:0;display:flex;flex-direction:column;gap:14px;min-width:0}
.li-board .scard{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px;box-shadow:0 1px 2px rgba(20,26,40,.04)}
.li-board .scard header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.li-board .scard h3{margin:0;flex:1;font:650 13.5px var(--sans);text-transform:none;letter-spacing:-.005em;color:var(--ink)}
.li-board .sic{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;background:color-mix(in srgb,var(--c-ind) 14%,transparent);color:var(--c-ind)}
.li-board .sic.crit{background:color-mix(in srgb,var(--c-red) 14%,transparent);color:var(--c-red)}
.li-board .scount{min-width:24px;text-align:center;padding:1px 8px;border-radius:99px;font:700 11.5px var(--sans);background:color-mix(in srgb,var(--c-ind) 14%,transparent);color:var(--c-ind)}
.li-board .scount.crit{background:color-mix(in srgb,var(--c-red) 14%,transparent);color:var(--c-red)}
.li-board .scard .prio{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.li-board .scard .prow{display:flex;flex-direction:column;align-items:flex-start;gap:1px;height:auto;padding:9px 12px 9px 14px;width:100%;text-align:left}
.li-board .scard .pt,.li-board .scard .ps{max-width:100%}
.li-board .slink{display:block;margin:10px 0 0;font-size:12.5px;font-weight:600;color:var(--accent);padding:4px 2px}
.li-board .scard .people{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.li-board .scard .person{display:flex;align-items:center;gap:10px;height:auto;padding:8px 6px;border:0;border-radius:10px;background:none}
.li-board .scard .person:hover{background:var(--surface-2)}
.li-board .pinfo{flex:1;min-width:0;display:flex;flex-direction:column}
.li-board .scard .pn{font-size:12.5px;font-weight:600;max-width:100%}
.li-board .scard .pc{width:auto;text-align:left;font-size:11.5px;font-weight:400;color:var(--ink-3)}
.li-board .scard .po,.li-board .scard .pr,.li-board .pk{min-width:0;padding:2px 9px;white-space:nowrap}
.li-board .pk{border-radius:99px;font-size:11px;font-weight:600;background:color-mix(in srgb,var(--c-emr) 14%,transparent);color:var(--c-emr)}
.li-board .scard .allclear{margin:0}
@media (max-width:1180px){.li-board .split>.side{margin-top:0}.li-board .split{grid-template-columns:minmax(0,1fr)}.li-board .side{position:static;order:-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.li-board .side{grid-template-columns:1fr}}

/* ---- UX pass: calmer side cards, fewer table columns (fits without sideways scroll) ---- */
.li-board .split{grid-template-columns:minmax(0,1fr) 288px;gap:18px}
.li-board .scard{padding:14px 14px 12px}
.li-board .scard header{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:6px}
.li-board .scard h3{flex:none;font:650 13.5px var(--sans)}
.li-board .ssub{font-size:11.5px;color:var(--ink-3)} .li-board .ssub b{font-weight:700} .li-board .ssub .crit{color:var(--c-red)} .li-board .ssub .warn{color:var(--c-amb)}
.li-board .alist,.li-board .wlist{list-style:none;margin:0;padding:0}
.li-board .alist li+li,.li-board .wlist li+li{border-top:1px solid var(--border)}
.li-board .arow{display:flex;align-items:center;gap:10px;width:100%;text-align:left;padding:9px 4px;border-radius:8px}
.li-board .arow:hover{background:var(--surface-2)}
.li-board .arow i{width:8px;height:8px;border-radius:50%;flex:none;background:var(--c-red)} .li-board .arow i.risk{background:var(--c-amb)}
.li-board .amain{flex:1;min-width:0;display:flex;flex-direction:column}
.li-board .arow .pt{font-size:12.5px;font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .arow .ps{font-size:11.5px;color:var(--ink-3);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .apct{font:700 12px var(--sans);font-variant-numeric:tabular-nums} .li-board .apct.crit{color:var(--c-red)} .li-board .apct.risk{color:var(--c-amb)}
.li-board .wlist li{display:flex;align-items:center;gap:9px;padding:8px 4px}
.li-board .wlist .av{width:24px;height:24px;font-size:10px}
.li-board .wlist .pn{flex:1;min-width:0;font-size:12.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:none}
.li-board .wn{font:700 12.5px var(--sans);color:var(--ink)}
.li-board .wlate{min-width:44px;text-align:center;padding:1px 7px;border-radius:99px;font-size:11px;font-weight:600;background:color-mix(in srgb,var(--c-red) 14%,transparent);color:var(--c-red)}
.li-board .wlate.ok{background:color-mix(in srgb,var(--c-emr) 14%,transparent);color:var(--c-emr)}
.li-board .slink{margin-top:8px}
/* table: owner joins status, received joins the request line */
.li-board table{table-layout:auto}
.li-board .stagewrap{min-width:0;max-width:none;width:100%}
.li-board .stbox{display:flex;flex-direction:column;gap:4px;align-items:flex-start}
.li-board .stbox .owner{font-size:12px;color:var(--ink-2);max-width:150px}
.li-board .tablewrap{overflow-x:auto}
.li-board .req .nm,.li-board .prev{max-width:300px}
@media (min-width:1500px){.li-board .req .nm,.li-board .prev{max-width:380px}}

/* fixed layout: every column always visible, long text truncates */
.li-board table{table-layout:fixed;min-width:760px}
.li-board th.c-check{width:40px}
.li-board thead th:nth-child(2){width:31%} .li-board thead th:nth-child(3){width:17%} .li-board thead th:nth-child(4){width:23%} .li-board thead th:nth-child(5){width:15%} .li-board thead th:nth-child(6){width:84px}
.li-board .req{min-width:0} .li-board .req>div:last-child{flex:1;min-width:0}
.li-board .req .nm,.li-board .prev{max-width:100%;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .cp,.li-board .ty,.li-board .stbox .owner{max-width:100%}
.li-board .rowact{justify-content:flex-end}
@media (min-width:1500px){.li-board .req .nm,.li-board .prev{max-width:100%}}

/* ---- stat cards + toolbar ---- */
.li-board .stabs{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}
.li-board .stab{--c:var(--c-ind);display:flex;align-items:center;gap:12px;padding:14px;border-radius:14px;background:var(--surface);border:1px solid var(--border);box-shadow:0 1px 2px rgba(20,26,40,.04);text-align:left;transition:border-color .12s,box-shadow .12s,transform .12s}
.li-board .stab:hover{background:var(--surface);border-color:color-mix(in srgb,var(--c) 45%,var(--border));box-shadow:0 4px 14px color-mix(in srgb,var(--c) 14%,transparent);transform:translateY(-1px)}
.li-board .stab.on{background:color-mix(in srgb,var(--c) 8%,var(--surface));border-color:var(--c);box-shadow:0 0 0 1px var(--c),0 4px 14px color-mix(in srgb,var(--c) 18%,transparent);color:var(--ink)}
.li-board .sico{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;flex:none;background:color-mix(in srgb,var(--c) 14%,transparent);color:var(--c)}
.li-board .stab.on .sico{background:var(--c);color:#fff}
.li-board .sico .ic{width:18px;height:18px}
.li-board .stxt{display:flex;flex-direction:column;min-width:0}
.li-board .stab .tn{font:700 22px/1.1 var(--sans);letter-spacing:-.02em;color:var(--ink)}
.li-board .stab.on .tn{color:var(--c)}
.li-board .stab .tl{font-size:12px;font-weight:500;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.li-board .stab.on .tn{color:var(--c)}
.li-board .stab i{display:none}
.li-board .toolbar{margin-top:16px;gap:10px}
.li-board .search{flex:1 1 260px;min-width:220px;height:38px;border-radius:10px}
.li-board .ddbtn,.li-board .sortsel{height:38px;border-radius:10px;padding:0 12px;font-size:13px;font-weight:500;color:var(--ink)}
.li-board .sortsel{appearance:none;-webkit-appearance:none;padding-right:32px;background:var(--surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238a92a6' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E") no-repeat right 11px center}
.li-board .sp{display:none}
.li-board .count{margin-left:auto}
@media (max-width:1280px){.li-board .stabs{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.li-board .stabs{grid-template-columns:repeat(2,minmax(0,1fr))}}
.li-board .stab{flex-direction:column;align-items:flex-start;gap:10px;padding:12px 14px}
.li-board .sico{width:30px;height:30px;border-radius:9px} .li-board .sico .ic{width:16px;height:16px}
.li-board .stxt{width:100%}

/* ---- underline tabs (final) ---- */
.li-board .stabs{display:flex;flex-wrap:wrap;gap:4px 24px;border-bottom:1px solid var(--border);margin-top:0}
.li-board .stab{--c:var(--c-ind);display:inline-flex;flex-direction:row;align-items:center;gap:8px;padding:10px 2px 11px;margin-bottom:-1px;border:0;border-bottom:2px solid transparent;border-radius:0;background:none;box-shadow:none;transform:none;color:var(--ink-2);font-size:13.5px;font-weight:500}
.li-board .stab:hover{background:none;border-color:var(--border-strong);box-shadow:none;transform:none;color:var(--ink)}
.li-board .stab.on{background:none;box-shadow:none;border-color:var(--c);color:var(--ink);font-weight:650}
.li-board .stab .tl{font-size:inherit;font-weight:inherit;color:inherit}
.li-board .stab .tn{font:700 11.5px var(--sans);min-width:22px;text-align:center;padding:1px 8px;border-radius:99px;background:color-mix(in srgb,var(--c) 13%,transparent);color:var(--c)}
.li-board .stab.on .tn{background:var(--c);color:#fff}
.li-board .toolbar{margin-top:14px}

/* ---- one unified filter bar; no checkbox column ---- */
.li-board thead th:nth-child(1){width:34%} .li-board thead th:nth-child(2){width:18%} .li-board thead th:nth-child(3){width:24%} .li-board thead th:nth-child(4){width:16%} .li-board thead th:nth-child(5){width:84px}
.li-board thead th:first-child,.li-board tbody td:first-child{padding-left:16px}
.li-board .toolbar{display:flex;align-items:center;gap:0;margin-top:14px;padding:0 6px 0 0;height:46px;background:var(--surface);border:1px solid var(--border);border-radius:12px;flex-wrap:nowrap}
.li-board .toolbar .search{flex:1 1 220px;min-width:140px;height:44px;border:0;border-radius:12px 0 0 12px;background:none;box-shadow:none;padding:0 14px}
.li-board .toolbar .search:focus-within{box-shadow:inset 0 0 0 2px var(--accent-soft);border:0}
.li-board .toolbar .dd,.li-board .toolbar .sortwrap{border-left:1px solid var(--border);height:26px;display:flex;align-items:center}
.li-board .toolbar .ddbtn{height:32px;border:0;background:none;border-radius:8px;padding:0 10px;margin:0 4px;font-size:13px;font-weight:500;color:var(--ink-2)}
.li-board .toolbar .ddbtn:hover{background:var(--surface-2);color:var(--ink)}
.li-board .toolbar .ddbtn.active{background:var(--accent-soft);color:var(--accent);font-weight:600}
.li-board .toolbar .sortsel{height:32px;border:0;background-color:transparent;font-size:13px;font-weight:500;color:var(--ink-2);margin-left:4px;padding-left:10px}
.li-board .toolbar .sortsel:hover{background-color:var(--surface-2)}
.li-board .toolbar .count{margin-left:6px;padding-left:12px;border-left:1px solid var(--border);white-space:nowrap}
.li-board .ddmenu{top:calc(100% + 10px);left:4px}
@media (max-width:760px){.li-board .toolbar{flex-wrap:wrap;height:auto;padding:6px}.li-board .toolbar .search{flex:1 1 100%;height:38px}.li-board .toolbar .dd{border-left:0}}
/* ---- side cards v3 ---- */
.li-board .scard{border-radius:16px;padding:16px;border:1px solid var(--border);box-shadow:0 1px 2px rgba(20,26,40,.04),0 8px 24px -12px rgba(20,26,40,.12);background:linear-gradient(180deg,color-mix(in srgb,var(--c-ind) 5%,var(--surface)) 0,var(--surface) 76px)}
.li-board .scard.attn{background:linear-gradient(180deg,color-mix(in srgb,var(--c-red) 6%,var(--surface)) 0,var(--surface) 90px)}
.li-board .scard header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.li-board .scard h3{flex:1;margin:0;font:650 14px var(--sans);letter-spacing:-.01em}
.li-board .sic{width:30px;height:30px;border-radius:9px}
.li-board .scard .slink{margin:0;padding:3px 9px;border-radius:99px;font-size:12px;font-weight:600;color:var(--accent);background:var(--accent-soft)}
.li-board .hero{display:flex;align-items:baseline;gap:8px;margin:0 0 10px}
.li-board .hn{font:700 30px/1 var(--sans);letter-spacing:-.03em} .li-board .hn.crit{color:var(--c-red)}
.li-board .hl{font-size:13px;color:var(--ink-2);font-weight:500}
.li-board .hchip{margin-left:auto;padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;background:color-mix(in srgb,var(--c-amb) 16%,transparent);color:var(--c-amb)}
.li-board .alist{display:flex;flex-direction:column;gap:2px;margin:0 -6px}
.li-board .alist li+li,.li-board .wlist li+li{border-top:0}
.li-board .arow{display:flex;align-items:center;gap:10px;padding:9px 8px;border-radius:12px;transition:background .12s}
.li-board .arow:hover{background:color-mix(in srgb,var(--c-ind) 7%,transparent)}
.li-board .arow .av{width:28px;height:28px;font-size:10.5px;flex:none}
.li-board .arow .pt{font-size:13px;font-weight:600;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.3}
.li-board .arow .ps{margin-top:1px}
.li-board .apct{padding:2px 7px;border-radius:7px;font-size:11.5px;background:color-mix(in srgb,var(--c-red) 12%,transparent)} .li-board .apct.risk{background:color-mix(in srgb,var(--c-amb) 15%,transparent)}
.li-board .arow .go{width:14px;height:14px;flex:none;color:var(--ink-3);opacity:0;transform:translateX(-3px);transition:opacity .12s,transform .12s}
.li-board .arow:hover .go,.li-board .arow:focus-visible .go{opacity:1;transform:none}
.li-board .wlist{margin:0 -6px;display:flex;flex-direction:column;gap:2px}
.li-board .wlist li{display:flex;align-items:center;gap:11px;padding:9px 8px;border-radius:12px;border:0}
.li-board .wlist li:hover{background:color-mix(in srgb,var(--c-ind) 7%,transparent)}
.li-board .wlist .av{width:30px;height:30px;font-size:11px}
.li-board .winfo{flex:1;min-width:0;display:flex;flex-direction:column}
.li-board .wlist .pn{font-size:13px;font-weight:600}
.li-board .wsub{font-size:11.5px;color:var(--ink-3)}
.li-board .wst{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;white-space:nowrap}
.li-board .wst i{width:7px;height:7px;border-radius:50%;background:currentColor}
.li-board .wst.crit{color:var(--c-red)} .li-board .wst.risk{color:var(--c-amb)} .li-board .wst.ok{color:var(--c-emr)}
.li-board .scard .more{margin:6px 0 0;padding-left:2px}

/* ---- filters + table share one card ---- */
.li-board .tcard{margin-top:14px;background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden;box-shadow:0 1px 2px rgba(20,26,40,.04)}
.li-board .tcard .toolbar{margin:0;border:0;border-bottom:1px solid var(--border);border-radius:0;background:none;padding-right:10px}
.li-board .tcard .toolbar .search{border-radius:0}
.li-board .tcard .chips{margin:0;padding:8px 14px;border-bottom:1px solid var(--border);background:var(--inset)}
.li-board .tcard .tablewrap{margin:0;border:0;border-radius:0;max-height:none;overflow-x:auto;overflow-y:visible}
.li-board .tcard thead th{border-top:0}
.li-board .tcard .tfoot{border:0;border-top:1px solid var(--border);border-radius:0}
.li-board .tcard .empty{border:0}
/* ---- pagination ---- */
.li-board .tcard .tfoot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;font-size:12.5px;color:var(--ink-2)}
.li-board .pager{display:flex;align-items:center;gap:4px}
.li-board .pgb{min-width:30px;height:30px;padding:0 8px;border-radius:8px;border:1px solid transparent;font:600 12.5px var(--sans);color:var(--ink-2);display:grid;place-items:center}
.li-board .pgb:hover:not(:disabled){background:var(--surface-2);color:var(--ink)}
.li-board .pgb.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.li-board .pgb:disabled{opacity:.35;cursor:not-allowed}
.li-board .pgb .ic{width:14px;height:14px;stroke-width:2.2} .li-board .pgb .prev{transform:rotate(90deg)} .li-board .pgb .next{transform:rotate(-90deg)}
.li-board .pge{padding:0 4px;color:var(--ink-3)}
.li-board .rpp{display:inline-flex;align-items:center;gap:6px;color:var(--ink-3)}
.li-board .rpp select{height:30px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font-size:12.5px;font-weight:600;padding:0 6px}
@media (max-width:760px){.li-board .tcard .tfoot{flex-wrap:wrap;justify-content:center}}
/* ---- header v2: breadcrumb, tools, primary action ---- */
.li-shell .top{position:sticky;top:0;z-index:20;padding:14px 28px 14px;background:color-mix(in srgb,var(--surface) 86%,transparent);backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--border)}
.li-shell .toprow{gap:8px}
.li-shell .crumb{display:flex;align-items:center;gap:4px;font-size:11.5px;font-weight:500;color:var(--ink-3);margin-bottom:2px}
.li-shell .crumb .ic{width:12px;height:12px} .li-shell .crumb span:last-child{color:var(--ink-2)}
.li-shell .ttlblk h1{font-size:22px;font-weight:700;letter-spacing:-.025em;line-height:1.2}
.li-shell .ttlblk .subttl{margin:1px 0 0;font-size:13px}
.li-shell .tools{display:flex;align-items:center;gap:2px}
.li-shell .iconbtn{position:relative;width:36px;height:36px;border-radius:10px;color:var(--ink-2);display:grid;place-items:center;transition:background .12s,color .12s}
.li-shell .iconbtn:hover{background:var(--surface-2);color:var(--ink)}
.li-shell .iconbtn .ic{width:17px;height:17px}
.li-shell .bell b{position:absolute;top:3px;right:2px;min-width:16px;height:16px;padding:0 4px;border-radius:99px;background:var(--c-red);color:#fff;font:700 10px/16px var(--sans);text-align:center;box-shadow:0 0 0 2px var(--surface)}
.li-shell .vsep{width:1px;height:24px;background:var(--border);margin:0 6px}
.li-shell .ghostbtn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 12px;border-radius:10px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:13px;transition:background .12s}
.li-shell .ghostbtn:hover{background:var(--surface-2)} .li-shell .ghostbtn:disabled{opacity:.5}
.li-shell .primary{height:36px;padding:0 16px;border-radius:10px;background:linear-gradient(180deg,color-mix(in srgb,var(--accent) 92%,#fff),var(--accent));font-size:13px;font-weight:600;box-shadow:inset 0 1px 0 rgba(255,255,255,.2),0 1px 2px rgba(20,26,40,.2),0 4px 12px -4px color-mix(in srgb,var(--accent) 55%,transparent)}
.li-shell .primary:hover{filter:brightness(1.07)}
@media (max-width:760px){.li-shell .top{padding:12px 16px}.li-shell .toprow{flex-wrap:wrap}.li-shell .ghostbtn span{display:none}}
.li-shell .toprow>.sp{display:block;flex:1}
/* ---- request kind icons ---- */
.li-board .chan{width:34px;height:34px;border-radius:10px}
.li-board .chan .ic{width:17px;height:17px}
.li-board .chan.k-ind{background:color-mix(in srgb,var(--c-ind) 14%,transparent);color:var(--c-ind)}
.li-board .chan.k-vio{background:color-mix(in srgb,var(--c-vio) 14%,transparent);color:var(--c-vio)}
.li-board .chan.k-sky{background:color-mix(in srgb,var(--c-sky) 15%,transparent);color:var(--c-sky)}
.li-board .chan.k-emr{background:color-mix(in srgb,var(--c-emr) 15%,transparent);color:var(--c-emr)}
.li-board .chan.k-amb{background:color-mix(in srgb,var(--c-amb) 17%,transparent);color:var(--c-amb)}
.li-board .chan.k-red{background:color-mix(in srgb,var(--c-red) 14%,transparent);color:var(--c-red)}
/* header v3: plain page heading under the app nav */
.li-shell .top{position:static;backdrop-filter:none;background:none;border-bottom:0;padding:22px 28px 6px}
/* header v4: enterprise page header band under the app nav */
.li-shell .top{position:static;background:var(--surface);border-bottom:1px solid var(--border);padding:20px 28px;box-shadow:0 1px 0 rgba(20,26,40,.02)}
.li-shell .toprow{gap:14px;align-items:center}
.li-shell .pico{width:46px;height:46px;border-radius:13px;display:grid;place-items:center;flex:none;color:#fff;background:var(--accent);box-shadow:none}
.li-shell .pico svg{width:22px;height:22px;stroke-width:1.8}
.li-shell .ttlblk h1{font-size:24px;font-weight:700;letter-spacing:-.03em;line-height:1.15;color:var(--ink)}
.li-shell .ttlblk .subttl{margin:4px 0 0;font-size:13.5px;color:var(--ink-2);font-weight:400;max-width:62ch}
.li-shell .scrollarea{padding-top:20px}
@media (max-width:760px){.li-shell .top{padding:14px 16px}.li-shell .pico{display:none}}
/* title block sits on the page itself (no header band) */
.li-shell .top{background:none;border-bottom:0;box-shadow:none;padding:24px 28px 0}
.li-shell .scrollarea{padding-top:18px}
/* compact page heading */
.li-shell .top{padding:16px 28px 0}
.li-shell .toprow{gap:12px}
.li-shell .pico{width:34px;height:34px;border-radius:10px}
.li-shell .pico svg{width:17px;height:17px}
.li-shell .ttlblk h1{font-size:18px;font-weight:650;letter-spacing:-.02em;line-height:1.25}
.li-shell .ttlblk .subttl{margin:1px 0 0;font-size:12.5px}
.li-shell .ghostbtn,.li-shell .primary{height:32px;font-size:12.5px;border-radius:9px;padding:0 12px}
.li-shell .ghostbtn svg,.li-shell .primary svg{width:14px;height:14px}
.li-shell .scrollarea{padding-top:14px}
/* tabs row has a fixed height so the side cards start level with the table card */
.li-board .stabs{flex-wrap:nowrap;overflow-x:auto;overflow-y:hidden;height:42px;align-items:stretch;gap:0 24px;scrollbar-width:none}
.li-board .stabs::-webkit-scrollbar{display:none}
.li-board .stab{padding:0 2px;white-space:nowrap;flex:none}
.li-board .split>.side{margin-top:0}
/* tighter gap between the page heading and the tabs */
.li-shell .top{padding-bottom:0}
.li-shell .scrollarea{padding-top:0}
.li-board .split{margin-top:2px}
.li-board .ov{margin-top:0}
.li-shell .top{padding-bottom:4px}
/* page heading is a white bordered card */
.li-shell .top{margin:16px 28px 0;padding:14px 18px;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:0 1px 2px rgba(20,26,40,.04)}
.li-shell .scrollarea{padding-top:12px}
@media (max-width:760px){.li-shell .top{margin:12px 16px 0;padding:12px 14px}}
/* ---- stepper: state colours + tooltip ---- */
.li-board .steps .nd{outline:0;cursor:default}
.li-board .steps .nd.d{border-color:var(--c-emr);background:var(--c-emr)}
.li-board .steps .ln.d{background:var(--c-emr)}
.li-board .steps .nd.c{border-color:var(--c-ind);box-shadow:0 0 0 3px color-mix(in srgb,var(--c-ind) 22%,transparent)}
.li-board .steps .nd.c::after{background:var(--c-ind)}
.li-board .steps .nd.c.risk{border-color:var(--c-amb);box-shadow:0 0 0 3px color-mix(in srgb,var(--c-amb) 24%,transparent)} .li-board .steps .nd.c.risk::after{background:var(--c-amb)}
.li-board .steps .nd.c.late{border-color:var(--c-red);box-shadow:0 0 0 3px color-mix(in srgb,var(--c-red) 22%,transparent)} .li-board .steps .nd.c.late::after{background:var(--c-red)}
.li-board .steps .nd:focus-visible{box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 40%,transparent)}
.li-board .steps .tip{position:absolute;z-index:40;bottom:calc(100% + 9px);left:50%;transform:translate(-50%,4px);min-width:150px;max-width:230px;padding:8px 11px;border-radius:10px;background:#161a26;color:#f1f3f9;box-shadow:0 10px 28px rgba(10,14,28,.35);display:flex;flex-direction:column;gap:3px;text-align:left;font-style:normal;white-space:normal;pointer-events:none;opacity:0;visibility:hidden;transition:opacity .14s,transform .14s var(--ease,ease),visibility .14s}
.dark .li-board .steps .tip{background:#eef0f7;color:#161a26}
.li-board .steps .tip::after{content:"";position:absolute;top:100%;left:50%;margin-left:-5px;border:5px solid transparent;border-top-color:#161a26}
.dark .li-board .steps .tip::after{border-top-color:#eef0f7}
.li-board .steps .nd.first .tip{left:-8px;transform:translate(0,4px)} .li-board .steps .nd.first .tip::after{left:14px;margin-left:0}
.li-board .steps .nd.last .tip{left:auto;right:-8px;transform:translate(0,4px)} .li-board .steps .nd.last .tip::after{left:auto;right:14px;margin-left:0}
.li-board .steps .nd:hover .tip,.li-board .steps .nd:focus-visible .tip{opacity:1;visibility:visible;transform:translate(-50%,0)}
.li-board .steps .nd.first:hover .tip,.li-board .steps .nd.first:focus-visible .tip,.li-board .steps .nd.last:hover .tip,.li-board .steps .nd.last:focus-visible .tip{transform:none}
.li-board .steps .tip b{font:650 12px/1.3 var(--sans)}
.li-board .steps .tip em{font:500 11px var(--sans);opacity:.65}
.li-board .steps .tst{display:inline-flex;align-items:center;gap:6px;font:600 11.5px var(--sans)}
.li-board .steps .tst i{width:7px;height:7px;border-radius:50%;background:#9aa3b8}
.li-board .steps .tst-d i{background:#4fd1a1} .li-board .steps .tst-d{color:#7ee2bd}
.li-board .steps .tst-c i{background:#8f9cff} .li-board .steps .tst-c{color:#aab4ff}
.li-board .steps .tst-r i{background:#f2b45a} .li-board .steps .tst-r{color:#f6c985}
.li-board .steps .tst-l i{background:#ff8a7c} .li-board .steps .tst-l{color:#ffaa9f}
.dark .li-board .steps .tst-d{color:#0b8a63} .dark .li-board .steps .tst-c{color:#4a58c9} .dark .li-board .steps .tst-r{color:#a86a0c} .dark .li-board .steps .tst-l{color:#c0372c}
.li-board .stagewrap,.li-board .tablewrap td{overflow:visible}
.li-board .tablewrap{overflow-y:visible}
.li-shell .crumb{display:flex;align-items:center;gap:4px;margin-top:2px;font-size:12px;font-weight:500;color:var(--ink-3)}
.li-shell .crumb svg{width:12px;height:12px;flex:none} .li-shell .crumb span:last-child{color:var(--ink-2)}
/* ---- side cards: explicit buttons ---- */
.li-board .alist{margin:0}
.li-board .alist .arow{display:flex;align-items:center;gap:8px;padding:8px;border-radius:12px;transition:background .12s}
.li-board .alist .arow:hover{background:color-mix(in srgb,var(--c-ind) 7%,transparent)}
.li-board .amainbtn{display:flex;align-items:center;gap:10px;flex:1;min-width:0;text-align:left;border-radius:8px}
.li-board .amainbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.li-board .aside2{display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex:none}
.li-board .abtn{height:26px;padding:0 11px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font:600 12px var(--sans);transition:background .12s,border-color .12s,color .12s}
.li-board .abtn:hover{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.li-board .abtn.on{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.li-board .wlist li{display:flex;align-items:center;gap:10px;padding:8px;border-radius:12px;transition:background .12s}
.li-board .wlist li.on{background:color-mix(in srgb,var(--c-ind) 10%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--c-ind) 40%,transparent)}
.li-board .wsub.crit{color:var(--c-red)} .li-board .wsub.risk{color:var(--c-amb)} .li-board .wsub.ok{color:var(--c-emr)}
.li-board .wlist .pn{max-width:100%}
.li-shell .scrollarea.nohead{padding-top:16px}
/* v4: one radius for cards (12), one inside (8); one border per card */
.li-shell .top{border-radius:12px;box-shadow:none}
.li-shell .pico{border-radius:10px}
.li-board .tcard{border-radius:12px;box-shadow:none}
.li-board .tcard .toolbar .search,.li-board .ddbtn,.li-board .sortsel,.li-board .btn,.li-board .abtn{border-radius:8px}
.li-board .scard{border-radius:12px;box-shadow:none;background:var(--surface)}
.li-board .sic{border-radius:8px}
.li-board .arow,.li-board .wlist li{border-radius:8px}
.li-board .tfoot{border-radius:0}
.li-board .wlist.scroll{max-height:320px;overflow-y:auto}
.li-board .moreb{display:block;width:100%;margin-top:6px;padding:7px;border-radius:10px;text-align:center;font-size:12.5px;font-weight:600;color:var(--accent);transition:background .12s}
.li-board .moreb:hover{background:var(--accent-soft)}
.li-board .kcards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:14px}
.li-board .kcard{--t:var(--c-ind);display:flex;align-items:center;gap:12px;padding:14px;border-radius:14px;text-align:left;background:var(--surface);box-shadow:0 0 0 1px var(--border);transition:transform .18s,box-shadow .18s}
.li-board .kcard:hover{transform:translateY(-2px);box-shadow:0 0 0 1px var(--border-strong),0 10px 20px -12px rgba(20,26,40,.25)}
.li-board .kc-vio{--t:var(--c-vio)}.li-board .kc-sky{--t:var(--c-sky)}.li-board .kc-amb{--t:var(--c-amb)}.li-board .kc-red{--t:var(--c-red)}
.li-board .kcard .kic{flex:none;display:grid;place-items:center;width:42px;height:42px;border-radius:12px;background:color-mix(in srgb,var(--t) 14%,transparent);color:var(--t)}
.li-board .kcard .kic .ic{width:21px;height:21px}
.li-board .kcard .ktx{display:flex;flex-direction:column;min-width:0}
.li-board .kcard .kl{font:600 12px var(--sans);color:var(--ink-2)}
.li-board .kcard .kn{font:700 26px/1.1 var(--sans);letter-spacing:-.02em;color:var(--ink)}
.li-board .kcard .ks{font-size:11px;color:var(--ink-3)}
.li-board .from.late{color:var(--crit);font-weight:600}
@media (max-width:1200px){.li-board .kcards{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.li-board .kcards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.li-board th.cdue,.li-board td.cdue{width:84px;min-width:84px;padding-left:6px;padding-right:6px;white-space:nowrap}
.li-board .rowact{white-space:nowrap}
.li-board .tablewrap table{table-layout:auto}
.li-board .req .nm,.li-board .req .prev{max-width:170px}
.li-board td .cp,.li-board td .ty{max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .stbox{max-width:120px}
.li-board tbody td{padding-left:8px;padding-right:8px}
.li-board .stagewrap{min-width:130px}
/* responsive layout must come last so it beats the two-column rule above */
@media (max-width:1180px){.li-board .split{grid-template-columns:minmax(0,1fr)}.li-board .side{position:static;order:-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.li-board .side{grid-template-columns:1fr}}

/* agreement kind + owner, spelled out */
.li-board .atw{display:flex;flex-direction:column;gap:4px;align-items:flex-start;min-width:0}
.li-board .atype{display:inline-flex;align-items:center;height:24px;padding:0 11px;border-radius:99px;font:700 12px var(--sans);letter-spacing:.01em;background:color-mix(in srgb,var(--c) 14%,transparent);color:var(--c);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .atype.a-vio{--c:var(--c-vio)}.li-board .atype.a-ind{--c:var(--c-ind)}.li-board .atype.a-amb{--c:var(--c-amb)}.li-board .atype.a-emr{--c:var(--c-emr)}.li-board .atype.a-sky{--c:var(--c-sky,var(--c-ind))}.li-board .atype.a-red{--c:var(--c-red)}.li-board .atype.a-mute{--c:var(--ink-2)}
.li-board .acat{font-size:11px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px}
.li-board .own{display:inline-flex;align-items:center;gap:8px;min-width:0;max-width:100%}
.li-board .own>i{flex:none;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font:700 10px var(--sans);font-style:normal;background:var(--accent-soft);color:var(--accent)}
.li-board .own.none>i{background:var(--surface-2);color:var(--ink-3)}
.li-board .own>span{display:flex;flex-direction:column;min-width:0;line-height:1.25}
.li-board .own em{font:600 9.5px var(--sans);font-style:normal;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.li-board .own b{font:600 12.5px var(--sans);color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .own.none b{color:var(--ink-3);font-weight:500}
/* columns sized for the seven fields, so nothing is clipped; long names wrap instead of cutting off */
.li-board .tablewrap table.lit{table-layout:fixed;min-width:0;width:100%}
.li-board table.lit thead th:nth-child(1){width:23%}.li-board table.lit thead th:nth-child(2){width:14%}.li-board table.lit thead th:nth-child(3){width:12%}.li-board table.lit thead th:nth-child(4){width:21%}.li-board table.lit thead th:nth-child(5){width:16%}.li-board table.lit thead th:nth-child(6){width:88px}.li-board table.lit thead th:nth-child(7){width:78px}
.li-board table.lit th.cdue,.li-board table.lit td.cdue{width:88px;min-width:88px;white-space:normal}
.li-board td.cdue .from{white-space:normal;line-height:1.25}
.li-board td .cp{max-width:100%;white-space:normal;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;word-break:break-word}
.li-board .atype{white-space:normal;height:auto;min-height:24px;padding:3px 11px;line-height:1.25;max-width:100%;overflow:visible;text-overflow:clip}
.li-board .acat{max-width:100%}
.li-board .own b{white-space:normal;overflow:visible;word-break:break-word}
.li-board .own{align-items:flex-start}
/* nothing spills into its neighbour: the first three cells clip, badges stay one line */
.li-board table.lit tbody td:nth-child(1),.li-board table.lit tbody td:nth-child(2),.li-board table.lit tbody td:nth-child(3){overflow:hidden}
.li-board .req{min-width:0}
.li-board .req .nm,.li-board .req .prev{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.li-board .atype{white-space:nowrap;height:24px;min-height:0;padding:0 11px;line-height:24px;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.li-board .acat{white-space:nowrap}
.li-board .own b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.li-board .own>span{min-width:0}
.li-board table.lit thead th:nth-child(1){width:26%}.li-board table.lit thead th:nth-child(2){width:11%}.li-board table.lit thead th:nth-child(3){width:13%}.li-board table.lit thead th:nth-child(4){width:21%}.li-board table.lit thead th:nth-child(5){width:15%}
/* names are shown in full: they wrap onto a second line instead of ending in "…" */
.li-board .tablewrap{overflow-x:auto;overflow-y:visible}
.li-board .tablewrap table.lit{table-layout:fixed;min-width:0;width:100%}
.li-board .req .nm{white-space:normal;overflow:visible;text-overflow:clip;display:block;text-align:left;line-height:1.3;word-break:break-word}
.li-board .req .prev{white-space:normal;overflow:visible;text-overflow:clip;line-height:1.35;word-break:break-word}
.li-board .own b{white-space:normal;overflow:visible;text-overflow:clip;word-break:break-word;line-height:1.25}
.li-board td .cp{display:block;-webkit-line-clamp:unset;overflow:visible;white-space:normal;word-break:break-word}
.li-board .acat{white-space:normal}
.li-board .stagenm,.li-board .wf,.li-board .recmark{white-space:normal;overflow:visible;text-overflow:clip;word-break:break-word;max-width:100%}
.li-board .stagenm{line-height:1.3}
.li-board table.lit tbody td{vertical-align:middle;padding-top:12px;padding-bottom:12px;position:static;box-shadow:none}
.li-board table.lit thead th:nth-child(1){width:30%}.li-board table.lit thead th:nth-child(2){width:9%}.li-board table.lit thead th:nth-child(3){width:14%}.li-board table.lit thead th:nth-child(4){width:19%}.li-board table.lit thead th:nth-child(5){width:15%}.li-board table.lit thead th:nth-child(6){width:84px}.li-board table.lit thead th:nth-child(7){width:74px}
.li-board table.lit th.cdue,.li-board table.lit td.cdue{width:84px;min-width:84px}
.li-board .stbox,.li-board .stbox .own{max-width:none;width:100%}
.li-board .own>span{flex:1 1 auto;min-width:0}
/* hover card + rows that rise in one after another */
.li-board .rtip{position:fixed;z-index:60;width:360px;padding:14px 16px 12px;border-radius:16px;background:var(--surface);box-shadow:0 0 0 1px var(--border),0 24px 50px -18px rgba(20,26,40,.55);pointer-events:none;transform-origin:20px 0;animation:rtpop .22s cubic-bezier(.2,.9,.3,1.2) both}
@keyframes rtpop{from{opacity:0;transform:translateY(-6px) scale(.94)}to{opacity:1;transform:none}}
.li-board .rt-top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}
.li-board .rt-ref{font:700 10.5px var(--mono);color:var(--accent);background:var(--accent-soft);padding:2px 8px;border-radius:99px}
.li-board .rtip .atype{max-width:140px}
.li-board .rt-title{display:block;font:650 14.5px/1.35 var(--sans);color:var(--ink);margin-bottom:10px;word-break:break-word}
.li-board .rt-grid{display:grid;grid-template-columns:92px 1fr;gap:6px 10px;margin:0;padding-top:10px;border-top:1px solid var(--border);font-size:12px}
.li-board .rt-grid dt{color:var(--ink-3);font-weight:600} .li-board .rt-grid dd{margin:0;color:var(--ink);word-break:break-word}
.li-board tbody tr{animation:rowin .45s calc(var(--ri,0) * 35ms) cubic-bezier(.2,.8,.2,1) both}
@keyframes rowin{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.li-board tbody tr:hover .atype{transform:scale(1.06)} .li-board .atype{transition:transform .2s}
.li-board tbody tr:hover .own>i{transform:scale(1.1)} .li-board .own>i{transition:transform .2s}
@media (prefers-reduced-motion:reduce){.li-board tbody tr,.li-board .rtip{animation:none!important}}
`;
