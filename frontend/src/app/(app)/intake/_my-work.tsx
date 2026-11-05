"use client";

// My Work — the same layout as Legal Intake: icon KPI cards, status pills, one table card (search +
// rows with stage dots, status pill and due date) and insight cards on the right. Wired to the real
// intake API (myWork + mine) and the approvals queue.

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckSquare, ChevronRight, Clock, FileText, Inbox, Search, ShieldCheck, Zap, type LucideIcon } from "lucide-react";
import { approvalsApi, intakeApi } from "@/lib/endpoints";
import { dueText } from "@/lib/approval-due";
import { contractDisplayName, plural } from "@/lib/utils";
import { CenterSpinner, ErrorState } from "@/components/ui";
import type { ApprovalRequest, IntakeRequest, IntakeTask } from "@/lib/types";
import { FilterMenu, StageCell, counterpartyOf, dueOf, kindOf, stageOf } from "./_legal-intake";
import { LIFECYCLE } from "@/components/lifecycle-view";

// Refetch on window focus rather than poll — see the note on LIVE_POLL in
// intake/page.tsx for the trade-off (sla_status won't advance on its own
// while the tab sits unfocused).
const POLL = { refetchOnWindowFocus: true } as const;

type Tab = "action" | "over" | "soon" | "approvals" | "tasks" | "raised";
type Urgency = "over" | "soon" | "later";

const urgency = (r: IntakeRequest): Urgency => (r.sla_status === "overdue" ? "over" : r.sla_status === "at_risk" ? "soon" : "later");
const created = (r: IntakeRequest) => r.created_at ?? r.submitted_at ?? "";
const askOf = (r: IntakeRequest) => (r.subject || r.description || "").split("\n")[0].trim() || r.type_label;

/** Where a request this person raised stands, in their words. */
function raisedState(r: IntakeRequest): "done" | "stuck" | "progress" | "input" {
  if (r.status === "closed" || r.status === "approved") return "done";
  if (r.sla_status === "overdue") return "stuck";
  return (r.workflow ?? []).some((s) => s.active) || r.contract_id ? "progress" : "input";
}
const RAISED_LABEL = { done: "executed", stuck: "overdue", progress: "in progress", input: "awaiting triage" } as const;
const RAISED_PILL = { done: "done", stuck: "crit", progress: "ok", input: "risk" } as const;

/** What an approval is about: the agreement, or the request when there is no contract. */
function approvalSubject(a: ApprovalRequest): string {
  if (a.contract_id) return a.contract_title ? contractDisplayName({ title: a.contract_title, contract_type: a.contract_type }) : "Contract";
  return [a.intake_ref, a.intake_title].filter(Boolean).join(" · ") || "Request";
}

function DueCell({ r }: { r: IntakeRequest }) {
  const d = dueOf(r, created(r));
  return d ? <><div className="age num">{d.date.toLocaleDateString([], { day: "numeric", month: "short" })}</div><div className={`from${d.late ? " late" : ""}`}>{d.left}</div></> : <span className="dim">—</span>;
}

function RequestRow({ r, onOpen, raised }: { r: IntakeRequest; onOpen: (id: string) => void; raised?: boolean }) {
  const kind = kindOf(r); const Icon = kind.Icon;
  const cp = counterpartyOf(r);
  const st = raised ? raisedState(r) : null;
  const pill = st ? RAISED_PILL[st] : r.sla_status === "overdue" ? "crit" : r.sla_status === "at_risk" ? "risk" : "ok";
  const label = st ? RAISED_LABEL[st] : r.sla_status === "overdue" ? "Overdue" : r.sla_status === "at_risk" ? "Due soon" : "On track";
  const hasFlow = (r.workflow ?? []).length > 0;
  return (
    <tr onClick={() => onOpen(r.id)}>
      <td><div className="req">
        <div className={`chan k-${kind.tone}`} title={kind.label}><Icon className="ic" /></div>
        <div style={{ minWidth: 0 }}>
          <button type="button" className="nm" title={askOf(r)} onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>{askOf(r)}</button>
          <div className="prev"><span className="ref">{r.ref}</span>{raised ? "" : ` · from ${r.requester_name ?? "—"}`}</div>
        </div>
      </div></td>
      <td>{cp ? <><div className="cp" title={cp}>{cp}</div><div className="ty">{r.type_label}</div></> : <div className="ty">{r.type_label}</div>}</td>
      <td>{hasFlow ? <StageCell r={r} /> : <span className="dim">{r.contract_id ? "contract drafted" : "not started"}</span>}</td>
      <td><div className="stbox"><div className="flags"><span className={`spill ${pill}`}>{label}</span></div></div></td>
      <td className="cdue"><DueCell r={r} /></td>
      <td><div className="rowact"><button className="btn sm" onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>Open</button></div></td>
    </tr>
  );
}

