"""The per-version report, and the false positives it must not raise.

Every check here runs in code rather than being left to a model, because the
two defects worth finding most — a clause cut in half and a missing clause
number — are both *absences*, and absence is what language models measurably
cannot see (AbsenceBench, arXiv 2506.11440: 69.6% F1 on removed content against
near-superhuman retrieval of content that is present).

Half of these tests guard against the report crying wolf. The first run over a
real 11-page scan produced 31 findings of which 21 were noise — repeated `(b)`
labels, list items ending in "; or", signature blocks with no full stop. A
checker nobody trusts is worse than no checker, because it trains a reviewer to
skip the one finding that mattered.
"""

from io import BytesIO

import pytest
from docx import Document
from sqlalchemy import select

import app.models  # noqa: F401  (register every mapper)
from app.core.database import SessionLocal
from app.docstudio.models import DsClause, DsDocument, DsEvent, DsVersion
from app.docstudio.parsing.registry import DOCX_MIME
from app.docstudio.report import findings, report
from app.docstudio.service import ingest

ORG = "docstudio-report-test-org"


def _clause(text, *, seq=0, label=None, kind="paragraph", page=None, bbox=None, level=1):
    return DsClause(
        org_id=ORG,
        version_id="v",
        clause_id=f"cl_{seq}",
        seq=seq,
        number_label=label,
        clause_type=kind,
        level=level,
        text=text,
        char_start=0,
        char_end=len(text),
        page_number=page,
        bbox=bbox,
    )


def _codes(clauses, **kwargs) -> list[str]:
    return [finding.code for finding in findings(clauses, **kwargs)]


def _detail(clauses, code, **kwargs) -> str:
    return next(f.detail for f in findings(clauses, **kwargs) if f.code == code)


# --- a clause cut in half ---------------------------------------------------


def test_a_sentence_cut_at_a_page_break_is_flagged():
    """The real one, from MSA_2020_6_000676 p1. Its missing tail — `"Compensation")`
    — was stored as a separate clause on p2, so neither half can be quoted and
    the clause list shows a sentence that simply stops."""
    clauses = [
        _clause(
            "4. Compensation. As consideration for the performance of the Services, "
            "the Foundation shall pay the Consultant the amount set forth in each "
            "Service Order for the relevant Project (collectively, the",
            label="4.",
            page=1,
        )
    ]

    assert "CUT OFF" in _codes(clauses)


def test_a_list_item_ending_in_or_is_not_a_cut_sentence():
    """Correct drafting, not damage: a list item ends "; or" and continues into
    the next item on purpose. Four of the first run's eighteen CUT OFF findings
    were this, which is how a real finding gets lost in the noise."""
    clauses = [
        _clause(
            "(iii) any unauthorized modification of the Services or deliverables, "
            "or the combination of the Services with products/services not "
            "furnished by the Consultant; or",
            label="(iii)",
            page=4,
        ),
        _clause(
            "(iv) the Foundation's failure to pay any invoice within thirty (30) "
            "days after it is rendered, and",
            label="(iv)",
            page=4,
        ),
    ]

    assert "CUT OFF" not in _codes(clauses)


def test_a_signature_block_is_not_a_cut_sentence():
    """A signature block and a notices address are laid out over several lines
    and end on a name or a date. Neither has a full stop, and neither is
    broken — two of these appeared on the sample document's last two pages."""
    clauses = [
        _clause(
            "By: /s/ Nicholas Ashford\nName: Nicholas Ashford\nTitle: Executive Vice "
            "President & COO\n26 - MAR - 2013\nMINDTREE LIMITED",
            page=10,
        )
    ]

    assert "CUT OFF" not in _codes(clauses)


def test_a_clause_ending_on_a_job_title_is_not_a_cut_sentence():
    """Truncation cuts a sentence, so it stops on an ordinary lower-case word.
    Ending on a proper noun is a party name or a title, and the notices clause
    of every contract ends on one."""
    clauses = [
        _clause(
            "If to the Foundation: The Ford Foundation, 320 East 43rd Street, "
            "New York, NY 10017, Attention: David Roth, Chief Technology Officer",
            page=7,
        )
    ]

    assert "CUT OFF" not in _codes(clauses)


