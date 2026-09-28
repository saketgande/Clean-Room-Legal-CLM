"""Block model for a contract's extracted text.

The redline generator used to locate the clause an AI deviation refers to by
string-matching the model's (often paraphrased) quote back into the raw text
(_find_phrase -> _align_phrase). When the quote drifts, the match fails and the
change floats to the end of the document as an unlocated insertion — the "AI
edit landed in the wrong place" bug.

Blocks fix this by changing the question from "where is this exact string?"
(brittle char offset) to "which clause is this?" (coarse, robust). We snap the
model's quote to a whole real block, then strike that block verbatim — no
guessing, and it can never land mid-sentence or float unapplied.

Mirrors frontend/src/lib/contract-blocks.ts (same split, hash, and anchor
logic). ponytail: id parity with the TS isn't exercised yet — the backend uses
blocks end-to-end internally. Add a cross-language parity test the day the
frontend renders redlines by stored block_id.
"""

from __future__ import annotations

import re
from typing import NamedTuple

_WS = re.compile(r"\s+")


class Block(NamedTuple):
    id: str
    text: str


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def _tokens(s: str) -> set[str]:
    # Words with edge punctuation stripped, so "invoice." matches "invoice"
    # and "(30)" matches "30" — the drift that makes a model quote miss.
    out = set()
    for w in _norm(s).split():
        w = w.strip(".,;:()[]{}\"'`")
        if w:
            out.add(w)
    return out


def _b36(n: int) -> str:
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def _hash(s: str) -> str:
    # djb2, kept to unsigned 32 bits per iteration to match the JS ((h<<5)+h+c)|0.
    h = 5381
    for ch in s:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return _b36(h)


# A new clause starting at the beginning of a line: "2.1 ", "10. ", "(a) ",
# "Article 4", "Section 3". OCR output rarely leaves blank lines between
# numbered clauses, so splitting on blank lines alone merges a whole article
# into one giant block; splitting on these markers too keeps clauses separate.
# Keep this in sync with splitBlocks in frontend/src/lib/contract-blocks.ts.
_CLAUSE_START = re.compile(
    r"(?m)(?<=\n)(?="
    r"\d+(?:\.\d+)*[.)]?\s"          # 1.  2.1  3.4.5)
    r"|\((?:[a-z]|[ivxlcdm]+|\d+)\)\s"  # (a) (iv) (3)
    r"|(?:Article|ARTICLE|Section|SECTION)\s"
    r")"
)


def block_id_for(text: str) -> str:
    """The content-hash anchor id for a chunk of clause text. Shared by the
    splitter and the structured-document builder so an element's block_id equals
    the id the splitter would give the same text — edits anchor either way."""
    return "b" + _hash(_norm(text))


def split_blocks(text: str | None) -> list[Block]:
    """Split extracted text into clause blocks — on blank lines, and on
    numbered/lettered clause markers at line starts (so OCR text, which drops
    blank lines between clauses, still splits cleanly). IDs derive from content,
    so editing one block never renumbers the others."""
    seen: dict[str, int] = {}
    out: list[Block] = []
    for chunk in re.split(r"\n\s*\n", text or ""):
        for raw in _CLAUSE_START.split(chunk):
            t = raw.strip()
            if not t:
                continue
            base = "b" + _hash(_norm(t))
            n = seen.get(base, 0)
            seen[base] = n + 1
            out.append(Block(base if n == 0 else f"{base}-{n}", t))
    return out


def anchor_quote(blocks: list[Block], quote: str | None) -> str | None:
    """Resolve a (possibly paraphrased) quote to the block it belongs to.
    Verbatim containment first; else the block with the best token-overlap score
    (shared words over the smaller word set) above a 0.6 floor, so an unrelated
    clause never silently absorbs the edit. None == no credible block, or several
    equally credible ones (repeated text): the caller should fall back, not guess."""
    needle = _norm(quote or "")
    if not needle:
        return None
    containing = [b.id for b in blocks if needle in _norm(b.text)]
    if containing:
        return containing[0] if len(containing) == 1 else None
    nt = _tokens(quote or "")
    if not nt:
        return None
    # Overlap coefficient (shared / smaller set), not Jaccard: the block is
    # usually longer than the quote, and Jaccard would punish that length even
    # when every quote word is present. Floor at 0.6 so an unrelated clause
    # sharing a few stopwords never absorbs the edit.
    scores = []
    for b in blocks:
        bt = _tokens(b.text)
        scores.append((len(nt & bt) / min(len(nt), len(bt)) if bt else 0.0, b.id))
    best = max((score for score, _ in scores), default=0.0)
    top = [block_id for score, block_id in scores if score == best]
    return top[0] if best >= 0.6 and len(top) == 1 else None

def block_by_id(blocks: list[Block], block_id: str | None) -> Block | None:
    if not block_id:
        return None
    for b in blocks:
        if b.id == block_id:
            return b
    return None


def find_phrase(text: str, phrase: str) -> tuple[int, int] | None:
    """Exact span of `phrase` in `text`, tolerating whitespace/case differences."""
    if not phrase or not text:
        return None
    normalized = re.escape(" ".join(phrase.split()))
    pattern = re.compile(normalized.replace(r"\ ", r"\s+"), flags=re.IGNORECASE)
    match = pattern.search(text)
    return (match.start(), match.end()) if match else None


def align_phrase(text: str, phrase: str, threshold: float = 82.0) -> tuple[int, int] | None:
    """Fuzzy fallback for find_phrase: LLMs paraphrase the clause they quote
    (drop a word, tweak punctuation), so an exact search misses even when the
    clause is genuinely there. Align the quote to the best-matching real span so
    the caller anchors to actual document text instead of guessing."""
    if not phrase or not text:
        return None
    try:
        from rapidfuzz import fuzz

        alignment = fuzz.partial_ratio_alignment(phrase.lower(), text.lower())
        if alignment is not None and alignment.score >= threshold:
            return alignment.dest_start, alignment.dest_end
    except Exception:  # pragma: no cover - rapidfuzz optional / defensive
        pass
    return None


def locate_phrase(text: str, phrase: str | None) -> tuple[int, int] | None:
    """Where does `phrase` actually sit in `text`? Exact first, then fuzzy.

    Use this instead of an offset the model reported. A model reads text but
    cannot count characters: measured across this corpus its span *lengths* were
    right to within a character while its *positions* drifted further the deeper
    into the document a clause sat (median 6 chars off in the first 500, 188 by
    6k). Searching for the text the model returned is exact wherever the text is
    real, and None — not a wrong number — wherever it isn't.
    """
    if not phrase or not text:
        return None
    return find_phrase(text, phrase) or align_phrase(text, phrase)
