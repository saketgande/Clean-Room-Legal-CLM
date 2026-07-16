"""Phase D — per-step SLA + escalation of overdue approvals.

The sweep itself (async, DB + email) is exercised end-to-end against a live
stack; here we lock the wiring and the target passthrough.
"""
from types import SimpleNamespace

from app.approvals import routes
from app.approvals.service import _rule_targets
from app.jobs import tasks
from app.jobs.celery_app import celery_app


def _step(**kw):
    base = dict(
        step_order=1, stage=1, condition=None, approver_group_id="g1",
        approver_user_id=None, approver_role=None, mode="any",
        sla_hours=None, escalation_group_id=None, escalation_user_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_rule_targets_carry_sla_and_escalation():
    rule = SimpleNamespace(
        id="R1", steps=[_step(sla_hours=48, escalation_group_id="gc", escalation_user_id=None)],
        approver_user_id=None, approver_role=None,
    )
    targets = _rule_targets(rule)
    assert targets[0]["sla_hours"] == 48
    assert targets[0]["escalation_group_id"] == "gc"
    assert targets[0]["escalation_user_id"] is None


def test_rule_with_no_steps_defaults_escalation_fields():
    rule = SimpleNamespace(id="R1", steps=[], approver_user_id="u1", approver_role=None)
    t = _rule_targets(rule)[0]
    assert t["sla_hours"] is None
    assert t["escalation_group_id"] is None and t["escalation_user_id"] is None


def test_escalation_endpoint_registered():
    paths = {(r.path, tuple(sorted(r.methods))) for r in routes.router.routes}
    assert ("/approvals/escalations/run", ("POST",)) in paths


def test_escalation_task_and_beat_registered():
    assert hasattr(tasks, "escalate_overdue_approvals_task")
    assert "escalate-overdue-approvals" in celery_app.conf.beat_schedule
    assert (
        celery_app.conf.beat_schedule["escalate-overdue-approvals"]["task"]
        == "app.jobs.tasks.escalate_overdue_approvals_task"
    )
