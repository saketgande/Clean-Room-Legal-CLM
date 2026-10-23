import { describe, expect, it } from "vitest";
import { isAwaitingTriage, isOpenRequest } from "./request-status";

const step = { label: "Review", stage: "review", done: false, active: true };

describe("isOpenRequest", () => {
  it.each(["open", "escalated"] as const)("counts a %s request as open", (status) => {
    expect(isOpenRequest({ status })).toBe(true);
  });

  it.each(["approved", "closed"] as const)("does not count a %s request as open", (status) => {
    expect(isOpenRequest({ status })).toBe(false);
  });
});

describe("isAwaitingTriage", () => {
  it("is a request nobody has started a workflow on", () => {
    expect(isAwaitingTriage({ status: "open", workflow: [] })).toBe(true);
    expect(isAwaitingTriage({ status: "open" })).toBe(true);
  });

  it("is not a request that is already in a workflow", () => {
    expect(isAwaitingTriage({ status: "open", workflow: [step] })).toBe(false);
  });

  it("is not a request whose contract has already been drafted by hand", () => {
    expect(isAwaitingTriage({ status: "open", workflow: [], contract_id: "c-1" })).toBe(false);
    expect(isAwaitingTriage({ status: "open", workflow: [], contract_id: null })).toBe(true);
  });

  it("is not an escalated or finished request", () => {
    expect(isAwaitingTriage({ status: "escalated", workflow: [] })).toBe(false);
    expect(isAwaitingTriage({ status: "closed", workflow: [] })).toBe(false);
  });
});
