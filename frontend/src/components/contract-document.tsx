"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Download,
  FileDown,
  FileText,
  GitCompare,
  Loader2,
  MessageSquarePlus,
  PenLine,
  X,
} from "lucide-react";
import { contractsApi } from "@/lib/endpoints";
import { splitBlocks, anchorEdit } from "@/lib/contract-blocks";
import { Badge, Button, Select } from "@/components/ui";
import { titleCase } from "@/lib/utils";
import { editCaution } from "@/lib/lifecycle-actions";
import { versionLabel } from "@/lib/version-label";
import { VersionCompare } from "@/components/version-compare";
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

// The block_id the backend stashed in a redline's anchor citation, if any —
// an exact target that survives quote drift and text reflow.
function citationBlockId(edit: ContractEditResponse): string | undefined {
  for (const c of edit.citation ?? []) {
    if (
      c &&
      typeof c === "object" &&
      (c as { type?: string }).type === "anchor"
    )
      return (c as { block_id?: string | null }).block_id ?? undefined;
  }
  return undefined;
}

function buildSegments(
  text: string,
  edits: ContractEditResponse[],
): Segment[] {
  const blocks = splitBlocks(text);
  const located = edits
    .filter((e) => e.original_text && e.status !== "rejected")
    .map((e) => {
      // Precise path: strike the exact quoted phrase when it's found verbatim.
      const span = findSpan(text, e.original_text as string);
      if (span) return { start: span[0], end: span[1], edit: e };
      // Fallback: the quote drifted from the source. Place the redline on the
      // clause the backend anchored it to (block_id), or the best-matching
      // block — instead of dropping it from the document view entirely.
      const blockId = anchorEdit(blocks, {
        block_id: citationBlockId(e),
        original_text: e.original_text,
      });
      const block = blockId ? blocks.find((b) => b.id === blockId) : undefined;
      if (block) {
        const i = text.indexOf(block.text);
        if (i >= 0)
          return { start: i, end: i + block.text.length, edit: e };
      }
      return null;
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
  variant,
  toolbar,
  mode = "markup",
  zoom = 1,
}: {
  contractId: string;
  className?: string;
  /** "page": the contract shown as a sheet of paper on a grey desk (the CLM workspace). */
  variant?: "page";
  /** Page variant: controls that float at the top of the desk (view mode, next change, zoom). */
  toolbar?: ReactNode;
  /** Page variant: Markup shows tracked changes; Final shows the text with every pending change
   *  accepted; Original shows it with none. Display only: nothing is decided by switching. */
  mode?: "markup" | "final" | "original";
  /** Page variant: text size, 1 = normal. */
  zoom?: number;
  edits?: ContractEditResponse[];
  activeEditId?: string | null;
  onSelectEdit?: (id: string) => void;
  onSelectComment?: (id: string) => void;
  highlightQuote?: string | null;
}) {
  const [override, setOverride] = useState<string | null>(null);
  const [comparing, setComparing] = useState(false);
  const qc = useQueryClient();
  const { notify } = useToast();
  const page = variant === "page";

  const { data: contract } = useQuery({
    queryKey: ["contract", contractId],
    queryFn: () => contractsApi.get(contractId),
  });
  // Same query the contract page's other tabs use (one request, shared): it says whether the text on screen
  // was approved, which decides what editing it means.
  const { data: reviewStatus } = useQuery({
    queryKey: ["review-status", contractId],
    queryFn: () => contractsApi.reviewStatus(contractId),
  });
  const caution = editCaution(contract?.lifecycle_stage, reviewStatus?.version_approval);
  const { data: versions } = useQuery({
    queryKey: ["contract", contractId, "versions"],
    queryFn: () => contractsApi.versions(contractId),
    // A fresh upload's text is extracted in the background: check again until it lands.
    refetchInterval: (query) => {
      const data = query.state.data;
      const pending = !!data && data.length > 0 && !data.some((v) => v.text_snapshot_id);
      return pending && query.state.dataUpdateCount < 100 ? 3000 : false;
    },
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
  const currentVersion = (versions ?? []).find(
    (v) => v.id === contract?.current_authoritative_version_id,
  );


  // Redlines anchor to the version they were created against, so while any is
  // still undecided we render that base text with the changes on it. Once every
  // change is decided the backend has made a new version from the accepted ones
  // — show that plain, not the old base with struck-out words forever.
  const pending = useMemo(() => (edits ?? []).filter((e) => e.status === "proposed"), [edits]);
  const baseVersionId = useMemo(() => {
    if (pending.length === 0) return null;
    const counts = new Map<string, number>();
    for (const e of pending)
      counts.set(
        e.contract_version_id,
        (counts.get(e.contract_version_id) ?? 0) + 1,
      );
    return (
      [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null
    );
  }, [pending]);
  const batch = useMemo(
    () => (edits ?? []).filter((e) => e.contract_version_id === baseVersionId),
    [edits, baseVersionId],
  );
  // Missing clauses have no original text to sit on; the backend appends them to
  // the end of the document, so that's where they show.
  const appended = batch.filter((e) => !e.original_text && e.replacement_text && e.status !== "rejected");

  const { data: redlineBase } = useQuery({
    queryKey: ["contract", contractId, "redline-base", baseVersionId],
    enabled: !!baseVersionId,
    queryFn: () =>
      contractsApi.versionText(contractId, baseVersionId as string),
  });

  const redlineMode = Boolean(baseVersionId && redlineBase?.text);
  const docText = redlineMode
    ? (redlineBase as ContractTextSnapshotResponse).text
    : resolved?.snap?.text;

  // buildSegments re-scans the whole document against every edit's original
  // text (findSpan) — memoized so it only re-runs when the text or edit set
  // actually changes, not on every render of this component (e.g. typing in
  // an unrelated comment box).
  const redlineSegments = useMemo(
    () => (redlineMode && docText ? buildSegments(docText, batch) : null),
    [redlineMode, docText, batch],
  );

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

  // "Show in document" picks a change: bring it into view, not just ring it —
  // the change is usually further down than the reader is.
  useEffect(() => {
    if (!activeEditId) return;
    const id = window.setTimeout(() => {
      scrollRef.current
        ?.querySelector(`#edit-${CSS.escape(activeEditId)}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 120);
    return () => window.clearTimeout(id);
  }, [activeEditId, redlineSegments]);

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
      notify(caution?.saved ?? "Saved as a new version", "success");
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
    const holder = scrollRef.current;
    if (!holder) return;
    // Count repeats inside the document text only: the title line and toolbar around it are not part of it.
    const article = holder.querySelector("article");
    if (!article || !article.contains(sel.getRangeAt(0).startContainer)) {
      setSelection(null);
      return;
    }
    const start = selectionOffset(docText, quote, article as HTMLElement, sel.getRangeAt(0));
    if (start < 0) {
      // Selection crossed redline markup or reflowed text — can't anchor it.
      setSelection(null);
      return;
    }
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
    // ClmWorkspace (the CLM editor) reads edits under this key — invalidate it
    // too so a proposed redline shows immediately instead of after a reload.
    qc.invalidateQueries({ queryKey: ["contract-edits", contractId] });
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
      notify("Redline proposed — accept or reject it in the Review tab", "success");
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
        (page
          ? "cd cd-page flex h-full flex-col overflow-hidden "
          : "flex h-full flex-col overflow-hidden rounded-md border border-slate-200 bg-slate-100 ") +
        (className ?? "")
      }
    >
      <div
        className={
          page
            ? "cd-head flex shrink-0 items-center justify-between gap-3"
            : "flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 px-4 py-3"
        }
      >
        <div className="min-w-0">
          <p className={"truncate text-[13px] font-semibold text-slate-900" + (page ? " cd-title" : "")}>
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
            <span className={"hidden text-slate-400 sm:inline" + (page ? " cd-hint" : "")}>
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
                  className="h-8 w-60 text-xs"
                  title="The version marked current is the text in use. Versions marked not in use are proposals that were never applied."
                >
                  {(versions ?? [])
                    .filter((v) => v.text_snapshot_id)
                    .sort((a, b) => b.version_number - a.version_number)
                    .map((v) => (
                      <option key={v.id} value={v.id}>
                        {versionLabel(v, currentVersion)}
                      </option>
                    ))}
                </Select>
              )}
              {(versions ?? []).filter((v) => v.text_snapshot_id).length > 1 && (
                <Button
                  size="sm"
                  variant="outline"
                  title="See what changed between any two versions"
                  onClick={() => setComparing(true)}
                >
                  <GitCompare className="h-3.5 w-3.5" />
                  Compare versions
                </Button>
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
                  {/* On the contract page "Original" is also a way of viewing the text, so this one says what it does. */}
                  {page ? "Download original" : "Original"}
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {editing && caution ? (
        <div
          role="note"
          className={page ? "cd-caution" : "mx-4 mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-[12px] text-amber-900"}
        >
          {caution.notice}
        </div>
      ) : null}

      <div
        ref={scrollRef}
        className={page ? "cd-scroll relative flex-1 overflow-y-auto" : "relative flex-1 overflow-y-auto bg-slate-100"}
        data-mode={page ? mode : undefined}
        onMouseUp={captureSelection}
      >
        {page && toolbar ? (
          <div className="cd-tool">
            <div className="cd-pill">{toolbar}</div>
          </div>
        ) : null}
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
            className={
              page
                ? "cd-edit block w-full resize-none whitespace-pre-wrap focus:outline-none"
                : "block h-full w-full resize-none whitespace-pre-wrap bg-slate-100 px-12 py-10 font-sans text-[15px] leading-7 text-slate-800 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand-200"
            }
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
          <div className={page ? "cd-sheet" : "contents"} style={page ? ({ "--z": zoom } as CSSProperties) : undefined}>
          {page ? (
            <div className="cd-ph">
              <span>{contract?.title ?? "Contract"}</span>
              <span>{shownVersion ? `V${shownVersion.version_number}` : ""}</span>
            </div>
          ) : null}
          <article
            className={
              page
                ? "cd-article whitespace-pre-wrap"
                : "w-full whitespace-pre-wrap px-12 py-10 font-sans text-[15px] leading-7 text-slate-800"
            }
          >
            {redlineSegments
              ? <>{redlineSegments.map((seg, i) => {
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
                        data-st="accepted"
                        onClick={() => onSelectEdit?.(e.id)}
                        className={"cursor-pointer" + ring}
                      >
                        {/* Accepted = this is now the wording; the rest of the
                            redline is still being decided. */}
                        <ins
                          title="Accepted change"
                          className="bg-success-subtle text-slate-800 no-underline"
                        >
                          {e.replacement_text ?? seg.text}
                        </ins>
                      </span>
                    );
                  }
                  // proposed — show the change inline (struck original +
                  // proposed replacement, dotted to signal "not yet accepted")
                  // so the redline is actually visible before accept/reject.
                  // No replacement = pure highlight fallback.
                  if (e.replacement_text) {
                    return (
                      <span
                        key={i}
                        id={`edit-${e.id}`}
                        data-st="proposed"
                        onClick={() => onSelectEdit?.(e.id)}
                        title={e.rationale ?? "Proposed change — click to review"}
                        className={"cursor-pointer" + ring}
                      >
                        <del className="bg-danger-subtle text-danger line-through decoration-danger/50">
                          {seg.text}
                        </del>
                        <ins className="bg-success-subtle text-success underline decoration-dotted decoration-success/60 underline-offset-2">
                          {e.replacement_text}
                        </ins>
                      </span>
                    );
                  }
                  return (
                    <mark
                      key={i}
                      id={`edit-${e.id}`}
                      data-st="proposed"
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
                })}
                {appended.map((e) => (
                  <span
                    key={e.id}
                    id={`edit-${e.id}`}
                    data-st={e.status}
                    onClick={() => onSelectEdit?.(e.id)}
                    title={e.rationale ?? "New clause — click to review"}
                    className={"cursor-pointer" + (activeEditId === e.id ? " ring-2 ring-offset-1 ring-brand-600 rounded" : "")}
                  >
                    {"\n\n"}
                    <ins
                      className={
                        e.status === "accepted"
                          ? "bg-success-subtle text-slate-800 no-underline"
                          : "bg-success-subtle text-success underline decoration-dotted decoration-success/60 underline-offset-2"
                      }
                    >
                      {e.replacement_text}
                    </ins>
                  </span>
                ))}</>
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
          </div>
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
      <VersionCompare
        contractId={contractId}
        versions={versions ?? []}
        current={currentVersion}
        open={comparing}
        onClose={() => setComparing(false)}
      />
    </div>
  );
}

/** Where the selected quote starts in the document text. A repeated passage
 *  ("Intentionally omitted", a notice address) is told apart by counting the
 *  quote in the rendered text before the selection; -1 when that can't settle it. */
function selectionOffset(docText: string, quote: string, holder: HTMLElement, range: Range): number {
  let seen = -1;
  try {
    const before = document.createRange();
    before.selectNodeContents(holder);
    before.setEnd(range.startContainer, range.startOffset);
    seen = before.toString().split(quote).length - 1;
  } catch {
    seen = -1;
  }
  let at = -1;
  for (let i = 0; seen >= 0 && i <= seen; i++) {
    at = docText.indexOf(quote, at + 1);
    if (at < 0) break;
  }
  if (at >= 0) return at;
  const first = docText.indexOf(quote);
  return first >= 0 && first === docText.lastIndexOf(quote) ? first : -1;
}

/** Styles for the `variant="page"` look: a white sheet on a grey desk, in a serif face, with the
 *  tracked changes drawn the way a word processor does. Scoped to the CLM workspace (`.clm`),
 *  which defines the colour variables used here. */
export const DOCUMENT_PAGE_CSS = `
.clm .cd-page{background:transparent}
.clm .cd-head{padding:10px 20px 0;min-height:0;flex-wrap:wrap;row-gap:6px}
.clm .cd-head .btn,.clm .cd-head button{white-space:nowrap}
.clm .cd-head span{white-space:nowrap}
.clm .cd-head .cd-title,.clm .cd-head .cd-hint{display:none}
.clm .cd-caution{margin:8px 20px 0;padding:8px 12px;border:1px solid var(--warn);background:var(--warn-soft);color:var(--ink);border-radius:8px;font-size:12.5px;line-height:1.45}
.clm .cd-scroll{background:var(--bg);padding:0 24px 80px}
.clm .cd-tool{position:sticky;top:12px;height:0;z-index:6;display:flex;justify-content:center;align-items:flex-start;pointer-events:none}
.clm .cd-pill{pointer-events:auto;display:flex;align-items:center;gap:6px;padding:4px;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:0 6px 20px -8px rgba(20,24,34,.35)}
.clm .cd-sheet{--z:1;width:min(100%,52em);font-size:calc(15px*var(--z));margin:64px auto 0;background:var(--paper);color:var(--paper-ink);font-family:"Tinos","Times New Roman",Times,serif;line-height:1.55;box-shadow:var(--sheet-shadow);border-radius:2px;min-height:40em;padding-bottom:3em}
.clm .cd-ph{display:flex;justify-content:space-between;gap:1em;padding:1.5em 3.6em 0;font-style:italic;font-size:.78em;color:var(--paper-muted)}
.clm .cd-article{padding:1.7em 3.6em 1em;font-family:inherit;font-size:1em;line-height:inherit;color:inherit}
.clm .cd-edit{width:min(100%,52em);margin:64px auto 0;min-height:40em;padding:3.4em 3.6em;background:var(--paper);color:var(--paper-ink);font:15px/1.55 "Tinos","Times New Roman",Times,serif;border-radius:2px;box-shadow:var(--sheet-shadow);display:block}
.clm .cd-article ::selection,.clm .cd-edit::selection{background:rgba(47,86,224,.24)}
.clm .cd-article del{color:var(--p-del);background:var(--p-del-bg);text-decoration:line-through;text-decoration-thickness:1px;border-radius:2px;padding:0 .08em}
.clm .cd-article ins{color:var(--p-ins);background:var(--p-ins-bg);text-decoration:none;border-radius:2px;padding:0 .08em;margin-left:.18em;box-shadow:inset 0 -1.5px 0 rgba(11,107,85,.5)}
.clm .cd-article [data-st="accepted"] ins{color:inherit;background:rgba(11,107,85,.1);box-shadow:none}
.clm .cd-article mark{color:inherit;text-decoration:none;border-radius:2px;background:var(--p-cmt);box-shadow:inset 0 -2px 0 var(--p-cmt-line)}
.clm .cd-article mark[data-st],.clm .cd-article #cite-hl{background:var(--p-hl);box-shadow:none}
.clm .cd-article [id^="edit-"]{--tw-ring-color:var(--accent)}
.clm .cd-scroll[data-mode="final"] [data-st="proposed"] del{display:none}
.clm .cd-scroll[data-mode="final"] [data-st="proposed"] ins{color:inherit;background:none;box-shadow:none;margin:0;padding:0}
.clm .cd-scroll[data-mode="original"] [data-st="proposed"] ins{display:none}
.clm .cd-scroll[data-mode="original"] [data-st="proposed"] del{color:inherit;background:none;text-decoration:none;padding:0}
@media (max-width:640px){.clm .cd-scroll{padding:0 12px 60px}.clm .cd-ph,.clm .cd-article{padding-left:1.5em;padding-right:1.5em}.clm .cd-edit{padding:2em 1.5em}}
`;
