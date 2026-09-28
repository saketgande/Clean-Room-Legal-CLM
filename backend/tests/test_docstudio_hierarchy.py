"""The AI settles only what the rules could not, from a closed list; code checks.

Letting a model place every clause had put 5.5 inside 5.4 on a Franklin
Madison MSA. These tests guard the reasons the narrower design is safe: the
model is asked only about undecided clauses, can only choose among the options
it was given, cannot move a clause the numbering placed, only three columns
ever change, and nothing is asked twice about the same bytes. The suite never
reaches the network.
"""

from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document
from sqlalchemy import select

import app.models  # noqa: F401  (register every mapper)
from app.core.database import SessionLocal
from app.docstudio.hierarchy import (
    ARRANGED,
    NOT_ARRANGED,
    Question,
    _rows_from,
    accept,
    arrange_version,
    arranged,
    broken_runs,
    questions_for,
    rebuild,
    render_outline,
)
from app.docstudio.models import DsClause, DsDocument, DsEvent, DsVersion
from app.docstudio.parsing.registry import DOCX_MIME
from app.docstudio.report import report
from app.docstudio.service import ingest

ORG = "docstudio-hierarchy-test-org"


def _clause(text, *, seq, label=None, parent=None, source="undecided"):
    return DsClause(
        org_id=ORG,
        version_id="v",
        clause_id=f"cl_{seq:04d}",
        seq=seq,
        number_label=label,
        parent_clause_id=None if parent is None else f"cl_{parent:04d}",
        clause_type="paragraph",
        level=1,
        text=f"{label} {text}" if label else text,
        char_start=0,
        char_end=0,
        structure_source=source,
    )


class _Judge:
    name = "fake"

    def __init__(self, reply=None, *, raises=False):
        self.reply = reply or {}
        self.raises = raises
        self.asked: list[Question] | None = None

    def answer(self, outline, questions):
        self.asked = questions
        if self.raises:
            raise RuntimeError("the model is down")
        return list(self.reply.items())


PAYMENTS = [
    _clause("Termination Payments.", seq=0, label="7."),
    _clause("Earned Salary. Executive is entitled to the following:", seq=1, label="(a)"),
    _clause("any Base Salary earned but unpaid;", seq=2, label="(i)"),
    _clause("unreimbursed business expenses.", seq=3, label="(ii)"),
    _clause("No other compensation will be due except as provided in 7(b).", seq=4),
    _clause("Severance. The Company shall continue Base Salary.", seq=5, label="(b)"),
]


# --- what is asked ----------------------------------------------------------


def test_only_the_undecided_clause_is_asked_about_with_every_open_clause_offered():
    """"No other compensation…" closes 7(a)'s list and belongs to 7 — nothing
    on the page says so. It is the only question, and 7 is among its options."""
    nodes, placements = rebuild(PAYMENTS)

    questions = questions_for(PAYMENTS, nodes, placements)

    assert questions == [Question(seq=4, options=(3, 1, 0, None))]


def test_a_list_item_continuing_an_undecided_one_is_not_asked_about():
    """(b) goes wherever (a) goes. Asking about both could only split them."""
    clauses = [
        _clause("MASTER AGREEMENT", seq=0),
        _clause("The parties agree as follows.", seq=1),
        _clause("first.", seq=2, label="(a)"),
        _clause("second.", seq=3, label="(b)"),
    ]
    nodes, placements = rebuild(clauses)

    asked = [q.seq for q in questions_for(clauses, nodes, placements)]

    assert 3 not in asked


def test_the_outline_marks_what_is_undecided_and_keeps_closing_words():
    """"…the following:" sits at the end of a clause and is how the model knows
    the next items belong inside it; cutting to the opening loses it."""
    clauses = [
        _clause("Retention Bonus. " + "The Company shall pay a bonus. " * 10
                + "If Executive resigns for any reason, then:", seq=0, label="(b)"),
        _clause("Any required repayment is due in thirty days.", seq=1),
    ]
    _, placements = rebuild(clauses)

    lines = render_outline(clauses, placements).splitlines()

    assert lines[0].strip().startswith("[0] (b) Retention Bonus.")
    assert lines[0].endswith("for any reason, then:")
    assert lines[1].startswith("? [1]")


