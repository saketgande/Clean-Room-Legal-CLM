"use client";

import { Loader2 } from "lucide-react";
import { LifecycleView } from "@/components/lifecycle-view";
import { fmtDate } from "@/lib/utils";
import type { LifecycleRow, LifecycleStage, WorkflowRunComment, WorkflowRunStep } from "@/lib/types";

export function RequestHeader(p: {
  title: string;
  refCode: string;
  requesterName: string | null;
  raisedAt: string | null;
  ownerLabel: string | null;
  highRisk: boolean;
  sla: string | null;
  onBack: () => void;
}) {
  const overdue = p.sla === "overdue";
  const atRisk = p.sla === "at_risk";
  return (
    <div className="rohd">
      <div className="crumb"><a onClick={p.onBack} style={{ cursor: "pointer", color: "var(--accent)" }}>← Legal Intake</a><span>/</span><span>{p.refCode}</span></div>
      <h1>{p.title}</h1>
      <div className="subl">
        {p.refCode} · raised by {p.requesterName ?? "the requester"}{p.raisedAt ? ` on ${fmtDate(p.raisedAt)}` : ""} · owner {p.ownerLabel ?? "unassigned"}
      </div>
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

export function ProgressStrip(p: {
  steps: WorkflowRunStep[];
  current: LifecycleStage;
  comments: WorkflowRunComment[];
  extras: Partial<Record<LifecycleStage, LifecycleRow[]>>;
  line: string;
  tone: "info" | "warn";
  working: boolean;
}) {
  return (
    <>
      <div className="lcwrap"><LifecycleView compact steps={p.steps} current={p.current} comments={p.comments} extras={p.extras} /></div>
      <div className={`stline ${p.tone}`}>
        <span>{p.line}</span>
        {p.working ? <span className="spin" aria-live="polite"><Loader2 className="ic" aria-hidden />Agent working…</span> : null}
      </div>
    </>
  );
}

export const HEADER_CSS = `
.ro .rohd{padding:4px 0 10px;margin-bottom:4px}
.ro .crumb{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-3);margin-bottom:8px}
.ro .crumb a{text-decoration:none;font-weight:600}
.ro .rohd h1{font-size:17px;font-weight:660;margin:0;letter-spacing:-.015em;overflow-wrap:anywhere;min-width:0}
.ro .subl{margin-top:4px;font-size:12.5px;color:var(--ink-2);overflow-wrap:anywhere}
.ro .pills{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.ro .lcwrap{min-width:0;overflow-x:auto}
.ro .stline{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:8px;padding:7px 12px;border-radius:9px;font-size:12.5px;font-weight:600;background:var(--accent-soft);color:var(--accent);overflow-wrap:anywhere}
.ro .stline.warn{background:var(--warn-soft);color:var(--warn)}
.ro .stline .spin{display:inline-flex;align-items:center;gap:6px;font-weight:500}
`;
