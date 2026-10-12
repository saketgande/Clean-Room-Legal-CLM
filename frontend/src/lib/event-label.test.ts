import { describe, expect, it } from "vitest";
import { eventLabel } from "./event-label";
import { plural } from "./utils";

describe("eventLabel", () => {
  it("keeps acronyms in capitals instead of writing 'Sla'", () => {
    expect(eventLabel("lifecycle.sla_breach")).toBe("Lifecycle SLA breach");
    expect(eventLabel("lifecycle.sla_escalation")).toBe("Lifecycle SLA escalation");
    expect(eventLabel("ai.skill_succeeded")).toBe("AI skill succeeded");
  });

  it("reads other codes as a plain sentence", () => {
    expect(eventLabel("workflow.step_assigned")).toBe("Workflow step assigned");
    expect(eventLabel("contract.comment.mention")).toBe("Contract comment mention");
    expect(eventLabel("delivered")).toBe("Delivered");
  });

  it("copes with nothing", () => {
    expect(eventLabel(null)).toBe("");
    expect(eventLabel("")).toBe("");
  });
});

describe("plural", () => {
  it("says '1 item', not '1 items'", () => {
    expect(plural(1, "item")).toBe("1 item");
    expect(plural(0, "item")).toBe("0 items");
    expect(plural(3, "item")).toBe("3 items");
    expect(plural(2, "company", "companies")).toBe("2 companies");
    expect(plural(1, "company", "companies")).toBe("1 company");
  });
});
