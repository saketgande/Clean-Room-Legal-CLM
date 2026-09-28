"""Page furniture: headers, footers, stamps — everything that is not content.

Shared by the native PDF parser and the OCR path. It has to serve both, because
the document that needs it most is the scanned one: OCR reads the whole page,
so every running header, page number and stamp caption arrives as a clause.

The case this was written for: a 14-page scanned MSA carried a DocuSign envelope
stamp on every page. Those 14 identical lines were 798 of its 860 characters —
enough to clear a naive "is there text here?" check, so a contract with no
readable content at all reported a clean parse.
"""

import re

from .base import ParsedBlock

# A line is page furniture when it repeats on at least this share of the pages.
# Two-page documents are excluded: a heading legitimately repeating on both is
# not the same evidence as one repeating on fourteen.
_FURNITURE_PAGE_SHARE = 0.6
_FURNITURE_MIN_PAGES = 3

_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")


def _furniture_key(text: str) -> str:
    """Normalise a line so the same header matches across pages.

    Digits collapse to `#` because a running header is usually "Page 3 of 7" —
    different characters on every page, the same furniture on all of them.
    """
    return _DIGITS.sub("#", _WHITESPACE.sub(" ", text).strip().lower())


def _strip_furniture(
    blocks: list[ParsedBlock], page_count: int | None
) -> tuple[list[ParsedBlock], int]:
    """Drop running headers, footers and e-signature stamps.

    This exists because of a real file: a 14-page scanned MSA where every page
    carried a DocuSign envelope stamp. 798 of its 860 characters were that one
    repeated line, which was enough to push it past the "is there any text
    here?" check — so a contract containing no readable text at all reported a
    clean parse. Furniture is not content and must not be counted as if it were.

    Returns the surviving blocks and how many characters were removed.
    """
    if not page_count or page_count < _FURNITURE_MIN_PAGES:
        return blocks, 0
    # Count pages when the blocks know theirs, occurrences when they do not.
    # OCR returns one flat string with no page structure, so the page-based
    # test silently does nothing on exactly the documents that need it most —
    # a scanned contract's every header, footer and stamp caption lands in the
    # clause list.
    seen: dict[str, set[int] | int] = {}
    positional = any(b.page_number is not None for b in blocks)
    for index, block in enumerate(blocks):
        key = _furniture_key(block.text)
        if positional:
            if block.page_number is None:
                continue
            seen.setdefault(key, set()).add(block.page_number)
        else:
            seen[key] = seen.get(key, 0) + 1
    threshold = max(_FURNITURE_MIN_PAGES, page_count * _FURNITURE_PAGE_SHARE)
    furniture = {
        key
        for key, hits in seen.items()
        if (len(hits) if isinstance(hits, set) else hits) >= threshold
    }
    if not furniture:
        return blocks, 0
    kept = [b for b in blocks if _furniture_key(b.text) not in furniture]
    removed = sum(len(b.text) for b in blocks if _furniture_key(b.text) in furniture)
    return kept, removed
