"""A scan stapled into a text PDF: a Schedule or an annexure that is only a picture.

The "this is images, run OCR" decision averaged text over the whole file, so one picture page among
text pages never tripped it, and that page was stored as empty with no warning. Both PDF readers now
send the file to OCR when any page is a picture with no readable text.
"""

import fitz

from app.contract_files.text_extraction import extract_text
from app.documents.reader.parsing.registry import parser_for

PDF_MIME = "application/pdf"


def _body(n: int) -> str:
    # Different on every page: a line repeated on most pages is read as page furniture and dropped.
    return " ".join(
        f"Clause {n}.{i} party number {n * i} shall perform obligation {n * 7 + i} within {n + i} days of notice."
        for i in range(1, 8)
    )


def _pdf(*, picture_page: bool = False, watermark_page: bool = False) -> bytes:
    document = fitz.open()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), False)
    pixmap.clear_with(220)
    for n in range(1, 4):
        page = document.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 540, 700), _body(n), fontsize=11)
    if watermark_page:  # a full-page background image behind a page of ordinary text
        page = document.new_page()
        page.insert_image(page.rect, pixmap=pixmap)
        page.insert_textbox(fitz.Rect(72, 72, 540, 700), _body(4), fontsize=11)
    if picture_page:
        page = document.new_page()
        page.insert_image(page.rect, pixmap=pixmap)
    return document.tobytes()


def test_one_picture_page_in_a_text_pdf_sends_the_file_to_ocr():
    """Guards a stapled-in Schedule being stored as an empty page with no warning, by either reader."""
    content = _pdf(picture_page=True)

    parsed = parser_for(PDF_MIME).parse(content, filename="c.pdf")
    assert parsed.needs_ocr and "Page(s) 4" in " ".join(parsed.warnings)

    legacy = extract_text(content, mime_type=PDF_MIME, filename="c.pdf")
    assert legacy.needs_ocr and legacy.metadata["picture_only_pages"] == [4]


def test_text_pages_and_a_page_with_a_background_image_are_not_sent_to_ocr():
    """Guards over-triggering: a watermark behind a full page of text is still readable text."""
    for content in (_pdf(), _pdf(watermark_page=True)):
        assert not parser_for(PDF_MIME).parse(content, filename="c.pdf").needs_ocr
        assert not extract_text(content, mime_type=PDF_MIME, filename="c.pdf").needs_ocr
