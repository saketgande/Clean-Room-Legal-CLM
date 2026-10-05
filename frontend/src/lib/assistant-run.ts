// What a chat says about an answer that didn't finish. Answers are produced
// in the background, so a chat can be reopened after its answer failed, was
// interrupted (the worker stopped), or was stopped by the user.
import type { AssistantRunSummary } from "./types";

export interface RunNotice {
  message: string;
  /** The question to send again, when it can be retried. */
  retryText: string | null;
}

/** Explain a chat's latest answer when it didn't finish (else null). */
export function runNoticeFor(
  run: AssistantRunSummary | null | undefined,
  msgs: { id: string; content: string }[],
): RunNotice | null {
  if (!run) return null;
  const question = msgs.find((m) => m.id === run.user_message_id)?.content ?? null;
  if (run.status === "failed" || run.status === "interrupted") {
    const base = run.has_answer
      ? "This answer was cut off before it finished."
      : run.error_message || "This answer didn't finish.";
    return { message: base, retryText: question };
  }
  if (run.status === "cancelled" && !run.has_answer)
    return { message: "You stopped this answer before Aegis wrote anything.", retryText: question };
  return null;
}
