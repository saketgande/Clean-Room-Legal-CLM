"""Round 2, bug 9: "Send back for rework" to a drafting step used to do nothing.

The step skipped itself again ("the contract already exists"), the toast said success, and the
contract stayed at V1. A send-back to a drafting step now makes that step wait for a person to
redo the draft, with the reason on the step.
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.core.database import engine
from app.workflows import service


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


STEPS = [
    {"id": "s0", "type": "clm_draft", "name": "Prepare draft", "config": {}},
    {"id": "s1", "type": "human_task", "name": "Legal review", "config": {}},
    {"id": "s2", "type": "approval", "name": "Finance approval", "config": {}},
]


def _step_runs(statuses, results=None):
    results = results or {}
    return [SimpleNamespace(idx=i, status=status, result=results.get(i), note=None, step_name=STEPS[i]["name"])
            for i, status in enumerate(statuses)]


def _run(contract_id="c-1", current_index=2):
    return SimpleNamespace(
        id="run-1", org_id="org-1", request_id="req-1", contract_id=contract_id, flow_name="Ladder",
        steps=STEPS, current_index=current_index, status="waiting", error=None, context={}, flow_version=1,
    )


@pytest.fixture
def rig(monkeypatch):
    """The send-back, with the database writes and the re-drive replaced by recorders."""
    seen = SimpleNamespace(advanced=0)

    async def advance(db, *, run, actor):
        seen.advanced += 1
        return run

    async def no_void(ids):
        return None

    monkeypatch.setattr(service, "advance_run", advance)
    monkeypatch.setattr(service, "_void_envelopes", no_void)
    monkeypatch.setattr(service, "_require_step_actor", lambda *a, **k: None)
    monkeypatch.setattr(service, "_sr_at", lambda db, run, idx: None)
    monkeypatch.setattr(service, "_rewind_contract_for_rework",
                        lambda *a, **k: {"cancelled_approvals": 0, "voided_envelopes": []})
    monkeypatch.setattr(service, "add_run_comment", lambda *a, **k: None)
    monkeypatch.setattr(service, "write_audit_log", lambda *a, **k: None)
    monkeypatch.setattr(service, "write_timeline_event", lambda *a, **k: None)
    return seen


def _send_back(monkeypatch, runs, run, to_idx, note="redo the draft"):
    monkeypatch.setattr(service, "_step_runs", lambda db, r: runs)
    asyncio.run(service.return_run(SimpleNamespace(), run=run, actor=SimpleNamespace(id="u-1"),
                                   to_idx=to_idx, note=note))


def test_sending_back_to_a_drafting_step_marks_it_for_rework(monkeypatch, rig):
    runs = _step_runs(["skipped", "done", "waiting_job"])
    _send_back(monkeypatch, runs, _run(), to_idx=0)
    assert runs[0].result["returned_for_rework"] is True
    assert runs[0].result["returned_reason"] == "redo the draft"
    assert [r.status for r in runs] == ["pending", "pending", "pending"]
    assert rig.advanced == 1


def test_only_a_drafting_step_with_a_contract_is_marked(monkeypatch, rig):
    runs = _step_runs(["done", "waiting_human", "pending"])
    _send_back(monkeypatch, runs, _run(current_index=2), to_idx=1)
    assert runs[1].result is None  # a person's step simply waits again

    runs = _step_runs(["skipped", "done", "waiting_job"])
    _send_back(monkeypatch, runs, _run(contract_id=None), to_idx=0)
    assert runs[0].result is None  # no contract yet: the step drafts one as usual


def _enter_draft(db, monkeypatch, result, contract_id="c-1"):
    assigned = []
    monkeypatch.setattr(service, "_assign_step", lambda db, **kw: assigned.append(kw["sr"].idx))
    monkeypatch.setattr(service, "_enter_step_stage", lambda *a, **k: None)
    sr = SimpleNamespace(idx=0, status="pending", result=result, note=None)
    run = _run(contract_id=contract_id, current_index=0)
    outcome = asyncio.run(service._execute_step(db, run=run, step=STEPS[0], sr=sr,
                                                actor=SimpleNamespace(id="u-1")))
    return outcome, sr, assigned


def test_a_drafting_step_that_was_sent_back_waits_for_a_person(db, monkeypatch):
    outcome, sr, assigned = _enter_draft(
        db, monkeypatch, {"returned_for_rework": True, "returned_reason": "redo the draft"})
    assert outcome == "wait"
    assert sr.status == "waiting_human"
    assert assigned == [0]
    assert "redo the draft" in sr.note and "Revise the draft" in sr.note


def test_the_send_back_is_used_up_so_a_later_pass_skips_as_before(db, monkeypatch):
    _, sr, _ = _enter_draft(db, monkeypatch, {"returned_for_rework": True, "returned_reason": "x"})
    assert sr.result["returned_for_rework"] is False
    outcome, sr2, _ = _enter_draft(db, monkeypatch, sr.result)
    assert outcome == "advance"
    assert sr2.status == "skipped"


def test_a_drafting_step_that_was_never_sent_back_still_skips_when_the_contract_exists(db, monkeypatch):
    outcome, sr, assigned = _enter_draft(db, monkeypatch, None)
    assert outcome == "advance"
    assert sr.status == "skipped"
    assert assigned == []
