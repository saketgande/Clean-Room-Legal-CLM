"""The ideal engine — proving it expresses what the live one cannot.

The live engine (app/flows) walks a list forwards: its index is incremented in
nine places and decremented in none, and a rejection sets ``run.error`` and
stops. So rework, negotiation rounds and escalation are unrepresentable.

Each test below is one thing the old engine could not do.

No database, no fixtures, no framework — the engine is a pure state machine, so
the tests are just calls and assertions.
"""

import pytest

from app.ideal.library import NDA
from app.ideal.workflow import (
    APPROVE,
    APPROVE_WITH_COMMENTS,
    DONE,
    ESCALATE,
    MAX_ROUNDS,
    NEED_INFO,
    PARKED,
    PAUSED,
    REJECT,
    REJECTED,
    REQUEST_CHANGES,
    RUNNING,
    Step,
    WorkflowError,
    advance,
    assign,
    build,
    deviate,
    evidence,
    record_change,
    rounds_of,
    start,
    step_report,
    waiting_on,
)


def _to(state, step_id, outcome, **kw):
    return advance(state, NDA, step_id=step_id, outcome=outcome, **kw)


def _reach_legal_review():
    s = start(NDA)
    s = _to(s, "prepare", APPROVE)
    return _to(s, "ai_review", APPROVE)


# ==========================================================================
# The headline: work can go backwards
# ==========================================================================

def test_a_reviewer_can_send_work_back():
    """THE missing capability. 'Change clause 7' returns to drafting instead of
    killing the run."""
    s = _reach_legal_review()
    assert s.active == ("legal_review",)

    s = _to(s, "legal_review", REQUEST_CHANGES, actor="A. Sharma",
            comment="Clause 7 — cap liability at 12 months' fees.")

    assert s.active == ("prepare",)          # went BACKWARDS
    assert s.status == RUNNING             # and is still alive


def test_the_send_back_carries_its_comments():
    s = _reach_legal_review()
    s = _to(s, "legal_review", REQUEST_CHANGES, actor="A. Sharma", comment="Fix clause 7")
    decided = [e for e in s.events if e.kind == "decided"][-1]
    assert decided.comment == "Fix clause 7"
    assert decided.actor == "A. Sharma"


def test_rounds_are_counted():
    """Round 1, round 2, round 3 — the negotiation is visible, not inferred."""
    s = _reach_legal_review()
    assert rounds_of(s, "legal_review") == 1

    s = _to(s, "legal_review", REQUEST_CHANGES, comment="round 1")
    s = _to(s, "prepare", APPROVE)
    s = _to(s, "ai_review", APPROVE)
    assert rounds_of(s, "legal_review") == 2
    assert rounds_of(s, "prepare") == 2


def test_a_send_back_loop_parks_instead_of_spinning():
    """A negotiation that will not converge must reach a human, not loop."""
    s = _reach_legal_review()
    for _ in range((MAX_ROUNDS + 2) * 3):   # a full send-back cycle is 3 moves
        if s.status != RUNNING:
            break
        if "legal_review" in s.active:
            s = _to(s, "legal_review", REQUEST_CHANGES)
        elif "prepare" in s.active:
            s = _to(s, "prepare", APPROVE)
        elif "ai_review" in s.active:
            s = _to(s, "ai_review", APPROVE)
    assert s.status == PARKED
    parked = [e for e in s.events if e.kind == "parked"]
    assert parked and "ceiling" in parked[-1].reason


# ==========================================================================
# The other outcomes
# ==========================================================================

def test_need_info_pauses_without_killing_the_run():
    s = _reach_legal_review()
    s = _to(s, "legal_review", NEED_INFO, comment="Which entity is contracting?")
    assert s.status == PAUSED
    assert "legal_review" in s.active   # the question stays on their desk


def test_escalate_routes_to_a_senior_step():
    s = _reach_legal_review()
    s = _to(s, "legal_review", ESCALATE, actor="A. Sharma")
    assert s.active == ("gc_review",)


