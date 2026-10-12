import { describe, expect, it } from "vitest";
import { dueText } from "./approval-due";

const at = (h: number, m = 0, dayOffset = 0) => {
  const d = new Date(2026, 9, 8 + dayOffset, h, m, 0);
  return d.toISOString();
};
const now = new Date(2026, 9, 8, 14, 0, 0);

describe("dueText", () => {
  it("is a dash with no due date", () => {
    expect(dueText({ due_at: null, overdue: false, status: "pending" }, now)).toBe("—");
  });
  it("says how late an overdue row is", () => {
    const out = dueText({ due_at: at(10), overdue: true, status: "pending" }, now);
    expect(out).toMatch(/Oct 8, 2026 · .*ago$/);
  });
  it("gives the time of day for a pending row due later today", () => {
    expect(dueText({ due_at: at(18, 30), overdue: false, status: "pending" }, now)).toMatch(/^Today \d{1,2}:30/);
  });
  it("is just the date for a row due on another day, or already decided", () => {
    expect(dueText({ due_at: at(18, 30, 3), overdue: false, status: "pending" }, now)).toBe("Oct 11, 2026");
    expect(dueText({ due_at: at(18, 30), overdue: false, status: "approved" }, now)).toBe("Oct 8, 2026");
  });
});
