"use client";

// "The AI review is running" / "The AI review was interrupted — Retry review", shown where a person
// would otherwise be left guessing: above the Lifecycle tab, in the chat, and on a workflow step that
// stopped on it. The wording and rules are in lib/review-retry.ts; the server decides what is allowed.
//
// `ReviewNoticeView` only draws; `ContractReviewNotice` connects it to the contract's retry call.

import type { ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { contractsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { fmtRelative } from "@/lib/utils";
import { canRetryReview, invalidateReviewQueries, retryOutcome, reviewNoticeText } from "@/lib/review-retry";
import type { AutoReviewState } from "@/lib/types";

export interface ReviewNoticeViewProps {
  state: "running" | "interrupted";
  title: string;
  body: string;
  /** When a running review began (shown as "Started 3m ago"). */
  since?: string | null;
  /** The Retry button, or nothing when this person may not retry. */
  action?: ReactNode;
  /** Said instead of the button when there is none. */
  hint?: string;
}

export function ReviewNoticeView(p: ReviewNoticeViewProps) {
  return (
    <div className={`rvn ${p.state}`} role={p.state === "interrupted" ? "alert" : "status"} data-testid="review-notice">
      <div className="rvn-txt">
        <b>
          {p.state === "running" ? <span className="rvn-spin" aria-hidden="true" /> : null}
          {p.title}
        </b>
        <span>{p.body}</span>
        {p.state === "running" && p.since ? <span className="rvn-when">Started {fmtRelative(p.since)}</span> : null}
      </div>
      {p.action ? <div className="rvn-act">{p.action}</div> : p.hint ? <span className="rvn-hint">{p.hint}</span> : null}
    </div>
  );
}

export function ContractReviewNotice({ contractId, auto, byHand = false }: {
  contractId: string;
  auto: AutoReviewState | null | undefined;
  byHand?: boolean;
}) {
  const { user } = useAuth();
  const { notify } = useToast();
  const qc = useQueryClient();
  const retry = useMutation({
    mutationFn: () => contractsApi.retryReview(contractId),
    onSuccess: (r) => {
      const o = retryOutcome(r);
      notify(o.message, o.tone);
      invalidateReviewQueries(qc, contractId);
    },
    onError: (e) => {
      notify(e instanceof Error && e.message ? e.message : "Couldn't restart the review", "error");
      // The server may have changed things before refusing: show what is true now.
      invalidateReviewQueries(qc, contractId);
    },
  });

  const text = reviewNoticeText(auto, { byHand });
  if (!text) return null;
  const may = canRetryReview(user?.permissions);
  return (
    <ReviewNoticeView
      {...text}
      since={auto?.since}
      action={
        text.state === "interrupted" && may ? (
          <button className="btn sm pri" disabled={retry.isPending} onClick={() => retry.mutate()}>
            {retry.isPending ? "Restarting…" : "Retry review"}
          </button>
        ) : null
      }
      hint={text.state === "interrupted" && !may ? "Ask someone who can redline and run playbooks to retry it." : undefined}
    />
  );
}

/**
 * On a workflow step that stopped because its AI review was interrupted (see interruptedReviewStep): the
 * same notice with a Retry button that restarts the review and sets the step going again. `canAct` is
 * whether this person may act on the request at all; the server also checks who the step belongs to.
 */
export function StepReviewNotice({ auto, canAct, busy, onRetry }: {
  /** The contract's review state, when known: it says why the review stopped. */
  auto?: AutoReviewState | null;
  canAct: boolean;
  busy: boolean;
  onRetry: () => void;
}) {
  const { user } = useAuth();
  const text = reviewNoticeText(
    auto?.state === "interrupted" ? auto : { state: "interrupted", reason: null, since: null, job_id: null },
    { byHand: true },
  );
  if (!text) return null;
  const may = canAct && canRetryReview(user?.permissions);
  return (
    <ReviewNoticeView
      {...text}
      action={
        may ? (
          <button className="ministep" disabled={busy} onClick={onRetry}>
            {busy ? "Restarting…" : "Retry review"}
          </button>
        ) : null
      }
      hint={canAct && !may ? "Ask someone who can redline and run playbooks to retry it." : undefined}
    />
  );
}

export const REVIEW_NOTICE_CSS = `
.rvn{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;border:1px solid var(--border);background:var(--surface-2);color:var(--ink-2);border-radius:10px;padding:10px 12px;font-size:12.5px;line-height:1.5}
.rvn-txt{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1 1 220px}
.rvn-txt b{display:flex;align-items:center;gap:7px;color:var(--ink);font-weight:650}
.rvn-when,.rvn-hint{font-size:11.5px;color:var(--ink-3)}
.rvn-act{display:flex;align-items:center;gap:8px;flex:none}
.rvn.interrupted{border-color:color-mix(in srgb,var(--warn) 45%,var(--border));background:var(--warn-soft)}
.rvn.interrupted .rvn-txt b{color:var(--warn)}
.rvn-spin{width:10px;height:10px;flex:none;border-radius:50%;border:1.5px solid var(--ink-3);border-top-color:transparent;animation:rvnspin .8s linear infinite}
@keyframes rvnspin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.rvn-spin{animation:none}}
.step .rvn{margin:2px 0 8px}
`;
