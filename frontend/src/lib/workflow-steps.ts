// Which step of a running workflow a person is looking at and can act on.
//
// Steps flagged `parallel` run side by side with the step above them ("all must respond"). The server's
// `current_index` stays on the FIRST step of such a group until every step in it is finished, so once the
// first reviewer is done, `steps[current_index]` is a finished step while the other reviewer is still being
// waited for. Screens that read only `steps[current_index]` then show the wrong step as "here", and offer
// nobody a button to finish the one that is actually waiting.

import type { WorkflowRun, WorkflowRunStep } from "./types";
import { RISK_UNKNOWN_LABEL } from "./utils";

const FINISHED = new Set(["done", "complete", "skipped", "failed"]);

type RunSteps = Pick<WorkflowRun, "current_index" | "steps">;

/** The steps that run together with the run's current step, in order (just that step when nothing is parallel). */
export function currentGroup(run: RunSteps | null | undefined): WorkflowRunStep[] {
  if (!run) return [];
  const steps = [...(run.steps ?? [])].sort((a, b) => a.idx - b.idx);
  const first = steps.find((s) => s.idx === run.current_index);
  if (!first) return [];
  const group = [first];
  for (const s of steps) {
    if (s.idx > first.idx && s.parallel && group[group.length - 1].idx === s.idx - 1) group.push(s);
  }
  return group;
}

/** Steps of the current group that are not finished yet. */
export const openSteps = (run: RunSteps | null | undefined): WorkflowRunStep[] =>
  currentGroup(run).filter((s) => !FINISHED.has(s.status));

/**
 * The step to show and act on: of the current group, one waiting for a person (the viewer's own first),
 * else any step still open, else the step the run is at.
 */
export function stepInFocus(run: RunSteps | null | undefined, userId?: string | null): WorkflowRunStep | undefined {
  const group = currentGroup(run);
  if (group.length === 0) return undefined;
  const open = group.filter((s) => !FINISHED.has(s.status));
  const waiting = open.filter((s) => s.status === "waiting_human");
  const mine = userId ? waiting.filter((s) => s.assignee_user_id === userId) : [];
  return mine[0] ?? waiting[0] ?? open[0] ?? group[0];
}

/**
 * The step that follows `step`: another step of its group that is still open, or, when it is the last one
 * open, the first step after the group. `null` at the end of the workflow.
 */
export function stepAfter(run: RunSteps | null | undefined, step: WorkflowRunStep): WorkflowRunStep | null {
  if (!run) return null;
  const group = currentGroup(run);
  const others = group.filter((s) => s.idx !== step.idx && !FINISHED.has(s.status));
  if (others.length > 0) return others[0];
  const lastIdx = group.length > 0 ? group[group.length - 1].idx : step.idx;
  return (run.steps ?? []).find((s) => s.idx === lastIdx + 1) ?? null;
}

const PLAIN_NAMES: Record<string, string> = {
  "Clm Draft": "Prepare draft", clm_draft: "Prepare draft",
  "Ai Task": "AI check", ai_task: "AI check",
  "Human Task": "Review", human_task: "Review",
  Counterparty: "Negotiation", counterparty: "Negotiation",
  Notify: "Notify people", notify: "Notify people",
  approval: "Approval", signature: "Signature",
};

/** A step name a requester understands. Unnamed steps are saved as the title-cased type ("Clm Draft") or the raw key. */
export function plainStepName(name: string | null | undefined, type?: string | null): string {
  const n = (name ?? "").trim();
  if (!n) return (type && PLAIN_NAMES[type]) || "Step";
  return PLAIN_NAMES[n] ?? n;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function driverText(d: unknown): string | null {
  if (typeof d === "string") return d.trim() || null;
  if (!d || typeof d !== "object") return null;
  const o = d as Record<string, unknown>;
  const text = [o.issue, o.what, o.label, o.name].find((v) => typeof v === "string" && v.trim()) as string | undefined;
  if (!text) return null;
  const sev = typeof o.severity === "string" && o.severity ? `${cap(o.severity)} · ` : "";
  const date = typeof o.date === "string" && o.date ? ` (${o.date})` : "";
  return `${sev}${text}${date}`;
}

/** A step's result as readable label/value pairs, never raw JSON. */
export function readableResult(res: Record<string, unknown> | null | undefined): [string, string][] {
  if (!res || typeof res !== "object") return [];
  const rows: [string, string][] = [];
  const push = (k: string, v: unknown) => { if (v != null && v !== "") rows.push([k, String(v)]); };
  const words = (v: unknown) => (typeof v === "string" ? v.replace(/_/g, " ") : v);
  if (res.risk_band) push("Risk", res.risk_band === "unknown" ? RISK_UNKNOWN_LABEL : cap(String(res.risk_band)));
  if (res.confidence != null && !Number.isNaN(Number(res.confidence))) push("Confidence", `${Math.round(Number(res.confidence) * 100)}%`);
  push("Category", words(res.category));
  push("Matter", words(res.matter_type));
  push("Severity", res.severity);
  if (res.notification_required != null) push("Breach notice", res.notification_required ? "required" : "not required");
  push("Summary", res.summary);
  const list = [res.top_drivers, res.affected_data_categories, res.statutory_deadlines].find((v) => Array.isArray(v) && v.length);
  if (Array.isArray(list)) {
    const items = list.map(driverText).filter((t): t is string => !!t).slice(0, 3);
    if (items.length) push("Drivers", items.join("; "));
  }
  return rows;
}

/** "Overdue 2 days" once a whole day late, plain "Overdue" before that. */
export function overdueWords(due: Date, now: number = Date.now()): string {
  const days = Math.floor((now - due.getTime()) / 86_400_000);
  return days >= 1 ? `Overdue ${days} ${days === 1 ? "day" : "days"}` : "Overdue";
}
