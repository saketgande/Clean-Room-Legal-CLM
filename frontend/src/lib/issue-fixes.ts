// The background job that writes a suggested change for each playbook issue that has none
// (backend: app/contract_files/issue_fixes.py). A playbook review starts it by itself; "Draft fixes
// for all" starts it by hand. These helpers say, in words, how far it has got and how it ended.

import type { IssueFixJob } from "@/lib/types";

/** Queued or running: changes are still being written. A job the server has given up on reads as failed. */
export const isFixJobActive = (job: IssueFixJob | null | undefined): boolean =>
  job?.status === "queued" || job?.status === "running";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What the Review tab says while it works. `total` is 0 until the job has counted its issues. */
export function draftingText(progress: { done: number; total: number } | null | undefined): string {
  return progress && progress.total > 0
    ? `Drafting suggested changes — ${progress.done} of ${progress.total} done.`
    : "Drafting suggested changes…";
}

export interface FixJobResult {
  text: string;
  tone: "success" | "info" | "error";
}

/** How a finished job went, for the one message the person gets when it ends. Never an internal error text:
 *  the server doesn't send one, and what to do next is the same whatever went wrong. */
export function describeFixJob(job: IssueFixJob): FixJobResult {
  if (job.status === "cancelled") return { text: "Drafting suggested changes was cancelled.", tone: "info" };
  if (job.drafted === 0 && job.no_change === 0) {
    // Nothing written and nothing answered: either every issue was handled meanwhile, or it did not work.
    if (job.failed === 0 && !job.stopped_early && job.status === "succeeded")
      return { text: "Every open issue already has a suggested change.", tone: "info" };
    return {
      text: "Couldn't draft suggested changes automatically. Use “Draft fixes for all” to try again.",
      tone: "error",
    };
  }

  const parts: string[] = [];
  if (job.drafted > 0)
    parts.push(
      `Drafted ${plural(job.drafted, "suggested change", "suggested changes")} — accept or reject ${job.drafted === 1 ? "it" : "them"} below.`,
    );
  if (job.no_change > 0)
    parts.push(`Aegis found nothing to change for ${plural(job.no_change, "issue", "issues")}.`);
  if (job.failed > 0)
    parts.push(
      `Couldn't draft a change for ${plural(job.failed, "issue", "issues")}: use “Draft fix with AI” on ${job.failed === 1 ? "it" : "each"} to try again.`,
    );
  if (job.stopped_early) parts.push("It stopped early; use “Draft fixes for all” to carry on with the rest.");
  else if (job.left_for_later > 0)
    parts.push(
      `${plural(job.left_for_later, "more open issue was", "more open issues were")} left for you: use “Draft fixes for all” for ${job.left_for_later === 1 ? "it" : "them"}.`,
    );

  const clean = job.failed === 0 && !job.stopped_early && job.left_for_later === 0 && job.drafted > 0;
  return { text: parts.join(" "), tone: clean ? "success" : "info" };
}
