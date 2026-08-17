"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Bot, Check, ChevronDown, ChevronUp, DoorOpen, FileText, Paperclip, PenLine, Plus, RotateCw, Search, ShieldCheck, Trash2, Users, Mail, MessageSquare, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, CenterSpinner, EmptyState,
  ErrorState, Field, Input, Modal, Pagination, Select, Table, TD, TH, THead,
  TR, Textarea,
} from "@/components/ui";
import { intakeApi, contractsApi, approvalsApi, aiApi, playbooksApi, workflowsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Markdown } from "@/components/markdown";
import { cn, titleCase } from "@/lib/utils";
import {
  can, POSTURE_LABEL, POSTURE_TONE, PRIORITY_TONE, slaBarColor,
  sortBySla, STATUS_LABEL, STATUS_TONE,
} from "@/lib/intake";
import type {
  ContractResponse, WorkflowRun, WorkflowRunStep, WorkflowSuggestion, IntakeFieldSpec, IntakeRequest, IntakeRequestType, IntakeSlaPosture, IntakeStatus, LitigationAssessment,
} from "@/lib/types";
import { SlaLegsBar, TeamsTab } from "./_phase1";
import { LegalIntakeBoard, MockupShell } from "./_legal-intake";
import { RequestOverview } from "./_request-overview";
import { CopilotChat, PoolOpsTab, SelfServiceTab } from "./_phase2";
import { RulesTab } from "../approvals/_rules-builder";
import { WorkflowPanel } from "./_workflow-panel";
import { GovernanceLadderSteps } from "./_governance-ladder";

// Keep the queue live: React Query re-fetches on this cadence (paused while the
// tab is backgrounded), so SLA postures advance and triage/escalation changes
// surface without a manual reload. The backend recomputes SLA from `now` on
// every serialize, so each poll reflects real elapsed time.
const LIVE_POLL = { refetchInterval: 15_000 } as const;

// A shared wall-clock that re-renders its consumers every `ms`. Used to make
// the SLA columns tick in real time instead of freezing between polls.
function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

// Elapsed with a live seconds field, so the SLA clock visibly runs.
// ponytail: derives from submitted_at, so it ignores paused time between polls;
// each 15s refetch re-syncs to the backend's pause-accurate posture.
function fmtDurLive(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}h ${pad(m)}m ${pad(sec)}s` : `${m}m ${pad(sec)}s`;
}

// A truthful "it's live" cue: a pulsing dot + how long ago the queue last
// refreshed, ticking every second. Owns its own tick so only this badge
// re-renders, not the whole page.
function LivePulse({ updatedAt }: { updatedAt: number }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const secs = updatedAt ? Math.max(0, Math.round((Date.now() - updatedAt) / 1000)) : 0;
  const ago = secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m`;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-success"
      title="The queue refreshes automatically"
    >
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
      </span>
      Live · updated {ago} ago
    </span>
  );
}

export default function IntakePage() {
  const { user } = useAuth();
  const isStaff = can(user, "intake:read");
  const isAdmin = can(user, "admin_panel:access");
  const { notify } = useToast();
  const qc = useQueryClient();
  const [gmailSyncing, setGmailSyncing] = useState(false);

  async function syncGmail() {
    setGmailSyncing(true);
    try {
      const res = await intakeApi.gmailSync();
      if (res.status === "disabled") {
        notify(res.note || "Gmail sync isn't configured yet", "error");
      } else if (res.status === "error") {
        notify(res.note || "Gmail sync failed", "error");
      } else {
        type Filed = { deduped?: boolean; thread_followup?: boolean; documents_added?: string[] };
        const filed = (res.filed ?? []) as Filed[];
        const newRequests = filed.filter((f) => !f.deduped).length;
        const docsAddedToThreads = filed
          .filter((f) => f.thread_followup)
          .reduce((n, f) => n + (f.documents_added?.length ?? 0), 0);
        const parts = [];
        if (newRequests > 0) parts.push(`${newRequests} new request(s)`);
        if (docsAddedToThreads > 0) parts.push(`${docsAddedToThreads} document(s) added to existing threads`);
        notify(parts.length > 0 ? `Synced: ${parts.join(", ")}` : "No new emails to sync", "success");
        qc.invalidateQueries({ queryKey: ["intake-list"] });
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Gmail sync failed", "error");
    } finally {
      setGmailSyncing(false);
    }
  }

  // Reference-style tab set — Work · File · Insights, divider-grouped. Inbox and
  // SLA are first-class tabs (not nested view-toggles) so every lens is one
  // click away. Requesters get just the filing three.
  const groups = useMemo<{ id: string; label: string }[][]>(() => {
    if (!isStaff)
      return [[
        { id: "new", label: "New Request" },
        { id: "self", label: "Self-Service" },
        { id: "mywork", label: "My Work" },
      ]];
    return [
      [
        { id: "queue", label: "Inbox" },
        { id: "mywork", label: "My Work" },
      ],
      [
        { id: "new", label: "New Request" },
        { id: "self", label: "Self-Service" },
      ],
      [
        { id: "workflows", label: "Workflows" },
        { id: "ops", label: "Operations" },
      ],
    ];
  }, [isStaff]);

  const [section, setSection] = useState(isStaff ? "queue" : "new");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [newRequestSeed, setNewRequestSeed] = useState("");
  // Deep-link: /intake?open=<id> opens that request's detail (used by My Work).
  useEffect(() => {
    const o = new URLSearchParams(window.location.search).get("open");
    if (o) setDetailId(o);
  }, []);

  const { data: listData, dataUpdatedAt } = useQuery({
    queryKey: ["intake-list"], queryFn: () => intakeApi.list(), enabled: isStaff, ...LIVE_POLL,
  });
  const { data: myWork } = useQuery({
    queryKey: ["intake-mywork"], queryFn: intakeApi.myWork, enabled: isStaff, ...LIVE_POLL,
  });
  // Count-pills on the tabs — the reference's at-a-glance queue signal.
  const openCount = (listData ?? []).filter((r) => r.status !== "closed" && r.status !== "approved").length;
  const onMe = myWork
    ? myWork.awaiting_review.length + myWork.my_tickets.length + myWork.my_tasks.length
    : 0;
  const atRiskCount = (listData ?? []).filter((r) => r.sla_status === "at_risk").length;
  const overdueCount = (listData ?? []).filter((r) => r.sla_status === "overdue").length;

  return (
    <MockupShell
      active={section === "mywork" ? "mywork" : "intake"}
      title="Legal Intake"
      subtitle="one front door — every request lands here"
      onMyWork={() => { setSection("mywork"); setDetailId(null); }}
      stats={isStaff ? (
        <span className="stat">
          <span><b>{openCount}</b> open</span>
          {atRiskCount > 0 ? <span className="warn"><b>{atRiskCount}</b> at risk</span> : null}
          {overdueCount > 0 ? <span className="crit"><b>{overdueCount}</b> overdue</span> : null}
        </span>
      ) : null}
      actions={
        <>
          {isStaff ? (
            <button className="iconbtn" title={gmailSyncing ? "Syncing…" : "Sync email"} disabled={gmailSyncing} onClick={syncGmail}>
              <Mail className="h-4 w-4" />
            </button>
          ) : null}
          {isStaff ? (
            <select className="sortsel" value="" onChange={(e) => { if (e.target.value) { setSection(e.target.value); setDetailId(null); } }}>
              <option value="">More…</option>
              <option value="self">Self-Service</option>
              <option value="workflows">Workflows</option>
              <option value="ops">Operations</option>
            </select>
          ) : null}
          <button className="primary" onClick={() => { setSection("new"); setDetailId(null); }}>
            <Plus className="h-4 w-4" />New request
          </button>
        </>
      }
    >
      {isStaff && section !== "queue" && !detailId ? (
        <button onClick={() => setSection("queue")} className="btn sm" style={{ marginTop: 14 }}>← Back to Inbox</button>
      ) : null}

      {detailId ? (
        <RequestOverview id={detailId} canManage={isStaff} onBack={() => setDetailId(null)} />
      ) : (
        <>
          {section === "queue" && <LegalIntakeBoard onOpen={setDetailId} />}
          {section === "mywork" && <MyWorkView isStaff={isStaff} onOpen={setDetailId} />}
          {section === "new" && <NewRequestTab onFiled={setDetailId} seed={newRequestSeed} />}
          {section === "self" && <SelfServiceTab onFileTopic={(t) => { setNewRequestSeed(`Re: ${t}\n\n`); setSection("new"); }} />}
          {section === "workflows" && isStaff && <WorkflowsBuilderTab />}
          {section === "ops" && isStaff && <OperationsView isAdmin={isAdmin} />}
        </>
      )}
    </MockupShell>
  );
}

// ---- shared bits ----------------------------------------------------------

function StatusPill({ r }: { r: IntakeRequest }) {
  return (
    <Badge tone={STATUS_TONE[r.status] as never}>{STATUS_LABEL[r.status]}</Badge>
  );
}

function fmtElapsedHours(h: number): string {
  const totalMin = Math.max(0, Math.round(h * 60));
  const hh = Math.floor(totalMin / 60);
  const mm = totalMin % 60;
  return hh > 0 ? `${hh}h ${mm}m` : `${mm}m`;
}

function SlaCell({ r }: { r: IntakeRequest }) {
  const now = useNow(1000);
  if (r.status === "closed" || r.status === "approved")
    return <span className="text-xs text-slate-400">—</span>;
  const posture = r.sla_status as IntakeSlaPosture;
  const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
  const elapsedMs = submitted != null ? now - submitted : null;
  const livePct = elapsedMs != null ? Math.min(100, (elapsedMs / (r.sla_hours * 3_600_000)) * 100) : r.sla_pct;
  return (
    <div className="flex flex-col gap-1">
      {elapsedMs != null && (
        <span className="text-xs tabular-nums text-slate-600">
          {fmtDurLive(elapsedMs)} <span className="text-slate-400">of {r.sla_hours}h</span>
        </span>
      )}
      <div className="flex items-center gap-2">
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full transition-[width] duration-1000 ease-linear"
            style={{ width: `${livePct}%`, background: slaBarColor(posture) }}
          />
        </div>
        <Badge tone={POSTURE_TONE[posture] as never}>{POSTURE_LABEL[posture]}</Badge>
      </div>
    </div>
  );
}

function RequestRow({ r, onOpen, showStatus = true, selectable = false, checked = false, onToggle, showRequester = false, showDescription = false }: { r: IntakeRequest; onOpen: (id: string) => void; showStatus?: boolean; selectable?: boolean; checked?: boolean; onToggle?: (id: string) => void; showRequester?: boolean; showDescription?: boolean }) {
  return (
    <TR
      className={cn(
        "cursor-pointer transition-colors hover:bg-slate-100/70",
        showRequester && r.sla_status === "overdue" && "bg-danger-subtle",
        showRequester && r.sla_status === "at_risk" && "bg-warning-subtle",
      )}
      onClick={() => onOpen(r.id)}
    >
      {selectable && (
        <TD>
          <input type="checkbox" className="accent-brand-600" checked={checked}
            aria-label={`Select ${r.ref}`}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onToggle?.(r.id)} />
        </TD>
      )}
      <TD>
        <span className="inline-flex items-center gap-2">
          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full",
            r.sla_status === "overdue" ? "bg-danger"
              : r.sla_status === "at_risk" ? "bg-warning" : "bg-success")}
            title={`SLA ${r.sla_status}`} />
          <span className="font-mono text-xs text-slate-500">{r.ref}</span>
        </span>
      </TD>
      {showRequester && (
        <TD className="whitespace-nowrap">
          <div className="font-medium text-slate-900">{r.requester_name ?? "—"}</div>
          {r.department && <div className="text-xs text-slate-400">{r.department}</div>}
        </TD>
      )}
      <TD className="whitespace-nowrap font-medium text-slate-900">{r.type_label}</TD>
      {showDescription && (
        <TD className="max-w-[16rem] truncate text-slate-600" title={r.subject || r.description || undefined}>
          {r.subject || r.description || <span className="text-slate-400">—</span>}
        </TD>
      )}
      <TD><Badge tone={PRIORITY_TONE[r.priority] as never}>{r.priority}</Badge></TD>
      <TD><SlaCell r={r} /></TD>
      {showStatus && <TD><StatusPill r={r} /></TD>}
      <TD className="text-slate-600">{r.assigned_to_label ?? <span className="text-slate-400">Unassigned</span>}</TD>
    </TR>
  );
}

// ---- New Request ----------------------------------------------------------


// One landing for filing + self-serve: a segmented switch between the
// structured form (reference "route to agent" styling), the copilot chat, and
// the self-service KB. Self-Service is no longer its own tab — it lives here so
// a requester tries to deflect before filing.
const DEPARTMENTS = ["Product", "Engineering", "Sales", "HR", "Finance", "Procurement", "Marketing", "Operations", "Legal", "Executive"];
const URGENCIES = ["Standard", "Priority", "Urgent — deadline this week", "Emergency — deal blocker"];
// Built-in categories that don't map to a configured type — they file as a
// general request (the configured types render first, with dynamic fields).
const BUILTIN_EXTRAS = ["IP Question", "Vendor Due Diligence", "Contract Question", "Legal Question — General", "Other"];

function urgencyToPriority(u: string): string {
  if (u.startsWith("Emergency")) return "Critical";
  if (u.startsWith("Urgent") || u === "Priority") return "High";
  return "Medium";
}

function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => { const s = String(r.result); const i = s.indexOf(","); resolve(i >= 0 ? s.slice(i + 1) : s); };
    r.onerror = () => reject(new Error("Could not read file"));
    r.readAsDataURL(file);
  });
}

// --- New Request catalog (Screen 5) — scoped under `.nr`, reuses the shell palette.
const nrSvg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;
const NR_ICONS: [RegExp, string][] = [
  [/nda|non.?disclos|confidential/i, '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>'],
  [/msa|master|service/i, '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>'],
  [/dpa|data|privacy|gdpr/i, '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/>'],
  [/vendor|supplier|procure/i, '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'],
  [/review/i, '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>'],
  [/question|triage|general|ask/i, '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.8.4-1 .9-1 1.7M12 17h.01"/>'],
];
const nrIcon = (s: string): string => (NR_ICONS.find(([re]) => re.test(s)) ?? [null, '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>'])[1] as string;

