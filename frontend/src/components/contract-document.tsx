"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, FileText, Loader2 } from "lucide-react";
import { contractsApi } from "@/lib/endpoints";
import { Badge, Button, Select } from "@/components/ui";
import { titleCase } from "@/lib/utils";
import type {
  ContractEditResponse,
  ContractTextSnapshotResponse,
} from "@/lib/types";

// Locate an edit's quoted original text in the shown document. Exact match
// first, then whitespace-tolerant (the snapshot may reflow line breaks).
function findSpan(
  haystack: string,
  needle: string,
): [number, number] | null {
  if (!needle) return null;
  const i = haystack.indexOf(needle);
  if (i !== -1) return [i, i + needle.length];

  const esc = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const tokens = needle.split(/\s+/).filter(Boolean);
  if (!tokens.length) return null;

  // Whitespace-tolerant, case-insensitive full match (snapshots reflow
  // lines; model quotes vary in casing).
  const tryRe = (toks: string[]): [number, number] | null => {
    try {
      const m = new RegExp(toks.map(esc).join("[\\s\\W]+"), "i").exec(
        haystack,
      );
      return m ? [m.index, m.index + m[0].length] : null;
    } catch {
      return null;
    }
  };
  const full = tryRe(tokens);
  if (full) return full;

  // Real model citations often differ from the source by punctuation,
  // smart quotes, or an inserted ellipsis. Fall back to anchoring on a
  // distinctive prefix so "click to verify" still jumps to the passage.
  for (const n of [14, 10, 6]) {
    if (tokens.length > n) {
      const span = tryRe(tokens.slice(0, n));
      if (span) return span;
    }
  }
  return null;
}

type Segment =
  | { kind: "text"; text: string }
  | { kind: "edit"; text: string; edit: ContractEditResponse };

function buildSegments(
  text: string,
  edits: ContractEditResponse[],
): Segment[] {
  const located = edits
    .filter((e) => e.original_text && e.status !== "rejected")
    .map((e) => {
      const span = findSpan(text, e.original_text as string);
      return span ? { start: span[0], end: span[1], edit: e } : null;
    })
    .filter((x): x is { start: number; end: number; edit: ContractEditResponse } =>
      Boolean(x),
    )
    .sort((a, b) => a.start - b.start);

  const segments: Segment[] = [];
  let cursor = 0;
  for (const { start, end, edit } of located) {
    if (start < cursor) continue; // drop overlap, keep earliest
    if (start > cursor)
      segments.push({ kind: "text", text: text.slice(cursor, start) });
    segments.push({ kind: "edit", text: text.slice(start, end), edit });
    cursor = end;
  }
  segments.push({ kind: "text", text: text.slice(cursor) });
  return segments;
}

/**
 * Renders the readable extracted text of a contract. Picks the best version to
 * show: the authoritative one if its snapshot has text, otherwise the most
 * recent version whose snapshot actually contains text (signed PDFs often have
 * an empty snapshot). A version switcher lets the user override. Reused by the
 * contract detail "Document" tab and the assistant workspace.
 */
