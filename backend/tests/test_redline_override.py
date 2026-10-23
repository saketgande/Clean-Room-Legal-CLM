"""Sending a contract for approval with redlines still open.

Leaving Review is blocked while any redline is undecided. A user who holds
contract:lifecycle_override may go past it with a stated reason. The move to
Approval is made by the workflow engine when its review step is completed, not
by the caller, so the override is passed by the route that completes the step.
These tests guard that the override is checked, scoped and recorded.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.contracts import lifecycle
from app.contracts.lifecycle import ContractLifecycleService, redline_override
from app.workflows import routes


class FakeDB:
    """Answers the two lookups a transition makes: the locked contract, then the
    number of open redlines."""

    def __init__(self, contract, open_redlines):
        self.contract, self.open_redlines = contract, open_redlines
        self.added = []
        self._answers = [contract, open_redlines]

    def scalar(self, _statement):
        return self._answers.pop(0)

    def add(self, row):
        self.added.append(row)


@pytest.fixture
def move(monkeypatch):
    audits = []
    monkeypatch.setattr(lifecycle, "write_audit_log", lambda db, **kw: audits.append(kw))
    monkeypatch.setattr(lifecycle, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr(ContractLifecycleService, "_fire_stage_entry_triggers", lambda self, **kw: None)

    def run(*, from_stage="review", to_stage="approval", open_redlines=2, **kwargs):
        contract = SimpleNamespace(id="c-1", org_id="org-1", lifecycle_stage=from_stage, updated_by_user_id=None)
        db = FakeDB(contract, open_redlines)
        ContractLifecycleService(db).transition_contract_stage(
            contract=contract, to_stage=to_stage, actor_user_id="u-1", **kwargs
        )
        [history] = db.added
        return contract, history, audits

    return run


def test_open_redlines_block_leaving_review(move):
    with pytest.raises(HTTPException) as exc:
        move()
    assert exc.value.status_code == 409 and "2 open redline" in exc.value.detail
    # Plain words for the person who reads it: the permission's internal name is not one of them.
    assert "lifecycle_override" not in exc.value.detail and "override rights" in exc.value.detail


def test_an_authorized_override_needs_a_reason(move):
    with pytest.raises(HTTPException) as exc:
        move(override=True, override_authorized=True)
    assert exc.value.status_code == 422


def test_a_workflow_step_completed_with_an_override_gets_past_the_gate(move):
    with redline_override("Both are cosmetic and the client is waiting"):
        contract, history, audits = move()
    assert contract.lifecycle_stage == "approval"
    assert history.override_used is True
    assert history.reason == "Both are cosmetic and the client is waiting"
    assert audits[0]["metadata"]["override"] is True and audits[0]["metadata"]["override_authorized"] is True


def test_the_override_ends_with_the_block_even_after_an_error(move):
    with pytest.raises(RuntimeError), redline_override("Because"):
        raise RuntimeError("the step failed")
    with pytest.raises(HTTPException) as exc:
        move()
    assert exc.value.status_code == 409


def test_a_blank_reason_overrides_nothing(move):
    with redline_override("   "), pytest.raises(HTTPException) as exc:
        move()
    assert exc.value.status_code == 409


def test_with_no_open_redlines_the_move_is_not_marked_as_an_override(move):
    with redline_override("Because"):
        _, history, _ = move(open_redlines=0)
    assert history.override_used is False and history.reason is None


def test_the_override_does_not_skip_the_approval_before_signature_check(move, monkeypatch):
    monkeypatch.setattr(lifecycle, "_current_version_approved", lambda db, contract: False)
    with redline_override("Because"), pytest.raises(HTTPException) as exc:
        move(from_stage="approval", to_stage="signature", open_redlines=0)
    assert exc.value.status_code == 409 and "approval chain" in exc.value.detail


def test_the_override_does_not_allow_a_move_the_state_machine_forbids(move):
    with redline_override("Because"), pytest.raises(HTTPException) as exc:
        move(from_stage="drafting", to_stage="active")
    assert exc.value.status_code == 409 and "Invalid lifecycle transition" in exc.value.detail


# --- the route that completes the workflow step --------------------------------


class FakeWorkflow:
    def __init__(self):
        self.seen = []

    def complete_human_step(self, **kwargs):
        self.seen.append(("complete", lifecycle._redline_override_reason.get()))

    async def advance_run(self, **kwargs):
        self.seen.append(("advance", lifecycle._redline_override_reason.get()))
        return kwargs["run"]

    def serialize_run(self, run):
        return {"id": run.id}


def _complete(monkeypatch, payload, permissions):
    monkeypatch.setattr(routes, "_get_run", lambda db, user, run_id: SimpleNamespace(id="run-1"))
    workflow = FakeWorkflow()
    user = SimpleNamespace(id="u-1", permission_values=permissions)
    request = SimpleNamespace(state=SimpleNamespace(request_id=None))
    body = routes.CompleteStepPayload(**payload)
    result = asyncio.run(
        routes.complete_step("run-1", body, request, db=None, workflow_service=workflow, current_user=user)
    )
    return result, workflow


def test_completing_a_step_without_override_carries_none(monkeypatch):
    _, workflow = _complete(monkeypatch, {}, permissions=[])
    assert workflow.seen == [("complete", None), ("advance", None)]


def test_override_reason_reaches_the_engine_only_while_the_step_is_completed(monkeypatch):
    _, workflow = _complete(
        monkeypatch, {"override_reason": "  Cosmetic only  "}, permissions=["contract:lifecycle_override"]
    )
    assert workflow.seen == [("complete", "Cosmetic only"), ("advance", "Cosmetic only")]
    assert lifecycle._redline_override_reason.get() is None


def test_override_needs_the_permission(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _complete(monkeypatch, {"override_reason": "Cosmetic only"}, permissions=["contract:update"])
    assert exc.value.status_code == 403 and "lifecycle_override" not in exc.value.detail
    assert "override" in exc.value.detail.lower()


def test_override_needs_a_reason(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _complete(monkeypatch, {"override_reason": "   "}, permissions=["contract:lifecycle_override"])
    assert exc.value.status_code == 422


def test_a_refused_override_does_not_touch_the_workflow(monkeypatch):
    workflow = FakeWorkflow()
    monkeypatch.setattr(routes, "_get_run", lambda db, user, run_id: SimpleNamespace(id="run-1"))
    user = SimpleNamespace(id="u-1", permission_values=[])
    with pytest.raises(HTTPException):
        asyncio.run(
            routes.complete_step(
                "run-1", routes.CompleteStepPayload(override_reason="Because"),
                SimpleNamespace(state=SimpleNamespace(request_id=None)),
                db=None, workflow_service=workflow, current_user=user,
            )
        )
    assert workflow.seen == []


def test_no_refusal_quotes_the_permission_code():
    """Bug 35: a person who is refused is told what to do, not the name of a permission row."""
    import ast
    import inspect

    from app.ai import tool_runtime
    from app.signatures import routes as signature_routes
    from app.workflows import routes as workflow_routes

    for module in (lifecycle, workflow_routes, signature_routes, tool_runtime):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPException":
                said = "".join(
                    n.value for arg in node.args + [k.value for k in node.keywords]
                    for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                )
                assert "lifecycle_override" not in said, f"{module.__name__}: {said[:120]}"
