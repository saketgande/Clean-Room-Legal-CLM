"use client";

import { Badge, Card, CardBody } from "@/components/ui";
import { titleCase } from "@/lib/utils";
import type { TrademarkCase } from "@/lib/types";

/** Where the clearance case stands: current workflow step, its owner, and how far along the flow is. */
export function StageTracker({ trademarkCase }: { trademarkCase: TrademarkCase }) {
  const { step_index, step_total, step_name, step_status, assignee_name, run_status, flow_name } = trademarkCase;
  const finished = run_status === "complete";
  const total = step_total ?? 0;
  const done = finished ? total : Math.min(step_index ?? 0, total);
  const percent = total ? Math.round((done / total) * 100) : 0;

  return (
    <Card>
      <CardBody className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <p className="text-[13px] font-semibold text-slate-900">{step_name ?? "Not started"}</p>
            {finished ? (
              <Badge tone="green">Complete</Badge>
            ) : step_status ? (
              <Badge tone={step_status === "waiting_human" ? "amber" : "slate"}>{titleCase(step_status)}</Badge>
            ) : null}
          </div>
          <p className="text-xs text-slate-500">
            {flow_name ?? "No workflow started"}
            {total > 0 && ` · ${finished ? total : (step_index ?? 0) + 1} of ${total}`}
            {!finished && assignee_name && ` · with ${assignee_name}`}
          </p>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200" aria-hidden>
          <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${percent}%` }} />
        </div>
      </CardBody>
    </Card>
  );
}
