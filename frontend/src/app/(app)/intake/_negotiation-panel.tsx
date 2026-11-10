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
    <div className="space-y-4 rounded-xl border border-slate-200 bg-slate-100 p-5">
      <div className="flex items-center gap-2.5">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-brand-50 text-brand-700"><GitBranch className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1 text-[14px] font-semibold tracking-[-0.01em] text-slate-900">Negotiation</div>
        {rounds.length > 0 && <span className="rounded-full bg-brand-50 px-2.5 py-0.5 text-[11px] font-bold text-brand-700">Round {rounds.length}</span>}
      </div>

      <div className="flex flex-wrap items-center gap-2.5 text-xs">
        <span className="font-medium text-slate-500">Negotiating with</span>
        <div className="inline-flex rounded-lg bg-slate-50 p-0.5 ring-1 ring-slate-200">
          {(["counterparty", "internal"] as const).map((p) => (
            <button
              key={p}
              onClick={() => { setParty(p); setSent(null); }}
              aria-pressed={party === p}
              className={cn(
                "rounded-md px-3 py-1 text-[12px] font-semibold capitalize transition-all",
                party === p ? "bg-brand-600 text-white shadow-sm" : "text-slate-600 hover:text-slate-900",
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
            className="h-8 rounded-lg border border-slate-300 bg-slate-100 px-2.5 text-xs text-slate-900"
          >
            <option value="">Pick a team…</option>
            {(teams ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        )}
      </div>

      <ol className="relative space-y-3">
        {rounds.length === 0 ? (
          <li className="text-xs text-slate-400">No versions yet — draft the document first.</li>
        ) : (
          rounds.map((v, i) => (
            <li key={v.id} className="relative flex items-start gap-3 text-xs">
              {i < rounds.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%+4px)] w-0.5 bg-slate-200" aria-hidden />}
              <span className={cn("relative z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full font-mono text-[10.5px] font-bold ring-4 ring-slate-100", SRC[v.source]?.cls ?? "bg-slate-100 text-slate-600")}>
                v{v.version_number}
              </span>
              <span className="min-w-0 flex-1 pt-0.5">
                <span className="flex items-center gap-2">
                  <span className="text-[12.5px] font-semibold text-slate-900">{whoOf(v.source)}</span>
                  {i === rounds.length - 1 && <span className="rounded-full bg-brand-600 px-2 py-px text-[10px] font-bold text-white">Latest</span>}
                </span>
                <span className="mt-0.5 block truncate text-slate-500">{v.change_summary}</span>
              </span>
            </li>
          ))
        )}
      </ol>

      {diff && (
        <div className="rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200">
          Last round: <b className="text-success">+{diff.added}</b> / <b className="text-danger">−{diff.removed}</b> lines changed
          (v{diff.base_version_number} → v{diff.target_version_number}).
        </div>
      )}

      {sent && (
        <div className="rounded-xl border border-slate-200 bg-slate-100 p-2.5 text-xs">
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
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3">
          <button
            disabled={busy || send.isPending || !canSend}
            onClick={() => send.mutate()}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-brand-600 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" /> Send to {party === "counterparty" ? "counterparty" : (teamName || "team")}
          </button>
          <button
            disabled={busy || record.isPending}
            onClick={() => { setPendingSide("their"); fileRef.current?.click(); }}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-slate-100 px-3 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-50"
          >
            <Upload className="h-3.5 w-3.5" /> Record {party === "counterparty" ? "counterparty" : "internal"}&apos;s revision
          </button>
          <button
            disabled={busy || record.isPending}
            onClick={() => { setPendingSide("our"); fileRef.current?.click(); }}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-slate-100 px-3 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-50"
          >
            Record our counter
          </button>
          <button
            disabled={busy}
            onClick={onConverged}
            className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-lg border border-success bg-success-subtle px-3 text-xs font-semibold text-success transition-colors hover:bg-success hover:text-white disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" /> Accept &amp; converge
          </button>
          <input ref={fileRef} type="file" hidden onChange={onFile} />
        </div>
      )}
    </div>
  );
}
