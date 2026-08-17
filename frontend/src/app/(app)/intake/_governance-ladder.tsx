"use client";

// The governance-ladder stepper, shared so the intake ticket and the contract
// page render the SAME workflow (the WorkflowRun steps) — not two different
// lifecycles. Styling matches the ticket exactly (Now marker, dashed path-ahead,
// running-beat spin); ig-bob / ig-conn-ahead live in globals.css.

import { Fragment, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Bot, Check, FileText, PenLine, RotateCw, ShieldCheck, SkipForward, Users, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn, fmtRelative, titleCase } from "@/lib/utils";
import { workflowsApi } from "@/lib/endpoints";
import type { WorkflowRun, WorkflowRunStep } from "@/lib/types";

export const STEP_META: Record<string, { icon: LucideIcon; label: string; wait: string; running: string }> = {
  ai_task: { icon: Bot, label: "AI step", wait: "Needs your review", running: "Agent is working…" },
  human_task: { icon: Users, label: "Human task", wait: "Waiting on you", running: "In progress" },
  approval: { icon: ShieldCheck, label: "Approval", wait: "Awaiting sign-off", running: "Awaiting sign-off" },
  signature: { icon: PenLine, label: "Signature", wait: "Out for signature", running: "Out for signature" },
  clm_draft: { icon: FileText, label: "Draft", wait: "Awaiting document", running: "Drafting…" },
  counterparty: { icon: Users, label: "Counterparty", wait: "With counterparty", running: "With counterparty" },
  notify: { icon: Bell, label: "Notify", wait: "Notifying", running: "Notifying" },
};
export function stepMeta(type: string) { return STEP_META[type] ?? STEP_META.human_task; }

