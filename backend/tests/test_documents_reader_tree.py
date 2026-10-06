"""Which clause sits inside which, from the strongest evidence first.

Measured on a real employment agreement against an answer key built from its
PDF, the level-stack this replaces placed 36.5% of child clauses correctly: a
paragraph with no number captured every list item after it. These tests pin the
rules that fixed it, each on the shape of the real text that broke it, and the
promise the AI step depends on — what the rules cannot decide is marked, never
guessed, and offered with every clause it could belong to.
"""

from app.documents.reader.tree import Node, build_tree, depths, options, violations


def _nodes(*rows):
    return [Node(label, text) for label, text in rows]


# 4. of the employment agreement, with the paragraph that used to capture (c).
COMPENSATION = _nodes(
    ("4.", "Compensation."),
    ("(a)", "Base Salary. The Company shall pay $600,000."),
    ("(b)", "Retention Bonus. If Executive resigns, then:"),
    ("(i)", "before 2027, repay 100% of the bonus; and"),
    ("(ii)", "before 2030, repay a pro rata portion."),
    (None, "Any required repayment shall be made within thirty days."),
    ("(c)", "Other Bonuses. As the Board determines."),
)


def test_a_paragraph_between_two_list_items_does_not_capture_the_second():
    """The defect this module exists for: (c) sat under "Any required
    repayment…", and every item after it went with it."""
    placements = build_tree(COMPENSATION)

    assert [p.parent for p in placements] == [None, 0, 0, 2, 2, None, 0]
    assert placements[6].source == "list"
    assert violations(COMPENSATION, placements) == []


def test_what_the_rules_cannot_place_is_offered_every_clause_still_open():
    """"Any required repayment…" could continue (ii), (b) or 4. Offering only
    the nearest was how an earlier design put it under (ii)."""
    placements = build_tree(COMPENSATION)

    assert placements[5].source == "undecided"
    assert options(COMPENSATION, placements, 5) == [4, 2, 0, None]


def test_an_answer_fills_only_what_was_undecided():
    """The AI cannot move a clause the numbering placed: an answer for (c) is
    ignored, the answer for the paragraph is taken."""
    placements = build_tree(COMPENSATION, answers={5: 2, 6: 5})

    assert (placements[5].parent, placements[5].source) == (2, "ai")
    assert (placements[6].parent, placements[6].source) == (0, "list")
    assert depths(placements) == [1, 2, 2, 3, 3, 3, 2]


def test_a_list_announced_by_its_introduction_belongs_to_it():
    """"Invoices must comply with the following:" owns the (a), (b) under it."""
    nodes = _nodes(
        ("7.", "Invoicing."),
        (None, "Invoices must comply with the following requirements:"),
        ("(a)", "Invoices must be in English."),
        ("(b)", "The format of the invoice is pdf."),
    )

    placements = build_tree(nodes)

    assert [p.parent for p in placements[2:]] == [1, 1]
    assert placements[2].source == "list"


def test_i_after_h_is_the_ninth_letter_and_after_following_a_numeral():
    """Both readings occur in one agreement: "4.(i) Withholding" continues the
    letters, "(h) … the following: (i) … (ii)" opens a numeral list."""
    letters = _nodes(("4.", "Compensation."), ("(g)", "Vacation."), ("(h)", "Expenses."),
                     ("(i)", "Withholding."))
    numerals = _nodes(("4.", "Termination."), ("(h)", "Cause means any of the following:"),
                      ("(i)", "conviction of a felony;"), ("(ii)", "gross negligence."))

    assert build_tree(letters)[3].parent == 0
    assert [p.parent for p in build_tree(numerals)[2:]] == [1, 1]


def test_a_decimal_sits_in_the_clause_its_number_names():
    """Letting a model place every clause had put 5.5 inside 5.4 on the
    Franklin Madison MSA; the number says 5, so the number decides."""
    nodes = _nodes(("5.", "Payment."), ("5.4", "Taxes."), (None, "Each party bears its own."),
                   ("5.5", "Audit."))

    placements = build_tree(nodes)

    assert placements[3].parent == 0
    assert placements[3].source == "numbering"


def test_a_heading_with_no_number_does_not_capture_the_numbered_clauses_after_it():
    """A page header read as a heading had taken 5.2 to 5.5 as its children."""
    nodes = [Node("5.", "Payment."), Node("5.1", "Fees."),
             Node(None, "MASTER SERVICES AGREEMENT", "heading"), Node("5.2", "Invoices.")]

    assert build_tree(nodes)[3].parent == 0


