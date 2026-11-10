import { describe, expect, it } from "vitest";
import { plainStepName, readableResult, currentGroup, openSteps, stepAfter, stepInFocus, overdueWords } from "./workflow-steps";
import type { WorkflowRunStep } from "./types";
import { RISK_UNKNOWN_LABEL } from "./utils";

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

describe("plainStepName", () => {
  it("maps every stored default name; failure mode: 'Clm Draft' leaking to requesters", () => {
    const t: [string, string][] = [["Clm Draft", "Prepare draft"], ["clm_draft", "Prepare draft"], ["Ai Task", "AI check"], ["ai_task", "AI check"],
      ["Human Task", "Review"], ["human_task", "Review"], ["Counterparty", "Negotiation"], ["counterparty", "Negotiation"],
      ["Notify", "Notify people"], ["notify", "Notify people"], ["approval", "Approval"], ["signature", "Signature"]];
    for (const [a, b] of t) expect(plainStepName(a)).toBe(b);
  });
  it("leaves a name a person typed alone; failure mode: renaming custom steps", () => {
    expect(plainStepName("  Legal review ")).toBe("Legal review");
  });
  it("falls back by type, then 'Step'; failure mode: blank row label or a throw on null", () => {
    expect(plainStepName("", "ai_task")).toBe("AI check");
    expect(plainStepName(null, "nope")).toBe("Step");
    expect(plainStepName(undefined)).toBe("Step");
  });
});

describe("readableResult", () => {
  it("null result gives no rows; failure mode: throwing on a step with no result", () => {
    expect(readableResult(null)).toEqual([]);
    expect(readableResult(undefined)).toEqual([]);
  });
  it("renders driver objects as text, never JSON; failure mode: raw {\"deviation_id\"...} on the page", () => {
    const rows = readableResult({
      risk_band: "high", confidence: 0.3,
      top_drivers: [{ deviation_id: "d1", severity: "high", issue: "Liability is unlimited" }, { deviation_id: "d2" }],
    });
    expect(rows).toContainEqual(["Risk", "High"]);
    expect(rows).toContainEqual(["Confidence", "30%"]);
    expect(rows).toContainEqual(["Drivers", "High · Liability is unlimited"]);
    const out = rows.map((r) => r[1]).join(" ");
    expect(out).not.toMatch(/[{"]|deviation_id/);
  });
  it("adds the date to deadlines and keeps only three; failure mode: dates lost or a wall of text", () => {
    const rows = readableResult({ statutory_deadlines: [{ what: "Notify regulator", date: "2026-11-01" }, "a", "b", "c"] });
    expect(rows).toEqual([["Drivers", "Notify regulator (2026-11-01); a; b"]]);
  });
  it("omits Drivers when nothing is readable; failure mode: empty label", () => {
    expect(readableResult({ top_drivers: [{ deviation_id: "x" }] })).toEqual([]);
  });
  it("unknown band uses the shared label and underscores become spaces; failure mode: 'unknown' or snake_case shown", () => {
    const rows = readableResult({ risk_band: "unknown", category: "data_breach", notification_required: false });
    expect(rows).toEqual([["Risk", RISK_UNKNOWN_LABEL], ["Category", "data breach"], ["Breach notice", "not required"]]);
  });
});

describe("overdueWords", () => {
  it("counts whole days late and says plain Overdue under a day", () => {
    /* Guards a tile reading "Overdue 0 days" or "Overdue 1 days" instead of the plain wording. */
    const now = Date.UTC(2026, 9, 20, 12);
    expect(overdueWords(new Date(now - (2 * 24 + 3) * 3_600_000), now)).toBe("Overdue 2 days");
    expect(overdueWords(new Date(now - 24 * 3_600_000), now)).toBe("Overdue 1 day");
    expect(overdueWords(new Date(now - 3 * 3_600_000), now)).toBe("Overdue");
  });
});
