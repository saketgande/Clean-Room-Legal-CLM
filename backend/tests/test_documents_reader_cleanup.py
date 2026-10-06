"""Removing what is not contract text, before anything is joined.

Every removal comes back with its reason: a footer or stamp removed by mistake
would otherwise be invisible, because the clause list would simply be shorter.
"""

from app.documents.reader.parsing.base import ParsedBlock
from app.documents.reader.parsing.cleanup import clean


def _box(y):
    return {"x0": 0.7, "y0": y, "x1": 0.9, "y1": y + 0.04}


def test_a_numbered_block_is_kept_whatever_the_provider_calls_it():
    """A clause the provider mislabelled "Header" would vanish with nothing on
    the page to show for it. The label alone is not enough to drop a number."""
    kept, removed, _ = clean(
        [ParsedBlock(text="Payment Terms.", number_label="5.", role="Header", page_number=2)],
        page_count=4,
    )

    assert len(kept) == 1 and removed == []


def test_a_stamp_in_the_same_spot_on_several_pages_is_removed():
    """OCR reads a rubber stamp differently every time; its position does not
    change. Three pages in the same spot is a stamp, not a clause."""
    blocks = [
        ParsedBlock(text=text, page_number=page, bbox=_box(0.9 + page * 0.005))
        for page, text in ((1, "DTREE LIMITE"), (2, "MINDTREE LTD"), (3, "BANGALOR"))
    ] + [ParsedBlock(text="The Client shall pay within thirty days.", page_number=3,
                     bbox={"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.3})]

    kept, removed, chars = clean(blocks, page_count=3)

    assert [b.text for b in kept] == ["The Client shall pay within thirty days."]
    assert {r["reason"] for r in removed} == {"stamp in the same spot on several pages"}
    assert chars == sum(len(r["text"]) for r in removed)


def test_headings_in_the_same_spot_on_several_pages_are_not_a_stamp():
    """A well-typeset contract opens each page's section in the same place:
    short, unnumbered and in one spot, exactly like a stamp — but headings."""
    blocks = [
        ParsedBlock(text=text, kind="heading", page_number=page, bbox=_box(0.1))
        for page, text in ((1, "DEFINITIONS"), (2, "PAYMENT"), (3, "TERM"))
    ]

    kept, _, _ = clean(blocks, page_count=3)

    assert len(kept) == 3


def test_a_block_that_is_not_words_is_removed_and_said_so():
    kept, removed, _ = clean(
        [ParsedBlock(text="~ |"), ParsedBlock(text="Either party may terminate.")], page_count=1
    )

    assert [b.text for b in kept] == ["Either party may terminate."]
    assert removed[0]["reason"] == "not words"


def test_a_page_counter_is_removed_even_though_it_reads_as_a_number():
    """"1 of 4" on a patent licence's drawing page was read as clause 1; the
    numbering seemed to restart, and the schedule's last Items fell outside it."""
    kept, removed, _ = clean(
        [
            ParsedBlock(text="of 4", number_label="1", page_number=26),
            ParsedBlock(text="Page 7 of 19", page_number=15),
            ParsedBlock(text="Payment is due within 30 days of invoice.", number_label="1"),
        ],
        page_count=30,
    )

    assert [b.text for b in kept] == ["Payment is due within 30 days of invoice."]
    assert {r["reason"] for r in removed} == {"page number"}
