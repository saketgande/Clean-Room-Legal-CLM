"""PDF extraction must not split words apart.

`pypdf` reconstructs word spacing by predicting where each glyph should land
and inserting a space when the next one starts further right than expected. It
does not account for the `Tc` character-spacing operator, so on a PDF that sets
`Tc` and draws text as positioned fragments — which is how WordPerfect, still
in wide use in law firms, emits every line — it inserts spaces *inside* words:

    LEGAL S ERVICES AGRE EM ENT
    requi res lawyers to have w ith thei r clients

Measured on a real 7-page legal services agreement, 20.2% of pypdf's words were
fragments against 0.9% from PyMuPDF. Nothing downstream can recover a word split
in three: search will not match it, the clause splitter cannot read a heading,
and a citation quoted to a lawyer is visibly broken. The extraction quality
score cannot see it either — fragments are printable alphabetic characters, so
that document scored 0.87 and never fell back to OCR.
"""

import re

import pytest

from app.contract_files.text_extraction import _join_number_lines, extract_text

# Helvetica advance widths at 12pt, for the few letters these fixtures use.
_WIDTHS = {"r": 3.996, "e": 6.672, "q": 6.672, "u": 6.672, "i": 2.664, "s": 6.0,
           "a": 6.672, "w": 8.664, "t": 3.336, "h": 6.672, "l": 2.664, "y": 6.0,
           "o": 6.672, "v": 6.0, " ": 3.336, "c": 6.0, "n": 6.672, "d": 6.672}
_TC = 0.42  # character spacing, the operator pypdf overlooks


def _pdf(lines: list[list[str]]) -> bytes:
    """A PDF that draws each line as positioned fragments, advancing by exactly
    the width already drawn — so the glyphs are contiguous and any space in the
    extracted text is invented. This is the shape of a WordPerfect PDF."""
    ops = ["BT", "/F1 12 Tf", f"{_TC} Tc", "-0.18 Tw", "72 720 Td"]
    for index, fragments in enumerate(lines):
        if index:
            ops.append("0 -14 Td")  # next line, back to the left margin
        for fragment in fragments:
            ops.append(f"({fragment})Tj")
            advance = sum(_WIDTHS.get(c, 6.672) + _TC for c in fragment)
            ops.append(f"{advance:.4f} 0.0000 TD")
    ops.append("ET")
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(out)


def _extract(pdf: bytes):
    return extract_text(pdf, mime_type="application/pdf", filename="contract.pdf")


# --- the defect --------------------------------------------------------------


def test_a_word_drawn_as_fragments_is_not_split():
    """The failure this file exists for, reproduced from the real document:
    "requires lawyers" came out "requi res lawyers"."""
    result = _extract(_pdf([["requi", "res", " l", "aw", "y", "ers"]]))

    assert "requires lawyers" in result.text
    assert "requi res" not in result.text


def test_a_heading_drawn_as_fragments_stays_one_word():
    """"LEGAL S ERVICES AGRE EM ENT" — a heading nothing can match against,
    which is how a contract loses its own title to search."""
    result = _extract(_pdf([["condi", "tions"], ["res", "ponsi", "bilit", "ies"]]))

    assert "conditions" in result.text.lower()
    assert "responsibilities" in result.text.lower()


def test_no_line_is_mostly_fragments():
    """The corpus-level guard. A single assertion on one phrase would pass on
    a rewrite that fixed that phrase and broke others, so measure the whole
    extraction the way the defect was originally measured."""
    short_words_that_are_real = {"a", "i", "of", "to", "in", "is", "it", "be",
                                 "as", "at", "or", "on", "an", "by", "we", "do"}
    result = _extract(_pdf([
        ["requi", "res l", "aw", "y", "ers"],
        ["to h", "ave w", "i", "t", "h thei", "r cli", "ents"],
    ]))
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", result.text)]
    fragments = [w for w in words if len(w) <= 2 and w not in short_words_that_are_real]

    assert words, "extraction produced no words at all"
    assert len(fragments) / len(words) < 0.05


# --- the clause number split across two lines --------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("1.\nIDENTIFICATION OF PARTIES", "1. IDENTIFICATION OF PARTIES"),
    ("(a)\nSub-clause text", "(a) Sub-clause text"),
    ("2.3\nGoverning law", "2.3 Governing law"),
])
def test_a_clause_number_rejoins_its_heading(raw, expected):
    """PDFs indent the number with a tab stop, which puts it in its own text
    block. The element builder reads that number as the clause's label, so a
    number on its own line produces a labelled clause with no text and an
    unlabelled clause with all of it."""
    assert _join_number_lines(raw) == expected


def test_two_clause_markers_are_never_joined_onto_one_line():
    """Guards the greedy version of the rejoin: "1." followed by "2. FEES"
    would become "1. 2. FEES", and the splitter can only see the first marker
    — silently merging two clauses into one."""
    assert _join_number_lines("1.\n2. FEES") == "1.\n2. FEES"


def test_a_blank_line_after_a_number_is_left_alone():
    """A blank line is a deliberate block boundary, not a wrapped heading."""
    assert _join_number_lines("1.\n\nFEES") == "1.\n\nFEES"


# --- what must not regress ---------------------------------------------------


def test_the_page_map_still_covers_every_page():
    """`page_map` is how a citation reports the page it came from; losing it
    would make every PDF citation unciteable."""
    result = _extract(_pdf([["Term and termination"]]))

    assert result.page_map is not None
    assert set(result.page_map) == {"1"}
    span = result.page_map["1"]
    assert result.text[span["start"]:span["end"]].strip()


def test_a_damaged_pdf_asks_for_ocr_rather_than_raising():
    """Extraction feeds the whole pipeline; an unreadable file must degrade to
    the OCR path, not take down the upload."""
    result = _extract(b"%PDF-1.4\nnot actually a pdf")

    assert result.method == "pdf_failed"
    assert result.needs_ocr is True
    assert result.text == ""
