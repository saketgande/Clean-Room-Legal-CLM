"""Turn a parsed document into structured elements (the Phase 1 normalizer).

The OCR/parse step already returns typed elements — {type, content, confidence,
page_id}. This module normalizes them into the shape ContractDocumentElement
stores: a stable clause type, a hierarchy level, a clause number, a content-hash
block_id (so edits anchor), and char offsets.

Key safety choice: elements are *located within the existing snapshot text*, not
used to re-author it. So `snapshot.text` (what every current reader reads) is
never changed — this stays purely additive. If an element can't be located
verbatim, placement reports failure and the caller leaves the snapshot
`flat_only` rather than storing offsets that don't line up.
"""

from __future__ import annotations

import re
from typing import Any

from app.contract_files.blocks import block_id_for, split_blocks

_TYPE_MAP = {
    "title": "title",
    "section_header": "heading",
    "sectionheader": "heading",
    "heading": "heading",
    "subtitle": "heading",
    "table": "table",
    "list": "list_item",
    "list_item": "list_item",
    "listitem": "list_item",
    "signature": "signature",
    "page_header": "page_artifact",
    "page_footer": "page_artifact",
    "header": "page_artifact",
    "footer": "page_artifact",
    "page_number": "page_artifact",
    "paragraph": "paragraph",
    "text": "paragraph",
    "narrative_text": "paragraph",
    "body": "paragraph",
}

_LABEL = re.compile(
    r"^\s*("
    r"\d+(?:\.\d+)*[.)]?"
    r"|\((?:[a-z]|[ivxlcdm]+|\d+)\)"
    r"|(?:Article|ARTICLE|Section|SECTION)\s+[\w.-]+"
    r")"
)


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with",
    "this", "that", "shall", "any", "by", "is", "as", "be", "its", "it", "not",
    "clause", "section", "change", "revise", "update", "make", "add", "remove",
}


def word_set(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower()))


def distinctive_tokens(s: str) -> set[str]:
    """The substance words of a query: stopwords and generic edit verbs
    ("change"/"revise"/"clause") dropped, so scoring keys on the real topic
    ("liability", "indemnity", "governing")."""
    return {w for w in _WORD.findall((s or "").lower()) if w not in _STOP and len(w) > 2}


def relevance(query: str, text: str) -> float:
    """Fraction of the query's distinctive words that appear in ``text`` — a
    cheap lexical relevance score for picking which clause an instruction is
    about."""
    qt = distinctive_tokens(query)
    return len(qt & word_set(text)) / len(qt) if qt else 0.0


def _norm_type(raw: str | None) -> str:
    return _TYPE_MAP.get((raw or "").strip().lower(), "paragraph")


def _number_label(text: str) -> str | None:
    m = _LABEL.match(text or "")
    return m.group(1).strip() if m else None


def _level(number_label: str | None, element_type: str) -> int:
    if not number_label:
        return 0 if element_type in ("title", "heading") else 1
    low = number_label.lower()
    if low.startswith(("article", "section")):
        return 0
    if number_label.startswith("("):
        return 2
    return number_label.rstrip(".)").count(".") + 1


def place_elements(
    raw_elements: list[dict[str, Any]], target_text: str, *, source: str
) -> tuple[list[dict[str, Any]], bool]:
    """Normalize raw elements and anchor each to its verbatim span in
    ``target_text``. Returns (elements, ok). ``ok`` is False if any element could
    not be located — the caller should then skip structuring (stay flat_only)
    rather than persist offsets that don't reproduce the text."""
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    cursor = 0
    seq = 0
    ok = True
    for el in raw_elements:
        text = (el.get("content") or el.get("text") or "").strip()
        if not text:
            continue
        idx = target_text.find(text, cursor)
        if idx < 0:
            idx = target_text.find(text)  # out-of-order fallback
        if idx < 0:
            ok = False  # unlocatable — signal degrade
            continue
        start, end = idx, idx + len(text)
        cursor = end

        etype = _norm_type(el.get("type"))
        label = _number_label(text)
        if etype == "paragraph" and label:
            etype = "clause"

        base = block_id_for(text)
        n = seen.get(base, 0)
        seen[base] = n + 1

        out.append({
            "seq": seq,
            "element_type": etype,
            "level": _level(label, etype),
            "number_label": label,
            "block_id": base if n == 0 else f"{base}-{n}",
            "text": text,
            "html": el.get("html") if etype == "table" else None,
            "page_number": el.get("page_id"),
            "char_start": start,
            "char_end": end,
            "confidence": el.get("confidence"),
            "source": source,
        })
        seq += 1
    return out, ok


def build_elements(
    raw_elements: list[dict[str, Any]], target_text: str
) -> tuple[list[dict[str, Any]], bool]:
    """From a real parse (provider elements), anchored to the snapshot text."""
    return place_elements(raw_elements, target_text, source="ocr")


def elements_from_flat_text(text: str) -> tuple[list[dict[str, Any]], bool]:
    """Fallback for text-only snapshots: split into clause blocks (verbatim
    substrings of ``text``) and anchor them. Lower fidelity — no confidence,
    pages, or tables — but every document gets an addressable clause index."""
    raw = [{"type": "text", "content": b.text} for b in split_blocks(text)]
    return place_elements(raw, text, source="from_flat_text")
