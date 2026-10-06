"""Removing what is not contract text — strongest evidence first, before any join.

The order is the point. On a scanned Franklin Madison MSA, the second half of
clause 3.2 ("effect at such Client Sites…") sat on the next page behind a stamp
the OCR had described in words — "Blue circular Mindtree Limited stamp/seal
with 'BENGALURU' in the center." Rejoining looks at neighbours, and the
neighbour was the stamp, so 3.2 stayed cut in two and its tail became a fake
sub-clause. The same happened in sections 6, 7 and 8.2. Removing the stamp
*first* puts the two halves side by side again.

Four checks, in order of how much they can be trusted:

1. **What the source says the block is.** The OCR provider labels every block,
   and "Header", "Footer", "Page Number" and "Figure" are not the document's
   text. On Franklin that was 26 page headers — one of which, read as a
   heading, had taken 5.2 to 5.5 as its children — 28 footers and 29 figures.
2. **Text repeated on most pages** — a running header in a native PDF, which
   has no labels to go on.
3. **Short blocks in the same spot on several pages** — a rubber stamp, whose
   characters OCR differently every time while its position does not.
4. **Debris** — a block of one to three characters, or no letters at all.

Nothing is silently lost: every removal is returned with its reason, so it can
be stored, reported and — for a stamp a viewer wants to show — drawn.
"""

import re

from .base import ParsedBlock
from .furniture import _strip_furniture

# What an OCR provider calls the things on a page that are not the text.
NON_CONTENT_ROLES = {
    "header": "page header",
    "footer": "page footer",
    "page number": "page number",
    "figure": "figure or stamp",
}

# A stamp is short, unnumbered, and lands in the same place on several pages.
# The tolerance is a fraction of the page: a skewed scan moves a stamp a few
# percent per page, and anything tighter splits one stamp into five.
STAMP_MAX_CHARS = 40
STAMP_TOLERANCE = 0.05
STAMP_MIN_PAGES = 3
# Below this, or with no letters at all, a block is not words.
DEBRIS_MAX_CHARS = 3
# A page counter: "1 of 4", "Page 7 of 19", "3/12". On a patent licence "1 of
# 4" was read as clause 1, which looked like the numbering restarting and
# pushed the schedule's Items 8 and 9 out of the schedule.
_PAGE_COUNTER = re.compile(r"^(?:page\s*)?\d{1,4}\s*(?:of|/)\s*\d{1,4}$", re.IGNORECASE)


def clean(
    blocks: list[ParsedBlock], page_count: int | None
) -> tuple[list[ParsedBlock], list[dict], int]:
    """The blocks worth keeping, what was removed and why, and its size."""
    removed: list[dict] = []

    kept = []
    for block in blocks:
        reason = NON_CONTENT_ROLES.get((block.role or "").lower())
        # A numbered block is never dropped on a label alone: a clause the
        # provider mislabelled would vanish with nothing on the page to show it.
        if block.number_label:
            reason = None
        # Whatever it was labelled: "1 of 4" is never a clause, even though
        # its "1" reads as a clause number.
        whole = f"{block.number_label or ''} {block.text}".strip()
        if reason is None and _PAGE_COUNTER.match(whole):
            reason = "page number"
        if reason:
            removed.append(_record(block, reason))
        else:
            kept.append(block)

    remaining, _ = _strip_furniture(kept, page_count)
    survivors = {id(block) for block in remaining}
    removed += [_record(b, "repeated on most pages") for b in kept if id(b) not in survivors]
    kept = remaining

    stamped = _repeated_position(kept)
    removed += [_record(b, "stamp in the same spot on several pages") for b in kept if id(b) in stamped]
    kept = [b for b in kept if id(b) not in stamped]

    debris = {id(b) for b in kept if _is_debris(b)}
    removed += [_record(b, "not words") for b in kept if id(b) in debris]
    kept = [b for b in kept if id(b) not in debris]

    return kept, removed, sum(len(r["text"]) for r in removed)


def _record(block: ParsedBlock, reason: str) -> dict:
    return {"reason": reason, "text": block.text[:200], "page": block.page_number, "bbox": block.bbox}


def _is_debris(block: ParsedBlock) -> bool:
    if block.number_label or block.kind in ("heading", "table"):
        return False
    text = block.text.strip()
    return len(text) <= DEBRIS_MAX_CHARS or not any(ch.isalpha() for ch in text)


def _centre(bbox: dict) -> tuple[float, float]:
    return (bbox["x0"] + bbox["x1"]) / 2, (bbox["y0"] + bbox["y1"]) / 2


def _repeated_position(blocks: list[ParsedBlock]) -> set[int]:
    """Ids of short blocks sharing one spot across enough pages to be a stamp."""
    candidates = [
        block
        for block in blocks
        if not block.number_label
        and block.kind not in ("heading", "table")
        and block.page_number is not None
        and isinstance(block.bbox, dict)
        and all(0.0 <= block.bbox.get(k, -1) <= 1.0 for k in ("x0", "y0", "x1", "y1"))
        and len(block.text.strip()) <= STAMP_MAX_CHARS
    ]
    clusters: list[list[ParsedBlock]] = []
    for block in candidates:
        x, y = _centre(block.bbox)
        for cluster in clusters:
            cx, cy = _centre(cluster[0].bbox)
            if abs(x - cx) <= STAMP_TOLERANCE and abs(y - cy) <= STAMP_TOLERANCE:
                cluster.append(block)
                break
        else:
            clusters.append([block])
    return {
        id(block)
        for cluster in clusters
        if len({b.page_number for b in cluster}) >= STAMP_MIN_PAGES
        for block in cluster
    }
