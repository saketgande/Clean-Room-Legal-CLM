"use client";

import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { LIFECYCLE, LifecycleView } from "@/components/lifecycle-view";
import { fmtDate } from "@/lib/utils";
import type { LifecycleAction, LifecycleRow, LifecycleStage, WorkflowRunComment, WorkflowRunStep } from "@/lib/types";

export function RequestHeader(p: {
  title: string;
  refCode: string;
  requesterName: string | null;
  raisedAt: string | null;
  ownerLabel: string | null;
  highRisk: boolean;
  sla: string | null;
  onBack: () => void;
  /** The same icon chip the list heading wears. */
  icon?: ReactNode;
  /** Quick-action buttons on the right of the header. */
  actions?: ReactNode;
  /** Chips under the title: the request's status, and what it is about. */
  statusLabel?: string;
  chips?: string[];
}) {
  const overdue = p.sla === "overdue";
  const atRisk = p.sla === "at_risk";
  return (
    <div className={`rohd${overdue ? " late" : atRisk ? " risk" : ""}`}>
      {p.icon ? <span className="pico" aria-hidden>{p.icon}</span> : null}
      <div className="ttlblk">
        <div className="crumb"><button type="button" className="back" onClick={p.onBack}>← Legal Intake</button><span>/</span><span>Requests</span><span>/</span><b className="refchip">{p.refCode}</b></div>
        <div className="rh-title"><h1>{p.title}</h1>{p.statusLabel ? <span className="rh-status">{p.statusLabel}</span> : null}</div>
        {p.chips && p.chips.length ? <div className="rh-chips">{p.chips.filter(Boolean).map((c, i) => <span key={c} data-i={i}>{c}</span>)}</div> : null}
        <div className="subl">
          {p.refCode} · raised by {p.requesterName ?? "the requester"}{p.raisedAt ? ` on ${fmtDate(p.raisedAt)}` : ""} · owner {p.ownerLabel ?? "unassigned"}
        </div>
      </div>
      {p.actions ? <div className="hdacts">{p.actions}</div> : null}
      {p.highRisk || overdue || atRisk ? (
        <div className="pills">
          {p.highRisk ? <span className="pill bad">High risk</span> : null}
          {overdue ? <span className="pill bad">Overdue</span> : null}
          {atRisk ? <span className="pill warn">SLA at risk</span> : null}
        </div>
      ) : null}
    </div>
  );
}

type ProgressProps = {
  steps: WorkflowRunStep[];
  current: LifecycleStage;
  comments: WorkflowRunComment[];
  extras: Partial<Record<LifecycleStage, LifecycleRow[]>>;
  line: string;
  tone: "info" | "warn";
  working: boolean;
  /** How the draft came about, for the drafting step. */
  draftNote?: string | null;
  /** Retry / Re-read on a lifecycle row; left out for someone who may not press it. */
  onAction?: (action: LifecycleAction) => Promise<unknown> | void;
};

/** The one-line "where it stands" under the header. The stages themselves are the StageTimeline in the left column. */
export function ProgressStrip(p: Pick<ProgressProps, "line" | "tone" | "working">) {
  return (
    <div className={`stline ${p.tone}`}>
      <span>{p.line}</span>
      {p.working ? <span className="spin" aria-live="polite"><Loader2 className="ic" aria-hidden />Agent working…</span> : null}
    </div>
  );
}

/** The seven stages as a scrolling timeline: a dot per stage on a dashed line, each stage opening to its steps. */
export function StageTimeline(p: Omit<ProgressProps, "line" | "tone" | "working">) {
  const done = Math.max(0, LIFECYCLE.findIndex((x) => x.key === p.current));
  return (
    <aside className="tlcard" aria-label="Request progress">
      <div className="tlhead"><b>Progress</b><span>{done} of {LIFECYCLE.length} stages complete</span></div>
      <div className="tlbar" aria-hidden><i style={{ width: `${(done / LIFECYCLE.length) * 100}%` }} /></div>
      <div className="tlscroll">
        <LifecycleView timeline draftNote={p.draftNote} steps={p.steps} current={p.current} comments={p.comments} extras={p.extras} onAction={p.onAction} />
      </div>
    </aside>
  );
}

export const HEADER_CSS = `
/* Header card: back (Fitts: big, first target), then identity, then the signals that need attention (Von Restorff). */
.ro .rohd{position:relative;padding:16px 20px 16px 22px;margin-bottom:12px;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:0 1px 2px rgba(20,26,40,.04);overflow:hidden}
.ro .rohd::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--accent)}
.ro .rohd.risk::before{background:var(--warn)} .ro .rohd.late::before{background:var(--crit)}
.ro .crumb{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ink-3);margin-bottom:10px}
.ro .crumb .back{display:inline-flex;align-items:center;height:28px;padding:0 10px;margin-left:-6px;border-radius:8px;border:0;background:none;color:var(--accent);font:600 12.5px var(--sans);cursor:pointer;transition:background .12s}
.ro .crumb .back:hover{background:var(--accent-soft)}
.ro .crumb .refchip{font:600 11.5px var(--mono);padding:2px 8px;border-radius:6px;background:var(--surface-2);color:var(--ink-2)}
.ro .rohd h1{font-size:22px;font-weight:700;margin:0;letter-spacing:-.025em;line-height:1.25;overflow-wrap:anywhere;min-width:0}
.ro .subl{margin-top:6px;font-size:13px;color:var(--ink-2);overflow-wrap:anywhere}
.ro .pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.ro .rohd .pill{padding:3px 11px;font-size:12px}
.ro .lcwrap{min-width:0;overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-bottom:0;border-radius:14px 14px 0 0;padding:14px 12px 6px}
.ro .stline{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:0;padding:10px 18px;border-radius:0 0 14px 14px;border:1px solid var(--border);font-size:13px;font-weight:600;background:var(--accent-soft);color:var(--accent);overflow-wrap:anywhere}
.ro .stline.warn{background:var(--warn-soft);color:var(--warn)}
.ro .stline .spin{display:inline-flex;align-items:center;gap:6px;font-weight:500}
`;
