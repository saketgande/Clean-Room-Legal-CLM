"""Clause -> obligation provenance matching.

The rule is deliberately strict: an obligation's source quote is a verbatim
span from the contract, so only a clause that literally contains it counts as
the source. A wrong provenance link ("this clause created that duty") is worse
than a missing one, so no fuzzy matching.
"""

from app.contract_brain.entities import clause_for_quote


CLAUSES = [
    ("confidentiality", "The Receiving Party shall hold the Confidential Information in strict confidence "
                        "and use it solely for the Purpose."),
    ("return", "Upon termination of this Agreement or upon the disclosing Party's written request, "
               "the receiving Party shall return or destroy all Confidential Information."),
    ("liability", "Aggregate liability shall not exceed twelve months of fees paid under this Agreement."),
]


def test_quote_matches_its_source_clause():
    q = "the receiving Party shall return or destroy all Confidential Information"
    assert clause_for_quote(q, CLAUSES) == "return"


def test_matching_ignores_whitespace_and_case():
    q = "THE RECEIVING PARTY  SHALL   HOLD the confidential information"
    assert clause_for_quote(q, CLAUSES) == "confidentiality"


def test_a_quote_in_no_clause_matches_nothing():
    q = "The parties shall arbitrate all disputes in Singapore under SIAC rules."
    assert clause_for_quote(q, CLAUSES) is None


def test_too_short_a_quote_is_not_attributed():
    """A 6-character quote could sit in many clauses — attributing it would be
    a guess, so it returns nothing."""
    assert clause_for_quote("shall", CLAUSES) is None


def test_empty_and_none():
    assert clause_for_quote(None, CLAUSES) is None
    assert clause_for_quote("", CLAUSES) is None


def test_first_containing_clause_wins():
    """When two clauses contain the quote, the first is returned deterministically
    rather than an arbitrary one."""
    dupes = [("a", "shared boilerplate sentence appears here"),
             ("b", "shared boilerplate sentence appears here too")]
    assert clause_for_quote("shared boilerplate sentence appears here", dupes) == "a"
