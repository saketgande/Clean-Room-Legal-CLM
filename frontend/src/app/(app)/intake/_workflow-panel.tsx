"use client";

// Per-ticket workflow-run panel — the primary "what's happening" view on a
// ticket. Renders the running WorkflowRun as a connected vertical stepper with
// per-step icons, results, and the one action the current step needs.

import { Fragment, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Workflow,
  FileText,
  Bot,
  Users,
  ShieldCheck,
  PenLine,
  Bell,
  Check,
  ArrowRight,
  Sparkles,
  RotateCw,
  GitBranch,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Button, Card, CardBody, Select } from "@/components/ui";
import { workflowsApi, intakeApi } from "@/lib/endpoints";
import { titleCase, cn } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { WorkflowRunStep, WorkflowSuggestion } from "@/lib/types";

const STEP_ICON: Record<string, LucideIcon> = {
  clm_draft: FileText,
  ai_task: Bot,
  human_task: Users,
  approval: ShieldCheck,
  signature: PenLine,
  counterparty: Users,
  notify: Bell,
};

function stepTone(status: string): "green" | "amber" | "red" | "slate" {
  if (status === "done" || status === "complete") return "green";
  if (status === "failed" || status === "cancelled") return "red";
  if (status === "running" || status.startsWith("waiting")) return "amber";
  return "slate";
}

// A one-line "what this step produced" — so the ladder shows results, not just status.
function stepResult(s: WorkflowRunStep): string | null {
  const r = (s.result ?? {}) as Record<string, unknown>;
  if (typeof r.confidence === "number") {
    const cat = r.category ? ` · ${String(r.category)}` : "";
    return `${Math.round((r.confidence as number) * 100)}% confidence${cat}`;
  }
  if (r.contract_id) return "Contract drafted";
  if (s.note) return s.note;
  return null;
}

