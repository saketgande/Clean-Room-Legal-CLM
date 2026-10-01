"""Finding an annotation's words again after the document changed under them.

The four cases the plan requires before anything is built on anchoring — a
reworded clause, a paragraph inserted above, a deleted clause, a sentence that
appears twice — and the one found while wiring it in: a short quote from a
deleted clause must be orphaned, not moved to the same words somewhere else.
"""

from app.docstudio.anchoring import MOVED, OK, ORPHANED, ClauseRef, capture, resolve

SEP = "\n\n"


def _version(*clauses: tuple[str, str]) -> tuple[str, list[ClauseRef]]:
    """(clause_id, text) pairs -> the flat text and its clause references."""
    refs, cursor = [], 0
    for clause_id, text in clauses:
        refs.append(ClauseRef(clause_id, text, cursor, cursor + len(text)))
        cursor += len(text) + len(SEP)
    return SEP.join(text for _, text in clauses), refs


def _anchor_on(flat: str, refs: list[ClauseRef], words: str, occurrence: int = 0):
    start = -1
    for _ in range(occurrence + 1):
        start = flat.index(words, start + 1)
    return capture(flat, start, start + len(words), refs)


V1 = _version(
    ("c1", "1. TERM. The term of this Agreement is three years."),
    ("c2", "2. FEES. Payment is due within thirty days of invoice."),
    ("c3", "3. LIABILITY. Neither party shall be liable for indirect damages arising here."),
    ("c4", "4. NOTICES. Notices must be given in writing to the Services address."),
)


def test_a_paragraph_inserted_above_does_not_move_the_comment():
    """The classic failure: every stored offset shifts by the inserted length.
    The clause is found by its identity, and the words inside it."""
    anchor = _anchor_on(*V1, "Payment is due within thirty days")
    flat, refs = _version(
        ("c1", "1. TERM. The term of this Agreement is three years."),
        ("new", "2. AUDIT. The Customer may audit the Supplier once a year."),
        ("c2", "3. FEES. Payment is due within thirty days of invoice."),
    )

    found = resolve(anchor, flat, refs)

    assert (found.state, found.rung, found.clause_id) == (OK, 1, "c2")
    assert flat[found.start : found.end] == "Payment is due within thirty days"


def test_a_reworded_clause_keeps_its_comment_on_the_new_words():
    anchor = _anchor_on(*V1, "Neither party shall be liable for indirect damages")
    reworded = (
        "3. LIABILITY. Neither party shall be liable for indirect or consequential "
        "damages arising here."
    )
    flat, refs = _version(("c3", reworded))

    found = resolve(anchor, flat, refs)

    assert (found.state, found.rung, found.clause_id) == (MOVED, 4, "c3")
    # The whole reworded phrase — not a window the old quote's length, which
    # cut it off at "indirect or cons".
    assert flat[found.start : found.end] == (
        "Neither party shall be liable for indirect or consequential damages"
    )


def test_a_deleted_clause_orphans_its_comment():
    anchor = _anchor_on(*V1, "Notices must be given in writing to the Services address")
    flat, refs = _version(
        ("c1", "1. TERM. The term of this Agreement is three years."),
        ("c2", "2. FEES. Payment is due within thirty days of invoice."),
        ("c3", "3. LIABILITY. Neither party shall be liable for indirect damages arising here."),
    )

    assert resolve(anchor, flat, refs).state == ORPHANED


def test_a_short_quote_from_a_deleted_clause_is_not_moved_to_the_same_words_elsewhere():
    """"the Services" is in many clauses. With its own clause gone, the next
    "the Services" in the contract is a coincidence, not its new home — and a
    comment attached to the wrong clause is worse than one marked lost."""
    flat1, refs1 = _version(
        ("a", "5. The Supplier shall perform the Services with reasonable care."),
        ("b", "6. The Customer may suspend the Services on written notice."),
    )
    anchor = _anchor_on(flat1, refs1, "the Services", occurrence=1)
    flat2, refs2 = _version(("a", "5. The Supplier shall perform the Services with reasonable care."))

    assert resolve(anchor, flat2, refs2).state == ORPHANED


def test_a_sentence_that_appears_twice_resolves_to_the_right_one():
    """Two identical sentences; the comment is on the second. With the clause
    identities gone and every offset shifted, only the words around it can
    tell them apart."""
    repeated = "The Supplier shall comply with all applicable laws."
    flat1, refs1 = _version(
        ("a", f"7. COMPLIANCE. {repeated} Breach is material."),
        ("b", f"8. SUBCONTRACTORS. {repeated} Subcontractors are bound too."),
    )
    anchor = _anchor_on(flat1, refs1, repeated, occurrence=1)
    flat2, refs2 = _version(
        ("x", "6. INSERTED. A new clause above both."),
        ("y", f"8. COMPLIANCE. {repeated} Breach is material."),
        ("z", f"9. SUBCONTRACTORS. {repeated} Subcontractors are bound too."),
    )

    found = resolve(anchor, flat2, refs2)

    assert (found.state, found.rung, found.clause_id) == (OK, 3, "z")


def test_an_unchanged_document_resolves_every_anchor_on_the_first_rung():
    flat, refs = V1
    anchor = _anchor_on(flat, refs, "three years")

    assert resolve(anchor, flat, refs).rung == 1
