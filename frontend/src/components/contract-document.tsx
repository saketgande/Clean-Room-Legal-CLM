"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Download,
  FileDown,
  FileText,
  Loader2,
  MessageSquarePlus,
  PenLine,
  X,
} from "lucide-react";
import { contractsApi } from "@/lib/endpoints";
import { Badge, Button, Select } from "@/components/ui";
import { titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type {
  ContractComment,
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

type CommentAnchor = { start?: number; end?: number; quote?: string };

type CommentSegment =
  | { kind: "text"; text: string }
  | { kind: "comment"; text: string; comment: ContractComment };

// Mark text ranges that carry anchored comments (unresolved only), so the
// discussion is visible where it belongs — in the document itself.
function buildCommentSegments(
  text: string,
  comments: ContractComment[],
): CommentSegment[] {
  const located = comments
    .map((c) => {
      const anchor = (c.anchor ?? {}) as CommentAnchor;
      const quote = anchor.quote;
      if (!quote) return null;
      // Trust stored offsets when they still match; re-locate otherwise.
      if (
        typeof anchor.start === "number" &&
        text.slice(anchor.start, anchor.start + quote.length) === quote
      )
        return { start: anchor.start, end: anchor.start + quote.length, comment: c };
      const span = findSpan(text, quote);
      return span ? { start: span[0], end: span[1], comment: c } : null;
    })
    .filter(
      (x): x is { start: number; end: number; comment: ContractComment } =>
        Boolean(x),
    )
    .sort((a, b) => a.start - b.start);

  const segments: CommentSegment[] = [];
  let cursor = 0;
  for (const { start, end, comment } of located) {
    if (start < cursor) continue;
    if (start > cursor)
      segments.push({ kind: "text", text: text.slice(cursor, start) });
    segments.push({ kind: "comment", text: text.slice(start, end), comment });
    cursor = end;
  }
  segments.push({ kind: "text", text: text.slice(cursor) });
  return segments;
}

type SelectionInfo = { quote: string; start: number; x: number; y: number };

/**
 * Renders the readable extracted text of a contract. Picks the best version to
 * show: the authoritative one if its snapshot has text, otherwise the most
 * recent version whose snapshot actually contains text (signed PDFs often have
 * an empty snapshot). A version switcher lets the user override. Selecting text
 * opens a popover to propose a manual redline or attach an anchored comment.
 * Reused by the contract detail "Document" tab and the assistant workspace.
 */
export function ContractDocument({
  contractId,
  className,
  edits,
  activeEditId,
  onSelectEdit,
  onSelectComment,
  highlightQuote,
}: {
  contractId: string;
  className?: string;
  edits?: ContractEditResponse[];
  activeEditId?: string | null;
  onSelectEdit?: (id: string) => void;
  onSelectComment?: (id: string) => void;
  highlightQuote?: string | null;
}) {
  const [override, setOverride] = useState<string | null>(null);
  const qc = useQueryClient();
  const { notify } = useToast();

  const { data: contract } = useQuery({
    queryKey: ["contract", contractId],
    queryFn: () => contractsApi.get(contractId),
  });
  const { data: versions } = useQuery({
    queryKey: ["contract", contractId, "versions"],
    queryFn: () => contractsApi.versions(contractId),
  });
  const { data: comments } = useQuery({
    queryKey: ["contract-comments", contractId],
    queryFn: () => contractsApi.comments(contractId),
  });
  const anchoredComments = useMemo(
    () =>
      (comments ?? []).filter(
        (c) => !c.resolved && (c.anchor as CommentAnchor | null)?.quote,
      ),
    [comments],
  );

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

  // ---- Word-style direct editing ------------------------------------------
  // Edit turns the page into a writable surface; Save creates a NEW
  // authoritative version (manual_edit) so history stays immutable.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saveSummary, setSaveSummary] = useState("");
  const [saving, setSaving] = useState(false);
  const authoritativeText = resolved?.snap?.text ?? "";

  function startEditing() {
    setDraft(authoritativeText);
    setSaveSummary("");
    setSelection(null);
    setEditing(true);
  }

  function cancelEditing() {
    if (
      draft !== authoritativeText &&
      !window.confirm("Discard your unsaved changes?")
    )
      return;
    setEditing(false);
  }

  async function saveEdit() {
    if (!draft.trim() || draft === authoritativeText) return;
    setSaving(true);
    try {
      await contractsApi.updateText(contractId, {
        text: draft,
        change_summary: saveSummary.trim() || undefined,
      });
      qc.invalidateQueries({ queryKey: ["contract", contractId] });
      qc.invalidateQueries({ queryKey: ["review-status", contractId] });
      notify("Saved as a new version", "success");
      setEditing(false);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save", "error");
    } finally {
      setSaving(false);
    }
  }

  // ---- Text selection → propose redline / anchored comment ---------------
  const [selection, setSelection] = useState<SelectionInfo | null>(null);
  const [popMode, setPopMode] = useState<"menu" | "redline" | "comment">(
    "menu",
  );
  const [replacement, setReplacement] = useState("");
  const [note, setNote] = useState("");
  const [visibility, setVisibility] = useState<"internal" | "shared">(
    "internal",
  );
  const [busy, setBusy] = useState(false);

  function captureSelection() {
    if (editing || !docText) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) {
      setSelection(null);
      return;
    }
    const quote = sel.toString().trim();
    if (quote.length < 4 || quote.length > 4000) {
      setSelection(null);
      return;
    }
    const start = docText.indexOf(quote);
    if (start < 0) {
      // Selection crossed redline markup or reflowed text — can't anchor it.
      setSelection(null);
      return;
    }
    const holder = scrollRef.current;
    if (!holder) return;
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const box = holder.getBoundingClientRect();
    const x = Math.min(
      Math.max(rect.left - box.left + rect.width / 2, 150),
      box.width - 150,
    );
    const y = rect.bottom - box.top + holder.scrollTop + 8;
    setPopMode("menu");
    setReplacement(quote);
    setNote("");
    setSelection({ quote, start, x, y });
  }

  function invalidateAfterMutation() {
    qc.invalidateQueries({ queryKey: ["contract", contractId, "edits"] });
    qc.invalidateQueries({ queryKey: ["contract", contractId, "versions"] });
    qc.invalidateQueries({ queryKey: ["contract-comments", contractId] });
    qc.invalidateQueries({ queryKey: ["review-status", contractId] });
  }

  async function submitRedline() {
    if (!selection) return;
    setBusy(true);
    try {
      await contractsApi.proposeEdit(contractId, {
        original_text: selection.quote,
        replacement_text: replacement,
        rationale: note.trim() || undefined,
        start_hint: selection.start,
      });
      invalidateAfterMutation();
      notify("Redline proposed — review it in the Redlines panel", "success");
      setSelection(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to propose", "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitComment() {
    if (!selection || !note.trim()) return;
    setBusy(true);
    try {
      const created = await contractsApi.addComment(contractId, {
        body: note.trim(),
        visibility,
        anchor: {
          start: selection.start,
          end: selection.start + selection.quote.length,
          quote: selection.quote,
        },
      });
      invalidateAfterMutation();
      notify("Comment anchored to the text", "success");
      setSelection(null);
      onSelectComment?.(created.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to comment", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={
        "flex h-full flex-col overflow-hidden rounded-md border border-slate-200 bg-slate-100 " +
        (className ?? "")
      }
    >
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold text-slate-900">
            {contract?.title ?? "Contract"}
          </p>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
            {editing ? (
              <Badge tone="violet">Editing — saves as a new version</Badge>
            ) : redlineMode ? (
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
            <span className="hidden text-slate-400 sm:inline">
              · select text to redline or comment
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {editing ? (
            <>
              <input
                value={saveSummary}
                onChange={(e) => setSaveSummary(e.target.value)}
                placeholder="Describe the change (optional)"
                className="h-8 w-52 rounded border border-slate-300 bg-slate-50 px-2 text-xs focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-200"
              />
              <Button
                size="sm"
                loading={saving}
                disabled={!draft.trim() || draft === authoritativeText}
                onClick={saveEdit}
              >
                <Check className="h-3.5 w-3.5" />
                Save
              </Button>
              <Button size="sm" variant="outline" onClick={cancelEditing}>
                Cancel
              </Button>
            </>
          ) : (
            <>
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
              {authoritativeText && (
                <Button
                  size="sm"
                  title="Edit the document directly — saves as a new version"
                  onClick={startEditing}
                >
                  <PenLine className="h-3.5 w-3.5" />
                  Edit
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                title="Export the current text (with accepted redlines) as .docx"
                onClick={() =>
                  contractsApi.exportDocx(
                    contractId,
                    `${contract?.title ?? "contract"}.docx`,
                  )
                }
              >
                <FileDown className="h-3.5 w-3.5" />
                Export .docx
              </Button>
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
            </>
          )}
        </div>
      </div>

      <div
        ref={scrollRef}
        className="relative flex-1 overflow-y-auto bg-slate-100"
        onMouseUp={captureSelection}
      >
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-sm text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading document…
          </div>
        ) : editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
            spellCheck={false}
            aria-label="Edit contract text"
            className="block h-full w-full resize-none whitespace-pre-wrap bg-slate-100 px-12 py-10 font-sans text-[15px] leading-7 text-slate-800 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand-200"
          />
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
          <article className="w-full whitespace-pre-wrap px-12 py-10 font-sans text-[15px] leading-7 text-slate-800">
            {redlineMode && edits
              ? buildSegments(docText, edits).map((seg, i) => {
                  if (seg.kind === "text") return <span key={i}>{seg.text}</span>;
                  const e = seg.edit;
                  const active = activeEditId === e.id;
                  const ring = active
                    ? " ring-2 ring-offset-1 ring-brand-600 rounded"
                    : "";
                  if (e.status === "accepted") {
                    return (
                      <span
                        key={i}
                        id={`edit-${e.id}`}
                        onClick={() => onSelectEdit?.(e.id)}
                        className={"cursor-pointer" + ring}
                      >
                        <del className="bg-danger-subtle text-danger line-through decoration-danger/60">
                          {seg.text}
                        </del>
                        {e.replacement_text && (
                          <ins className="bg-success-subtle text-success no-underline">
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
                        "cursor-pointer rounded bg-warning-subtle text-warning underline decoration-warning/60 decoration-dotted underline-offset-2" +
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
                        className="rounded bg-warning-subtle text-warning underline decoration-warning/60 underline-offset-2"
                      >
                        {docText.slice(span[0], span[1])}
                      </mark>,
                      <span key="a">{docText.slice(span[1])}</span>,
                    ];
                  })()
                : anchoredComments.length > 0
                  ? buildCommentSegments(docText, anchoredComments).map(
                      (seg, i) => {
                        if (seg.kind === "text")
                          return <span key={i}>{seg.text}</span>;
                        return (
                          <mark
                            key={i}
                            id={`anchor-comment-${seg.comment.id}`}
                            onClick={() => onSelectComment?.(seg.comment.id)}
                            title={`${seg.comment.author_name}: ${seg.comment.body.slice(0, 120)}`}
                            className="cursor-pointer rounded bg-info-subtle text-info underline decoration-info/60 decoration-dotted underline-offset-2"
                          >
                            {seg.text}
                          </mark>
                        );
                      },
                    )
                  : docText}
          </article>
        )}

        {selection && (
          <div
            className="absolute z-20 w-[19rem] -translate-x-1/2 rounded-lg border border-slate-200 bg-slate-100 p-2 shadow-pop"
            style={{ left: selection.x, top: selection.y }}
            onMouseUp={(e) => e.stopPropagation()}
          >
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <p className="truncate text-[11px] text-slate-500">
                “{selection.quote.slice(0, 60)}
                {selection.quote.length > 60 ? "…" : ""}”
              </p>
              <button
                onClick={() => setSelection(null)}
                className="shrink-0 rounded p-0.5 text-slate-500 hover:bg-slate-200 hover:text-slate-700"
                aria-label="Close"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {popMode === "menu" && (
              <div className="flex gap-1.5">
                <Button
                  size="sm"
                  className="flex-1"
                  onClick={() => setPopMode("redline")}
                >
                  <PenLine className="h-3.5 w-3.5" />
                  Propose change
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="flex-1"
                  onClick={() => setPopMode("comment")}
                >
                  <MessageSquarePlus className="h-3.5 w-3.5" />
                  Comment
                </Button>
              </div>
            )}

            {popMode === "redline" && (
              <div className="space-y-1.5">
                <textarea
                  value={replacement}
                  onChange={(e) => setReplacement(e.target.value)}
                  rows={3}
                  placeholder="Replacement text (leave empty to delete)"
                  className="w-full resize-none rounded border border-slate-300 bg-slate-50 p-2 text-xs focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-200"
                />
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Rationale (optional)"
                  className="w-full rounded border border-slate-300 bg-slate-50 p-2 text-xs focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-200"
                />
                <Button
                  size="sm"
                  className="w-full"
                  loading={busy}
                  onClick={submitRedline}
                >
                  Propose redline
                </Button>
              </div>
            )}

            {popMode === "comment" && (
              <div className="space-y-1.5">
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={3}
                  placeholder="Comment on this passage…"
                  className="w-full resize-none rounded border border-slate-300 bg-slate-50 p-2 text-xs focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-200"
                />
                <div className="flex items-center gap-1.5">
                  <Select
                    value={visibility}
                    onChange={(e) =>
                      setVisibility(e.target.value as "internal" | "shared")
                    }
                    className="h-8 flex-1 text-xs"
                  >
                    <option value="internal">Internal</option>
                    <option value="shared">Shared with counterparty</option>
                  </Select>
                  <Button
                    size="sm"
                    loading={busy}
                    disabled={!note.trim()}
                    onClick={submitComment}
                  >
                    Comment
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
