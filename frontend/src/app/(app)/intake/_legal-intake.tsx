"use client";

// Legal Intake board — a 1:1 port of the Screen-1 mockup (its own palette, table
// header style, tiles, segmented stage bars, pills), wired to the real intake API.
// All CSS is scoped under `.li-board` so it cannot leak into the rest of the app;
// dark mode is driven by the app's `.dark` class.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Mail, MessageSquare, FileText, Search, ChevronDown, Clock, Inbox, CheckSquare, ClipboardCheck, PenLine, ListChecks, Moon } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { intakeApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/utils";
import type { IntakeRequest, WorkflowSuggestion } from "@/lib/types";

const POLL = { refetchInterval: 15_000 } as const;

// ---------- data helpers (all from real request fields) ----------
function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(id); }, [ms]);
  return now;
}
const isOpenReq = (r: IntakeRequest) => r.status !== "closed" && r.status !== "approved";
function channelOf(r: IntakeRequest): { key: string; label: string; Icon: LucideIcon } {
  const s = (r.source ?? "").toLowerCase();
  if (/mail|gmail|email|imap/.test(s)) return { key: "email", label: "Email", Icon: Mail };
  if (/slack|chat|teams|message|dm/.test(s)) return { key: "chat", label: "Chat", Icon: MessageSquare };
  if (/form|portal|self|web|intake/.test(s)) return { key: "form", label: "Form", Icon: FileText };
  return { key: "other", label: s || "other", Icon: FileText };
}
function counterpartyOf(r: IntakeRequest): string | null {
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
function ageOf(iso: string | null, now: number): string {
  if (!iso) return "";
  const m = Math.floor((now - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}
const isMsa = (s: string) => /msa|master|service/i.test(s);

// status filter tokens
function statusMatch(r: IntakeRequest, token: string): boolean {
  switch (token) {
    case "new": return r.status === "open";
    case "running": return isOpenReq(r) && (r.workflow ?? []).some((s) => s.active);
    case "stuck": return r.sla_status === "overdue";
    case "at_risk": return r.sla_status === "at_risk";
    case "unassigned": return isOpenReq(r) && !r.assigned_to_user_id;
    case "lowconf": return lowConfidence(r);
    default: return true;
  }
}
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
  if ((r.gates?.effective_keys?.length ?? 0) > 0) f.push({ label: "gated", cls: "new" });
  return f;
}

// ---------- compact dropdown (mockup .dd / .ddmenu) ----------
function FilterMenu({ label, options, selected, onToggle }: { label: string; options: { value: string; label: string; dot?: string }[]; selected: Set<string>; onToggle: (v: string) => void }) {
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
function StageCell({ r }: { r: IntakeRequest }) {
  const steps = r.workflow ?? [];
  if (steps.length) {
    const done = steps.filter((s) => s.done).length;
    const active = steps.find((s) => s.active);
    const stuck = r.sla_status === "overdue";
    const i = active ? steps.indexOf(active) : done - 1;
    return (
      <div className="stagewrap">
        <div className="stagehead">
          <span className={`sdot ${stuck ? "stuck" : "ok"}`} />
          <span className="stagenm">{active?.label ?? "In progress"}</span>
          <span className="stepno">{done}/{steps.length}</span>
        </div>
        <div className="stepbar">
          {steps.map((s, k) => {
            const cls = s.done ? "done" : k === i ? "cur" : "";
            return <span key={k} className={`seg ${cls}${cls && stuck ? " st" : ""}`} />;
          })}
        </div>
        <div className="wflabel">{r.type_label}{stuck ? <> · <span className="stucktag">overdue</span></> : null}</div>
      </div>
    );
  }
  const fs = flowSuggestion(r);
  if (fs?.flow_name) {
    const conf = Math.round((fs.confidence ?? 0) * 100);
    const col = conf >= 85 ? "var(--good)" : conf >= 72 ? "var(--warn)" : "var(--crit)";
    return (
      <div className="stagewrap">
        <div className="recmark"><Clock className="ic" style={{ width: 12, height: 12 }} />Not started · recommended</div>
        <div style={{ marginTop: 5 }}><span className={`wf ${isMsa(fs.flow_name) ? "msa" : ""}`}>{fs.flow_name}</span></div>
        <div className="confbar"><div className="track"><div className="fill" style={{ width: `${conf}%`, background: col }} /></div><span className="pct">{conf}% conf.</span></div>
      </div>
    );
  }
  return <span className="dim">—</span>;
}

// ---------- the board ----------
export function LegalIntakeBoard({ onOpen }: { onOpen: (id: string) => void }) {
  const now = useNow(30_000);
  const { data } = useQuery({ queryKey: ["intake-list"], queryFn: () => intakeApi.list(), ...POLL });
  const rows = useMemo(() => data ?? [], [data]);

  const [search, setSearch] = useState("");
  const [channelSel, setChannelSel] = useState<Set<string>>(new Set());
  const [typeSel, setTypeSel] = useState<Set<string>>(new Set());
  const [statusSel, setStatusSel] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState("new");
  const [briefOpen, setBriefOpen] = useState(true);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, v: string) => {
    const n = new Set(set); if (n.has(v)) n.delete(v); else n.add(v); setter(n);
  };

  const channelOpts = useMemo(() => {
    const m = new Map<string, string>();
    rows.forEach((r) => { const c = channelOf(r); m.set(c.key, c.label.charAt(0).toUpperCase() + c.label.slice(1)); });
    return [...m].map(([value, label]) => ({ value, label }));
  }, [rows]);
  const typeOpts = useMemo(() => [...new Set(rows.map((r) => r.type_label))].sort().map((t) => ({ value: t, label: t })), [rows]);

  // briefing figures
  const openRows = rows.filter(isOpenReq);
  const overdue = openRows.filter((r) => r.sla_status === "overdue");
  const atRisk = openRows.filter((r) => r.sla_status === "at_risk");
  const unassigned = openRows.filter((r) => !r.assigned_to_user_id);
  const awaiting = openRows.filter((r) => r.status === "open");
  const inWorkflow = openRows.filter((r) => (r.workflow ?? []).some((s) => s.active));
  const confs = openRows.map((r) => flowSuggestion(r)?.confidence).filter((c): c is number => typeof c === "number");
  const avgConf = confs.length ? Math.round((confs.reduce((a, b) => a + b, 0) / confs.length) * 100) : null;
  const worst = overdue[0] ?? atRisk[0];
  const attention = [...overdue, ...atRisk].slice(0, 6);
  const loadMap = new Map<string, { total: number; stuck: number }>();
  openRows.filter((r) => (r.workflow ?? []).some((s) => s.active) || r.status !== "open").forEach((r) => {
    const t = r.assigned_to_label ?? "Unassigned";
    const e = loadMap.get(t) ?? { total: 0, stuck: 0 };
    e.total += 1; if (r.sla_status === "overdue") e.stuck += 1;
    loadMap.set(t, e);
  });
  const load = [...loadMap.entries()].sort((a, b) => b[1].total - a[1].total).slice(0, 6);
  const maxLoad = Math.max(1, ...load.map(([, v]) => v.total));
  const tiles: { n: number | string; l: string; c?: string }[] = [
    { n: awaiting.length, l: "awaiting triage", c: "a" },
    { n: inWorkflow.length, l: "in workflow" },
    { n: atRisk.length, l: "at risk", c: "w" },
    { n: overdue.length, l: "overdue", c: "c" },
    { n: unassigned.length, l: "unassigned" },
    { n: avgConf != null ? `${avgConf}%` : "—", l: "avg confidence" },
  ];

  // filter + sort
  const q = search.trim().toLowerCase();
  const created = (r: IntakeRequest) => r.created_at ?? r.submitted_at ?? "";
  const filtered = rows.filter((r) => {
    if (channelSel.size && !channelSel.has(channelOf(r).key)) return false;
    if (typeSel.size && !typeSel.has(r.type_label)) return false;
    if (statusSel.size && ![...statusSel].some((t) => statusMatch(r, t))) return false;
    if (q && !`${r.ref} ${r.requester_name ?? ""} ${r.type_label} ${r.subject ?? ""} ${r.description ?? ""} ${counterpartyOf(r) ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const shown = [...filtered].sort((a, b) => {
    if (sort === "cp") return (counterpartyOf(a) ?? "~").localeCompare(counterpartyOf(b) ?? "~");
    if (sort === "old") return created(a).localeCompare(created(b));
    return created(b).localeCompare(created(a));
  });
  const anyFilter = !!(q || channelSel.size || typeSel.size || statusSel.size);

  return (
    <div className="li-board">
      <style dangerouslySetInnerHTML={{ __html: LI_CSS }} />

      {/* toolbar */}
      <div className="toolbar">
        <div className="search">
          <Search className="ic" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search counterparty, subject, ref…" />
        </div>
        <FilterMenu label="Channel" options={channelOpts} selected={channelSel} onToggle={(v) => toggle(channelSel, setChannelSel, v)} />
        <FilterMenu label="Type" options={typeOpts} selected={typeSel} onToggle={(v) => toggle(typeSel, setTypeSel, v)} />
        <FilterMenu label="Status" options={STATUS_FILTERS} selected={statusSel} onToggle={(v) => toggle(statusSel, setStatusSel, v)} />
        <select className="sortsel" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="new">Newest first</option>
          <option value="old">Oldest first</option>
          <option value="cp">Counterparty A–Z</option>
        </select>
        {anyFilter && <span className="clear" onClick={() => { setSearch(""); setChannelSel(new Set()); setTypeSel(new Set()); setStatusSel(new Set()); }}>Clear</span>}
        <span className="sp" />
        <span className="dim num" style={{ fontSize: 12 }}>{shown.length} of {rows.length}</span>
      </div>

      {/* AI briefing */}
      <div className={`brief${briefOpen ? "" : " collapsed"}`}>
        <div className="briefhd">
          <div className="aiglyph">✦</div>
          <div><b>Intake briefing</b> <span className="when">· reads your full queue</span></div>
          <button className="tog" onClick={() => setBriefOpen((o) => !o)}>{briefOpen ? "Hide" : "Show"}</button>
        </div>
        <div className="oneline"><b>{awaiting.length}</b> to triage · <b>{inWorkflow.length}</b> in workflow · <b style={{ color: "var(--crit)" }}>{overdue.length} overdue</b></div>
        <div className="briefbody">
          <p className="summary">
            <b>{openRows.length} open requests.</b> {atRisk.length} at risk{overdue.length ? <>, <span className="c">{overdue.length} overdue</span></> : null}.{" "}
            {worst ? <>Start with <b>{worst.ref}</b> — {worst.subject || worst.type_label}, {worst.sla_status === "overdue" ? "overdue" : "at risk"}. </> : null}
            {unassigned.length ? <>{unassigned.length} still unassigned.</> : "Everything's assigned."}
          </p>
          <div className="tiles">
            {tiles.map((t) => (
              <div key={t.l} className={`tile ${t.c ?? ""}`}><div className="tn num">{t.n}</div><div className="tl">{t.l}</div></div>
            ))}
          </div>
          {load.length > 0 && (
            <div className="load">
              <div className="loadhd">Workload · in-flight by owner</div>
              <div className="loadgrid">
                {load.map(([name, v]) => (
                  <div key={name} className="loadrow">
                    <span className="ldname">{name}</span>
                    <span className="ldbar">
                      <span className="ldfill" style={{ width: `${((v.total - v.stuck) / maxLoad) * 100}%` }} />
                      <span className="ldstuck" style={{ width: `${(v.stuck / maxLoad) * 100}%` }} />
                    </span>
                    <span className="ldn">{v.total}{v.stuck ? <span className="s"> ·{v.stuck}</span> : null}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="attention">
            <span className="attlbl">Needs you now</span>
            {attention.length ? attention.map((r) => (
              <button key={r.id} className={`attchip ${r.sla_status === "overdue" ? "c" : "w"}`} onClick={() => onOpen(r.id)}>
                {r.ref} · {r.requester_name ?? r.type_label}
              </button>
            )) : <span className="dim" style={{ fontSize: 12 }}>Nothing overdue — queue is healthy.</span>}
          </div>
        </div>
      </div>

      {/* table */}
      <div className="tablewrap">
        <table>
          <thead><tr>
            <th className="c-check"><input type="checkbox" className="cbx" checked={shown.length > 0 && shown.every((r) => sel.has(r.id))} onChange={(e) => setSel(e.target.checked ? new Set(shown.map((r) => r.id)) : new Set())} /></th>
            <th className="c-idx">#</th>
            <th>Request</th>
            <th>Counterparty</th>
            <th>Stage / progress</th>
            <th>Flags</th>
            <th>Received</th>
            <th />
          </tr></thead>
          <tbody>
            {shown.map((r, i) => {
              const ch = channelOf(r); const Icon = ch.Icon;
              const desc = (r.subject || r.description || "").split("\n")[0].trim() || r.type_label;
              const cp = counterpartyOf(r);
              const flags = rowFlags(r);
              const fs = flowSuggestion(r);
              const isNew = !(r.workflow ?? []).some((s) => s.active) && !!fs;
              return (
                <tr key={r.id} onClick={() => onOpen(r.id)} style={{ cursor: "pointer" }}>
                  <td className="c-check"><input type="checkbox" className="cbx" checked={sel.has(r.id)} onClick={(e) => e.stopPropagation()} onChange={() => { const n = new Set(sel); if (n.has(r.id)) n.delete(r.id); else n.add(r.id); setSel(n); }} /></td>
                  <td className="c-idx"><span className="idx">{i + 1}</span></td>
                  <td>
                    <div className="req">
                      <div className="chan"><Icon className="ic" /></div>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                          <span className="nm">{desc}</span><span className="ref">{r.ref}</span>
                        </div>
                        <div className="prev">{r.requester_name ?? "—"}{r.department ? ` · ${r.department}` : ""} · {r.type_label}</div>
                      </div>
                    </div>
                  </td>
                  <td>{cp ? <><div className="cp">{cp}</div><div className="ty">{r.type_label}</div></> : <span className="dim">—</span>}</td>
                  <td><StageCell r={r} /></td>
                  <td><div className="flags">{flags.length ? flags.map((f) => <span key={f.label} className={`pill ${f.cls}`}>{f.label}</span>) : <span className="dim" style={{ fontSize: 11 }}>clean</span>}</div></td>
                  <td><div className="from">{r.requester_name ?? "—"}</div><div className="age">{ch.label.toLowerCase()} · {ageOf(created(r), now)} ago</div></td>
                  <td><div className="rowact">
                    {isNew ? <button className="btn pri sm" onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>▸ Start</button>
                      : <button className="btn sm" onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>Open</button>}
                    <button className="btn sm" title="Open" onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>⋯</button>
                  </div></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {shown.length === 0 && <div className="empty">No requests match these filters.</div>}
      </div>
    </div>
  );
}

// ---------- full mockup shell (sidebar + topbar) ----------
const NAV_MAIN: { key: string; label: string; href: string; icon: LucideIcon }[] = [
  { key: "intake", label: "Legal Intake", href: "/intake", icon: Inbox },
  { key: "mywork", label: "My Work", href: "/my-work", icon: CheckSquare },
  { key: "contracts", label: "Contracts", href: "/contracts", icon: FileText },
  { key: "search", label: "Search", href: "/search", icon: Search },
];
const NAV_LIFE: { key: string; label: string; href: string; icon: LucideIcon }[] = [
  { key: "approvals", label: "Approvals", href: "/approvals", icon: ClipboardCheck },
  { key: "signatures", label: "Signatures", href: "/signatures", icon: PenLine },
  { key: "obligations", label: "Obligations", href: "/obligations", icon: ListChecks },
];

export function MockupShell({ active, title, subtitle, stats, actions, onMyWork, children }: {
  active: string; title: string; subtitle?: string; stats?: ReactNode; actions?: ReactNode; onMyWork?: () => void; children: ReactNode;
}) {
  const { user } = useAuth();
  const name = user?.full_name ?? user?.email ?? "You";
  const role = user?.active_role_name ?? "Legal";
  const navItem = (n: { key: string; label: string; href: string; icon: LucideIcon }) => {
    const Icon = n.icon;
    const cls = active === n.key ? "on" : "";
    void onMyWork;
    return <Link key={n.key} href={n.href} className={cls}><Icon className="ic" />{n.label}</Link>;
  };
  return (
    <div className="li-board li-shell">
      <style dangerouslySetInnerHTML={{ __html: LI_CSS }} />
      <div className="main">
        <div className="top">
          <div className="toprow">
            <h1>{title}</h1>
            {subtitle ? <span className="subttl">{subtitle}</span> : null}
            <span className="sp" />
            {stats}
            {actions}
            <button className="iconbtn" title="Theme" onClick={() => document.documentElement.classList.toggle("dark")}><Moon className="ic" /></button>
          </div>
        </div>
        <div className="scrollarea">{children}</div>
      </div>
    </div>
  );
}

// ---------- scoped mockup CSS (Screen-1 palette, verbatim, prefixed) ----------
const LI_CSS = `
.li-board{
  --bg:#f4f6f9;--surface:#ffffff;--surface-2:#eef1f6;--inset:#f8fafc;
  --ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;
  --accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;
  --good:#2f875f;--good-soft:#e4f1ea;--warn:#a9772b;--warn-soft:#f6edd9;
  --crit:#bb4835;--crit-soft:#f7e4df;--ext:#75589f;--ext-soft:#efe8f7;--ai:#3f6699;--ai-soft:#e7eef8;
  --shadow:0 1px 2px rgba(20,26,40,.05),0 10px 26px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  color:var(--ink);font:400 13px/1.5 var(--sans);-webkit-font-smoothing:antialiased;
}
.dark .li-board{
  --bg:#0c0f16;--surface:#141922;--surface-2:#1b2130;--inset:#10141d;
  --ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;
  --accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;
  --good:#5cbf90;--good-soft:#15271f;--warn:#d3a24e;--warn-soft:#2a2213;
  --crit:#e2705c;--crit-soft:#2c1a17;--ext:#ab90d2;--ext-soft:#221b31;--ai:#6f9bd6;--ai-soft:#151f2e;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 30px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55);
}
.li-board *{box-sizing:border-box}
.li-board button{cursor:pointer;border:0;background:none;color:inherit;font:inherit}
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
.li-board .btn{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12px}
.li-board .btn:hover{background:var(--surface-2)}
.li-board .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .li-board .btn.pri:hover{filter:brightness(1.06)}
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
`;