export function ContractDocument({
  contractId,
  className,
  edits,
  activeEditId,
  onSelectEdit,
  highlightQuote,
}: {
  contractId: string;
  className?: string;
  edits?: ContractEditResponse[];
  activeEditId?: string | null;
  onSelectEdit?: (id: string) => void;
  highlightQuote?: string | null;
}) {
  const [override, setOverride] = useState<string | null>(null);

  const { data: contract } = useQuery({
    queryKey: ["contract", contractId],
    queryFn: () => contractsApi.get(contractId),
  });
  const { data: versions } = useQuery({
    queryKey: ["contract", contractId, "versions"],
    queryFn: () => contractsApi.versions(contractId),
  });

  // Candidate versions (those with a snapshot), authoritative first then newest.
  const candidates = useMemo(() => {
    const vs = (versions ?? []).filter((v) => v.text_snapshot_id);
    const authId = contract?.current_authoritative_version_id;
    return [...vs].sort((a, b) => {
      if (a.id === authId) return -1;
      if (b.id === authId) return 1;
      return b.version_number - a.version_number;
    });
  }, [versions, contract]);

  // Resolve the first candidate whose snapshot actually has text.
  const { data: resolved, isLoading } = useQuery({
    queryKey: [
      "contract",
      contractId,
      "doc",
      override,
      candidates.map((c) => c.id).join(","),
    ],
    enabled: candidates.length > 0,
    queryFn: async () => {
      const order = override
        ? [
            ...candidates.filter((c) => c.id === override),
            ...candidates.filter((c) => c.id !== override),
          ]
        : candidates;
      for (const v of order) {
        try {
          const snap = await contractsApi.versionText(contractId, v.id);
          if (snap?.text && snap.text.trim().length > 0)
            return { versionId: v.id, snap } as {
              versionId: string;
              snap: ContractTextSnapshotResponse;
            };
        } catch {
          /* try next */
        }
      }
      return null;
    },
  });

  const shownVersion = (versions ?? []).find(
    (v) => v.id === resolved?.versionId,
  );

  // Redlines anchor to the version they were created against. Render that base
  // text so accepted edits still show as struck original + inserted
  // replacement (rather than silently becoming the new authoritative text).
  const baseVersionId = useMemo(() => {
    if (!edits || edits.length === 0) return null;
    const counts = new Map<string, number>();
    for (const e of edits)
      counts.set(
        e.contract_version_id,
        (counts.get(e.contract_version_id) ?? 0) + 1,
      );
    return (
      [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null
    );
  }, [edits]);

  const { data: redlineBase } = useQuery({
    queryKey: ["contract", contractId, "redline-base", baseVersionId],
    enabled: !!baseVersionId,
    queryFn: () =>
      contractsApi.versionText(contractId, baseVersionId as string),
  });

  const redlineMode = Boolean(
    edits && edits.length > 0 && redlineBase?.text,
  );
  const docText = redlineMode
    ? (redlineBase as ContractTextSnapshotResponse).text
    : resolved?.snap?.text;

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!highlightQuote) return;
    const id = window.setTimeout(() => {
      scrollRef.current
        ?.querySelector("#cite-hl")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 120);
    return () => window.clearTimeout(id);
  }, [highlightQuote, docText]);

  return (
    <div
      className={
        "flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white " +
        (className ?? "")
      }
    >
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">
            {contract?.title ?? "Contract"}
          </p>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-400">
            {redlineMode ? (
              <Badge tone="amber">Redline · tracked changes</Badge>
            ) : shownVersion ? (
              <Badge tone="slate">V{shownVersion.version_number}</Badge>
            ) : null}
            {!redlineMode && shownVersion && (
              <span>{titleCase(shownVersion.source)}</span>
            )}
            {!redlineMode && resolved?.snap?.extraction_method && (
              <span>· {titleCase(resolved.snap.extraction_method)}</span>
            )}
            {redlineMode && (
              <span>
                {edits?.filter((e) => e.status === "proposed").length ?? 0}{" "}
                proposed
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {(versions ?? []).filter((v) => v.text_snapshot_id).length > 1 && (
            <Select
              value={resolved?.versionId ?? ""}
              onChange={(e) => setOverride(e.target.value)}
              className="h-8 w-36 text-xs"
            >
              {(versions ?? [])
                .filter((v) => v.text_snapshot_id)
                .sort((a, b) => b.version_number - a.version_number)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    V{v.version_number} · {titleCase(v.source)}
                  </option>
                ))}
            </Select>
          )}
          {shownVersion && (
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                contractsApi.downloadVersion(contractId, shownVersion.id)
              }
            >
              <Download className="h-3.5 w-3.5" />
              Original
            </Button>
          )}
        </div>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto bg-slate-50 px-8 py-8"
      >
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-sm text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading document…
          </div>
        ) : !docText ? (
          <div className="flex flex-col items-center justify-center gap-2 py-20 text-center text-slate-400">
            <FileText className="h-8 w-8" />
            <p className="text-sm">
              No extracted text available for this contract yet.
            </p>
            <p className="max-w-xs text-xs">
              Text appears once extraction/OCR completes. You can still download
              the original file above.
            </p>
          </div>
        ) : (
          <article className="mx-auto max-w-3xl whitespace-pre-wrap rounded-lg border border-slate-200 bg-white px-10 py-12 font-serif text-[15px] leading-7 text-slate-800 shadow-sm">
            {redlineMode && edits
              ? buildSegments(docText, edits).map((seg, i) => {
                  if (seg.kind === "text") return <span key={i}>{seg.text}</span>;
                  const e = seg.edit;
                  const active = activeEditId === e.id;
                  const ring = active
                    ? " ring-2 ring-offset-1 ring-brand-400 rounded"
                    : "";
                  if (e.status === "accepted") {
                    return (
                      <span
                        key={i}
                        id={`edit-${e.id}`}
                        onClick={() => onSelectEdit?.(e.id)}
                        className={"cursor-pointer" + ring}
                      >
                        <del className="bg-red-50 text-red-700 line-through decoration-red-400">
                          {seg.text}
                        </del>
                        {e.replacement_text && (
                          <ins className="bg-emerald-50 text-emerald-800 no-underline">
                            {e.replacement_text}
                          </ins>
                        )}
                      </span>
                    );
                  }
                  // proposed — highlight the area suggested for change
                  return (
                    <mark
                      key={i}
                      id={`edit-${e.id}`}
                      onClick={() => onSelectEdit?.(e.id)}
                      title={e.rationale ?? "Suggested change"}
                      className={
                        "cursor-pointer rounded bg-amber-100 text-amber-900 underline decoration-amber-400 decoration-dotted underline-offset-2" +
                        ring
                      }
                    >
                      {seg.text}
                    </mark>
                  );
                })
              : highlightQuote && docText
                ? (() => {
                    const span = findSpan(docText, highlightQuote);
                    if (!span) return docText;
                    return [
                      <span key="b">{docText.slice(0, span[0])}</span>,
                      <mark
                        key="h"
                        id="cite-hl"
                        className="rounded bg-amber-100 text-amber-900 underline decoration-amber-400 underline-offset-2"
                      >
                        {docText.slice(span[0], span[1])}
                      </mark>,
                      <span key="a">{docText.slice(span[1])}</span>,
                    ];
                  })()
                : docText}
          </article>
        )}
      </div>
    </div>
  );
}
