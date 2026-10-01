"""A page map only means something against the exact text it was measured on.

When OCR replaced the extracted text, the native extractor's page map was
carried across unchanged — so every offset pointed into a string that no longer
existed. A 200-page scan whose native extraction yielded 200 characters of
noise and whose OCR yielded 80,000 got a map claiming page 1 was characters
0–50. Citations and per-page field capture were confidently wrong.

No page map is a missing citation. A stale one is a false citation.
"""

from app.integrations.ocr import page_map_from_elements

PAGE_ONE = "MASTER SERVICES AGREEMENT"
PAGE_ONE_BODY = "This Agreement is entered into between Acme and Widgets Ltd."
PAGE_TWO = "9. LIMITATION OF LIABILITY"
PAGE_TWO_BODY = "Aggregate liability shall not exceed twelve months of fees."

ELEMENTS = [
    {"type": "title", "content": PAGE_ONE, "page_id": 0},
    {"type": "text", "content": PAGE_ONE_BODY, "page_id": 0},
    {"type": "section_header", "content": PAGE_TWO, "page_id": 1},
    {"type": "text", "content": PAGE_TWO_BODY, "page_id": 1},
]
TEXT = "\n".join(e["content"] for e in ELEMENTS)


def _slice(text, page_map, page):
    span = page_map[str(page)]
    return text[span["start"]:span["end"]]


def test_offsets_actually_slice_the_page_they_name():
    """The whole point: the map has to index the text being stored, so slicing
    by it returns that page and nothing from its neighbours."""
    page_map = page_map_from_elements(TEXT, ELEMENTS)

    assert _slice(TEXT, page_map, 1) == f"{PAGE_ONE}\n{PAGE_ONE_BODY}"
    assert _slice(TEXT, page_map, 2) == f"{PAGE_TWO}\n{PAGE_TWO_BODY}"


def test_pages_are_one_based_like_every_other_page_map():
    """`page_id` is 0-based; the extractor's own map and the `[Page N]` markers
    are 1-based. Off by one here would shift every citation by a page."""
    page_map = page_map_from_elements(TEXT, ELEMENTS)
    assert sorted(page_map) == ["1", "2"]


def test_a_stripped_leading_newline_does_not_shift_every_offset():
    """Both providers strip the assembled text before returning it. If the map
    is measured on the unstripped join, every offset is off by the whitespace
    that was removed."""
    elements = [{"type": "text", "content": "", "page_id": 0}, *ELEMENTS]
    text = "\n".join(e["content"] for e in elements).strip()

    page_map = page_map_from_elements(text, elements)
    assert _slice(text, page_map, 1) == f"{PAGE_ONE}\n{PAGE_ONE_BODY}"


def test_elements_that_do_not_reproduce_the_text_yield_no_map():
    """The refusal that makes this safe. If the reconstruction disagrees with
    the text being stored, the offsets cannot be trusted — and returning None
    is the correct answer, not a best guess."""
    assert page_map_from_elements("completely different text", ELEMENTS) is None


def test_a_provider_without_page_ids_yields_no_map():
    """Reducto's chunks carry no page information. Inventing one would be the
    original bug in a new costume."""
    reducto_shaped = [{"type": "text", "content": PAGE_ONE}, {"type": "text", "content": PAGE_TWO}]
    assert page_map_from_elements(f"{PAGE_ONE}\n{PAGE_TWO}", reducto_shaped) is None


def test_no_elements_at_all_yields_no_map():
    assert page_map_from_elements(TEXT, []) is None
    assert page_map_from_elements(TEXT, None) is None


def test_a_page_split_across_non_adjacent_elements_spans_both():
    """Parsers interleave elements; a page's span is from its first element to
    its last, not just the first one seen."""
    elements = [
        {"content": "page one opening", "page_id": 0},
        {"content": "page one closing", "page_id": 0},
    ]
    text = "\n".join(e["content"] for e in elements)

    page_map = page_map_from_elements(text, elements)
    assert _slice(text, page_map, 1) == text
