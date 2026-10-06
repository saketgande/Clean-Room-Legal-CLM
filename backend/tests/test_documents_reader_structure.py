"""Clauses the contract never numbered still have to belong somewhere.

Measured on a real employment agreement: 23 of its 93 clauses carried no
number. Twenty-one of those genuinely have none in the original — the title,
the recitals, the notices block, the signature page. Two were continuation
paragraphs of a numbered obligation, left floating at the top level, so opening
clause 4 did not show the sentence saying when the retention bonus becomes
repayable.
"""

from app.documents.reader.parsing.base import ParsedBlock, ParsedDocument
from app.documents.reader.parsing.text import blocks_from_text
from app.documents.reader.structure import build


def _structure(*blocks):
    return build(ParsedDocument(blocks=list(blocks)))


def _numbered(text, label, level=1):
    return ParsedBlock(text=text, number_label=label, level=level)


# --- recital letters --------------------------------------------------------


def test_a_recital_letter_is_read_as_a_label():
    """"A. The Company and Executive are parties to..." is how every recital in
    a US agreement is numbered. Matching only digits and "(a)" left all of them
    unlabelled and uncitable."""
    block = blocks_from_text("A. The Company and Executive are parties to the Prior Agreement.")[0]

    assert block.number_label == "A."
    assert block.text.startswith("The Company")


def test_an_abbreviation_is_not_mistaken_for_a_recital():
    """"U.S." opens with a capital and a dot. Requiring whitespace after the dot
    is what stops it becoming clause "U."."""
    assert blocks_from_text("U.S. Government contracts are excluded.")[0].number_label is None


def test_an_ordinary_sentence_is_not_a_recital():
    assert blocks_from_text("NOW, THEREFORE, in consideration of the mutual...")[0].number_label is None


# --- nesting comes only from the document's own numbers --------------------


def test_nesting_follows_the_numbers_the_document_uses():
    """1.1 belongs under 1. Without the parent link, "the indemnity in 4.2"
    loses the 4."""
    structure = _structure(
        _numbered("DEFINITIONS", "1.", level=1),
        _numbered("Confidential Information means non-public information.", "1.1", level=2),
    )
    parent, child = structure.clauses

    assert child.parent_clause_id == parent.clause_id
    assert parent.parent_clause_id is None


def test_an_unnumbered_paragraph_takes_no_parent():
    """Attaching unnumbered paragraphs to the last numbered clause was tried
    and reverted. It reads correctly on a contract whose clauses are prose, and
    on a document listing partners by name it put 44 body paragraphs underneath
    "(v) Mr. Rama Krishna Gadela". A paragraph the document never numbered keeps
    its place in sequence and claims nothing more."""
    structure = _structure(
        _numbered("Mr. Rama Krishna Gadela, S/o Shri Sinhachalam Naidu", "(v)", level=3),
        ParsedBlock(text="The said Company purchased a HUDA auctioned land."),
    )

    assert structure.clauses[1].parent_clause_id is None


def test_a_new_clause_closes_the_deeper_levels_below_it():
    """Section 2's first sub-clause must attach to 2., not to 1.'s last child."""
    structure = _structure(
        _numbered("DEFINITIONS", "1.", level=1),
        _numbered("Confidential Information.", "1.1", level=2),
        _numbered("TERM", "2.", level=1),
        _numbered("This Agreement continues for three years.", "2.1", level=2),
    )
    _one, _one_one, two, two_one = structure.clauses

    assert two_one.parent_clause_id == two.clause_id
    assert two.parent_clause_id is None
