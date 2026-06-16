"use client";

import { use, useCallback, useEffect, useState } from "react";
import { Scale, Loader2, CheckCircle2, XCircle } from "lucide-react";
import { approvalsApi } from "@/lib/endpoints";
import type { ApprovalReviewContext } from "@/lib/types";
import { Button } from "@/components/ui";

export default function ApprovalReviewPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [ctx, setCtx] = useState<ApprovalReviewContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [done, setDone] = useState<"approved" | "rejected" | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCtx(await approvalsApi.reviewByToken(token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "This approval link is invalid or has expired.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(decision: "approve" | "reject") {
    if (decision === "reject" && !comment.trim()) {
      setError("A reason is required to reject.");
      return;
    }
    setBusy(decision);
    setError(null);
    try {
      await approvalsApi.tokenDecide(token, decision, comment.trim() || undefined);
      setDone(decision === "approve" ? "approved" : "rejected");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not record your decision.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-3">
          <Scale className="h-5 w-5 text-brand-600" />
          <span className="text-sm font-semibold text-slate-800">AEGIS — Approval</span>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6">
        {loading ? (
          <div className="flex justify-center py-20 text-slate-400">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : done ? (
          <div className="mx-auto max-w-sm rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
            {done === "approved" ? (
              <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-500" />
            ) : (
              <XCircle className="mx-auto h-10 w-10 text-rose-500" />
            )}
            <p className="mt-3 text-sm font-medium text-slate-800">
              You {done} this contract.
            </p>
            <p className="mt-1 text-xs text-slate-500">Thanks — your decision has been recorded.</p>
          </div>
        ) : ctx ? (
          <div className="space-y-6">
            <div>
              <h1 className="text-xl font-semibold text-slate-900">{ctx.contract_title}</h1>
              <p className="mt-0.5 text-sm text-slate-500">
                {ctx.requester_name} requested your approval
                {ctx.due_at ? ` · due ${new Date(ctx.due_at).toLocaleDateString()}` : ""}
              </p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Document
              </h2>
              <pre className="max-h-[55vh] overflow-y-auto whitespace-pre-wrap font-sans text-sm text-slate-700">
                {ctx.document_text || "No document text available."}
                {ctx.document_truncated ? "\n\n… (truncated)" : ""}
              </pre>
            </div>

            {ctx.can_decide ? (
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="Add a comment (required to reject)…"
                  rows={3}
                  className="w-full resize-none rounded-md border border-slate-200 p-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
                />
                {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
                <div className="mt-3 flex justify-end gap-2">
                  <Button
                    variant="danger"
                    loading={busy === "reject"}
                    disabled={busy !== null}
                    onClick={() => decide("reject")}
                  >
                    Reject
                  </Button>
                  <Button
                    loading={busy === "approve"}
                    disabled={busy !== null}
                    onClick={() => decide("approve")}
                  >
                    Approve
                  </Button>
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                This request is no longer open for a decision (status: {ctx.status}).
              </div>
            )}
          </div>
        ) : (
          <div className="mx-auto max-w-sm rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
            <p className="text-sm text-rose-600">
              {error ?? "This approval link is invalid or has expired."}
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
