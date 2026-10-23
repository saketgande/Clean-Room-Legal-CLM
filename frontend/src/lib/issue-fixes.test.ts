import { describe, expect, it } from "vitest";
import { describeFixJob, draftingText, isFixJobActive } from "./issue-fixes";
import type { IssueFixJob } from "./types";

const job = (over: Partial<IssueFixJob> = {}): IssueFixJob => ({
  job_id: "j-1", status: "succeeded", trigger: "review", total: 3, done: 3, drafted: 3, no_change: 0, failed: 0,
  left_for_later: 0, stopped_early: false, started_at: null, finished_at: null, ...over,
});

describe("isFixJobActive", () => {
  it("is true only while changes are still being written", () => {
    expect(isFixJobActive(job({ status: "queued" }))).toBe(true);
    expect(isFixJobActive(job({ status: "running" }))).toBe(true);
    for (const status of ["succeeded", "failed", "cancelled"] as const) expect(isFixJobActive(job({ status }))).toBe(false);
    expect(isFixJobActive(null)).toBe(false);
    expect(isFixJobActive(undefined)).toBe(false);
  });
});

describe("draftingText", () => {
  it("says how far the job has got once it has counted its issues", () => {
    expect(draftingText({ done: 0, total: 3 })).toBe("Drafting suggested changes — 0 of 3 done.");
    expect(draftingText({ done: 2, total: 3 })).toBe("Drafting suggested changes — 2 of 3 done.");
  });

  it("does not show a count it doesn't have yet", () => {
    expect(draftingText({ done: 0, total: 0 })).toBe("Drafting suggested changes…");
    expect(draftingText(null)).toBe("Drafting suggested changes…");
    expect(draftingText(undefined)).toBe("Drafting suggested changes…");
  });
});

describe("describeFixJob", () => {
  it("is plain good news when every issue got a change", () => {
    expect(describeFixJob(job({ drafted: 3 }))).toEqual({ text: "Drafted 3 suggested changes — accept or reject them below.", tone: "success" });
    expect(describeFixJob(job({ total: 1, done: 1, drafted: 1 })).text).toBe("Drafted 1 suggested change — accept or reject it below.");
  });

  it("says when the model found nothing to change for some, which is an answer and not an error", () => {
    const r = describeFixJob(job({ drafted: 1, no_change: 2 }));
    expect(r.text).toBe("Drafted 1 suggested change — accept or reject it below. Aegis found nothing to change for 2 issues.");
    expect(r.tone).toBe("success");
    expect(describeFixJob(job({ drafted: 0, no_change: 1 })).text).toBe("Aegis found nothing to change for 1 issue.");
  });

  it("says what to do about the issues that errored, and is not a success", () => {
    const one = describeFixJob(job({ drafted: 2, failed: 1 }));
    expect(one.text).toContain("Couldn't draft a change for 1 issue: use “Draft fix with AI” on it to try again.");
    expect(one.tone).toBe("info");
    expect(describeFixJob(job({ drafted: 1, failed: 2 })).text).toContain("for 2 issues: use “Draft fix with AI” on each to try again.");
  });

  it("says when it left issues for the person, or stopped early", () => {
    const later = describeFixJob(job({ drafted: 8, left_for_later: 3 }));
    expect(later.text).toContain("3 more open issues were left for you");
    expect(later.tone).toBe("info");
    expect(describeFixJob(job({ drafted: 8, left_for_later: 1 })).text).toContain("1 more open issue was left for you");
    const early = describeFixJob(job({ drafted: 2, stopped_early: true, left_for_later: 5 }));
    expect(early.text).toContain("It stopped early; use “Draft fixes for all” to carry on with the rest.");
    expect(early.text).not.toContain("left for you");
  });

  it("tells the person to try again when nothing could be written", () => {
    const r = describeFixJob(job({ status: "failed", drafted: 0, failed: 3 }));
    expect(r).toEqual({ text: "Couldn't draft suggested changes automatically. Use “Draft fixes for all” to try again.", tone: "error" });
    expect(describeFixJob(job({ status: "failed", drafted: 0, failed: 0, total: 0, done: 0 })).tone).toBe("error");
    expect(describeFixJob(job({ drafted: 0, stopped_early: true, done: 0 })).tone).toBe("error");
  });

  it("does not call it a failure when every issue was handled while it ran", () => {
    expect(describeFixJob(job({ drafted: 0, total: 0, done: 0 }))).toEqual({ text: "Every open issue already has a suggested change.", tone: "info" });
  });

  it("says a cancelled job was cancelled", () => {
    expect(describeFixJob(job({ status: "cancelled", drafted: 0 }))).toEqual({ text: "Drafting suggested changes was cancelled.", tone: "info" });
  });

  it("never contains an internal error text", () => {
    for (const j of [job({ status: "failed", drafted: 0, failed: 3 }), job({ drafted: 1, failed: 1 })])
      expect(describeFixJob(j).text).not.toMatch(/Error|Traceback|401|500|exception/i);
  });
});
