"""Bug 4: resubmitting after a rejection must carry a written reply.

When an approver rejects, the engine sends the request back to Review. Before this
rule, "Approve & advance" on that step sent the SAME draft straight back to the
approver: the engine never looked at why it had been rejected.

The rule now: a rejected approval leaves a "reply owed" marker on the run
(``run.context["rework"]``). The first human step completed after that must come
with a written reply (``rework_reply``). The reply is kept as a comment on the
request, so the approver and everyone else can read it, and the marker is cleared.

Only a rejected APPROVAL owes a reply. A manual "send back" and a contract pulled
back from signature do not.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.workflows import routes, service
from app.workflows.models import WorkflowRun, WorkflowStepRun

REASON = "term dates are impossible, end is before start"


# --------------------------------------------------------------------------- fakes


class FakeDB:
    def flush(self):
        pass

    def commit(self):
        pass

    def refresh(self, _obj):
        pass

    def scalar(self, _stmt):
        return None


def _user(uid, *perms):
    return SimpleNamespace(id=uid, full_name=f"User {uid}", email=f"{uid}@example.com", permission_values=set(perms))


def _owed(reason=REASON):
    return {"rework": {"reply_required": True, "reason": reason, "from_idx": 2, "to_idx": 1}}


def _run(context=None, steps=None, current_index=1):
    steps = steps if steps is not None else [
        {"type": "human_task", "name": "Intake"},
        {"type": "human_task", "name": "Legal review"},
        {"type": "approval", "name": "Finance approval"},
    ]
    return SimpleNamespace(
        id="run-1", org_id="org-1", request_id="req-1", current_index=current_index, status="waiting",
        steps=steps, context=context if context is not None else {}, error=None,
    )


@pytest.fixture
def engine(monkeypatch):
    """A run waiting on a human step assigned to u-assignee, with RBAC enforced."""
    monkeypatch.setattr(service, "has_permission", lambda perms, p: p in set(perms))
    sr = SimpleNamespace(idx=1, status="waiting_human", note=None, step_name="Legal review",
                         assignee_user_id="u-assignee", team_id=None)
    audits = []
    monkeypatch.setattr(service, "_sr_at", lambda db, run, idx: sr)
    monkeypatch.setattr(service, "_group_done", lambda db, run, start, end: True)
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: audits.append(kw))
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: None)
    return sr, audits


def _complete(run, **kwargs):
    return service.complete_human_step(FakeDB(), run=run, actor=_user("u-assignee"), **kwargs)


# --------------------------------------------------------------------------- the rule


def test_the_step_cannot_be_completed_without_a_reply(engine):
    sr, audits = engine
    run = _run(_owed())
    with pytest.raises(HTTPException) as exc:
        _complete(run)
    assert exc.value.status_code == 422
    assert REASON in exc.value.detail  # the person is told what they are replying to
    assert sr.status == "waiting_human"  # nothing moved
    assert run.current_index == 1
    assert audits == []
    assert run.context == _owed()  # still owed, no comment added


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t "])
def test_a_blank_reply_does_not_count(engine, blank):
    sr, _ = engine
    with pytest.raises(HTTPException) as exc:
        _complete(_run(_owed()), rework_reply=blank)
    assert exc.value.status_code == 422
    assert sr.status == "waiting_human"


def test_a_step_note_is_not_a_reply(engine):
    """The chat stage-move and 'counterparty agreed' paths complete steps with a fixed
    note. That must never pass for the person's answer to the approver."""
    sr, _ = engine
    with pytest.raises(HTTPException) as exc:
        _complete(_run(_owed()), note="Completed from the chat")
    assert exc.value.status_code == 422
    assert sr.status == "waiting_human"


def test_a_reply_lets_the_step_through_and_is_kept_as_a_comment(engine):
    sr, audits = engine
    run = _run(_owed())
    _complete(run, rework_reply="  Fixed: the term now runs 2026-11-01 to 2027-10-31.  ")
    assert sr.status == "done"
    [comment] = run.context["comments"]
    assert comment["kind"] == "reply"
    assert comment["text"] == "Fixed: the term now runs 2026-11-01 to 2027-10-31."
    assert comment["actor_id"] == "u-assignee"
    assert comment["idx"] == 1
    assert run.context["rework"]["reply_required"] is False
    assert [a["action"] for a in audits] == ["workflow.rejection_replied", "workflow.step_completed"]
    assert audits[0]["after"]["reply"] == "Fixed: the term now runs 2026-11-01 to 2027-10-31."


