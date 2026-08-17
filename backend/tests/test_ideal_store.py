"""Event-sourced persistence — the events ARE the run.

The single property everything rests on: **replaying the events must give back
exactly the state that produced them.** If that ever drifts, a restarted server
resumes a negotiation somewhere other than where it left off — worse than
losing it outright.
"""

import pytest

from app.ideal.library import CR, MSA, NDA
from app.ideal.roster import Member, Roster, auto_assigner
from app.ideal.store import InMemoryEventStore, dump, inbox, replay
from app.ideal.workflow import (
    APPROVE,
    DONE,
    NEED_INFO,
    PAUSED,
    REJECT,
    REJECTED,
    REQUEST_CHANGES,
    RunState,
    WorkflowError,
    advance,
    assign,
    record_change,
    start,
)


def _roster():
    return Roster(members=(
        Member("Ops bot", "Legal Ops"), Member("A. Sharma", "Legal & IP"),
        Member("M. Rao", "Finance & Tax"), Member("R. Iyer", "Quality & Compliance"),
    ))


def _negotiation():
    """A realistic run: a send-back, work recorded inside a step, a second round."""
    a = auto_assigner(_roster())
    s = start(NDA, assigner=a)
    s = advance(s, NDA, step_id="prepare", outcome=APPROVE, actor="Ops bot", assigner=a)
    s = advance(s, NDA, step_id="ai_review", outcome=APPROVE, actor="agent",
                confidence=0.88, assigner=a)
    s = record_change(s, NDA, step_id="legal_review", what="struck clause 9", by="A. Sharma")
    s = advance(s, NDA, step_id="legal_review", outcome=REQUEST_CHANGES,
                actor="A. Sharma", comment="cap the liability", assigner=a)
    s = advance(s, NDA, step_id="prepare", outcome=APPROVE, actor="Ops bot", assigner=a)
    s = advance(s, NDA, step_id="ai_review", outcome=APPROVE, actor="agent", assigner=a)
    return s


def _same(a: RunState, b: RunState) -> bool:
    return (
        a.workflow_key == b.workflow_key
        and a.active == b.active
        and a.status == b.status
        and a.rounds == b.rounds
        and a.assignees == b.assignees
        and a.context == b.context
        and [e.seq for e in a.events] == [e.seq for e in b.events]
    )


# ==========================================================================
# The property everything rests on
# ==========================================================================

def test_a_replayed_run_is_identical_to_the_original():
    original = _negotiation()
    rebuilt = replay(dump("run-1", original), NDA)
    assert _same(original, rebuilt), (
        f"\noriginal active={original.active} status={original.status} "
        f"assignees={original.assignees}"
        f"\nrebuilt  active={rebuilt.active} status={rebuilt.status} "
        f"assignees={rebuilt.assignees}"
    )


def test_it_survives_a_restart():
    """Save, throw the object away, load it back, carry on."""
    store = InMemoryEventStore()
    store.append("run-1", _negotiation())

    resumed = store.load("run-1", NDA)          # the original object is gone
    assert resumed.active == ("legal_review",)
    assert resumed.rounds["legal_review"] == 2  # still round 2 of the negotiation

    resumed = advance(resumed, NDA, step_id="legal_review", outcome=APPROVE, actor="A. Sharma")
    assert resumed.active == ("finance", "quality")


def test_only_new_events_are_written():
    """Appending twice must not duplicate — the log is append-only, not
    append-everything-again."""
    store = InMemoryEventStore()
    s = _negotiation()
    assert store.append("run-1", s) > 0
    assert store.append("run-1", s) == 0

    s = advance(s, NDA, step_id="legal_review", outcome=APPROVE, actor="A. Sharma")
    assert store.append("run-1", s) > 0


# ==========================================================================
# What event sourcing buys that a status column cannot
# ==========================================================================

def test_any_earlier_moment_can_be_reconstructed():
    """'What did this look like when legal first saw it?' — a real question for
    a regulated business, with a real answer."""
    s = _negotiation()
    rows = dump("run-1", s)

    first_legal = next(e.seq for e in s.events
                       if e.kind == "entered" and e.step_id == "legal_review")
    back_then = replay(rows, NDA, upto=first_legal)

    assert back_then.active == ("legal_review",)
    assert back_then.rounds["legal_review"] == 1     # round 1, before the send-back
    assert s.rounds["legal_review"] == 2             # ...and 2 now


def test_history_cannot_be_rewritten():
    """The store has no update and no delete. That is the point."""
    store = InMemoryEventStore()
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")