// `onSelect` makes steps clickable (the ticket uses it to drive its
// step-detail box); omit it for a read-only ladder. `orientation` picks the
// horizontal node row (ticket) or a vertical timeline with each step's detail
// caption directly beneath its name (contract page's Status tab — a narrow
// column reads far better as a timeline than a scrolling row).
export function GovernanceLadderSteps({
  steps, currentIndex, complete, selectedIdx = null, onSelect, orientation = "horizontal",
}: {
  steps: WorkflowRunStep[];
  currentIndex: number;
  complete: boolean;
  selectedIdx?: number | null;
  onSelect?: (idx: number) => void;
  orientation?: "horizontal" | "vertical";
}) {
  const items = steps.map((s, i) => {
    const m = stepMeta(s.type);
    const done = s.status === "done" || s.status === "complete";
    const skipped = s.status === "skipped";
    const failed = s.status === "failed" || s.status === "cancelled";
    const active = s.idx === currentIndex && !complete && !failed && !skipped;
    // "working" = a subsystem is actively running (ai_task mid-beat,
    // approval/signature in flight) → spin; "needsYou" = parked on the user.
    const working = s.status === "running" || s.status === "waiting_job";
    const needsYou = s.status === "waiting_human";
    const StepIcon = working ? RotateCw : m.icon;
    const isSel = !!onSelect && s.idx === selectedIdx;
    const caption = done ? "Done" : failed ? "Failed" : skipped ? "Skipped" : active ? (needsYou ? m.wait : m.running) : m.label;
    return { s, i, done, skipped, failed, active, working, needsYou, StepIcon, isSel, caption };
  });

  if (orientation === "vertical") {
    return (
      <div className="flex flex-col">
        {items.map(({ s, i, done, skipped, failed, active, working, needsYou, StepIcon, isSel, caption }) => (
          <button
            key={s.idx}
            type="button"
            onClick={onSelect ? () => onSelect(s.idx) : undefined}
            className={cn(
              "flex items-stretch gap-3 rounded-lg px-1.5 py-1 text-left transition",
              onSelect && "hover:bg-slate-50",
              isSel && "bg-brand-50",
            )}
          >
            <span className="flex flex-col items-center">
              <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-full border-2 transition",
                done ? "border-success bg-success-subtle text-success"
                  : failed ? "border-danger bg-danger-subtle text-danger"
                    : skipped ? "border-slate-300 bg-slate-100 text-slate-400"
                      : active ? cn("border-brand-500 bg-brand-50 text-brand-700", (working || needsYou) && "animate-pulse")
                        : "border-slate-300 bg-slate-100 text-slate-400")}>
                {done ? <Check className="h-3.5 w-3.5" /> : failed ? <X className="h-3.5 w-3.5" /> : skipped ? <SkipForward className="h-3 w-3" /> : <StepIcon className={cn("h-3.5 w-3.5", working && "animate-spin")} />}
              </span>
              {i < steps.length - 1 && (
                <span className={cn("my-0.5 w-0.5 flex-1 rounded", done ? "bg-success" : skipped ? "bg-slate-300" : "ig-conn-ahead")} style={{ minHeight: 20 }} />
              )}
            </span>
            <span className="min-w-0 flex-1 pb-3 pt-1">
              <span className="flex flex-wrap items-center gap-1.5">
                <span className={cn("text-[13px] font-semibold leading-tight", active ? "text-slate-900" : failed ? "text-danger" : (done || skipped) ? "text-slate-600" : "text-slate-400")}>{s.name}</span>
                {active && (
                  <span className="ig-bob rounded-full bg-brand-600 px-1.5 py-0.5 font-mono text-[8px] font-semibold uppercase tracking-[0.12em] text-white">Now</span>
                )}
              </span>
              {/* The workflow detail for this stage, right below its name — a
                  relative "Xh ago" (from the step's last status change) turns
                  this into a stage log, not just a live tracker. */}
              <span className="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 font-mono text-[10px] uppercase tracking-wide">
                <span className={cn(done ? "text-success" : failed ? "text-danger" : skipped ? "text-slate-400" : active ? (needsYou ? "text-warning" : "text-brand-600") : "text-slate-400")}>
                  {caption}
                </span>
                {(done || failed || skipped) && s.updated_at && (
                  <span className="text-slate-400">· {fmtRelative(s.updated_at)}</span>
                )}
              </span>
              {/* Why it's waiting on you — most relevant for an escalated ai_task
                  (a failed agent call, or a low-confidence result), so it doesn't
                  read as an unexplained stall. */}
              {active && needsYou && s.note && (
                <span className="mt-0.5 block text-[11px] normal-case tracking-normal text-slate-500">{s.note}</span>
              )}
            </span>
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="flex items-start overflow-x-auto pb-1 pt-5">
      {items.map(({ s, i, done, skipped, failed, active, working, needsYou, StepIcon, isSel, caption }) => (
        <Fragment key={s.idx}>
          <button
            type="button"
            onClick={onSelect ? () => onSelect(s.idx) : undefined}
            className={cn(
              "relative flex min-w-[96px] max-w-[132px] shrink-0 flex-col items-center gap-2 rounded-xl px-1 py-1 text-center transition",
              onSelect && "hover:bg-slate-50",
              isSel && "bg-brand-50",
            )}
          >
            {active && (
              <span className="ig-bob absolute -top-4 left-1/2 -translate-x-1/2 rounded-full bg-brand-600 px-2 py-0.5 font-mono text-[8px] font-semibold uppercase tracking-[0.12em] text-white">Now</span>
            )}
            <span className={cn("grid h-10 w-10 place-items-center rounded-full border-2 transition",
              done ? "border-success bg-success-subtle text-success"
                : failed ? "border-danger bg-danger-subtle text-danger"
                  : skipped ? "border-slate-300 bg-slate-100 text-slate-400"
                    : active ? cn("border-brand-500 bg-brand-50 text-brand-700", (working || needsYou) && "animate-pulse")
                      : "border-slate-300 bg-slate-100 text-slate-400")}>
              {done ? <Check className="h-4 w-4" /> : failed ? <X className="h-4 w-4" /> : skipped ? <SkipForward className="h-3.5 w-3.5" /> : <StepIcon className={cn("h-4 w-4", working && "animate-spin")} />}
            </span>
            <span className={cn("text-[11px] font-medium leading-tight", active ? "text-slate-900" : failed ? "text-danger" : (done || skipped) ? "text-slate-600" : "text-slate-400")}>{s.name}</span>
            <span className={cn("font-mono text-[8px] uppercase tracking-wide",
              done ? "text-success" : failed ? "text-danger" : skipped ? "text-slate-400" : active ? (needsYou ? "text-warning" : "text-brand-600") : "text-slate-400")}>
              {caption}
            </span>
          </button>
          {i < steps.length - 1 && <span className={cn("mt-5 h-0.5 min-w-[14px] flex-1 rounded", done ? "bg-success" : skipped ? "bg-slate-300" : "ig-conn-ahead")} />}
        </Fragment>
      ))}
    </div>
  );
}

// A self-contained bar for the contract page: fetches the governance ladder that
// drove this contract and renders it above the 7-stage CLM lifecycle. Renders
// nothing if the contract wasn't produced by a workflow.
export function ContractGovernanceLadder({ contractId }: { contractId: string }) {
  const qc = useQueryClient();
  const { data: run } = useQuery({
    queryKey: ["flow-run-contract", contractId],
    queryFn: () => workflowsApi.runForContract(contractId),
    // Same live-poll as the intake ticket's ladder: keep checking while
    // something is actually working (a mid-beat ai_task or a subsystem job in
    // flight) so the animation resolves instead of freezing on the last poll.
    refetchInterval: (q) => {
      const r = q.state.data as WorkflowRun | null | undefined;
      if (!r) return false;
      const working = (r.steps ?? []).some((s) => s.status === "running" || s.status === "waiting_job");
      return r.status === "running" || working ? 1500 : false;
    },
  });

  // Pump a mid-beat step: `ai_task` marks itself "running" and yields so the
  // "Agent is working…" beat is visible, then only actually runs the agent on
  // the NEXT call to /refresh — nothing else triggers that call. The intake
  // ticket page pumps this itself; viewed only from here (the contract page),
  // a run would otherwise sit "running" forever with a spinner that never
  // resolves. Mirrors that same pump, guarded to fire once per step and
  // deliberately not cancelled on unmount — navigating away mid-beat should
  // still let the step complete server-side.
  const pumpedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const key = `${run.id}:${run.current_index}`;
    if (pumpedRef.current === key) return;
    pumpedRef.current = key;
    const runId = run.id;
    setTimeout(() => {
      workflowsApi.refreshRun(runId).finally(() =>
        qc.invalidateQueries({ queryKey: ["flow-run-contract", contractId] }),
      );
    }, 1000);
  }, [run, qc, contractId]);

  if (!run || !run.steps || run.steps.length === 0) return null;
  const steps = run.steps;
  const doneCount = steps.filter((s) => ["done", "complete", "skipped"].includes(s.status)).length;
  const pct = steps.length ? Math.round((doneCount / steps.length) * 100) : 0;
  return (
    <div className="shrink-0 border-b border-slate-200 bg-slate-50 px-4 py-3">
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
          Governance Ladder · {run.flow_name}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-slate-400">
          Step {Math.min(run.current_index + 1, steps.length)} of {steps.length} ·{" "}
          <span className={run.status === "complete" ? "text-success" : "text-slate-500"}>{titleCase(run.status)}</span>
        </span>
      </div>
      <div className="mb-1 h-1.5 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-brand-600 transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
      <GovernanceLadderSteps steps={steps} currentIndex={run.current_index} complete={run.status === "complete"} orientation="vertical" />
      {(run.brief ?? []).length > 0 && (
        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3">
          <div className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            Agent hand-off trace
          </div>
          <ol className="space-y-2">
            {(run.brief ?? []).map((b, i) => (
              <li key={i} className="flex gap-2.5 text-[13px]">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
                <div className="min-w-0">
                  <span className="font-medium text-slate-700">{titleCase(b.agent || b.type)}</span>
                  <span className="text-slate-400"> · {b.step}</span>
                  <div className="text-slate-600">{b.summary}</div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
