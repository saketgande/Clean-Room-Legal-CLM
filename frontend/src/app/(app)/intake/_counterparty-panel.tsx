"use client";

// Reviewer-side controls for a workflow "Send to counterparty" step: send the
// 7-day share link, see whether the counterparty has responded, read every
// comment they left, then decide (approve continues, request changes returns).

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, RotateCcw, Send } from "lucide-react";
import { Badge, Button, Input } from "@/components/ui";
import { workflowsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";

const STATE_LABEL = {
  not_sent: { text: "Not sent", tone: "slate" },
  sent: { text: "Waiting for counterparty", tone: "amber" },
  submitted: { text: "Counterparty submitted", tone: "green" },
  expired: { text: "Link expired", tone: "red" },
} as const;

export function CounterpartyPanel({
  runId,
  stepIdx,
  onChanged,
}: {
  runId: string;
  stepIdx: number;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["cp-state", runId],
    queryFn: () => workflowsApi.counterparty(runId),
    // Waiting on an external party isn't sub-minute time-critical either —
    // relaxed from 10s.
    refetchInterval: 30_000,
  });

  if (isLoading) return null;
  if (error || !data) {
    return (
      <p className="mt-2 text-xs text-rose-600">
        {error instanceof Error ? error.message : "Couldn't load counterparty status."}
      </p>
    );
  }

  const recipient = email || data.suggested_email || "";
  const meta = STATE_LABEL[data.state];

  async function run(key: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(key);
    try {
      await fn();
      notify(ok, "success");
      qc.invalidateQueries({ queryKey: ["cp-state", runId] });
      onChanged();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    } finally {
      setBusy(null);
    }
  }

  function send() {
    if (!recipient.trim()) return;
    const verb = data!.state === "not_sent" ? "Send" : "Re-send (the previous link will stop working)";
    if (!window.confirm(`${verb} the contract to ${recipient.trim()}? The link expires in ${data!.expiry_days} days, or when they submit.`)) return;
    void run("send", () => workflowsApi.sendToCounterparty(runId, {
      recipient_email: recipient.trim(),
      recipient_name: name.trim() || undefined,
      message: message.trim() || undefined,
    }), "Sent to counterparty");
  }

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-slate-700">Counterparty</span>
        <Badge tone={meta.tone as never}>{meta.text}</Badge>
      </div>

      {data.state !== "submitted" && (
        <div className="space-y-2">
          <Input type="email" value={recipient} onChange={(e) => setEmail(e.target.value)} placeholder="Counterparty email" />
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Recipient name (optional)" />
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={2}
            placeholder="Message to include (optional)"
            className="w-full resize-none rounded-md border border-slate-200 p-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
          />
          <Button size="sm" loading={busy === "send"} disabled={!recipient.trim()} onClick={send}>
            <Send className="h-3.5 w-3.5" />
            {data.state === "not_sent" ? "Send to counterparty" : "Re-send link"}
          </Button>
        </div>
      )}

      {data.state !== "not_sent" && (
        <p className="text-xs text-slate-500">
          Sent to {data.recipient_email}
          {data.state === "sent" && data.expires_at ? ` · expires ${new Date(data.expires_at).toLocaleString()}` : ""}
          {data.state === "submitted" && data.submitted_at ? ` · submitted ${new Date(data.submitted_at).toLocaleString()}` : ""}
        </p>
      )}

      <div>
        <p className="mb-1 text-xs font-semibold text-slate-700">
          Counterparty comments ({data.comments.length})
        </p>
        {data.comments.length === 0 ? (
          <p className="text-xs text-slate-400">No comments yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {data.comments.map((c) => (
              <li key={c.id} className="rounded-md border border-slate-200 bg-slate-50 p-2">
                <div className="flex items-center justify-between text-[11px] text-slate-500">
                  <span className="font-medium text-slate-700">{c.author_name}</span>
                  <span>{new Date(c.created_at).toLocaleString()}</span>
                </div>
                <p className="mt-0.5 whitespace-pre-wrap text-sm text-slate-700">{c.body}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-2">
        <Button
          size="sm"
          loading={busy === "approve"}
          onClick={() => void run("approve", () => workflowsApi.completeStep(runId, undefined, stepIdx), "Approved — workflow continues")}
        >
          <Check className="h-3.5 w-3.5" />
          Approve &amp; continue
        </Button>
        <Button
          size="sm"
          variant="outline"
          loading={busy === "return"}
          onClick={() => {
            if (window.confirm("Send this back to the earlier step for changes?"))
              void run("return", () => workflowsApi.returnStep(runId, undefined, "Counterparty requested changes"), "Returned for changes");
          }}
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Request changes
        </Button>
      </div>
    </div>
  );
}
