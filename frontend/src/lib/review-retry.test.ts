import { describe, expect, it, vi } from "vitest";
import type { QueryClient } from "@tanstack/react-query";
import {
  canRetryReview,
  interruptedReviewStep,
  invalidateReviewQueries,
  retryOutcome,
  reviewNoticeText,
  reviewPollInterval,
  reviewQueryKeys,
} from "./review-retry";
import type { AutoReviewState, ReviewRetryResponse, WorkflowRun, WorkflowRunStep } from "./types";

const auto = (over: Partial<AutoReviewState> = {}): AutoReviewState => ({
  state: "interrupted", reason: "stalled", since: "2026-10-08T07:00:00Z", job_id: "j-1", ...over,
});

const step = (over: Partial<WorkflowRunStep> = {}): WorkflowRunStep => ({
  idx: 2, type: "ai_task", name: "AI risk review", status: "waiting_human",
  assignee_user_id: null, assignee_label: null, team_id: null, team_label: null,
  note: null, result: { review_state: "interrupted" }, updated_at: null, ...over,
});

const runOf = (steps: WorkflowRunStep[], over: Partial<WorkflowRun> = {}): WorkflowRun => ({
  id: "r-1", request_id: "q-1", flow_id: "f-1", flow_name: "Standard", status: "waiting",
  current_index: 2, contract_id: "c-1", error: null, steps, comments: [], ...over,
});

describe("who may retry", () => {
  it("needs both the redline and the playbook right, or the wildcard", () => {
    expect(canRetryReview(["contract:redline", "playbook:run"])).toBe(true);
    expect(canRetryReview(["*"])).toBe(true);
    expect(canRetryReview(["contract:redline"])).toBe(false);
    expect(canRetryReview(["playbook:run"])).toBe(false);
    expect(canRetryReview([])).toBe(false);
    expect(canRetryReview(null)).toBe(false);
    expect(canRetryReview(undefined)).toBe(false);
  });
});

describe("keeping an eye on a running review", () => {
  it("asks again only while the review is running", () => {
    expect(reviewPollInterval(auto({ state: "running", reason: null }))).toBe(5000);
    expect(reviewPollInterval(auto())).toBe(false);
    expect(reviewPollInterval(null)).toBe(false);
    expect(reviewPollInterval(undefined)).toBe(false);
  });
});

describe("what the notice says", () => {
  it("says nothing when no review is pending or the state is not known", () => {
    expect(reviewNoticeText(null)).toBeNull();
    expect(reviewNoticeText(undefined)).toBeNull();
    expect(reviewNoticeText({ ...auto(), state: "paused" as never })).toBeNull();
  });

  it("says a running review is in progress and that the changes will appear", () => {
    const t = reviewNoticeText(auto({ state: "running", reason: null }));
    expect(t).toMatchObject({ state: "running", title: "AI review in progress" });
    expect(t?.body).toMatch(/appear here when it finishes/);
  });

  it("says why an interrupted review stopped, in plain words, for each reason", () => {
    expect(reviewNoticeText(auto({ reason: "failed" }))?.body).toMatch(/stopped with an error/);
    expect(reviewNoticeText(auto({ reason: "stalled" }))?.body).toMatch(/never reported back/);
    expect(reviewNoticeText(auto({ reason: "no_job" }))?.body).toMatch(/never started/);
    expect(reviewNoticeText(auto({ reason: null }))?.body).toMatch(/stopped before it finished/);
    expect(reviewNoticeText(auto({ reason: "something_new" }))?.body).toMatch(/stopped before it finished/);
    for (const reason of ["failed", "stalled", "no_job", null]) {
      expect(reviewNoticeText(auto({ reason }))).toMatchObject({ state: "interrupted", title: "The AI review was interrupted" });
    }
  });

  it("says a re-check of accepted changes lists findings and writes nothing", () => {
    const t = reviewNoticeText(auto({ state: "running", reason: null, after: "accepted_edits" }));
    expect(t).toMatchObject({ state: "running", title: "Re-checking your changes" });
    expect(t?.body).toMatch(/what still applies/);
    expect(t?.body).toMatch(/Nothing is changed or redlined automatically/);
    expect(t?.body).not.toMatch(/suggested changes appear/);
  });

  it("tells an interrupted re-check apart, and still says why it stopped", () => {
    const t = reviewNoticeText(auto({ reason: "failed", after: "accepted_edits" }));
    expect(t).toMatchObject({ state: "interrupted", title: "The re-check of your changes was interrupted" });
    expect(t?.body).toMatch(/stopped with an error/);
    expect(t?.body).toMatch(/Retry it to re-check/);
  });

  it("keeps the ordinary wording for any other review", () => {
    expect(reviewNoticeText(auto({ state: "running", after: null }))?.title).toBe("AI review in progress");
    expect(reviewNoticeText(auto({ after: undefined }))?.title).toBe("The AI review was interrupted");
  });

  it("offers reviewing by hand only where a step is waiting on the person", () => {
    expect(reviewNoticeText(auto())?.body).not.toMatch(/yourself/);
    expect(reviewNoticeText(auto(), { byHand: true })?.body).toMatch(/review the contract yourself and complete the step/);
  });
});