def test_reject_ends_the_run_deliberately():
    """Still possible — but now it is one outcome among six, not the only way
    out of a disagreement."""
    s = _reach_legal_review()
    s = _to(s, "legal_review", REJECT, comment="Counterparty is sanctioned.")
    assert s.status == REJECTED
    assert s.active == ()


def test_approve_with_comments_moves_on_and_keeps_the_note():
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE_WITH_COMMENTS, comment="Fine, but watch the term.")
    assert s.status == RUNNING
    assert [e for e in s.events if e.kind == "decided"][-1].comment


# ==========================================================================
# Parallel review
# ==========================================================================

def test_finance_and_quality_review_at_the_same_time():
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    assert set(s.active) == {"finance", "quality"}


def test_the_group_waits_for_all_of_them():
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    s = _to(s, "finance", APPROVE)
    assert s.active == ("quality",)      # still waiting on Quality
    s = _to(s, "quality", APPROVE)
    assert s.active == ("counterparty",)


def test_the_most_severe_answer_governs_a_parallel_group():
    """Quality wants changes, Finance approves. The work must go BACK.

    Letting whoever answers last decide would silently discard a reviewer's
    objection — a control that quietly stops working.
    """
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    s = _to(s, "quality", REQUEST_CHANGES, comment="GxP impact.")
    s = _to(s, "finance", APPROVE)          # answers last, approves
    assert s.active == ("prepare",)                  # ...and is overruled


def test_order_of_answers_does_not_change_the_group_result():
    """The same two answers in the opposite order must give the same result."""
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    s = _to(s, "finance", APPROVE)          # approves first this time
    s = _to(s, "quality", REQUEST_CHANGES, comment="GxP impact.")
    assert s.active == ("prepare",)


def test_either_reviewer_can_send_the_whole_thing_back():
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    s = _to(s, "quality", REQUEST_CHANGES, comment="GxP impact — needs a quality agreement.")
    s = _to(s, "finance", APPROVE)
    assert s.active == ("prepare",)


# ==========================================================================
# The counterparty round
# ==========================================================================

def test_a_counterparty_markup_rejoins_the_ordinary_review_loop():
    """The correction: a markup is just another version. It goes back through
    the SAME automated + legal review our own draft does — there is no separate
    redlining stage to keep in step."""
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    s = _to(s, "finance", APPROVE)
    s = _to(s, "quality", APPROVE)
    assert s.active == ("counterparty",)

    s = _to(s, "counterparty", REQUEST_CHANGES, comment="They returned a markup.")
    assert s.active == ("ai_review",)            # ...the normal review path

    s = _to(s, "ai_review", APPROVE, comment="one change vs the last version")
    assert s.active == ("legal_review",)
    assert rounds_of(s, "ai_review") == 2        # round 2 of the negotiation


def test_a_negotiation_can_close():
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    s = _to(s, "finance", APPROVE)
    s = _to(s, "quality", APPROVE)
    s = _to(s, "counterparty", APPROVE, comment="They accept.")
    assert s.active == ("sign",)
    s = _to(s, "sign", APPROVE)
    assert s.status == DONE


def test_the_same_workflow_serves_all_three_starting_situations():
    """We draft it / they attached theirs / template plus the requester's terms.

    All three produce a working document and enter the same loop — the workflow
    does not care which, which is why 'draft' was the wrong first step.
    """
    for note in ("drafted from template", "used their attached paper",
                 "template + requester's terms"):
        s = start(NDA)
        s = _to(s, "prepare", APPROVE, comment=note)
        assert s.active == ("ai_review",)


# ==========================================================================
# Deviation — flexibility that stays on the record
# ==========================================================================

