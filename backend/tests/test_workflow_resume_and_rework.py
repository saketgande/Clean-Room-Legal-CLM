"""WF-03: waiting runs settle from the real state of what they wait on (including
rejection) and progress without the UI. WF-04: sending a run back past an approval
or signature takes the contract back too."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.approvals.models import ApprovalRequest
from app.contracts import lifecycle
from app.core.enums import ApprovalStatus, SignatureStatus
from app.signatures.models import SignatureRequest
from app.workflows import service


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows


class DB:
    def __init__(self, by_entity=None):
        self.by_entity = by_entity or {}

    def scalars(self, stmt):
        return _Rows(self.by_entity.get(stmt.column_descriptions[0]["entity"], []))

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _sr(idx, status, result=None):
    return SimpleNamespace(idx=idx, status=status, note=None, result=result)


def _run(steps, *, contract_id="c-1", current_index=0, status="waiting"):
    return SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id=contract_id, flow_name="MSA",
                           steps=steps, current_index=current_index, status=status, error=None)


def _wire(monkeypatch, step_runs, stage=None):
    monkeypatch.setattr(service, "_step_runs", lambda db, run: step_runs)
    monkeypatch.setattr(service, "_get_contract", lambda db, cid: SimpleNamespace(id=cid, lifecycle_stage=stage))


def test_a_rejected_contract_approval_fails_the_run_instead_of_waiting_forever(monkeypatch):
    srs = [_sr(0, "waiting_job")]
    _wire(monkeypatch, srs, stage="review")
    run = _run([{"type": "approval"}])
    assert service._resolve_waiting_group(DB(), run=run, steps=run.steps) == "failed"
    assert srs[0].status == "failed"


def test_a_cleared_contract_approval_advances(monkeypatch):
    _wire(monkeypatch, [_sr(0, "waiting_job")], stage="signature")
    run = _run([{"type": "approval"}])
    assert service._resolve_waiting_group(DB(), run=run, steps=run.steps) == "advance"


def test_a_rejected_intake_approval_chain_fails_the_run(monkeypatch):
    _wire(monkeypatch, [_sr(0, "waiting_job", {"approval_ids": ["a-1"]})])
    run = _run([{"type": "approval"}], contract_id=None)
    db = DB({ApprovalRequest: [SimpleNamespace(status=ApprovalStatus.REJECTED)]})
    assert service._resolve_waiting_group(db, run=run, steps=run.steps) == "failed"


def test_a_parallel_group_with_an_ai_step_mid_run_is_re_driven(monkeypatch):
    monkeypatch.setattr(lifecycle, "_current_version_approved", lambda db, contract: False)
    _wire(monkeypatch, [_sr(0, "running"), _sr(1, "waiting_job")], stage="approval")
    run = _run([{"type": "ai_task"}, {"type": "approval", "parallel": True}])
    assert service._resolve_waiting_group(DB(), run=run, steps=run.steps) == "rerun"


def test_advance_run_never_re_executes_a_step_that_is_already_waiting(monkeypatch):
    monkeypatch.setattr(service, "_step_runs", lambda db, run: [_sr(0, "waiting_job")])
    executed = []

    async def fake_execute(*args, **kwargs):
        executed.append(True)
        return "wait"

    monkeypatch.setattr(service, "_execute_step", fake_execute)
    run = _run([{"type": "approval"}], status="running")
    asyncio.run(service.advance_run(DB(), run=run, actor=SimpleNamespace(id="u-1")))
    assert executed == []
    assert run.status == "waiting"


def test_the_resume_task_is_on_the_beat_schedule():
    from app.jobs.celery_app import celery_app

    assert "app.jobs.tasks.resume_workflow_runs" in {e["task"] for e in celery_app.conf.beat_schedule.values()}


def test_sending_back_past_approval_rewinds_the_contract(monkeypatch):
    moves = []

    def fake_transition(db, *, contract, to_stage, actor_user_id, reason, override, override_authorized):
        moves.append((contract.lifecycle_stage, to_stage, reason))
        contract.lifecycle_stage = to_stage

    monkeypatch.setattr(lifecycle, "transition_contract_stage", fake_transition)
    contract = SimpleNamespace(id="c-1", lifecycle_stage="approval")
    monkeypatch.setattr(service, "_get_contract", lambda db, cid: contract)
    rung = SimpleNamespace(status=ApprovalStatus.PENDING, updated_by_user_id=None)
    envelope = SimpleNamespace(status=SignatureStatus.SENT, provider_envelope_id="env-1", updated_by_user_id=None)
    db = DB({ApprovalRequest: [rung], SignatureRequest: [envelope]})
    rework = service._rewind_contract_for_rework(
        db, run=_run([], current_index=1), steps=[{"type": "human_task"}, {"type": "approval"}],
        from_idx=1, to_idx=0, actor=SimpleNamespace(id="u-1"), note="Fix the liability cap",
    )
    assert moves == [("approval", "review", "Workflow sent back for rework: Fix the liability cap")]
    assert rung.status == ApprovalStatus.CANCELLED
    assert envelope.status == SignatureStatus.VOIDED
    assert rework == {"cancelled_approvals": 1, "voided_envelopes": ["env-1"]}


def test_a_signed_contract_cannot_be_sent_back_past_signature(monkeypatch):
    monkeypatch.setattr(service, "_get_contract", lambda db, cid: SimpleNamespace(id=cid, lifecycle_stage="active"))
    steps = [{"type": "human_task"}, {"type": "approval"}, {"type": "signature"}]
    with pytest.raises(HTTPException) as exc:
        service._rewind_contract_for_rework(DB(), run=_run(steps, current_index=2), steps=steps,
                                            from_idx=2, to_idx=0, actor=SimpleNamespace(id="u-1"), note=None)
    assert exc.value.status_code == 409


def test_a_return_that_reopens_no_gate_leaves_the_contract_alone(monkeypatch):
    def no_contract(db, cid):
        raise AssertionError("the contract must not be touched")

    monkeypatch.setattr(service, "_get_contract", no_contract)
    steps = [{"type": "human_task"}, {"type": "human_task"}, {"type": "human_task"}]
    rework = service._rewind_contract_for_rework(DB(), run=_run(steps, current_index=2), steps=steps,
                                                 from_idx=2, to_idx=1, actor=SimpleNamespace(id="u-1"), note=None)
    assert rework == {"cancelled_approvals": 0, "voided_envelopes": []}