# --- numbering --------------------------------------------------------------


def test_a_missing_clause_number_is_named():
    """Pure arithmetic, and the reason this is code and not a model: the gap
    leaves no mark in the text, so there is nothing for attention to find."""
    clauses = [
        _clause("3. Term. Three years from the Effective Date.", seq=0, label="3.", page=2),
        _clause("5. Fees. As set out in Schedule A.", seq=1, label="5.", page=2),
        _clause("6. Notices. In writing.", seq=2, label="6.", page=2),
    ]

    detail = _detail(clauses, "NUMBERING GAP")

    assert "no clause 4" in detail
    # Naming the neighbours is what makes the finding actionable rather than
    # merely true — it says where to look in the source document.
    assert "3. Term" in detail and "5. Fees" in detail


def test_wholesale_missing_numbering_is_reported_once():
    """When the numbering was not read at all, listing every absent number
    buries the rest of the page. One line saying so is the useful report."""
    clauses = [
        _clause("1. Services.", seq=0, label="1."),
        _clause("40. Governing law.", seq=1, label="40."),
    ]

    codes = _codes(clauses)

    assert codes == ["NUMBERING BROKEN"]
    assert "38 of 40" in _detail(clauses, "NUMBERING BROKEN")


def test_a_repeated_letter_label_is_not_a_duplicate():
    """Every clause in a contract has its own (a), (b), (c). Treating a
    repeated letter as a fault produced five findings and no information."""
    clauses = [
        _clause("(a) The first obligation applies.", seq=0, label="(a)"),
        _clause("(b) The second obligation applies.", seq=1, label="(b)"),
        _clause("(a) A new list begins here.", seq=2, label="(a)"),
        _clause("(b) And continues here.", seq=3, label="(b)"),
    ]

    assert "DUPLICATE NUMBER" not in _codes(clauses)


def test_a_repeated_numeric_label_close_by_is_a_duplicate():
    """A numeric label appearing twice near itself is one clause read as two,
    which silently doubles it in every citation and every count."""
    clauses = [
        _clause("4.2 Payment terms are net thirty days.", seq=0, label="4.2", page=3),
        _clause("4.2 Payment terms are net thirty days.", seq=1, label="4.2", page=3),
    ]

    assert "DUPLICATE NUMBER" in _codes(clauses)


def test_a_schedule_restarting_its_numbering_is_not_a_duplicate():
    """Schedule A begins again at 1, two hundred clauses later. That is how the
    document is written, and flagging it would fire on almost every contract."""
    clauses = [
        _clause("1. Services. The Consultant shall provide the Services.", seq=0, label="1."),
        _clause("2. Term. Three years.", seq=1, label="2."),
        _clause("1. Scope of Schedule A. The following applies.", seq=200, label="1."),
        _clause("2. Charges under Schedule A.", seq=201, label="2."),
    ]

    assert "DUPLICATE NUMBER" not in _codes(clauses)


def test_a_subclause_without_its_parent_is_flagged():
    """8.1 arriving without 8 means the parent heading was missed, so every
    sub-clause under it is parented wrongly or not at all."""
    clauses = [
        _clause("7. Confidentiality. Each party shall keep information secret.", label="7."),
        _clause("8.1 Each party shall indemnify the other.", seq=1, label="8.1", page=5),
    ]

    assert "ORPHAN" in _codes(clauses)
    assert "8.1 exists but 8" in _detail(clauses, "ORPHAN")


# --- cross-references -------------------------------------------------------


def _numbered(texts: dict[int, str]) -> list[DsClause]:
    """A document whose numbering parsed, which cross-referencing requires."""
    return [
        _clause(f"{n}. {body}", seq=index, label=f"{n}.", page=1)
        for index, (n, body) in enumerate(sorted(texts.items()))
    ]


def test_a_reference_to_a_clause_that_does_not_exist_is_flagged():
    """Either the reference is wrong, which a reviewer must see, or the target
    was lost in extraction, which is our bug. Both need a line."""
    clauses = _numbered(
        {
            1: "Services. The Consultant shall provide them.",
            2: "Term. Three years.",
            3: "Fees. Net thirty days.",
            4: "Liability is limited as set out in Section 12.",
            13: "Notices. In writing.",
        }
    )

    assert "NO TARGET" in _codes(clauses)