def test_a_step_can_be_skipped_with_a_reason():
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    s = deviate(s, NDA, action="skip", step_id="quality",
                reason="No GxP impact — pure services NDA.", actor="A. Sharma")
    dev = [e for e in s.events if e.kind == "deviated"][-1]
    assert dev.reason.startswith("No GxP impact")
    assert dev.actor == "A. Sharma"


def test_a_deviation_without_a_reason_is_refused():
    """The reason is the whole point — it is what makes the departure auditable
    instead of a bypass."""
    s = _reach_legal_review()
    with pytest.raises(WorkflowError, match="reason"):
        deviate(s, NDA, action="skip", step_id="legal_review", reason="   ", actor="someone")


def test_an_extra_reviewer_can_be_pulled_in_mid_flow():
    """Not in the template — added live, and recorded."""
    s = _reach_legal_review()
    s = deviate(s, NDA, action="add_reviewer", step_id="legal_review",
                reason="Ask Tax about the withholding clause.", actor="A. Sharma")
    assert "legal_review" in s.active           # flow shape unchanged
    assert [e for e in s.events if e.kind == "deviated"]


# ==========================================================================
# Guards
# ==========================================================================

def test_a_step_only_accepts_the_outcomes_it_offers():
    s = start(NDA)
    with pytest.raises(WorkflowError, match="does not offer"):
        _to(s, "prepare", REJECT)          # a system draft step cannot "reject"


def test_a_closed_step_cannot_be_answered():
    s = _reach_legal_review()
    with pytest.raises(WorkflowError, match="not open"):
        _to(s, "prepare", APPROVE)


def test_a_finished_run_cannot_be_advanced():
    s = _reach_legal_review()
    s = _to(s, "legal_review", REJECT)
    with pytest.raises(WorkflowError, match="rejected"):
        _to(s, "legal_review", APPROVE)


def test_a_workflow_with_a_dead_end_step_is_refused_at_build_time():
    """Fails on import, not halfway through a live negotiation."""
    with pytest.raises(WorkflowError, match="no outcomes"):
        build("bad", "Bad", "a", [Step(id="a", name="A", asks="?", outcomes={})])


def test_a_workflow_routing_to_a_missing_step_is_refused():
    with pytest.raises(WorkflowError, match="unknown step"):
        build("bad", "Bad", "a", [Step(id="a", name="A", asks="?", outcomes={APPROVE: "ghost"})])


# ==========================================================================
# Visibility
# ==========================================================================

def test_every_step_reports_its_evidence():
    """What the application records today and shows nowhere together."""
    s = _reach_legal_review()
    s = _to(s, "ai_review", APPROVE) if "ai_review" in s.active else s
    s = _to(s, "legal_review", REQUEST_CHANGES, actor="A. Sharma",
            comment="Clause 7", confidence=None)

    rows = evidence(s, NDA)
    last = rows[-1]
    assert last["step"] == "Legal review"
    assert last["asks"].startswith("Review the flagged points")
    assert last["outcome"] == REQUEST_CHANGES
    assert last["by"] == "A. Sharma"
    assert last["comment"] == "Clause 7"


def test_an_agent_step_can_record_its_confidence():
    s = start(NDA)
    s = _to(s, "prepare", APPROVE)
    s = _to(s, "ai_review", APPROVE, actor="nda-agent", confidence=0.88)
    ai = [r for r in evidence(s, NDA) if r["by"] == "nda-agent"][-1]
    assert ai["confidence"] == 0.88


def test_the_whole_history_is_ordered_and_immutable():
    s = _reach_legal_review()
    s = _to(s, "legal_review", APPROVE)
    assert [e.seq for e in s.events] == list(range(1, len(s.events) + 1))
    with pytest.raises(Exception):
        s.events[0].outcome = "tampered"      # frozen dataclass


# ==========================================================================
# Assignment — a step lands on a named person, not a queue
# ==========================================================================

