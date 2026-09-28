"""The shape every OCR provider returns.

Vendor-neutral on purpose: Reducto and Databricks both produce one of these, so
contract_files.service can swap providers without knowing which ran. It lived
in reducto.py originally, which made the Databricks client look as though it
depended on Reducto — it never did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OCRResult:
    text: str
    provider: str
    quality_score: float
    metadata: dict
    # Structured elements from the parser (type/content/confidence/page_id).
    # Optional so a provider that returns only text still fits this shape.
    elements: list[dict[str, Any]] = field(default_factory=list)
    # The same content at block granularity, each with the page and rectangle it
    # occupies: {content, type, page, bbox{x0,y0,x1,y1}, confidence}. Separate
    # from `elements` rather than replacing it, because `page_map_from_elements`
    # depends on that field's existing granularity.
    #
    # Without this, a scanned document's clauses carry no geometry at all — OCR
    # returns one long string — so a citation into a scan can be quoted but
    # never shown on the page it came from. Providers that return only text
    # leave this empty.
    blocks: list[dict[str, Any]] = field(default_factory=list)


def page_map_from_elements(text: str, elements: list[dict[str, Any]] | None) -> dict | None:
    """Page offsets into ``text``, rebuilt from the parser's elements.

    A page map is a set of character offsets, so it only means anything against
    the exact string it was measured on. When OCR replaces the text, carrying
    the native extractor's map across leaves every offset pointing into a
    string that no longer exists — a page citation that is confidently wrong.

    So this rebuilds the map from the elements the OCR provider returned, and
    returns ``None`` the moment it cannot prove the reconstruction matches the
    text being stored. No page map is a missing citation; a stale one is a
    false citation, and the second is worse.

    Providers whose elements carry no ``page_id`` (Reducto today) get ``None``.
    """
    elements = elements or []
    if not any(el.get("page_id") is not None for el in elements):
        return None
    contents = [(el, str(el.get("content") or "")) for el in elements]
    contents = [(el, content) for el, content in contents if content]
    if not contents:
        return None

    # Both providers assemble their text as a newline join of element contents
    # and strip the result. Rebuild that exactly, and bail if it disagrees.
    rebuilt = "\n".join(content for _, content in contents)
    if rebuilt.strip() != text:
        return None
    lead = len(rebuilt) - len(rebuilt.lstrip())
    limit = len(text)

    page_map: dict[str, dict[str, int]] = {}
    cursor = 0
    for el, content in contents:
        start = min(max(cursor - lead, 0), limit)
        end = min(max(cursor - lead + len(content), 0), limit)
        # page_id is 0-based; page maps everywhere else in the codebase are
        # 1-based, matching the [Page N] the extractor emits.
        key = str(int(el.get("page_id") or 0) + 1)
        span = page_map.get(key)
        if span is None:
            page_map[key] = {"start": start, "end": end}
        else:
            span["start"] = min(span["start"], start)
            span["end"] = max(span["end"], end)
        cursor += len(content) + 1  # +1 for the newline the join inserts
    return page_map or None
