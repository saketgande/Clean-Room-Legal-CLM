"""Text Word shows that `Paragraph.text` cannot see.

python-docx reads only the runs sitting directly inside a paragraph. Word wraps
runs whenever it has something to record about them — a tracked change, a
hyperlink — and every wrapped run's text is then invisible.

On a counterparty redline that is not a cosmetic loss:

    Liability is capped at [ins: twelve (12) months][del: six (6) months] of fees.

came out as "Liability is capped at  of fees." — a sentence that reads as though
there is no cap at all, on the document that matters most.
"""

from io import BytesIO

from docx import Document
from docx.oxml import parse_xml

from app.docstudio.parsing.registry import DOCX_MIME, parser_for
from app.docstudio.structure import build

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _text(document) -> str:
    buffer = BytesIO()
    document.save(buffer)
    return build(parser_for(DOCX_MIME).parse(buffer.getvalue(), filename="c.docx")).flat_text


def _with_paragraph(xml: str) -> str:
    document = Document()
    document.element.body.insert(0, parse_xml(xml))
    return _text(document)


# --- tracked changes --------------------------------------------------------


REDLINE = f'''<w:p {W}>
  <w:r><w:t xml:space="preserve">Liability is capped at </w:t></w:r>
  <w:ins w:id="1" w:author="Counsel" w:date="2026-01-01T00:00:00Z">
    <w:r><w:t>twelve (12) months</w:t></w:r></w:ins>
  <w:del w:id="2" w:author="Counsel" w:date="2026-01-01T00:00:00Z">
    <w:r><w:delText>six (6) months</w:delText></w:r></w:del>
  <w:r><w:t xml:space="preserve"> of fees.</w:t></w:r>
</w:p>'''


def test_the_counterpartys_proposed_wording_is_extracted():
    """The defect this file exists for. Without it the clause loses the only
    number in it and still reads as a complete sentence, so nothing downstream
    can tell that anything is missing."""
    assert _with_paragraph(REDLINE) == "Liability is capped at twelve (12) months of fees."


def test_deleted_wording_is_not_extracted():
    """The extraction is the document as it reads with changes accepted — the
    wording under negotiation. Keeping the struck text too would produce a
    clause asserting both six and twelve months."""
    assert "six (6) months" not in _with_paragraph(REDLINE)


def test_text_in_a_deletion_is_dropped_even_when_it_is_not_delText():
    """The spec says deleted text uses w:delText, so matching on the tag alone
    would usually work. Some producers put a plain w:t inside w:del, and
    trusting the tag then resurrects struck wording into the live clause."""
    xml = f'''<w:p {W}>
      <w:r><w:t xml:space="preserve">The term is </w:t></w:r>
      <w:del w:id="3" w:author="X" w:date="2026-01-01T00:00:00Z">
        <w:r><w:t>three years</w:t></w:r></w:del>
      <w:r><w:t xml:space="preserve">five years.</w:t></w:r>
    </w:p>'''

    assert _with_paragraph(xml) == "The term is five years."


def test_a_hyperlink_keeps_its_text():
    """Word wraps hyperlink runs the same way it wraps tracked changes, so the
    same blindness silently drops the notices address out of a notices clause."""
    xml = f'''<w:p {W} {R}>
      <w:r><w:t xml:space="preserve">Notices go to </w:t></w:r>
      <w:hyperlink r:id="rId9"><w:r><w:t>legal@birchtech.com</w:t></w:r></w:hyperlink>
      <w:r><w:t xml:space="preserve"> within 30 days.</w:t></w:r>
    </w:p>'''

    assert _with_paragraph(xml) == "Notices go to legal@birchtech.com within 30 days."


def test_tabs_and_line_breaks_survive():
    """A manual line break is how an address is laid out inside one clause, and
    a tab is how a defined term is set out from its definition. `Paragraph.text`
    renders both as nothing, running the words together.

    (A tab straight after a clause number is a different matter: there it is the
    label separator, and `split_label` normalises it to a single space.)
    """
    xml = f'''<w:p {W}>
      <w:r><w:t>James Trettel</w:t><w:br/><w:t>Attn:</w:t><w:tab/><w:t>General Counsel</w:t></w:r>
    </w:p>'''

    assert _with_paragraph(xml) == "James Trettel\nAttn:\tGeneral Counsel"


# --- nested tables ----------------------------------------------------------


def test_a_table_inside_a_table_cell_is_read():
    """Contracts put a fee schedule inside a cell of a layout table. Reading
    only a cell's paragraphs loses the inner table whole — every figure in the
    schedule, with nothing to show it happened."""
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "Schedule of Charges"
    inner = cell.add_table(rows=2, cols=2)
    inner.cell(0, 0).text = "LIABILITY CAP"
    inner.cell(0, 1).text = "USD 5,000,000"
    inner.cell(1, 0).text = "Payment terms"
    inner.cell(1, 1).text = "Net 30"

    text = _text(document)

    assert "USD 5,000,000" in text
    assert "Net 30" in text
    assert "Schedule of Charges" in text


def test_a_tracked_change_inside_a_table_cell_is_read():
    """Both defects at once, which is how they appear in a redlined schedule."""
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.paragraphs[0]._p.addnext(
        parse_xml(f'''<w:p {W}><w:ins w:id="9" w:author="X" w:date="2026-01-01T00:00:00Z">
          <w:r><w:t>USD 7,500,000</w:t></w:r></w:ins></w:p>''')
    )

    assert "USD 7,500,000" in _text(document)


def test_an_ordinary_table_still_reads_one_row_per_line():
    """The regression guard: cells tab-separated, rows newline-separated, which
    is what makes a table readable when it is quoted back in a citation."""
    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Milestone"
    table.cell(0, 1).text = "Amount"
    table.cell(1, 0).text = "Final delivery"
    table.cell(1, 1).text = "USD 2,400,000"

    assert "Milestone\tAmount\nFinal delivery\tUSD 2,400,000" in _text(document)