def test_an_ocr_misread_letter_is_read_as_the_letter_it_continues():
    """"(l) Counterparts" came back "(1)"; read as written it opened a new list
    and took (m) and (n) inside it."""
    nodes = _nodes(("9.", "General."), ("(k)", "Notices."), ("(1)", "Counterparts."),
                   ("(m)", "Headings."))

    placements = build_tree(nodes)

    assert [p.parent for p in placements[1:]] == [0, 0, 0]
    assert placements[3].follows == 2


def test_an_exhibit_starts_its_own_part():
    """A work order numbering from 16.17 is not a continuation of the main
    agreement's 16, and nothing inside it may be placed outside it."""
    nodes = _nodes(("16.", "Miscellaneous."), ("16.16", "Severability."),
                   (None, "Exhibit A - Work Order"), ("16.17", "Project Assumptions."),
                   (None, "The assumptions are listed below."))

    placements = build_tree(nodes)

    assert placements[2].parent is None
    assert placements[3].parent != 0
    assert None not in options(nodes, placements, 4)


def test_an_exhibit_title_before_any_clause_names_the_whole_document():
    """"EXHIBIT 10.2" heads SEC filings; it is the document, not a part of it."""
    nodes = _nodes((None, "EXHIBIT 10.2"), (None, "EMPLOYMENT AGREEMENT"), ("1.", "Term."),
                   ("1.1", "Start."))

    placements = build_tree(nodes)

    assert placements[3].parent == 2
    assert placements[0].part == placements[3].part == 0


def test_word_levels_count_only_inside_their_own_list():
    """An (a)/(b) list under 1.1 is usually a separate Word list starting again
    at level 1. Read as the document's level 1, it sat at the top — and 1.2
    after it went looking for a parent among the letters."""
    nodes = [
        Node("1.", "Definitions.", declared_level=1, declared_list="main"),
        Node("1.1", "Terms.", declared_level=2, declared_list="main"),
        Node("(a)", "first;", declared_level=1, declared_list="letters"),
        Node("(b)", "second.", declared_level=1, declared_list="letters"),
        Node("1.2", "Interpretation.", declared_level=2, declared_list="main"),
    ]

    placements = build_tree(nodes)

    assert [p.parent for p in placements] == [None, 0, 1, 1, 0]
    assert placements[4].source == "document"


def test_the_first_block_and_the_first_line_of_an_exhibit_are_not_questions():
    """With only one place to go there is nothing to ask."""
    nodes = _nodes((None, "MASTER SERVICES AGREEMENT"), ("1.", "Term."),
                   (None, "Exhibit A"), (None, "Scope of the work order."))

    placements = build_tree(nodes)

    assert placements[0].source == "top"
    assert (placements[3].parent, placements[3].source) == (2, "part")


def test_numbered_recitals_are_asked_about_and_the_run_follows_the_answer():
    """"WHEREAS: 1. … 2. …" on a patent licence: recitals numbered like the
    main clauses, which a "1." alone cannot tell apart from "…agree as
    follows: 1. Definitions". Both first items become questions — offering the
    sentence before them, even across the restart of the numbering — and each
    run goes wherever its first item goes."""
    nodes = [
        Node(None, "LICENSE AGREEMENT", "heading"),
        Node(None, "WHEREAS:", "heading"),
        Node("1.", "Licensee wishes to obtain a license."),
        Node("2.", "Licensor is willing to grant it."),
        Node(None, "NOW, THEREFORE, the parties agree as follows:"),
        Node("1.", "Definitions"),
        Node("1.1", '"Agreement" means this License Agreement.'),
        Node("2.", "License Grant"),
    ]

    asked = build_tree(nodes)
    answered = build_tree(nodes, answers={1: 0, 2: 1, 4: None, 5: None})

    assert [asked[i].source for i in (2, 5)] == ["undecided", "undecided"]
    assert 1 in options(nodes, asked, 2) and 4 in options(nodes, asked, 5)
    assert [p.parent for p in answered] == [None, 0, 1, 1, None, None, 5, None]
    assert violations(nodes, answered) == []


