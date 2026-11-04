"""A trademark clearance case, and an examination / opposition response, are worked in Trademarks, so the
Legal Intake request tables must not list them, while every other request (including one with no answers
at all) still appears there."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.intake import service as intake_service
from app.intake.models import IntakeRequest
from app.intake.service import IntakeService
from app.trademarks import clearance_service
from app.trademarks.clearance_service import TrademarkClearanceService
from app.trademarks.models import Trademark


@pytest.fixture
def org(db_session):
    org_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    owner = User(
        id=str(uuid.uuid4()), org_id=org_id, email=f"owner-{suffix}@lists.test", full_name="Owner",
        hashed_password="x", status="active",
    )
    db_session.add(owner)
    db_session.flush()

    def request(ref: str, field_values) -> IntakeRequest:
        row = IntakeRequest(
            id=str(uuid.uuid4()), org_id=org_id, ref=f"{ref}-{suffix}", requester_user_id=owner.id,
            type_label="Trademark", description="x", field_values=field_values, status="in_review",
            submitted_at=datetime.now(UTC),
        )
        db_session.add(row)
        return row

    rows = {
        "clearance": request("CLR", {"mark": "ZQ", "tm_clearance": True}),
        "response": request("RSP", {"mark": "ZQ", "tm_response": True, "tm_response_for": "some-mark"}),
        "ordinary": request("ORD", {"mark": "ZQ"}),
        "no_answers": request("NIL", None),
        "other_flag": request("OTH", {"tm_clearance": False}),
    }
    db_session.flush()
    trademark = Trademark(
        id=str(uuid.uuid4()), org_id=org_id, name="ZQ", jurisdiction="IN", status="draft",
        source_intake_request_id=rows["clearance"].id,
    )
    db_session.add(trademark)
    db_session.commit()
    yield type("Org", (), {"id": org_id, "owner": owner, "rows": rows, "trademark": trademark})

    db_session.rollback()
    db_session.execute(delete(Trademark).where(Trademark.org_id == org_id))
    db_session.execute(delete(IntakeRequest).where(IntakeRequest.org_id == org_id))
    db_session.execute(delete(User).where(User.org_id == org_id))
    db_session.commit()


def test_the_request_lists_leave_out_clearance_cases_and_keep_everything_else(db_session, org):
    refs = {r["id"] for r in IntakeService(db_session).list_requests(user=org.owner)}
    mine = {r["id"] for r in IntakeService(db_session).list_requests(user=org.owner, mine=True)}
    wanted = {org.rows[k].id for k in ("ordinary", "no_answers", "other_flag")}

    assert refs == wanted and mine == wanted
    assert {org.rows["clearance"].id, org.rows["response"].id}.isdisjoint(refs | mine)


def test_the_marker_matches_the_one_the_clearance_workflow_is_chosen_by():
    assert intake_service.CLEARANCE_MARKER_FIELD == clearance_service.CLEARANCE_MARKER_FIELD
    assert intake_service.RESPONSE_MARKER_FIELD == clearance_service.RESPONSE_MARKER_FIELD


def test_a_request_finds_the_trademark_it_carries(db_session, org):
    service = TrademarkClearanceService(db_session)
    found = service.trademark_for_request(org_id=org.id, request_id=org.rows["clearance"].id)
    assert found.id == org.trademark.id


def test_a_request_with_no_trademark_or_in_another_org_is_a_404(db_session, org):
    service = TrademarkClearanceService(db_session)
    with pytest.raises(HTTPException) as no_mark:
        service.trademark_for_request(org_id=org.id, request_id=org.rows["ordinary"].id)
    with pytest.raises(HTTPException) as other_org:
        service.trademark_for_request(org_id=str(uuid.uuid4()), request_id=org.rows["clearance"].id)
    assert no_mark.value.status_code == other_org.value.status_code == 404
