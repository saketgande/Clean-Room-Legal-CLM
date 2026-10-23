"""A decision the person already made is not asked for again.

Each playbook review is a fresh AI call that knows nothing of earlier decisions. After text changed
(an accepted fix elsewhere in the contract) the new review raised the same problems again, worded
differently, on clauses nobody had touched, and the person had to decide them a second time.

A finding that lands on the SAME, UNCHANGED clause (same clause content hash, same rule or clause
type) as one the person already decided inherits that decision. The decision is recorded as carried,
with the original decider and reason, and keeps counting toward the risk band exactly as before.
Nothing is carried when the clause changed, the finding is about another rule, the decision was
"fixed" or "accepted_fallback", or a high risk would be kept without the reason it needs.

Runs against Postgres inside one rolled-back transaction; the AI answer is a fake.
"""

from __future__ import annotations

from sqlalchemy import select

import app.models  # noqa: F401  (register every mapper)
from app.contract_files.models import ContractEdit
from app.contracts.risk import contract_risk
from app.core.audit import ResourceTimelineEvent  # noqa: F401  (registers the timeline mapper)
from app.playbooks.models import PlaybookDecision, PlaybookDeviation
from tests import test_nda_fast_lane
from tests.test_current_findings import TEXT_1, TEXT_2, World

db = test_nda_fast_lane.db  # the same rolled-back-transaction fixture

LIABILITY = "Liability of each party is unlimited"
LAW = "laws of Mars"
REASON = "Business accepts this exposure for this customer."


def _first_review(w: World, **extra):
    w.text(TEXT_1, 1)
    return w.review(
        w.dev(2, "liability", "high", "Liability is unlimited", LIABILITY),
        w.dev(3, "governing_law", "low", "Governing law is Mars", LAW),
        **extra,
    )


def _rows(db, w, status=None):
    q = select(PlaybookDeviation).where(PlaybookDeviation.contract_id == w.contract.id)
    rows = list(db.scalars(q.order_by(PlaybookDeviation.created_at.asc(), PlaybookDeviation.id.asc())))
    return [r for r in rows if status is None or r.status == status]


def _decide(w, deviation, decision, reason=REASON):
    from app.playbooks.service import PlaybooksService

    PlaybooksService(w.db).record_deviation_decision(
        deviation=deviation, decision=decision, rationale=reason, user=w.user
    )


def test_a_decision_follows_the_same_unchanged_clause_into_the_next_review(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[0], "accepted")
    _decide(w, art.deviations[1], "waived")
    w.text(TEXT_2, 2)  # only the term clause changed

    again = w.review(
        w.dev(2, "liability", "high", "Unlimited liability exposure", LIABILITY),
        w.dev(3, "governing_law", "low", "Governing law is not a real jurisdiction", LAW),
    )

    assert [d.status for d in again.deviations] == ["accepted", "waived"]
    assert w.listed() == []  # nothing left to decide
    risk = contract_risk(db, w.contract)
    assert risk["band"] == "high"  # a concession still counts
    assert sorted((d["status"], d["conceded"]) for d in risk["drivers"]) == [("accepted", True), ("waived", True)]


def test_the_carried_decision_keeps_who_decided_and_why(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[0], "accepted")
    w.text(TEXT_2, 2)

    again = w.review(w.dev(2, "liability", "high", "Unlimited liability exposure", LIABILITY))

    decision = db.scalar(select(PlaybookDecision).where(PlaybookDecision.playbook_deviation_id == again.deviations[0].id))
    assert decision.decision == "accepted"
    assert decision.decided_by_user_id == w.user.id
    assert decision.rationale.startswith("Carried over from your earlier decision on this same clause")
    assert "Liability is unlimited" in decision.rationale and REASON in decision.rationale


def test_a_changed_clause_is_asked_about_again(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "waived")
    w.text(TEXT_1.replace("laws of Mars", "laws of Venus"), 2)

    again = w.review(w.dev(3, "governing_law", "low", "Governing law is Venus", "laws of Venus"))

    assert again.deviations[0].status == "open"
    assert [d["issue"] for d in w.listed()] == ["Governing law is Venus"]


def test_another_rule_on_the_same_clause_is_not_carried(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "waived")
    w.text(TEXT_2, 2)

    again = w.review(w.dev(1, "term", "low", "A different rule flags the governing law clause", LAW))

    assert again.deviations[0].status == "open"


def test_a_fix_is_not_carried(db):
    """"fixed" says our wording went in; the clause the next review sees is no longer the one decided."""
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "fixed")
    w.text(TEXT_2, 2)

    again = w.review(w.dev(3, "governing_law", "low", "Governing law is Mars", LAW))

    assert again.deviations[0].status == "open"


def test_a_fallback_acceptance_is_not_carried(db):
    """accepted_fallback is the one decision that takes a finding out of the band, so it must be earned again."""
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "accepted_fallback")
    w.text(TEXT_2, 2)

    again = w.review(w.dev(3, "governing_law", "low", "Governing law is Mars", LAW))

    assert again.deviations[0].status == "open"


def test_a_high_risk_is_not_kept_without_the_reason_it_needs(db):
    """The earlier decision was on a low finding with no reason; the new review rates the same clause high."""
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "waived", reason=None)
    w.text(TEXT_2, 2)

    again = w.review(w.dev(3, "governing_law", "high", "Governing law makes the contract unenforceable", LAW))

    assert again.deviations[0].status == "open"