def _roster():
    from app.ideal.roster import Member, Roster
    return Roster(members=(
        Member("Legal Ops bot", "Legal Ops"),
        Member("A. Sharma", "Legal & IP", capacity=3),
        Member("P. Nair", "Legal & IP", capacity=3),
        Member("M. Rao", "Finance & Tax"),
        Member("R. Iyer", "Quality & Compliance"),
    ))


def test_a_step_is_auto_assigned_from_its_department():
    from app.ideal.roster import auto_assigner
    s = start(NDA, assigner=auto_assigner(_roster()))
    assert s.assignees["prepare"] == "Legal Ops bot"


def test_the_least_loaded_person_gets_it():
    """A. Sharma is on two other matters, so P. Nair takes this one."""
    from app.ideal.roster import auto_assigner
    a = auto_assigner(_roster(), other_load={"A. Sharma": 2})
    s = start(NDA, assigner=a)
    s = advance(s, NDA, step_id="prepare", outcome=APPROVE, assigner=a)
    s = advance(s, NDA, step_id="ai_review", outcome=APPROVE, assigner=a)
    assert s.assignees["legal_review"] == "P. Nair"


def test_both_halves_of_a_parallel_step_get_a_person():
    from app.ideal.roster import auto_assigner
    a = auto_assigner(_roster())
    s = start(NDA, assigner=a)
    s = advance(s, NDA, step_id="prepare", outcome=APPROVE, assigner=a)
    s = advance(s, NDA, step_id="ai_review", outcome=APPROVE, assigner=a)
    s = advance(s, NDA, step_id="legal_review", outcome=APPROVE, assigner=a)
    assert s.assignees["finance"] == "M. Rao"
    assert s.assignees["quality"] == "R. Iyer"


def test_a_full_department_overflows_rather_than_overloading():
    from app.ideal.roster import Member, Roster, pick
    r = Roster(
        members=(Member("A", "Legal & IP", capacity=1), Member("B", "Legal Ops")),
        overflow={"Legal & IP": "Legal Ops"},
    )
    assert pick(r, "Legal & IP", load={"A": 1}) == "B"


def test_nobody_free_leaves_the_step_honestly_unassigned():
    """Better an unassigned step you can see than one silently dumped on
    someone already at capacity."""
    from app.ideal.roster import Member, Roster, pick
    r = Roster(members=(Member("A", "Legal & IP", capacity=1),))
    assert pick(r, "Legal & IP", load={"A": 1}) is None


def test_reassignment_is_recorded_not_overwritten():
    """'It sat with three different people' must stay answerable."""
    s = _reach_legal_review()
    s = assign(s, NDA, step_id="legal_review", to="A. Sharma", by="ops")
    s = assign(s, NDA, step_id="legal_review", to="P. Nair", by="A. Sharma")
    kinds = [e.kind for e in s.events if e.step_id == "legal_review"]
    assert "assigned" in kinds and "reassigned" in kinds
    assert s.assignees["legal_review"] == "P. Nair"


def test_work_done_inside_a_step_is_recorded():
    """The outcome says how it ended; this says what happened while it was open."""
    s = _reach_legal_review()
    s = assign(s, NDA, step_id="legal_review", to="A. Sharma", by="ops")
    s = record_change(s, NDA, step_id="legal_review", what="struck the assignment clause", by="A. Sharma")
    s = _to(s, "legal_review", REQUEST_CHANGES, actor="A. Sharma", comment="cap the liability")

    visit = [v for v in step_report(s, NDA) if v["step"] == "Legal review"][0]
    assert visit["with"] == "A. Sharma"
    assert visit["changes"][0]["what"] == "struck the assignment clause"
    assert visit["outcome"] == REQUEST_CHANGES
    assert visit["decided_by"] == "A. Sharma"


def test_a_change_must_say_what_changed():
    s = _reach_legal_review()
    with pytest.raises(WorkflowError, match="what changed"):
        record_change(s, NDA, step_id="legal_review", what="  ", by="A. Sharma")