# --- what is accepted -------------------------------------------------------


def test_an_answer_outside_the_options_is_refused_not_trusted():
    """The options are every clause still open at that point, so any other
    answer is a misreading — and a clause answered twice has no answer."""
    questions = [Question(seq=4, options=(3, 1, 0, None)), Question(seq=6, options=(5, None))]

    accepted, refused = accept([(4, 0), (6, 2), (9, 0)], questions)
    twice, _ = accept([(4, 0), (4, 1)], questions)

    assert accepted == {4: 0}
    assert [r["why"] for r in refused] == ["not one of its options", "not asked about"]
    assert twice == {}


def test_malformed_rows_are_dropped_and_stay_undecided():
    response = SimpleNamespace(tool_use_blocks=[{"input": {"answers": [
        {"seq": 1, "parent": None},
        {"seq": "2", "parent": 1},
        {"seq": 3, "parent": "x"},
        {"seq": 4},
    ]}}])

    assert _rows_from(response) == [(1, None)]


# --- the independent check --------------------------------------------------


def test_a_numbering_run_split_across_folders_is_flagged():
    """(a) and (b) follow each other, so they share a folder in every style."""
    parents = {0: None, 1: 0, 2: 1, 3: 1, 4: 0, 5: 1}

    assert broken_runs(PAYMENTS, parents) == ((5, 1, "placed inside the item before it"),)
    assert broken_runs(PAYMENTS, {0: None, 1: 0, 2: 1, 3: 1, 4: 0, 5: 0}) == ()


def test_a_run_continuing_into_an_exhibit_is_not_a_split():
    """A work order numbering its own clauses 16.17 onwards is not a
    continuation of the main agreement's 16.16."""
    clauses = [
        _clause("Miscellaneous.", seq=0, label="16."),
        _clause("Severability.", seq=1, label="16.16"),
        _clause("Exhibit A - Work Order", seq=2),
        _clause("Project Assumptions.", seq=3, label="16.17"),
    ]

    assert broken_runs(clauses, {0: None, 1: 0, 2: None, 3: 2}) == ()


# --- applying, once ---------------------------------------------------------


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


def _version(db, *texts):
    document = Document()
    for text in texts or (
        "1. TERM. The term is three years.",
        "Renewal is automatic unless either party gives notice.",
        "2. FEES. Payment is due in thirty days.",
    ):
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    result = ingest(db, org_id=ORG, content=buffer.getvalue(), filename="c.docx", mime_type=DOCX_MIME)
    clauses = list(
        db.scalars(select(DsClause).where(DsClause.version_id == result.version_id).order_by(DsClause.seq))
    )
    return result.version_id, clauses


def test_ingest_stores_who_decided_each_clause(db):
    _, clauses = _version(db)

    assert [c.structure_source for c in clauses] == ["top", "undecided", "top"]
    assert clauses[0].number_scheme == "decimal" and clauses[0].number_path == [1]


def test_applying_changes_parent_level_and_source_and_nothing_else(db):
    """Comments anchor to clause_id and citations resolve through the offsets;
    if either moved, arranging would orphan annotations."""
    version_id, clauses = _version(db)
    before = [(c.clause_id, c.text, c.char_start, c.char_end, c.seq) for c in clauses]

    status = arrange_version(db, version_id, judge=_Judge({1: 0}))

    assert status.startswith("arranged — the AI settled 1 of 1")
    assert clauses[1].parent_clause_id == clauses[0].clause_id
    assert (clauses[1].level, clauses[1].structure_source) == (2, "ai")
    assert [(c.clause_id, c.text, c.char_start, c.char_end, c.seq) for c in clauses] == before


def test_the_ai_cannot_move_a_clause_the_numbering_placed(db):
    """Clause 2. is placed by its number; an answer about it was never asked
    for, so it is refused and recorded — not applied."""
    version_id, clauses = _version(db)

    arrange_version(db, version_id, judge=_Judge({1: 0, 2: 0}))

    assert clauses[2].parent_clause_id is None
    event = db.scalars(select(DsEvent).where(DsEvent.version_id == version_id, DsEvent.event_type == ARRANGED)).one()
    assert event.details["refused"] == [{"seq": 2, "parent": 0, "why": "not asked about"}]


