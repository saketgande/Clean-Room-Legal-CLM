"""A scanned clause has to know where it sits on the page.

OCR returns a long string, so a clause built from it carried no page number and
no rectangle — every scanned document's citations could be quoted but never
shown on the page they came from. Twelve of thirteen documents in the sample
corpus are scans, so that was almost all of them.

The provider had the geometry all along. `chunk.blocks[].bbox` gives a page and
a rectangle per block; the client was reading `chunk.content` and discarding
the rest.
"""

from dataclasses import dataclass

from app.docstudio.ocr import OcrResult
from app.docstudio.parsing.base import ParsedDocument
from app.docstudio.service import _apply_ocr, _blocks_from_ocr


def _scanned() -> ParsedDocument:
    return ParsedDocument(blocks=[], page_count=11, warnings=["scanned"], needs_ocr=True)


@dataclass
class _Provider:
    name: str
    result: OcrResult

    def extract(self, content, *, filename, mime_type):
        return self.result


def _blocks(*entries) -> OcrResult:
    return OcrResult(
        text="\n\n".join(e["content"] for e in entries),
        provider="reducto",
        quality=0.9,
        blocks=list(entries),
    )


# --- the defect -------------------------------------------------------------


def test_a_clause_from_ocr_keeps_its_page_and_rectangle():
    """The failure this file exists for."""
    result = _blocks(
        {"content": "1. Services. The Consultant shall provide the Services.",
         "type": "text", "page": 1,
         "bbox": {"x0": 0.17, "y0": 0.37, "x1": 0.83, "y1": 0.51}},
    )

    blocks = _blocks_from_ocr(result)

    assert len(blocks) == 1
    assert blocks[0].page_number == 1
    assert blocks[0].bbox == {"x0": 0.17, "y0": 0.37, "x1": 0.83, "y1": 0.51}


def test_the_clause_number_is_still_read_from_the_text():
    """Using the provider's blocks must not cost the numbering that splitting
    the text gave us."""
    blocks = _blocks_from_ocr(_blocks(
        {"content": "2.1 Contract Coordinator", "type": "title", "page": 2, "bbox": None},
    ))

    assert blocks[0].number_label == "2.1"
    assert blocks[0].level == 2
    assert not blocks[0].text.startswith("2.1")


def test_the_providers_own_block_type_is_used_for_headings():
    """The provider labels a block "title" — better evidence than anything
    inferable from the words, which is all the text splitter has."""
    blocks = _blocks_from_ocr(_blocks(
        {"content": "Master Service Agreement", "type": "Title", "page": 1, "bbox": None},
        {"content": "This agreement is made between the parties.", "type": "text",
         "page": 1, "bbox": None},
    ))

    assert [b.kind for b in blocks] == ["heading", "paragraph"]


# --- falling back -----------------------------------------------------------


def test_a_provider_that_returns_only_text_still_works():
    """Not every provider reports blocks. Returning nothing here lets the
    caller split the flat string instead, rather than producing no clauses."""
    assert _blocks_from_ocr(OcrResult(text="1. Services.", provider="x")) == []


def test_ingest_falls_back_to_splitting_the_text():
    """End to end: a provider with no geometry must still produce clauses."""
    provider = _Provider("plain", OcrResult(
        text="1. Services. The Consultant shall provide.\n\n2. Term. Three years.",
        provider="plain",
    ))

    parsed, used, _ = _apply_ocr(
        _scanned(), b"%PDF", filename="c.pdf", mime_type="application/pdf",
        providers=[provider],
    )

    assert used == "plain"
    assert [b.number_label for b in parsed.blocks] == ["1.", "2."]
    assert all(b.bbox is None for b in parsed.blocks)


