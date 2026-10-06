"""A PDF has no tables — only text at coordinates.

Read in reading order, a cell that is vertically centred is emitted *after* the
cell beside it. In the definitions table of a real IT services agreement that
separated "Warranty Period" from its own definition and reversed the two, so
the term and its meaning became unrelated clauses:

    clause: "for each Deliverable, the period specified as such in a SOW..."
    clause: "Warranty Period"

Detecting the table restores the pairing and makes the whole table one unit,
which is also what the Word parser does with a table of data.
"""

import warnings

import fitz

from app.documents.reader.parsing.registry import parser_for
from app.documents.reader.structure import build

warnings.filterwarnings("ignore")


def _definitions_table() -> bytes:
    """A ruled two-column table whose first term is vertically centred, so it
    sits lower on the page than the first line of its own definition."""
    document = fitz.open()
    page = document.new_page()
    for y0, y1 in [(100, 140), (140, 200), (200, 240)]:
        page.draw_rect(fitz.Rect(72, y0, 500, y1), color=(0, 0, 0), width=0.8)
        page.draw_line(fitz.Point(220, y0), fitz.Point(220, y1), color=(0, 0, 0), width=0.8)
    page.insert_text((80, 115), "Term", fontsize=10)
    page.insert_text((230, 115), "Meaning", fontsize=10)
    page.insert_text((80, 178), "Warranty Period", fontsize=10)
    page.insert_text((230, 158), "for each Deliverable, the period", fontsize=10)
    page.insert_text((230, 172), "specified as such in a SOW.", fontsize=10)
    page.insert_text((80, 225), "Term", fontsize=10)
    page.insert_text((230, 225), "has the meaning given in clause 2.", fontsize=10)
    return document.tobytes()


def _clauses(content: bytes):
    return build(parser_for("application/pdf").parse(content, filename="c.pdf")).clauses


# --- the defect -------------------------------------------------------------


def test_a_term_stays_with_its_own_definition():
    """The failure this file exists for. Read in reading order the term came
    after its definition, and the two became separate clauses."""
    clauses = _clauses(_definitions_table())
    table = next(c for c in clauses if c.clause_type == "table")

    row = next(r for r in table.text.split("\n") if "Warranty Period" in r)
    assert "for each Deliverable" in row, "the term lost its definition"
    assert row.index("Warranty Period") < row.index("for each Deliverable")


def test_a_table_is_one_clause_not_a_scattering_of_cells():
    """Every cell as its own clause makes a definitions list of forty terms into
    eighty unrelated fragments, none of which reads as anything."""
    clauses = _clauses(_definitions_table())

    assert sum(1 for c in clauses if c.clause_type == "table") == 1


def test_table_text_is_not_also_emitted_as_loose_paragraphs():
    """Read once as a table and again in reading order, every cell would appear
    twice — and the second copy would carry the broken ordering."""
    clauses = _clauses(_definitions_table())
    loose = [c for c in clauses if c.clause_type != "table"]

    assert not any("Warranty Period" in c.text for c in loose)


def test_rows_are_tab_separated_like_the_word_parser():
    """A table must read the same whichever format it arrived in, because the
    text is quoted back in citations either way."""
    table = next(c for c in _clauses(_definitions_table()) if c.clause_type == "table")

    assert "Term\tMeaning" in table.text


def test_phantom_empty_columns_are_dropped():
    """Detection reports column boundaries that are empty in every row. Kept,
    they put runs of tabs through the middle of a quoted clause."""
    table = next(c for c in _clauses(_definitions_table()) if c.clause_type == "table")

    assert "\t\t" not in table.text


# --- what must not happen ---------------------------------------------------


def test_no_table_is_invented_in_flowing_prose():
    """The guard that matters most. A contract is mostly prose, and turning a
    page of it into a table would destroy every clause on that page."""
    document = fitz.open()
    page = document.new_page()
    for index, line in enumerate([
        "1 Definitions and Interpretation",
        "The Supplier must perform the Services with the degree of skill and care",
        "reasonably expected of a professional supplier of such services.",
    ]):
        page.insert_text((72, 700 + index * 14), line, fontsize=11)

    clauses = _clauses(document.tobytes())

    assert not any(c.clause_type == "table" for c in clauses)


def test_a_page_that_cannot_be_analysed_costs_its_tables_not_the_document():
    """Detection runs on client paper of unknown quality. A page it chokes on
    must lose its table structure, not take the upload down with it."""
    from app.documents.reader.parsing.pdf import _find_tables

    class _Page:
        def find_tables(self):
            raise RuntimeError("malformed page")

    assert _find_tables(_Page(), 500) == []


def test_an_image_only_page_is_not_searched_for_tables():
    """A scanned page has no table structure to find, and searching it still
    costs real time — up to 8.5 seconds on a 30-page scan. Twelve of thirteen
    documents in the sample corpus are scans, so this is the common path."""
    from app.documents.reader.parsing.pdf import _find_tables

    class _Page:
        def find_tables(self):
            raise AssertionError("an image-only page must not be searched")

    assert _find_tables(_Page(), 0) == []
