"use client";

// The approval card on the request page: what the approver is deciding on (stored facts only, each tagged
// with where it came from), and, for the assigned approver alone, Approve / Reject. Rendered inside `.ro`.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { approvalsApi, intakeApi, workflowsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import type { ApprovalSummary, ApprovalView, IntakeApprovalRung } from "@/lib/types";

// Kept for the header tooltip only; the view name is internal wording and is not shown.
const VIEW_LABEL: Record<ApprovalView, string> = { general: "General view", finance: "Finance view", legal: "Legal view" };

const CSS = `
.ro .apc .note{font-size:12px;color:var(--ink-3)}
.ro .apc .facts .f .v{white-space:pre-line}
.ro .apc .facts .f .v.na{color:var(--ink-3);font-weight:500}
.ro .apc .facts .f .v.q{font-style:italic}
.ro .apc .src{font:600 9px var(--sans);text-transform:uppercase;letter-spacing:.05em;margin-left:6px;color:var(--ink-3)}
.ro .apc .src.verified{color:var(--good)}
.ro .apc .ch .cnt{margin-left:auto;white-space:nowrap}
.ro .apc .hd{display:flex;flex-direction:column;gap:2px;min-width:0}
.ro .apc .hd .sub{font-size:12px;color:var(--ink-3);font-weight:500}
.ro .apc .facts .f .v.clamp{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.ro .apc .more{display:block;margin-top:4px;padding:0;border:0;background:none;color:var(--brand);font:600 11.5px var(--sans);cursor:pointer}
.ro .apc .more:focus-visible{outline:2px solid var(--brand);outline-offset:2px;border-radius:3px}
.ro .apc .decide{margin-top:14px}
.ro .apc .decide label.ack{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;margin-bottom:10px;color:var(--ink)}
.ro .apc .decide textarea{width:100%;margin-bottom:10px;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans);resize:vertical;outline:none}
.ro .apc .err{font-size:12.5px;color:var(--crit);margin-bottom:8px}
`;

// A fact that spans the row is clamped to 3 lines; past this many characters it can overflow, so offer the toggle
// (a character count, not a measurement: it must work without layout).
const TOGGLE_AT = 180;

function Fact({ f }: { f: ApprovalSummary["fields"][number] }) {
  const [open, setOpen] = useState(false);
  const wide = f.quote || (f.value != null && (f.value.length > 48 || f.value.includes("\n")));
  const long = f.value != null && f.value.length > TOGGLE_AT;
  return (
    <div className="f" style={wide ? { gridColumn: "1 / -1" } : undefined}><div className="k">{f.label}</div>
      {f.value == null ? <div className="v na">Not available</div>
        : <div className={`v${f.quote ? " q" : ""}${long && !open ? " clamp" : ""}`}>{f.quote ? `“${f.value}”` : f.value}{f.source ? <span className={`src ${f.source}`}>{f.source}</span> : null}</div>}
      {long ? <button type="button" className="more" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Show less" : "Show more"}</button> : null}
    </div>
  );
}

export function ApprovalCard({ requestId, rungs, runId, stepName, subject }: { requestId: string; rungs: IntakeApprovalRung[]; runId: string | null; stepName?: string; subject?: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [comment, setComment] = useState("");
  const [ack, setAck] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = rungs.find((r) => r.can_decide) ?? rungs.find((r) => r.status === "pending" && r.approval_request_id);
  const approvalId = active?.approval_request_id ?? null;
  const { data: summary } = useQuery({
    queryKey: ["intake-approval-summary", requestId, approvalId],
    queryFn: () => intakeApi.approvalSummary(requestId, approvalId!),
    enabled: !!approvalId,
  });

  const gate = summary ? summary.open_risk.critical + summary.open_risk.high : 0;
  const decide = useMutation({
    mutationFn: (d: "approve" | "reject") =>
      approvalsApi.decide(approvalId!, d, comment.trim() || undefined, d === "approve" && gate > 0 ? ack : false),
    onSuccess: (_res, d) => {
      notify(d === "approve" ? "Approved" : "Rejected", "success");
      setComment(""); setAck(false); setError(null);
      for (const k of [["intake-approval-chain", requestId], ["intake-request", requestId], ["flow-run", requestId], ["approvals"], ["contract-risk"], ["intake-approval-summary", requestId]]) {
        qc.invalidateQueries({ queryKey: k });
      }
      // The no-contract path advances only on a refresh; a refused refresh is fine, the 2-minute beat covers it.
      if (runId) workflowsApi.refreshRun(runId).then((run) => qc.setQueryData(["flow-run", requestId], run)).catch(() => qc.invalidateQueries({ queryKey: ["flow-run", requestId] }));
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : "Decision failed";
      setError(msg); notify(msg, "error");
      qc.invalidateQueries({ queryKey: ["intake-approval-chain", requestId] });
    },
  });

  if (!active) return null;
  const reject = () => {
    if (!comment.trim()) { setError("Rejection requires a comment"); return; }
    setError(null); decide.mutate("reject");
  };

  return (
    <div className="card apc"><style>{CSS}</style>
      <div className="ch" title={summary ? VIEW_LABEL[summary.profile] : undefined}>
        <div className="hd"><h2>{stepName || "Approval"}</h2>
          {rungs.length > 1 ? <span className="sub">Approval step {active.step_order} of {rungs.length}</span> : null}
          {subject ? <span className="sub">{subject}</span> : null}
        </div>
        <span className="cnt">{active.can_decide ? "Waiting for you" : `Waiting on ${active.approver_label}`}</span>
      </div>
      <div className="cb">
        <div className="facts" style={{ marginTop: 0 }}>
          {(summary?.fields ?? []).map((f) => <Fact f={f} key={f.key} />)}
        </div>
        {!active.can_decide ? null : (
          <div className={`decide${gate > 0 ? " warn" : " ok"}`}>
            {gate > 0
              ? <div className="dw">{summary!.open_risk.critical} critical + {summary!.open_risk.high} high risk{gate === 1 ? "" : "s"} open</div>
              : <div className="dw ok">No critical or high risks open</div>}
            {gate > 0 ? <label className="ack"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />I have seen the open critical and high risks</label> : null}
            <textarea rows={3} maxLength={2000} aria-label="Comment (required to reject)" placeholder="Comment (required to reject)" value={comment} onChange={(e) => setComment(e.target.value)} />
            {error ? <div className="err" role="alert">{error}</div> : null}
            <div className="db">
              <button className="btn pri wide" disabled={decide.isPending || (gate > 0 && !ack)} onClick={() => { setError(null); decide.mutate("approve"); }}>Approve</button>
              <button className="btn wide" disabled={decide.isPending} onClick={reject}>Reject</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
