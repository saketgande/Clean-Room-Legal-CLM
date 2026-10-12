"""The request page must show who the approval is really with, and when it is due.

An Approval step in a run used to carry nothing about its approval: the page showed the
workflow step's planned team and "step start + SLA hours", while Delegate and Escalate
change the approval record. After a hand-over the Approvals page said one person and
one date and the request page another. ``serialize_run`` now reads the live approval.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approvals.models import ApprovalRequest
from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.intake.models import IntakeRequest
from app.organizations.models import Organization
from app.workflows.models import WorkflowRun, WorkflowStepRun
from app.workflows.service import serialize_run


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


def _org(db):
    org = Organization(id=new_uuid(), name="Run Co", slug=f"run-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db, org_id, label):
    u = User(org_id=org_id, email=f"{label}-{uuid.uuid4().hex[:6]}@example.com", full_name=label,
             hashed_password="h", status="active")
    db.add(u)
    db.flush()
    return u


def _run(db, org, requester, *, with_contract=True):
    contract = None
    if with_contract:
        contract = Contract(org_id=org.id, title="MSA", owner_user_id=requester.id)
        db.add(contract)
        db.flush()
    req = IntakeRequest(
        org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=requester.id,
        type_label="New agreement Request", description="t", field_values={}, priority="Medium",
        status="open", stage="new", submitted_at=utcnow(),
        created_by_user_id=requester.id, updated_by_user_id=requester.id,
    )
    db.add(req)
    db.flush()
    run = WorkflowRun(
        org_id=org.id, request_id=req.id, flow_name="Ladder", flow_version=1,
        steps=[{"id": "s0", "type": "approval", "name": "Finance approval", "config": {"sla_hours": 24}}],
        status="waiting", current_index=0, contract_id=contract.id if contract else None,
    )
    db.add(run)
    db.flush()
    db.add(WorkflowStepRun(org_id=org.id, flow_run_id=run.id, idx=0, step_type="approval",
                           step_name="Finance approval", status="waiting_job"))
    db.flush()
    return run, contract, req


def _approval(db, org, requester, *, contract=None, intake=None, holder=None, status="pending",
              due_in_days=7, step_order=1, batch=None, created_offset=0):
    row = ApprovalRequest(
        org_id=org.id, contract_id=contract.id if contract else None,
        intake_request_id=intake.id if intake else None, status=status,
        requested_by_user_id=requester.id, approver_user_id=holder.id if holder else None,
        step_order=step_order, mode="any",
        due_at=datetime.now(UTC) + timedelta(days=due_in_days),
        metadata_json={"submission_batch_id": batch or new_uuid()},
        created_by_user_id=requester.id, updated_by_user_id=requester.id,
    )
    db.add(row)
    db.flush()
    if created_offset:
        row.created_at = row.created_at + timedelta(seconds=created_offset)
        db.flush()
    return row


def _step(db, run):
    return serialize_run(db, run)["steps"][0]


def test_the_approval_step_shows_the_person_the_approval_is_now_with(db):
    org = _org(db)
    requester = _user(db, org.id, "requester")
    delegate = _user(db, org.id, "Casey Delegate")
    run, contract, _ = _run(db, org, requester)
    appr = _approval(db, org, requester, contract=contract, holder=delegate, due_in_days=7)

    shown = _step(db, run)["approval"]

    assert shown["approver_label"] == "Casey Delegate"
    assert shown["step_order"] == 1
    assert shown["overdue"] is False
    assert shown["due_at"].startswith(appr.due_at.isoformat()[:16])


def test_an_overdue_approval_says_so(db):
    org = _org(db)
    requester = _user(db, org.id, "requester")
    holder = _user(db, org.id, "holder")
    run, contract, _ = _run(db, org, requester)
    _approval(db, org, requester, contract=contract, holder=holder, due_in_days=-1)

    assert _step(db, run)["approval"]["overdue"] is True


def test_with_no_pending_approval_there_is_nothing_to_show(db):
    org = _org(db)
    requester = _user(db, org.id, "requester")
    holder = _user(db, org.id, "holder")
    run, contract, _ = _run(db, org, requester)
    _approval(db, org, requester, contract=contract, holder=holder, status="approved")

    assert _step(db, run)["approval"] is None


def test_an_old_rejected_chain_does_not_hide_the_new_one(db):
    org = _org(db)
    requester = _user(db, org.id, "requester")
    old, new = _user(db, org.id, "old"), _user(db, org.id, "new")
    run, contract, _ = _run(db, org, requester)
    _approval(db, org, requester, contract=contract, holder=old, status="rejected", created_offset=-60)
    _approval(db, org, requester, contract=contract, holder=new, status="pending")

    assert _step(db, run)["approval"]["approver_label"] == "new"


def test_a_request_with_no_contract_reads_the_approval_on_the_request(db):
    org = _org(db)
    requester = _user(db, org.id, "requester")
    holder = _user(db, org.id, "holder")
    run, _, req = _run(db, org, requester, with_contract=False)
    _approval(db, org, requester, intake=req, holder=holder)

    assert _step(db, run)["approval"]["approver_label"] == "holder"


def test_other_step_types_carry_no_approval(db):
    org = _org(db)
    requester = _user(db, org.id, "requester")
    run, contract, _ = _run(db, org, requester)
    run.steps = [{"id": "s0", "type": "human_task", "name": "Review", "config": {}}]
    sr = db.query(WorkflowStepRun).filter_by(flow_run_id=run.id).one()
    sr.step_type, sr.step_name, sr.status = "human_task", "Review", "waiting_human"
    db.flush()
    _approval(db, org, requester, contract=contract, holder=requester)

    assert _step(db, run)["approval"] is None