const NR_CSS = `
.nr{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--ext:#75589f;--ext-soft:#efe8f7;--ai:#5a49c0;--ai-soft:#ecebf9;--shadow:0 1px 2px rgba(20,26,40,.05),0 10px 26px rgba(20,26,40,.06);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;max-width:940px;margin:0 auto;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .nr{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--ext:#ab90d2;--ext-soft:#221b31;--ai:#a99cf0;--ai-soft:#1c1a33;--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 30px rgba(0,0,0,.4)}
.nr .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.nr h2{margin:0;font-weight:660;letter-spacing:-.015em}
.nr .btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 13px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer}
.nr .btn:hover{background:var(--surface-2)} .nr .btn.pri{background:var(--ai);border-color:var(--ai);color:#fff} .nr .btn.pri:hover{filter:brightness(1.06)}
.nr .nrback{background:none;border:0;color:var(--accent);font-weight:600;font-size:12.5px;cursor:pointer;padding:0 0 12px}
.nr .autonote{display:flex;align-items:center;gap:11px;padding:12px 15px;border:1px solid var(--border);border-radius:11px;background:var(--surface);color:var(--ink-2);font-size:12.5px;box-shadow:var(--shadow)}
.nr .autonote .g{width:30px;height:30px;border-radius:8px;background:var(--good-soft);color:var(--good);display:grid;place-items:center;flex:none}
.nr .autonote b{color:var(--ink)}
.nr .ways{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}
.nr .way{border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:var(--shadow);padding:18px 19px;display:flex;flex-direction:column;gap:6px}
.nr .way.aiway{border-color:color-mix(in srgb,var(--ai) 30%,var(--border));background:linear-gradient(120deg,var(--ai-soft),var(--surface))}
.nr .way .wico{width:40px;height:40px;border-radius:11px;display:grid;place-items:center;font-size:19px;margin-bottom:5px}
.nr .way.aiway .wico{background:var(--ai);color:#fff} .nr .way.formway .wico{background:var(--accent-soft);color:var(--accent)} .nr .way.formway .wico .ic{width:20px;height:20px}
.nr .way h2{font-size:14.5px} .nr .way p{margin:0;font-size:12.5px;color:var(--ink-2);line-height:1.5;flex:1}
.nr .way .btn{margin-top:10px;align-self:flex-start}
.nr .catlbl{margin:24px 2px 12px;font:600 11px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.nr .types{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.nr .type{text-align:left;border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);padding:14px;cursor:pointer;transition:.12s;font:inherit;color:inherit}
.nr .type:hover{border-color:var(--accent);transform:translateY(-1px)}
.nr .type .ti{width:32px;height:32px;border-radius:9px;display:grid;place-items:center;margin-bottom:10px;background:var(--accent-soft);color:var(--accent)}
.nr .type .tn{font-weight:660;font-size:13px} .nr .type .td{font-size:11.5px;color:var(--ink-2);margin-top:3px;line-height:1.4}
.nr .type .tw{margin-top:9px;font:600 9.5px var(--sans);text-transform:uppercase;letter-spacing:.04em;color:var(--ink-3)}
/* --- structured request form --- */
.nr .nrform{display:flex;flex-direction:column;gap:16px;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
.nr .fhead{display:flex;align-items:center;gap:12px}
.nr .fhic{width:38px;height:38px;border-radius:10px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center} .nr .fhic .ic{width:19px;height:19px}
.nr .fhtx .fhk{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)} .nr .fhtx h2{font-size:17px}
.nr .tchips{display:flex;flex-wrap:wrap;gap:7px}
.nr .tchip{border:1px solid var(--border);border-radius:99px;padding:6px 12px;font-weight:600;font-size:12px;color:var(--ink-2);background:var(--surface);cursor:pointer}
.nr .tchip:hover{border-color:var(--border-strong);color:var(--ink)} .nr .tchip.on{background:var(--accent-soft);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 34%,var(--border))}
.nr .wfrow{display:flex;align-items:center;gap:7px;flex-wrap:wrap} .nr .wlab{font:600 9.5px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
.nr .wchip{font:600 10.5px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:6px;padding:2px 8px}
.nr .fsec{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:16px 18px}
.nr .fsh{font:600 11px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3);margin-bottom:14px}
.nr .fgrid{display:grid;grid-template-columns:1fr 1fr;gap:15px 16px}
.nr .fld{display:flex;flex-direction:column;gap:6px;min-width:0} .nr .fld.wide{grid-column:1/-1}
.nr .lab{font-weight:600;font-size:12.5px;color:var(--ink)} .nr .req{color:var(--crit,#bb4835)}
.nr .inp,.nr .sel,.nr .ta{width:100%;padding:9px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 13px var(--sans)}
.nr .inp:focus,.nr .sel:focus,.nr .ta:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.nr .ta{resize:vertical;line-height:1.5;min-height:66px}
.nr .inp.bad,.nr .sel.bad,.nr .ta.bad{border-color:var(--crit,#bb4835);box-shadow:0 0 0 3px color-mix(in srgb,var(--crit,#bb4835) 16%,transparent)}
.nr .help{font-size:11px;color:var(--ink-3);line-height:1.4}
.nr .chk{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--ink);cursor:pointer;padding-top:4px} .nr .chk input{width:16px;height:16px;accent-color:var(--accent)}
.nr .drop{display:flex;align-items:center;gap:11px;flex-wrap:wrap}
.nr .dropbtn{display:inline-flex;align-items:center;gap:7px;border:1px dashed var(--border-strong);border-radius:9px;padding:9px 13px;font-weight:600;font-size:12px;color:var(--accent);cursor:pointer} .nr .dropbtn:hover{border-color:var(--accent);background:var(--accent-soft)}
.nr .filepill{display:flex;align-items:center;justify-content:space-between;gap:8px;border:1px solid var(--border);border-left:2px solid var(--good);background:var(--good-soft);border-radius:8px;padding:8px 11px;font-size:12px;margin-top:8px}
.nr .fbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-top:2px}
.nr .routepill{border:1px solid color-mix(in srgb,var(--accent) 22%,var(--border));background:var(--accent-soft);color:var(--accent);border-radius:8px;padding:7px 12px;font-size:12px;font-weight:600} .nr .routepill b{font-weight:700}
.nr .miss{font-size:12px;color:var(--warn,#a9772b);font-weight:600}
.nr .fbar .btn.pri{padding:10px 18px;font-size:13px}
/* --- intake assistant chat --- */
.nr .cochat{display:flex;flex-direction:column;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:var(--shadow);overflow:hidden}
.nr .cochat .coh{display:flex;align-items:center;gap:10px;padding:12px 15px;border-bottom:1px solid var(--border)}
.nr .cochat .coh .glb{width:30px;height:30px;border-radius:9px;background:var(--ai-soft);color:var(--ai);display:grid;place-items:center;font-size:15px}
.nr .cochat .coh .con{font-weight:660;font-size:13px} .nr .cochat .coh .cos{font-size:11px;color:var(--ink-3)}
.nr .cochat .cothread{display:flex;flex-direction:column;gap:12px;padding:16px;overflow-y:auto;max-height:56vh;min-height:230px;background:var(--inset)}
.nr .cochat .comsg{display:flex;gap:9px;max-width:100%} .nr .cochat .comsg.you{flex-direction:row-reverse}
.nr .cochat .mav{width:25px;height:25px;border-radius:7px;flex:none;display:grid;place-items:center;font:700 9px var(--sans)}
.nr .cochat .comsg.agent .mav{background:var(--ai-soft);color:var(--ai)} .nr .cochat .comsg.you .mav{background:var(--accent-soft);color:var(--accent)}
.nr .cochat .cobub{padding:10px 13px;border-radius:13px;font-size:13px;line-height:1.55;max-width:82%;white-space:pre-wrap}
.nr .cochat .comsg.agent .cobub{background:var(--surface);border:1px solid var(--border);border-top-left-radius:4px} .nr .cochat .comsg.you .cobub{background:var(--accent);color:var(--accent-ink);border-top-right-radius:4px}
.nr .cochat .cobub.dim{color:var(--ink-3)}
.nr .cochat .reqprev{align-self:flex-start;width:min(320px,86%);border:1px solid color-mix(in srgb,var(--accent) 26%,var(--border));border-radius:12px;overflow:hidden;background:var(--surface)}
.nr .cochat .reqprev .rph{display:flex;align-items:center;gap:7px;padding:8px 12px;background:var(--accent-soft);color:var(--accent);font-weight:660;font-size:12px} .nr .cochat .reqprev .rph .ic{width:14px;height:14px}
.nr .cochat .reqprev .rph .ready{margin-left:auto;font:600 9px var(--sans);text-transform:uppercase;letter-spacing:.05em;background:var(--good-soft);color:var(--good);padding:2px 7px;border-radius:99px}
.nr .cochat .reqprev .rpb{padding:8px 12px 11px;display:flex;flex-direction:column;gap:4px}
.nr .cochat .reqprev .rpr{display:flex;justify-content:space-between;gap:12px;font-size:12px} .nr .cochat .reqprev .rpk{color:var(--ink-3);text-transform:capitalize} .nr .cochat .reqprev .rpv{font-weight:600;text-align:right}
.nr .cochat .filebtn{margin:12px 16px 0;justify-content:center}
.nr .cochat .cocomposer{display:flex;gap:9px;align-items:center;margin:12px 16px 16px;border:1px solid var(--border-strong);border-radius:13px;background:var(--surface);padding:6px 6px 6px 14px}
.nr .cochat .cocomposer input{flex:1;border:0;background:none;outline:none;color:var(--ink);font-size:13px;padding:6px 0}
.nr .cochat .cosend{width:34px;height:34px;border-radius:10px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;flex:none;border:0;cursor:pointer} .nr .cochat .cosend .ic{width:16px;height:16px} .nr .cochat .cosend:disabled{opacity:.5;cursor:default}
@media (max-width:900px){.nr .ways{grid-template-columns:1fr}.nr .types{grid-template-columns:repeat(2,1fr)}}
@media (max-width:640px){.nr .types{grid-template-columns:1fr}.nr .fgrid{grid-template-columns:1fr}}
`;

