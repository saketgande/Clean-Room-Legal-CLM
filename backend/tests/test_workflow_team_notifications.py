"""Every active member of a workflow step's assigned team gets emailed once
the step reaches them (SendGrid), scoped to the one place a real ``team_id``
is resolved today: ``_assign_step`` (human_task steps). Covers: fan-out to
active members only, per-recipient failure isolation, and the idempotency
guard against ``_execute_step`` re-entering an already-waiting step.

Two fixture styles, matching ``test_approval_chain_reroute.py``'s split:
``db`` (rollback-only) for tests that call ``_assign_step`` directly against
the fixture's own session; ``db_real_commit`` for tests that exercise
``_notify_workflow_step_team``, which deliberately opens its OWN
``SessionLocal()`` (mirroring every other Celery task in this codebase) and
so can only see rows the fixture has actually committed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.core.database import engine, new_uuid
from app.intake.models import IntakeRequest, IntakeTeam, IntakeTeamMember
from app.jobs import tasks as jobs_tasks
from app.notifications.models import Notification
from app.organizations.models import Organization
from app.workflows import service as wf_service
from app.workflows.models import WorkflowRun, WorkflowStepRun

_CLEANUP_MODELS: tuple[type, ...] = (
    Notification,
    WorkflowStepRun,
    WorkflowRun,
    IntakeTeamMember,
    IntakeTeam,
    IntakeRequest,
    User,
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


@pytest.fixture
def db_real_commit():
    """Real, per-commit transactions (like a production request-scoped
    session) so a row this fixture commits is visible to the separate
    ``SessionLocal()`` that ``_notify_workflow_step_team`` opens for itself.
    Cleans up every row it created, by org, at teardown."""
    connection = engine.connect()
    session = Session(bind=connection)
    org_ids: list[str] = []
    try:
        yield session, org_ids
    finally:
        session.rollback()
        if org_ids:
            for model in _CLEANUP_MODELS:
                session.query(model).filter(model.org_id.in_(org_ids)).delete(synchronize_session=False)
            session.query(Organization).filter(Organization.id.in_(org_ids)).delete(synchronize_session=False)
            session.commit()
        session.close()
        connection.close()


def _make_org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="Notif Co", slug=f"notif-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _make_user(db: Session, *, org_id: str, label: str) -> User:
    user = User(
        org_id=org_id,
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=label,
        hashed_password="hash",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def _make_team(db: Session, *, org_id: str) -> IntakeTeam:
    team = IntakeTeam(org_id=org_id, key=f"team-{uuid.uuid4().hex[:6]}", name="IP Team")
    db.add(team)
    db.flush()
    return team


def _make_member(db: Session, *, org_id: str, team_id: str, user_id: str, active: bool = True) -> IntakeTeamMember:
    member = IntakeTeamMember(org_id=org_id, team_id=team_id, user_id=user_id, active=active)
    db.add(member)
    db.flush()
    return member


def _make_request(db: Session, *, org_id: str, requester_id: str) -> IntakeRequest:
    from app.core.database import utcnow

    req = IntakeRequest(
        org_id=org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form",
        requester_user_id=requester_id, type_label="General request", description="test",
        field_values={}, priority="Medium", status="open", stage="new",
        submitted_at=utcnow(), created_by_user_id=requester_id, updated_by_user_id=requester_id,
    )
    db.add(req)
    db.flush()
    return req


def _make_run_and_step(db: Session, *, org_id: str, request_id: str) -> tuple[WorkflowRun, WorkflowStepRun]:
    run = WorkflowRun(
        org_id=org_id, request_id=request_id, flow_name="Test flow", flow_version=1,
        steps=[{"id": "s1", "type": "human_task", "name": "Review", "config": {}}],
        status="running", current_index=0,
    )
    db.add(run)
    db.flush()
    sr = WorkflowStepRun(
        org_id=org_id, flow_run_id=run.id, idx=0, step_type="human_task", step_name="Review",
        status="waiting_human",
    )
    db.add(sr)
    db.flush()
    return run, sr


async def test_notify_sends_only_to_active_team_members(db_real_commit):
    db, org_ids = db_real_commit
    org = _make_org(db)
    org_ids.append(org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    active_member = _make_user(db, org_id=org.id, label="active-member")
    inactive_member = _make_user(db, org_id=org.id, label="inactive-member")
    team = _make_team(db, org_id=org.id)
    _make_member(db, org_id=org.id, team_id=team.id, user_id=active_member.id, active=True)
    _make_member(db, org_id=org.id, team_id=team.id, user_id=inactive_member.id, active=False)
    request = _make_request(db, org_id=org.id, requester_id=requester.id)
    _run, sr = _make_run_and_step(db, org_id=org.id, request_id=request.id)
    sr.team_id = team.id
    db.commit()

    result = await jobs_tasks._notify_workflow_step_team(sr.id)

    assert result["sent"] == 1
    assert result["failed"] == 0
    notifications = db.query(Notification).filter(Notification.org_id == org.id).all()
    assert len(notifications) == 1
    assert notifications[0].user_id == active_member.id
    assert notifications[0].status == "sent"
    assert notifications[0].channel == "email"
    assert notifications[0].event_type == "workflow.step_assigned"


async def test_notify_isolates_a_per_recipient_failure(db_real_commit, monkeypatch):
    db, org_ids = db_real_commit
    org = _make_org(db)
    org_ids.append(org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    good_member = _make_user(db, org_id=org.id, label="good-member")
    bad_member = _make_user(db, org_id=org.id, label="bad-member")
    team = _make_team(db, org_id=org.id)
    _make_member(db, org_id=org.id, team_id=team.id, user_id=good_member.id)
    _make_member(db, org_id=org.id, team_id=team.id, user_id=bad_member.id)
    request = _make_request(db, org_id=org.id, requester_id=requester.id)
    _run, sr = _make_run_and_step(db, org_id=org.id, request_id=request.id)
    sr.team_id = team.id
    db.commit()

    from app.integrations.sendgrid import EmailResult, sendgrid_client

    async def _flaky_send(*, to: str, subject: str, html: str):
        if to == bad_member.email:
            raise RuntimeError("simulated provider outage")
        return EmailResult(None, "sent", {})

    monkeypatch.setattr(sendgrid_client, "send_email", _flaky_send)

    result = await jobs_tasks._notify_workflow_step_team(sr.id)

    assert result["sent"] == 1
    assert result["failed"] == 1
    statuses = {
        n.user_id: n.status
        for n in db.query(Notification).filter(Notification.org_id == org.id).all()
    }
    assert statuses[good_member.id] == "sent"
    assert statuses[bad_member.id] == "failed"


def test_assign_step_dispatches_once_per_step_run(db: Session, monkeypatch):
    org = _make_org(db)
    requester = _make_user(db, org_id=org.id, label="requester")
    member = _make_user(db, org_id=org.id, label="member")
    team = _make_team(db, org_id=org.id)
    _make_member(db, org_id=org.id, team_id=team.id, user_id=member.id)
    request = _make_request(db, org_id=org.id, requester_id=requester.id)
    run, sr = _make_run_and_step(db, org_id=org.id, request_id=request.id)

    calls: list[tuple[list, dict]] = []
    monkeypatch.setattr(
        "app.jobs.tasks.notify_workflow_step_team.apply_async",
        lambda args=None, **kwargs: calls.append((args, kwargs)),
    )

    wf_service._assign_step(db, run=run, sr=sr, cfg={"team_id": team.id})
    assert len(calls) == 1
    assert calls[0][0] == [sr.id]
    assert calls[0][1].get("countdown", 0) > 0  # must wait for the caller's commit to land
    assert (sr.result or {}).get("notified_at") is not None

    # A second call against the same step_run (e.g. a poll re-entering
    # waiting_human) must not dispatch again.
    wf_service._assign_step(db, run=run, sr=sr, cfg={"team_id": team.id})
    assert len(calls) == 1


async def test_notify_workflow_step_team_handles_a_step_with_no_team(db_real_commit):
    """A step whose ``_assign_step`` never resolved a team (no config,
    balancer found nobody) must short-circuit cleanly, not error."""
    db, org_ids = db_real_commit
    org = _make_org(db)
    org_ids.append(org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    request = _make_request(db, org_id=org.id, requester_id=requester.id)
    _run, sr = _make_run_and_step(db, org_id=org.id, request_id=request.id)
    db.commit()

    result = await jobs_tasks._notify_workflow_step_team(sr.id)
    assert result["reason"] == "no_team"