def test_blocks_are_preferred_over_splitting_the_text():
    """When both are available the blocks win, because only they carry the
    geometry — splitting the same text again would discard it."""
    provider = _Provider("reducto", _blocks(
        {"content": "1. Services.", "type": "text", "page": 1,
         "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.3}},
    ))

    parsed, _, _ = _apply_ocr(
        _scanned(), b"%PDF", filename="c.pdf", mime_type="application/pdf",
        providers=[provider],
    )

    assert parsed.blocks[0].bbox is not None


def test_an_empty_block_is_dropped():
    """Providers emit blocks for whitespace and page furniture. Stored, each
    becomes a clause a reviewer has to read and dismiss."""
    blocks = _blocks_from_ocr(_blocks(
        {"content": "   ", "type": "text", "page": 1, "bbox": None},
        {"content": "1. Services.", "type": "text", "page": 1, "bbox": None},
    ))

    assert len(blocks) == 1


# --- one coordinate system --------------------------------------------------


def test_native_pdf_boxes_are_page_fractions_too():
    """The OCR path reports boxes as a fraction of the page, and a consumer
    cannot tell two coordinate systems apart by looking at the numbers. Both
    paths normalise, which is also what a viewer needs: fractions hold at any
    zoom and any rendered resolution."""
    import warnings

    import fitz

    warnings.filterwarnings("ignore")
    from app.docstudio.parsing.registry import parser_for
    from app.docstudio.structure import build

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 700), "1. TERM. This Agreement runs for three years.", fontsize=11)

    clauses = build(parser_for("application/pdf").parse(document.tobytes(), filename="c.pdf")).clauses

    box = clauses[0].bbox
    assert box is not None
    assert all(0.0 <= value <= 1.0 for value in box.values())
    # 72pt from the left of a 595pt page is about an eighth of the way across.
    assert 0.10 < box["x0"] < 0.14


# --- what the provider says a block is --------------------------------------


def test_a_page_header_is_not_read_as_a_heading():
    """Reducto's "Header" is the page's running header. Read as a heading, one
    had taken clauses 5.2 to 5.5 of the Franklin Madison MSA as its children."""
    blocks = _blocks_from_ocr(_blocks(
        {"content": "DocuSign Envelope ID: D6EA132C", "type": "Header", "page": 3, "bbox": None},
        {"content": "5. PAYMENT TERMS", "type": "Section Header", "page": 3, "bbox": None},
    ))

    assert (blocks[0].kind, blocks[0].role) == ("paragraph", "Header")
    assert blocks[1].kind == "heading"


def test_a_stamp_between_two_halves_of_a_clause_no_longer_keeps_them_apart():
    """Clause 3.2 of the Franklin Madison MSA stayed cut in two: the OCR had
    described the seal in words between its halves, and the joiner only looks
    at neighbours. Cleanup runs first now, and says what it removed."""
    result = _blocks(
        {"content": "3.2 Mindtree personnel shall observe all applicable rules in",
         "type": "List Item", "page": 3, "bbox": {"x0": 0.1, "y0": 0.9, "x1": 0.9, "y1": 0.95}},
        {"content": "Blue circular Mindtree Limited stamp/seal", "type": "Figure", "page": 3,
         "bbox": {"x0": 0.7, "y0": 0.95, "x1": 0.9, "y1": 0.99}},
        {"content": "Page 3 of 11", "type": "Footer", "page": 3, "bbox": None},
        {"content": "effect at such Client Sites.", "type": "Text", "page": 4,
         "bbox": {"x0": 0.1, "y0": 0.05, "x1": 0.6, "y1": 0.08}},
    )

    parsed, _, _ = _apply_ocr(
        _scanned(), b"%PDF", filename="c.pdf", mime_type="application/pdf",
        providers=[_Provider("reducto", result)],
    )

    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].text.endswith("rules in effect at such Client Sites.")
    assert [r["page"] for r in parsed.blocks[0].all_regions] == [3, 4]
    removed = {r["reason"] for r in parsed.artifacts["removed"]}
    assert removed == {"figure or stamp", "page footer"}
    assert parsed.artifacts["joined"][0]["pages"] == [3, 4]