function ApprovalRow({ a }: { a: ApprovalRequest }) {
  return (
    <tr>
      <td><div className="req">
        <div className="chan k-vio"><ShieldCheck className="ic" /></div>
        <div style={{ minWidth: 0 }}><span className="nm">{approvalSubject(a)}</span><div className="prev">Waiting for your approval</div></div>
      </div></td>
      <td><div className="ty">{a.step_order ? `Step ${a.step_order}` : "Approval"}</div></td>
      <td><span className="phase stg-approval">Approval</span></td>
      <td><div className="stbox"><div className="flags"><span className={`spill ${a.overdue ? "crit" : "ok"}`}>{a.overdue ? "Overdue" : "On track"}</span></div></div></td>
      <td className="cdue"><div className={`from${a.overdue ? " late" : ""}`}>{a.overdue ? "overdue · " : "due "}{dueText(a)}</div></td>
      <td><div className="rowact"><Link href="/approvals" className="btn pri sm">Review</Link></div></td>
    </tr>
  );
}

function TaskRow({ t, onDone, busy }: { t: IntakeTask; onDone: (id: string) => void; busy: boolean }) {
  return (
    <tr>
      <td><div className="req">
        <div className="chan k-sky"><CheckSquare className="ic" /></div>
        <div style={{ minWidth: 0 }}><span className="nm">{t.title}</span>{t.description ? <div className="prev">{t.description}</div> : null}</div>
      </div></td>
      <td><div className="ty">Task</div></td>
      <td><span className="dim">—</span></td>
      <td><div className="stbox"><div className="flags"><span className={`spill ${t.status === "done" ? "done" : "ok"}`}>{t.status}</span></div></div></td>
      <td className="cdue"><span className="dim">—</span></td>
      <td><div className="rowact"><button className="btn sm" disabled={t.status === "done" || busy} onClick={() => onDone(t.id)}>{t.status === "done" ? "Done" : "Mark done"}</button></div></td>
    </tr>
  );
}