describe("what to say after a retry", () => {
  const r = (over: Partial<ReviewRetryResponse>): ReviewRetryResponse => ({ status: "queued", job_id: "j-2", workflow_resumed: false, ...over });

  it("says it restarted, and that the workflow carries on when it was waiting", () => {
    expect(retryOutcome(r({}))).toMatchObject({ tone: "success", message: expect.stringMatching(/restarted/i) });
    expect(retryOutcome(r({})).message).not.toMatch(/workflow/);
    expect(retryOutcome(r({ workflow_resumed: true })).message).toMatch(/workflow carries on/);
  });

  it("is plain about a second click, a review that already exists and a missing playbook", () => {
    expect(retryOutcome(r({ status: "already_running" }))).toMatchObject({ tone: "info", message: expect.stringMatching(/already running/) });
    expect(retryOutcome(r({ status: "not_needed" }))).toMatchObject({ tone: "info", message: expect.stringMatching(/already has a review/) });
    expect(retryOutcome(r({ status: "no_playbook" }))).toMatchObject({ tone: "error", message: expect.stringMatching(/No playbook covers/) });
  });

  it("never reports success for an answer it does not know", () => {
    expect(retryOutcome(r({ status: "mystery" as never })).tone).toBe("error");
  });
});

describe("the workflow step that stopped on an interrupted review", () => {
  it("is found: the current AI step, waiting on a person, marked interrupted", () => {
    const s = step();
    expect(interruptedReviewStep(runOf([step({ idx: 0, status: "done", result: null }), step({ idx: 1, status: "done", result: null }), s]))).toBe(s);
  });

  it("is not found for any other state", () => {
    expect(interruptedReviewStep(null)).toBeNull();
    expect(interruptedReviewStep(undefined)).toBeNull();
    expect(interruptedReviewStep(runOf([step({ idx: 2 })], { status: "running" }))).toBeNull();
    expect(interruptedReviewStep(runOf([step({ idx: 2 })], { status: "complete" }))).toBeNull();
    expect(interruptedReviewStep(runOf([step({ idx: 2, type: "human_task" })]))).toBeNull();
    expect(interruptedReviewStep(runOf([step({ idx: 2, status: "running" })]))).toBeNull();
    expect(interruptedReviewStep(runOf([step({ idx: 2, result: null })]))).toBeNull();
    expect(interruptedReviewStep(runOf([step({ idx: 2, result: { risk_band: "high" } })]))).toBeNull();
    // An interrupted step that is not the current one is not what the run is stopped on.
    expect(interruptedReviewStep(runOf([step({ idx: 1 }), step({ idx: 2, status: "pending", result: null })]))).toBeNull();
    expect(interruptedReviewStep(runOf([], { current_index: 0 }))).toBeNull();
  });
});

describe("refreshing the page after a review", () => {
  it("covers the review state, suggestions, issues, risk, fixes and the workflow", () => {
    const keys = reviewQueryKeys("c-1").map((k) => JSON.stringify(k));
    for (const k of [
      ["review-status", "c-1"], ["contract-review", "c-1"], ["contract", "c-1"], ["contract-edits", "c-1"],
      ["contract-devs", "c-1"], ["contract-risk", "c-1"], ["issue-fix-job", "c-1"], ["flow-run-contract", "c-1"], ["flow-run"],
    ]) expect(keys).toContain(JSON.stringify(k));
  });

  it("invalidates every one of them", () => {
    const invalidateQueries = vi.fn();
    invalidateReviewQueries({ invalidateQueries } as unknown as QueryClient, "c-1");
    expect(invalidateQueries.mock.calls.map((c) => c[0].queryKey)).toEqual(reviewQueryKeys("c-1"));
  });
});
