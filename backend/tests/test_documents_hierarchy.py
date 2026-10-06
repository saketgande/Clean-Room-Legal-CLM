"""The AI settles only what the rules could not, from a closed list; code checks.

Letting a model place every clause had put 5.5 inside 5.4 on a Franklin
Madison MSA. These tests guard the reasons the narrower design is safe: the
model is asked only about undecided clauses, can only choose among the options
it was given, cannot move a clause the numbering placed, and only three
columns ever change. The call goes through the AI gateway like every other
feature. DB-free; the suite never reaches the network.

Ported from tests/test_docstudio_hierarchy.py when docstudio was removed: the
logic tests are unchanged, the database tests became tests on the reader's
output, which is what this module now works on.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS
from app.core.config import settings
from app.documents.hierarchy import (
    GatewayHierarchyJudge,
    Question,
    accept,
    arrange,
    broken_runs,
    questions_for,
    rebuild,
    render_outline,
    rows_from,
)
from app.documents.reader import structure
from app.documents.reader.parsing.registry import DOCX_MIME, parser_for
from app.documents.reader.structure import BuiltClause
from tests.test_ai_gateway import (  # noqa: F401 — the autouse fixture applies here too
    FakeProvider,
    FakeSession,
    _ledger_uses_fake_sessions,
    _response,
)


def _clause(text, *, seq, label=None, parent=None, source="undecided"):
    return BuiltClause(
        clause_id=f"cl_{seq:04d}",
        seq=seq,
        parent_clause_id=None if parent is None else f"cl_{parent:04d}",
        number_label=label,
        level=1,
        clause_type="paragraph",
        text=f"{label} {text}" if label else text,
        char_start=0,
        char_end=0,
        page_number=None,
        bbox=None,
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
    nodes, placements = rebuild(PAYMENTS)

    assert questions_for(PAYMENTS, nodes, placements) == [Question(seq=4, options=(3, 1, 0, None))]


def test_a_list_item_continuing_an_undecided_one_is_not_asked_about():
    clauses = [
        _clause("MASTER AGREEMENT", seq=0),
        _clause("The parties agree as follows.", seq=1),
        _clause("first.", seq=2, label="(a)"),
        _clause("second.", seq=3, label="(b)"),
    ]
    nodes, placements = rebuild(clauses)

    assert 3 not in [q.seq for q in questions_for(clauses, nodes, placements)]


def test_the_outline_marks_what_is_undecided_and_keeps_closing_words():
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
    questions = [Question(seq=4, options=(3, 1, 0, None)), Question(seq=6, options=(5, None))]

    accepted, refused = accept([(4, 0), (6, 2), (9, 0)], questions)
    twice, _ = accept([(4, 0), (4, 1)], questions)

    assert accepted == {4: 0}
    assert [r["why"] for r in refused] == ["not one of its options", "not asked about"]
    assert twice == {}


def test_malformed_rows_are_dropped_and_stay_undecided():
    payload = {"answers": [
        {"seq": 1, "parent": None},
        {"seq": "2", "parent": 1},
        {"seq": 3, "parent": "x"},
        {"seq": 4},
        {"seq": True, "parent": None},
    ]}

    assert rows_from(payload) == [(1, None)]


# --- the independent check --------------------------------------------------


def test_a_numbering_run_split_across_folders_is_flagged():
    parents = {0: None, 1: 0, 2: 1, 3: 1, 4: 0, 5: 1}

    assert broken_runs(PAYMENTS, parents) == ((5, 1, "placed inside the item before it"),)
    assert broken_runs(PAYMENTS, {0: None, 1: 0, 2: 1, 3: 1, 4: 0, 5: 0}) == ()


def test_a_run_continuing_into_an_exhibit_is_not_a_split():
    clauses = [
        _clause("Miscellaneous.", seq=0, label="16."),
        _clause("Severability.", seq=1, label="16.16"),
        _clause("Exhibit A - Work Order", seq=2),
        _clause("Project Assumptions.", seq=3, label="16.17"),
    ]

    assert broken_runs(clauses, {0: None, 1: 0, 2: None, 3: 2}) == ()


# --- arranging the reader's output -------------------------------------------


def _read(*texts):
    document = Document()
    for text in texts or (
        "1. TERM. The term is three years.",
        "Renewal is automatic unless either party gives notice.",
        "2. FEES. Payment is due in thirty days.",
    ):
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    parsed = parser_for(DOCX_MIME).parse(buffer.getvalue(), filename="c.docx")
    return structure.build(parsed).clauses


def test_the_reader_records_who_decided_each_clause():
    clauses = _read()

    assert [c.structure_source for c in clauses] == ["top", "undecided", "top"]


def test_arranging_changes_parent_level_and_source_and_nothing_else():
    """Comments anchor to clause_id and citations resolve through the offsets;
    if either moved, arranging would orphan them."""
    clauses = _read()
    before = [(c.clause_id, c.text, c.char_start, c.char_end, c.seq) for c in clauses]

    result = arrange(clauses, _Judge({1: 0}))

    assert result.applied and result.summary.startswith("arranged — the AI settled 1 of 1")
    out = result.clauses
    assert out[1].parent_clause_id == out[0].clause_id
    assert (out[1].level, out[1].structure_source) == (2, "ai")
    assert [(c.clause_id, c.text, c.char_start, c.char_end, c.seq) for c in out] == before
    # The input is not modified.
    assert clauses[1].structure_source == "undecided"


def test_the_ai_cannot_move_a_clause_the_numbering_placed():
    clauses = _read()

    result = arrange(clauses, _Judge({1: 0, 2: 0}))

    assert result.clauses[2].parent_clause_id is None
    assert result.refused == [{"seq": 2, "parent": 0, "why": "not asked about"}]


def test_nothing_undecided_means_no_ai_call():
    judge = _Judge(raises=True)

    result = arrange(_read("1. TERM. Three years.", "1.1 Renewal is automatic.", "2. FEES."), judge)

    assert result.summary == "every clause placed by the numbering — nothing needed the AI"
    assert judge.asked is None


def test_a_failed_call_changes_nothing():
    clauses = _read()

    result = arrange(clauses, _Judge(raises=True))

    assert not result.applied
    assert result.summary.startswith("rules only — the AI call failed (RuntimeError)")
    assert result.clauses == clauses


# --- through the AI gateway ---------------------------------------------------


@pytest.fixture
def _real_ai_path(monkeypatch):
    monkeypatch.setattr(settings, "mock_claude", False)


def test_the_judge_goes_through_the_gateway_with_the_moved_prompt(_real_ai_path):
    db = FakeSession()
    provider = FakeProvider(_response(tool="clause_parents", tool_input={"answers": [{"seq": 1, "parent": 0}]}))

    result = arrange(_read(), GatewayHierarchyJudge(db, org_id="org-1", resource=("contract", "c-1"), provider=provider))

    assert result.applied and result.answered == 1
    sent = provider.calls[0][1]
    assert sent["system_prompt"] == DEFAULT_SKILL_PROMPTS["clause_hierarchy"] + "\n\n" + UNTRUSTED_INPUT_GUARD
    assert (sent["tool_name"], sent["max_tokens"], sent["temperature"]) == ("clause_parents", 4000, 0.0)
    assert sent["user_prompt"].startswith("OUTLINE\n") and "\n\nQUESTIONS\n[1] options:" in sent["user_prompt"]
    [row] = db.rows
    assert (row.prompt_key, row.status, row.resource_type, row.resource_id) == (
        "clause_hierarchy", "succeeded", "contract", "c-1",
    )


def test_a_failed_gateway_call_is_recorded_and_the_rules_stand(_real_ai_path):
    db = FakeSession()
    clauses = _read()

    result = arrange(clauses, GatewayHierarchyJudge(db, org_id="org-1", provider=FakeProvider(error=TimeoutError())))

    assert not result.applied and result.clauses == clauses
    assert db.rows[0].status == "failed" and db.rows[0].error_class == "TimeoutError"
