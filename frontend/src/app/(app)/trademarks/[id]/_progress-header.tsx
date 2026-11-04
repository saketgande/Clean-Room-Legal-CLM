"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Minus, X } from "lucide-react";
import { Badge, Card, CardBody } from "@/components/ui";
import { workflowsApi } from "@/lib/endpoints";
import { cn, titleCase } from "@/lib/utils";
import type { TrademarkCase, WorkflowRunStep } from "@/lib/types";

type Tone = "done" | "live" | "skipped" | "failed" | "planned";

const DONE = new Set(["done", "complete"]);
const OPEN = new Set(["running", "waiting", "waiting_human", "waiting_job"]);

function toneOf(step: WorkflowRunStep): Tone {
  if (DONE.has(step.status)) return "done";
  if (OPEN.has(step.status)) return "live";
  if (step.status === "skipped") return "skipped";
  if (step.status === "failed") return "failed";
  return "planned";
}

const CAPTION: Record<Tone, string> = { done: "Done", live: "In progress", skipped: "Skipped", failed: "Failed", planned: "Not started" };

/** Who has the step: the person, else the team, else the team the workflow names for it. */
function ownerOf(step: WorkflowRunStep): string | null {
  return step.assignee_label || step.team_label || step.planned_team_label || null;
}

/**
 * The case's progress on every tab: every workflow step in order, each with its number, its name and a tick
 * once it is done. The step the case is on is highlighted and names who has it. Always shown above the tabs,
 * so it stays put while you move between them.
 */
export function ProgressHeader({ trademarkCase }: { trademarkCase: TrademarkCase }) {
  const { data: run } = useQuery({
    queryKey: ["flow-run", trademarkCase.request_id],
    queryFn: () => workflowsApi.runForRequest(trademarkCase.request_id),
    refetchInterval: 15_000,
  });
  const steps = run?.steps ?? [];
  const finished = (run?.status ?? trademarkCase.run_status) === "complete";
  const total = steps.length || trademarkCase.step_total || 0;
  const doneCount = steps.filter((step) => toneOf(step) === "done").length;
  const current = finished ? null : steps.find((step) => step.idx === run?.current_index) ?? null;

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[13px] font-semibold text-slate-900">{run?.flow_name ?? trademarkCase.flow_name ?? "No workflow started"}</p>
            {finished ? (
              <Badge tone="green">Complete</Badge>
            ) : current ? (
              <Badge tone="amber">{`Step ${steps.indexOf(current) + 1} of ${total}`}</Badge>
            ) : trademarkCase.step_status ? (
              <Badge tone="slate">{titleCase(trademarkCase.step_status)}</Badge>
            ) : null}
          </div>
          {total > 0 && (
            <p className="text-xs text-slate-500">
              {doneCount} of {total} steps done
              {current && ownerOf(current) ? ` · with ${ownerOf(current)}` : ""}
            </p>
          )}
        </div>

        {steps.length > 0 && (
          <ol
            className="grid gap-1 overflow-x-auto pb-1"
            style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(104px, 1fr))` }}
            aria-label="Case progress"
          >
            {steps.map((step, position) => {
              const tone = toneOf(step);
              const owner = ownerOf(step);
              const caption = tone === "live" ? (owner ?? CAPTION.live) : CAPTION[tone];
              return (
                <li
                  key={step.idx}
                  className="relative flex flex-col items-center gap-1 px-1 text-center"
                  aria-current={tone === "live" ? "step" : undefined}
                  aria-label={`Step ${position + 1} of ${steps.length}, ${step.name}: ${CAPTION[tone].toLowerCase()}`}
                >
                  {position > 0 && (
                    <span
                      aria-hidden
                      className={cn(
                        "absolute right-1/2 top-[15px] h-0.5 w-full",
                        tone === "planned" ? "bg-slate-200" : "bg-brand-600",
                      )}
                    />
                  )}
                  <span
                    className={cn(
                      "relative z-10 grid h-[30px] w-[30px] place-items-center rounded-full border-2 text-xs font-semibold",
                      tone === "done" && "border-brand-600 bg-brand-600 text-white",
                      tone === "live" && "border-brand-600 bg-brand-50 text-brand-700 ring-4 ring-brand-100",
                      tone === "skipped" && "border-slate-300 bg-slate-200 text-slate-500",
                      tone === "failed" && "border-danger bg-danger text-white",
                      tone === "planned" && "border-slate-300 bg-slate-50 text-slate-400",
                    )}
                  >
                    {tone === "done" ? (
                      <Check className="h-4 w-4" strokeWidth={3} />
                    ) : tone === "skipped" ? (
                      <Minus className="h-4 w-4" />
                    ) : tone === "failed" ? (
                      <X className="h-4 w-4" />
                    ) : (
                      position + 1
                    )}
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-slate-400">Step {position + 1}</span>
                  <span
                    className={cn(
                      "line-clamp-2 text-[12px] font-semibold leading-tight",
                      tone === "planned" || tone === "skipped" ? "text-slate-400" : "text-slate-900",
                    )}
                    title={step.name}
                  >
                    {step.name}
                  </span>
                  <span className="line-clamp-1 max-w-full text-[11px] text-slate-500" title={caption}>
                    {caption}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </CardBody>
    </Card>
  );
}
