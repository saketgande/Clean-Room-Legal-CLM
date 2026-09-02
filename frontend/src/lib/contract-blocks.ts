// Shared block model for the contract document.
//
// Why this exists: today redlines are re-anchored into the raw plaintext by
// string-matching each edit's `original_text` (see findSpan/buildSegments in
// contract-document.tsx). That fails whenever the model's quote differs from the
// source by a smart quote, whitespace, or an OCR artifact — the misplaced /
// missing redline bug.
//
// The fix is to stop locating a *character offset in the whole document* and
// instead identify *which block* an edit belongs to. A block is a coarse,
// stable target, so the match is far more forgiving. New edits will carry an
// explicit `block_id` (exact lookup, zero fuzz); legacy edits with only
// `original_text` still resolve here by a within-block match that's much more
// robust than a whole-document offset scan.

export type Block = { id: string; text: string };

// Cheap deterministic string hash (djb2). Deterministic == same text always
// yields the same id, so editing one block never renumbers the others.
function hash(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();

// Split extracted text into paragraph blocks on blank lines. IDs derive from
// content (not position) so a change to block N leaves every other block's id
// stable — the property that makes block-anchored edits durable across saves.
// A new clause at a line start: "2.1 ", "10. ", "(a) ", "Article 4",
// "Section 3". OCR text rarely leaves blank lines between numbered clauses, so
// splitting on those too keeps clauses separate. Must stay in sync with
// _CLAUSE_START in backend/app/contract_files/blocks.py so ids agree.
const CLAUSE_START =
  /(?<=\n)(?=\d+(?:\.\d+)*[.)]?\s|\((?:[a-z]|[ivxlcdm]+|\d+)\)\s|(?:Article|ARTICLE|Section|SECTION)\s)/;

export function splitBlocks(text: string): Block[] {
  const seen = new Map<string, number>();
  return text
    .split(/\n\s*\n/)
    .flatMap((chunk) => chunk.split(CLAUSE_START))
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => {
      const base = "b" + hash(norm(t));
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      return { id: n === 0 ? base : `${base}-${n}`, text: t };
    });
}

// Words with edge punctuation stripped, so "invoice." matches "invoice" and
// "(30)" matches "30" — the drift that makes a model quote miss.
const tokens = (s: string) =>
  norm(s)
    .split(" ")
    .map((w) => w.replace(/^[.,;:()[\]{}"'`’]+|[.,;:()[\]{}"'`’]+$/g, ""))
    .filter(Boolean);

// Resolve an edit to the block it applies to. Prefer an explicit block_id
// (exact, no fuzz). Fall back for legacy edits: the block that contains the
// quote verbatim (whitespace-normalised), else the best token-overlap block
// above a confidence floor. Returns null when nothing is a credible match —
// callers should surface "couldn't place this redline" rather than guess.
export function anchorEdit(
  blocks: Block[],
  edit: { block_id?: string | null; original_text?: string | null },
): string | null {
  if (edit.block_id && blocks.some((b) => b.id === edit.block_id))
    return edit.block_id;

  const needle = norm(edit.original_text ?? "");
  if (!needle) return null;

  for (const b of blocks) if (norm(b.text).includes(needle)) return b.id;

  // Fuzzy fallback: overlap coefficient (shared / smaller set), not Jaccard —
  // the block is usually longer than the quote, and Jaccard would punish that
  // length even when every quote word is present. Floor 0.6 so an unrelated
  // block never absorbs an edit. Matches the backend anchor (blocks.py).
  const nt = new Set(tokens(needle));
  if (!nt.size) return null;
  let best: { id: string; score: number } | null = null;
  for (const b of blocks) {
    const bt = new Set(tokens(b.text));
    let inter = 0;
    for (const t of nt) if (bt.has(t)) inter++;
    const score = inter / Math.min(nt.size, bt.size);
    if (!best || score > best.score) best = { id: b.id, score };
  }
  return best && best.score >= 0.6 ? best.id : null;
}
