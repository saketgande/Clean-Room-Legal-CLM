"""Word documents show nesting by indentation, not only by numbering.

Found on a real observation notice: eight quoted statutory provisions — the
text of CGST sections the document relies on — were indented half an inch under
the sentence introducing them, and nothing in their words said so. Read without
indentation they sat at the top level alongside the author's own paragraphs, so
"Section 15(1) provides:" and the provision it quotes looked like unrelated
peers.

An earlier attempt solved this by attaching any unnumbered paragraph to the
last numbered clause. That put 44 body paragraphs underneath one partner's name
and was reverted. Indentation is the signal the document actually carries.
"""

from io import BytesIO

from docx import Document
from docx.shared import Inches

from app.documents.reader.parsing.registry import DOCX_MIME, parser_for
from app.documents.reader.structure import build


def _docx(paragraphs) -> bytes:
    """`paragraphs` is [(text, indent_inches_or_None, style_or_None), ...]."""
    document = Document()
    for text, indent, style in paragraphs:
        paragraph = document.add_paragraph(text, style=style)
        if indent is not None:
            paragraph.paragraph_format.left_indent = Inches(indent)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _clauses(paragraphs):
    parsed = parser_for(DOCX_MIME).parse(_docx(paragraphs), filename="c.docx")
    return build(parsed).clauses


# --- the defect -------------------------------------------------------------


def test_an_indented_quote_nests_under_the_paragraph_introducing_it():
    """The failure this file exists for, taken from the real document."""
    clauses = _clauses([
        ("Section 15(1) of the CGST Act, 2017 provides:", None, None),
        ("The value of a supply shall be the transaction value.", 0.5, None),
    ])
    introduction, quote = clauses

    assert quote.level == 2
    assert quote.parent_clause_id == introduction.clause_id


def test_deeper_indentation_nests_deeper_again():
    clauses = _clauses([
        ("Rule 28 further provides that the value shall be:", None, None),
        ("The open market value of such supply;", 0.5, None),
        ("or ninety percent of the price charged.", 0.75, None),
    ])

    assert [c.level for c in clauses] == [1, 2, 3]
    assert clauses[2].parent_clause_id == clauses[1].clause_id


# --- what must not change ---------------------------------------------------


def test_a_document_with_no_indentation_is_unaffected():
    """Most contracts carry their structure in the numbers alone. Indentation
    must add nesting where it exists and invent none where it does not."""
    clauses = _clauses([
        ("1. DEFINITIONS", None, None),
        ("Confidential Information means non-public information.", None, None),
    ])

    assert [c.level for c in clauses] == [1, 1]


def test_a_number_places_its_clause_whatever_the_indentation():
    """Indentation speaks only for a paragraph with no number. "1.1" is a
    sub-clause of 1 whether or not the template indented it, and an indented
    "2." is still a top-level clause: the number is what a reader cites."""
    clauses = _clauses([
        ("1. DEFINITIONS", None, None),
        ("1.1 Confidential Information means non-public information.", None, None),
        ("2. TERM", 0.5, None),
    ])

    assert [c.number_label for c in clauses] == ["1.", "1.1", "2."]
    assert [c.level for c in clauses] == [1, 2, 1]
    assert clauses[1].parent_clause_id == clauses[0].clause_id


def test_indentation_inherited_from_a_style_is_found():
    """Word templates put indentation on a named style, and house styles
    inherit it through basedOn — the same reason numbering has to walk the
    chain. Reading only the paragraph misses every clause styled this way.

    "Intense Quote" carries a 936-twip indent in Word's own template; plain
    "Quote" carries none, which is why it makes a poor fixture here."""
    clauses = _clauses([
        ("Section 15(1) of the CGST Act provides:", None, None),
        ("The value of a supply shall be the transaction value.", None, "Intense Quote"),
    ])

    assert clauses[1].level == 2


def test_a_stray_indent_does_not_become_its_own_level():
    """Editing by hand leaves indents a few twips apart. Bucketing to an eighth
    of an inch stops 0.50" and 0.51" reading as two levels of nesting."""
    clauses = _clauses([
        ("The Act provides as follows:", None, None),
        ("first quoted provision", 0.5, None),
        ("second quoted provision", 0.51, None),
    ])

    assert clauses[1].level == clauses[2].level == 2
