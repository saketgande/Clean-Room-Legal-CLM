"""Clause labelling on top of the Documents reader's segmentation.

The reader (contract_files/structure.py) already splits a contract into its
numbered clauses with exact offsets — deterministic and complete. Clause
extraction used to ignore that and ask the model to find the clauses again and
copy each one's text back, which (a) re-did work already done, (b) cut off at
the output limit on long contracts and stored nothing, and (c) produced
offsets by searching for the model's copy. Here the model only names the type
of segments we hand it; text and offsets come from the segmentation.

Pure functions over element-like rows, so they're testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass

# What the model sees of each segment — enough to recognise the clause, not the
# whole thing, so the prompt stays small whatever the contract's length.
EXCERPT_CHARS = 300
_SEGMENT_TYPES = {"clause", "heading"}
# ponytail: above this many level-1/2 segments only level-1 sections are sent;
# label sub-clauses in batches if a 300+ clause contract ever needs finer types.
MAX_SEGMENTS = 250


@dataclass(frozen=True)
class Segment:
    id: str            # "S1", "S2", … — what the model answers with
    level: int
    number: str | None  # "13.", "13.2"
    heading: str       # first line of the segment, for the stored clause
    start: int         # offsets into ContractTextSnapshot.text, section-wide
    end: int
    excerpt: str

    def for_prompt(self) -> dict:
        return {"id": self.id, "number": self.number, "excerpt": self.excerpt}


def _first_line(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:140]


def segments_from_elements(elements: list, text: str) -> list[Segment]:
    """Turn the reader's elements (ordered by ``seq``) into labellable segments.

    A level-1 section's span runs to the next section at its level or above, so
    "13. LIMITATION OF LIABILITY" covers 13.1–13.4; a sub-clause covers itself
    and its own children. Page furniture and paragraphs outside any numbered
    clause (title, recitals, signature lines) aren't offered.
    """
    body = [e for e in elements if e.element_type != "page_artifact" and e.char_start is not None]
    cands = [i for i, e in enumerate(body) if e.element_type in _SEGMENT_TYPES and (e.level or 1) <= 2]
    if len(cands) > MAX_SEGMENTS:
        cands = [i for i in cands if (body[i].level or 1) == 1]
    out: list[Segment] = []
    for n, i in enumerate(cands, start=1):
        el = body[i]
        level = el.level or 1
        end = el.char_end or el.char_start
        for nxt in body[i + 1:]:
            if nxt.element_type in _SEGMENT_TYPES and (nxt.level or 1) <= level:
                break
            end = max(end, nxt.char_end or end)
        span_text = text[el.char_start:end]
        out.append(Segment(
            id=f"S{n}", level=level, number=el.number_label, heading=_first_line(el.text),
            start=el.char_start, end=end, excerpt=" ".join(span_text.split())[:EXCERPT_CHARS],
        ))
    return out


def clause_rows(segments: list[Segment], labels: list, text: str) -> list[dict]:
    """The clauses to store: one per label naming a real segment (unknown ids
    and repeats are dropped), with the segment's own text and offsets."""
    by_id = {s.id: s for s in segments}
    seen: set[str] = set()
    rows: list[dict] = []
    for lab in labels:
        seg = by_id.get(str(lab.segment_id).strip())
        if seg is None or seg.id in seen:
            continue
        seen.add(seg.id)
        rows.append({
            "clause_type": lab.clause_type, "heading": seg.heading,
            "text": text[seg.start:seg.end], "start_char": seg.start, "end_char": seg.end,
            "confidence": lab.confidence,
        })
    return rows


def segments_for_snapshot(db, snapshot_id: str | None) -> list[Segment]:
    """The segments of a stored snapshot, or [] when the reader didn't segment
    it (a scan, a damaged file) — the caller then falls back to the old
    find-and-copy extraction."""
    if not snapshot_id:
        return []
    from sqlalchemy import select

    from app.contract_files.models import ContractDocumentElement, ContractTextSnapshot

    snapshot = db.get(ContractTextSnapshot, snapshot_id)
    if snapshot is None:
        return []
    elements = db.scalars(
        select(ContractDocumentElement)
        .where(ContractDocumentElement.text_snapshot_id == snapshot_id)
        .order_by(ContractDocumentElement.seq)
    ).all()
    return segments_from_elements(list(elements), snapshot.text or "")


def clause_skill_input(db, *, contract_id: str, contract_version_id: str | None,
                       text_snapshot_id: str | None) -> tuple[str, dict]:
    """Which skill extracts this version's clauses, and its input: labelling
    when the reader segmented the document, else the old extraction."""
    payload = {"contract_id": contract_id, "contract_version_id": contract_version_id,
               "text_snapshot_id": text_snapshot_id}
    segments = segments_for_snapshot(db, text_snapshot_id)
    if segments:
        return "clause_labeling", {**payload, "segments": [s.for_prompt() for s in segments]}
    return "clause_extraction", payload