def test_applying_records_what_it_changed_from(db):
    """A model can be wrong, and a change nobody can see or undo is not one
    worth making. The event holds every before-and-after."""
    version_id, clauses = _version(db)

    arrange_version(db, version_id, judge=_Judge({1: 0}))

    event = db.scalars(select(DsEvent).where(DsEvent.version_id == version_id, DsEvent.event_type == ARRANGED)).one()
    change = event.details["changes"][0]
    assert change["from"] == {"parent": None, "level": 1, "source": "undecided"}
    assert change["to"] == {"parent": clauses[0].clause_id, "level": 2, "source": "ai"}
    assert event.details["asked"] == 1 and event.details["prompt_version"] == "hierarchy/2"


def test_a_version_is_arranged_once(db):
    """The tree is stored, and a model is not a parser: asking again about the
    same bytes costs money to risk a different answer."""
    version_id, _ = _version(db)
    arrange_version(db, version_id, judge=_Judge({1: 0}))
    second = _Judge(raises=True)

    status = arrange_version(db, version_id, judge=second)

    assert "already" in status
    assert second.asked is None
    assert arranged(db, version_id)


def test_nothing_undecided_means_no_ai_call(db):
    version_id, _ = _version(db, "1. TERM. Three years.", "1.1 Renewal is automatic.", "2. FEES.")
    judge = _Judge(raises=True)

    status = arrange_version(db, version_id, judge=judge)

    assert status == "every clause placed by the numbering — nothing needed the AI"
    assert judge.asked is None
    assert arranged(db, version_id)


def test_a_failed_call_is_recorded_and_changes_nothing(db):
    """"Why is this document not arranged?" deserves an answer that is not a
    guess, so a failed attempt leaves an event and no changed clause."""
    version_id, clauses = _version(db)
    before = [(c.parent_clause_id, c.level, c.structure_source) for c in clauses]

    status = arrange_version(db, version_id, judge=_Judge(raises=True))

    assert status.startswith("rules only — the AI call failed")
    assert [(c.parent_clause_id, c.level, c.structure_source) for c in clauses] == before
    assert db.scalars(select(DsEvent).where(DsEvent.event_type == NOT_ARRANGED, DsEvent.version_id == version_id)).one()
    assert not arranged(db, version_id)


def test_a_mocked_client_is_not_asked(db):
    """The suite runs with MOCK_CLAUDE on. A canned answer would be refused
    anyway; saying so up front is clearer than a failure in the event log."""
    version_id, _ = _version(db)

    assert arrange_version(db, version_id) == (
        "rules only — the Claude client is mocked; 1 clause left undecided"
    )


def test_the_report_puts_likely_mistakes_first_then_the_ais_placements(db):
    version_id, _ = _version(
        db,
        "7. Termination Payments.",
        "(a) Earned Salary. Executive is entitled to the following:",
        "(i) any Base Salary earned but unpaid;",
        "No other compensation will be due except as provided in 7(b).",
        "(b) Severance. The Company shall continue Base Salary.",
    )
    arrange_version(db, version_id, judge=_Judge({3: 0}))
    # (b) moved inside (a) by hand: the kind of mistake the check exists for.
    clauses = list(db.scalars(select(DsClause).where(DsClause.version_id == version_id).order_by(DsClause.seq)))
    clauses[4].parent_clause_id = clauses[1].clause_id
    db.flush()

    page = report(db, version_id)

    assert "was asked about 1 clause the rules could not place" in page
    assert "Check first — 1 likely mistake." in page
    assert "Placed by the AI — 1" in page
    assert page.index("Check first") < page.index("Placed by the AI")


def test_the_report_says_what_is_still_undecided(db):
    version_id, _ = _version(db)

    page = report(db, version_id)

    assert "Still undecided — 1" in page
    assert "has not been asked" in page