function NewRequestTab({ onFiled, seed = "" }: { onFiled: (id: string) => void; seed?: string }) {
  const { data: types } = useQuery({ queryKey: ["intake-types"], queryFn: () => intakeApi.listTypes() });
  const [mode, setMode] = useState<"catalog" | "form" | "chat">("catalog");
  const [seedDesc, setSeedDesc] = useState(seed);
  const [preType, setPreType] = useState("");
  const typesRef = useRef<HTMLDivElement>(null);
  // A topic handed over from the Self-Service tab seeds the structured form.
  useEffect(() => { if (seed) { setSeedDesc(seed); setMode("form"); } }, [seed]);

  // The catalog: real request types from the DB + two built-in intake paths.
  const catalog = [
    ...(types ?? []).map((t) => ({ key: t.id, name: t.name, desc: t.description ?? "", tag: t.workstream ?? "Workflow", icon: nrIcon(`${t.name} ${t.key}`) })),
    { key: "Contract Question", name: "Review a contract", desc: "Send us their paper to check against the playbook.", tag: "Review workflow", icon: nrIcon("review") },
    { key: "Legal Question — General", name: "General legal question", desc: "Not sure what you need? Just ask legal.", tag: "Triage", icon: nrIcon("question") },
  ];

  if (mode !== "catalog") {
    return (
      <div className="nr">
        <style dangerouslySetInnerHTML={{ __html: NR_CSS }} />
        <button className="nrback" onClick={() => setMode("catalog")}>← Raise a request</button>
        {mode === "form"
          ? <RequestForm types={types ?? []} onFiled={onFiled} initialDesc={seedDesc} initialType={preType} />
          : <div style={{ maxWidth: 760, margin: "0 auto" }}><CopilotChat onFiled={onFiled} /></div>}
      </div>
    );
  }

  return (
    <div className="nr">
      <style dangerouslySetInnerHTML={{ __html: NR_CSS }} />
      <div className="autonote"><div className="g">{nrSvg('<path d="M3 7l9 6 9-6"/><rect x="3" y="5" width="18" height="14" rx="2"/>')}</div><div><b>Email requests come in automatically.</b> Your inbox is connected — messages are read, understood and raised as requests with no pasting. This page is for raising one yourself, two ways:</div></div>
      <div className="ways">
        <div className="way aiway"><div className="wico">✦</div><h2>Chat with the intake assistant</h2><p>Describe what you need in plain words. The assistant interviews you, fills in the details, and raises the request — no form.</p><button className="btn pri" onClick={() => setMode("chat")}>Start a conversation →</button></div>
        <div className="way formway"><div className="wico">{nrSvg('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>')}</div><h2>Fill a request form</h2><p>Know what you want? Pick a type and complete its full intake form — every field legal needs for that request.</p><button className="btn" onClick={() => typesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}>Choose a type ↓</button></div>
      </div>
      <div className="catlbl" ref={typesRef}>Request types</div>
      <div className="types">
        {catalog.map((c) => (
          <button key={c.key} className="type" onClick={() => { setPreType(c.key); setMode("form"); }}>
            <div className="ti">{nrSvg(c.icon)}</div>
            <div className="tn">{c.name}</div>
            {c.desc ? <div className="td">{c.desc}</div> : null}
            <div className="tw">→ {c.tag}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function RequestForm({ types, onFiled, initialDesc, initialType = "" }: { types: IntakeRequestType[]; onFiled: (id: string) => void; initialDesc: string; initialType?: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const [subject, setSubject] = useState("");
  const [department, setDepartment] = useState("Product");
  const [urgency, setUrgency] = useState("Standard");
  const [typeSel, setTypeSel] = useState<string>(initialType);
  const [values, setValues] = useState<Record<string, string>>({});
  const [description, setDescription] = useState(initialDesc);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [showErr, setShowErr] = useState(false);

  // A configured request-type from the DB carries dynamic fields + a stage
  // ladder; the built-in extras carry only their label and file as general.
  const gridItems = [
    ...types.map((t) => ({ key: t.id, label: t.name, type: t as IntakeRequestType | null })),
    ...BUILTIN_EXTRAS.map((label) => ({ key: label, label, type: null as IntakeRequestType | null })),
  ];
  const selected = gridItems.find((g) => g.key === typeSel) ?? null;
  const selType = selected?.type ?? null;

  const missingRequired = (selType?.fields ?? []).filter((f) => f.required && !String(values[f.key] ?? "").trim());
  const preview = derivePreview(description, selType ?? undefined);
  const canSubmit = !!subject.trim() && (description.trim().length >= 10 || !!file) && missingRequired.length === 0;

  async function submit() {
    setBusy(true);
    try {
      const r = await intakeApi.create({
        type_label: selType ? selType.name + " Request" : (selected ? selected.label : "General request"),
        subject: subject.trim() || null,
        request_type_id: selType?.id ?? null,
        priority: urgencyToPriority(urgency),
        department: department || null,
        requester_name: user?.full_name ?? null,
        description,
        field_values: Object.keys(values).length ? values : null,
      });
      if (file) {
        const content_b64 = await fileToB64(file);
        await intakeApi.uploadDocument(r.id, { filename: file.name, mime_type: file.type || "application/octet-stream", content_b64 });
        await intakeApi.ingestAttachment(r.id);
      }
      qc.invalidateQueries({ queryKey: ["intake-mine"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Filed ${r.ref} — routed for triage`, "success");
      onFiled(r.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Submit failed", "error");
    } finally { setBusy(false); }
  }

  const needDesc = description.trim().length < 10 && !file;

  return (
    <div className="nrform">
      {/* type header + inline change */}
      <div className="fhead">
        <div className="fhic">{nrSvg(nrIcon(selType?.name ?? selected?.label ?? "request"))}</div>
        <div className="fhtx"><div className="fhk">New request</div><h2>{selType?.name ?? selected?.label ?? "General request"}</h2></div>
      </div>
      <div className="tchips">
        {gridItems.map((g) => (
          <button key={g.key} type="button" className={`tchip${typeSel === g.key ? " on" : ""}`}
            onClick={() => { setTypeSel(typeSel === g.key ? "" : g.key); setValues({}); }}>{g.label}</button>
        ))}
      </div>
      {selType && (selType.stages?.length ?? 0) > 0 && (
        <div className="wfrow"><span className="wlab">Workflow</span>{(selType.stages ?? []).map((s, i) => <span key={i} className="wchip">{i + 1}. {titleCase(s)}</span>)}</div>
      )}

      {/* the basics */}
      <div className="fsec"><div className="fsh">The basics</div>
        <div className="fgrid">
          <div className="fld wide"><label className="lab">Subject <span className="req">*</span></label>
            <input className={`inp${showErr && !subject.trim() ? " bad" : ""}`} value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. Mutual NDA with Acme Corp" />
            <div className="help">A short title legal will see in the queue.</div></div>
        </div>
      </div>

      {/* type-specific deal terms */}
      {(selType?.fields ?? []).length > 0 && (
        <div className="fsec"><div className="fsh">Deal terms</div>
          <div className="fgrid">
            {[...(selType?.fields ?? [])].sort((a, b) => a.sort_order - b.sort_order).map((f) => (
              <NrField key={f.key} f={f} value={values[f.key] ?? ""} showError={showErr} onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))} />
            ))}
          </div>
        </div>
      )}

      {/* context */}
      <div className="fsec"><div className="fsh">Context</div>
        <div className="fgrid">
          <div className="fld wide"><label className="lab">Describe your request <span className="req">*</span></label>
            <textarea className={`ta${showErr && needDesc ? " bad" : ""}`} rows={6} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="E.g. Mutual NDA for discussions with Acme Corp — 2-year term, Delaware law." />
            <div className="help">Be specific — triage and the AI agent route on this. Or attach the paper below.</div></div>
          <div className="fld wide"><label className="lab">Attach a document <span className="help" style={{ fontWeight: 400 }}>· optional</span></label>
            <div className="drop">
              <label className="dropbtn"><Paperclip className="h-3.5 w-3.5" /> Choose file
                <input type="file" accept=".docx,.txt,.text,.md,.pdf,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ display: "none" }} /></label>
              <span className="help">Word, text, or PDF · max 3 MB. Scanned PDFs can't be read.</span>
            </div>
            {file && <div className="filepill"><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>📄 {file.name} · {(file.size / 1024).toFixed(0)} KB</span><button type="button" onClick={() => setFile(null)} style={{ background: "none", border: 0, cursor: "pointer", color: "var(--ink-3)", fontSize: 13 }}>✕</button></div>}
          </div>
        </div>
      </div>

      {/* priority & routing */}
      <div className="fsec"><div className="fsh">Priority &amp; routing</div>
        <div className="fgrid">
          <div className="fld"><label className="lab">Department <span className="req">*</span></label>
            <select className="sel" value={department} onChange={(e) => setDepartment(e.target.value)}>{DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}</select></div>
          <div className="fld"><label className="lab">Urgency</label>
            <select className="sel" value={urgency} onChange={(e) => setUrgency(e.target.value)}>{URGENCIES.map((u) => <option key={u}>{u}</option>)}</select></div>
        </div>
      </div>

      {/* submit bar */}
      <div className="fbar">
        {preview && <div className="routepill">Routes as <b>{preview}</b></div>}
        <div style={{ flex: 1 }} />
        {showErr && !canSubmit && <span className="miss">Still needed: {[!subject.trim() ? "Subject" : null, needDesc ? "a description or a file" : null, ...missingRequired.map((f) => f.label)].filter(Boolean).join(", ")}</span>}
        <button className="btn pri" disabled={busy} onClick={() => { setShowErr(true); if (canSubmit) submit(); }}>{busy ? "Filing…" : "File request →"}</button>
      </div>
    </div>
  );
}

// A dynamic request-type field, rendered in the `.nr` form style. Textareas span
// the full width; a required-but-empty field shows a red border once submit is tried.
function NrField({ f, value, onChange, showError }: { f: IntakeFieldSpec; value: string; onChange: (v: string) => void; showError: boolean }) {
  const bad = showError && f.required && !String(value).trim();
  const lab = <label className="lab">{f.label}{f.required ? <span className="req"> *</span> : null}</label>;
  if (f.kind === "boolean")
    return <div className="fld"><label className="chk"><input type="checkbox" checked={value === "true"} onChange={(e) => onChange(e.target.checked ? "true" : "false")} />{f.label}{f.required ? <span className="req"> *</span> : null}</label></div>;
  if (f.kind === "textarea")
    return <div className="fld wide">{lab}<textarea className={`ta${bad ? " bad" : ""}`} rows={3} value={value} onChange={(e) => onChange(e.target.value)} /></div>;
  if (f.kind === "select")
    return (
      <div className="fld">{lab}
        <select className={`sel${bad ? " bad" : ""}`} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Select…</option>
          {(f.options ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
    );
  const type = f.kind === "date" ? "date" : f.kind === "number" ? "number" : "text";
  return <div className="fld">{lab}<input type={type} className={`inp${bad ? " bad" : ""}`} value={value} onChange={(e) => onChange(e.target.value)} /></div>;
}

function derivePreview(desc: string, type?: IntakeRequestType): string | null {
  const v = desc.toLowerCase();
  if (type) return `${type.name}${type.workstream ? ` · ${type.workstream}` : ""}`;
  if (!v.trim()) return null;
  if (/litig|dispute/.test(v)) return "Litigation → senior counsel";
  if (/privacy|dpa|data protection/.test(v)) return "Privacy / DPA → DPO review";
  if (/nda|non-disclosure/.test(v)) return "NDA → fast lane";
  if (/msa|contract|review/.test(v)) return "Contract review → counsel";
  return "General → triage queue";
}

// ---- My Requests (requester portal) --------------------------------------

const MY_REQUESTS_PAGE_SIZE = 25;

function MyRequestsTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-mine"], queryFn: intakeApi.mine });
  const [page, setPage] = useState(1);
  if (isLoading) return <CenterSpinner label="Loading your requests…" />;
  if (error) return <ErrorState error={error} />;
  const rows = data ?? [];
  if (!rows.length)
    return <EmptyState icon={<DoorOpen className="h-5 w-5" />} title="No requests yet"
      description="File your first request from the New Request tab." />;
  const pageCount = Math.max(1, Math.ceil(rows.length / MY_REQUESTS_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageRows = rows.slice((currentPage - 1) * MY_REQUESTS_PAGE_SIZE, currentPage * MY_REQUESTS_PAGE_SIZE);
  return (
    <Card>
      <Table>
        <THead><TR><TH>Ref</TH><TH>Request</TH><TH>Priority</TH><TH>SLA</TH><TH>Status</TH><TH>Assignee</TH></TR></THead>
        <tbody>{pageRows.map((r) => <RequestRow key={r.id} r={r} onOpen={onOpen} />)}</tbody>
      </Table>
      <Pagination page={currentPage} pageCount={pageCount} onPageChange={setPage}
        totalItems={rows.length} pageSize={MY_REQUESTS_PAGE_SIZE} />
    </Card>
  );
}

// ---- Inbox (staff, whole queue) ------------------------------------------

// Inbox filters — mirrors the reference intake app's Inbox chips.
function buildFilters(myId: string | null): { id: string; label: string; match: (r: IntakeRequest) => boolean }[] {
  return [
    { id: "all", label: "All", match: () => true },
    { id: "mine", label: "My Queue", match: (r) => !!myId && r.assigned_to_user_id === myId },
    { id: "overdue", label: "SLA Breached", match: (r) => r.sla_status === "overdue" },
    { id: "at_risk", label: "At risk", match: (r) => r.sla_status === "at_risk" },
    { id: "open", label: "Open", match: (r) => r.status === "open" },
    { id: "escalated", label: "Escalated", match: (r) => r.status === "escalated" },
    { id: "approved", label: "Approved", match: (r) => r.status === "approved" },
  ];
}

// ---- Operations Cockpit (Inbox landing) -----------------------------------
const INBOX_PAGE = 25;

type InboxSortKey = "ref" | "requester" | "type" | "description" | "priority" | "sla" | "status" | "assignee";

const INBOX_COLUMNS: { key: InboxSortKey; label: string }[] = [
  { key: "ref", label: "ID" },
  { key: "requester", label: "Requester" },
  { key: "type", label: "Type" },
  { key: "description", label: "Description" },
  { key: "priority", label: "Priority" },
  { key: "sla", label: "SLA" },
  { key: "status", label: "Status" },
  { key: "assignee", label: "Assignee" },
];

const PRIORITY_RANK: Record<string, number> = { Low: 0, Medium: 1, High: 2, Critical: 3 };
const STATUS_RANK: Record<string, number> = Object.fromEntries(Object.keys(STATUS_LABEL).map((s, i) => [s, i]));

function inboxSortValue(r: IntakeRequest, key: InboxSortKey): number | string {
  switch (key) {
    case "ref": return parseInt(r.ref.replace(/\D/g, ""), 10) || 0;
    case "requester": return (r.requester_name ?? "").toLowerCase();
    case "type": return (r.type_label ?? "").toLowerCase();
    case "description": return (r.subject || r.description || r.type_label || "").toLowerCase();
    case "priority": return PRIORITY_RANK[r.priority] ?? -1;
    case "sla": return r.sla_pct ?? 0;
    case "status": return STATUS_RANK[r.status] ?? -1;
    case "assignee": return (r.assigned_to_label ?? "").toLowerCase();
  }
}

// AI intake briefing — reads the whole queue and surfaces what needs attention
// before you scan a single row. Computed live from the same request list.
function IntakeBriefing({ rows, onOpen, open, setOpen }: { rows: IntakeRequest[]; onOpen: (id: string) => void; open: boolean; setOpen: (b: boolean) => void }) {
  const openRows = rows.filter((r) => r.status !== "closed" && r.status !== "approved");
  const overdue = openRows.filter((r) => r.sla_status === "overdue");
  const atRisk = openRows.filter((r) => r.sla_status === "at_risk");
  const unassigned = openRows.filter((r) => !r.assigned_to_user_id);
  const attention = [...overdue, ...atRisk].slice(0, 6);
  const worst = overdue[0] ?? atRisk[0];
  // Team load — in-flight work by the team currently holding it.
  const inflight = openRows.filter((r) => (r.workflow ?? []).some((s) => s.active) || r.status !== "open");
  const loadMap = new Map<string, { total: number; stuck: number }>();
  inflight.forEach((r) => {
    const t = r.assigned_to_label ?? "Unassigned";
    const e = loadMap.get(t) ?? { total: 0, stuck: 0 };
    e.total += 1; if (r.sla_status === "overdue") e.stuck += 1;
    loadMap.set(t, e);
  });
  const load = [...loadMap.entries()].sort((a, b) => b[1].total - a[1].total).slice(0, 6);
  const maxLoad = Math.max(1, ...load.map(([, v]) => v.total));
  const awaitingTriage = openRows.filter((r) => r.status === "open").length;
  const inWorkflow = openRows.filter((r) => (r.workflow ?? []).some((s) => s.active)).length;
  const confs = openRows.map((r) => (r.ai_triage as { flow_suggestion?: WorkflowSuggestion } | null)?.flow_suggestion?.confidence).filter((c): c is number => typeof c === "number");
  const avgConf = confs.length ? Math.round((confs.reduce((a, b) => a + b, 0) / confs.length) * 100) : null;
  const tiles: { n: number | string; l: string; tone: string }[] = [
    { n: awaitingTriage, l: "awaiting triage", tone: "text-brand-700" },
    { n: inWorkflow, l: "in workflow", tone: "text-slate-900" },
    { n: atRisk.length, l: "at risk", tone: "text-warning" },
    { n: overdue.length, l: "overdue", tone: "text-danger" },
    { n: unassigned.length, l: "unassigned", tone: "text-slate-900" },
    { n: avgConf != null ? `${avgConf}%` : "—", l: "avg AI confidence", tone: "text-slate-900" },
  ];
  return (
    <Card className="border-brand-200/70">
      <CardBody className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-brand-600"><Bot className="h-4 w-4" /></span>
          <div className="text-sm font-semibold text-slate-900">Intake briefing</div>
          <span className="text-[11px] text-slate-400">· reads the whole queue</span>
          <button type="button" onClick={() => setOpen(!open)} className="ml-auto rounded-md px-2 py-1 text-xs font-semibold text-brand-700 hover:bg-slate-200/60">{open ? "Hide" : "Show"}</button>
        </div>
        {open ? (
          <>
            <p className="text-[13.5px] leading-relaxed text-slate-700">
              <b className="font-semibold text-slate-900">{openRows.length} open requests.</b>{" "}
              {atRisk.length} at risk{overdue.length ? <>, <span className="font-semibold text-danger">{overdue.length} overdue</span></> : null}.{" "}
              {worst ? <>Start with <span className="font-semibold text-slate-900">{worst.ref}</span> — {worst.subject || worst.type_label}, {POSTURE_LABEL[worst.sla_status as IntakeSlaPosture].toLowerCase()}. </> : null}
              {unassigned.length ? <>{unassigned.length} still unassigned.</> : "Everything's assigned."}
            </p>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
              {tiles.map((t) => (
                <div key={t.l} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                  <div className={cn("font-mono text-xl font-semibold tabular-nums", t.tone)}>{t.n}</div>
                  <div className="mt-0.5 text-[10.5px] text-slate-500">{t.l}</div>
                </div>
              ))}
            </div>
            {load.length > 0 && (
              <div>
                <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.06em] text-slate-500">Workload · in-flight by owner</div>
                <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
                  {load.map(([name, v]) => (
                    <div key={name} className="flex items-center gap-3">
                      <span className="w-28 shrink-0 truncate text-[12px] font-medium text-slate-700">{name}</span>
                      <span className="flex h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
                        <span className="h-full bg-success" style={{ width: `${((v.total - v.stuck) / maxLoad) * 100}%` }} />
                        <span className="h-full bg-danger" style={{ width: `${(v.stuck / maxLoad) * 100}%` }} />
                      </span>
                      <span className="w-12 shrink-0 text-right font-mono text-[11px] text-slate-500">{v.total}{v.stuck ? <span className="text-danger"> ·{v.stuck}</span> : null}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {attention.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-[0.06em] text-slate-500">Needs you now</span>
                {attention.map((r) => (
                  <button key={r.id} type="button" onClick={() => onOpen(r.id)}
                    className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold",
                      r.sla_status === "overdue" ? "border-danger/40 bg-danger-subtle text-danger" : "border-warning/40 bg-warning-subtle text-warning")}>
                    {r.ref} · {r.requester_name ?? r.type_label}
                  </button>
                ))}
              </div>
            )}
          </>
        ) : null}
      </CardBody>
    </Card>
  );
}

// Stage / recommendation for a request: if it has a workflow, show where it is;
// otherwise fall back to the AI's flow suggestion (name + confidence).
function stageBadge(r: IntakeRequest): { kind: "stage" | "rec"; text: string; sub: string } | null {
  const steps = r.workflow ?? [];
  if (steps.length) {
    const done = steps.filter((s) => s.done).length;
    const active = steps.find((s) => s.active) ?? steps[done] ?? steps[steps.length - 1];
    return { kind: "stage", text: active?.label ?? "In progress", sub: `${done}/${steps.length}` };
  }
  const fs = (r.ai_triage as { flow_suggestion?: WorkflowSuggestion } | null)?.flow_suggestion;
  if (fs?.flow_name) return { kind: "rec", text: fs.flow_name, sub: `${Math.round((fs.confidence ?? 0) * 100)}%` };
  return null;
}

// Inbox — the reference "Legal Mission Control" list: a KPI strip, filter chips,
// and ONE dense full-width table (ID · Requester · Type · Description · Priority
// · SLA · Status · Assignee). Click a row → the dispatch-desk detail. No side
// cards, no rail — the queue is the page, so a filed ticket is easy to find.
// Channel of a request, derived from its source, with an icon.
function channelOf(r: IntakeRequest): { key: string; label: string; Icon: LucideIcon } {
  const s = (r.source ?? "").toLowerCase();
  if (/mail|gmail|email|imap/.test(s)) return { key: "email", label: "Email", Icon: Mail };
  if (/slack|chat|teams|message|dm/.test(s)) return { key: "chat", label: "Chat", Icon: MessageSquare };
  if (/form|portal|self|web|intake/.test(s)) return { key: "form", label: "Form", Icon: FileText };
  return { key: "other", label: titleCase(s || "other"), Icon: FileText };
}
const isOpenReq = (r: IntakeRequest) => r.status !== "closed" && r.status !== "approved";
function lowConfidence(r: IntakeRequest): boolean {
  const fs = (r.ai_triage as { flow_suggestion?: WorkflowSuggestion } | null)?.flow_suggestion;
  return !!fs && (fs.needs_human || (fs.confidence ?? 1) < 0.72);
}
// Status filter tokens → matcher, all from real request fields.
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
const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "new", label: "Awaiting triage" },
  { value: "running", label: "In workflow" },
  { value: "stuck", label: "Stuck / overdue" },
  { value: "at_risk", label: "At risk" },
  { value: "unassigned", label: "Unassigned" },
  { value: "lowconf", label: "Low AI confidence" },
];

// Compact multi-select filter dropdown (closes on outside click).
function FilterMenu({ label, options, selected, onToggle }: { label: string; options: { value: string; label: string }[]; selected: Set<string>; onToggle: (v: string) => void }) {
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
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen((o) => !o)}
        className={cn("inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] font-semibold transition",
          n ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-300 bg-slate-100 text-slate-600 hover:bg-slate-200/60")}>
        {label}{n ? <span className="rounded-full bg-brand-600 px-1.5 text-[10px] font-bold tabular-nums text-white">{n}</span> : null}
        <ChevronDown className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div className="absolute left-0 z-30 mt-1.5 max-h-72 min-w-[190px] overflow-auto rounded-xl border border-slate-200 bg-slate-100 p-1.5 shadow-pop">
          {options.length === 0 ? <div className="px-2.5 py-1.5 text-[12px] text-slate-400">No options</div> :
            options.map((o) => (
              <label key={o.value} className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] text-slate-700 hover:bg-slate-200/60">
                <input type="checkbox" checked={selected.has(o.value)} onChange={() => onToggle(o.value)} className="h-3.5 w-3.5 accent-brand-600" />
                {o.label}
              </label>
            ))}
        </div>
      )}
    </div>
  );
}

// Stage / progress cell — a segmented bar for an in-flight workflow, or the AI's
// suggested workflow + confidence when it hasn't started.
function StageCell({ r }: { r: IntakeRequest }) {
  const steps = r.workflow ?? [];
  if (steps.length) {
    const done = steps.filter((s) => s.done).length;
    const active = steps.find((s) => s.active);
    return (
      <div>
        <div className="text-[12px] font-medium text-slate-800">
          {active?.label ?? "In progress"} <span className="font-mono text-[10px] text-slate-400">{done}/{steps.length}</span>
        </div>
        <div className="mt-1 flex gap-0.5">
          {steps.map((s, i) => (
            <span key={i} className={cn("h-1.5 flex-1 rounded-sm", s.done ? "bg-success/50" : s.active ? "bg-brand-500" : "bg-slate-200")} />
          ))}
        </div>
      </div>
    );
  }
  const fs = (r.ai_triage as { flow_suggestion?: WorkflowSuggestion } | null)?.flow_suggestion;
  if (fs?.flow_name) {
    const conf = Math.round((fs.confidence ?? 0) * 100);
    return (
      <div>
        <span className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-2 py-0.5 text-[11px] font-semibold text-brand-700"><Bot className="h-3 w-3" />{fs.flow_name}</span>
        <div className="mt-1 flex items-center gap-1.5">
          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200"><span className={cn("block h-full rounded-full", conf >= 80 ? "bg-success" : conf >= 60 ? "bg-warning" : "bg-danger")} style={{ width: `${conf}%` }} /></span>
          <span className="font-mono text-[10px] text-slate-400">{conf}%</span>
        </div>
      </div>
    );
  }
  return <span className="text-xs text-slate-400">—</span>;
}

// Row flags — derived from real signals (SLA posture, assignment, AI confidence, gates).
function rowFlags(r: IntakeRequest): { label: string; cls: string }[] {
  const f: { label: string; cls: string }[] = [];
  if (r.sla_status === "overdue") f.push({ label: "overdue", cls: "bg-danger-subtle text-danger" });
  else if (r.sla_status === "at_risk") f.push({ label: "at risk", cls: "bg-warning-subtle text-warning" });
  if (isOpenReq(r) && !r.assigned_to_user_id) f.push({ label: "unassigned", cls: "bg-slate-200 text-slate-600" });
  if (lowConfidence(r)) f.push({ label: "low confidence", cls: "bg-warning-subtle text-warning" });
  if ((r.gates?.effective_keys?.length ?? 0) > 0) f.push({ label: "gated", cls: "bg-brand-50 text-brand-700" });
  return f;
}

// Inbox — the redesigned Legal Intake board: AI briefing, compact filters, and a
// dense triage table (Request · Counterparty · Stage · Flags · SLA · Assignee)
// where each row carries enough to triage without opening it. Row → detail.
function InboxCockpit({ onOpen }: { onOpen: (id: string) => void }) {
  const now = useNow(1000); // tick the SLA clocks every second
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-list"], queryFn: () => intakeApi.list(), ...LIVE_POLL });
  const [search, setSearch] = useState("");
  const [channelSel, setChannelSel] = useState<Set<string>>(new Set());
  const [typeSel, setTypeSel] = useState<Set<string>>(new Set());
  const [statusSel, setStatusSel] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState("new");
  const [page, setPage] = useState(1);
  const [briefOpen, setBriefOpen] = useState(true);
  useEffect(() => setPage(1), [search, channelSel, typeSel, statusSel, sort]);

  const rows = useMemo(() => data ?? [], [data]);
  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, v: string) => {
    const n = new Set(set); if (n.has(v)) n.delete(v); else n.add(v); setter(n);
  };

  const channelOpts = useMemo(() => {
    const m = new Map<string, string>();
    rows.forEach((r) => { const c = channelOf(r); m.set(c.key, c.label); });
    return [...m].map(([value, label]) => ({ value, label }));
  }, [rows]);
  const typeOpts = useMemo(() => [...new Set(rows.map((r) => r.type_label))].sort().map((t) => ({ value: t, label: t })), [rows]);

  const q = search.trim().toLowerCase();
  const created = (r: IntakeRequest) => r.created_at ?? r.submitted_at ?? "";
  const filtered = rows.filter((r) => {
    if (channelSel.size && !channelSel.has(channelOf(r).key)) return false;
    if (typeSel.size && !typeSel.has(r.type_label)) return false;
    if (statusSel.size && ![...statusSel].some((t) => statusMatch(r, t))) return false;
    if (q && !`${r.ref} ${r.requester_name ?? ""} ${r.type_label} ${r.subject ?? ""} ${r.description ?? ""} ${requestCounterparty(r) ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const shown = [...filtered].sort((a, b) => {
    if (sort === "cp") return (requestCounterparty(a) ?? "~").localeCompare(requestCounterparty(b) ?? "~");
    if (sort === "sla") return sortBySla(a, b);
    if (sort === "old") return created(a).localeCompare(created(b));
    return created(b).localeCompare(created(a));
  });
  const pageCount = Math.max(1, Math.ceil(shown.length / INBOX_PAGE));
  const currentPage = Math.min(page, pageCount);
  const pageRows = shown.slice((currentPage - 1) * INBOX_PAGE, currentPage * INBOX_PAGE);
  const anyFilter = !!(q || channelSel.size || typeSel.size || statusSel.size);

  if (isLoading) return <CenterSpinner label="Loading the queue…" />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      <IntakeBriefing rows={rows} onOpen={onOpen} open={briefOpen} setOpen={setBriefOpen} />

      {/* compact filter toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search counterparty, subject, ref…" className="h-9 w-64 pl-8 text-[13px]" />
        </div>
        <FilterMenu label="Channel" options={channelOpts} selected={channelSel} onToggle={(v) => toggle(channelSel, setChannelSel, v)} />
        <FilterMenu label="Type" options={typeOpts} selected={typeSel} onToggle={(v) => toggle(typeSel, setTypeSel, v)} />
        <FilterMenu label="Status" options={STATUS_FILTERS} selected={statusSel} onToggle={(v) => toggle(statusSel, setStatusSel, v)} />
        <Select value={sort} onChange={(e) => setSort(e.target.value)} className="h-9 w-auto text-[13px]">
          <option value="new">Newest first</option>
          <option value="old">Oldest first</option>
          <option value="sla">SLA pressure</option>
          <option value="cp">Counterparty A–Z</option>
        </Select>
        {anyFilter && (
          <button type="button" onClick={() => { setSearch(""); setChannelSel(new Set()); setTypeSel(new Set()); setStatusSel(new Set()); }}
            className="text-[12px] font-semibold text-brand-700 hover:underline">Clear</button>
        )}
        <span className="ml-auto font-mono text-xs tabular-nums text-slate-400">{shown.length} of {rows.length}</span>
      </div>

      {/* dense triage table */}
      <Card className="overflow-hidden">
        {shown.length === 0 ? (
          <CardBody><EmptyState title="Nothing here" description={anyFilter ? "No requests match these filters." : "No requests in the queue."} /></CardBody>
        ) : (
          <>
            <Table className="table-fixed">
              <THead><TR>
                <TH className="w-9 text-right">#</TH>
                <TH>Request</TH>
                <TH className="w-[15%]">Counterparty</TH>
                <TH className="w-[16%]">Stage / progress</TH>
                <TH className="w-[11%]">Flags</TH>
                <TH className="w-[14%]">SLA</TH>
                <TH className="w-[9%]">Assignee</TH>
              </TR></THead>
              <tbody>{pageRows.map((r, i) => {
                const ch = channelOf(r);
                const Icon = ch.Icon;
                const desc = (r.subject || r.description || "").split("\n")[0].trim() || r.type_label;
                const cp = requestCounterparty(r);
                const flags = rowFlags(r);
                const tint = r.sla_status === "overdue" ? "bg-danger-subtle/30" : "";
                return (
                  <TR key={r.id} className={cn("cursor-pointer align-top", tint)} onClick={() => onOpen(r.id)}>
                    <TD className="pt-3 text-right font-mono text-[11px] text-slate-400">{(currentPage - 1) * INBOX_PAGE + i + 1}</TD>
                    <TD>
                      <div className="flex items-start gap-2.5">
                        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-200/70 text-slate-500" title={ch.label}><Icon className="h-4 w-4" /></span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-baseline gap-2">
                            <span className="truncate font-semibold text-slate-800" title={desc}>{desc}</span>
                            <span className="shrink-0 font-mono text-[10px] font-semibold text-slate-400">{r.ref}</span>
                          </div>
                          <div className="mt-0.5 truncate text-[11px] text-slate-400">{r.requester_name ?? "—"}{r.department ? ` · ${r.department}` : ""} · {r.type_label}</div>
                        </div>
                      </div>
                    </TD>
                    <TD>
                      <div className="truncate">{cp ? <span className="font-medium text-slate-700">{cp}</span> : <span className="text-slate-400">—</span>}</div>
                    </TD>
                    <TD><StageCell r={r} /></TD>
                    <TD>
                      {flags.length ? (
                        <div className="flex flex-wrap gap-1">
                          {flags.map((f) => <span key={f.label} className={cn("whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold", f.cls)}>{f.label}</span>)}
                        </div>
                      ) : <span className="text-[11px] text-slate-300">—</span>}
                    </TD>
                    <TD>{(() => {
                      const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
                      if (r.status === "closed" || r.status === "approved" || submitted == null) return <span className="text-xs text-slate-400">—</span>;
                      const elapsedMs = now - submitted;
                      const livePct = Math.min(100, (elapsedMs / (r.sla_hours * 3_600_000)) * 100);
                      const c = r.sla_status === "overdue" ? "text-danger" : r.sla_status === "at_risk" ? "text-warning" : "text-slate-500";
                      return (
                        <div className="flex items-center gap-1.5">
                          <span className={cn("shrink-0 font-mono text-[10.5px] tabular-nums", c)}>{fmtDurLive(elapsedMs)}</span>
                          <span className="h-1.5 min-w-[2rem] flex-1 overflow-hidden rounded-full bg-slate-200"><span className="block h-full rounded-full transition-[width] duration-1000 ease-linear" style={{ width: `${livePct}%`, background: slaBarColor(r.sla_status as IntakeSlaPosture) }} /></span>
                        </div>
                      );
                    })()}</TD>
                    <TD className="truncate text-[11px] text-slate-600">{r.assigned_to_label ?? <span className="text-slate-400">Unassigned</span>}</TD>
                  </TR>
                );
              })}</tbody>
            </Table>
            <Pagination page={currentPage} pageCount={pageCount} onPageChange={setPage}
              totalItems={shown.length} pageSize={INBOX_PAGE} />
          </>
        )}
      </Card>
    </div>
  );
}


// ---- My Work (staff) ------------------------------------------------------

function MyWorkTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-mywork"], queryFn: intakeApi.myWork, ...LIVE_POLL });
  if (isLoading) return <CenterSpinner label="Loading your work…" />;
  if (error) return <ErrorState error={error} />;
  const mw = data!;
  const tickets = [...mw.my_tickets].sort(sortBySla);
  const allClear = !mw.awaiting_review.length && !tickets.length && !mw.my_tasks.length;

  return (
    <div className="space-y-4">
      {allClear && (
        <Card><CardBody>
          <EmptyState icon={<DoorOpen className="h-5 w-5" />} title="Inbox zero 🎉"
            description="Nothing waiting on you. Pick up unassigned work from the Inbox." />
        </CardBody></Card>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Awaiting my review</CardTitle>
            <Badge tone="blue">{String(mw.awaiting_review.length)}</Badge></CardHeader>
          <CardBody className="p-0">
            {mw.awaiting_review.length === 0
              ? <p className="px-4 py-3 text-xs text-slate-400">Nothing waiting on your review.</p>
              : <Table><tbody>{mw.awaiting_review.map((r) => <RequestRow key={r.id} r={r} onOpen={onOpen} showStatus={false} />)}</tbody></Table>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>My tickets</CardTitle><Badge tone="slate">{String(tickets.length)}</Badge></CardHeader>
          <CardBody className="p-0">
            {tickets.length === 0
              ? <p className="px-4 py-3 text-xs text-slate-400">Nothing assigned to you.</p>
              : <Table><tbody>{tickets.map((r) => <RequestRow key={r.id} r={r} onOpen={onOpen} />)}</tbody></Table>}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle>My tasks</CardTitle></CardHeader>
        <CardBody className="p-0">
          {mw.my_tasks.length === 0
            ? <p className="px-4 py-3 text-xs text-slate-400">No open sub-tasks assigned to you.</p>
            : <Table><tbody>{mw.my_tasks.map((t) => (
              <TR key={t.id}><TD className="font-medium">{t.title}</TD>
                <TD><Badge tone="slate">{titleCase(t.status)}</Badge></TD>
                <TD className="tabular-nums text-slate-500">{t.effort_minutes}m</TD></TR>
            ))}</tbody></Table>}
        </CardBody>
      </Card>
    </div>
  );
}

// ---- Request Types admin --------------------------------------------------

function RequestTypesTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-types-all"], queryFn: () => intakeApi.listTypes(true) });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<IntakeRequestType | null>(null);

  async function remove(t: IntakeRequestType) {
    if (!window.confirm(`Delete request type "${t.name}"?`)) return;
    try {
      await intakeApi.deleteType(t.id);
      qc.invalidateQueries({ queryKey: ["intake-types-all"] });
      qc.invalidateQueries({ queryKey: ["intake-types"] });
      notify("Type deleted", "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Delete failed", "error"); }
  }

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">No-code request types — fields + stage workflow, live on the New Request form instantly.</p>
        <Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New type</Button>
      </div>
      <Card>
        {(data ?? []).length === 0 ? (
          <CardBody><EmptyState title="No request types" description="Create one to give filers a structured form." /></CardBody>
        ) : (
          <Table>
            <THead><TR><TH>Type</TH><TH>Workstream</TH><TH>Fields</TH><TH>Stage workflow</TH><TH></TH></TR></THead>
            <tbody>{(data ?? []).map((t) => (
              <TR key={t.id}>
                <TD className="font-medium">{t.name}{!t.active && <span className="ml-2 text-xs text-slate-400">(inactive)</span>}</TD>
                <TD className="text-slate-500">{t.workstream ?? "—"}</TD>
                <TD className="tabular-nums text-slate-500">{t.fields.length}</TD>
                <TD className="text-xs text-slate-500">{["Submitted", ...(t.stages ?? ["Assigned", "Review"]), "Complete"].join(" → ")}</TD>
                <TD className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(t)}><PenLine className="h-3.5 w-3.5" />Edit</Button>
                  <Button variant="ghost" size="sm" onClick={() => remove(t)}><Trash2 className="h-3.5 w-3.5" />Delete</Button>
                </TD>
              </TR>
            ))}</tbody>
          </Table>
        )}
      </Card>
      {(creating || editing) && <TypeEditorModal
        key={editing?.id ?? "new"} existing={editing ?? undefined}
        onClose={() => { setCreating(false); setEditing(null); }}
        onSaved={() => { qc.invalidateQueries({ queryKey: ["intake-types-all"] }); qc.invalidateQueries({ queryKey: ["intake-types"] }); setCreating(false); setEditing(null); }} />}
    </div>
  );
}

// A select field's choices are edited as one "value|Label" line each ("Label"
// alone derives the value). They're stored as [{value,label}] — exactly what the
// New Request form's <option> list reads, so a select saved without them renders
// a dropdown containing nothing but the "Select…" placeholder.
type FieldRow = { key: string; label: string; kind: string; required: boolean; options: string };

function parseOptions(text: string): { value: string; label: string }[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const i = line.indexOf("|");
    if (i === -1) return { value: line.toLowerCase().replace(/[^a-z0-9]+/g, "_"), label: line };
    return { value: line.slice(0, i).trim(), label: line.slice(i + 1).trim() };
  });
}
function serializeOptions(options?: { value: string; label: string }[] | null): string {
  return (options ?? []).map((o) => `${o.value}|${o.label}`).join("\n");
}

function TypeEditorModal({ existing, onClose, onSaved }: { existing?: IntakeRequestType; onClose: () => void; onSaved: () => void }) {
  const { notify } = useToast();
  const [name, setName] = useState(existing?.name ?? "");
  const [key, setKey] = useState(existing?.key ?? "");
  const [workstream, setWorkstream] = useState(existing?.workstream ?? "");
  const [stages, setStages] = useState((existing?.stages ?? []).join(", "));
  const [fields, setFields] = useState<FieldRow[]>(
    (existing?.fields ?? []).map((f) => ({
      key: f.key, label: f.label, kind: f.kind, required: f.required, options: serializeOptions(f.options),
    })),
  );
  const [busy, setBusy] = useState(false);

  const canSave = name.trim() && key.trim();
  async function save() {
    setBusy(true);
    try {
      const payload = {
        key: key.trim().toLowerCase(), name: name.trim(),
        workstream: workstream.trim() || null,
        stages: stages.trim() ? stages.split(",").map((s) => s.trim()).filter(Boolean) : null,
        fields: fields.map((f, i) => ({
          key: f.key, label: f.label, kind: f.kind, required: f.required,
          sort_order: (i + 1) * 10,
          options: f.kind === "select" ? parseOptions(f.options) : null,
        })),
      };
      if (existing) await intakeApi.updateType(existing.id, payload);
      else await intakeApi.createType(payload);
      notify(existing ? "Request type updated" : "Request type created", "success"); onSaved();
    } catch (e) { notify(e instanceof Error ? e.message : "Save failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <Modal open onClose={onClose} title={existing ? `Edit ${existing.name}` : "New request type"} size="lg">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><Input value={name} onChange={(e) => { setName(e.target.value); if (!key) setKey(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")); }} placeholder="NDA" /></Field>
          <Field label="Key" hint="lowercase, unique"><Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="nda" /></Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Workstream"><Input value={workstream} onChange={(e) => setWorkstream(e.target.value)} placeholder="Commercial" /></Field>
          <Field label="Stages" hint="comma-separated mid-stages (blank = default)"><Input value={stages} onChange={(e) => setStages(e.target.value)} placeholder="draft, review" /></Field>
        </div>
        <div>
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Fields</p>
            <Button variant="outline" size="sm" onClick={() => setFields((s) => [...s, { key: "", label: "", kind: "text", required: false, options: "" }])}>Add field</Button>
          </div>
          <div className="space-y-2">
            {fields.map((f, i) => (
              <div key={i} className="space-y-1">
                <div className="grid grid-cols-[1fr_1fr_auto_auto_auto] items-center gap-2">
                  <Input value={f.label} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, label: e.target.value, key: x.key || e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_") } : x))} placeholder="Label" />
                  <Input value={f.key} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, key: e.target.value } : x))} placeholder="key" />
                  <Select value={f.kind} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, kind: e.target.value } : x))}>
                    {["text", "textarea", "select", "date", "number", "boolean"].map((k) => <option key={k}>{k}</option>)}
                  </Select>
                  <label className="flex items-center gap-1 text-xs"><input type="checkbox" className="accent-brand-600" checked={f.required} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, required: e.target.checked } : x))} />req</label>
                  <Button variant="ghost" size="sm" onClick={() => setFields((s) => s.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></Button>
                </div>
                {f.kind === "select" && (
                  <div className="pl-1">
                    <Textarea
                      value={f.options} rows={3}
                      onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, options: e.target.value } : x))}
                      placeholder={"Dropdown choices — one per line, as value|Label\nmutual|Mutual NDA\none_way|One-way NDA"}
                    />
                    {!f.options.trim() && (
                      <p className="mt-1 text-xs text-warning">A select with no choices shows an empty dropdown on the New Request form.</p>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>{existing ? "Save changes" : "Create type"}</Button>
        </div>
      </div>
    </Modal>
  );
}

