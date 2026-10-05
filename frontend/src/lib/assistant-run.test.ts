import { describe, expect, it } from "vitest";
import { runNoticeFor } from "./assistant-run";
import type { AssistantRunSummary } from "./types";

const q = [{ id: "m1", content: "What is the notice period?" }];
const run = (over: Partial<AssistantRunSummary>): AssistantRunSummary => ({
  id: "r1",
  status: "succeeded",
  error_message: null,
  user_message_id: "m1",
  has_answer: true,
  ...over,
});

describe("runNoticeFor", () => {
  it("says nothing about a finished or running answer", () => {
    expect(runNoticeFor(null, q)).toBeNull();
    expect(runNoticeFor(run({}), q)).toBeNull();
    expect(runNoticeFor(run({ status: "running", has_answer: false }), q)).toBeNull();
    expect(runNoticeFor(run({ status: "waiting_confirmation", has_answer: false }), q)).toBeNull();
  });

  it("offers to retry a failed answer with the server's message", () => {
    const n = runNoticeFor(run({ status: "failed", has_answer: false, error_message: "Budget used up." }), q);
    expect(n).toEqual({ message: "Budget used up.", retryText: "What is the notice period?" });
  });

  it("calls a partly written answer cut off", () => {
    const n = runNoticeFor(run({ status: "interrupted", has_answer: true }), q);
    expect(n?.message).toMatch(/cut off/);
    expect(n?.retryText).toBe("What is the notice period?");
  });

  it("only mentions a stop when nothing was written", () => {
    expect(runNoticeFor(run({ status: "cancelled", has_answer: true }), q)).toBeNull();
    expect(runNoticeFor(run({ status: "cancelled", has_answer: false }), q)?.message).toMatch(/stopped/);
  });

  it("can't retry when the question isn't loaded", () => {
    expect(runNoticeFor(run({ status: "failed", has_answer: false }), [])?.retryText).toBeNull();
  });
});