def test_a_repeated_clause_cannot_be_told_apart_so_nothing_is_carried(db):
    twice = TEXT_1 + "4. Governing law. This agreement is governed by the laws of Mars.\n"
    w = World(db)
    w.text(twice, 1)
    art = w.review(w.dev(3, "governing_law", "low", "Governing law is Mars", LAW))  # quote is in two clauses
    _decide(w, art.deviations[0], "waived")
    w.text(twice.replace("five (5)", "two (2)"), 2)

    again = w.review(w.dev(3, "governing_law", "low", "Governing law is Mars", LAW))

    assert again.deviations[0].status == "open"


def test_a_finding_with_an_unchecked_quote_is_not_carried(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "waived")
    w.text(TEXT_2, 2)
    odd = w.dev(3, "governing_law", "low", "Governing law is Mars", LAW)
    odd.citations[0].quote = "wording that is not in the contract at all"

    again = w.review(odd)

    assert again.deviations[0].status == "needs_review"


def test_a_re_run_on_the_same_text_does_not_ask_again_either(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[0], "accepted")

    again = w.review(w.dev(2, "liability", "high", "Unlimited liability exposure", LIABILITY))

    assert again.deviations[0].status == "accepted"
    assert w.listed() == [d for d in w.listed() if d["status"] in ("open", "needs_review")]
    assert [d["issue"] for d in w.listed()] == ["Governing law is Mars"]


def test_the_newest_decision_wins(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "waived")
    second = w.review(w.dev(3, "governing_law", "low", "Governing law is Mars again", LAW))
    # the second review inherited "waived"; the person then rejects it on purpose
    _decide(w, second.deviations[0], "rejected")
    w.text(TEXT_2, 2)

    third = w.review(w.dev(3, "governing_law", "low", "Governing law is Mars, still", LAW))

    assert third.deviations[0].status == "rejected"


def test_a_carried_finding_gets_no_redline_but_a_new_one_does(db):
    samples = {"governing_law": "This agreement is governed by the laws of England.", "term": "Two years."}
    w = World(db, samples=samples)
    art = _first_review(w)
    _decide(w, art.deviations[1], "waived")
    w.text(TEXT_2, 2)

    again = w.review(
        w.dev(3, "governing_law", "low", "Governing law is Mars", LAW),
        w.dev(1, "term", "medium", "Term is not the playbook's", "two (2) years"),
        redline=True,
    )

    assert [d.status for d in again.deviations] == ["waived", "open"]
    edits = list(db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.contract.id)))
    assert [e.replacement_text for e in edits] == ["Two years."]


def test_the_run_says_how_many_decisions_it_carried(db):
    w = World(db)
    art = _first_review(w)
    _decide(w, art.deviations[1], "waived")
    w.text(TEXT_2, 2)

    again = w.review(w.dev(3, "governing_law", "low", "Governing law is Mars", LAW))

    assert again.run.validated_output["carried_decisions"] == 1


def test_a_quote_that_did_not_check_out_does_not_identify_a_clause():
    """The clause a finding is about is only known when its quote was verified against the text."""
    from types import SimpleNamespace

    from app.contract_files.blocks import split_blocks
    from app.playbooks.carry_over import _clause_id

    blocks = split_blocks(TEXT_1)

    def finding(status):
        return SimpleNamespace(citation={"type": "ai_citation", "quote": LAW, "validation_status": status})

    assert _clause_id(blocks, finding("valid")) is not None
    assert _clause_id(blocks, finding("invalid")) is None
    assert _clause_id(blocks, SimpleNamespace(citation={"type": "missing"})) is None


def test_the_re_check_after_accepting_changes_does_not_ask_or_redline_again(db, monkeypatch):
    """The reported story, end to end through the real automatic re-check: the person decided three
    findings, accepted the fix text, and the re-check (which is a fresh AI answer that raises the same
    problems again, worded differently) must neither re-open them nor propose new changes."""
    import asyncio

    from app.ai.controller import ai_controller
    from app.ai.schemas import PlaybookReviewOutput
    from app.playbooks.service import PlaybooksService

    samples = {"governing_law": "This agreement is governed by the laws of England.", "term": "Two years."}
    w = World(db, samples=samples)
    art = _first_review(w, redline=True)  # the first review of a fresh draft redlines, as it should
    _decide(w, art.deviations[0], "accepted")
    _decide(w, art.deviations[1], "waived")
    w.text(TEXT_2, 2)  # the accepted fix landed in a new version
    edits_before = len(list(db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.contract.id))))

    again = PlaybookReviewOutput(deviations=[
        w.dev(2, "liability", "high", "Unlimited liability exposure", LIABILITY),
        w.dev(3, "governing_law", "low", "Governing law is not a real jurisdiction", LAW),
    ])

    async def fake_ai(*_a, **_k):
        return again.model_dump(mode="json")

    monkeypatch.setattr(ai_controller, "run_structured_skill", fake_ai)

    result = asyncio.run(
        PlaybooksService(db).auto_review_contract(contract=w.contract, actor_user_id=w.user.id, create_redline=False)
    )

    assert edits_before > 0  # the first review did propose changes, so the comparison below means something
    assert result["ran"] is True and result["error"] is None
    assert w.listed() == []  # nothing left to decide
    edits_after = len(list(db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.contract.id))))
    assert edits_after == edits_before  # and nothing new proposed
    risk = contract_risk(db, w.contract)
    assert risk["band"] == "high"  # the accepted high risk still counts, as it did before
    assert {d["conceded"] for d in risk["drivers"]} == {True}