def test_a_statutory_reference_is_not_a_broken_cross_reference():
    """"Section 12 of the Companies Act" points outside this document. Flagging
    every statutory citation as broken would make the check worthless on any
    contract that cites a statute, which is most of them."""
    clauses = _numbered(
        {
            1: "Services. The Consultant shall provide them.",
            2: "Term. Three years.",
            3: "The parties comply with Section 12 of the Companies Act 2013.",
            13: "Notices. In writing.",
        }
    )

    assert "NO TARGET" not in _codes(clauses)


def test_a_statutory_reference_with_a_subsection_is_still_external():
    """Real citations are written "section 45(1) of the Criminal Finances Act
    2017" and "Section 2(31) of the CGST Act". Matching only a bare "of" let
    every one of those through as a broken internal reference."""
    clauses = _numbered(
        {
            1: "Services. The Consultant shall provide them.",
            2: "A UK tax evasion facilitation offence under section 45(1) of the "
            "Criminal Finances Act 2017 is a breach.",
            3: "Definition of Consideration - Section 2(31) of the CGST Act, 2017.",
            4: "Upon verification of Clause 4.3(a) of the Supplementary Agreement dated "
            "06.06.2020, the partner is admitted.",
        }
    )

    assert "NO TARGET" not in _codes(clauses)


def test_a_section_number_far_above_the_document_is_a_statute():
    """"Section 409A Compliance" in a contract with thirteen sections is the
    Internal Revenue Code, and it is written without an "of the ..." to catch."""
    clauses = _numbered(
        {
            1: "Services. The Consultant shall provide them.",
            2: "Section 409A Compliance. The parties intend that all provisions comply.",
            3: "Notices. In writing.",
        }
    )

    assert "NO TARGET" not in _codes(clauses)


def test_a_trailing_zero_still_finds_its_clause():
    """A document numbered "6.0" style cites itself as "Section 6.0". Comparing
    the parsed tuples literally reported every reference in it as broken."""
    clauses = _numbered(
        {
            1: "Services. The Consultant shall provide them.",
            2: "Charges are as per Section 6.0 of this agreement.",
            6: "Charges. The rates are set out in Schedule A.",
        }
    )

    assert "NO TARGET" not in _codes(clauses)


def test_cross_references_are_not_checked_when_the_numbering_barely_parsed():
    """The check has a precondition. With almost no numbering read, "the
    reference is broken" cannot be told apart from "we never read the clause it
    points at" — and reporting the first is a lie. One corpus document produced
    28 findings this way, every one restating a single root cause."""
    clauses = [
        _clause(f"Some unnumbered paragraph {n} referring to Section 4.", seq=n)
        for n in range(40)
    ]
    clauses[0].number_label = "1."

    assert "NO TARGET" not in _codes(clauses)


def test_one_finding_per_missing_target_not_per_citing_clause():
    """Six clauses citing the same absent clause 4 is one problem. Six findings
    for it is the noise that makes a reviewer stop reading."""
    clauses = _numbered(
        {n: "Subject to Section 4, the party shall comply." for n in (1, 2, 3, 5, 6, 7)}
    )

    assert _codes(clauses).count("NO TARGET") == 1
    assert "5 other clauses" in _detail(clauses, "NO TARGET")


# --- OCR debris -------------------------------------------------------------


def test_a_complete_short_statement_without_a_full_stop_is_not_cut_off():
    """Contracts are full of table cells, template instructions and addresses
    that end without punctuation. Matching merely "no terminator" fired 203
    times across the corpus and was right about a quarter of the time, which
    buried every real truncation."""
    clauses = [
        _clause("(b) The format of the electronic invoice is pdf", label="(b)"),
        _clause("Scope of work - Pl list all material assumptions here"),
        _clause("Travel costs - Visa and insurance expenses at actuals"),
        _clause("Contact for escalation is dibyendu.halder@intinfotech.com"),
    ]

    assert "CUT OFF" not in _codes(clauses)