// ---- Request detail (inline, reference-style) -----------------------------

// The five derived Tier-0 gates (mirror backend app/intake/gates.py). Labels are
// display-only; the server is the source of truth for what a gate forces.
const GATE_CATALOG = [
  { key: "exclusivity", label: "Exclusivity / exclusive licence" },
  { key: "clinical", label: "Clinical-trial agreement" },
  { key: "sensitive_data", label: "Sensitive personal data" },
  { key: "regulated_marketing", label: "Regulated / therapeutic claim" },
  { key: "litigation", label: "Litigation / breach-termination" },
] as const;

// RAG per rung — dot colour + name emphasis. "planned" = a preview rung (the
// ladder before it's been started), rendered as a hollow dot.
const RUNG_UI: Record<string, { dot: string; name: string }> = {
  approved: { dot: "bg-success", name: "text-slate-500" },
  pending: { dot: "bg-warning animate-pulse", name: "text-slate-900 font-semibold" },
  waiting: { dot: "bg-slate-300", name: "text-slate-400" },
  rejected: { dot: "bg-danger", name: "text-danger line-through" },
  cancelled: { dot: "bg-slate-300", name: "text-slate-400 line-through" },
  planned: { dot: "border border-slate-400", name: "text-slate-500" },
};
const RUNG_LABEL: Record<string, string> = {
  approved: "Approved", pending: "Awaiting", waiting: "Waiting",
  rejected: "Rejected", cancelled: "Skipped", planned: "Planned",
};

