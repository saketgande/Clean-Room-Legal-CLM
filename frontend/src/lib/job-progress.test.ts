import { describe, expect, it } from "vitest";
import { jobProgressPercent } from "./job-progress";

describe("jobProgressPercent", () => {
  it("shows a job that has just started as 5%, not as finished", () => {
    expect(jobProgressPercent(5)).toBe(5);
  });

  it("shows the values the worker writes as they are", () => {
    expect(jobProgressPercent(0)).toBe(0);
    expect(jobProgressPercent(40)).toBe(40);
    expect(jobProgressPercent(100)).toBe(100);
  });

  it("keeps odd values inside 0 to 100 and never produces NaN", () => {
    expect(jobProgressPercent(-3)).toBe(0);
    expect(jobProgressPercent(250)).toBe(100);
    expect(jobProgressPercent(null)).toBe(0);
    expect(jobProgressPercent(undefined)).toBe(0);
    expect(jobProgressPercent(Number.NaN)).toBe(0);
  });
});