def test_a_clause_ending_on_a_word_that_cannot_end_a_sentence_is_cut_off():
    """The signal that survived: a sentence cannot stop on "the" or "of"."""
    clauses = [
        _clause(
            "9.3 The Supplier is not liable for (6) causes external to the operation of the",
            label="9.3",
        )
    ]

    assert "CUT OFF" in _codes(clauses)


# --- provider markup --------------------------------------------------------


def test_html_from_the_ocr_provider_is_its_own_finding():
    """Reducto returns some blocks as HTML and the tags are reaching stored
    clauses. Quoting one back to a reviewer shows them markup; embedding one
    puts markup in the vector. It was previously surfacing as a clause that
    ended oddly, which named the wrong problem."""
    clauses = [
        _clause("1. <b><u>Project Name</u></b>. The Project shall be as described.", label="1."),
        _clause("LICENSOR: Ms. LING Huieng Alicia Attn: <empty>", seq=1),
    ]

    assert "MARKUP IN TEXT" in _codes(clauses)


def test_ocr_debris_is_flagged_as_a_fragment():
    """A one-character clause is not a clause. These are what a rubber stamp
    and a form checkbox leave behind, and each one is a row a reviewer has to
    read and dismiss."""
    clauses = [_clause("S", seq=31, page=5), _clause(".....", seq=43, page=7)]

    assert _codes(clauses).count("FRAGMENT") == 2


def test_a_repeated_fragment_is_reported_once_as_furniture():
    """A document-management stamp ("MDC\\757175_1") and a footer ("Page 4 of
    13") arrive as their own clauses on every page. Reporting each copy turned
    one furniture-stripping gap into eighteen findings."""
    clauses = [_clause("MDC\\757175_1", seq=n, page=n) for n in (4, 9, 13, 18)]

    detail = _detail(clauses, "FRAGMENT")

    assert _codes(clauses).count("FRAGMENT") == 1
    assert "4 times" in detail and "page furniture" in detail


def test_a_one_off_short_phrase_is_not_a_fragment():
    """"WITNESSETH", "SAP ABAP HR", "Representative" are real content the
    parser typed as a paragraph instead of a heading or a table cell. Flagging
    them put 234 findings on the corpus and buried the 40 that meant something.
    A short block is only evidence of damage when it repeats or is not words."""
    clauses = [
        _clause("WITNESSETH", seq=0, page=1),
        _clause("Representative", seq=1, page=3),
        _clause("SAP ABAP HR", seq=2, page=5),
    ]

    assert "FRAGMENT" not in _codes(clauses)


def _stamp(seq, page, x0, y0):
    return _clause(
        "NDTRE TED",
        seq=seq,
        page=page,
        bbox={"x0": x0, "y0": y0, "x1": x0 + 0.11, "y1": y0 + 0.08},
    )


def test_a_stamp_in_the_same_corner_of_several_pages_is_found():
    """furniture.py strips repeats by *text*: a DocuSign stamp OCRs identically
    every time and is caught, a rubber stamp OCRs differently every time and is
    not. Position repeats when the characters do not — and 8 of the 12 errors
    in the hand-scored accuracy run were a stamp read as clause text."""
    clauses = [
        _stamp(25, 4, 0.756, 0.858),
        _stamp(31, 5, 0.766, 0.855),
        _stamp(33, 6, 0.770, 0.859),
        _stamp(68, 9, 0.776, 0.850),
        _stamp(82, 11, 0.793, 0.846),
    ]

    detail = _detail(clauses, "REPEATED BY POSITION")

    assert "5 short blocks" in detail
    assert "4-6, 9, 11" in detail


def test_a_box_in_pdf_points_is_not_clustered():
    """Rows written before both parsers normalised hold points, not page
    fractions, and STAMP_TOLERANCE of 0.05 is meaningless against them — it
    grouped three unrelated blocks 456 points apart as one stamp."""
    clauses = [
        _clause(
            "Schedule 3",
            seq=n,
            page=n,
            bbox={"x0": 456.27, "y0": 40.67, "x1": 520.0, "y1": 58.0},
        )
        for n in (28, 29, 30)
    ]

    assert "REPEATED BY POSITION" not in _codes(clauses)


