// Which step of a running workflow a person is looking at and can act on.
//
// Steps flagged `parallel` run side by side with the step above them ("all must respond"). The server's
// `current_index` stays on the FIRST step of such a group until every step in it is finished, so once the
// first reviewer is done, `steps[current_index]` is a finished step while the other reviewer is still being
// waited for. Screens that read only `steps[current_index]` then show the wrong step as "here", and offer
// nobody a button to finish the one that is actually waiting.

import type { WorkflowRun, WorkflowRunStep } from "./types";

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
