import { describe, expect, it } from "vitest";
import { answerProblem, EMAIL_PROBLEM, looksLikeEmail } from "./form-checks";

describe("looksLikeEmail (the same rule as the server)", () => {
  it.each(["maya@example.com", " maya@example.com ", "maya.chen+legal@sub.example.co.in"])("accepts %s", (ok) => {
    expect(looksLikeEmail(ok)).toBe(true);
  });

  it.each(["maya.example.com", "maya@", "@example.com", "maya@example", "ma ya@example.com", "maya@@example.com",
    "maya@exa..mple.com", "maya@example.", "a@b@c.com", "maya@.example.com", ""])("refuses %j", (bad) => {
    expect(looksLikeEmail(bad)).toBe(false);
  });

  it("refuses an address longer than a mailbox can be", () => {
    expect(looksLikeEmail(`${"a".repeat(250)}@example.com`)).toBe(false);
  });
});

describe("answerProblem", () => {
  it("says nothing about an empty answer: whether it is required is another question", () => {
    expect(answerProblem("email", "")).toBeNull();
    expect(answerProblem("email", undefined)).toBeNull();
    expect(answerProblem("date", "  ", { label: "Start date", value: "2026-10-01" })).toBeNull();
  });

  it("names what is wrong with an email with no @", () => {
    expect(answerProblem("email", "maya.example.com")).toBe(EMAIL_PROBLEM);
    expect(answerProblem("email", "maya@example.com")).toBeNull();
  });

  it("refuses an end date before its start date, and says which date", () => {
    const start = { label: "Start date", value: "2026-10-01" };
    expect(answerProblem("date", "2026-01-01", start)).toBe("Can't be before start date.");
    expect(answerProblem("date", "2026-10-01", start)).toBeNull(); // the same day is allowed
    expect(answerProblem("date", "2027-09-30", start)).toBeNull();
  });

  it("does not compare against a start date that is not filled in yet", () => {
    expect(answerProblem("date", "2026-01-01", { label: "Start date", value: "" })).toBeNull();
    expect(answerProblem("date", "2026-01-01", { label: "Start date", value: undefined })).toBeNull();
    expect(answerProblem("date", "2026-01-01")).toBeNull();
  });

  it("does not compare a date that is still half typed", () => {
    expect(answerProblem("date", "2026-1", { label: "Start date", value: "2026-10-01" })).toBeNull();
  });
});
