// Retrying an AI review that stopped part-way.
//
// After a draft is made, a counterparty revision arrives, or accepted edits make a new version, the
// server reviews the contract against its playbook in the background. If the worker doing that stops
// part-way, the review never finishes. The server now says so (`auto_review` on the review status:
// "running" or "interrupted") and offers one way out: run the review again (contractsApi.retryReview,
// or workflowsApi.retryReview for a workflow step that stopped on it).
//
// This file holds the plain rules the screens share: who may retry, what to say, when to keep asking
// the server, and which cached data to refresh. The screens only draw and report clicks.

import type { QueryClient } from "@tanstack/react-query";
import type { AutoReviewState, ReviewRetryResponse, WorkflowRun, WorkflowRunStep } from "./types";

/** Restarting a review runs a playbook and writes tracked changes, so the server asks for both rights. */
export const RETRY_REVIEW_PERMISSIONS = ["contract:redline", "playbook:run"] as const;

/** A hint for the buttons; the server is the one that decides (it answers 403 when this is wrong). */
export function canRetryReview(permissions: readonly string[] | null | undefined): boolean {
  if (!permissions) return false;
  if (permissions.includes("*")) return true;
  return RETRY_REVIEW_PERMISSIONS.every((p) => permissions.includes(p));
}

/** How often to ask the server again while a review is running. */
export const REVIEW_POLL_MS = 5_000;

/**
 * For a query's `refetchInterval`: keep asking while the review is running, so the page notices when it
 * finishes (or turns out to be interrupted). Not while interrupted: nothing changes until someone retries.
 */
export function reviewPollInterval(auto: AutoReviewState | null | undefined): number | false {
  return auto?.state === "running" ? REVIEW_POLL_MS : false;
}

/** The server's `auto_review.after` for a review that follows accepted changes (contracts.auto_review.AFTER_ACCEPT). */
export const AFTER_ACCEPT = "accepted_edits";

export interface ReviewNoticeText {
  state: "running" | "interrupted";
  title: string;
  body: string;
}

const WHY_INTERRUPTED: Record<string, string> = {
  failed: "It stopped with an error before it finished.",
  stalled: "It started but never reported back.",
  no_job: "It was asked for but never started.",
};

/**
 * What to tell a person about a requested review that has not finished. `null` when there is nothing to
 * say (no review pending, or a state this page does not know). `byHand` adds that the person can also
 * review the contract themselves (used where a workflow step is waiting on them).
 */
export function reviewNoticeText(
  auto: AutoReviewState | null | undefined,
  opts: { byHand?: boolean } = {},
): ReviewNoticeText | null {
  if (!auto) return null;
  // After accepted changes the server only re-checks the new text and lists what still applies;
  // it writes no new suggested changes, so don't promise any.
  const recheck = auto.after === AFTER_ACCEPT;
  if (auto.state === "running") {
    return recheck
      ? {
          state: "running",
          title: "Re-checking your changes",
          body: "Aegis is reading the updated contract against the playbook to see what still applies. Nothing is changed or redlined automatically.",
        }
      : {
          state: "running",
          title: "AI review in progress",
          body: "Aegis is reading this draft against the playbook. The suggested changes appear here when it finishes.",
        };
  }
  if (auto.state === "interrupted") {
    const why = WHY_INTERRUPTED[auto.reason ?? ""] ?? "It stopped before it finished.";
    const next = opts.byHand
      ? "Retry it, or review the contract yourself and complete the step."
      : recheck
        ? "Retry it to re-check your changes."
        : "Retry it to get the review and its suggested changes.";
    return {
      state: "interrupted",
      title: recheck ? "The re-check of your changes was interrupted" : "The AI review was interrupted",
      body: `${why} ${next}`,
    };
  }
  return null;
}

export interface RetryOutcome {
  message: string;
  tone: "success" | "info" | "error";
}

/** What to say after the server answered a retry. */
export function retryOutcome(r: ReviewRetryResponse): RetryOutcome {
  switch (r.status) {
    case "queued":
      return {
        tone: "success",
        message: r.workflow_resumed
          ? "Review restarted. The workflow carries on by itself when it finishes."
          : "Review restarted. The suggested changes appear here when it finishes.",
      };
    case "already_running":
      return { tone: "info", message: "The review is already running." };
    case "not_needed":
      return { tone: "info", message: "This draft already has a review, so there is nothing to redo." };
    case "no_playbook":
      return { tone: "error", message: "No playbook covers this contract type, so there is nothing to review it against." };
    default:
      return { tone: "error", message: "The review could not be restarted." };
  }
}

/**
 * The workflow step that stopped because the review it waited for never finished: the run's current
 * step, an AI step, waiting on a person, and marked so by the server. `null` for any other step.
 */
export function interruptedReviewStep(run: WorkflowRun | null | undefined): WorkflowRunStep | null {
  if (!run || run.status !== "waiting") return null;
  const step = run.steps.find((s) => s.idx === run.current_index);
  if (!step || step.type !== "ai_task" || step.status !== "waiting_human") return null;
  return step.result?.review_state === "interrupted" ? step : null;
}

/**
 * Everything a restarted or finished review changes on the contract page: the review state itself, the
 * suggestions and issues, the risk band, the drafted fixes, and the workflow that was waiting on it.
 */
export const reviewQueryKeys = (contractId: string): unknown[][] => [
  ["review-status", contractId],
  ["contract-review", contractId],
  ["contract", contractId],
  ["contract-lifecycle", contractId],
  ["contract-stage-history", contractId],
  ["contract-edits", contractId],
  ["contract-devs", contractId],
  ["contract-risk", contractId],
  ["issue-fix-job", contractId],
  ["flow-run-contract", contractId],
  ["lifecycle-rows-contract", contractId],
  ["flow-run"],
];

export function invalidateReviewQueries(qc: QueryClient, contractId: string): void {
  for (const queryKey of reviewQueryKeys(contractId)) void qc.invalidateQueries({ queryKey });
}
