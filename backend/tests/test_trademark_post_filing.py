"""Post-filing tracking and the examination / opposition response.

The LTE logs updates and responses on a FILED mark; the LTE Lead, TM Legal SPOC, TM Head and the Business
are told; a response is its own request and workflow run, kept out of the Legal Intake tables.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.enums import TrademarkStatus
from app.core.models import ResourceTimelineEvent
from app.intake.models import IntakeRequest, IntakeTeam, IntakeTeamMember
from app.notifications.models import Notification
from app.trademarks.clearance_service import (
    RESPONSE_FOR_FIELD,
    RESPONSE_MARKER_FIELD,
    TrademarkClearanceService,
)
from app.trademarks.models import Trademark
from app.trademarks.schemas import PostFilingUpdateRequest, ResponseRequest
from app.workflows import stages
from app.workflows.builtin import BUILTIN_FLOWS
from app.workflows.models import WorkflowRun, WorkflowStepRun
from app.workflows.service import _matches


@pytest.fixture
def filed(db_session):
    org_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]

    def make_user(label: str) -> User:
        user = User(
            id=str(uuid.uuid4()), org_id=org_id, email=f"{label}-{suffix}@postfiling.test", full_name=label.title(),
            hashed_password="x", status="active",
        )
        db_session.add(user)
        return user

    people = {label: make_user(label) for label in ("business", "lte", "legal", "head", "outsider")}
    db_session.flush()
    teams = {}
    for key, member in (("lte", "lte"), ("tm_legal", "legal"), ("tm_head", "head")):
        team = IntakeTeam(id=str(uuid.uuid4()), org_id=org_id, key=key, name=key, active=True)
        db_session.add(team)
        db_session.flush()
        db_session.add(IntakeTeamMember(id=str(uuid.uuid4()), org_id=org_id, team_id=team.id, user_id=people[member].id, active=True))
        teams[key] = team

    request = IntakeRequest(
        id=str(uuid.uuid4()), org_id=org_id, ref=f"REQ-{suffix}", requester_user_id=people["business"].id,
        type_label="Trademark", description="Trademark clearance", field_values={"mark": "ZQ", "tm_clearance": True},
        submitted_at=datetime.now(UTC),
    )
    db_session.add(request)
    db_session.flush()
    trademark = Trademark(
        id=str(uuid.uuid4()), org_id=org_id, name="ZQ", jurisdiction="IN", status="filed",
        filed_on=datetime.now(UTC), application_number="TM-1", source_intake_request_id=request.id,
    )
    unfiled = Trademark(id=str(uuid.uuid4()), org_id=org_id, name="DRAFTY", jurisdiction="IN", status="draft")
    db_session.add_all([trademark, unfiled])
    db_session.commit()

    yield SimpleNamespace(org_id=org_id, people=people, teams=teams, request=request, trademark=trademark, unfiled=unfiled)

    db_session.rollback()
    for model in (Notification, ResourceTimelineEvent, WorkflowStepRun, WorkflowRun, Trademark):
        db_session.execute(delete(model).where(model.org_id == org_id))
    db_session.execute(delete(IntakeRequest).where(IntakeRequest.org_id == org_id))
    db_session.execute(delete(IntakeTeamMember).where(IntakeTeamMember.org_id == org_id))
    db_session.execute(delete(IntakeTeam).where(IntakeTeam.org_id == org_id))
    db_session.execute(delete(User).where(User.org_id == org_id))
    db_session.commit()


def _told(db_session, org_id: str, event_type: str) -> set[str]:
    return set(
        db_session.scalars(
            select(Notification.user_id).where(Notification.org_id == org_id, Notification.event_type == event_type)
        ).all()
    )


def _fake_open_flow_request(service, db_session, org_id):
    """Stands in for the intake engine (AI triage, workflow start): files the request row the way it would."""

    def open_flow_request(*, user, marker, subject, description, answers, department=None):
        row = IntakeRequest(
            id=str(uuid.uuid4()), org_id=org_id, ref=f"RSP-{uuid.uuid4().hex[:6]}", requester_user_id=user.id,
            type_label="Trademark", description=description, field_values=answers, submitted_at=datetime.now(UTC),
            subject=subject,
        )
        db_session.add(row)
        db_session.commit()
        return {"id": row.id, "ref": row.ref}

    service.open_flow_request = open_flow_request


# --- the update -------------------------------------------------------------


def test_the_lte_logs_an_update_and_the_lead_legal_spoc_and_business_are_told(db_session, filed):
    service = TrademarkClearanceService(db_session)
    due = datetime.now(UTC) + timedelta(days=30)

    result = service.log_post_filing_update(
        user=filed.people["lte"],
        trademark_id=filed.trademark.id,
        payload=PostFilingUpdateRequest(
            note="Examination report expected", stage=TrademarkStatus.EXAMINATION, next_action_on=due, next_action_note="Reply due"
        ),
    )

    assert result.status == "examination"
    assert (result.next_action_on, result.next_action_note) == (due, "Reply due")
    titles = [e.title for e in db_session.scalars(select(ResourceTimelineEvent).where(ResourceTimelineEvent.org_id == filed.org_id)).all()]
    assert any(t.startswith("Post-filing update: Examination report expected") and "(now Examination)" in t for t in titles)
    told = _told(db_session, filed.org_id, "trademark.post_filing_update")
    # The Business and TM Legal are told (the acting LTE is not told about their own update).
    assert told == {filed.people["business"].id, filed.people["legal"].id}


def test_only_the_lte_or_an_admin_can_log_and_only_on_a_filed_mark(db_session, filed):
    service = TrademarkClearanceService(db_session)
    update = PostFilingUpdateRequest(note="x")

    with pytest.raises(HTTPException) as outsider:
        service.log_post_filing_update(user=filed.people["outsider"], trademark_id=filed.trademark.id, payload=update)
    with pytest.raises(HTTPException) as unfiled:
        service.log_post_filing_update(user=filed.people["lte"], trademark_id=filed.unfiled.id, payload=update)
    with pytest.raises(HTTPException) as bad_stage:
        service.log_post_filing_update(
            user=filed.people["lte"], trademark_id=filed.trademark.id,
            payload=PostFilingUpdateRequest(note="x", stage=TrademarkStatus.DRAFT),
        )

    assert (outsider.value.status_code, unfiled.value.status_code, bad_stage.value.status_code) == (403, 409, 422)


def test_a_later_update_can_clear_the_next_date(db_session, filed):
    service = TrademarkClearanceService(db_session)
    when = datetime.now(UTC) + timedelta(days=10)
    service.log_post_filing_update(
        user=filed.people["lte"], trademark_id=filed.trademark.id,
        payload=PostFilingUpdateRequest(note="Hearing set", next_action_on=when, next_action_note="Hearing"),
    )

    cleared = service.log_post_filing_update(
        user=filed.people["lte"], trademark_id=filed.trademark.id,
        payload=PostFilingUpdateRequest(note="Hearing held", next_action_on=None),
    )

    assert (cleared.next_action_on, cleared.next_action_note) == (None, None)


# --- the response -----------------------------------------------------------


def test_logging_an_examination_report_opens_a_response_and_tells_the_tm_head_too(db_session, filed):
    service = TrademarkClearanceService(db_session)
    _fake_open_flow_request(service, db_session, filed.org_id)
    due = datetime.now(UTC) + timedelta(days=30)

    result = service.open_response(
        user=filed.people["lte"], trademark_id=filed.trademark.id,
        payload=ResponseRequest(kind="examination", summary="Objection on class 5", reference="EX-9", due_on=due),
    )

    assert result.status == "examination" and result.next_action_on == due
    assert result.next_action_note == "Response to examination report due"
    response = db_session.scalar(
        select(IntakeRequest).where(IntakeRequest.org_id == filed.org_id, IntakeRequest.field_values[RESPONSE_MARKER_FIELD].as_string() == "true")
    )
    assert response.field_values[RESPONSE_FOR_FIELD] == filed.trademark.id
    assert response.subject == "Examination report response: ZQ"
    assert _told(db_session, filed.org_id, "trademark.response_opened") == {
        filed.people["business"].id, filed.people["legal"].id, filed.people["head"].id,
    }


def test_an_opposition_marks_the_mark_opposed_and_a_mark_can_have_several_responses(db_session, filed):
    service = TrademarkClearanceService(db_session)
    _fake_open_flow_request(service, db_session, filed.org_id)
    for kind in ("examination", "opposition"):
        result = service.open_response(
            user=filed.people["lte"], trademark_id=filed.trademark.id, payload=ResponseRequest(kind=kind, summary="x")
        )

    assert result.status == "opposed"
    listed = service.responses_for(org_id=filed.org_id, trademark_ids=[filed.trademark.id])[filed.trademark.id]
    assert {case.title for case in listed} == {"Examination report response: ZQ", "Opposition response: ZQ"}


def test_a_response_needs_the_lte_and_a_filed_mark(db_session, filed):
    service = TrademarkClearanceService(db_session)
    payload = ResponseRequest(kind="opposition", summary="x")

    with pytest.raises(HTTPException) as outsider:
        service.open_response(user=filed.people["outsider"], trademark_id=filed.trademark.id, payload=payload)
    with pytest.raises(HTTPException) as unfiled:
        service.open_response(user=filed.people["lte"], trademark_id=filed.unfiled.id, payload=payload)

    assert (outsider.value.status_code, unfiled.value.status_code) == (403, 409)


def test_a_response_request_leads_back_to_its_mark_and_shows_in_its_audit_log_and_awaiting_list(db_session, filed):
    service = TrademarkClearanceService(db_session)
    _fake_open_flow_request(service, db_session, filed.org_id)
    service.open_response(
        user=filed.people["lte"], trademark_id=filed.trademark.id, payload=ResponseRequest(kind="examination", summary="x")
    )
    response = db_session.scalar(
        select(IntakeRequest).where(IntakeRequest.org_id == filed.org_id, IntakeRequest.field_values[RESPONSE_MARKER_FIELD].as_string() == "true")
    )
    # The response's own workflow: one live run waiting on the TM Legal team's step.
    run = WorkflowRun(
        id=str(uuid.uuid4()), org_id=filed.org_id, request_id=response.id, flow_name="Response", flow_version=1,
        steps=[{"name": "Coordinate the response"}], status="waiting", current_index=0, context={},
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        WorkflowStepRun(
            id=str(uuid.uuid4()), org_id=filed.org_id, flow_run_id=run.id, idx=0, step_type="human_task",
            step_name="Coordinate the response", status="waiting_human", team_id=filed.teams["tm_legal"].id,
        )
    )
    from app.core.audit import write_timeline_event

    write_timeline_event(
        db_session, org_id=filed.org_id, resource_type="intake_request", resource_id=response.id,
        event_type="flow.started", title="Workflow started: Response", actor_user_id=filed.people["lte"].id,
    )
    db_session.commit()

    assert service.trademark_for_request(org_id=filed.org_id, request_id=response.id).id == filed.trademark.id
    assert "Workflow started: Response" in [e.title for e in service.activity(org_id=filed.org_id, trademark_id=filed.trademark.id)]
    waiting = service.awaiting_action(user=filed.people["legal"])
    assert [(a.trademark_id, a.step_name, a.request_id) for a in waiting] == [(filed.trademark.id, "Coordinate the response", response.id)]
    assert service.awaiting_action(user=filed.people["outsider"]) == []


# --- the flow definition ----------------------------------------------------


def _response_flow() -> dict:
    return next(f for f in BUILTIN_FLOWS if f["name"] == "Trademark Examination / Opposition Response")


def test_the_response_flow_is_chosen_only_by_its_marker_and_never_the_clearances():
    flow = _response_flow()

    def request(**values):
        return SimpleNamespace(type_label="Trademark", description="", priority="Medium", department=None, field_values=values)

    assert _matches(flow["criteria"], request(tm_response=True))
    assert not _matches(flow["criteria"], request(tm_clearance=True))
    assert not _matches(flow["criteria"], request(mark="ZQ"))
    clearance = next(f for f in BUILTIN_FLOWS if f["name"] == "Trademark Clearance & Filing")
    assert not _matches(clearance["criteria"], request(tm_response=True))


def test_the_response_flow_routes_lte_lead_then_tm_legal_spoc_then_lte_lead_and_follows_the_stage_rules():
    steps = _response_flow()["steps"]

    assert [(s["name"], s["config"]["team"], s["config"]["assign_by"]) for s in steps] == [
        ("Decide response approach", "lte", "Auto — team head"),
        ("Coordinate the response", "tm_legal", "Auto — team head"),
        ("Record and file the response", "lte", "Auto — team head"),
    ]
    assert stages.problems(steps) == []
