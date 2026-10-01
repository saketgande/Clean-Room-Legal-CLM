"""The document model written out as a .docx.

Everything a run carries — family, size, bold, italic, colour — is written on
the run itself rather than left to a style, because a style is the first thing
a lawyer's own template overwrites. The page keeps the PDF's paper size and the
margins its text actually used, the running header and footer go in Word's
header and footer, and a page number becomes Word's PAGE field so it stays
right while the document is edited.
"""

from __future__ import annotations

import io

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor

from .toword import Doc, Para, Picture, Table

ALIGN = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
         "both": WD_ALIGN_PARAGRAPH.JUSTIFY}


def build(doc: Doc) -> bytes:
    """The model as Word bytes."""
    document = Document()
    _paper(document, doc)
    for piece in doc.body:
        _piece(document, piece, doc)
    _margins(document, doc)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _paper(document: Document, doc: Doc) -> None:
    section = document.sections[0]
    section.page_width, section.page_height = Pt(doc.paper.width), Pt(doc.paper.height)
    section.left_margin, section.right_margin = Pt(doc.paper.left), Pt(doc.paper.right)
    section.top_margin, section.bottom_margin = Pt(doc.paper.top), Pt(doc.paper.bottom)
    section.header_distance = Pt(max(doc.paper.top / 3, 12))
    section.footer_distance = Pt(max(doc.paper.bottom / 3, 12))
    # Word's own default is a blank line under every paragraph; the PDF's spacing
    # is measured and written on each one instead.
    normal = document.styles["Normal"]
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.space_before = Pt(0)


def _piece(document: Document, piece, doc: Doc) -> None:
    if isinstance(piece, Para):
        _paragraph(document.add_paragraph(), piece)
    elif isinstance(piece, Table):
        _table(document, piece, doc)
    elif isinstance(piece, Picture) and piece.content:
        _picture(document.add_paragraph(), piece)


def _paragraph(paragraph, para: Para):
    style = paragraph.paragraph_format
    style.alignment = ALIGN.get(para.align)
    style.left_indent = Pt(para.left)
    style.first_line_indent = Pt(para.first)
    style.space_before = Pt(para.before)
    style.space_after = Pt(0)
    if para.pitch:
        # At least what the page gave the line: bigger text inside it still fits.
        style.line_spacing = Pt(para.pitch)
        paragraph.paragraph_format.element.get_or_add_pPr().find(qn("w:spacing")).set(
            qn("w:lineRule"), "atLeast")
    if para.page_break:
        paragraph.add_run().add_break(WD_BREAK.PAGE)
    for run in para.runs:
        _run(paragraph, run)
    return paragraph


def _run(paragraph, run) -> None:
    made = paragraph.add_run(run.text)
    made.bold, made.italic = run.bold, run.italic
    made.font.size = Pt(run.size)
    made.font.name = run.font
    # A run's font is named three times in Word: for latin, for symbols and for
    # east-asian text. Named once, the other two fall back to the theme's.
    properties = made._element.get_or_add_rPr().get_or_add_rFonts()
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        properties.set(qn(attribute), run.font)
    if run.colour:
        made.font.color.rgb = RGBColor((run.colour >> 16) & 255, (run.colour >> 8) & 255, run.colour & 255)


def _picture(paragraph, picture: Picture) -> None:
    for content in (picture.content, _as_png(picture.content)):
        if not content:
            continue
        try:
            paragraph.add_run().add_picture(io.BytesIO(content), width=Pt(picture.width))
            return
        except Exception:   # not a picture Word takes as it stands; try it as a PNG
            continue


def _as_png(content: bytes) -> bytes:
    """A logo drawn in a colour space Word does not read — a CMYK JPEG is the
    usual one — written out as the PNG it does."""
    import fitz

    try:
        return fitz.Pixmap(content).tobytes("png")
    except Exception:
        return b""


def _table(document: Document, table: Table, doc: Doc) -> None:
    columns = max(len(row) for row in table.rows)
    made = document.add_table(rows=len(table.rows), cols=columns)
    made.alignment = WD_TABLE_ALIGNMENT.LEFT
    made.autofit = False
    if table.ruled:
        made.style = "Table Grid"
    else:
        _no_lines(made)
    width = doc.paper.width - doc.paper.left - doc.paper.right
    for index, row in enumerate(table.rows):
        for place in range(columns):
            cell = made.cell(index, place)
            cell.width = Emu(int(Pt(width * (table.widths[place] if place < len(table.widths)
                                             else 1 / columns))))
            paragraphs = row[place] if place < len(row) else []
            for number, para in enumerate(paragraphs):
                # Indentation measured from the page's edge means nothing in a
                # cell: the cell is already at that place.
                para.left, para.first = 0.0, 0.0
                _paragraph(cell.paragraphs[0] if number == 0 else cell.add_paragraph(), para)


