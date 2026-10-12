"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { contractsApi } from "@/lib/endpoints";
import { CenterSpinner, EmptyState, LoadError, Modal, Select } from "@/components/ui";
import { versionLabel } from "@/lib/version-label";
import type { ContractVersionResponse, VersionDiffLine } from "@/lib/types";

// Unchanged lines kept around each change; the server collapses the rest into one "N unchanged lines" line.
const CONTEXT_LINES = 3;

/** What each kind of line looks like. The words are for screen readers and for anyone who cannot tell the
 *  colours apart; the sign is the usual diff sign. */
const KIND: Record<"add" | "remove" | "context", { sign: string; word: string; row: string }> = {
  add: { sign: "+", word: "Added", row: "bg-success-subtle text-slate-900" },
  remove: { sign: "−", word: "Removed", row: "bg-danger-subtle text-slate-900 line-through decoration-danger/50" },
  context: { sign: "", word: "Unchanged", row: "text-slate-600" },
};

function DiffRow({ line }: { line: VersionDiffLine }) {
  if (line.type === "gap") {
    return (
      <li data-kind="gap" className="px-3 py-1 text-center text-[11px] italic text-slate-400">
        … {line.text} …
      </li>
    );
  }
  const k = KIND[line.type];
  return (
    <li data-kind={line.type} className={`flex gap-2 px-3 py-1 text-[13px] leading-relaxed ${k.row}`}>
      <span aria-hidden="true" className="w-3 shrink-0 select-none font-mono font-semibold">{k.sign}</span>
      <span className="sr-only">{k.word}: </span>
      <span className="min-w-0 whitespace-pre-wrap break-words">{line.text}</span>
    </li>
  );
}

/** Pick two versions of a contract and see which lines were added and removed going from the first to the
 *  second. Only versions whose text has been read can be compared. */
export function VersionCompare({
  contractId,
  versions,
  current,
  open,
  onClose,
}: {
  contractId: string;
  versions: ContractVersionResponse[];
  current: ContractVersionResponse | null | undefined;
  open: boolean;
  onClose: () => void;
}) {
  // The picks live in CompareBody, which Modal only mounts while it is open, so every opening starts
  // from the defaults instead of whatever was left on screen when it was closed.
  return (
    <Modal open={open} onClose={onClose} title="Compare versions" size="xl">
      <CompareBody contractId={contractId} versions={versions} current={current} />
    </Modal>
  );
}

function CompareBody({
  contractId,
  versions,
  current,
}: {
  contractId: string;
  versions: ContractVersionResponse[];
  current: ContractVersionResponse | null | undefined;
}) {
  // Newest first, like the version dropdown.
  const candidates = useMemo(
    () => versions.filter((v) => v.text_snapshot_id).sort((a, b) => b.version_number - a.version_number),
    [versions],
  );
  const ids = useMemo(() => new Set(candidates.map((v) => v.id)), [candidates]);

  // What the person picked. A pick that is no longer on offer (the list changed) falls back to the default.
  const [pickedFrom, setPickedFrom] = useState<string | null>(null);
  const [pickedTo, setPickedTo] = useState<string | null>(null);

  const defaultTo = (current && ids.has(current.id) ? current : candidates[0])?.id ?? "";
  const toId = pickedTo && ids.has(pickedTo) ? pickedTo : defaultTo;
  const toNumber = candidates.find((v) => v.id === toId)?.version_number ?? 0;
  const defaultFrom = (candidates.find((v) => v.version_number < toNumber) ?? candidates.find((v) => v.id !== toId))?.id ?? "";
  const fromId = pickedFrom && ids.has(pickedFrom) ? pickedFrom : defaultFrom;

  const canCompare = candidates.length >= 2;
  const same = canCompare && fromId !== "" && fromId === toId;

  const diff = useQuery({
    queryKey: ["contract", contractId, "version-diff", fromId, toId],
    enabled: canCompare && !!fromId && !!toId && !same,
    queryFn: () => contractsApi.versionDiff(contractId, fromId, toId, CONTEXT_LINES),
  });

  const label = (v: ContractVersionResponse) => versionLabel(v, current);
  const fromVersion = candidates.find((v) => v.id === fromId);
  const toVersion = candidates.find((v) => v.id === toId);

  return (
    <>
      {!canCompare ? (
        <EmptyState title="This contract has fewer than two versions with readable text." description="A version can be compared once its text has been read." />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="compare-from" className="mb-1 block text-xs font-medium text-slate-600">From</label>
              <Select id="compare-from" value={fromId} onChange={(e) => setPickedFrom(e.target.value)}>
                {candidates.map((v) => <option key={v.id} value={v.id}>{label(v)}</option>)}
              </Select>
            </div>
            <div>
              <label htmlFor="compare-to" className="mb-1 block text-xs font-medium text-slate-600">To</label>
              <Select id="compare-to" value={toId} onChange={(e) => setPickedTo(e.target.value)}>
                {candidates.map((v) => <option key={v.id} value={v.id}>{label(v)}</option>)}
              </Select>
            </div>
          </div>

          {same ? (
            <p className="text-sm text-slate-600">Pick two different versions to compare.</p>
          ) : diff.isLoading ? (
            <CenterSpinner label="Comparing the two versions…" />
          ) : diff.isError ? (
            <LoadError error={diff.error} what="the comparison" onRetry={() => void diff.refetch()} />
          ) : diff.data ? (
            diff.data.added === 0 && diff.data.removed === 0 ? (
              <EmptyState
                title="No differences"
                description={fromVersion && toVersion ? `The text of ${label(fromVersion)} and ${label(toVersion)} is the same.` : undefined}
              />
            ) : (
              <>
                <p className="flex gap-3 text-sm">
                  <span className="font-medium text-success">{diff.data.added} added</span>
                  <span className="font-medium text-danger">{diff.data.removed} removed</span>
                  <span className="text-slate-500">going from V{diff.data.base_version_number} to V{diff.data.target_version_number}</span>
                </p>
                {diff.data.truncated ? (
                  <div role="note" className="rounded-md border border-warning/30 bg-warning-subtle px-3 py-2 text-[13px] text-warning">
                    This comparison was cut off because it is very long, so later changes may not be shown. The counts above include them.
                  </div>
                ) : null}
                <ol
                  aria-label={`Changes from V${diff.data.base_version_number} to V${diff.data.target_version_number}`}
                  className="divide-y divide-slate-200 overflow-hidden rounded-md border border-slate-200 bg-slate-50"
                >
                  {diff.data.lines.map((line, i) => <DiffRow key={i} line={line} />)}
                </ol>
              </>
            )
          ) : null}
        </div>
      )}
    </>
  );
}
