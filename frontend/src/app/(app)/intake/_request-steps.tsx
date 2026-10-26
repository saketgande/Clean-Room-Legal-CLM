"use client";
import { useState, type ReactNode, type RefObject } from "react";
import Link from "next/link";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2, Undo2 } from "lucide-react";
import { LIFECYCLE } from "@/components/lifecycle-view";
import { plainStepName } from "@/lib/workflow-steps";
import { fmtDateTime } from "@/lib/utils";
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

function Tile({ w, check }: { w: StepRow; check: { label: string; onClick: () => void; busy: boolean } | null }) {
  const stIdx = w.stage ? LIFECYCLE.findIndex((x) => x.key === w.stage) : -1;
  const kind = w.skipped ? "skip" : w.tone;
  const name = w.node === "–" || w.node === "✓" || w.node === "!" ? `${w.node} ${w.label}` : `${w.node} · ${w.label}`;
  return (
    <div className={`t ${kind}`}>
      {stIdx >= 0 ? <span className="tstage">{LIFECYCLE[stIdx].label}</span> : null}
      <b>{name}</b>
      {w.skipped ? <small>Skipped</small>
        : w.tone === "done" ? <small>{w.assign ?? "Done"}</small>
        : w.tone === "warn" ? <small>{w.badge}</small>
        : w.tone === "cur" ? (
          <>
            <small>{w.wait?.who ? `With ${w.wait.who}` : w.badge}</small>
            {w.wait?.extra ? <small className="clamp">{w.wait.extra}</small> : null}
            {w.wait?.due ? <small className={w.wait.overdue ? "over" : undefined}>{w.wait.due}</small> : null}
          </>
        ) : w.next ? <small>Next</small> : null}
      {w.focus && check ? <button type="button" className="ministep" disabled={check.busy} onClick={check.onClick}>{check.busy ? "Working…" : check.label}</button> : null}
      {w.focus && w.doorway ? <Link href={w.doorway.href} className="ministep doorway">{w.doorway.label}</Link> : null}
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
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const sb = p.sendBack;
  const withResult = p.rows.filter((w) => w.detail.length > 0);
  const notices = p.rows.filter((w) => w.notice);
  const link = (id: string, label: string, isOpen: boolean, onClick: () => void) => (
    <button key={id} type="button" className="xtoggle" aria-expanded={isOpen} onClick={onClick}>
      {isOpen ? <ChevronDown className="ic" /> : <ChevronRight className="ic" />}{label}
    </button>
  );
  return (
    <div className="card">
      <div className="ch"><h2>Workflow</h2>
        {p.flow.kind === "running" ? <span className="wfname">{p.flow.name}</span> : null}
        {p.countText ? <span className="cnt">{p.countText}</span> : null}
      </div>
      <div className="cb">
        {p.flow.kind === "running" && p.flow.status === "running" ? null : <FlowLine flow={p.flow} />}
        {p.error ? <div className="flowmeta warnline"><AlertTriangle className="ic" /> {p.error}</div> : null}
        {p.rows.length === 0 ? <div className="dim" style={{ fontSize: 12.5 }}>No steps yet.</div> : (
          <div className="wft">{p.rows.map((w, i) => <Tile key={i} w={w} check={p.check} />)}</div>
        )}
        {notices.map((w, i) => <div key={i} className="wfnotice"><div className="lbl">{w.label}</div>{w.notice}</div>)}
        <div className="wflinks">
          {withResult.length > 0 ? link("result", "Review result", open.has("result"), () => toggle("result")) : null}
          {p.extras.map((x) => link(x.id, x.label, open.has(x.id), () => toggle(x.id)))}
          {p.trace.length > 0 ? link("trace", `Agent hand-off trace (${p.trace.length})`, open.has("trace"), () => toggle("trace")) : null}
          {sb ? link("sendback", "Send back for rework", sb.open, sb.onToggle) : null}
        </div>
        {open.has("result") && withResult.length > 0 ? (
          <div className="wfpanel">{withResult.map((w, i) => (
            <div key={i}><div className="lbl">{w.label}</div>
              <div className="sdetail">{w.detail.map(([k, v]) => <div key={k} className="rr"><span className="rk">{k}</span><span className="rv">{v}</span></div>)}</div></div>
          ))}</div>
        ) : null}
        {p.extras.filter((x) => open.has(x.id)).map((x) => <div key={x.id} className="wfpanel">{x.node}</div>)}
        {open.has("trace") && p.trace.length > 0 ? (
          <div className="wfpanel"><ol className="trace">
            {p.trace.map((b, i) => <li key={i}><b>{plainStepName(b.step, b.type)}</b>: {b.summary}</li>)}
          </ol></div>
        ) : null}
        {sb && sb.open ? (
          <div className="sendback wfpanel">
            <div className="lbl">Send back for rework</div>
            <select className="sel" ref={sb.selectRef} aria-label="Step to send back to" value={sb.to} onChange={(e) => sb.onTo(e.target.value)}>
              <option value="">Pick the step to return to…</option>
              {sb.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <input className="cin" value={sb.note} placeholder="Reason / question for the owner…" aria-label="Reason for sending back" onChange={(e) => sb.onNote(e.target.value)} />
            <button type="button" className="btn" disabled={sb.busy || sb.to === ""} aria-describedby={sb.to === "" ? "sendback-hint" : undefined} title={sb.to === "" ? "Pick the step to return to first" : undefined} onClick={sb.onSend}>
              {sb.busy ? <Loader2 className="ic" /> : <Undo2 className="ic" />}Send back
            </button>
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
.ro .wft{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:8px}
.ro .wft .t{min-width:0;border:1px solid var(--border);border-radius:9px;padding:8px 10px;display:flex;flex-direction:column;gap:2px;font-size:12px;overflow-wrap:anywhere}
.ro .wft .t b{font-weight:600;font-size:12.5px} .ro .wft .t small{color:var(--ink-3);font-size:12px}
.ro .wft .t.done{background:var(--good-soft);border-color:transparent} .ro .wft .t.cur{border-color:var(--accent);background:var(--accent-soft)}
.ro .wft .t.warn{border-color:var(--warn);background:var(--warn-soft)} .ro .wft .t.skip{border-style:dashed;color:var(--ink-3)} .ro .wft .t.todo b{color:var(--ink-2)}
.ro .wft .t .over{color:var(--crit);font-weight:600} .ro .wft .t .tstage{font:600 9.5px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ro .wft .t .clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ro .wft .t .ministep{align-self:flex-start;margin-top:4px}
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