export function MyWorkBoard({ onOpen }: { onOpen: (id: string) => void }) {
  const { data: mw, isLoading: mwLoading, error: mwError } = useQuery({ queryKey: ["intake-mywork"], queryFn: intakeApi.myWork, ...POLL });
  const { data: mineData, isLoading: mineLoading } = useQuery({ queryKey: ["intake-mine"], queryFn: intakeApi.mine, ...POLL });
  // Same query key as the Approvals page. A person without approval access just has none here; that must not
  // take the rest of the board down with it.
  const { data: approvalData } = useQuery({ queryKey: ["approvals"], queryFn: approvalsApi.list, retry: false, ...POLL });
  const qc = useQueryClient();
  const markDone = useMutation({
    mutationFn: (id: string) => intakeApi.updateTask(id, { status: "done" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["intake-mywork"] }),
  });
  const [tab, setTab] = useState<Tab>("action");
  const [search, setSearch] = useState("");
  // Lifecycle stages to show; approvals sit in Approval, tasks in no stage (so a stage filter hides them).
  const [stageSel, setStageSel] = useState<Set<string>>(new Set());

  const assigned = useMemo(() => {
    const seen = new Set<string>();
    return [...(mw?.awaiting_review ?? []), ...(mw?.my_tickets ?? [])].filter((r) => { if (seen.has(r.id)) return false; seen.add(r.id); return true; });
  }, [mw]);
  const raised = useMemo(() => mineData ?? [], [mineData]);
  const tasks = mw?.my_tasks ?? [];
  // Waiting on this person right now: still pending, and theirs to decide (the same test as the Approvals page).
  const approvals = useMemo(() => (approvalData ?? []).filter((a) => a.status === "pending" && a.can_decide), [approvalData]);

  const over = assigned.filter((r) => urgency(r) === "over");
  const soon = assigned.filter((r) => urgency(r) === "soon");
  const onDesk = assigned.length + tasks.length + approvals.length;
  const overdueCount = over.length + approvals.filter((a) => a.overdue).length;
  const starter = over[0] ?? soon[0] ?? assigned[0];
  const raisedBy = { done: 0, stuck: 0, progress: 0, input: 0 };
  for (const r of raised) raisedBy[raisedState(r)]++;

  const kcards: { tab: Tab; n: number; l: string; s: string; I: LucideIcon; t: string }[] = [
    { tab: "action", n: onDesk, l: "On your desk", s: "all open work", I: Inbox, t: "ind" },
    { tab: "over", n: overdueCount, l: "Overdue", s: "needs action now", I: AlertTriangle, t: "red" },
    { tab: "soon", n: soon.length, l: "Due soon", s: "SLA at risk", I: Clock, t: "amb" },
    { tab: "approvals", n: approvals.length, l: "Approvals", s: "waiting for you", I: ShieldCheck, t: "vio" },
    { tab: "tasks", n: tasks.length, l: "Tasks", s: "to do", I: CheckSquare, t: "sky" },
    { tab: "raised", n: raised.length, l: "Raised by you", s: `${raisedBy.done} executed`, I: FileText, t: "ind" },
  ];
  const pills: { tab: Tab; label: string; n: number; c: string }[] = [
    { tab: "action", label: "On your desk", n: onDesk, c: "ind" },
    { tab: "over", label: "Overdue", n: overdueCount, c: "red" },
    { tab: "soon", label: "Due soon", n: soon.length, c: "amb" },
    { tab: "approvals", label: "Approvals", n: approvals.length, c: "vio" },
    { tab: "tasks", label: "Tasks", n: tasks.length, c: "sky" },
    { tab: "raised", label: "Raised by you", n: raised.length, c: "emr" },
  ];

  const q = search.trim().toLowerCase();
  const hit = (...s: (string | null | undefined)[]) => !q || s.some((x) => (x ?? "").toLowerCase().includes(q));
  const stageHit = (st: string) => !stageSel.size || stageSel.has(st);
  const reqHit = (r: IntakeRequest) => stageHit(stageOf(r)) && hit(r.ref, r.subject, r.type_label, counterpartyOf(r), r.requester_name);
  const reqRows = (list: IntakeRequest[], isRaised = false) => list.filter(reqHit).map((r) => <RequestRow key={r.id} r={r} onOpen={onOpen} raised={isRaised} />);
  const apRows = (list: ApprovalRequest[]) => list.filter((a) => stageHit("approval") && hit(approvalSubject(a))).map((a) => <ApprovalRow key={a.id} a={a} />);
  const taskRows = () => tasks.filter((t) => !stageSel.size && hit(t.title, t.description)).map((t) => <TaskRow key={t.id} t={t} onDone={(id) => markDone.mutate(id)} busy={markDone.isPending} />);

  const rowsFor = (): { nodes: ReactNode[]; empty: string } => {
    switch (tab) {
      case "over": return { nodes: [...apRows(approvals.filter((a) => a.overdue)), ...reqRows(over)], empty: "Nothing overdue." };
      case "soon": return { nodes: reqRows(soon), empty: "Nothing due soon." };
      case "approvals": return { nodes: apRows(approvals), empty: "No approvals are waiting for you." };
      case "tasks": return { nodes: taskRows(), empty: "No tasks assigned to you." };
      case "raised": {
        const order = { stuck: 0, input: 1, progress: 2, done: 3 };
        return { nodes: reqRows([...raised].sort((a, b) => order[raisedState(a)] - order[raisedState(b)]), true), empty: raised.length ? "No requests match." : "You haven’t raised any requests yet." };
      }
      default:
        return {
          nodes: [...apRows(approvals), ...reqRows(over), ...reqRows(soon), ...reqRows(assigned.filter((r) => urgency(r) === "later")), ...taskRows()],
          empty: onDesk ? "Nothing matches your search." : "Nothing on your desk — inbox zero 🎉",
        };
    }
  };

  if ((mwLoading || mineLoading) && !mw && !mineData) return <CenterSpinner label="Loading your work…" />;
  if (mwError) return <ErrorState error={mwError} />;

  const { nodes, empty } = rowsFor();
  const attention = [...over, ...soon].slice(0, 4);

  return (
    <div className="li-board mw">
      <style dangerouslySetInnerHTML={{ __html: MW_CSS }} />
      <div className="split">
        <div className="maincol">
          <section className="ov" aria-label="Your work at a glance">
            <div className="kcards">
              {kcards.map((k) => (
                <button key={k.l} type="button" className={`kcard kc-${k.t}`} onClick={() => setTab(k.tab)} tabIndex={-1} aria-hidden="true" title={`Show ${k.l.toLowerCase()}`}>
                  <span className="kic"><k.I className="ic" aria-hidden /></span>
                  <span className="ktx"><span className="kl">{k.l}</span><b className="kn num">{k.n}</b><span className="ks">{k.s}</span></span>
                </button>
              ))}
            </div>
            <div className="stabs" role="group" aria-label="Filter your work">
              {pills.map((p) => (
                <button key={p.tab} type="button" aria-pressed={tab === p.tab} onClick={() => setTab(p.tab)} className={`stab${tab === p.tab ? " on" : ""}`} style={{ ["--c" as string]: `var(--c-${p.c})` }}>
                  <i aria-hidden /><span className="tl">{p.label}</span><span className="tn num">{p.n}</span>
                </button>
              ))}
            </div>
          </section>

          <div className="tcard">
            <div className="toolbar">
              <div className="search">
                <Search className="ic" />
                <input aria-label="Search your work" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search reference, subject or counterparty…" />
              </div>
              <FilterMenu label="Stage" options={LIFECYCLE.map((l) => ({ value: l.key, label: l.label }))} selected={stageSel} onToggle={(v) => setStageSel((p) => { const n = new Set(p); if (n.has(v)) n.delete(v); else n.add(v); return n; })} />
              {stageSel.size ? <button type="button" className="clear" onClick={() => setStageSel(new Set())}>Clear</button> : null}
              <span className="sp" />
              <span className="dim num count">{nodes.length} {nodes.length === 1 ? "item" : "items"}</span>
            </div>
            <div className="tablewrap">
              <table>
                <thead><tr><th>Item</th><th>Counterparty</th><th>Stage</th><th>Status</th><th className="cdue">Due</th><th><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{nodes}</tbody>
              </table>
              {nodes.length === 0 && <div className="empty">{empty}</div>}
            </div>
          </div>
        </div>

        <aside className="side" aria-label="Work insights">
          <section className="scard">
            <header>
              <span className="sic"><Zap className="ic" /></span>
              <h3>Start here</h3>
            </header>
            {starter ? (
              <>
                <p className="mwlead"><b>{starter.ref}</b> — {counterpartyOf(starter) ?? starter.type_label}</p>
                <p className="mwsub">{askOf(starter)}</p>
                <button type="button" className="btn pri mwgo" onClick={() => onOpen(starter.id)}>Open request<ChevronRight className="ic" /></button>
              </>
            ) : <p className="allclear">No request is waiting on you.</p>}
          </section>

          <section className="scard attn">
            <header>
              <span className="sic crit"><AlertTriangle className="ic" /></span>
              <h3>Needs attention</h3>
            </header>
            {attention.length === 0 ? <p className="allclear">Nothing overdue or at risk.</p> : (
              <>
                <div className="hero"><span className="hn crit num">{overdueCount}</span><span className="hl">overdue</span>{soon.length > 0 && <span className="hchip">{soon.length} at risk</span>}</div>
                <ul className="alist">
                  {attention.map((r) => (
                    <li key={r.id} className="arow">
                      <button type="button" onClick={() => onOpen(r.id)} className="amainbtn" aria-label={`Open ${r.ref}: ${askOf(r)}`}>
                        <span className="amain">
                          <span className="pt" title={askOf(r)}>{askOf(r)}</span>
                          <span className="ps">{r.ref} · {r.type_label}</span>
                        </span>
                      </button>
                      <span className="aside2"><button type="button" className="abtn" onClick={() => onOpen(r.id)}>Open</button></span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <section className="scard">
            <header>
              <span className="sic"><FileText className="ic" /></span>
              <h3>Requests you raised</h3>
              <button type="button" className="slink" style={{ margin: 0 }} onClick={() => setTab("raised")}>View all</button>
            </header>
            {raised.length === 0 ? <p className="allclear">You haven&rsquo;t raised any requests yet.</p> : (
              <ul className="mwstat">
                <li><span>In progress</span><b className="num">{raisedBy.progress}</b></li>
                <li><span>Awaiting triage</span><b className="num">{raisedBy.input}</b></li>
                <li><span>Overdue</span><b className="num crit">{raisedBy.stuck}</b></li>
                <li><span>Executed</span><b className="num">{raisedBy.done}</b></li>
              </ul>
            )}
            {onDesk > 0 ? <p className="mwsub" style={{ marginTop: 10 }}>{plural(onDesk, "item")} on your desk.</p> : null}
          </section>
        </aside>
      </div>
    </div>
  );
}

const MW_CSS = `
.mw .kcards{grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:0 0 12px}
.mw .kcard{padding:12px;gap:10px}
.mw .kcard .kic{width:36px;height:36px;border-radius:10px}
.mw .kcard .kn{font-size:22px}
.mw .kcard .kl{white-space:nowrap}
.mw .kcard .ks{display:none}
@media (max-width:1400px){.mw .kcards{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.mw .kcards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.mw .kcard{cursor:pointer}
.mw .kcard .kl{text-transform:none}
.mw .mwlead{margin:0;font-size:13px;color:var(--ink)}
.mw .mwsub{margin:4px 0 0;font-size:12.5px;color:var(--ink-2)}
.mw .mwgo{margin-top:12px;width:100%;justify-content:center}
.mw .mwstat{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.mw .mwstat li{display:flex;justify-content:space-between;padding:7px 2px;border-bottom:1px solid var(--border);font-size:12.5px;color:var(--ink-2)}
.mw .mwstat li:last-child{border-bottom:0}
.mw .mwstat b{color:var(--ink);font-weight:650} .mw .mwstat b.crit{color:var(--c-red)}
.mw .tcard .tablewrap td .nm{display:block;max-width:300px;text-align:left}
/* type labels and reference lines wrap instead of ending in "…" */
.li-board td .ty,.li-board td .cp{white-space:normal;overflow:visible;text-overflow:clip;max-width:100%;word-break:break-word;line-height:1.3}
.li-board .arow .ps,.li-board .scard .ps{white-space:normal;overflow:visible;text-overflow:clip;word-break:break-word;line-height:1.35}
.li-board .arow .pt,.li-board .scard .pt{white-space:normal;overflow:visible;text-overflow:clip;word-break:break-word;line-height:1.3}
.li-board td .nm,.li-board td .prev{white-space:normal;overflow:visible;text-overflow:clip;word-break:break-word}
.li-board tbody td{vertical-align:middle}
`;