def test_a_reply_is_asked_for_only_once_per_rejection(engine):
    sr, _ = engine
    run = _run(_owed())
    _complete(run, rework_reply="Fixed the dates.")
    sr.status = "waiting_human"  # the next human step after it
    _complete(run)
    assert sr.status == "done"
    assert len(run.context["comments"]) == 1


def test_a_request_that_was_never_rejected_needs_no_reply(engine):
    sr, audits = engine
    run = _run({})
    _complete(run)
    assert sr.status == "done"
    assert "comments" not in run.context
    assert [a["action"] for a in audits] == ["workflow.step_completed"]


def test_a_reply_sent_when_none_is_owed_is_ignored(engine):
    """No stray comment appears just because a client sent the field."""
    sr, _ = engine
    run = _run({})
    _complete(run, rework_reply="hello")
    assert sr.status == "done"
    assert "comments" not in run.context


def test_a_stranger_is_refused_before_hearing_why_it_was_rejected(engine):
    sr, audits = engine
    run = _run(_owed())
    with pytest.raises(HTTPException) as exc:
        service.complete_human_step(FakeDB(), run=run, actor=_user("u-stranger"), rework_reply="x")
    assert exc.value.status_code == 403
    assert REASON not in exc.value.detail
    assert sr.status == "waiting_human"
    assert audits == []
    assert run.context == _owed()


# --------------------------------------------------------------------------- when a reply becomes owed


def _wire_send_back(monkeypatch, step_runs, reason=REASON):
    monkeypatch.setattr(service, "_step_runs", lambda db, run: step_runs)
    monkeypatch.setattr(service, "_rejection_note", lambda db, run: reason)
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: None)


def _sr(idx, status):
    return SimpleNamespace(idx=idx, status=status, note=None, result=None)


def test_a_rejected_approval_leaves_a_reply_owed(monkeypatch):
    steps = [{"type": "human_task", "name": "Legal review"}, {"type": "approval", "name": "Finance approval"}]
    srs = [_sr(0, "done"), _sr(1, "failed")]
    _wire_send_back(monkeypatch, srs)
    run = _run({}, steps=steps, current_index=1)
    assert service._send_back_after_rejection(FakeDB(), run=run, steps=steps, actor_user_id=None) is True
    assert run.current_index == 0
    state = run.context["rework"]
    assert state["reply_required"] is True
    assert state["reason"] == REASON
    assert state["to_idx"] == 0 and state["from_idx"] == 1
    assert [s.status for s in srs] == ["pending", "pending"]


def test_a_signature_pulled_back_owes_no_reply(monkeypatch):
    steps = [{"type": "human_task", "name": "Legal review"}, {"type": "signature", "name": "Signature"}]
    _wire_send_back(monkeypatch, [_sr(0, "done"), _sr(1, "failed")])
    run = _run({}, steps=steps, current_index=1)
    assert service._send_back_after_rejection(FakeDB(), run=run, steps=steps, actor_user_id=None) is True
    assert service.pending_rework_reply(run) is None


def test_a_second_rejection_asks_for_a_new_reply(monkeypatch):
    steps = [{"type": "human_task", "name": "Legal review"}, {"type": "approval", "name": "Finance approval"}]
    _wire_send_back(monkeypatch, [_sr(0, "done"), _sr(1, "failed")], reason="still wrong")
    answered = {"rework": {"reply_required": False, "reason": REASON, "replied_at": "2026-10-08T00:00:00"}}
    run = _run(answered, steps=steps, current_index=1)
    service._send_back_after_rejection(FakeDB(), run=run, steps=steps, actor_user_id=None)
    assert service.pending_rework_reply(run) == {"reply_required": True, "reason": "still wrong"}


def test_a_rejection_inside_a_parallel_group_still_counts(monkeypatch):
    """The failed approval need not be the step the run points at: it can be any step
    of the group that was in flight."""
    steps = [
        {"type": "human_task", "name": "Legal review"},
        {"type": "human_task", "name": "Privacy review", "parallel": True},
        {"type": "approval", "name": "Finance approval", "parallel": True},
    ]
    _wire_send_back(monkeypatch, [_sr(0, "done"), _sr(1, "done"), _sr(2, "failed")])
    run = _run({}, steps=steps, current_index=1)
    service._send_back_after_rejection(FakeDB(), run=run, steps=steps, actor_user_id=None)
    assert service.pending_rework_reply(run) is not None