// Workflows tab — the approval-ladder builder (reuses the routing-rule builder)
// plus the read-only Tier-0 gate catalogue that always forces its own rungs.
function WorkflowsBuilderTab() {
  return (
    <div className="space-y-5">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-400">
          <span className="text-brand-600">◎</span> Approval ladders · workflow builder
        </p>
        <p className="mt-1.5 max-w-2xl text-sm text-slate-500">
          Define the ordered approver chain a request runs through. The rule&rsquo;s criteria (value / type / risk) pick
          the ladder; each step is a group, role, or person, activated in turn. Tier-0 gates below always force their
          own senior rung on top — no matter which ladder matches.
        </p>
      </div>
      <RulesTab />
      <Card>
        <CardHeader>
          <CardTitle>Tier-0 hard gates</CardTitle>
          <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">always applied</span>
        </CardHeader>
        <CardBody className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {GATE_CATALOG.map((g) => <Badge key={g.key} tone="amber">{g.label}</Badge>)}
          </div>
          <p className="text-xs text-slate-400">
            An AI classifier fires these from the request text/fields and forces a senior rung regardless of the ladder
            above. A reviewer can add or remove any gate per request from its Approval-ladder card.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

// The rule-driven approval ladder, always visible: Tier-0 gate chips (add/remove
// overrides) over a RAG rung strip. Before submission the rungs are a preview
// (planned); after, they show live RAG status with inline Approve / Reject.
function ApprovalLadderCard({ r, canTriage, onRefreshed }: {
  r: IntakeRequest; canTriage: boolean; onRefreshed: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const [addKey, setAddKey] = useState("");
  const [reassign, setReassign] = useState<"delegate" | "escalate" | null>(null);
  const [reassignTo, setReassignTo] = useState("");
  const { data: chain } = useQuery({ queryKey: ["intake-chain", r.id], queryFn: () => intakeApi.approvalChain(r.id) });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees });

  const gates = r.gates ?? { detected: [], overrides: [], effective: [], effective_keys: [] };
  const effectiveKeys = new Set(gates.effective_keys);
  const addable = GATE_CATALOG.filter((g) => !effectiveKeys.has(g.key));
  const rungs = (chain ?? []).filter((x) => x.status !== "cancelled");
  const started = rungs.some((x) => x.status !== "planned");
  const pending = rungs.find((x) => x.status === "pending");
  const closed = r.status === "closed" || r.status === "approved";

  async function run(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try {
      await fn();
      qc.invalidateQueries({ queryKey: ["intake-chain", r.id] });
      onRefreshed();
      notify(msg, "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    } finally {
      setBusy(false);
    }
  }

  function decide(decision: "approve" | "reject") {
    if (!pending?.approval_request_id) return;
    let comment: string | undefined;
    if (decision === "reject") {
      comment = window.prompt("Reason for rejection (required):") ?? "";
      if (!comment.trim()) return;
    }
    return run(
      () => approvalsApi.decide(pending.approval_request_id as string, decision, comment),
      decision === "approve" ? "Step approved" : "Sent back for review",
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Approval ladder</CardTitle>
        <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
          {closed ? "Closed" : started ? "Running" : "Preview"}
        </span>
      </CardHeader>
      <CardBody className="space-y-4">
        {/* Tier-0 gates */}
        <div>
          <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">Mandatory gates</p>
          {gates.effective.length === 0 ? (
            <p className="text-xs text-slate-400">No hard gates — the ladder follows the value / type routing rules.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {gates.effective.map((g) => (
                <Badge key={g.key} tone="amber">
                  {g.label} → {g.approver_group}
                  {canTriage && !closed && (
                    <button disabled={busy} title="Remove gate"
                      onClick={() => run(() => intakeApi.overrideGate(r.id, { gate_key: g.key, action: "remove", reason: "removed by reviewer" }), "Gate removed")}
                      className="ml-0.5 hover:text-warning disabled:opacity-50">×</button>
                  )}
                </Badge>
              ))}
            </div>
          )}
          {canTriage && !closed && addable.length > 0 && (
            <div className="mt-2 flex items-center gap-2">
              <Select value={addKey} onChange={(e) => setAddKey(e.target.value)}>
                <option value="">Add a gate…</option>
                {addable.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
              </Select>
              <Button size="sm" variant="ghost" loading={busy} disabled={!addKey}
                onClick={() => run(() => intakeApi.overrideGate(r.id, { gate_key: addKey, action: "add", reason: "added by reviewer" }).then(() => setAddKey("")), "Gate added")}>
                Add
              </Button>
            </div>
          )}
        </div>

        {/* RAG rung strip — always shown (preview before submit, live after) */}
        <div>
          <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
            Governance ladder{!started && !closed ? " · preview" : ""}
          </p>
          {rungs.length === 0 ? (
            <p className="text-xs text-slate-400">No approvers resolved — set a routing rule or add a gate.</p>
          ) : (
            <div className="flex flex-wrap items-center gap-x-1 gap-y-2">
              {rungs.map((rung, i) => {
                const ui = RUNG_UI[rung.status] ?? RUNG_UI.waiting;
                return (
                  <Fragment key={rung.approval_request_id ?? `p${rung.step_order}`}>
                    <span className="inline-flex items-center gap-1.5"
                      title={`${rung.step_order}. ${rung.approver_label} — ${RUNG_LABEL[rung.status] ?? rung.status}${(rung.needed ?? 1) > 1 ? ` · needs all ${rung.needed}` : ""}`}>
                      <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", ui.dot)} />
                      <span className={cn("text-[11px]", ui.name)}>{rung.approver_label}</span>
                      {(rung.needed ?? 1) > 1 && (rung.status === "pending" || rung.status === "approved") && (
                        <span className={cn("rounded px-1 text-[9px] font-semibold tabular-nums",
                          (rung.approvals ?? 0) >= (rung.needed ?? 1) ? "bg-success-subtle text-success" : "bg-warning-subtle text-warning")}>
                          {rung.approvals ?? 0}/{rung.needed}
                        </span>
                      )}
                    </span>
                    {i < rungs.length - 1 && <span className="px-1 text-slate-300">—</span>}
                  </Fragment>
                );
              })}
            </div>
          )}

          {/* actions */}
          {!closed && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {!started && canTriage && (
                <Button size="sm" loading={busy}
                  onClick={() => run(() => intakeApi.submitForApproval(r.id), "Submitted for approval")}>
                  Submit for approval
                </Button>
              )}
              {started && pending && canTriage && (reassign ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[11px] text-slate-500">{reassign === "delegate" ? "Delegate this step to" : "Escalate this step to"}</span>
                  <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)} className="h-8 w-48 text-[13px]">
                    <option value="">Select a person…</option>
                    {(assignees ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </Select>
                  <Button size="sm" loading={busy} disabled={!reassignTo}
                    onClick={() => run(() => approvalsApi.reassign(pending.approval_request_id as string, reassignTo, reassign), reassign === "delegate" ? "Delegated" : "Escalated").then(() => { setReassign(null); setReassignTo(""); })}>Go</Button>
                  <Button size="sm" variant="ghost" onClick={() => { setReassign(null); setReassignTo(""); }}>Cancel</Button>
                </div>
              ) : (
                <>
                  <Button size="sm" loading={busy} onClick={() => decide("approve")}>✓ Approve step</Button>
                  <Button size="sm" variant="outline" loading={busy} onClick={() => decide("reject")}>✕ Reject</Button>
                  <Button size="sm" variant="ghost" onClick={() => setReassign("delegate")}>Delegate</Button>
                  <Button size="sm" variant="ghost" onClick={() => setReassign("escalate")}>Escalate</Button>
                  <span className="text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
                    step {pending.step_order} · {pending.approver_label}{(pending.needed ?? 1) > 1 ? ` · ${pending.approvals ?? 0} of ${pending.needed} signed` : ""}
                  </span>
                </>
              ))}
              {started && !pending && (
                <span className="text-[10px] font-medium uppercase tracking-[0.06em] text-success">Ladder complete</span>
              )}
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

function WorkflowStepper({ steps }: { steps: IntakeRequest["workflow"] }) {
  return (
    <section>
      <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-400">Request workflow</p>
      <div className="flex overflow-x-auto rounded-md border border-slate-200">
        {steps.map((s) => (
          <div key={s.stage}
            className={cn("min-w-[128px] flex-1 border-r border-slate-200 px-3 py-3 text-center last:border-r-0",
              s.active ? "bg-brand-50" : s.done ? "bg-slate-50" : "")}>
            <div className={cn("text-sm leading-none", s.done ? "text-success" : s.active ? "text-brand-600" : "text-slate-300")}>
              {s.done ? "✓" : s.active ? "⏳" : "○"}
            </div>
            <div className={cn("mt-1.5 text-xs font-medium", s.active || s.done ? "text-slate-900" : "text-slate-400")}>{s.label}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

// The CLM contract lifecycle — once a request has a drafted contract, the ticket
// rides these stages instead of the intake spine.
const CONTRACT_STAGES: { key: string; label: string }[] = [
  { key: "intake", label: "Intake" },
  { key: "drafting", label: "Drafting" },
  { key: "review", label: "Review" },
  { key: "approval", label: "Approval" },
  { key: "signature", label: "Signature" },
  { key: "active", label: "Active" },
  { key: "closed", label: "Closed" },
];

const _NEXT_STAGE: Record<string, string> = {
  intake: "drafting", drafting: "review", review: "approval", approval: "signature", signature: "active",
};

// Which draftable document (if any) a request maps to — mirrors the backend
// resolve_doc_type keyword pass so the "Draft" button shows for the 4 types.
function draftableDocType(r: IntakeRequest): string | null {
  const t = `${r.type_label} ${r.description ?? ""}`.toLowerCase();
  if (/nda|non-disclosure|non disclosure/.test(t)) return "NDA";
  if (/\b(dpa|data processing|data protection|gdpr)\b/.test(t)) return "DPA";
  if (/\b(msa|master service|master services|services agreement|statement of work|sow)\b/.test(t)) return "MSA";
  if (/vendor|supplier|procurement/.test(t)) return "Vendor agreement";
  return null;
}

function goToWorkspace(contractId: string) {
  window.location.href = `/contracts/${contractId}`;
}

// The counterparty this request is about — structured party first, else the
// screening-derived name.
function requestCounterparty(r: IntakeRequest): string | null {
  const parties = (r.parties ?? []) as { name: string; role: string }[];
  const cp = parties.find((p) => p.role === "counterparty") ?? parties[0];
  if (cp?.name) return cp.name;
  const sc = (r.screening ?? null) as { counterparty?: string } | null;
  return (sc?.counterparty || "").trim() || null;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{children}</dd>
    </div>
  );
}

function riskTone(band?: string | null): "red" | "amber" | "green" | "slate" {
  const b = (band ?? "").toLowerCase();
  return b === "high" ? "red" : b === "medium" ? "amber" : b === "low" ? "green" : "slate";
}

// Related contracts — prior deals with the same counterparty, and other
// contracts of the same type, so the reviewer has precedent at a glance.
const _TYPE_SYNONYMS: Record<string, string[]> = {
  "nda": ["nda", "non-disclosure", "non disclosure", "confidential"],
  "msa": ["msa", "master service", "services agreement", "sow", "statement of work"],
  "dpa": ["dpa", "data processing", "data protection", "gdpr"],
  "vendor agreement": ["vendor", "supplier", "procurement"],
};

function RelatedContractsCard({ r, contracts }: { r: IntakeRequest; contracts: ContractResponse[] }) {
  const cp = requestCounterparty(r);
  const typeHint = (draftableDocType(r) ?? "").toLowerCase();
  const keywords = _TYPE_SYNONYMS[typeHint] ?? (typeHint ? [typeHint] : []);
  const norm = (s?: string | null) => (s ?? "").trim().toLowerCase();
  const pool = contracts.filter((c) => c.id !== r.contract_id);
  const sameParty = cp
    ? pool.filter((c) => {
        const a = norm(c.counterparty_name);
        const b = norm(cp);
        return !!a && !!b && (a.includes(b) || b.includes(a));
      })
    : [];
  const sameType = keywords.length
    ? pool.filter((c) => {
        const hay = `${norm(c.contract_type)} ${norm(c.title)}`;
        return keywords.some((k) => hay.includes(k)) && !sameParty.includes(c);
      })
    : [];

  const Row = (c: ContractResponse) => (
    <a key={c.id} href={`/contracts/${c.id}`}
      className="flex items-center justify-between gap-2 rounded-md border border-slate-200 px-3 py-2 hover:border-brand-300 hover:bg-brand-50">
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-slate-800">{c.title}</span>
        <span className="text-[11px] text-slate-400">
          {c.contract_type ?? "—"}{c.counterparty_name ? ` · ${c.counterparty_name}` : ""} · {titleCase(c.lifecycle_stage)}
        </span>
      </span>
      {c.risk_band && <Badge tone={riskTone(c.risk_band) as never}>{c.risk_band}</Badge>}
    </a>
  );

  return (
    <Card>
      <CardHeader><CardTitle>Related contracts</CardTitle>
        <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
          {sameParty.length + sameType.length} found
        </span>
      </CardHeader>
      <CardBody className="space-y-3">
        {!sameParty.length && !sameType.length && (
          <p className="text-xs text-slate-400">No prior contracts with {cp ?? "this counterparty"} or of this type.</p>
        )}
        {sameParty.length > 0 && (
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Same counterparty{cp ? ` · ${cp}` : ""}</p>
            <div className="space-y-1.5">{sameParty.slice(0, 5).map(Row)}</div>
          </div>
        )}
        {sameType.length > 0 && (
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Similar {typeHint.toUpperCase()} contracts</p>
            <div className="space-y-1.5">{sameType.slice(0, 5).map(Row)}</div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// Contract lifecycle stage tracker — replaces the intake WorkflowStepper once a
// contract exists. Fed by the contract's review-status (current stage).
function ContractLifecycleTracker({ contractId }: { contractId: string }) {
  const { data: rs } = useQuery({
    queryKey: ["contract-review", contractId],
    queryFn: () => contractsApi.reviewStatus(contractId),
  });
  const curIdx = rs ? CONTRACT_STAGES.findIndex((s) => s.key === rs.lifecycle_stage) : -1;
  return (
    <section>
      <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-400">Contract lifecycle</p>
      <div className="flex overflow-x-auto rounded-md border border-slate-200">
        {CONTRACT_STAGES.map((s, i) => {
          const done = curIdx > i, active = curIdx === i;
          return (
            <div key={s.key}
              className={cn("min-w-[100px] flex-1 border-r border-slate-200 px-2 py-3 text-center last:border-r-0",
                active ? "bg-brand-50" : done ? "bg-slate-50" : "")}>
              <div className={cn("text-sm leading-none", done ? "text-success" : active ? "text-brand-600" : "text-slate-300")}>
                {done ? "✓" : active ? "⏳" : "○"}
              </div>
              <div className={cn("mt-1.5 text-xs font-medium", active || done ? "text-slate-900" : "text-slate-400")}>{s.label}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// The AI analysis surfaced on the ticket once a contract exists: weighted risk
// + the playbook deviations, severity-ranked, so a reviewer can act without
// leaving the ticket.
function AiAnalysisCard({ contractId }: { contractId: string }) {
  const { data: risk } = useQuery({ queryKey: ["contract-risk", contractId], queryFn: () => contractsApi.risk(contractId) });
  const { data: devs } = useQuery({ queryKey: ["contract-devs", contractId], queryFn: () => contractsApi.deviations(contractId) });
  const { data: plain, isLoading: plainLoading } = useQuery({ queryKey: ["contract-plain", contractId], queryFn: () => contractsApi.plainSummary(contractId), staleTime: 5 * 60 * 1000 });
  const rs = (risk ?? null) as { score?: number | null; band?: string | null; note?: string } | null;
  const deviations = devs ?? [];
  const sev = (s: string) => (s || "").toLowerCase();
  const counts = {
    high: deviations.filter((d) => sev(d.severity) === "high" || sev(d.severity) === "critical").length,
    medium: deviations.filter((d) => sev(d.severity) === "medium").length,
    low: deviations.filter((d) => sev(d.severity) === "low").length,
  };
  return (
    <Card>
      <CardHeader><CardTitle>AI analysis</CardTitle>
        {rs?.score != null
          ? <Badge tone={riskTone(rs.band) as never}>{(rs.band ?? "risk")} · {rs.score}</Badge>
          : <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">running…</span>}
      </CardHeader>
      <CardBody className="space-y-3">
        {/* Plain-English summary — readable to non-lawyers, front and centre. */}
        <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-3">
          <p className="mb-1.5 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-brand-600">In plain English</p>
          {plainLoading
            ? <p className="text-[13px] text-slate-400">Summarising the review…</p>
            : <div className="prose-sm text-[13px] leading-relaxed text-slate-700 [&_p]:my-1.5 [&_strong]:text-slate-900 [&_ul]:my-1.5 [&_ul]:space-y-1 [&_ul]:pl-4"><Markdown>{plain?.summary || "No summary available yet."}</Markdown></div>}
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="font-medium text-danger">{counts.high} high</span>
          <span className="text-warning">{counts.medium} medium</span>
          <span className="text-slate-500">{counts.low} low</span>
          {deviations.length === 0 && (
            <span className="text-slate-400">No open deviations — clean, or the review is still running.</span>
          )}
        </div>

        {/* The clause-by-clause legal detail — tucked away for whoever wants it. */}
        {deviations.length > 0 && (
          <details className="rounded-md border border-slate-200">
            <summary className="cursor-pointer list-none px-3 py-2 text-[12px] font-medium text-slate-600 hover:text-slate-900">▸ Clause-by-clause detail ({deviations.length})</summary>
            <div className="space-y-2 px-3 pb-3">
              {deviations.slice(0, 12).map((d) => (
                <div key={d.id} className="rounded-md border border-slate-200 p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={riskTone(d.severity) as never}>{d.severity}</Badge>
                    <span className="text-xs font-medium text-slate-800">{titleCase(d.clause_type)}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-600">{d.issue}</p>
                  {d.suggested_fix && <p className="mt-1 text-[11px] text-slate-400">Fix: {d.suggested_fix}</p>}
                </div>
              ))}
              {deviations.length > 12 && (
                <p className="text-xs text-slate-400">+{deviations.length - 12} more — open the workspace for the full set.</p>
              )}
            </div>
          </details>
        )}
      </CardBody>
    </Card>
  );
}

// Per-step type metadata for the governance ladder — the icon + plain-English
// status so a reviewer can tell an AI step from a human task from an approval,
// and whether a step is done, running, or waiting on them.
const STEP_META: Record<string, { icon: LucideIcon; label: string; wait: string; running: string }> = {
  ai_task: { icon: Bot, label: "AI step", wait: "Needs your review", running: "Agent is working…" },
  human_task: { icon: Users, label: "Human task", wait: "Waiting on you", running: "In progress" },
  approval: { icon: ShieldCheck, label: "Approval", wait: "Awaiting sign-off", running: "Awaiting sign-off" },
  signature: { icon: PenLine, label: "Signature", wait: "Out for signature", running: "Out for signature" },
  clm_draft: { icon: FileText, label: "Draft", wait: "Awaiting document", running: "Drafting…" },
  counterparty: { icon: Users, label: "Counterparty", wait: "With counterparty", running: "With counterparty" },
  notify: { icon: Bell, label: "Notify", wait: "Notifying", running: "Notifying" },
};
function stepMeta(type: string) { return STEP_META[type] ?? STEP_META.human_task; }
// Plain-English "what is this and what do I do" for the current step — the piece
// that makes a bare step name ("Cross-functional Assessment") actionable.
function stepGuidance(s: WorkflowRunStep): string {
  const waiting = s.status.startsWith("waiting");
  switch (s.type) {
    case "human_task":
      return `A manual checkpoint you handle off-system. Do the “${s.name}” work — loop in the relevant team and gather what's needed — then click “Complete this step” to record it and move the workflow on.`;
    case "ai_task":
      return waiting
        ? "The agent flagged this for your review — its output is on the ticket. Check it, then complete the step to move on."
        : "The agent runs this automatically. No action needed — it advances on its own when done.";
    case "approval":
      return "Waiting for the approver to sign off. It advances on its own once approved — use “Check approval status” to refresh, or complete it here if you're recording the decision manually.";
    case "signature":
      return "The document is out for e-signature. It advances once every signer has signed — use “Check signature status” to refresh.";
    case "clm_draft":
      return waiting ? "Waiting for the document to be drafted or uploaded before this step can advance." : "The agent is drafting the document — no action needed.";
    case "counterparty":
      return "With the counterparty for their review or input; advance once you hear back.";
    case "notify":
      return "Sending a notification — no action needed; it advances automatically.";
    default:
      return "Click “Complete this step” to advance the workflow.";
  }
}

// The actual litigation findings an AI step produced — the extracted deadlines
// (with statutory source) plus the hold / outside-counsel / settlement flags.
function AiFindingsLitigation({ a }: { a: LitigationAssessment }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-100 px-2.5 py-2">
      <p className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">Findings · {a.matter_type}</p>
      <div className="flex flex-wrap gap-1.5">
        {a.legal_hold_required && <Badge tone="red">Legal hold required</Badge>}
        {a.outside_counsel_likely && <Badge tone="amber">Outside counsel likely</Badge>}
        {a.settlement_posture && a.settlement_posture !== "none" && <Badge tone="violet">Settlement {a.settlement_posture}</Badge>}
      </div>
      {a.statutory_deadlines?.length ? (
        <ul className="mt-1.5 space-y-1">
          {a.statutory_deadlines.map((d, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-1.5 text-[12px]">
              <span className="font-medium text-slate-800">{d.what}</span>
              {d.date && <span className="font-mono text-[11px] font-semibold text-danger">{d.date}</span>}
              {d.source && <span className="text-[10.5px] text-slate-400">· {d.source}</span>}
            </li>
          ))}
        </ul>
      ) : <p className="mt-1 text-[11px] text-slate-400">No dated deadlines extracted.</p>}
    </div>
  );
}

// The playbook deviations an AI review found — counts + the top few, inline.
function AiFindingsDeviations({ contractId }: { contractId: string }) {
  const { data: devs } = useQuery({ queryKey: ["contract-devs", contractId], queryFn: () => contractsApi.deviations(contractId) });
  const deviations = devs ?? [];
  const sev = (s: string) => (s || "").toLowerCase();
  const hi = deviations.filter((d) => sev(d.severity) === "high" || sev(d.severity) === "critical").length;
  const md = deviations.filter((d) => sev(d.severity) === "medium").length;
  const lo = deviations.filter((d) => sev(d.severity) === "low").length;
  return (
    <div className="rounded-md border border-slate-200 bg-slate-100 px-2.5 py-2">
      <p className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">Playbook deviations</p>
      {deviations.length === 0 ? (
        <p className="text-[11px] text-slate-400">No open deviations — clean, or the review is still running.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-[11px]">
            <span className="font-medium text-danger">{hi} high</span>
            <span className="text-warning">{md} medium</span>
            <span className="text-slate-500">{lo} low</span>
          </div>
          <ul className="mt-1.5 space-y-1">
            {deviations.slice(0, 3).map((d) => (
              <li key={d.id} className="text-[12px]">
                <span className={cn("mr-1.5 rounded px-1 py-0.5 text-[9px] font-semibold uppercase",
                  sev(d.severity) === "high" || sev(d.severity) === "critical" ? "bg-danger-subtle text-danger" : sev(d.severity) === "medium" ? "bg-warning-subtle text-warning" : "bg-slate-200 text-slate-500")}>{d.severity}</span>
                <span className="text-slate-700">{titleCase(d.clause_type)}</span> — <span className="text-slate-500">{d.issue}</span>
              </li>
            ))}
          </ul>
          {deviations.length > 3 && <p className="mt-1 text-[11px] text-slate-400">+{deviations.length - 3} more below.</p>}
        </>
      )}
    </div>
  );
}

// Dynamic step read-out — shows the information that matters for the SELECTED
// step at its current state: an AI step surfaces the agent's output + findings,
// a draft step the document, a human task the guidance; done steps show what
// they produced, pending steps say they haven't run.
function StepInfo({ step, r, state }: { step: WorkflowRunStep; r: IntakeRequest; state: "done" | "current" | "pending" }) {
  const res = (step.result ?? {}) as Record<string, unknown>;
  const conf = typeof res.confidence === "number" ? Math.round((res.confidence as number) * 100) : null;
  const la = (r.ai_triage as Record<string, unknown> | null)?.litigation_assessment as LitigationAssessment | undefined;

  const agentOutput = step.type === "ai_task" && (res.agent != null || res.category != null || conf != null) ? (
    <div className="rounded-md border border-slate-200 bg-slate-100 px-2.5 py-2">
      <p className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">Agent output</p>
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        {res.agent != null && <Badge tone="violet">{titleCase(String(res.agent).replace(/[-_]/g, " "))}</Badge>}
        {res.category != null && <span className="text-slate-700">{String(res.category)}</span>}
        {conf != null && <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold", conf >= 75 ? "bg-success-subtle text-success" : "bg-warning-subtle text-warning")}>{conf}% confidence</span>}
      </div>
    </div>
  ) : null;
  const findings = step.type === "ai_task" ? (la ? <AiFindingsLitigation a={la} /> : r.contract_id ? <AiFindingsDeviations contractId={r.contract_id} /> : null) : null;

  const statusLine =
    state === "done" ? <p className="text-[12px] font-medium text-success">✓ Completed</p>
      : state === "pending" ? <p className="text-[12px] text-slate-400">Pending — runs once the earlier steps finish.</p>
        : <p className="text-[12.5px] leading-relaxed text-slate-600">{stepGuidance(step)}</p>;
  const note = step.note ? <p className="text-[11px] text-slate-400">{step.note}</p> : null;
  const link = step.type === "clm_draft" && r.contract_id
    ? <a href={`/contracts/${r.contract_id}`} className="inline-block text-[12px] font-medium text-brand-700 hover:underline">Open the drafted document →</a>
    : step.type === "ai_task" && r.contract_id
      ? <a href={`/contracts/${r.contract_id}`} className="inline-block text-[12px] font-medium text-brand-700 hover:underline">Open the full analysis →</a>
      : null;

  return <div className="space-y-2">{agentOutput}{findings}{statusLine}{note}{link}</div>;
}

// Litigation Intake Agent read-out — the matter facts a triaging lawyer needs,
// surfaced when the request classifies as litigation.
function LitigationCard({ a }: { a: LitigationAssessment }) {
  return (
    <Card className="border-l-2 border-l-brand-600 p-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-brand-600">§ Litigation Intake · {a.matter_type}</p>
        {a.legal_hold_required && <Badge tone="red">Legal hold required</Badge>}
        {a.outside_counsel_likely && <Badge tone="amber">Outside counsel likely</Badge>}
        {a.settlement_posture && a.settlement_posture !== "none" && <Badge tone="violet">Settlement {a.settlement_posture}</Badge>}
      </div>
      <p className="text-[13px] leading-relaxed text-slate-700">{a.summary}</p>
      {a.statutory_deadlines.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Deadlines</p>
          <ul className="space-y-1 text-[12.5px] text-slate-600">
            {a.statutory_deadlines.map((d, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-1.5">
                <span className="font-medium text-slate-800">{d.what}</span>
                {d.date && <span className="font-mono text-[11px] font-semibold text-danger">{d.date}</span>}
                {d.source && <span className="text-[11px] text-slate-400">· {d.source}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {a.key_parties.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="mb-1 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Parties</p>
          <p className="text-[12.5px] text-slate-600">{a.key_parties.join(" · ")}</p>
        </div>
      )}
    </Card>
  );
}

// Owner picker for the ticket assignment card: a selectable list of assignable
// staff (initials avatar + name), replacing the old bare dropdown. The current
// owner is flagged; clicking a row toggles the pending selection.
function OwnerPicker({
  assignees, selected, onSelect, currentOwnerId,
}: {
  assignees: { id: string; name: string }[];
  selected: string;
  onSelect: (id: string) => void;
  currentOwnerId: string | null;
}) {
  if (assignees.length === 0) return <p className="text-xs text-slate-400">No assignable staff.</p>;
  return (
    <div className="max-h-60 space-y-1 overflow-y-auto pr-0.5">
      {assignees.map((a) => {
        const initials = a.name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
        const active = selected === a.id;
        const isCurrent = currentOwnerId === a.id;
        return (
          <button key={a.id} type="button" onClick={() => onSelect(active ? "" : a.id)}
            className={cn("flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition",
              active ? "border-brand-500 bg-brand-50" : "border-slate-200 hover:bg-slate-50")}>
            <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-full text-[12px] font-semibold",
              active ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-600")}>{initials || "—"}</span>
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-900">{a.name}</span>
            {isCurrent && <Badge tone="slate">Current</Badge>}
            {active && <Check className="h-4 w-4 shrink-0 text-brand-600" />}
          </button>
        );
      })}
    </div>
  );
}

function RequestDetailView({ id, onBack, canTriage, inPane = false }: { id: string; onBack: () => void; canTriage: boolean; inPane?: boolean }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const { data: r, isLoading } = useQuery({ queryKey: ["intake-req", id], queryFn: () => intakeApi.get(id) });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees, enabled: canTriage });
  const { data: contracts } = useQuery({ queryKey: ["contracts"], queryFn: contractsApi.list, enabled: canTriage });
  const { data: legs } = useQuery({ queryKey: ["intake-sla", id], queryFn: () => intakeApi.slaLegs(id) });
  const { data: ticketDocs } = useQuery({ queryKey: ["intake-docs", id], queryFn: () => intakeApi.documents(id) });
  const { data: flowRun } = useQuery({
    queryKey: ["flow-run", id],
    queryFn: () => workflowsApi.runForRequest(id),
    // Poll while something is actively working so the ladder animates live — a
    // mid-beat step (running) or a subsystem in progress (waiting_job). Idle
    // runs (waiting on a human / complete) don't poll.
    refetchInterval: (q) => {
      const run = q.state.data as WorkflowRun | null | undefined;
      if (!run) return false;
      const working = (run.steps ?? []).some((s) => s.status === "running" || s.status === "waiting_job");
      return run.status === "running" || working ? 1500 : false;
    },
  });
  const hasRun = !!flowRun && ["running", "waiting", "complete", "failed", "cancelled"].includes(flowRun.status);
  const hasAttachment = (ticketDocs ?? []).some((d) => d.extracted_chars > 0);
  const [busy, setBusy] = useState(false);
  const [reassignTo, setReassignTo] = useState("");

  function refresh() {
    ["intake-list", "intake-mywork", "intake-mine"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    qc.invalidateQueries({ queryKey: ["intake-req", id] });
    qc.invalidateQueries({ queryKey: ["intake-sla", id] });
    qc.invalidateQueries({ queryKey: ["flow-run", id] });
  }
  async function act(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try { await fn(); refresh(); notify(msg, "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Action failed", "error"); }
    finally { setBusy(false); }
  }

  // Which ladder step the detail box shows — defaults to and follows the current
  // step; a click on any step overrides it. (Hook must precede the early return.)
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  useEffect(() => { setSelectedIdx(null); }, [flowRun?.current_index]);

  // Pump a mid-beat step: a run parked in "running" is an ai_task showing its
  // "Agent is working…" animation with the agent not yet run. Hold ~1s so the
  // beat is visible, then resume the executor (refresh) so the agent runs for
  // real and the flow advances. Guarded to fire once per step; deliberately not
  // cancelled on unmount, so navigating away mid-beat still completes it.
  const pumpedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!flowRun || flowRun.status !== "running") return;
    const key = `${flowRun.id}:${flowRun.current_index}`;
    if (pumpedRef.current === key) return;
    pumpedRef.current = key;
    const runId = flowRun.id;
    setTimeout(() => {
      workflowsApi.refreshRun(runId).finally(() => qc.invalidateQueries({ queryKey: ["flow-run", id] }));
    }, 1000);
  }, [flowRun, id, qc]);

  if (isLoading || !r) return <CenterSpinner label="Loading the request…" />;

  const posture = r.sla_status as IntakeSlaPosture;
  const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
  const elapsedH = submitted != null ? (Date.now() - submitted) / 3_600_000 : null;
  const open = r.status !== "closed" && r.status !== "approved";
  const ai = (r.ai_triage ?? {}) as Record<string, unknown>;
  const la = ai.litigation_assessment as LitigationAssessment | undefined;
  const accent = posture === "overdue" ? "border-l-danger" : posture === "at_risk" ? "border-l-warning" : "border-l-success";
  const postureText = posture === "overdue" ? "text-danger" : posture === "at_risk" ? "text-warning" : "text-success";
  // A short header title — descriptions can be long (pasted text, folded
  // attachments). Take the first line, capped; the full text lives in Overview.
  const firstLine = (r.subject || r.description || r.type_label).trim().split("\n")[0].trim();
  const shortTitle = firstLine.length > 140 ? `${firstLine.slice(0, 137).trimEnd()}…` : (firstLine || r.type_label);

  // Dispatch-desk detail (reference "Legal Mission Control" layout, in the light
  // theme): a single scrolling page — header + SLA meter, dispatch banner,
  // governance ladder, request brief, AI analysis, assignment, custody legs, and
  // a tamper-evident timeline. A render helper draws the mono-caps section heads.
  const youCreated = !!user && r.requester_user_id === user.id;
  const owner = r.assigned_to_label;
  const flowSteps = flowRun?.steps ?? [];
  const current = flowRun ? flowSteps[flowRun.current_index] : undefined;
  const doneCount = flowSteps.filter((s) => ["done", "complete", "skipped"].includes(s.status)).length;
  // Click any ladder step to inspect it; defaults to (and follows) the current step.
  const selIdx = selectedIdx ?? flowRun?.current_index ?? 0;
  const selStep = flowSteps[selIdx];
  const selState: "done" | "current" | "pending" = !selStep ? "pending"
    : (selStep.status === "done" || selStep.status === "complete") ? "done"
      : (selStep.idx === flowRun?.current_index && flowRun?.status !== "complete") ? "current"
        : "pending";
  const head = (label: string, note?: React.ReactNode) => (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">{label}</span>
      {note && <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-slate-400">{note}</span>}
    </div>
  );

  return (
    <div className="space-y-3">
      {!inPane && (
        <button onClick={onBack}
          className="inline-flex items-center gap-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500 hover:text-slate-900">
          ← Back to inbox
        </button>
      )}

      {/* ===== header + SLA meter ===== */}
      <Card className={cn("border-l-[3px] p-5", accent)}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[11px] text-slate-500">{r.ref}</span>
              <Badge tone={PRIORITY_TONE[r.priority] as never}>{r.priority}</Badge>
              <Badge tone="blue">{r.type_label}</Badge>
              <StatusPill r={r} />
              {r.handoff_holder && <Badge tone="violet">{titleCase(r.handoff_holder)} holds</Badge>}
              {youCreated && <Badge tone="slate">You created</Badge>}
            </div>
            <h2 className="mt-2 line-clamp-2 text-[19px] font-semibold leading-snug text-slate-900">{shortTitle}</h2>
            <p className="mt-1.5 text-xs text-slate-500">
              From <span className="text-slate-700">{r.requester_name ?? "—"}</span>
              {r.department ? ` · ${r.department}` : ""}
              {r.submitted_at ? ` · Submitted ${new Date(r.submitted_at).toLocaleString()}` : ""}
              {owner ? <> · Owner <span className="text-slate-700">{owner}</span></> : ""}
            </p>
          </div>
          {open && elapsedH != null && (
            <div className="shrink-0 sm:text-right">
              <p className={cn("font-mono text-[10px] font-semibold uppercase tracking-[0.1em]", postureText)}>SLA {POSTURE_LABEL[posture]}</p>
              <p className="text-2xl font-semibold tabular-nums text-slate-900">{fmtElapsedHours(elapsedH)}</p>
              <p className="text-[11px] text-slate-400">of {r.sla_hours} hrs window</p>
              <div className="mt-1.5 h-1.5 w-40 overflow-hidden rounded-full bg-slate-200 sm:ml-auto">
                <div className="h-full rounded-full" style={{ width: `${Math.min(100, r.sla_pct)}%`, background: slaBarColor(posture) }} />
              </div>
              <p className="mt-1 text-[11px] tabular-nums text-slate-400">{r.sla_pct}% elapsed</p>
            </div>
          )}
        </div>
      </Card>

      {/* ===== governance ladder ===== */}
      <Card className="p-5">
        {hasRun && flowRun ? (
          <>
            {head(`Governance Ladder · ${flowRun.flow_name}`,
              <>Step {Math.min(flowRun.current_index + 1, flowSteps.length)} of {flowSteps.length} · <span className={flowRun.status === "complete" ? "text-success" : "text-slate-500"}>{titleCase(flowRun.status)}</span></>)}
            <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-slate-200">
              <div className="h-full rounded-full bg-brand-600 transition-all duration-500" style={{ width: `${flowSteps.length ? Math.round((doneCount / flowSteps.length) * 100) : 0}%` }} />
            </div>
            <GovernanceLadderSteps
              steps={flowSteps}
              currentIndex={flowRun.current_index}
              complete={flowRun.status === "complete"}
              selectedIdx={selIdx}
              onSelect={setSelectedIdx}
            />
            {selStep && (
              <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700">
                  {(() => { const I = stepMeta(selStep.type).icon; return <I className="h-3.5 w-3.5" />; })()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-slate-400">{selState === "current" ? "Now" : selState === "done" ? "Done" : `Step ${selStep.idx + 1}`}</span>
                    <span className="text-[13px] font-semibold text-slate-900">{selStep.name}</span>
                    <Badge tone={selState === "done" ? "green" : selState === "current" ? (selStep.status.startsWith("waiting") ? "amber" : "blue") : "slate"}>{stepMeta(selStep.type).label}</Badge>
                  </div>
                  <div className="mt-1"><StepInfo step={selStep} r={r} state={selState} /></div>
                </div>
              </div>
            )}
            {flowRun.status === "complete" && (
              <div className="mt-4 flex items-center gap-2 rounded-lg border border-success/30 bg-success-subtle px-3 py-2 text-[13px] text-success">
                <Check className="h-4 w-4" /> Workflow complete — every step is done.
              </div>
            )}
            {(flowRun.status === "failed" || flowRun.status === "cancelled") && (
              <div className="mt-4 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
                <X className="mt-0.5 h-4 w-4 shrink-0" />
                <span>Workflow {flowRun.status === "cancelled" ? "cancelled" : "failed"}{flowRun.error ? ` — ${flowRun.error}` : "."} The ladder stopped here; resolve the step or restart the workflow.</span>
              </div>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
              {current?.status === "waiting_human" && (
                <Button size="sm" loading={busy} onClick={() => act(() => workflowsApi.completeStep(flowRun.id), `“${current.name}” completed`)}><Check className="h-4 w-4" /> Complete this step</Button>
              )}
              {current && (current.type === "approval" || current.type === "signature") && (
                <Button size="sm" variant="outline" loading={busy} onClick={() => act(() => workflowsApi.refreshRun(flowRun.id), "Refreshed")}>Check {current.type} status</Button>
              )}
              {r.contract_id && (
                <a href={`/contracts/${r.contract_id}`} className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-brand-700 hover:underline">Open the drafted contract →</a>
              )}
            </div>
          </>
        ) : r.contract_id ? (
          <>
            <ContractLifecycleTracker contractId={r.contract_id} />
            <div className="mt-3"><WorkflowPanel requestId={id} suggestion={r.ai_triage?.flow_suggestion as WorkflowSuggestion | undefined} /></div>
          </>
        ) : (
          <>
            {head("Request Workflow")}
            <WorkflowStepper steps={r.workflow} />
            <div className="mt-3"><WorkflowPanel requestId={id} suggestion={r.ai_triage?.flow_suggestion as WorkflowSuggestion | undefined} /></div>
          </>
        )}
      </Card>

      {/* ===== detail grid: substance (left) · status & actions (right) ===== */}
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
        <div className="grid gap-3">
          {la && <LitigationCard a={la} />}

      {/* ===== request brief ===== */}
      <Card className="p-5">
        {head("Request Brief", <>{ai.category != null && <span className="text-brand-600">{String(ai.category)}</span>}{ai.risk_flag != null && <span className={cn("ml-2", String(ai.risk_flag) === "high" ? "text-danger" : "text-warning")}>{String(ai.risk_flag)} risk</span>}</>)}
        <p className="max-h-48 overflow-y-auto whitespace-pre-wrap text-sm text-slate-700">{r.description || r.type_label}</p>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-slate-100 pt-4 sm:grid-cols-4">
          <Fact label="Counterparty">{requestCounterparty(r) ?? <span className="text-slate-400">—</span>}</Fact>
          <Fact label="Department">{r.department ?? "—"}</Fact>
          {ai.confidence != null && <Fact label="AI confidence">{Math.round(Number(ai.confidence) * 100)}%</Fact>}
          {ai.complexity != null && <Fact label="Complexity">{titleCase(String(ai.complexity))}</Fact>}
          {r.contract_id && r.contract_title && <Fact label="Contract"><a className="text-brand-700 hover:underline" href={`/contracts/${r.contract_id}`}>{r.contract_title}</a></Fact>}
        </dl>
        {(r.fired_rules as { summaries?: { name: string; actions: string[] }[] } | null)?.summaries?.length ? (
          <div className="mt-3 border-t border-slate-100 pt-3">
            <p className="mb-1 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Routing rules fired</p>
            <ul className="space-y-1 text-xs text-slate-600">
              {(r.fired_rules as { summaries: { name: string; actions: string[] }[] }).summaries.map((s, i) => <li key={i}>▸ <b>{s.name}</b> — {s.actions.join(", ")}</li>)}
            </ul>
          </div>
        ) : null}
        {/* contract access / draft */}
        {r.contract_id ? (
          <a href={`/contracts/${r.contract_id}`} className="mt-4 flex items-center justify-between rounded-lg border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm hover:bg-brand-100">
            <span className="min-w-0 truncate text-slate-700">Contract: <strong className="text-slate-900">{r.contract_title ?? "linked"}</strong></span>
            <span className="shrink-0 font-semibold text-brand-700">Open →</span>
          </a>
        ) : canTriage && open && draftableDocType(r) ? (
          <div className="mt-4 rounded-lg border border-dashed border-brand-300 bg-brand-50/50 px-4 py-3 text-sm">
            {hasAttachment ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-1 text-slate-600">Attached document ready:</span>
                <Button size="sm" loading={busy} onClick={() => act(() => intakeApi.ingestAttachment(id), "Using the attachment — review running")}>Use attached as the contract →</Button>
                <Button size="sm" variant="outline" loading={busy} onClick={() => act(() => intakeApi.draftContract(id), `${draftableDocType(r)} drafted — review running`)}>Approve &amp; draft fresh</Button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-1 text-slate-600">Approve to auto-draft the {draftableDocType(r)}:</span>
                <Button size="sm" loading={busy} onClick={() => act(() => intakeApi.draftContract(id), `${draftableDocType(r)} drafted — review running`)}>Approve &amp; draft the {draftableDocType(r)} →</Button>
              </div>
            )}
          </div>
        ) : null}
      </Card>

      {/* ===== documents (kept with the brief, not at the end) ===== */}
      <Card className="p-5">{head("Documents")}<DocumentsPanel requestId={r.id} /></Card>

      {/* ===== AI analysis ===== */}
      {r.contract_id && <Card className="p-5">{head("Playbook Deviations · Risk")}<AiAnalysisCard contractId={r.contract_id} /></Card>}

      {/* ===== counterparty & screening + documents ===== */}
      <Card className="p-5">{head("Counterparty & Screening")}
        <div className="space-y-4"><PartiesPanel r={r} canTriage={canTriage} onRefreshed={refresh} /><ScreeningPanel r={r} canTriage={canTriage} onRefreshed={refresh} /></div>
      </Card>
        </div>

        <div className="grid gap-3">
      {/* ===== assignment · owner ===== */}
      {canTriage && open && (
        <Card id="assignment" className="p-5">
          {head("Assignment · Owner", owner ? <>Owned by <span className="text-slate-600">{owner}</span></> : "Unassigned")}
          <OwnerPicker
            assignees={assignees ?? []}
            selected={reassignTo}
            onSelect={setReassignTo}
            currentOwnerId={r.assigned_to_user_id ?? null}
          />
          <div className="mt-3 flex justify-end border-t border-slate-100 pt-3">
            <Button size="sm" loading={busy} disabled={!reassignTo}
              onClick={() => act(async () => { await intakeApi.triage(id, { action: "reassigned", assignee_user_id: reassignTo }); setReassignTo(""); }, "Owner reassigned")}>
              Reassign owner
            </Button>
          </div>
        </Card>
      )}

      <RelatedContractsCard r={r} contracts={contracts ?? []} />

      {/* ===== SLA custody legs ===== */}
      {legs && legs.legs.length > 0 && (
        <Card className="p-5">
          {head("SLA Custody Legs", <span className={legs.breached ? "text-danger" : ""}>{legs.breached ? "Window breached" : "Clock running"} · {fmtElapsedHours(elapsedH ?? 0)} elapsed</span>)}
          <SlaLegsBar legs={legs.legs} breached={legs.breached} />
          <p className="mt-2 text-[11px] text-slate-400">Legs derive from the hand-off ledger — every baton pass starts a new clock segment. One window, no resets.</p>
        </Card>
      )}

        </div>
      </div>
    </div>
  );
}

// ---- screening + documents panels (gap-fill) -------------------------------

const ROLE_LABEL: Record<string, string> = {
  counterparty: "Counterparty", adverse: "Adverse", related: "Related", our_side: "Our side",
};

function PartiesPanel({ r, canTriage, onRefreshed }: { r: IntakeRequest; canTriage: boolean; onRefreshed: () => void }) {
  const { notify } = useToast();
  const parties = (r.parties ?? []) as { name: string; role: string; is_person?: boolean }[];
  const [name, setName] = useState("");
  const [role, setRole] = useState("adverse");
  const [busy, setBusy] = useState(false);

  async function save(next: { name: string; role: string }[]) {
    setBusy(true);
    try { await intakeApi.setParties(r.id, next); onRefreshed(); notify("Parties updated — re-screened", "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Update failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Parties</p>
      {parties.length === 0 && <p className="text-xs text-slate-400">No parties captured.</p>}
      <ul className="space-y-1 text-xs">
        {parties.map((p, i) => (
          <li key={i} className="flex items-center gap-2">
            <Badge tone={p.role === "adverse" ? "red" : p.role === "counterparty" ? "blue" : "slate"}>{ROLE_LABEL[p.role] ?? p.role}</Badge>
            <span className="font-medium text-slate-700">{p.name}</span>
            {canTriage && (
              <button className="ml-auto text-slate-400 hover:text-danger" disabled={busy}
                onClick={() => save(parties.filter((_, j) => j !== i))}>remove</button>
            )}
          </li>
        ))}
      </ul>
      {canTriage && (
        <div className="mt-2 flex gap-1.5">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Party name…" className="h-8 text-xs" />
          <Select value={role} onChange={(e) => setRole(e.target.value)} className="h-8 w-28 text-xs">
            {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
          <Button size="sm" loading={busy} disabled={!name.trim()}
            onClick={() => { save([...parties, { name: name.trim(), role }]); setName(""); }}>Add</Button>
        </div>
      )}
    </div>
  );
}

function ScreeningPanel({ r, canTriage, onRefreshed }: { r: IntakeRequest; canTriage: boolean; onRefreshed: () => void }) {
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const sc = (r.screening ?? null) as {
    status?: string; note?: string; counterparty?: string;
    sanctions?: { status?: string; party?: string; matches?: { name?: string; programs?: string | null }[]; note?: string };
    conflicts?: { kind: string; severity?: string; party?: string; role_here?: string; via?: string; ref?: string; title?: string }[];
    relationship?: { note?: string };
  } | null;
  const sanTone = (st?: string) => (st === "hit" ? "red" : st === "clear" ? "green" : "amber");
  const conflicts = sc?.conflicts ?? [];
  const high = conflicts.filter((c) => c.severity === "high").length;
  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Screening</p>
        {canTriage && (
          <button className="text-xs text-brand-600 hover:underline disabled:opacity-50" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try { await intakeApi.screen(r.id); onRefreshed(); notify("Screening re-run", "success"); }
              catch (e) { notify(e instanceof Error ? e.message : "Screening failed", "error"); }
              finally { setBusy(false); }
            }}>{busy ? "Running…" : "Re-run"}</button>
        )}
      </div>
      {!sc || sc.status === "skipped" || sc.status === "error" ? (
        <p className="text-xs text-slate-400">{sc?.note ?? "Not screened — no parties captured."}</p>
      ) : (
        <div className="space-y-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500">Sanctions:</span>
            <Badge tone={sanTone(sc.sanctions?.status) as never}>
              {sc.sanctions?.status === "hit" ? "HIT" : sc.sanctions?.status === "clear" ? "Clear" : "Unavailable"}
            </Badge>
            {sc.sanctions?.party && <span className="text-slate-500">{sc.sanctions.party}</span>}
            {(sc.sanctions?.matches ?? []).slice(0, 2).map((m, i) => (
              <span key={i} className="text-danger">{m.name}{m.programs ? ` · ${m.programs}` : ""}</span>
            ))}
          </div>
          {sc.sanctions?.note && (
            <p className="text-slate-500">{sc.sanctions.note}</p>
          )}
          <div>
            <div className="mb-1 flex items-center gap-2">
              <span className="text-slate-500">Conflicts:</span>
              {conflicts.length === 0 ? <span className="text-slate-400">none found</span>
                : high > 0 ? <Badge tone="red">{high} high</Badge> : <Badge tone="amber">{conflicts.length} to review</Badge>}
            </div>
            <ul className="space-y-1">
              {conflicts.slice(0, 6).map((c, i) => (
                <li key={i} className="flex items-start gap-1.5">
                  <Badge tone={c.severity === "high" ? "red" : "amber"}>{c.severity === "high" ? "HIGH" : "REVIEW"}</Badge>
                  <span className="text-slate-600">
                    <span className="font-medium">{c.party}</span>
                    {c.role_here ? ` (${ROLE_LABEL[c.role_here] ?? c.role_here})` : ""} — {c.via}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {sc.relationship?.note && <p className="text-slate-600"><span className="text-slate-500">Relationship: </span>{sc.relationship.note}</p>}
        </div>
      )}
    </div>
  );
}

function DocumentsPanel({ requestId }: { requestId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: docs } = useQuery({ queryKey: ["intake-docs", requestId], queryFn: () => intakeApi.documents(requestId) });
  const [busy, setBusy] = useState(false);
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  async function upload(file: File) {
    if (file.size > 3 * 1024 * 1024) { notify("Max 3 MB", "error"); return; }
    setBusy(true);
    try {
      const buf = await file.arrayBuffer();
      let bin = ""; const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i += 0x8000)
        bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      await intakeApi.uploadDocument(requestId, {
        filename: file.name, mime_type: file.type || "application/octet-stream",
        content_b64: btoa(bin),
      });
      qc.invalidateQueries({ queryKey: ["intake-docs", requestId] });
      qc.invalidateQueries({ queryKey: ["intake-req", requestId] });
      notify("Attached — text extracted for triage", "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Upload failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Attachments</p>
        <label className="cursor-pointer text-xs text-brand-600 hover:underline">
          {busy ? "Uploading…" : "Attach"}
          <input type="file" className="hidden" accept=".txt,.docx,.pdf,.doc" disabled={busy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        </label>
      </div>
      {(docs ?? []).length === 0 ? (
        <p className="text-xs text-slate-400">No documents attached.</p>
      ) : (
        <ul className="space-y-2 text-xs text-slate-600">
          {(docs ?? []).map((d) => (
            <li key={d.id}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{d.filename}</span>
                <span className="text-slate-400">{Math.round(d.size_bytes / 1024)} KB</span>
                {d.extracted_chars > 0
                  ? <Badge tone="green">text extracted</Badge>
                  : <Badge tone="slate">no text</Badge>}
                {d.extracted_text && (
                  <button className="text-brand-600 hover:underline"
                    onClick={() => setOpenDoc((v) => (v === d.id ? null : d.id))}>
                    {openDoc === d.id ? "Hide text" : "View text"}
                  </button>
                )}
              </div>
              {openDoc === d.id && d.extracted_text && (
                <pre className="mt-1.5 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-2 font-sans text-[11px] leading-relaxed text-slate-600">
                  {d.extracted_text}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


// ---- consolidated wrappers: My Work + one Operations door -------------------

function MyWorkView({ isStaff, onOpen }: { isStaff: boolean; onOpen: (id: string) => void }) {
  if (!isStaff) return <MyRequestsTab onOpen={onOpen} />;
  return (
    <div className="space-y-4">
      <MyWorkTab onOpen={onOpen} />
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Filed by me</p>
        <MyRequestsTab onOpen={onOpen} />
      </div>
    </div>
  );
}

function OperationsView({ isAdmin }: { isAdmin: boolean }) {
  // SLA and Agents are now first-class tabs; Smart Routing lives under Approvals → Intake routing.
  const items = [
    { id: "pool", label: "Pool Ops" },
    ...(isAdmin ? [{ id: "teams", label: "Teams" }, { id: "types", label: "Request Types" }] : []),
  ];
  const [sub, setSub] = useState("pool");
  return (
    <div className="space-y-4">
      <div className="flex gap-1 border-b border-slate-200">
        {items.map((it) => (
          <button key={it.id} onClick={() => setSub(it.id)}
            className={cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              sub === it.id ? "border-brand-600 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-900")}>
            {it.label}
          </button>
        ))}
      </div>
      {sub === "pool" && <PoolOpsTab />}
      {sub === "teams" && isAdmin && <TeamsTab isAdmin={isAdmin} />}
      {sub === "types" && isAdmin && <RequestTypesTab />}
    </div>
  );
}
