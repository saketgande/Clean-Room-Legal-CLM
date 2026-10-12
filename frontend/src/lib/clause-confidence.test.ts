import { describe, expect, it } from "vitest";
import { clauseConfidenceLabel } from "./clause-confidence";

describe("clauseConfidenceLabel", () => {
  it.each([
    ["high", "High"],
    ["medium", "Medium"],
    ["low", "Low"],
  ])("shows %s as the word %s, never a percentage", (value, label) => {
    expect(clauseConfidenceLabel(value)).toBe(label);
  });

  it("shows nothing when the server stated no confidence", () => {
    expect(clauseConfidenceLabel(null)).toBe("");
    expect(clauseConfidenceLabel(undefined)).toBe("");
  });

  it("never produces NaN", () => {
    expect(clauseConfidenceLabel("high")).not.toContain("NaN");
  });
});