def _no_lines(table) -> None:
    properties = table._tbl.tblPr
    borders = properties.makeelement(qn("w:tblBorders"), {})
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        line = borders.makeelement(qn(f"w:{edge}"), {qn("w:val"): "none"})
        borders.append(line)
    properties.append(borders)


def _margins(document: Document, doc: Doc) -> None:
    """The running header and footer into Word's own, once for the document."""
    if not doc.header and not doc.footer:
        return
    section = document.sections[0]
    for where, paragraphs in (("header", doc.header), ("footer", doc.footer)):
        if not paragraphs:
            continue
        part = section.header if where == "header" else section.footer
        part.is_linked_to_previous = False
        for empty in list(part.paragraphs):
            empty._element.getparent().remove(empty._element)
        for para in paragraphs:
            if isinstance(para, Picture):
                _picture(part.add_paragraph(), para)
                continue
            made = _paragraph(part.add_paragraph(), para)
            if doc.numbered == where and _is_number(para):
                _page_field(made)


def _is_number(para: Para) -> bool:
    from .toword import _page_number

    return _page_number(para.text.strip().lower())


def _page_field(paragraph) -> None:
    """The digits replaced by Word's page number, so it stays right as the
    document is edited."""
    keep = paragraph.runs[0]._element.find(qn("w:rPr")) if paragraph.runs else None
    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)
    for kind, text in (("begin", None), (None, " PAGE "), ("separate", None), (None, "1"), ("end", None)):
        run = paragraph.add_run()
        if keep is not None:
            run._element.insert(0, keep.__copy__())
        if kind:
            mark = run._element.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): kind})
            run._element.append(mark)
        elif text == " PAGE ":
            field = run._element.makeelement(qn("w:instrText"), {qn("xml:space"): "preserve"})
            field.text = text
            run._element.append(field)
        else:
            run.text = text


# --- the whole conversion, checked -------------------------------------------------------


def convert(content: bytes) -> tuple[bytes, dict]:
    """A PDF as a Word document, and what the conversion kept.

    Two things are checked, because a conversion that quietly loses or scrambles
    words would make every clause, answer and note taken from it wrong:

    * every word of the PDF is in the Word file;
    * every line the PDF printed is still one run of words in it — the test that
      catches a two-column page read straight across, where no word is lost and
      every sentence is ruined.
    """
    from collections import Counter

    import fitz

    from .toword import ConversionError, _flat, _furniture, _page_number, read

    doc = read(content)
    made = build(doc)
    said = _said(made)
    with fitz.open(stream=content, filetype="pdf") as pdf:
        where_of, _, _ = _furniture(pdf)
        want: Counter = Counter()
        lines: list[str] = []
        once: set[str] = set()
        for page in pdf:
            for block in page.get_text("dict")["blocks"]:
                # The running header and footer are printed on every page and
                # belong in Word's margins once; counted per page they would
                # look like words the conversion had lost.
                said_here = _flat("".join(span["text"] for line in block.get("lines", [])
                                          for span in line["spans"]))
                furniture = said_here in where_of or _page_number(said_here)
                if furniture and said_here in once:
                    continue
                once.add(said_here)
                for line in block.get("lines", []):
                    text = _plain(" ".join(span["text"] for span in line["spans"]))
                    want.update(_squash(text))
                    if len(text.split()) >= 6 and not furniture:
                        lines.append(text)
        pages = pdf.page_count
    # Letters, not words: a PDF prints "( 18 )" where Word writes "(18)", and
    # counting words would call the difference a loss.
    kept = sum((want & Counter(_squash(said))).values()) / max(sum(want.values()), 1)
    if kept < 0.98:
        raise ConversionError(f"Only {kept:.0%} of the PDF's words came through into Word.")
    flat = _squash(said)
    whole = sum(1 for line in lines if _squash(line) in flat)
    if lines and whole < 0.95 * len(lines):
        raise ConversionError(
            f"Only {whole / len(lines):.0%} of the PDF's printed lines came through whole — its "
            "columns or boxes were read across.")
    return made, {"pages": pages, "words_kept": round(kept, 4),
                  "lines_whole": round(whole / len(lines), 4) if lines else 1.0,
                  "paragraphs": sum(1 for piece in doc.body if hasattr(piece, "runs")),
                  "header": bool(doc.header), "footer": bool(doc.footer)}


def _said(content: bytes) -> str:
    """Everything the built file says, as one line of words."""
    document = Document(io.BytesIO(content))
    pieces = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        pieces += [cell.text for row in table.rows for cell in row.cells]
    for section in document.sections:
        pieces += [p.text for p in section.header.paragraphs] + [p.text for p in section.footer.paragraphs]
    return _plain(" ".join(pieces))


def _squash(text: str) -> str:
    """Letters and digits only: the spacing a PDF prints between them is its
    own, and Word writes it differently."""
    import re

    return re.sub(r"[^0-9a-z]+", "", text.lower())


def _plain(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()
