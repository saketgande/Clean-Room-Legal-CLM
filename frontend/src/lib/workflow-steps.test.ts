import { describe, expect, it } from "vitest";
import { currentGroup, openSteps, stepAfter, stepInFocus } from "./workflow-steps";
import type { WorkflowRunStep } from "./types";

const step = (idx: number, status: string, over: Partial<WorkflowRunStep> = {}): WorkflowRunStep => ({
  idx, type: "human_task", name: `Step ${idx}`, status,
  assignee_user_id: null, assignee_label: null, team_id: null, team_label: null, note: null, result: null, updated_at: null,
  ...over,
});
const par = (idx: number, status: string, over: Partial<WorkflowRunStep> = {}) => step(idx, status, { parallel: true, ...over });
const run = (steps: WorkflowRunStep[], current_index = 0) => ({ current_index, steps });

describe("currentGroup", () => {
  it("is just the current step when nothing runs beside it", () => {
    expect(currentGroup(run([step(0, "waiting_human"), step(1, "pending")])).map((s) => s.idx)).toEqual([0]);
  });

  it("holds the current step and the steps flagged parallel right after it", () => {
    const r = run([step(0, "done"), step(1, "waiting_human"), par(2, "waiting_human"), par(3, "waiting_human"), step(4, "pending")], 1);
    expect(currentGroup(r).map((s) => s.idx)).toEqual([1, 2, 3]);
  });

  it("is empty with no run, or when current_index names no step", () => {
    expect(currentGroup(null)).toEqual([]);
    expect(currentGroup(run([step(0, "done")], 4))).toEqual([]);
  });
});

describe("stepInFocus", () => {
  it("is the step still waiting once the first of two side-by-side steps is done", () => {
    const r = run([step(0, "done"), par(1, "waiting_human")]);
    expect(stepInFocus(r)?.idx).toBe(1);
  });

  it("is the viewer's own step when both wait", () => {
    const r = run([step(0, "waiting_human", { assignee_user_id: "a" }), par(1, "waiting_human", { assignee_user_id: "b" })]);
    expect(stepInFocus(r, "b")?.idx).toBe(1);
    expect(stepInFocus(r, "a")?.idx).toBe(0);
    expect(stepInFocus(r, "someone-else")?.idx).toBe(0);
  });

  it("prefers a step waiting for a person over one that is still working", () => {
    const r = run([step(0, "running"), par(1, "waiting_human")]);
    expect(stepInFocus(r)?.idx).toBe(1);
  });

  it("is the step the run is at when nothing in the group is open", () => {
    expect(stepInFocus(run([step(0, "done"), par(1, "done")]))?.idx).toBe(0);
  });

  it("is the current step of a plain sequential workflow, whatever its state", () => {
    expect(stepInFocus(run([step(0, "done"), step(1, "waiting_human"), step(2, "pending")], 1))?.idx).toBe(1);
    expect(stepInFocus(run([step(0, "running")]))?.idx).toBe(0);
  });
});

describe("openSteps / stepAfter", () => {
  it("lists only the unfinished steps of the group", () => {
    expect(openSteps(run([step(0, "done"), par(1, "waiting_human"), par(2, "skipped")])).map((s) => s.idx)).toEqual([1]);
  });

  it("follows a step with the open step beside it while there is one", () => {
    const r = run([step(0, "waiting_human"), par(1, "waiting_human"), step(2, "approval" as never)]);
    expect(stepAfter(r, r.steps[0])?.idx).toBe(1);
  });

  it("follows the last open step of a group with the first step after the group", () => {
    const r = run([step(0, "done"), par(1, "waiting_human"), step(2, "pending")]);
    expect(stepAfter(r, r.steps[1])?.idx).toBe(2);
  });

  it("follows the first of the group with the step after the group when the others are done", () => {
    const r = run([step(0, "waiting_human"), par(1, "done"), step(2, "pending")]);
    expect(stepAfter(r, r.steps[0])?.idx).toBe(2);
  });

  it("has nothing after the last step", () => {
    const r = run([step(0, "waiting_human")]);
    expect(stepAfter(r, r.steps[0])).toBeNull();
  });
});
