"use client";
import { useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import Link from "next/link";
import { AlertTriangle, ChevronDown, ChevronRight, ListChecks, Loader2, LocateFixed, Undo2 } from "lucide-react";
import { LIFECYCLE } from "@/components/lifecycle-view";
import { plainStepName } from "@/lib/workflow-steps";
import { fmtRelative, fmtDateTime, fmtDate } from "@/lib/utils";
import type { IntakeApprovalRung, LifecycleStage, WorkflowRun, WorkflowRunBriefEntry } from "@/lib/types";

export interface StepRow {
  label: string;
  tone: "done" | "cur" | "warn" | "todo";
  node: string;
  badge?: string; badgeTone: "done" | "cur" | "warn" | "skip";
  skipped?: boolean;
  sidx?: number;
  stage?: LifecycleStage | null;
  assign?: string;
  wait?: { who: string | null; extra: string | null; due: string | null; overdue: boolean } | null;
  focus?: boolean;
  next?: boolean;
  detail: [string, string][];
  doorway?: { href: string; label: "Open the draft" | "Review & redline" | "Open to negotiate" };
  notice?: ReactNode;
  /** What kind of step this is, who owns it, how long it may take, when it last moved, and why it is where it is. */
  kind?: string; team?: string; sla?: string; updated?: string | null; note?: string | null; parallel?: boolean; conditional?: boolean;
}
export type FlowState =
  | { kind: "running"; name: string; status: WorkflowRun["status"] }
  | { kind: "suggested"; name: string; reasoning: string; canStart: boolean; starting: boolean; onStart: () => void }
  | { kind: "setting_up" }
  | { kind: "none"; reasoning: string | null; canPick: boolean; flows: { id: string; name: string }[]; picking: boolean; onPick: (flowId: string) => void };
export interface ExtraDetail { id: string; label: string; node: ReactNode }

const ini = (n: string) => n.split(/[ /]/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

/** The approval chain, rung by rung: label, status, quorum and every recorded decision. */
export function ApprovalChain({ rungs }: { rungs: IntakeApprovalRung[] }) {
  return (
    <div>
      {rungs.slice().sort((a, b) => a.step_order - b.step_order).map((a, i) => {
        const st = (a.status || "pending").toLowerCase();
        const cls = st === "approved" ? "ok" : st === "rejected" ? "no" : "pend";
        return (
          <div key={a.approval_request_id ?? `rung-${i}`} className="appr"><div className="an">{ini(a.approver_label || "?")}</div>
            <div className="al"><div className="n">{a.approver_label}</div><div className="r">step {a.step_order}{a.mode === "all" && a.needed ? ` · ${a.approvals ?? 0}/${a.needed}` : ""}</div></div>
            <span className={`as ${cls}`}>{st === "approved" ? "approved" : st === "rejected" ? "rejected" : "waiting"}</span>
            {(a.decisions ?? []).map((d, j) => (
              <div key={j} className="why">
                {d.approver_name ?? "Approver"} · {d.decision === "approve" ? "approved" : "rejected"} · {fmtDateTime(d.decided_at)}{d.comment ? ` — “${d.comment}”` : ""}
              </div>
            ))}</div>
        );
      })}
    </div>
  );
}

function Toggle({ id, label, open, onToggle, children }: { id: string; label: string; open: boolean; onToggle: (id: string) => void; children: ReactNode }) {
  return (
    <div>
      <button type="button" className="xtoggle" aria-expanded={open} onClick={() => onToggle(id)}>
        {open ? <ChevronDown className="ic" /> : <ChevronRight className="ic" />}{label}
      </button>
      {open ? children : null}
    </div>
  );
}

type FilterKey = "all" | "active" | "upcoming" | "done" | "skipped";
const FILTERS: [FilterKey, string][] = [["all", "All"], ["active", "In progress"], ["upcoming", "Upcoming"], ["done", "Done"], ["skipped", "Skipped"]];
const matches = (w: StepRow, f: FilterKey) =>
  f === "all" ? true : f === "skipped" ? !!w.skipped : w.skipped ? false
    : f === "active" ? w.tone === "cur" || w.tone === "warn" : f === "done" ? w.tone === "done" : w.tone === "todo";

const flowText = (s: WorkflowRun["status"]) => (s === "complete" ? " · complete" : s === "failed" ? " · failed" : s === "cancelled" ? " · cancelled" : "");

function FlowLine({ flow }: { flow: FlowState }) {
  if (flow.kind === "running") return <div className="flowmeta"><b>Workflow: {flow.name}</b>{flowText(flow.status)}</div>;
  if (flow.kind === "suggested") {
    return (
      <div className="flowmeta"><b>Suggested: {flow.name}.</b> <span className="dim">{flow.reasoning}</span>
        {flow.canStart ? <button type="button" className="ministep startit" disabled={flow.starting} onClick={flow.onStart}>Start it</button> : null}
      </div>
    );
  }
  if (flow.kind === "setting_up") return <div className="flowmeta" aria-live="polite"><b>Setting up the workflow…</b> <span className="dim">This request was just filed. The workflow appears here in a few seconds.</span></div>;
  return (
    <div className="flowmeta">
      <b>No workflow is set up for this request.</b> <span className="dim">{flow.reasoning ?? "Pick one to start it."}</span>
      {flow.canPick ? (
        <select className="flowpick" value="" aria-label="Pick a workflow" disabled={flow.picking} onChange={(e) => { if (e.target.value) flow.onPick(e.target.value); }}>
          <option value="">Pick a workflow to start…</option>
          {flow.flows.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      ) : null}
    </div>
  );
}

function Tile({ w, i, check }: { w: StepRow; i: number; check: { label: string; onClick: () => void; busy: boolean } | null }) {
  const stIdx = w.stage ? LIFECYCLE.findIndex((x) => x.key === w.stage) : -1;
  const kind = w.skipped ? "skip" : w.tone;
  const name = w.label; // the step number / tick now lives in the timeline node beside it
  const stamp = w.updated ? `${fmtDate(w.updated)}, ${new Date(w.updated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${fmtRelative(w.updated)}` : "";
  const sub = w.skipped ? null
    : w.tone === "done" ? `Completed${stamp ? ` · ${stamp}` : ""}`
    : w.tone === "cur" ? `In progress${stamp ? ` · ${stamp}` : ""}`
    : w.tone === "warn" ? `Needs attention${stamp ? ` · ${stamp}` : ""}`
    : w.next ? "Up next" : "Pending";
  const notes = w.note && !w.skipped ? <small className="tnote">{w.note}</small> : null;
  const status = w.skipped ? <small>Skipped</small>
    : w.tone === "done" ? <small>{w.assign ?? "Done"}</small>
    : w.tone === "warn" ? <small>{w.badge}</small>
    : w.tone === "cur" ? (
      <>
        <small>{w.wait?.who ? `With ${w.wait.who}` : w.badge}</small>
        {w.wait?.extra ? <small className="clamp">{w.wait.extra}</small> : null}
        {w.wait?.due ? <small className={w.wait.overdue ? "over" : undefined}>{w.wait.due}</small> : null}
      </>
    ) : null;
  return (
    <div className={`t ${kind}`} style={{ "--i": i } as CSSProperties}>
      <span className="tnode" aria-hidden>{w.node}</span>
      <div className="tbody">
        {stIdx >= 0 ? <span className="tstage">{LIFECYCLE[stIdx].label}</span> : null}
        <b>{name}</b>
        {sub ? <span className="tsub">{sub}</span> : null}
        {w.kind || w.team || w.sla || w.parallel || w.conditional ? (
          <span className="tmeta">
            {w.kind ? <span className="tchip kind">{w.kind}</span> : null}
            {w.team ? <span className="tchip" title="Team">{w.team}</span> : null}
            {w.sla ? <span className="tchip" title="Time allowed for this step">{w.sla}</span> : null}
            {w.parallel ? <span className="tchip" title="Runs at the same time as the step next to it">Runs alongside</span> : null}
            {w.conditional ? <span className="tchip" title="Only runs when its condition is met">Conditional</span> : null}
          </span>
        ) : null}
        {w.tone === "cur" || w.tone === "warn" ? (
          <div className="tinfo tmsg">
            <span className="tmav" aria-hidden>{(w.tone === "cur" && w.wait?.who ? w.wait.who : w.badge ?? "?").split(/[\s·/()@.]+/).filter(Boolean).slice(0, 2).map((x) => x[0]).join("").toUpperCase()}</span>
            <div className="tbub">
              <small className="tmname">{w.tone === "cur" ? (w.wait?.who ? `With ${w.wait.who}` : w.badge) : w.badge}</small>
              {notes}
              {w.tone === "cur" && w.wait?.extra ? <small className="clamp">{w.wait.extra}</small> : null}
              {w.tone === "cur" && w.wait?.due ? <small className={`tmdue${w.wait.overdue ? " over" : ""}`}>{w.wait.due}</small> : null}
            </div>
          </div>
        ) : <>{notes}{status}</>}
        {w.focus && check ? <button type="button" className="ministep" disabled={check.busy} onClick={check.onClick}>{check.busy ? "Working…" : check.label}</button> : null}
        {w.focus && w.doorway ? <Link href={w.doorway.href} className="ministep doorway">{w.doorway.label}</Link> : null}
      </div>
    </div>
  );
}

/** The request's workflow as one wide card: a tile per step, the open one highlighted, and the
 *  detail (results, chain, trace, send back) behind links underneath. */
export function WorkflowCard(p: {
  rows: StepRow[];
  countText: string | null;
  flow: FlowState;
  error: string | null;
  check: { label: string; onClick: () => void; busy: boolean } | null;
  extras: ExtraDetail[];
  trace: WorkflowRunBriefEntry[];
  sendBack: {
    options: { value: string; label: string }[]; to: string; note: string;
    onTo: (v: string) => void; onNote: (v: string) => void; onSend: () => void; busy: boolean;
    selectRef: RefObject<HTMLSelectElement | null>;
    open: boolean; onToggle: () => void;
  } | null;
}) {
  // Everything is shown by default (result, approval chain, trace); a person can fold a section away.
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const isOpen = (id: string) => !closed.has(id);
  const toggle = (id: string) => setClosed((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const sb = p.sendBack;
  // Steps skipped because their condition was not met (Quality review, Privacy review…) do not apply to this request:
  // hidden unless asked for, so the list shows only what can happen here.
  const [showNA, setShowNA] = useState(false);
  const notApplicable = p.rows.filter((w) => w.skipped && w.conditional);
  const rows = showNA ? p.rows : p.rows.filter((w) => !(w.skipped && w.conditional));
  const [filter, setFilter] = useState<FilterKey>("all");
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const listRef = useRef<HTMLDivElement>(null);
  // Consecutive steps of one lifecycle stage form a group; a step with no stage joins the group before it.
  const groups: { key: string; stage: LifecycleStage | null; rows: { w: StepRow; i: number }[] }[] = [];
  rows.forEach((w, i) => {
    const last = groups[groups.length - 1];
    if (last && (!w.stage || w.stage === last.stage)) last.rows.push({ w, i });
    else groups.push({ key: `${w.stage ?? "none"}-${i}`, stage: w.stage ?? null, rows: [{ w, i }] });
  });
  const jumpToCurrent = () => {
    setFilter("all"); setFolded(new Set());
    window.setTimeout(() => {
      const el = listRef.current?.querySelector<HTMLElement>(".t.cur, .t.warn");
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
      window.setTimeout(() => el.classList.remove("flash"), 1600);
    }, 60);
  };
  const withResult = p.rows.filter((w) => w.detail.length > 0);
  const notices = p.rows.filter((w) => w.notice);
  const link = (id: string, label: string, isOpen: boolean, onClick: () => void) => (
    <button key={id} type="button" className="xtoggle" aria-expanded={isOpen} onClick={onClick}>
      {isOpen ? <ChevronDown className="ic" /> : <ChevronRight className="ic" />}{label}
    </button>
  );
  return (
    <div className="card tint-vio wfcard">
      <div className="ch"><div className="glyph g-vio"><ListChecks className="ic" aria-hidden /></div><h2>Workflow</h2>
        {p.flow.kind === "running" ? <span className="wfname">{p.flow.name}</span> : null}
        {p.countText ? <span className="cnt">{p.countText}</span> : null}
        {p.rows.length > 0 ? <span className="wfprog" aria-hidden><i style={{ width: `${Math.round((p.rows.filter((w) => w.tone === "done").length / p.rows.length) * 100)}%` }} /></span> : null}
      </div>
      <div className="cb"><p className="hint">The steps this request goes through, and who each one is waiting on.</p>
        {p.flow.kind === "running" && p.flow.status === "running" ? null : <FlowLine flow={p.flow} />}
        {p.error ? <div className="flowmeta warnline"><AlertTriangle className="ic" /> {p.error}</div> : null}
        {p.rows.length === 0 ? <div className="dim" style={{ fontSize: 12.5 }}>No steps yet.</div> : (
          <>
            <div className="wf2-bar">
              <div className="wf2-seg" role="group" aria-label="Show steps">
                {FILTERS.map(([key, label]) => {
                  const n = rows.filter((w) => matches(w, key)).length;
                  return <button key={key} type="button" aria-pressed={filter === key} disabled={key !== "all" && n === 0} onClick={() => setFilter(key)}>{label}<i>{n}</i></button>;
                })}
              </div>
              <div className="wf2-tools">
                {rows.some((w) => w.tone === "cur" || w.tone === "warn") ? <button type="button" className="wf2-btn" onClick={jumpToCurrent}><LocateFixed aria-hidden />Current step</button> : null}
                {groups.length > 1 ? <button type="button" className="wf2-btn" onClick={() => setFolded(folded.size ? new Set() : new Set(groups.map((g) => g.key)))}>{folded.size ? "Expand all" : "Collapse all"}</button> : null}
              </div>
            </div>
            <div className="wft" ref={listRef}>{groups.map((g) => {
              const shown = g.rows.filter(({ w }) => matches(w, filter));
              if (!shown.length) return null;
              const real = g.rows.filter(({ w }) => !w.skipped);
              const doneN = real.filter(({ w }) => w.tone === "done").length;
              const open = !folded.has(g.key);
              return (
                <section key={g.key} className="wf2-stage" data-stage={g.stage ?? undefined} data-state={g.rows.every(({ w }) => w.skipped) ? "skip" : doneN === real.length ? "done" : g.rows.some(({ w }) => w.tone === "cur" || w.tone === "warn") ? "cur" : "todo"}>
                  {g.stage ? (
                    <button type="button" className="wsg" aria-expanded={open} onClick={() => setFolded((f) => { const n = new Set(f); if (n.has(g.key)) n.delete(g.key); else n.add(g.key); return n; })}>
                      <ChevronDown className="wf2-chev" aria-hidden />
                      <span>{LIFECYCLE.find((x) => x.key === g.stage)?.label ?? g.stage}</span>
                      <span className="wf2-sp" aria-hidden><i style={{ width: `${real.length ? Math.round((doneN / real.length) * 100) : 0}%` }} /></span>
                      <em>{g.rows.every(({ w }) => w.skipped) ? "Skipped" : `${doneN} of ${real.length} done`}</em>
                    </button>
                  ) : null}
                  {open ? <div className="wf2-list">{shown.map(({ w, i }) => <Tile key={i} w={w} i={i} check={p.check} />)}</div> : null}
                </section>
              );
            })}</div>
          </>
        )}
        {notApplicable.length > 0 ? (
          <button type="button" className="xtoggle wf2-na" aria-expanded={showNA} onClick={() => setShowNA((v) => !v)}>
            {showNA ? <ChevronDown className="ic" /> : <ChevronRight className="ic" />}{showNA ? "Hide" : "Show"} {notApplicable.length} step{notApplicable.length === 1 ? "" : "s"} that {notApplicable.length === 1 ? "does" : "do"} not apply to this request
          </button>
        ) : null}
        {notices.map((w, i) => <div key={i} className="wfnotice"><div className="lbl">{w.label}</div>{w.notice}</div>)}
        <div className="wflinks">
          {withResult.length > 0 ? link("result", "Step details", isOpen("result"), () => toggle("result")) : null}
          {p.extras.map((x) => link(x.id, x.label, isOpen(x.id), () => toggle(x.id)))}
          {p.trace.length > 0 ? link("trace", `Agent hand-off trace (${p.trace.length})`, isOpen("trace"), () => toggle("trace")) : null}
          {sb ? <button type="button" className="sbtn" aria-expanded={sb.open} onClick={sb.onToggle}><Undo2 className="ic" aria-hidden />Send back for rework</button> : null}
        </div>
        {isOpen("result") && withResult.length > 0 ? (
          <div className="wfpanel">{withResult.map((w, i) => (
            <div key={i}><div className="lbl">{w.label}</div>
              <div className="sdetail">{w.detail.map(([k, v]) => <div key={k} className="rr"><span className="rk">{k}</span><span className="rv">{v}</span></div>)}</div></div>
          ))}</div>
        ) : null}
        {p.extras.filter((x) => isOpen(x.id)).map((x) => <div key={x.id} className="wfpanel"><div className="lbl">{x.label}</div>{x.node}</div>)}
        {isOpen("trace") && p.trace.length > 0 ? (
          <div className="wfpanel"><ol className="trace">
            {p.trace.map((b, i) => <li key={i}><b>{plainStepName(b.step, b.type)}</b>: {b.summary}</li>)}
          </ol></div>
        ) : null}
        {sb && sb.open ? (
          <div className="sendback wfpanel">
            <div className="sbhead"><span className="sbico"><Undo2 className="ic" aria-hidden /></span><div><div className="lbl">Send back for rework</div><p>Return the work to an earlier step. The owner of that step sees your reason.</p></div></div>
            <label className="sbf"><span>Step to return to</span>
              <select className="sel" ref={sb.selectRef} aria-label="Step to send back to" value={sb.to} onChange={(e) => sb.onTo(e.target.value)}>
                <option value="">Pick the step to return to…</option>
                {sb.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="sbf"><span>Reason</span>
              <textarea className="sbta" rows={3} value={sb.note} placeholder="Reason / question for the owner…" aria-label="Reason for sending back" onChange={(e) => sb.onNote(e.target.value)} />
            </label>
            <div className="sbact">
              <button type="button" className="btn pri" disabled={sb.busy || sb.to === ""} aria-describedby={sb.to === "" ? "sendback-hint" : undefined} title={sb.to === "" ? "Pick the step to return to first" : undefined} onClick={sb.onSend}>
                {sb.busy ? <Loader2 className="ic" /> : <Undo2 className="ic" />}Send back
              </button>
              <button type="button" className="btn" onClick={sb.onToggle}>Cancel</button>
            </div>
            {sb.to === "" ? <div id="sendback-hint" className="dim" style={{ fontSize: 11.5 }}>Pick the step to return to, then Send back is available.</div> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export const STEPS_CSS = `
.ro .flowmeta{font-size:12.5px;color:var(--ink-2);line-height:1.6;overflow-wrap:anywhere}
.ro .flowmeta.warnline{color:var(--warn);margin-top:6px} .ro .flowmeta .ic{width:13px;height:13px;vertical-align:-2px}
.ro .flowmeta .startit{margin:0 0 0 8px}
.ro .flowpick{display:block;margin-top:8px;width:100%;max-width:360px;padding:7px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .wfname{color:var(--ink-3);font-size:12.5px;overflow-wrap:anywhere;min-width:0}
/* Vertical timeline: a node per step on a connecting line, the open one ringed. */
.ro .wft{display:flex;flex-direction:column;gap:0;margin-top:4px}
.ro .wft .t{position:relative;min-width:0;border:0;background:none;border-radius:0;padding:0 0 16px 40px;display:flex;flex-direction:column;gap:2px;font-size:12.5px;overflow-wrap:anywhere}
.ro .wft .t::before{content:"";position:absolute;left:12px;top:26px;bottom:-2px;width:2px;background:var(--border)}
.ro .wft .t:last-child{padding-bottom:2px} .ro .wft .t:last-child::before{display:none}
.ro .wft .t.done::before{background:var(--good)}
.ro .wft .tnode{position:absolute;left:0;top:0;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font:700 11px var(--sans);background:var(--surface);border:2px solid var(--border-strong);color:var(--ink-3)}
.ro .wft .t.done .tnode{background:var(--good);border-color:var(--good);color:#fff}
.ro .wft .t.cur .tnode{border-color:var(--accent);color:var(--accent);box-shadow:0 0 0 4px var(--accent-soft)}
.ro .wft .t.warn .tnode{border-color:var(--warn);color:var(--warn);box-shadow:0 0 0 4px var(--warn-soft)}
.ro .wft .t.skip .tnode{border-style:dashed}
.ro .wft .t b{font-weight:650;font-size:13.5px;color:var(--ink);line-height:1.35;padding-top:3px}
.ro .wft .t.todo b,.ro .wft .t.skip b{color:var(--ink-2);font-weight:600}
.ro .wft .t small{color:var(--ink-3);font-size:12px}
.ro .wft .t.cur{background:linear-gradient(90deg,var(--accent-soft),transparent 70%);border-radius:10px;margin-left:-8px;padding-left:48px;padding-right:8px;padding-top:6px;margin-bottom:6px}
.ro .wft .t.cur .tnode{left:8px;top:6px} .ro .wft .t.cur::before{left:20px;top:32px}
.ro .wft .t.cur b{color:var(--accent)}
.ro .wft .t .over{color:var(--crit);font-weight:600}
.ro .wft .t .tstage{font:700 9.5px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);padding-top:3px}
.ro .wft .t .clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ro .wft .t .ministep{align-self:flex-start;margin-top:6px}
.ro .wflinks{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:10px} .ro .wfpanel{margin-top:6px} .ro .wfnotice{margin-top:8px}
.ro .xtoggle{display:inline-flex;align-items:center;gap:4px;margin:2px 0 6px;padding:0;border:0;background:none;color:var(--accent);font:600 11.5px var(--sans);cursor:pointer;text-align:left} .ro .xtoggle .ic{width:13px;height:13px}
.ro .sdetail{margin:2px 0 8px;padding:9px 11px;border:1px solid var(--border);border-radius:9px;background:var(--inset);display:grid;grid-template-columns:auto 1fr;gap:4px 12px}
.ro .sdetail .rr{display:contents} .ro .sdetail .rk{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.04em;color:var(--ink-3)} .ro .sdetail .rv{font-size:12px;color:var(--ink);overflow-wrap:anywhere}
.ro .trace{margin:6px 0 4px;padding:10px 14px 10px 28px;border:1px solid var(--border);border-radius:10px;background:var(--surface);display:flex;flex-direction:column;gap:8px;font-size:12.5px;color:var(--ink-2);overflow-wrap:anywhere} .ro .trace b{color:var(--ink)}
.ro .sendback{margin-top:14px;padding:12px;border:1px dashed var(--border-strong);border-radius:10px;display:flex;flex-direction:column;gap:8px}
.ro .sendback .lbl{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ro .ministep{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:7px;border:1px solid var(--accent);background:var(--accent);color:var(--accent-ink);font:600 11px var(--sans);cursor:pointer}
.ro .ministep[disabled]{opacity:.5;pointer-events:none} .ro .ministep.doorway{text-decoration:none}
.ro .appr{display:flex;flex-wrap:wrap;align-items:center;gap:9px;padding:8px 0;border-bottom:1px solid var(--border)} .ro .appr:last-child{border-bottom:0}
.ro .appr .an{width:26px;height:26px;border-radius:50%;background:var(--surface-2);display:grid;place-items:center;font:700 10px var(--mono);color:var(--ink-2);flex:none}
.ro .appr .al{flex:1;min-width:0} .ro .appr .al .n{font-weight:600;font-size:12.5px;overflow-wrap:anywhere} .ro .appr .al .r{font-size:11px;color:var(--ink-3)}
.ro .appr .as{font:600 10px var(--sans);padding:2px 8px;border-radius:99px;flex:none} .ro .as.ok{background:var(--good-soft);color:var(--good)} .ro .as.pend{background:var(--surface-2);color:var(--ink-3)} .ro .as.no{background:var(--crit-soft);color:var(--crit)}
.ro .appr .why{flex-basis:100%;color:var(--ink-3);font-size:12px;overflow-wrap:anywhere}
`;
