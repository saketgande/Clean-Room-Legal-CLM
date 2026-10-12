"""PDF parsing.

Uses PyMuPDF rather than a position-guessing reader. A PDF does not store words;
it stores glyphs with coordinates, and a reader has to decide where the spaces
go. Readers that predict the next glyph's position from font metrics alone get
this wrong on any file that sets character spacing (`Tc`) — WordPerfect, still
common in legal, emits every line as positioned fragments — and the result is
spaces *inside* words:

    LEGAL S ERVICES AGRE EM ENT
    requi res lawyers to have w ith thei r clients

Measured on a real 7-page legal services agreement: 20.2% of words were
fragments that way, against 0.9% here. Nothing downstream recovers a word split
in three, and a naive quality score cannot see the damage because fragments are
still printable letters.

Coordinates are captured for every block. A citation can only be highlighted on
the page it came from if the page position was kept at parse time.
"""

import re

from .base import ParsedBlock, ParsedDocument, UnsupportedFormat
from .cleanup import clean
from .labels import LEADING_NUMBER, label_only, level_for, split_label
from .reflow import join_wrapped_blocks

# A clause number alone on its line, with its heading on the next. PDFs indent
# the number to a tab stop, which puts it in its own text block, so "1." and
# "IDENTIFICATION OF PARTIES" arrive separately. The next line must start with a
# letter: joining "1." to "2. FEES" would put two clause markers on one line.
_NUMBER_ONLY_LINE = re.compile(
    r"(?m)^([ \t]*(?:\d+(?:\.\d+)*[.)]?|\((?:[a-z]|[ivxlcdm]+|\d+)\)))[ \t]*\n[ \t]*(?=[A-Za-z])"
)


# Below this much *real* text per page, the pages are images and the document
# needs OCR. Measured against content only — see `cleanup.py`.
_SPARSE_TEXT_PER_PAGE = 120


def _line_text(line) -> str:
    return "".join(span.get("text", "") for span in line.get("spans", []))


# A line narrower than this share of the block's widest line is a heading, not
# a wrapped line of prose. Body text fills the column; a heading stops early.
_HEADING_WIDTH_SHARE = 0.6


# Table detection on a page with no text finds nothing and still costs real
# time — measured at up to 8.5 seconds on a 30-page scan. Scans are the common
# case here, not the exception.
_MIN_CHARS_FOR_TABLE_SEARCH = 40


def _find_tables(page, char_count: int) -> list:
    """Tables on this page, or nothing if detection fails.

    A PDF has no table structure — only positioned text — so a cell that is
    vertically centred is emitted *after* the cell beside it when blocks are
    read in reading order. In a definitions table that separates a term from
    its own definition and reverses them: "Warranty Period" arrived as one
    clause and "for each Deliverable, the period specified..." as another.

    Detection costs nothing on a document without tables (measured: 0.0s on a
    52-page scan, 0.3s on eight pages of prose, and no tables invented in
    either) and a few seconds on one that is full of them.
    """
    if char_count < _MIN_CHARS_FOR_TABLE_SEARCH:
        return []  # an image of a page has no table structure to find
    try:
        return list(page.find_tables().tables)
    except Exception:
        return []  # a malformed page must cost its tables, not the document


def _render_table(table) -> str:
    """One row per line, cells tab-separated — the same shape the Word parser
    produces, so a table reads identically whichever format it arrived in."""
    lines: list[str] = []
    for row in table.extract():
        cells = [" ".join((cell or "").split()) for cell in row]
        # Detection frequently reports phantom columns that are empty in every
        # row; keeping them would put runs of tabs in the middle of a citation.
        line = "\t".join(c for c in cells if c)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def _inside_any(box, rects) -> bool:
    """Is this line inside a table? Compared on the centre, because a line's
    rectangle can overhang a cell border by a point or two."""
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)