def test_the_log_must_start_at_the_beginning():
    """A truncated log is refused rather than rebuilt into a plausible lie."""
    rows = dump("run-1", _negotiation())
    with pytest.raises(WorkflowError, match="incomplete"):
        replay(rows[3:], NDA)


def test_an_empty_log_is_refused():
    with pytest.raises(WorkflowError, match="no events"):
        replay([], NDA)


# ==========================================================================
# Every terminal state survives the round trip
# ==========================================================================

def test_a_finished_run_replays_as_finished():
    s = start(NDA)
    for step, out in [("prepare", APPROVE), ("ai_review", APPROVE), ("legal_review", APPROVE),
                      ("finance", APPROVE), ("quality", APPROVE),
                      ("counterparty", APPROVE), ("sign", APPROVE)]:
        s = advance(s, NDA, step_id=step, outcome=out)
    assert s.status == DONE
    assert replay(dump("r", s), NDA).status == DONE


def test_a_rejected_run_replays_as_rejected():
    s = start(NDA)
    s = advance(s, NDA, step_id="prepare", outcome=APPROVE)
    s = advance(s, NDA, step_id="ai_review", outcome=APPROVE)
    s = advance(s, NDA, step_id="legal_review", outcome=REJECT, comment="sanctioned party")
    assert replay(dump("r", s), NDA).status == REJECTED


def test_a_paused_run_replays_as_paused_with_the_step_still_open():
    s = start(NDA)
    s = advance(s, NDA, step_id="prepare", outcome=APPROVE)
    s = advance(s, NDA, step_id="ai_review", outcome=APPROVE)
    s = advance(s, NDA, step_id="legal_review", outcome=NEED_INFO, comment="which entity?")
    rebuilt = replay(dump("r", s), NDA)
    assert rebuilt.status == PAUSED
    assert "legal_review" in rebuilt.active


# ==========================================================================
# The conditional MSA survives it too
# ==========================================================================

def test_the_matters_facts_survive_the_round_trip():
    """The context decides which reviewers exist. Losing it on reload would
    quietly change who has to approve the contract."""
    ctx = {"gxp": True, "personal_data": False, "systems_access": False,
           "cross_border": True, "value_inr": 8 * CR}
    s = start(MSA, context=ctx)
    s = advance(s, MSA, step_id="prepare", outcome=APPROVE)
    s = advance(s, MSA, step_id="ai_review", outcome=APPROVE)
    s = advance(s, MSA, step_id="legal_review", outcome=APPROVE, actor="A. Sharma")

    rebuilt = replay(dump("r", s), MSA)
    assert rebuilt.context == ctx
    assert set(rebuilt.active) == {"finance", "tax", "procurement", "quality"}


def test_what_was_not_required_is_still_in_the_replayed_log():
    ctx = {"gxp": False, "personal_data": False, "systems_access": False,
           "cross_border": False, "value_inr": 40_00_000}
    s = start(MSA, context=ctx)
    s = advance(s, MSA, step_id="prepare", outcome=APPROVE)
    s = advance(s, MSA, step_id="ai_review", outcome=APPROVE)
    s = advance(s, MSA, step_id="legal_review", outcome=APPROVE, actor="A. Sharma")

    rebuilt = replay(dump("r", s), MSA)
    skipped = {e.step_id for e in rebuilt.events if e.kind == "not_applicable"}
    assert skipped == {"tax", "quality", "privacy", "security"}


# ==========================================================================
# A read model built from the same events
# ==========================================================================

def test_an_inbox_can_be_built_without_touching_the_engine():
    """'What is on my desk' is a projection of the log, safe to drop and rebuild."""
    store = InMemoryEventStore()
    store.append("run-1", _negotiation())
    mine = inbox(store, NDA, "A. Sharma")
    assert len(mine) == 1
    assert mine[0]["step"] == "Legal review"
    assert mine[0]["round"] == 2


def test_a_finished_run_leaves_nobodys_inbox_full():
    store = InMemoryEventStore()
    s = start(NDA)
    for step in ("prepare", "ai_review", "legal_review", "finance", "quality",
                 "counterparty", "sign"):
        s = advance(s, NDA, step_id=step, outcome=APPROVE, actor="A. Sharma")
        s = assign(s, NDA, step_id=step, to="A. Sharma", by="ops") if step in s.active else s
    store.append("run-1", s)
    assert inbox(store, NDA, "A. Sharma") == []
