"""A PDF block is whatever the renderer grouped together, not a clause.

Measured on a real 36-page IT services agreement — the only native-text PDF in
a corpus of thirteen: 451 clause markers were buried inside other clauses,
263 of its 304 clauses sat flat at the top level, and its longest "clause" ran
to 5,044 characters across a page header and several paragraphs.

Reading a block whole makes every clause after the first in it unreachable. It
has no number of its own, no level, and cannot be cited or commented on — while
the text is all present, so nothing downstream can tell.

Splitting at line starts took that document to 754 clauses, 536 numbered, and
no buried markers.
"""

import fitz
import pytest

from app.documents.reader.parsing.labels import LEADING_NUMBER
from app.documents.reader.parsing.registry import parser_for
from app.documents.reader.structure import build

PDF_MIME = "application/pdf"


def _pdf(lines: list[str], *, spacing: int = 14) -> bytes:
    """Lines close enough together that the renderer groups them into one
    block — the shape this file exists for."""
    document = fitz.open()
    page = document.new_page()
    for index, text in enumerate(lines):
        page.insert_text((72, 700 + index * spacing), text, fontsize=11)
    return document.tobytes()


def _clauses(lines, **kw):
    content = _pdf(lines, **kw)
    return build(parser_for(PDF_MIME).parse(content, filename="c.pdf")).clauses


def _one_block(lines, **kw) -> bool:
    document = fitz.open(stream=_pdf(lines, **kw), filetype="pdf")
    text_blocks = [b for b in document[0].get_text("dict")["blocks"] if b.get("type") == 0]
    return len(text_blocks) == 1


# --- the defect -------------------------------------------------------------


def test_a_clause_starting_mid_block_becomes_its_own_clause():
    """The failure this file exists for."""
    lines = [
        "The parties agree as follows:",
        "1 Definitions and Interpretation",
        "1.1 The meanings of the terms used are set out below.",
    ]
    assert _one_block(lines), "fixture must produce a single block, or it proves nothing"

    clauses = _clauses(lines)

    assert len(clauses) == 3
    assert [c.number_label for c in clauses] == [None, "1", "1.1"]
    assert [c.level for c in clauses] == [1, 1, 2]


def test_no_clause_marker_is_left_buried():
    """The corpus-level measure. A single assertion on one phrase would pass on
    a rewrite that split that phrase and buried others."""
    clauses = _clauses([
        "Recitals",
        "2 Services",
        "2.1 The Supplier shall provide the Services.",
        "2.2 Orica shall pay the Charges.",
        "(a) within thirty days of invoice",
    ])

    buried = [
        line
        for clause in clauses
        for line in clause.text.split("\n")[1:]
        if LEADING_NUMBER.match(line)
    ]
    assert buried == []


def test_the_first_line_does_not_split_off_on_its_own():
    """A block opening with a clause marker is already the start of a clause.
    Treating it as a split point would emit an empty group before it."""
    clauses = _clauses([
        "3 The Supplier shall invoice Orica monthly in arrears for all Services",
        "performed during the preceding month.",
    ])

    assert len(clauses) == 1
    assert clauses[0].number_label == "3"
    assert "preceding month" in clauses[0].text


def test_a_block_with_no_markers_stays_one_clause():
    """The guard against over-splitting: a wrapped paragraph is one clause, and
    breaking it at every line would make each fragment uncitable."""
    lines = [
        "The Supplier shall perform the Services with the degree of skill and",
        "care reasonably expected of a professional supplier of such services,",
        "and in accordance with all applicable laws.",
    ]
    assert _one_block(lines)

    clauses = _clauses(lines)

    assert len(clauses) == 1
    assert "applicable laws" in clauses[0].text


# --- geometry ---------------------------------------------------------------


def test_each_clause_gets_the_rectangle_of_its_own_lines():
    """Coordinates are computed per clause, not per block. Reusing the block's
    rectangle would highlight the half page a clause was printed on rather than
    the clause."""
    clauses = _clauses([
        "1 Definitions",
        "2 Services",
        "3 Charges",
    ])

    assert len(clauses) == 3
    boxes = [c.bbox for c in clauses]
    assert all(b is not None for b in boxes)
    # Printed top to bottom, so each clause sits below the one before it.
    assert boxes[0]["y0"] < boxes[1]["y0"] < boxes[2]["y0"]
    # And each covers roughly its own line, not the block they share. Line
    # rectangles include ascender space and overlap by a point or so, so the
    # test is on relative size rather than on a hard boundary.
    span = boxes[2]["y1"] - boxes[0]["y0"]
    assert all(b["y1"] - b["y0"] < span * 0.6 for b in boxes)


@pytest.mark.parametrize("marker", ["1", "1.1", "(a)", "Section 4.2", "A."])
def test_every_clause_marker_shape_starts_a_new_clause(marker):
    """The split uses the same pattern as everything else, so a shape that is
    a clause number anywhere is a clause number here."""
    clauses = _clauses(["Introductory wording follows.", f"{marker} The clause text."])

    assert len(clauses) == 2
    assert clauses[1].number_label == marker


# --- a heading and its body are two clauses ---------------------------------


def test_a_numbered_heading_does_not_absorb_its_body():
    """Measured against the document itself: "1.2 Interpretation" and "In this
    agreement:" were one clause, burying the body inside the heading. Six
    clauses on three pages were lost this way — 1.2, 1.3, 1.4, 4.4, 5.1, 5.2."""
    # A full-width line has to exist on the page, or nothing establishes what
    # the text column is and no line can be judged short.
    lines = [
        "The Supplier must perform the Services with the degree of skill and care reasonably expected of a professional supplier of such services.",
        "1.2 Interpretation",
        "In this agreement:",
    ]

    clauses = _clauses(lines)

    assert [c.number_label for c in clauses] == [None, "1.2", None]
    assert clauses[2].text == "In this agreement:"


def test_a_clause_number_set_on_its_own_line_still_joins_its_heading():
    """PDFs frequently set the number in its own line at a tab stop. Treating a
    bare number as a finished heading splits "1.2" from "Interpretation" and
    loses the label from both halves."""
    clauses = _clauses(["The Supplier must perform the Services with the degree of skill and care reasonably expected of a professional supplier of such services.", "1.2", "Interpretation", "In this agreement:"])

    assert clauses[1].number_label == "1.2"
    assert "Interpretation" in clauses[1].text
    assert clauses[2].text == "In this agreement:"


def test_a_wrapped_paragraph_is_not_split_after_its_first_line():
    """The guard. A clause's first line is full width; a heading stops early.
    Without the width test every wrapped clause would break in two."""
    lines = [
        "(c) An expression importing a person includes any company, partnership,",
        "joint venture, association or other body corporate.",
    ]
    assert _one_block(lines)

    clauses = _clauses(lines)

    assert len(clauses) == 1
    assert "body corporate" in clauses[0].text