def test_two_pages_is_not_enough_to_call_something_a_stamp():
    """Two short blocks landing in the same place is a coincidence a two-page
    document will produce honestly. Deleting or flagging real clauses on that
    evidence is worse than missing a stamp."""
    assert "REPEATED BY POSITION" not in _codes([_stamp(1, 1, 0.76, 0.85), _stamp(2, 2, 0.76, 0.85)])


def test_a_stamp_drifting_across_the_page_is_still_one_stamp():
    """A skewed scan moves the stamp a few percent per page. Matching exact
    position splits one stamp into five clusters and reports none of them."""
    clauses = [_stamp(n, n, 0.75 + n * 0.008, 0.86 - n * 0.004) for n in range(1, 6)]

    assert "REPEATED BY POSITION" in _codes(clauses)


def test_a_stamp_is_not_also_reported_as_a_fragment():
    """Naming the same stamp twice makes the document look worse than it is,
    and pads the findings list the reader is meant to work through."""
    clauses = [_stamp(n, n, 0.76, 0.85) for n in range(1, 6)]

    assert _codes(clauses).count("FRAGMENT") == 0


# --- pages ------------------------------------------------------------------


def test_a_page_that_produced_no_clauses_is_reported():
    """A page yielding nothing is either blank or unread, and only the document
    can tell you which — but the report has to say it happened."""
    clauses = [_clause("1. Services. The Consultant shall provide them.", label="1.", page=1)]

    assert "EMPTY PAGE" in _codes(clauses, page_count=3)
    assert "2 of 3 pages" in _detail(clauses, "EMPTY PAGE", page_count=3)


def test_no_page_numbers_at_all_is_its_own_finding():
    """This was every scanned document until the OCR blocks carried geometry:
    the text is fine and no citation can be shown on the page it came from."""
    clauses = [_clause("1. Services. The Consultant shall provide them.", label="1.")]

    assert "NO PAGE NUMBERS" in _codes(clauses, page_count=11)


# --- the quiet case ---------------------------------------------------------


def test_a_clean_document_produces_no_findings():
    """The test that makes the rest mean something. A checker that always finds
    something is exactly as useless as one that never does."""
    clauses = [
        _clause("1. Services. The Consultant shall provide the Services in Schedule A.", label="1."),
        _clause("2. Term. This Agreement runs three years from the Effective Date.", seq=1, label="2."),
        _clause("3. Fees. The Foundation shall pay the Compensation in each Order.", seq=2, label="3."),
    ]

    assert findings(clauses) == []


# --- the rendered page ------------------------------------------------------


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        for model in (DsEvent, DsClause, DsVersion, DsDocument):
            for row in session.scalars(select(model).where(model.org_id == ORG)):
                session.delete(row)
        session.commit()
        session.close()


def _docx(paragraphs: list[str]) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_the_report_states_the_files_own_identity(db):
    """A report that does not say which bytes it describes cannot be trusted
    later: the same filename is re-uploaded with different content constantly,
    and the hash is the only thing that tells the two apart."""
    content = _docx(["1. TERM. Three years.", "2. FEES. Net thirty."])
    result = ingest(
        db, org_id=ORG, content=content, filename="msa.docx", mime_type=DOCX_MIME
    )

    page = report(db, result.version_id)

    import hashlib

    assert hashlib.sha256(content).hexdigest() in page
    assert f"{len(content):,} bytes" in page
    assert "docx" in page
    assert result.version_id in page


def test_the_report_carries_every_clause_in_full(db):
    """The page has to stand alone. A summary that omits the text forces a
    second lookup for every question worth asking of it, and a model reading
    it cannot judge whether two clauses contradict each other from an outline.

    The clause tree stays as well, truncated, because it is the map a reader
    orients by before reading two hundred clauses.
    """
    body = "9. INDEMNITY. " + ("The Consultant shall indemnify the Foundation. " * 20)
    result = ingest(
        db, org_id=ORG, content=_docx([body]), filename="msa.docx", mime_type=DOCX_MIME
    )

    page = report(db, result.version_id)

    assert body.strip() in page          # the clause, whole
    assert "## Clause tree" in page      # and the truncated map above it
    assert "…" in page


