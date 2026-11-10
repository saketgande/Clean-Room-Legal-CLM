import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RiskReview, riskFacts, RISK_CSS } from "./_request-risk";
import type { ContractDeviation, ContractRiskSummary } from "@/lib/types";

const dev = (id: string, severity: string, over: Partial<ContractDeviation> = {}): ContractDeviation => ({
  id, playbook_run_id: "r", playbook_rule_id: null, contract_id: "c", severity, clause_type: "limitation_of_liability",
  issue: `Issue ${id}`, suggested_fix: null, citation: null, status: "open", ...over,
});
const risk = (over: Partial<ContractRiskSummary> = {}): ContractRiskSummary => ({
  band: "high", counts: { critical: 0, high: 1, medium: 0, low: 0 }, drivers: [], ...over,
});
const view = (r: ContractRiskSummary | null, devs: ContractDeviation[] | undefined, p: { canRecompute?: boolean } = {}) => {
  const onRecompute = vi.fn();
  render(<RiskReview risk={r} facts={riskFacts(r, devs, true)} canRecompute={!!p.canRecompute} recomputing={false} onRecompute={onRecompute} />);
  return onRecompute;
};

describe("riskFacts", () => {
  it("counts critical into the high bucket and gates on critical+high", () => {
    /* Guards the header pill / decision warning drifting from the backend contract_risk counts. */
    const f = riskFacts(risk({ counts: { critical: 1, high: 2, medium: 3, low: 4 } }), [], true);
    expect([f.critCount, f.gateCount, f.findHigh, f.findMedium, f.findLow, f.findingCount]).toEqual([1, 3, 3, 3, 4, 10]);
  });
  it("decidedCount is findings minus open devs, and 0 until deviations load", () => {
    /* Guards '3 findings' beside an empty list reading as a contradiction. */
    const r = risk({ counts: { critical: 0, high: 3, medium: 0, low: 0 } });
    expect(riskFacts(r, [dev("a", "high")], true).decidedCount).toBe(2);
    expect(riskFacts(r, [dev("a", "high"), dev("b", "high"), dev("c", "high")], true).decidedCount).toBe(0);
    expect(riskFacts(r, undefined, true).decidedCount).toBe(0);
  });
  it("needs_review devs stay open, decided ones drop out", () => {
    /* Guards an unverified finding vanishing from the list. */
    const f = riskFacts(risk(), [dev("a", "high", { status: "needs_review" }), dev("b", "high", { status: "accepted" })], true);
    expect(f.openDevs.map((d) => d.id)).toEqual(["a"]);
  });
  it("unknown only when the request has a contract", () => {
    /* Guards a contract-less request showing 'not reviewed'. */
    expect(riskFacts(risk({ band: "unknown" }), [], true).unknown).toBe(true);
    expect(riskFacts(risk({ band: "unknown" }), [], false).unknown).toBe(false);
  });
});

describe("RiskReview", () => {
  it("shows band, counts, issue with clause and fix, and conceded drivers", () => {
    /* Guards the readable issue list losing the clause, the fix, or the still-counts note. */
    const r = risk({ counts: { critical: 0, high: 1, medium: 0, low: 1 }, drivers: [{ deviation_id: "z", issue: "Old issue", severity: "low", status: "accepted", conceded: true }] });
    view(r, [dev("a", "high", { suggested_fix: "Cap at fees" })]);
    expect(screen.getByText("high risk")).toBeTruthy();
    expect(screen.getByText(/2 findings count toward this · 1 high · 0 medium · 1 low · 1 still to decide · 1 decided, still counted/)).toBeTruthy();
    expect(screen.getByText("limitation of liability")).toBeTruthy();
    expect(screen.getByText("Issue a")).toBeTruthy();
    expect(screen.getByText(/Cap at fees/)).toBeTruthy();
    expect(screen.getByText("accepted as-is — still counts")).toBeTruthy();
  });
  it("keeps the medium group collapsed until clicked", () => {
    /* Guards a long low-value list pushing the decision down the page. */
    view(risk({ band: "medium", counts: { critical: 0, high: 0, medium: 1, low: 0 } }), [dev("m", "medium")]);
    expect(screen.queryByText("Issue m")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /1 medium/ }));
    expect(screen.getByText("Issue m")).toBeTruthy();
  });
  it("offers Recompute only with canRecompute", () => {
    /* Guards a non-manager seeing or firing the recompute action. */
    const cb = view(risk(), [], { canRecompute: true });
    fireEvent.click(screen.getByRole("button", { name: /Recompute/ }));
    expect(cb).toHaveBeenCalledTimes(1);
  });
  it("hides Recompute without canRecompute", () => {
    /* Guards the gate being dropped. */
    view(risk(), []);
    expect(screen.queryByRole("button", { name: /Recompute/ })).toBeNull();
  });
  it("shows one Not reviewed line when the band is unknown", () => {
    /* Guards an unreviewed text being shown as clean or as a risk level. */
    view(risk({ band: "unknown", note: "Text changed." }), []);
    expect(screen.getByText(/Not reviewed — needs a person/)).toBeTruthy();
    expect(screen.getByText(/Text changed\./)).toBeTruthy();
    expect(screen.queryByText(/risk$/)).toBeNull();
  });
  it("shows 'No open issues.' for low with zero findings", () => {
    /* Guards an empty low-risk card rendering as blank space. */
    view(risk({ band: "low", counts: { critical: 0, high: 0, medium: 0, low: 0 } }), []);
    expect(screen.getByText("No open issues.")).toBeTruthy();
  });
  it("shows a loading line while the risk is not loaded", () => {
    /* Guards a blank card before the query returns. */
    view(null, undefined);
    expect(screen.getByText("Loading the risk review…")).toBeTruthy();
  });
});

describe("RISK_CSS", () => {
  it("has no hardcoded colours", () => {
    /* Guards the dark-mode regressions (#fff gauge, #fdecec/#b42318 critical). */
    expect(RISK_CSS).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });
});