def _page_text_width(blocks) -> float:
    """The width of the page's text column.

    The reference for "is this line short" has to be the page, not the block. A
    block containing only a heading and a one-line body has no full-width line
    in it to compare against, and every heading in it would read as ordinary
    prose.
    """
    widths = [
        line["bbox"][2] - line["bbox"][0]
        for block in blocks
        if block.get("type") == 0
        for line in block.get("lines", [])
    ]
    return max(widths) if widths else 1.0


def _split_at_clause_starts(lines, widest: float) -> list[list[tuple[str, tuple]]]:
    """Cut one PDF block into the clauses it actually contains.

    A block is whatever the renderer happened to group together, which is often
    several clauses — a definitions list, or a heading followed by its first
    paragraph. Treating a block as one clause means every clause after the
    first is unreachable: it has no number of its own, no level, and cannot be
    cited or commented on.

    Two things start a new clause:

    * a line beginning with a clause marker;
    * the line after a numbered heading — "1.2 Interpretation" followed by
      "In this agreement:" is a heading and its body, and merging them buries
      the body inside the heading. A heading is recognised by its width: prose
      fills the column and a heading stops early.

    The first line never starts a new group, because it is already the start of
    one.
    """
    entries = [
        (_line_text(line), tuple(line.get("bbox", (0, 0, 0, 0))))
        for line in lines
        if _line_text(line).strip()
    ]
    if not entries:
        return []

    groups: list[list[tuple[str, tuple]]] = [[entries[0]]]
    for text, box in entries[1:]:
        # `label_only` as well as `LEADING_NUMBER`: a number set on its own line
        # has no text after it to match against, so the leading-number pattern
        # does not see it — and it is exactly where a clause begins.
        if (
            LEADING_NUMBER.match(text)
            or label_only(text)
            or _is_complete_heading(groups[-1], widest)
        ):
            groups.append([(text, box)])
        else:
            groups[-1].append((text, box))
    return groups


def _is_complete_heading(group, widest: float) -> bool:
    """Is this group a numbered heading, with nothing after it yet?

    A heading is narrow — prose fills the column and a heading stops early —
    and it carries a clause number. The number is frequently set on its own
    line, so a group holding only a bare number is not a heading yet: its words
    are on the next line and must be allowed to join it.
    """
    texts = [text for text, _ in group]
    if len(group) > 2:
        return False
    if len(texts) == 1 and label_only(texts[0]):
        return False
    if LEADING_NUMBER.match(" ".join(texts)) is None:
        return False
    return max(box[2] - box[0] for _, box in group) < widest * _HEADING_WIDTH_SHARE


def _union(boxes, page_size: tuple[float, float] | None = None) -> dict | None:
    """The rectangle covering these lines, as a fraction of the page.

    Computed per clause rather than per block, so a citation highlights the
    clause and not the half page it was printed on.

    Normalised to 0-1 because the OCR path reports boxes that way and a
    consumer cannot tell two coordinate systems apart by looking at the
    numbers. Fractions are also what a viewer needs: they hold at any zoom and
    any rendered resolution.
    """
    if not boxes:
        return None
    width, height = page_size or (1.0, 1.0)
    width = width or 1.0
    height = height or 1.0
    return {
        "x0": round(min(b[0] for b in boxes) / width, 4),
        "y0": round(min(b[1] for b in boxes) / height, 4),
        "x1": round(max(b[2] for b in boxes) / width, 4),
        "y1": round(max(b[3] for b in boxes) / height, 4),
    }


