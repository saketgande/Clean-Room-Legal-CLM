import { describe, expect, it } from "vitest";
import { buildSuggestions, editOrigin, editsForIssue, editState, reviewProgress } from "./suggestions";
import type { ContractDeviation, ContractEditResponse } from "./types";

const dev = (id: string, over: Partial<ContractDeviation> = {}): ContractDeviation => ({
  id,
  playbook_run_id: "run-1",
  playbook_rule_id: null,
  contract_id: "c-1",
  severity: "medium",
  clause_type: "payment",
  issue: `Issue ${id}`,
  suggested_fix: null,
  citation: null,
  status: "open",
  ...over,
});

const edit = (id: string, over: Partial<ContractEditResponse> = {}): ContractEditResponse => ({
  id,
  contract_id: "c-1",
  contract_version_id: "v-1",
  edit_type: "replace",
  status: "proposed",
  original_text: "Net 30.",
  replacement_text: "Net 60.",
  rationale: null,
  citation: [],
  playbook_deviation_id: null,
  locatable: true,
  ...over,
});

describe("buildSuggestions", () => {
  it("shows a chat suggestion as its own card, next to the playbook issues", () => {
    const s = buildSuggestions({
      deviations: [dev("d1")],
      edits: [edit("e1", { edit_type: "playbook_redline", playbook_deviation_id: "d1" }), edit("e2")],
    });
    expect(s.issues.map((i) => i.deviation.id)).toEqual(["d1"]);
    expect(s.issues[0].edits.map((e) => e.id)).toEqual(["e1"]);
    expect(s.standalone.map((e) => e.id)).toEqual(["e2"]);
    expect(s.toDecide).toBe(2);
    expect(s.proposedEdits).toBe(2);
  });

  it("does not list a decided change as a card", () => {
    const s = buildSuggestions({ deviations: [], edits: [edit("e1", { status: "accepted" }), edit("e2", { status: "rejected" })] });
    expect(s.standalone).toEqual([]);
    expect(s.toDecide).toBe(0);
    expect(s.proposedEdits).toBe(0);
  });

  it("keeps a decided change on its issue so the issue can be closed", () => {
    const s = buildSuggestions({
      deviations: [dev("d1")],
      edits: [edit("e1", { status: "accepted", playbook_deviation_id: "d1" })],
    });
    expect(s.issues[0].edits[0].status).toBe("accepted");
    expect(s.toDecide).toBe(1);
  });

  it("skips closed issues and shows their still-undecided change on its own", () => {
    const s = buildSuggestions({
      deviations: [dev("d1", { status: "accepted" })],
      edits: [edit("e1", { playbook_deviation_id: "d1" })],
    });
    expect(s.issues).toEqual([]);
    expect(s.standalone.map((e) => e.id)).toEqual(["e1"]);
  });

  it("offers a new fix for an issue whose only change was rejected for an older version", () => {
    const s = buildSuggestions({
      deviations: [dev("d1")],
      edits: [edit("e1", { status: "rejected", contract_version_id: "v-0", playbook_deviation_id: "d1" })],
      currentVersionId: "v-1",
    });
    expect(s.issues[0].edits).toEqual([]);
  });

  it("still shows a rejected change made for the current version, so the issue can be closed", () => {
    const s = buildSuggestions({
      deviations: [dev("d1")],
      edits: [edit("e1", { status: "rejected", playbook_deviation_id: "d1" })],
      currentVersionId: "v-1",
    });
    expect(s.issues[0].edits.map((e) => e.id)).toEqual(["e1"]);
  });

  it("counts an issue that needs review as open", () => {
    expect(buildSuggestions({ deviations: [dev("d1", { status: "needs_review" })], edits: [] }).issues).toHaveLength(1);
  });

  it("puts the most serious issues first and keeps the server order within a level", () => {
    const s = buildSuggestions({
      deviations: [dev("low", { severity: "low" }), dev("a", { severity: "high" }), dev("crit", { severity: "critical" }), dev("b", { severity: "high" }), dev("med")],
      edits: [],
    });
    expect(s.issues.map((i) => i.deviation.id)).toEqual(["a", "crit", "b", "med", "low"]);
  });
});

