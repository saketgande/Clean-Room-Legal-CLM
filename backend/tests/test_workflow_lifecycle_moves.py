"""The workflow moves the contract through the lifecycle, and a rejection sends it
back to Review instead of stopping it.

Before: a rejected approval left the workflow "failed" until someone restarted it
by hand, a Review step never brought its contract into Review, and approvals could
be started from three places outside any workflow (Approvals page, intake route,
the assistant) — so an approval could exist that no workflow asked for.
"""

from types import SimpleNamespace

import app.models  # noqa: F401
from app.ai.tool_registry import tool_registry
from app.approvals.routes import router as approvals_router
from app.intake.routes import router as intake_router
from app.workflows import service

STEPS = [
    {"type": "clm_draft", "name": "Draft", "stage": "drafting"},
    {"type": "human_task", "name": "Legal review", "stage": "review"},
    {"type": "human_task", "name": "Privacy review", "stage": "review"},
    {"type": "approval", "name": "Finance approval", "stage": "approval"},
    {"type": "signature", "name": "Sign", "stage": "signature"},
]


def _run_at(monkeypatch, statuses, current=3):
    srs = [SimpleNamespace(idx=i, status=st) for i, st in enumerate(statuses)]
    monkeypatch.setattr(service, "_step_runs", lambda db, run: srs)
    return SimpleNamespace(current_index=current)


def test_a_rejection_goes_back_to_the_last_review_that_actually_ran(monkeypatch):
    """Privacy review was skipped (its condition didn't hold) — rework goes to Legal review."""
    run = _run_at(monkeypatch, ["done", "done", "skipped", "failed", "pending"])
    assert service._rework_target(None, run=run, steps=STEPS) == 1


def test_the_steps_own_send_back_setting_wins(monkeypatch):
    steps = [dict(s) for s in STEPS]
    steps[3]["config"] = {"return_to": 0}
    run = _run_at(monkeypatch, ["done", "done", "done", "failed", "pending"])
    assert service._rework_target(None, run=run, steps=steps) == 0


def test_with_nothing_to_go_back_to_the_run_stops_as_before(monkeypatch):
    steps = [{"type": "approval", "name": "Board", "stage": "approval"}]
    run = _run_at(monkeypatch, ["failed"], current=0)
    assert service._rework_target(None, run=run, steps=steps) is None


def test_a_review_step_brings_its_contract_into_review(monkeypatch):
    moved = []
    monkeypatch.setattr(service, "_get_contract", lambda db, cid: SimpleNamespace(lifecycle_stage="drafting"))
    monkeypatch.setattr(service, "_advance_contract_to", lambda db, **kw: moved.append(kw["target"]))
    run = SimpleNamespace(contract_id="c1", flow_name="MSA")
    service._enter_step_stage(None, run=run, step=STEPS[1], actor=None)
    assert moved == ["review"]


def test_the_contract_never_moves_backwards_or_into_approval_early(monkeypatch):
    moved = []
    monkeypatch.setattr(service, "_get_contract", lambda db, cid: SimpleNamespace(lifecycle_stage="approval"))
    monkeypatch.setattr(service, "_advance_contract_to", lambda db, **kw: moved.append(kw["target"]))
    run = SimpleNamespace(contract_id="c1", flow_name="MSA")
    service._enter_step_stage(None, run=run, step=STEPS[1], actor=None)  # review step, contract past it
    service._enter_step_stage(None, run=run, step=STEPS[3], actor=None)  # approval: its own branch
    assert moved == []


def test_approvals_can_only_be_started_by_a_workflow():
    approval_posts = {r.path for r in approvals_router.routes if "POST" in getattr(r, "methods", set())}
    assert "/approvals/requests" not in approval_posts
    assert not any(r.path.endswith("/submit-for-approval") for r in intake_router.routes)
    assert "submit_for_approval" not in {t.name for t in tool_registry.all()}
