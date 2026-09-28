"""AUTH-05: a workflow step can only be completed or sent back by the people the
engine assigned (or intake staff), and every change is audited with the actor."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.workflows import service


class FakeDB:
    def __init__(self, team_member=None):
        self.team_member = team_member

    def scalar(self, _stmt):
        return self.team_member

    def flush(self):
        pass

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _user(uid, *perms):
    return SimpleNamespace(id=uid, permission_values=set(perms))


@pytest.fixture
def engine(monkeypatch):
    """A run waiting on a human step assigned to u-assignee, with RBAC enforced
    (core/rbac.has_permission is currently disabled app-wide)."""
    monkeypatch.setattr(service, "has_permission", lambda perms, p: p in set(perms))
    sr = SimpleNamespace(idx=1, status="waiting_human", note=None, step_name="Legal Sign-off",
                         assignee_user_id="u-assignee", team_id="team-legal")
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", current_index=1, status="waiting",
                          steps=[{"type": "human_task", "name": "Intake"}, {"type": "human_task", "name": "Legal Sign-off"}])
    audits = []
    monkeypatch.setattr(service, "_sr_at", lambda db, run, idx: sr)
    monkeypatch.setattr(service, "_group_done", lambda db, run, start, end: True)
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: audits.append(kw))
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: None)
    return run, sr, audits


def test_someone_else_cannot_complete_the_step(engine):
    run, sr, audits = engine
    with pytest.raises(HTTPException) as exc:
        service.complete_human_step(FakeDB(), run=run, actor=_user("u-stranger"))
    assert exc.value.status_code == 403
    assert sr.status == "waiting_human"
    assert audits == []


def test_assignee_completes_and_is_recorded(engine):
    run, sr, audits = engine
    service.complete_human_step(FakeDB(), run=run, actor=_user("u-assignee"), note="Looks good", request_id="rid-7")
    assert sr.status == "done"
    assert audits == [{
        "action": "workflow.step_completed", "resource_type": "workflow_run", "resource_id": "run-1",
        "org_id": "org-1", "actor_user_id": "u-assignee", "request_id": "rid-7",
        "after": {"step_idx": 1, "step_name": "Legal Sign-off", "note": "Looks good"},
    }]


def test_team_member_can_complete(engine):
    run, sr, _ = engine
    service.complete_human_step(FakeDB(team_member="member-row"), run=run, actor=_user("u-teammate"))
    assert sr.status == "done"


def test_intake_staff_can_complete(engine):
    run, sr, _ = engine
    service.complete_human_step(FakeDB(), run=run, actor=_user("u-ops", "intake:update"))
    assert sr.status == "done"


def test_someone_else_cannot_send_the_step_back(engine):
    run, _, audits = engine
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.return_run(FakeDB(), run=run, actor=_user("u-stranger"), to_idx=0))
    assert exc.value.status_code == 403
    assert audits == []


def test_editing_a_flow_is_audited_with_before_and_after(monkeypatch):
    audits = []
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: audits.append(kw))
    old_steps = [{"id": "s1", "type": "human_task", "name": "GC approval", "config": {}}]
    flow = SimpleNamespace(id="flow-1", org_id="org-1", name="MSA", description=None, enabled=True,
                           eval_order=1, criteria={}, steps=old_steps, version=3, updated_by_user_id=None)
    service.update_flow(FakeDB(), actor=_user("u-admin"), flow=flow, payload={"steps": []}, request_id="rid-9")
    [row] = audits
    assert row["action"] == "workflow.updated" and row["actor_user_id"] == "u-admin"
    assert row["before"]["steps"] == old_steps
    assert row["after"]["steps"] == [] and row["after"]["version"] == 4
