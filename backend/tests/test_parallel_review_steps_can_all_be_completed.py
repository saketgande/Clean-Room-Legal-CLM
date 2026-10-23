"""Steps flagged ``parallel`` run side by side ("all must respond"). ``current_index`` stays on the FIRST of
them, so once that one is done a caller that does not name a step used to complete nothing (and still said
"Step completed") while the other reviewer's step waited for ever. Seen in the built-in Master Services
Agreement: Quality review and Privacy review."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.contracts import stage_moves
from app.workflows import service


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows


class DB:
    def __init__(self, step_runs):
        self.step_runs = step_runs

    def scalars(self, stmt):
        return _Rows(self.step_runs if stmt.column_descriptions[0]["entity"] is service.WorkflowStepRun else [])

    def flush(self):
        pass

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _sr(idx, status, assignee=None):
    return SimpleNamespace(idx=idx, status=status, note=None, result=None, assignee_user_id=assignee, step_name=f"Step {idx}")


def _run(steps, current_index=0):
    return SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id=None, flow_name="MSA", steps=steps,
                           current_index=current_index, status="waiting", error=None, created_by_user_id="u-1", context={})


MSA = [{"type": "human_task"}, {"type": "human_task", "parallel": True}, {"type": "approval"}]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr(service, "_take_rework_reply", lambda db, **kw: None)
    monkeypatch.setattr(service, "_require_step_actor", lambda db, **kw: None)


def test_the_other_reviewer_can_finish_once_the_first_is_done():
    quality, privacy = _sr(0, "done"), _sr(1, "waiting_human", "u-privacy")
    run = _run(MSA)
    service.complete_human_step(DB([quality, privacy]), run=run, actor=SimpleNamespace(id="u-privacy"))
    assert privacy.status == "done"
    assert run.current_index == 2  # the group is over, the run moves on


def test_without_a_step_named_the_person_gets_their_own_step():
    quality, privacy = _sr(0, "waiting_human", "u-quality"), _sr(1, "waiting_human", "u-privacy")
    run = _run(MSA)
    service.complete_human_step(DB([quality, privacy]), run=run, actor=SimpleNamespace(id="u-privacy"))
    assert (quality.status, privacy.status) == ("waiting_human", "done")
    assert run.current_index == 0  # the other reviewer has not answered yet


def test_a_team_member_gets_the_step_they_may_act_on(monkeypatch):
    quality, privacy = _sr(0, "waiting_human"), _sr(1, "waiting_human")

    def only_privacy(db, *, sr, actor):
        if sr is not privacy:
            raise HTTPException(403, "not yours")

    monkeypatch.setattr(service, "_require_step_actor", only_privacy)
    service.complete_human_step(DB([quality, privacy]), run=_run(MSA), actor=SimpleNamespace(id="u-team"))
    assert (quality.status, privacy.status) == ("waiting_human", "done")


def test_a_step_named_by_the_caller_is_still_the_one_completed():
    quality, privacy = _sr(0, "waiting_human"), _sr(1, "waiting_human", "u-1")
    service.complete_human_step(DB([quality, privacy]), run=_run(MSA), actor=SimpleNamespace(id="u-1"), step_idx=0)
    assert (quality.status, privacy.status) == ("done", "waiting_human")


def test_steps_that_run_one_after_another_still_complete_the_current_one():
    first, second = _sr(0, "waiting_human"), _sr(1, "pending")
    run = _run([{"type": "human_task"}, {"type": "human_task"}])
    service.complete_human_step(DB([first, second]), run=run, actor=SimpleNamespace(id="u-1"))
    assert (first.status, second.status, run.current_index) == ("done", "pending", 1)


# ---- the chat's "send it to approval" looks at the same step ---------------------------------------------

def _view(statuses, steps=None, current_index=0, assignees=None):
    steps = steps or MSA
    assignees = assignees or {}
    return {"current_index": current_index, "steps": [
        {"idx": i, "name": f"Step {i}", "type": t["type"], "parallel": bool(t.get("parallel")), "status": st,
         "assignee_user_id": assignees.get(i)}
        for i, (t, st) in enumerate(zip(steps, statuses))
    ]}


def test_the_waiting_reviewer_is_found_after_the_first_is_done():
    cur, nxt = stage_moves._step_waiting_for_a_person(_view(["done", "waiting_human", "pending"]), actor_id="u-1")
    assert (cur["idx"], nxt["idx"], nxt["type"]) == (1, 2, "approval")


def test_with_both_waiting_the_actors_own_step_comes_first_and_the_other_is_next():
    view = _view(["waiting_human", "waiting_human", "pending"], assignees={0: "u-a", 1: "u-b"})
    cur, nxt = stage_moves._step_waiting_for_a_person(view, actor_id="u-b")
    assert (cur["idx"], nxt["idx"]) == (1, 0)  # finishing it does not send the contract anywhere yet


def test_the_first_reviewer_finishing_last_is_followed_by_the_step_after_the_group():
    cur, nxt = stage_moves._step_waiting_for_a_person(_view(["waiting_human", "done", "pending"]), actor_id=None)
    assert (cur["idx"], nxt["idx"]) == (0, 2)


def test_a_single_step_is_followed_by_the_next_one():
    steps = [{"type": "human_task"}, {"type": "approval"}]
    cur, nxt = stage_moves._step_waiting_for_a_person(_view(["waiting_human", "pending"], steps=steps), actor_id=None)
    assert (cur["idx"], nxt["idx"]) == (0, 1)


def test_nothing_waiting_names_the_step_the_run_is_at():
    cur, _ = stage_moves._step_waiting_for_a_person(_view(["running", "pending", "pending"]), actor_id=None)
    assert (cur["idx"], cur["status"]) == (0, "running")