# --------------------------------------------------------------------------- what the page is told


def test_the_page_is_told_what_is_owed():
    assert service.pending_rework_reply(_run(_owed())) == {"reply_required": True, "reason": REASON}


@pytest.mark.parametrize("context", [{}, {"rework": {"reply_required": False, "reason": "x"}}, {"rework": "junk"}])
def test_the_page_is_told_nothing_when_nothing_is_owed(context):
    assert service.pending_rework_reply(_run(context)) is None


def test_a_run_without_any_context_owes_nothing():
    assert service.pending_rework_reply(SimpleNamespace(context=None)) is None


# --------------------------------------------------------------------------- the route


class FakeWorkflow:
    def __init__(self):
        self.completed = []

    def complete_human_step(self, **kwargs):
        self.completed.append(kwargs)

    async def advance_run(self, **kwargs):
        return kwargs["run"]

    def serialize_run(self, run):
        return {"id": run.id}


def _post(monkeypatch, payload):
    monkeypatch.setattr(routes, "_get_run", lambda db, user, run_id: SimpleNamespace(id="run-1"))
    workflow = FakeWorkflow()
    user = SimpleNamespace(id="u-1", permission_values=[])
    request = SimpleNamespace(state=SimpleNamespace(request_id=None))
    asyncio.run(routes.complete_step(
        "run-1", routes.CompleteStepPayload(**payload), request, db=None, workflow_service=workflow, current_user=user,
    ))
    return workflow


def test_the_route_hands_the_reply_to_the_engine(monkeypatch):
    [call] = _post(monkeypatch, {"rework_reply": "Fixed the dates."}).completed
    assert call["rework_reply"] == "Fixed the dates."


def test_the_route_sends_no_reply_when_the_client_sent_none(monkeypatch):
    [call] = _post(monkeypatch, {}).completed
    assert call["rework_reply"] is None


def test_the_reply_has_a_length_limit():
    with pytest.raises(ValueError):
        routes.CompleteStepPayload(rework_reply="x" * 2001)


# --------------------------------------------------------------------------- real database


def test_the_reply_and_the_cleared_marker_are_saved(db_session):
    """JSON columns only save when the whole value is reassigned. Prove it against
    a real Postgres row, reloaded from the database."""
    import uuid

    from app.auth.models import User
    from app.core.database import utcnow
    from app.intake.models import IntakeRequest
    from app.organizations.models import Organization

    db = db_session
    org = Organization(name="Reply Co", slug=f"reply-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"reply-{uuid.uuid4().hex[:8]}@example.com", full_name="Reviewer",
                hashed_password="h", status="active")
    db.add(user)
    db.flush()
    req = IntakeRequest(
        org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=user.id,
        type_label="New agreement Request", description="t", field_values={}, priority="Medium",
        status="open", stage="new", submitted_at=utcnow(),
        created_by_user_id=user.id, updated_by_user_id=user.id,
    )
    db.add(req)
    db.flush()
    run = WorkflowRun(
        org_id=org.id, request_id=req.id, flow_name="F", flow_version=1,
        steps=[{"id": "s0", "type": "human_task", "name": "Legal review", "config": {}},
               {"id": "s1", "type": "approval", "name": "Finance approval", "config": {}}],
        status="waiting", current_index=0, context=_owed(),
    )
    db.add(run)
    db.flush()
    db.add(WorkflowStepRun(org_id=org.id, flow_run_id=run.id, idx=0, step_type="human_task",
                           step_name="Legal review", status="waiting_human", assignee_user_id=user.id))
    db.flush()

    with pytest.raises(HTTPException) as exc:
        service.complete_human_step(db, run=run, actor=user)
    assert exc.value.status_code == 422

    service.complete_human_step(db, run=run, actor=user, rework_reply="Changed the term to 12 months.")
    db.flush()
    db.expire_all()

    saved = db.get(WorkflowRun, run.id)
    assert saved.context["rework"]["reply_required"] is False
    [comment] = saved.context["comments"]
    assert comment["kind"] == "reply" and comment["text"] == "Changed the term to 12 months."
    assert service.pending_rework_reply(saved) is None