describe("editsForIssue", () => {
  it("finds changes by the issue they name, whatever made them", () => {
    const edits = [edit("e1", { playbook_deviation_id: "d1" }), edit("e2", { playbook_deviation_id: "d1", edit_type: "playbook_redline" }), edit("e3", { playbook_deviation_id: "d2" })];
    expect(editsForIssue(dev("d1"), edits).map((e) => e.id)).toEqual(["e1", "e2"]);
  });

  it("matches an older playbook change by its text, but only when it names no issue", () => {
    const old = edit("e1", { edit_type: "playbook_redline", rationale: "Issue d1" });
    const other = edit("e2", { edit_type: "playbook_redline", rationale: "Issue d1", playbook_deviation_id: "d9" });
    const chat = edit("e3", { rationale: "Issue d1" });
    expect(editsForIssue(dev("d1"), [old, other, chat]).map((e) => e.id)).toEqual(["e1"]);
  });
});

describe("editState", () => {
  it("is ready for a placed change on the current version", () => {
    expect(editState(edit("e1"), "v-1")).toBe("ready");
  });
  it("is stale once the contract has moved on", () => {
    expect(editState(edit("e1"), "v-2")).toBe("stale");
  });
  it("is unplaceable when the text can't locate it", () => {
    expect(editState(edit("e1", { locatable: false }), "v-1")).toBe("unplaceable");
  });
  it("reports stale ahead of unplaceable", () => {
    expect(editState(edit("e1", { locatable: false }), "v-2")).toBe("stale");
  });
  it("doesn't call a change stale before the contract has loaded", () => {
    expect(editState(edit("e1"), null)).toBe("ready");
  });
});

describe("editOrigin", () => {
  it("tells playbook, manual and assistant changes apart", () => {
    expect(editOrigin(edit("e1", { edit_type: "playbook_redline" }))).toBe("playbook");
    expect(editOrigin(edit("e2", { edit_type: "manual" }))).toBe("manual");
    expect(editOrigin(edit("e3", { edit_type: "replace" }))).toBe("assistant");
  });
});

describe("reviewProgress", () => {
  const build = (deviations: ContractDeviation[], edits: ContractEditResponse[], version: string | null = "v-1") =>
    reviewProgress(buildSuggestions({ deviations, edits, currentVersionId: version }), edits, version);

  it("is all zero when there is nothing to review", () => {
    expect(build([], [])).toEqual({ decided: 0, total: 0, toDecide: 0, risk: { high: 0, medium: 0, low: 0 } });
  });

  it("counts the open issues by severity, folding critical into high", () => {
    const p = build(
      [dev("a", { severity: "critical" }), dev("b", { severity: "high" }), dev("c", { severity: "medium" }), dev("d", { severity: "low" }), dev("e", { severity: "low" })],
      [],
    );
    expect(p.risk).toEqual({ high: 2, medium: 1, low: 2 });
    expect(p.toDecide).toBe(5);
    expect(p.total).toBe(5);
    expect(p.decided).toBe(0);
  });

  it("leaves out issues that are not open, and severities it does not know", () => {
    const p = build([dev("a", { status: "accepted", severity: "high" }), dev("b", { severity: "their_change" })], []);
    expect(p.risk).toEqual({ high: 0, medium: 0, low: 0 });
    expect(p.toDecide).toBe(1);
  });

  it("starts at 0 of everything, then counts each decided change as one more done", () => {
    const issues = [dev("a"), dev("b"), dev("c")];
    // nothing decided yet: three issues, two of them have a proposed change
    expect(build(issues, [edit("e1", { playbook_deviation_id: "a" }), edit("e2", { playbook_deviation_id: "b" })])).toMatchObject({ decided: 0, total: 3, toDecide: 3 });
    // "a" was accepted: its issue left the open list and its change is decided
    const after = build(
      [dev("b"), dev("c")],
      [edit("e1", { playbook_deviation_id: "a", status: "accepted" }), edit("e2", { playbook_deviation_id: "b" })],
    );
    expect(after).toMatchObject({ decided: 1, total: 3, toDecide: 2 });
  });

  it("counts a decided change that fixed no issue as decided", () => {
    const p = build([], [edit("e1", { status: "rejected" }), edit("e2")]);
    expect(p).toMatchObject({ decided: 1, total: 2, toDecide: 1 });
  });

  it("ignores changes made for an older version of the contract", () => {
    const p = build([], [edit("old", { contract_version_id: "v-0", status: "accepted" }), edit("now", { status: "accepted" })]);
    expect(p.decided).toBe(1);
  });

  it("counts every decided change while the current version is not known yet", () => {
    const p = build([], [edit("a", { contract_version_id: "v-0", status: "accepted" }), edit("b", { status: "rejected" })], null);
    expect(p.decided).toBe(2);
  });
});
