"""What a parser is, and what it must return.

Parsing is a replaceable stage, not a branch inside the upload path. A better
parser (a layout model, a different OCR provider) should be a registry entry,
not a rewrite — which is the shape OpenContracts settled on and the shape the
previous pipeline lacked.

A parser returns *blocks in reading order*. It does not decide what a clause is;
`structure.py` does that, so every format produces clauses the same way.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ParsedBlock:
    """One run of text as the document itself presents it."""

    text: str
    kind: str = "paragraph"  # paragraph | heading | table | list_item
    # The number the document *displays*, which for a Word file is computed at
    # render time and appears nowhere in the text (see parsing/docx.py).
    number_label: str | None = None
    level: int = 1
    page_number: int | None = None
    # {"x0","y0","x1","y1"} as a fraction of the page, origin top-left. The
    # first region only; `regions` holds every one.
    bbox: dict | None = None
    # What the source itself says this block is. An OCR provider labels every
    # block — "Header", "Footer", "Figure", "Section Header" — and that label
    # is stronger evidence than anything inferable from the words: it is how a
    # running page header and a stamp described in prose are told apart from a
    # clause. It was being discarded, and "Header" (a page header) was being
    # read as a heading.
    role: str | None = None
    # Every place the block sits, in reading order: ({"page", "bbox"}, ...). A
    # block rejoined across a page break has one per page; a viewer must light
    # up both halves of the clause, not just the first.
    regions: tuple = ()
    # The level the author set in Word's automatic numbering, and the list it
    # belongs to. Where it exists it is the author's own answer about nesting,
    # not an inference — but only within that list.
    declared_level: int | None = None
    declared_list: str | None = None
    # Why this block is several pieces rejoined, if it is.
    joins: tuple = ()

    @property
    def all_regions(self) -> list[dict]:
        if self.regions:
            return list(self.regions)
        if self.page_number is None and self.bbox is None:
            return []
        return [{"page": self.page_number, "bbox": self.bbox}]

    def __post_init__(self):
        # Postgres TEXT rejects NUL, and a stray 0x00 arrives from .txt files,
        # PDF text and OCR alike. Every parser and the OCR path build blocks
        # here, so this is the one place that covers all of them — and it runs
        # before `structure.py` computes offsets, so they stay consistent.
        if "\x00" in self.text:
            object.__setattr__(self, "text", self.text.replace("\x00", ""))


@dataclass(frozen=True)
class ParsedDocument:
    blocks: list[ParsedBlock]
    page_count: int | None = None
    # Anything the parser had to give up on. Surfaced rather than swallowed: a
    # document that parsed badly must be visibly different from one that parsed
    # well, or bad extraction looks exactly like a thin contract.
    warnings: list[str] = field(default_factory=list)
    # The file carries almost no readable text — its pages are images. The
    # parser reports this rather than deciding what to do about it; `service.py`
    # owns the policy of when to spend money on OCR.
    needs_ocr: bool = False
    # Characters the parser removed knowingly — page furniture, mostly. Declared
    # so `audit.py` can tell a deliberate omission from a silent loss; without
    # it every document with a running header looks like a parser failure.
    dropped_chars: int = 0
    # What cleanup removed and which blocks were rejoined, with reasons:
    # {"removed": [...], "joined": [...]}. Stored on the version so nothing
    # extraction changed is invisible.
    artifacts: dict = field(default_factory=dict)


@runtime_checkable
class Parser(Protocol):
    """`name` and `version` are stored on the version row, so a document can
    always be re-parsed the way it was parsed the first time."""

    name: str
    version: str

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument: ...


class UnsupportedFormat(Exception):
    """No parser handles this media type, or the file is too damaged (or too
    hostile) for its parser to open. Raised rather than returning empty text: a
    document nobody could read must not be stored as a document with no content
    in it."""
