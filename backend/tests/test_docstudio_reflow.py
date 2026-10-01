"""A clause does not stop at the bottom of a page, but a parser does.

Found in a real 14-page scanned MSA: eight of its clauses were cut in half at
page boundaries, because both the native reader and OCR return one block per
page region. Clause 3.2 ended "...shall observe all safety and other applicable
rules in" and the next clause began "effect at such Client Sites, provided
that...". Neither half can be quoted, cited or reviewed on its own.
"""

from app.docstudio.parsing.base import ParsedBlock
from app.docstudio.parsing.reflow import join_wrapped_blocks


def _blocks(*texts, **kw):
    return [ParsedBlock(text=t, **kw) for t in texts]


# --- the defect -------------------------------------------------------------


def test_a_sentence_cut_by_a_page_break_is_rejoined():
    """The failure this file exists for, verbatim from the MSA."""
    joined, joins = join_wrapped_blocks(
        _blocks(
            "Mindtree personnel shall observe all safety and other applicable rules in",
            "effect at such Client Sites, provided that reasonable notice has been supplied.",
        )
    )

    assert len(joins) == 1
    assert joined[0].text.endswith("rules in effect at such Client Sites, "
                                   "provided that reasonable notice has been supplied.")


def test_a_word_broken_across_the_boundary_loses_its_hyphen():
    """Scanned text hyphenates at the margin. Joining with a space leaves
    "compli- ance", which matches nothing a reader or a search would look for."""
    joined, _ = join_wrapped_blocks(_blocks("Client shall ensure compli-", "ance with all laws."))

    assert joined[0].text == "Client shall ensure compliance with all laws."


# --- what must NOT be joined ------------------------------------------------


def test_a_finished_sentence_is_left_alone():
    joined, joins = join_wrapped_blocks(
        _blocks("The Agreement continues for three years.", "thereafter it renews annually.")
    )

    assert len(joins) == 0
    assert len(joined) == 2


def test_a_colon_introducing_a_list_does_not_swallow_the_first_item():
    """"Such Services can be of two (2) types of projects:" introduces (a) and
    (b). Absorbing the first item would hide it from the clause list."""
    _, joins = join_wrapped_blocks(
        _blocks("Such Services can be of two (2) types of projects:",
                "a fixed price project is managed by Mindtree.")
    )

    assert len(joins) == 0


def test_a_numbered_clause_always_starts_a_new_clause():
    """A number means a new clause whatever precedes it. Joining "9.1" onto an
    unterminated line would merge two separately citable obligations."""
    blocks = [
        ParsedBlock(text="the parties agree as follows"),
        ParsedBlock(text="indirect damages are waived", number_label="9.1"),
    ]
    joined, joins = join_wrapped_blocks(blocks)

    assert len(joins) == 0
    assert len(joined) == 2


def test_a_heading_is_never_joined_to_what_follows():
    """"11. GOVERNING LAWS AND DISPUTE RESOLUTION" has no full stop and is
    followed by sub-item "a.". A heading is self-contained."""
    blocks = [
        ParsedBlock(text="GOVERNING LAWS AND DISPUTE RESOLUTION", kind="heading"),
        ParsedBlock(text="a. where a Client is incorporated under Delaware law"),
    ]
    _, joins = join_wrapped_blocks(blocks)

    assert len(joins) == 0


def test_a_table_is_never_joined():
    """A table's rows are tab-separated lines. Merging a paragraph into one
    destroys the grid that makes the fee schedule readable."""
    blocks = [
        ParsedBlock(text="Milestone\tAmount\nFinal\tUSD 2,400,000", kind="table"),
        ParsedBlock(text="payable within thirty days of invoice."),
    ]
    _, joins = join_wrapped_blocks(blocks)

    assert len(joins) == 0


def test_a_capitalised_opening_starts_a_new_paragraph():
    """A block beginning with a capital is far more likely a new sentence the
    parser split for layout than a continuation. Joining those would merge
    genuinely separate paragraphs."""
    _, joins = join_wrapped_blocks(
        _blocks("the Parties agree as follows", "Mindtree shall provide the Services.")
    )

    assert len(joins) == 0


# --- position -----------------------------------------------------------------


def test_the_joined_clause_keeps_the_page_it_starts_on():
    """A clause spanning pages 3 and 4 starts on page 3. Reporting page 4 sends
    a reader to the page where the clause ended."""
    blocks = [
        ParsedBlock(text="Mindtree personnel shall observe all rules in", page_number=3,
                    bbox={"x0": 1, "y0": 2, "x1": 3, "y1": 4}),
        ParsedBlock(text="effect at such Client Sites.", page_number=4),
    ]
    joined, _ = join_wrapped_blocks(blocks)

    assert joined[0].page_number == 3
    assert joined[0].bbox == {"x0": 1, "y0": 2, "x1": 3, "y1": 4}


def test_an_empty_document_is_handled():
    assert join_wrapped_blocks([]) == ([], [])


def test_joining_stops_before_a_page_becomes_one_clause():
    """Reflow had no limit. Ten unterminated fragments merged into a single
    block, turning badly split text into one unreadable clause rather than
    leaving the damage visible where somebody would notice it."""
    from app.docstudio.parsing.reflow import MAX_CONSECUTIVE_JOINS

    joined, _ = join_wrapped_blocks(_blocks(*[f"fragment {i} continues" for i in range(10)]))

    assert len(joined) > 1
    assert all(b.text.count("fragment") <= MAX_CONSECUTIVE_JOINS + 1 for b in joined)


def test_a_sentence_crossing_two_page_boundaries_still_rejoins():
    """The cap must not break the case reflow exists for. A long clause can
    cross more than one page."""
    joined, joins = join_wrapped_blocks(
        _blocks("Mindtree personnel shall observe all safety rules in",
                "effect at such Client Sites, provided that reasonable notice of",
                "the rules has been supplied in writing.")
    )

    assert len(joined) == 1
    assert len(joins) == 2


def test_a_rejoined_clause_keeps_where_both_halves_sit():
    """A viewer lighting up a clause cut by a page break must light up both
    halves. Keeping only the first half's box highlighted half a clause, and
    the join itself must be on record with its reason and pages."""
    joined, joins = join_wrapped_blocks(
        [
            ParsedBlock(text="shall observe all applicable rules in", page_number=3,
                        bbox={"x0": 0.1, "y0": 0.9, "x1": 0.9, "y1": 0.95}),
            ParsedBlock(text="effect at such Client Sites.", page_number=4,
                        bbox={"x0": 0.1, "y0": 0.05, "x1": 0.6, "y1": 0.08}),
        ]
    )

    assert [region["page"] for region in joined[0].all_regions] == [3, 4]
    assert joined[0].joins == ("ended on “in”",)
    assert joins[0]["pages"] == [3, 4]


def test_an_item_ending_in_or_is_never_joined_to_the_next_item():
    """"…the Deliverables; or" then "ii. Mindtree's compliance…" on the
    Franklin Madison MSA: two items of one list, glued into one clause by the
    lower-case rule because "ii." was not read as a number."""
    joined, joins = join_wrapped_blocks(
        _blocks("i. Client's failure to complete the Deliverables; or",
                "ii. Mindtree's compliance with Client's designs; or")
    )

    assert len(joined) == 2 and joins == []


def test_a_sentence_cut_after_a_bare_and_is_still_joined():
    """Only "; or" / ", and" end an item. "…the goods and" is cut mid-sentence."""
    _, joins = join_wrapped_blocks(
        _blocks("The Supplier shall deliver the goods and", "services described in Schedule 1.")
    )

    assert len(joins) == 1
