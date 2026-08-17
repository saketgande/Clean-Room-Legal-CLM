"use client";

// Guided negotiation round-trip for a negotiate step. A negotiation is a series
// of ROUNDS on the contract's versions; each round the other party returns a
// redline (recorded as a new version), we diff it, then accept or counter. The
// "other party" can be the external counterparty OR an internal team; "Send"
// creates a counterparty share link or notifies the internal team.

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Upload, Check, Send, Link2 as LinkIcon } from "lucide-react";
import { contractsApi, intakeApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { cn } from "@/lib/utils";

// Version source → who produced that round, so the timeline reads as a negotiation.
const SRC: Record<string, { who: string; cls: string }> = {
  counterparty_revision: { who: "Counterparty", cls: "bg-info-subtle text-info" },
  user_redline: { who: "Internal party", cls: "bg-warning-subtle text-warning" },
  manual_upload: { who: "Our side", cls: "bg-success-subtle text-success" },
  manual_edit: { who: "Our side", cls: "bg-success-subtle text-success" },
  template_generated: { who: "Initial draft", cls: "bg-slate-100 text-slate-600" },
  assistant_generated: { who: "Initial draft", cls: "bg-slate-100 text-slate-600" },
  upload: { who: "Initial draft", cls: "bg-slate-100 text-slate-600" },
  playbook_redline: { who: "AI redline", cls: "bg-brand-50 text-brand-700" },
};
const whoOf = (s: string) => SRC[s]?.who ?? "Revision";

export function NegotiationPanel({ contractId, canAct, onConverged, busy }: {
  contractId: string;
  canAct: boolean;
  onConverged: () => void;
  busy: boolean;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [party, setParty] = useState<"counterparty" | "internal">("counterparty");
  const [teamId, setTeamId] = useState("");
  const [pendingSide, setPendingSide] = useState<"their" | "our" | null>(null);
  const [sent, setSent] = useState<{ kind: "link"; url: string } | { kind: "notified"; team: string } | null>(null);

  const { data: teams } = useQuery({ queryKey: ["intake-teams"], queryFn: intakeApi.teams });
  const teamName = teams?.find((t) => t.id === teamId)?.name;

  const { data: versions } = useQuery({
    queryKey: ["contract-versions", contractId],
    queryFn: () => contractsApi.versions(contractId),
  });
  const rounds = useMemo(
    () => (versions ?? []).slice().sort((a, b) => a.version_number - b.version_number),
    [versions],
  );
  const latest = rounds[rounds.length - 1];
  const prev = rounds[rounds.length - 2];
  const { data: diff } = useQuery({
    queryKey: ["version-diff", contractId, prev?.id, latest?.id],
    queryFn: () => contractsApi.versionDiff(contractId, prev!.id, latest!.id),
    enabled: !!(prev && latest),
  });

  const record = useMutation({
    mutationFn: ({ file, who }: { file: File; who: "counterparty" | "internal" | "us" }) =>
      contractsApi.logNegotiationRevision(contractId, file, who, {
        party_label: who === "internal" ? teamName || undefined : undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contract-versions", contractId] });
      notify("Revision recorded — new round", "success");
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Upload failed", "error"),
  });

  const send = useMutation({
    mutationFn: async () => {
      if (party === "counterparty") {
        const res = await contractsApi.createShare(contractId, { contract_version_id: latest?.id });
        return { kind: "link" as const, url: `${window.location.origin}/s/${res.token}` };
      }
      const res = await contractsApi.notifyTeam(contractId, teamId);
      return { kind: "notified" as const, team: res.team };
    },
    onSuccess: (r) => {
      setSent(r);
      notify(r.kind === "link" ? "Counterparty share link created" : `Notified ${r.team}`, "success");
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Send failed", "error"),
  });

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    const side = pendingSide;
    setPendingSide(null);
    if (!f) return;
    record.mutate({ file: f, who: side === "our" ? "us" : party });
  }

  const canSend = party === "counterparty" || !!teamId;

  return (
    <div className="space-y-3 rounded-lg border border-brand-200 bg-brand-50/40 p-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-brand-800">
        <GitBranch className="h-4 w-4" /> Negotiation
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-500">Negotiating with</span>
        <div className="inline-flex overflow-hidden rounded-md border border-slate-200">
          {(["counterparty", "internal"] as const).map((p) => (
            <button
              key={p}
              onClick={() => { setParty(p); setSent(null); }}
              className={cn(
                "px-2.5 py-1 font-medium capitalize",
                party === p ? "bg-brand-600 text-white" : "bg-slate-50 text-slate-600",
              )}
            >
              {p}
            </button>
          ))}
        </div>
        {party === "internal" && (
          <select
            value={teamId}
            onChange={(e) => { setTeamId(e.target.value); setSent(null); }}
            className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs"
          >
            <option value="">Pick a team…</option>
            {(teams ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        )}
      </div>

      <ol className="space-y-1">
        {rounds.length === 0 ? (
          <li className="text-xs text-slate-400">No versions yet — draft the document first.</li>
        ) : (
          rounds.map((v) => (
            <li key={v.id} className="flex items-center gap-2 text-xs">
              <span className="font-mono text-slate-400">v{v.version_number}</span>
              <span className={cn("rounded px-1.5 py-0.5 font-medium", SRC[v.source]?.cls ?? "bg-slate-100 text-slate-600")}>
                {whoOf(v.source)}
              </span>
              <span className="truncate text-slate-500">{v.change_summary}</span>
            </li>
          ))
        )}
      </ol>

      {diff && (
        <div className="text-xs text-slate-600">
          Last round: <b className="text-success">+{diff.added}</b> / <b className="text-danger">−{diff.removed}</b> lines changed
          (v{diff.base_version_number} → v{diff.target_version_number}).
        </div>
      )}

      {sent && (
        <div className="rounded-md border border-slate-200 bg-slate-50 p-2 text-xs">
          {sent.kind === "link" ? (
            <div className="flex items-center gap-2">
              <LinkIcon className="h-3.5 w-3.5 text-brand-600" />
              <span className="text-slate-500">Counterparty link:</span>
              <a href={sent.url} target="_blank" rel="noreferrer" className="truncate font-medium text-brand-700 underline">{sent.url}</a>
            </div>
          ) : (
            <span className="text-slate-600">Sent for internal review — <b>{sent.team}</b> notified.</span>
          )}
        </div>
      )}

      {canAct && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            disabled={busy || send.isPending || !canSend}
            onClick={() => send.mutate()}
            className="inline-flex items-center gap-1 rounded-md bg-brand-600 px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" /> Send to {party === "counterparty" ? "counterparty" : (teamName || "team")}
          </button>
          <button
            disabled={busy || record.isPending}
            onClick={() => { setPendingSide("their"); fileRef.current?.click(); }}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 disabled:opacity-50"
          >
            <Upload className="h-3.5 w-3.5" /> Record {party === "counterparty" ? "counterparty" : "internal"}&apos;s revision
          </button>
          <button
            disabled={busy || record.isPending}
            onClick={() => { setPendingSide("our"); fileRef.current?.click(); }}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 disabled:opacity-50"
          >
            Record our counter
          </button>
          <button
            disabled={busy}
            onClick={onConverged}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-success px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" /> Accept &amp; converge
          </button>
          <input ref={fileRef} type="file" hidden onChange={onFile} />
        </div>
      )}
      <div className="text-[11px] text-slate-400">
        Send the current version to the {party === "counterparty" ? "counterparty" : "internal team"}, record their redline as the next round, review the diff, then accept or counter.
      </div>
    </div>
  );
}
