"""A clause keeps its identity across versions when it is recognisably the same.

That identity is what lets a comment follow its clause into the next version
without searching for it (anchoring's rung 1). A content hash would break on
any edit; position alone would break on every insertion above.
"""

from app.docstudio.versions import carry_ids

PREVIOUS = [
    ("term", "TERM. The term of this Agreement is three years."),
    ("fees", "FEES. Payment is due within thirty days of invoice."),
    ("liability", "LIABILITY. Neither party shall be liable for indirect damages."),
    ("notices", "NOTICES. Notices must be given in writing."),
]


def test_insertions_rewording_and_deletion_each_keep_or_lose_identity_as_they_should():
    carried = carry_ids(
        PREVIOUS,
        [
            "TERM. The term of this Agreement is three years.",
            "AUDIT. The Customer may audit the Supplier once a year.",  # inserted
            "FEES. Payment is due within thirty days of invoice.",
            "LIABILITY. Neither party shall be liable for indirect or consequential damages.",
        ],  # NOTICES deleted
    )

    assert carried == ["term", None, "fees", "liability"]


def test_a_clause_replaced_by_a_different_one_is_not_the_same_clause():
    """Sharing a slot is not sharing an identity: a comment on the old
    liability cap must not silently land on a new indemnity clause."""
    carried = carry_ids(
        PREVIOUS[:3], [PREVIOUS[0][1], PREVIOUS[1][1], "INDEMNITY. Each party shall indemnify the other."]
    )

    assert carried == ["term", "fees", None]


def test_line_breaks_and_case_do_not_make_a_clause_new():
    carried = carry_ids(PREVIOUS[:1], ["term.  The term of this\nAgreement is three years."])

    assert carried == ["term"]
