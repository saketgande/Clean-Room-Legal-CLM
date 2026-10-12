import { describe, expect, it } from "vitest";
import { describeSlaScan } from "./sla-scan";

describe("describeSlaScan", () => {
  it("says what the scan found", () => {
    expect(describeSlaScan({ escalated: 2, breached: 3, completed: 0 })).toBe("Scan: 3 newly overdue, 2 escalated");
  });

  it("also says when it closed finished requests, with the right plural", () => {
    expect(describeSlaScan({ escalated: 0, breached: 0, completed: 1 })).toBe(
      "Scan: 0 newly overdue, 0 escalated, 1 finished request closed",
    );
    expect(describeSlaScan({ escalated: 0, breached: 0, completed: 9 })).toContain("9 finished requests closed");
  });
});
