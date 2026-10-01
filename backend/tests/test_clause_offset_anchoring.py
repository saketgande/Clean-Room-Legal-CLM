"""Clause offsets must locate real text, not repeat what the model claimed.

Guards the citation-integrity failure found across the corpus: a model reads the
document but cannot count it, so the offsets it reports drift further the deeper
a clause sits (median 68 chars off overall, 188 past 6k). 57% of stored offsets
pointed at the wrong span and quoted the wrong clause back to a lawyer.
"""

from app.contract_files.blocks import locate_phrase

# Padded so a clause sits deep enough that a model's guess would realistically
# drift — the shallow-clause case never exposed the bug.
DOC = (
    "MASTER SERVICES AGREEMENT\n\n"
    + "Recitals. " + ("The parties have discussed the engagement at length. " * 40)
    + "\n\n1. TERM\nThis Agreement continues for thirty-six (36) months.\n\n"
    "2. LIMITATION OF LIABILITY\n"
    "Supplier's total aggregate liability shall not exceed the fees paid in the "
    "three (3) months preceding the claim.\n\n"
    "3. GOVERNING LAW\nThis Agreement is governed by the laws of the Cayman Islands."
)


def test_located_span_is_the_clause_not_the_models_guess():
    clause = "This Agreement is governed by the laws of the Cayman Islands."
    span = locate_phrase(DOC, clause)
    assert span is not None
    assert DOC[span[0] : span[1]] == clause


def test_offsets_resolve_for_every_clause_regardless_of_depth():
    # The drift was positional: clauses near the end were the badly-placed ones.
    for clause in (
        "This Agreement continues for thirty-six (36) months.",
        (
            "Supplier's total aggregate liability shall not exceed the fees paid in the "
            "three (3) months preceding the claim."
        ),
        "This Agreement is governed by the laws of the Cayman Islands.",
    ):
        span = locate_phrase(DOC, clause)
        assert span is not None, clause
        assert DOC[span[0] : span[1]] == clause


def test_paraphrased_quote_still_anchors_to_real_text():
    # Models drop and reflow words; an exact search alone would miss and the
    # citation would be dropped even though the clause is genuinely there.
    span = locate_phrase(DOC, "This Agreement continues for thirty-six (36)  months.")
    assert span is not None
    assert "thirty-six (36) months" in DOC[span[0] : span[1]]


def test_text_absent_from_the_document_yields_none_not_a_wrong_span():
    # The whole point: a hallucinated clause must produce an unlocated citation,
    # never a plausible-looking offset pointing at an unrelated clause.
    assert locate_phrase(DOC, "Either party may terminate for convenience.") is None
    assert locate_phrase(DOC, "") is None
    assert locate_phrase(DOC, None) is None
    assert locate_phrase("", "anything") is None