export function WorkflowPanel({ requestId, suggestion }: { requestId: string; suggestion?: WorkflowSuggestion | null }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);

  const { data: run, isLoading } = useQuery({
    queryKey: ["flow-run", requestId],
    queryFn: () => workflowsApi.runForRequest(requestId),
  });
  const { data: flows } = useQuery({ queryKey: ["flows-list"], queryFn: workflowsApi.listFlows });

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["flow-run", requestId] });
    qc.invalidateQueries({ queryKey: ["intake-req", requestId] });
  }

  async function act(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try {
      await fn();
      invalidate();
      notify(msg, "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return null;

  if (!run) {
    const sug = suggestion ?? null;
    const hasSug = !!sug && !!sug.flow_id;
    const conf = sug ? Math.round(sug.confidence * 100) : 0;
    const tone: "green" | "amber" | "slate" =
      !sug ? "slate" : sug.needs_human ? "amber" : conf >= 75 ? "green" : "amber";
    return (
      <Card>
        <CardBody className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5">
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-50 text-brand-600">
                <Sparkles className="h-4 w-4" />
              </span>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-brand-600">Workflow router · suggestion</p>
                <p className="text-xs text-slate-500">No workflow running yet.</p>
              </div>
            </div>
            <button onClick={() => act(() => intakeApi.suggestFlow(requestId), "Re-suggested")}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-500 hover:text-slate-800">
              <RotateCw className="h-3 w-3" /> Re-suggest
            </button>
          </div>

          {hasSug ? (
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-slate-900">{sug!.flow_name}</p>
                <Badge tone={tone}>{conf}% {sug!.needs_human ? "· review" : "confident"}</Badge>
              </div>
              <p className="mt-1 text-[12.5px] leading-relaxed text-slate-600">{sug!.reasoning}</p>
              {sug!.alternatives.length > 0 && (
                <p className="mt-1.5 text-[11px] text-slate-400">Also considered: {sug!.alternatives.map((a) => a.flow_name).filter(Boolean).join(", ")}</p>
              )}
            </div>
          ) : (
            <div className="rounded-lg border border-warning/40 bg-warning-subtle/30 p-3 text-[12.5px] leading-relaxed text-slate-600">
              {sug?.reasoning ?? "No suggestion yet — pick a workflow to route this request, or re-suggest."}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {hasSug && (
              <Button size="sm" loading={busy}
                onClick={() => act(() => workflowsApi.startFlow(requestId, sug!.flow_id!), `Assigned ${sug!.flow_name}`)}>
                <Check className="h-4 w-4" /> Assign this flow
              </Button>
            )}
            <Select value="" onChange={(e) => { const v = e.target.value; if (v) act(() => workflowsApi.startFlow(requestId, v), "Workflow started"); }}
              className="h-8 w-52 text-[13px]">
              <option value="">{hasSug ? "Choose another…" : "Choose a workflow…"}</option>
              {(flows ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </Select>
          </div>
        </CardBody>
      </Card>
    );
  }

  const steps = run.steps;
  const doneCount = steps.filter((s) => s.status === "done" || s.status === "complete").length;
  const current: WorkflowRunStep | undefined = steps[run.current_index];
  const pct = steps.length ? Math.round((doneCount / steps.length) * 100) : 0;
  // The current parallel group: current_index plus following parallel-flagged
  // steps. Every step in it is in flight at once, so they all read as active.
  const groupEnd = (() => {
    let e = run.current_index + 1;
    while (e < steps.length && steps[e]?.parallel) e++;
    return e;
  })();
  const inGroup = (i: number) => i >= run.current_index && i < groupEnd;
  const parallelActive = !["complete", "failed", "cancelled"].includes(run.status) && groupEnd - run.current_index > 1;

  return (
    <Card className="overflow-hidden">
      {/* header band */}
      <div className="border-b border-slate-100 bg-slate-50/60 px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand-100 text-brand-700">
              <Workflow className="h-4 w-4" />
            </span>
            <div>
              <p className="text-[10px] font-mono uppercase tracking-[0.12em] text-slate-400">Workflow</p>
              <h3 className="text-sm font-semibold text-slate-900">{run.flow_name}</h3>
            </div>
          </div>
          <Badge tone={stepTone(run.status)}>{titleCase(run.status)}</Badge>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
            <div className="h-full rounded-full bg-brand-500 transition-all duration-500" style={{ width: `${pct}%` }} />
          </div>
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-slate-500">
            {doneCount}/{steps.length} done
          </span>
        </div>
      </div>

      <CardBody className="space-y-4">
        <ol>
          {steps.map((s, i) => {
            const active = inGroup(s.idx);
            const done = s.status === "done" || s.status === "complete";
            const Icon = STEP_ICON[s.type] ?? Workflow;
            const result = stepResult(s);
            const last = i === steps.length - 1;
            return (
              <Fragment key={s.idx}>
              {parallelActive && s.idx === run.current_index && (
                <li className="mb-2 flex items-center gap-1.5 pl-12 text-[11px] font-semibold uppercase tracking-wide text-brand-600">
                  <GitBranch className="h-3.5 w-3.5" />
                  Parallel · all must respond
                </li>
              )}
              <li className={cn("relative flex gap-3", !last && "pb-4")}>
                {/* connector rail */}
                {!last && (
                  <span
                    className={cn("absolute left-[17px] top-9 bottom-0 w-px", done ? "bg-brand-300" : "bg-slate-200")}
                  />
                )}
                {/* node */}
                <span
                  className={cn(
                    "relative z-10 grid h-9 w-9 shrink-0 place-items-center rounded-full border transition-colors",
                    done
                      ? "border-brand-600 bg-brand-600 text-white"
                      : active
                        ? "border-brand-400 bg-brand-50 text-brand-700 ring-4 ring-brand-100"
                        : "border-slate-300 bg-slate-100 text-slate-400",
                  )}
                >
                  {done ? <Check className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
                </span>
                {/* body */}
                <div
                  className={cn(
                    "min-w-0 flex-1 rounded-lg border px-3 py-2 transition-colors",
                    active ? "border-brand-200 bg-brand-50/60" : "border-transparent",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={cn("truncate text-sm font-medium", active ? "text-brand-900" : "text-slate-800")}>
                      {s.name}
                      {s.parallel && <span className="ml-1.5 rounded bg-brand-50 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-brand-600">∥ parallel</span>}
                    </span>
                    <Badge tone={stepTone(s.status)}>{titleCase(s.status.replace(/_/g, " "))}</Badge>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-slate-500">
                    {titleCase(s.type.replace(/_/g, " "))}
                    {result && <span className="text-slate-600"> · {result}</span>}
                  </p>
                  {s.status === "waiting_human" && (
                    <button
                      onClick={() => act(() => workflowsApi.completeStep(run.id, undefined, s.idx), "Step completed")}
                      disabled={busy}
                      className="mt-2 inline-flex items-center gap-1 rounded-md bg-brand-600 px-2.5 py-1 text-xs font-semibold text-white transition-colors hover:bg-brand-700 disabled:opacity-50"
                    >
                      <Check className="h-3 w-3" />
                      Mark done
                    </button>
                  )}
                </div>
              </li>
              </Fragment>
            );
          })}
        </ol>

        {/* actions for the current step */}
        {(run.contract_id ||
          (current && (current.type === "approval" || current.type === "signature"))) && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            {current && (current.type === "approval" || current.type === "signature") && (
              <Button
                size="sm"
                variant="outline"
                loading={busy}
                onClick={() => act(() => workflowsApi.refreshRun(run.id), "Refreshed")}
              >
                Check {current.type === "signature" ? "signature" : "approval"} status
              </Button>
            )}
            {run.contract_id && (
              <a
                href={`/contracts/${run.contract_id}`}
                className="ml-auto inline-flex items-center gap-1 text-sm font-semibold text-brand-700 hover:underline"
              >
                Open the drafted contract
                <ArrowRight className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        )}

        {run.error && <p className="text-xs text-danger">{run.error}</p>}
      </CardBody>
    </Card>
  );
}