class PdfParser:
    name = "pdf"
    # 2: blocks are cut at clause starts using line geometry. The output — and
    # therefore every character offset — differs from version 1, so a version
    # parsed by 1 must not be deduplicated against one parsed by 2.
    # 3: cleanup before joining, and the tree built from numbering (`tree.py`).
    # 4: "ii." read as a clause number, and "…; or" never joined to the next item.
    # 5: "Item 5" and OCR-glued "12.3.LICENSOR" read; recitals numbered 1., 2.;
    #    page counters ("1 of 4") removed.
    # 6: numbering that starts again at 1 is asked about, not a new part.
    version = "6"

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument:
        import fitz  # PyMuPDF

        blocks: list[ParsedBlock] = []
        warnings: list[str] = []
        total_chars = 0

        try:
            opened = fitz.open(stream=content, filetype="pdf")
        except fitz.FileDataError as exc:
            raise UnsupportedFormat("The file is not a readable PDF") from exc
        with opened as document:
            page_count = document.page_count
            for page_number, page in enumerate(document, start=1):
                # "dict" gives blocks -> lines -> spans, each with its own
                # rectangle. The coarser "blocks" mode returns a paragraph-ish
                # run as one string, and a clause starting part-way down it is
                # then invisible: on one native-text agreement that buried 451
                # clause markers inside other clauses and left 263 of 304
                # clauses flat at the top level. Lines are what let a block be
                # cut where the document cuts it, and they carry the geometry
                # to describe each piece honestly.
                page_blocks = page.get_text("dict")["blocks"]
                column_width = _page_text_width(page_blocks)
                page_size = (page.rect.width, page.rect.height)

                page_chars = sum(
                    len(_line_text(line))
                    for block in page_blocks
                    if block.get("type") == 0
                    for line in block.get("lines", [])
                )
                tables = _find_tables(page, page_chars)
                table_rects = [tuple(t.bbox) for t in tables]
                for table in tables:
                    rendered = _render_table(table)
                    if not rendered:
                        continue
                    total_chars += len(rendered)
                    blocks.append(
                        ParsedBlock(
                            text=rendered,
                            kind="table",
                            page_number=page_number,
                            bbox=_union([tuple(table.bbox)], page_size),
                        )
                    )

                for block in sorted(
                    page_blocks, key=lambda b: (b["bbox"][1], b["bbox"][0])
                ):
                    if block.get("type") != 0:  # 0 = text, 1 = image
                        continue
                    # Lines inside a table were emitted with it above; read
                    # again here they would appear twice, in reading order
                    # rather than row order.
                    lines_outside = [
                        line
                        for line in block.get("lines", [])
                        if not _inside_any(line.get("bbox", (0, 0, 0, 0)), table_rects)
                    ]
                    for lines in _split_at_clause_starts(lines_outside, column_width):
                        raw = "\n".join(text for text, _ in lines)
                        text = _NUMBER_ONLY_LINE.sub(r"\1 ", raw).strip()
                        if not text:
                            continue
                        total_chars += len(text)
                        label, body = split_label(text)
                        stripped = body.strip()
                        is_heading = (
                            bool(stripped)
                            and len(stripped) < 120
                            and stripped == stripped.upper()
                        )
                        blocks.append(
                            ParsedBlock(
                                # Without its number: `structure.py` recomposes,
                                # so PDF and DOCX arrive in the same shape even
                                # though only one of them stores the number as
                                # characters.
                                text=stripped,
                                kind="heading" if is_heading else "paragraph",
                                number_label=label,
                                level=level_for(label),
                                page_number=page_number,
                                bbox=_union([box for _, box in lines], page_size),
                            )
                        )

        blocks, removed, furniture_chars = clean(blocks, page_count)
        # Order matters: cleanup first, or the page footer sits between the two
        # halves of a sentence and there is nothing adjacent left to join.
        blocks, joined = join_wrapped_blocks(blocks)
        content_chars = total_chars - furniture_chars

        needs_ocr = bool(page_count) and content_chars < _SPARSE_TEXT_PER_PAGE * page_count
        if needs_ocr:
            # Said plainly rather than stored as a thin document: a scanned
            # contract that silently holds no text is indistinguishable from a
            # contract that says very little, and everything downstream will
            # happily report success on it.
            detail = (
                f" ({furniture_chars} more were page furniture repeated on most pages)"
                if furniture_chars
                else ""
            )
            warnings.append(
                f"Only {content_chars} characters of readable content across "
                f"{page_count} pages{detail} — this PDF is images and needs OCR."
            )
        return ParsedDocument(
            blocks=blocks,
            page_count=page_count,
            warnings=warnings,
            needs_ocr=needs_ocr,
            dropped_chars=furniture_chars,
            artifacts={"removed": removed, "joined": joined},
        )