def test_the_report_carries_everything_stored_about_a_clause(db):
    """Text alone is not enough: the clause id is what an annotation anchors
    to, the offsets are what a citation resolves through, and the page and box
    are what let it be shown on the page it came from. A report missing any of
    them cannot be used to check the thing it describes."""
    result = ingest(
        db,
        org_id=ORG,
        content=_docx(["1. TERM. Three years.", "1.1 Renewal is automatic."]),
        filename="msa.docx",
        mime_type=DOCX_MIME,
    )
    clause = next(
        c
        for c in db.scalars(
            select(DsClause).where(DsClause.version_id == result.version_id)
        )
        if c.number_label == "1.1"
    )

    page = report(db, result.version_id)

    assert clause.clause_id in page
    assert clause.parent_clause_id in page
    assert f"chars {clause.char_start:,}-{clause.char_end:,}" in page
    assert "level 2" in page


def test_the_report_carries_the_audit_counts_the_ingest_recorded(db):
    """The event holds how the version was produced — parser identity and the
    coverage audit at the time it ran. Once the code moves on that record is
    the only account of it, so the page has to carry it."""
    result = ingest(
        db,
        org_id=ORG,
        content=_docx(["1. TERM. Three years from the Effective Date."]),
        filename="msa.docx",
        mime_type=DOCX_MIME,
    )

    page = report(db, result.version_id)

    assert "version.ingested" in page
    assert "source_chars" in page


def test_the_report_says_so_when_nothing_is_wrong(db):
    """Silence is ambiguous — it reads the same as a report that failed to run.
    Saying every check passed is the only version a reader can act on."""
    result = ingest(
        db,
        org_id=ORG,
        content=_docx(["1. TERM. Three years from the Effective Date."]),
        filename="msa.docx",
        mime_type=DOCX_MIME,
    )

    assert "None. Every check passed." in report(db, result.version_id)


def test_an_unknown_version_raises_rather_than_returning_an_empty_page(db):
    """An empty report for a version that does not exist would be read as a
    clean document."""
    with pytest.raises(LookupError):
        report(db, "no-such-version")


def test_real_section_headings_are_not_clustered_as_a_stamp():
    """A contract that opens each section near the top of a page puts its
    headings at almost the same spot on page after page. Position alone then
    reads them as a watermark: "1. DEFINITIONS", "3. PROJECT TEAM",
    "11. WORKPLACE" and "16.6 Entire Agreement" came back as one stamp on a
    real 19-page MSA, alongside three more findings of the same kind.

    A numbered block is a clause whatever else is true of it — the same rule
    reflow.py uses to refuse a join.
    """
    clauses = [
        _clause(
            text,
            seq=n,
            page=page,
            label=label,
            bbox={"x0": 0.23, "y0": 0.58, "x1": 0.34, "y1": 0.60},
        )
        for n, (page, label, text) in enumerate(
            [
                (1, "1.", "1. DEFINITIONS"),
                (4, "3.", "3. PROJECT TEAM"),
                (9, "11.", "11. WORKPLACE"),
                (15, "16.6", "16.6 Entire Agreement"),
            ]
        )
    ]

    assert "REPEATED BY POSITION" not in _codes(clauses)


def test_the_clause_tree_draws_exactly_what_is_stored():
    """A clause drawn under another must be inside it. Drawn from the parent
    links rather than from levels, the tree cannot show a structure that is not
    the one stored — and it says who placed what a reader should double-check."""
    from app.docstudio.report import _tree_section

    def clause(seq, text, parent=None, source="numbering", label=None):
        return DsClause(clause_id=f"c{seq}", seq=seq, number_label=label, text=text,
                        parent_clause_id=parent, structure_source=source, page_number=4)

    page = _tree_section(
        [
            clause(0, "4. Compensation.", label="4.", source="top"),
            clause(1, "(b) Retention Bonus.", "c0", "list", "(b)"),
            clause(2, "Any required repayment is due in thirty days.", "c1", "ai"),
            clause(3, "Signed in counterparts.", source="undecided"),
        ],
        "c.pdf",
    )

    assert (
        "c.pdf\n"
        "├── 4. Compensation.  (p4, #0)\n"
        "│   └── (b) Retention Bonus.  (p4, #1)\n"
        "│       └── Any required repayment is due in thirty days.  (p4, #2) [AI]\n"
        "└── Signed in counterparts.  (p4, #3) [?]\n"
    ) in page