def test_every_clause_still_open_is_offered_however_many_lines_are_unplaced():
    """Capping the options at eight nearest-first cut off the right answer for
    six lines of a design certificate, and the AI's correct answers were refused."""
    nodes = [Node("1.", "Registration Certificate")] + [
        Node(None, f"Certificate field {n}:") for n in range(1, 12)
    ]

    placements = build_tree(nodes)

    assert 0 in options(nodes, placements, 11)


def test_a_plain_paragraph_is_never_offered_as_the_parent_of_the_next():
    """Offered the line before, the AI stacked each recital inside the one
    before it and "NOW, THEREFORE" inside the last. A plain paragraph holds
    only what it introduces; a heading or a numbered clause holds the rest."""
    nodes = [
        Node(None, "BACKGROUND", "heading"),
        Node(None, "WHEREAS, LICENSOR has designed a sealing machine;"),
        Node(None, "WHEREAS, LICENSOR owns the patent rights;"),
        Node(None, "NOW, THEREFORE, the parties agree as follows:"),
    ]

    placements = build_tree(nodes)

    assert options(nodes, placements, 2) == [0, None]
    assert options(nodes, placements, 3) == [0, None]


def test_a_finished_sentence_ending_in_below_introduces_nothing():
    """"…the parties referenced in Item 2 below." is a sentence, not an
    introduction: read as one, it took a schedule's Items 2-9 inside Item 1."""
    nodes = [
        Node(None, "Schedule", "heading"),
        Node(None, "Item 1 License Agreement", "heading"),
        Node(None, "THE LICENSE AGREEMENT IS BETWEEN THE PARTIES REFERENCED IN ITEM 2 BELOW."),
        Node("Item 2", "Name and Address of Licensor and Licensee"),
        Node("Item 3", "Other License Terms"),
    ]

    placements = build_tree([Node("1.", "Term.")] + nodes)

    assert [(p.parent, p.source) for p in placements[4:]] == [(1, "part"), (1, "part")]


def test_a_numbered_list_inside_a_clause_is_not_a_new_part():
    """An MSA's 5.2 Rate Revision defines its formula's terms as "1. PO = …"
    to "4. …". Read as numbering starting again — a new part — the terms went
    to the top level and 5.3 could no longer find 5. Now the restart is a
    question, 5.2 among its options, and 5.3 and 6 are placed by their numbers."""
    nodes = [
        Node("5", "Compensation", "heading"),
        Node("5.1", "Basis of Compensation", "heading"),
        Node(None, "The compensation payable by Customer is set out in each Work Order."),
        Node("5.2", "Rate Revision", "heading"),
        Node(None, "The Cost of Living adjustment formula shall be as follows:"),
        Node(None, "P1 = PO x (Price Country Index 1 / Price Country Index 0)"),
        Node("1.", "PO = current price at the date of signature"),
        Node("2.", "P1 = new price at the date of rate revision"),
        Node("3.", "Price Country Index 0 = consumer price index at signature"),
        Node("4.", "Price Country Index 1 = consumer price index at revision"),
        Node("5.3", "Expenses:"),
        Node("6", "Payment Terms", "heading"),
    ]

    asked = build_tree(nodes)
    answered = build_tree(nodes, answers={2: 1, 4: 3, 5: 4, 6: 4})

    assert asked[6].source == "undecided" and {3, 0} <= set(options(nodes, asked, 6))
    assert [p.parent for p in answered] == [None, 0, 1, 0, 3, 4, 4, 4, 4, 4, 0, None]
    assert violations(nodes, answered) == []


def test_a_paragraph_after_a_nested_list_can_still_reach_the_clause_it_ends():
    """16.10's order of precedence lists "1. Master Services Agreement" to
    "3. Work Order". Treated as a new part, the paragraph after the list could
    not be offered 16.10 — the AI's right answer was refused — and 16.11 onwards
    could not find 16."""
    nodes = [
        Node("16.", "Miscellaneous", "heading"),
        Node("16.10", "Order of Precedence", "heading"),
        Node(None, "In the event of conflict, the following order of precedence would prevail:"),
        Node("1.", "Master Services Agreement"),
        Node("2.", "Exhibits"),
        Node("3.", "Work Order"),
        Node(None, "The Parties agree that this Agreement governs any purchase order."),
        Node("16.11", "Publication/Advertising", "heading"),
    ]

    placements = build_tree(nodes)

    assert 1 in options(nodes, placements, 6)
    assert (placements[7].parent, placements[7].source) == (0, "numbering")
