"""WF-09 / WF-10: a step failure is recorded on the step that raised (after rolling
back a broken transaction), and a disabled workflow can't be started."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

import app.models  # noqa: F401  (register every mapper)
from app.workflows import service


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class DB:
    def __init__(self, step_runs=()):
        self.step_runs, self.rollbacks = list(step_runs), 0

    def scalars(self, _stmt):
        return _Rows(self.step_runs)

    def execute(self, _stmt):
        return _Rows([])

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


ACTOR = SimpleNamespace(id="u-1")
STEPS = [{"type": "human_task", "name": "Quality review"},
         {"type": "ai_task", "name": "Privacy check", "parallel": True}]


def _advance(monkeypatch, error):
    async def execute(db, *, run, step, sr, actor):
        if step["name"] == "Privacy check":
            raise error
        sr.status = "waiting_human"
        return "wait"

    monkeypatch.setattr(service, "_execute_step", execute)
    quality, privacy = (SimpleNamespace(idx=i, status="pending", note=None, result=None, assignee_user_id=None,
                                        step_name=STEPS[i]["name"]) for i in range(2))
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id=None, flow_name="MSA",
                          steps=STEPS, current_index=0, status="running", error=None)
    db = DB([quality, privacy])
    asyncio.run(service.advance_run(db, run=run, actor=ACTOR))
    return db, run, quality, privacy


def test_the_failure_lands_on_the_step_that_raised(monkeypatch):
    db, run, quality, privacy = _advance(monkeypatch, RuntimeError("privacy agent crashed"))
    assert (quality.status, privacy.status, run.status) == ("waiting_human", "failed", "failed")
    assert "privacy agent crashed" in privacy.note and quality.note is None
    assert db.rollbacks == 0  # an ordinary error keeps the session and the work already done


def test_a_database_error_is_rolled_back_before_the_failure_is_recorded(monkeypatch):
    db, run, _quality, privacy = _advance(monkeypatch, OperationalError("UPDATE step", {}, Exception("connection reset")))
    assert db.rollbacks == 1
    assert (privacy.status, run.status) == ("failed", "failed")


def test_a_disabled_workflow_cannot_be_started(monkeypatch):
    monkeypatch.setattr(service, "get_run_for_request", lambda db, request_id, org_id: None)
    flow = SimpleNamespace(id="wf-1", name="Old NDA flow", enabled=False, steps=[], version=1)
    request = SimpleNamespace(id="req-1", org_id="org-1", contract_id=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.start_flow(DB(), actor=ACTOR, request=request, flow=flow))
    assert exc.value.status_code == 409
