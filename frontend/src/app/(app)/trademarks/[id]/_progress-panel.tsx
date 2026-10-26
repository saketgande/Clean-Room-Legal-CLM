"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Circle, CircleDot, Undo2 } from "lucide-react";
import { Badge, Button, Card, CardBody, CenterSpinner, EmptyState, ErrorState, Textarea } from "@/components/ui";
import { workflowsApi } from "@/lib/endpoints";
import { fmtDateTime, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { WorkflowRun, WorkflowRunStep } from "@/lib/types";

function stepTone(step: WorkflowRunStep): "green" | "amber" | "slate" | "red" {
  if (step.status === "done" || step.status === "complete") return "green";
  if (step.status === "failed" || step.status === "cancelled") return "red";
  if (step.status === "running" || step.status.startsWith("waiting")) return "amber";
  return "slate";
}

/** The clearance workflow's steps for the case, with the one action the current step needs. */
export function ProgressPanel({ requestId, trademarkId }: { requestId: string; trademarkId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState("");
  const [sendBackNote, setSendBackNote] = useState("");
  const [sendingBack, setSendingBack] = useState(false);
  const queryKey = ["flow-run", requestId];
  const { data: run, isLoading, error } = useQuery({
    queryKey,
    queryFn: () => workflowsApi.runForRequest(requestId),
  });

  if (isLoading) return <CenterSpinner label="Loading workflow…" />;
  if (error) return <ErrorState error={error} />;
  if (!run) {
    return (
      <EmptyState
        title="No workflow running"
        description="Start the clearance workflow from the case's Legal Intake request."
      />
    );
  }

  const active = run.status === "running" || run.status === "waiting";
  const current = run.steps.find((step) => step.idx === run.current_index);
  const owed = run.rework ?? null;

  async function act(action: () => Promise<WorkflowRun>, success: string) {
    setBusy(true);
    try {
      const next = await action();
      qc.setQueryData(queryKey, next);
      qc.invalidateQueries({ queryKey: ["trademark", trademarkId] });
      qc.invalidateQueries({ queryKey: ["trademarks"] });
      notify(success, "success");
      setReply("");
      setSendBackNote("");
      setSendingBack(false);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardBody className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[13px] font-semibold text-slate-900">{run.flow_name}</p>
          <Badge tone={run.status === "complete" ? "green" : run.status === "failed" ? "red" : "amber"}>
            {titleCase(run.status)}
          </Badge>
        </div>

        <ol className="space-y-3">
          {run.steps.map((step) => {
            const isCurrent = active && step.idx === run.current_index;
            const done = step.status === "done" || step.status === "complete";
            const Icon = done ? Check : isCurrent ? CircleDot : Circle;
            const owner = step.assignee_label ?? step.team_label ?? step.planned_team_label;
            return (
              <li key={step.idx} className="flex items-start gap-3 text-[13px]">
                <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${done ? "text-success" : isCurrent ? "text-brand-600" : "text-slate-400"}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className={isCurrent ? "font-semibold text-slate-900" : "text-slate-800"}>{step.name}</p>
                    <Badge tone={stepTone(step)}>{titleCase(step.status)}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500">
                    {owner ? `With ${owner}` : "Unassigned"}
                    {step.updated_at && done ? ` · ${fmtDateTime(step.updated_at)}` : ""}
                  </p>
                  {step.note && <p className="mt-0.5 text-xs text-slate-600">{step.note}</p>}
                </div>
              </li>
            );
          })}
        </ol>

        {active && current && (
          <div className="space-y-3 border-t border-slate-200 pt-4">
            {owed && (
              <div className="space-y-1.5">
                <p className="text-xs text-slate-600">{owed.reason}</p>
                <Textarea
                  rows={2}
                  placeholder="Reply before continuing"
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                />
              </div>
            )}
            {sendingBack && (
              <Textarea
                rows={2}
                placeholder="What needs to be reworked? (optional)"
                value={sendBackNote}
                onChange={(e) => setSendBackNote(e.target.value)}
              />
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                loading={busy}
                disabled={!!owed && !reply.trim()}
                onClick={() =>
                  act(
                    () =>
                      owed
                        ? workflowsApi.completeStep(run.id, undefined, current.idx, undefined, reply.trim())
                        : workflowsApi.completeStep(run.id, undefined, current.idx),
                    "Step completed",
                  )
                }
              >
                <Check className="h-4 w-4" />
                Mark done
              </Button>
              {run.current_index > 0 &&
                (sendingBack ? (
                  <>
                    <Button
                      variant="outline"
                      loading={busy}
                      onClick={() =>
                        act(
                          () => workflowsApi.returnStep(run.id, undefined, sendBackNote.trim() || undefined),
                          "Sent back for rework",
                        )
                      }
                    >
                      <Undo2 className="h-4 w-4" />
                      Confirm send back
                    </Button>
                    <Button variant="ghost" onClick={() => setSendingBack(false)}>
                      Cancel
                    </Button>
                  </>
                ) : (
                  <Button variant="outline" onClick={() => setSendingBack(true)}>
                    <Undo2 className="h-4 w-4" />
                    Send back
                  </Button>
                ))}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
