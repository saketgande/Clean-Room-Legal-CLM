"""A workflow step's assignee must be able to read the contract it's on.

Regression: a contract sitting at a ``human_task`` step (e.g. "Legal Review")
only ever records its reviewer on ``WorkflowStepRun.assignee_user_id`` — no
grant, no ``ApprovalRequest`` row (that only exists for ``approval``-type
steps, already covered by ``_is_pending_approver``). Without this check the
person a workflow assigns to review a contract got 404s on every one of its
endpoints (GET the contract, /risk, /parties, /edits, /deviations,
/workflows/runs/by-contract) — they simply had no path to the access grant.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contracts.access import user_can_access_contract
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.intake.models import IntakeRequest
from app.organizations.models import Organization
from app.workflows.models import WorkflowRun, WorkflowStepRun


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


def _org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="Access Co", slug=f"access-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db: Session, org_id: str, label: str) -> User:
    u = User(org_id=org_id, email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", full_name=label,
             hashed_password="h", status="active")
    db.add(u)
    db.flush()
    return u


def _contract(db: Session, *, org_id: str, owner_id: str) -> Contract:
    c = Contract(org_id=org_id, title="MSA", owner_user_id=owner_id)
    db.add(c)
    db.flush()
    return c


def _run_with_human_task(db: Session, *, org_id: str, requester_id: str, contract_id: str,
                         assignee_user_id: str | None) -> WorkflowRun:
    req = IntakeRequest(
        org_id=org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=requester_id,
        type_label="New agreement Request", description="test", field_values={}, priority="Medium",
        status="open", stage="new", submitted_at=utcnow(),
        created_by_user_id=requester_id, updated_by_user_id=requester_id,
    )
    db.add(req)
    db.flush()
    run = WorkflowRun(
        org_id=org_id, request_id=req.id, flow_name="Test Ladder", flow_version=1,
        steps=[{"id": "s0", "type": "human_task", "name": "Review", "config": {}}],
        status="waiting", current_index=0, contract_id=contract_id,
    )
    db.add(run)
    db.flush()
    sr = WorkflowStepRun(
        org_id=org_id, flow_run_id=run.id, idx=0, step_type="human_task", step_name="Review",
        status="waiting_human", assignee_user_id=assignee_user_id,
    )
    db.add(sr)
    db.flush()
    return run


def test_human_task_assignee_can_read_the_contract(db: Session):
    org = _org(db)
    owner = _user(db, org.id, "owner")
    reviewer = _user(db, org.id, "reviewer")
    contract = _contract(db, org_id=org.id, owner_id=owner.id)
    _run_with_human_task(db, org_id=org.id, requester_id=owner.id, contract_id=contract.id,
                         assignee_user_id=reviewer.id)

    assert user_can_access_contract(db, contract=contract, user=reviewer) is True


def test_unrelated_staff_member_still_cannot_read_it(db: Session):
    org = _org(db)
    owner = _user(db, org.id, "owner")
    reviewer = _user(db, org.id, "reviewer")
    bystander = _user(db, org.id, "bystander")
    contract = _contract(db, org_id=org.id, owner_id=owner.id)
    _run_with_human_task(db, org_id=org.id, requester_id=owner.id, contract_id=contract.id,
                         assignee_user_id=reviewer.id)

    assert user_can_access_contract(db, contract=contract, user=bystander) is False