def test_a_rejection_through_the_real_engine_and_route(db_session, monkeypatch):
    """Whole path on a real database: an approver's rejection -> the engine sends the
    request back and marks a reply owed -> the page is told -> the route refuses the
    step without a reply -> accepts it with one -> the reply is a comment on the
    request and nothing is owed any more.

    Real: the database, refresh_run, _send_back_after_rejection, the route function,
    WorkflowService and the serialiser. Stubbed: advance_run, which would run the next
    step (and submit the approval again), and which the test does by hand where needed.
    """
    import uuid

    from app.approvals.models import ApprovalDecision, ApprovalRequest
    from app.auth.models import User
    from app.contracts.models import Contract
    from app.core.database import utcnow
    from app.core.enums import ApprovalStatus
    from app.intake.models import IntakeRequest
    from app.organizations.models import Organization

    db = db_session
    org = Organization(name="Flow Co", slug=f"flow-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"flow-{uuid.uuid4().hex[:8]}@example.com", full_name="Owner",
                hashed_password="h", status="active")
    db.add(user)
    db.flush()
    contract = Contract(org_id=org.id, title="MSA", owner_user_id=user.id)
    db.add(contract)
    db.flush()
    contract.lifecycle_stage = "review"  # where the approvals subsystem puts it on a rejection
    req = IntakeRequest(
        org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=user.id,
        type_label="New agreement Request", description="t", field_values={}, priority="Medium",
        status="open", stage="new", submitted_at=utcnow(),
        created_by_user_id=user.id, updated_by_user_id=user.id,
    )
    db.add(req)
    db.flush()
    run = WorkflowRun(
        org_id=org.id, request_id=req.id, flow_name="MSA", flow_version=1, contract_id=contract.id,
        steps=[{"id": "s0", "type": "human_task", "name": "Legal review", "stage": "review", "config": {}},
               {"id": "s1", "type": "approval", "name": "Finance approval", "stage": "approval", "config": {}}],
        status="waiting", current_index=1,
    )
    db.add(run)
    db.flush()
    legal = WorkflowStepRun(org_id=org.id, flow_run_id=run.id, idx=0, step_type="human_task",
                            step_name="Legal review", status="done", assignee_user_id=user.id)
    finance = WorkflowStepRun(org_id=org.id, flow_run_id=run.id, idx=1, step_type="approval",
                              step_name="Finance approval", status="waiting_job")
    db.add_all([legal, finance])
    rejected = ApprovalRequest(org_id=org.id, contract_id=contract.id, requested_by_user_id=user.id,
                               status=ApprovalStatus.REJECTED, step_order=1, metadata_json={})
    db.add(rejected)
    db.flush()
    db.add(ApprovalDecision(org_id=org.id, approval_request_id=rejected.id, approver_user_id=user.id,
                            decision="reject", comment=REASON, decided_at=utcnow()))
    db.flush()

    advanced = []

    async def fake_advance(db_, *, run, actor):
        advanced.append(run.current_index)
        return run

    monkeypatch.setattr(service, "advance_run", fake_advance)

    # 1. The engine settles the waiting approval from what the approver did.
    asyncio.run(service.refresh_run(db, run=run, actor=user))
    assert run.current_index == 0 and advanced == [0]  # back at Legal review, engine asked to carry on
    workflow = service.WorkflowService(db)
    assert workflow.serialize_run(run)["rework"] == {"reply_required": True, "reason": REASON}

    # What advance_run does for a human step: it waits for the person.
    legal.status = "waiting_human"
    db.flush()

    # 2. The route refuses the step without a reply, and changes nothing.
    request = SimpleNamespace(state=SimpleNamespace(request_id=None))

    def post(**payload):
        return asyncio.run(routes.complete_step(
            run.id, routes.CompleteStepPayload(**payload), request, db=db, workflow_service=workflow, current_user=user,
        ))

    with pytest.raises(HTTPException) as exc:
        post()
    assert exc.value.status_code == 422 and REASON in exc.value.detail
    assert legal.status == "waiting_human" and run.current_index == 0
    assert workflow.serialize_run(run)["rework"] is not None

    # 3. With a reply it goes through; the reply is on the request for the approver.
    out = post(rework_reply="Fixed: end date is now 2027-10-31.")
    assert legal.status == "done" and run.current_index == 1
    assert out["rework"] is None
    assert [(c["kind"], c["text"]) for c in out["comments"] if c["kind"] == "reply"] == [
        ("reply", "Fixed: end date is now 2027-10-31.")]
    assert advanced == [0, 1]  # the engine was told to carry on to the approval
