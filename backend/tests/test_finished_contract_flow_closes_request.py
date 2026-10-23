"""A request whose contract workflow has finished is finished work, and must stop counting.

The workflow engine left contract flows to "the contract lifecycle" to close the request, but nothing
did. The request stayed open for ever with its SLA clock running, so the intake list counted it as
open and as overdue (and the sweep escalated it) while its stage read "Complete".
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.core.models import AuditLog
from app.intake.models import IntakeRequest
from app.intake.service import IntakeService, posture
from app.organizations.models import Organization
from app.workflows.models import WorkflowRun
from app.workflows.service import (
    advance_run,
    close_finished_requests,
    close_request_when_contract_flow_done,
)


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


class World:
    def __init__(self, db, *, org=None):
        self.db = db
        self.org = org or Organization(id=new_uuid(), name="Flow Co", slug=f"flow-co-{uuid.uuid4().hex[:8]}")
        if org is None:
            db.add(self.org)
            db.flush()
        self.user = User(org_id=self.org.id, email=f"u-{uuid.uuid4().hex[:6]}@example.com", full_name="Requester",
                         hashed_password="h", status="active")
        db.add(self.user)
        db.flush()

    def contract(self):
        c = Contract(org_id=self.org.id, title="MSA", owner_user_id=self.user.id)
        self.db.add(c)
        self.db.flush()
        return c

    def request(self, *, status="open", contract=None, age_days=60):
        r = IntakeRequest(
            org_id=self.org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=self.user.id,
            type_label="New agreement Request", description="t", field_values={}, priority="Medium",
            status=status, stage="new", sla_hours=24, submitted_at=utcnow() - timedelta(days=age_days),
            created_by_user_id=self.user.id, updated_by_user_id=self.user.id,
            contract_id=contract.id if contract else None,
        )
        self.db.add(r)
        self.db.flush()
        return r

    def run(self, request, *, status, contract=None, steps=None, age=0):
        run = WorkflowRun(
            org_id=self.org.id, request_id=request.id, flow_name="MSA flow", flow_version=1,
            steps=steps if steps is not None else [], status=status, current_index=0,
            contract_id=contract.id if contract else None, created_by_user_id=self.user.id,
        )
        self.db.add(run)
        self.db.flush()
        if age:
            run.created_at = run.created_at - timedelta(minutes=age)
            self.db.flush()
        return run

    def audit(self, request, action):
        return self.db.scalars(select(AuditLog).where(
            AuditLog.resource_id == request.id, AuditLog.action == action)).all()


def close(w, run):
    return close_request_when_contract_flow_done(w.db, run=run, actor=w.user)


# --- the moment the run completes -------------------------------------------------

def test_finishing_a_contract_workflow_closes_the_request_and_stops_its_clock(db):
    w = World(db)
    contract = w.contract()
    request = w.request(contract=contract)
    assert posture(request) == "overdue"  # 60 days into a 24-hour window
    run = w.run(request, status="complete", contract=contract)

    assert close(w, run) is True

    assert (request.status, request.stage) == ("closed", "complete")
    assert request.closed_at is not None
    assert posture(request) == "on_track"
    assert len(w.audit(request, "intake.completed")) == 1


def test_an_escalated_request_is_closed_too(db):
    w = World(db)
    contract = w.contract()
    request = w.request(status="escalated", contract=contract)

    assert close(w, w.run(request, status="complete", contract=contract)) is True
    assert request.status == "closed"


@pytest.mark.parametrize("status", ["running", "waiting", "failed", "cancelled"])
def test_a_workflow_that_has_not_finished_leaves_the_request_open(db, status):
    w = World(db)
    contract = w.contract()
    request = w.request(contract=contract)

    assert close(w, w.run(request, status=status, contract=contract)) is False
    assert request.status == "open"


def test_a_flow_with_no_contract_is_left_to_the_approval_rule(db):
    """No-contract flows are finalised only when an approval really cleared; completion alone is not that."""
    w = World(db)
    request = w.request()

    assert close(w, w.run(request, status="complete")) is False
    assert request.status == "open"


def test_a_request_with_another_run_still_going_is_not_closed(db):
    w = World(db)
    contract = w.contract()
    request = w.request(contract=contract)
    finished = w.run(request, status="complete", contract=contract, age=10)
    w.run(request, status="waiting", contract=contract)

    assert close(w, finished) is False
    assert request.status == "open"


def test_an_already_approved_or_closed_request_is_left_alone(db):
    w = World(db)
    contract = w.contract()
    for status in ("approved", "closed"):
        request = w.request(status=status, contract=contract)
        assert close(w, w.run(request, status="complete", contract=contract)) is False
        assert request.status == status
        assert w.audit(request, "intake.completed") == []


def test_closing_twice_does_nothing_the_second_time(db):
    w = World(db)
    contract = w.contract()
    request = w.request(contract=contract)
    run = w.run(request, status="complete", contract=contract)

    assert close(w, run) is True
    assert close(w, run) is False
    assert len(w.audit(request, "intake.completed")) == 1


def test_the_engine_closes_the_request_when_it_walks_off_the_end_of_the_flow(db):
    w = World(db)
    contract = w.contract()
    request = w.request(contract=contract)
    run = w.run(request, status="running", contract=contract, steps=[])

    asyncio.run(advance_run(db, run=run, actor=w.user))

    assert run.status == "complete"
    assert request.status == "closed"


# --- requests that finished before this fix ---------------------------------------

def test_the_repair_closes_old_finished_requests_and_only_those(db):
    w = World(db)
    contract = w.contract()
    stuck = w.request(contract=contract)
    w.run(stuck, status="complete", contract=contract)
    working = w.request(contract=w.contract())
    w.run(working, status="waiting", contract=working.contract_id and db.get(Contract, working.contract_id))
    plain = w.request()

    assert close_finished_requests(db, org_id=w.org.id) == 1

    assert (stuck.status, working.status, plain.status) == ("closed", "open", "open")
    assert close_finished_requests(db, org_id=w.org.id) == 0


def test_the_repair_goes_by_the_latest_run(db):
    w = World(db)
    contract = w.contract()
    request = w.request(contract=contract)
    w.run(request, status="complete", contract=contract, age=30)
    w.run(request, status="waiting", contract=contract)

    assert close_finished_requests(db, org_id=w.org.id) == 0
    assert request.status == "open"


def test_the_repair_stays_inside_the_organisation_it_is_asked_about(db):
    mine = World(db)
    theirs = World(db)
    for w in (mine, theirs):
        contract = w.contract()
        w.run(w.request(contract=contract), status="complete", contract=contract)

    assert close_finished_requests(db, org_id=mine.org.id) == 1
    assert db.scalar(select(IntakeRequest.status).where(IntakeRequest.org_id == theirs.org.id)) == "open"


def test_the_sla_scan_closes_finished_requests_instead_of_escalating_them(db):
    w = World(db)
    contract = w.contract()
    request = w.request(contract=contract)
    w.run(request, status="complete", contract=contract)

    result = IntakeService(db).run_sla_sweep(org_id=w.org.id)

    assert result["completed"] == 1
    assert result["escalated"] == 0
    assert request.status == "closed"
