"""Parsed blocks -> clauses, flat text, and offsets.

Every format converges here, so a PDF and a Word file produce clauses the same
way and nothing downstream has to know which parser ran.

Two invariants this module owns, both cheap to check and both worth checking:

* ``flat_text[c.char_start:c.char_end] == c.text`` for every clause. If that
  ever fails, every citation into this version points somewhere wrong.
* a clause's text includes its visible number. Word stores the number nowhere
  and PDFs store it as characters; recomposing here means a quoted clause reads
  the same whichever it came from.
"""

import secrets
from dataclasses import dataclass

from .parsing.base import ParsedDocument
from .tree import Node, build_tree, depths
from .versions import carry_ids

# Blocks are separated by a blank line in the flat text. Two characters, one
# constant, because the offsets depend on it.
BLOCK_SEPARATOR = "\n\n"


def new_clause_id() -> str:
    """A fresh, permanent clause identity.

    Not derived from the text. A content hash changes the moment a clause is
    reworded, which is precisely when its comments most need to follow it.
    """
    return f"cl_{secrets.token_hex(6)}"


@dataclass(frozen=True)
class BuiltClause:
    clause_id: str
    seq: int
    parent_clause_id: str | None
    number_label: str | None
    level: int
    clause_type: str
    text: str
    char_start: int
    char_end: int
    page_number: int | None
    bbox: dict | None
    number_scheme: str | None = None
    number_path: list[int] | None = None
    structure_source: str | None = None
    source_regions: list[dict] | None = None


@dataclass(frozen=True)
class BuiltStructure:
    clauses: list[BuiltClause]
    flat_text: str
    page_count: int | None
    warnings: list[str]


def _compose(number_label: str | None, text: str) -> str:
    if not number_label:
        return text
    if text.startswith(number_label):
        return text
    return f"{number_label} {text}"


def body(number_label: str | None, text: str) -> str:
    """A clause's words without its number — `_compose` undone."""
    label = (number_label or "").strip()
    return text[len(label) :].lstrip() if label and text.startswith(label) else text


def build(parsed: ParsedDocument, previous: list[tuple[str, str]] | None = None) -> BuiltStructure:
    """Assemble clauses, the flat text they index into, and their tree.

    The tree comes from `tree.build_tree` — the numbering and what the file
    says about itself — and records who decided each parent. What it cannot
    decide is stored as "undecided" for the AI to settle later, never guessed:
    a paragraph with no number between (b) and (c) no longer captures (c).

    `previous` is the last version's clauses as (clause_id, body): a clause
    that is recognisably one of them keeps its id (`versions.carry_ids`), so
    what is anchored to it follows it into this version.
    """
    kept: list[tuple] = []
    parts: list[str] = []
    cursor = 0
    for block in parsed.blocks:
        text = _compose(block.number_label, block.text).strip()
        if not text:
            continue
        if parts:
            cursor += len(BLOCK_SEPARATOR)
        parts.append(text)
        kept.append((block, text, cursor, cursor + len(text)))
        cursor += len(text)

    nodes = [
        Node(
            label=block.number_label,
            text=block.text,
            kind=block.kind,
            declared_level=block.declared_level,
            declared_list=block.declared_list,
            # Indentation only speaks for a paragraph with no number: a number
            # says where its clause sits, and says it more reliably.
            layout_level=None if block.number_label else block.level,
        )
        for block, *_ in kept
    ]
    placements = build_tree(nodes)
    levels = depths(placements)
    carried = carry_ids(previous or [], [body(b.number_label, text) for b, text, *_ in kept])
    ids = [clause_id or new_clause_id() for clause_id in carried]

    clauses = []
    for index, ((block, text, start, end), placement) in enumerate(zip(kept, placements, strict=True)):
        regions = block.all_regions
        first = regions[0] if regions else {}
        number = placement.number
        clauses.append(
            BuiltClause(
                clause_id=ids[index],
                seq=index,
                parent_clause_id=None if placement.parent is None else ids[placement.parent],
                number_label=block.number_label,
                level=levels[index],
                clause_type=block.kind,
                text=text,
                char_start=start,
                char_end=end,
                page_number=first.get("page", block.page_number),
                bbox=first.get("bbox", block.bbox),
                number_scheme=number.scheme if number else None,
                number_path=list(number.path) if number else None,
                structure_source=placement.source,
                source_regions=regions or None,
            )
        )

    flat_text = BLOCK_SEPARATOR.join(parts)
    warnings = list(parsed.warnings)
    if not clauses:
        warnings.append("No clauses could be identified in this document.")
    return BuiltStructure(
        clauses=clauses,
        flat_text=flat_text,
        page_count=parsed.page_count,
        warnings=warnings,
    )


def verify_offsets(structure: BuiltStructure) -> list[str]:
    """Every clause must be findable at the offsets it claims.

    Returned rather than asserted so the caller can record the mismatch against
    the document instead of losing the whole ingest — but a non-empty result is
    a parser bug, not a data quirk.
    """
    problems: list[str] = []
    for clause in structure.clauses:
        actual = structure.flat_text[clause.char_start : clause.char_end]
        if actual != clause.text:
            problems.append(
                f"clause {clause.seq} ({clause.number_label or 'unnumbered'}): "
                f"offsets point at {actual[:40]!r}, expected {clause.text[:40]!r}"
            )
    return problems
