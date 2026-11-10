"""Filing and changing a SOW request's deliverables leaves a record, and changes only what it should.

Failures this guards:
* a SOW filing whose audit row does not hold the rows, pricing, window, value and currency it was filed with
  (FR-38);
* rows changed by an update, or shares added and removed by a pricing change (FR-43), with no audit row and no
  history entry showing who, and the rows before and after;
* the record written on a second session, or after the commit, instead of the request's own transaction
  (CLAUDE.md, audit lock);
* a refused filing or update that leaves rows, an audit row or a history entry behind;
* an older request (free-text deliverables only) refused for an unrelated change, or its free text altered
  (FR-31, FR-32, AC-34, AC-35);
* a table refused with different words on filing than on update (AC-19);
* an owner's name that matches a user creating a notification, a user or any access (FR-13, AC-16);
* another organisation's request reachable by an update (AC-43).

Runs against Postgres inside one rolled-back transaction.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import engine, new_uuid, utcnow
from app.core.models import AuditLog, ResourceTimelineEvent
from app.intake import service
from app.intake.models import IntakeRequest
from app.intake.schemas import RequestCreate, RequestUpdate
from app.notifications.models import Notification
from app.organizations.models import Organization
from tests.test_sow_deliverable_rows import CASES, OLD_TEXT, ROWS, older, sow

SHARES = ("40", "35", "25")


@pytest.fixture
def db():
    """Commits inside filing and updating are savepoints of one outer transaction, so they behave as in
    production and still leave nothing behind."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _user(db: Session, name: str = "Requester", org: Organization | None = None) -> User:
    if org is None:
        org = Organization(id=new_uuid(), name="sow rows", slug=f"sow-rows-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
    user = User(org_id=org.id, email=f"u-{uuid.uuid4().hex[:8]}@example.com", full_name=name,
                hashed_password="hash", status="active")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def me(db):
    return _user(db)


def _file(db: Session, user: User, **over) -> dict:
    return service.create_request(
        db, actor=user, defer_triage=True,
        payload=RequestCreate(type_label="Statement of Work Request", description="sow rows",
                              field_values=sow(**over)))


def _update(db: Session, user: User, request_id: str, **payload) -> dict:
    return service.update_request(db, actor=user, request_id=request_id, payload=RequestUpdate(**payload))


def _filed_before_the_table(db: Session, user: User, **over) -> IntakeRequest:
    """A SOW request as it was filed before this feature: free-text deliverables and no rows."""
    row = IntakeRequest(
        org_id=user.org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=user.id,
        type_label="Statement of Work Request", description="sow rows", field_values=older(**over),
        priority="Medium", status="open", stage="new", submitted_at=utcnow(),
        created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(row)
    db.flush()
    return row


def _audit(db: Session, request_id: str, action: str) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.resource_id == request_id, AuditLog.action == action)
                           .order_by(AuditLog.created_at, AuditLog.id)))


def _history(db: Session, request_id: str, event: str) -> list[ResourceTimelineEvent]:
    return list(db.scalars(select(ResourceTimelineEvent).where(
        ResourceTimelineEvent.resource_type == "intake_request", ResourceTimelineEvent.resource_id == request_id,
        ResourceTimelineEvent.event_type == event)))


def _with_shares(rows, shares=SHARES):
    return [{**r, "share": s} for r, s in zip(rows, shares, strict=True)]


def _refused(call) -> str:
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 422
    return exc.value.detail


# --- filing -----------------------------------------------------------------------------------------

def test_filing_records_the_rows_and_what_they_were_checked_against(db, me):
    """FR-38: the filing's audit row holds the rows as stored, with the pricing, window, value and currency."""
    out = _file(db, me, pricing="Milestones", deliverable_rows=_with_shares(ROWS))
    stored = out["field_values"]["deliverable_rows"]
    assert [r["share"] for r in stored] == ["40.00", "35.00", "25.00"]
    (row,) = _audit(db, out["id"], "intake.created")
    assert row.after["deliverable_rows"] == stored
    assert {k: row.after[k] for k in ("pricing", "services_start", "services_end", "value", "currency")} == {
        "pricing": "Milestones", "services_start": "2026-11-01", "services_end": "2027-03-31",
        "value": 4500000, "currency": "INR"}
    assert row.after["ref"] == out["ref"]


def test_an_empty_row_and_untidy_cells_are_stored_clean(db, me):
    rows = [{**ROWS[0], "name": "  Migration \n plan "}, {"name": "", "due": "", "their_owner": "", "our_owner": ""},
            ROWS[1]]
    out = _file(db, me, deliverable_rows=rows)
    assert out["field_values"]["deliverable_rows"] == [{**ROWS[0]}, ROWS[1]]


def test_a_request_that_is_not_a_sow_keeps_its_audit_row_as_it_was(db, me):
    out = service.create_request(db, actor=me, defer_triage=True,
                                 payload=RequestCreate(type_label="Question", description="sow rows"))
    (row,) = _audit(db, out["id"], "intake.created")
    assert set(row.after) == {"ref", "type", "priority"}


def test_a_refused_filing_files_and_records_nothing(db, me):
    detail = _refused(lambda: _file(db, me, deliverable_rows=[ROWS[0], {**ROWS[1], "due": "2031-01-01"}]))
    assert detail.startswith('"Deliverables", row 2: "Due date" must be between')
    assert db.scalar(select(func.count()).select_from(IntakeRequest).where(IntakeRequest.org_id == me.org_id)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.org_id == me.org_id)) == 0


def test_an_owner_who_shares_a_users_name_gives_that_user_nothing(db, me):
    """FR-13, AC-16: owners are typed text. No lookup, notification, account or access follows from one."""
    priya = _user(db, "Priya Nair", org=db.get(Organization, me.org_id))
    people = db.scalar(select(func.count()).select_from(User).where(User.org_id == me.org_id))
    out = _file(db, me)  # ROWS names Priya Nair as their owner of two deliverables
    assert db.scalar(select(func.count()).select_from(User).where(User.org_id == me.org_id)) == people
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == priya.id)) == 0
    with pytest.raises(HTTPException) as exc:  # and the named user cannot read the request through the name
        service.get_request(db, user=priya, request_id=out["id"])
    assert exc.value.status_code == 404


# --- updating ----------------------------------------------------------------------------------------

def test_changing_three_rows_to_two_records_who_and_both_lists(db, me):
    """AC-41: the audit row and the request's history hold the rows before and after, in one transaction."""
    out = _file(db, me)
    kept = [ROWS[0], ROWS[2]]
    updated = _update(db, me, out["id"], field_values=sow(deliverable_rows=kept))
    assert updated["field_values"]["deliverable_rows"] == kept
    (row,) = _audit(db, out["id"], "intake.deliverables_changed")
    assert row.actor_user_id == me.id and row.org_id == me.org_id
    assert row.before == {"deliverable_rows": ROWS, "pricing": "Fixed fee"}
    assert row.after == {"deliverable_rows": kept, "pricing": "Fixed fee"}
    (event,) = _history(db, out["id"], "intake.deliverables_changed")
    assert event.title == "Deliverables changed" and event.created_by_user_id == me.id
    assert event.details == {"before": row.before, "after": row.after}


def test_changes_that_leave_the_rows_alone_record_nothing_about_them(db, me):
    out = _file(db, me)
    _update(db, me, out["id"], priority="High")
    _update(db, me, out["id"], field_values=sow(department="Finance"))
    assert _audit(db, out["id"], "intake.deliverables_changed") == []
    assert _history(db, out["id"], "intake.deliverables_changed") == []
    assert service.get_request(db, user=me, request_id=out["id"]).field_values["department"] == "Finance"


def test_the_record_is_written_when_the_update_also_moves_the_stage(db, me):
    """Both branches of an update commit the same way, so the record must not depend on the stage."""
    out = _file(db, me)
    _update(db, me, out["id"], stage="assigned", field_values=sow(deliverable_rows=ROWS[:1]))
    assert len(_audit(db, out["id"], "intake.deliverables_changed")) == 1
    assert len(_history(db, out["id"], "intake.deliverables_changed")) == 1


def test_a_pricing_change_away_from_milestones_removes_the_shares_and_says_so(db, me):
    """AC-46: shares are filed only under Milestones; the update removes them and the audit shows both lists."""
    out = _file(db, me, pricing="Milestones", deliverable_rows=_with_shares(ROWS))
    with_shares = out["field_values"]["deliverable_rows"]
    updated = _update(db, me, out["id"], field_values=sow(pricing="Time and materials",
                                                          deliverable_rows=with_shares))
    assert updated["field_values"]["deliverable_rows"] == ROWS
    (row,) = _audit(db, out["id"], "intake.deliverables_changed")
    assert row.before == {"deliverable_rows": with_shares, "pricing": "Milestones"}
    assert row.after == {"deliverable_rows": ROWS, "pricing": "Time and materials"}


def test_a_pricing_change_to_milestones_needs_a_share_on_every_row(db, me):
    out = _file(db, me)
    detail = _refused(lambda: _update(db, me, out["id"], field_values=sow(pricing="Milestones")))
    assert detail == '"Deliverables", row 1: "Share of the fee (%)" is required.'
    assert _audit(db, out["id"], "intake.deliverables_changed") == []
    done = _update(db, me, out["id"], field_values=sow(pricing="Milestones", deliverable_rows=_with_shares(ROWS)))
    assert [r["share"] for r in done["field_values"]["deliverable_rows"]] == ["40.00", "35.00", "25.00"]
    (row,) = _audit(db, out["id"], "intake.deliverables_changed")
    assert row.after["pricing"] == "Milestones"


def test_a_refused_update_changes_nothing_and_records_nothing(db, me):
    out = _file(db, me)
    before = service.get_request(db, user=me, request_id=out["id"]).field_values
    _refused(lambda: _update(db, me, out["id"], field_values=sow(deliverable_rows=[{**ROWS[0], "name": ""}])))
    assert service.get_request(db, user=me, request_id=out["id"]).field_values == before
    assert _audit(db, out["id"], "intake.deliverables_changed") == []
    assert _history(db, out["id"], "intake.deliverables_changed") == []


@pytest.mark.parametrize("case_id", ["m5-no-due-date", "m9-years-away", "m11-adjacent-rows",
                                     "m12-total-99", "m10-three-decimal-places", "m4-twenty-one-rows"])
def test_filing_and_updating_refuse_a_table_with_the_same_words(db, me, case_id):
    """AC-19: one validator for both routes, so the message a person gets does not depend on which they used."""
    case = next(c for c in CASES["rows"] if c["id"] == case_id)
    bad = sow(**{**case["values"], "deliverable_rows": case["rows"]})
    assert _refused(lambda: service.create_request(
        db, actor=me, defer_triage=True,
        payload=RequestCreate(type_label="Statement of Work Request", description="sow rows",
                              field_values=bad))) == case["error"]
    out = _file(db, me, pricing=case["values"]["pricing"], **(
        {"deliverable_rows": _with_shares(ROWS)} if case["values"]["pricing"] == "Milestones" else {}))
    assert _refused(lambda: _update(db, me, out["id"], field_values=bad)) == case["error"]


def test_another_organisations_request_cannot_be_updated(db, me):
    """AC-43: a request is found only inside its own organisation."""
    out = _file(db, me)
    stranger = _user(db, "Stranger")
    with pytest.raises(HTTPException) as exc:
        _update(db, stranger, out["id"], field_values=sow(deliverable_rows=ROWS[:1]))
    assert exc.value.status_code == 404
    assert _audit(db, out["id"], "intake.deliverables_changed") == []


# --- requests filed before the table (FR-31, FR-32) ------------------------------------------------

def test_an_older_request_takes_a_change_of_priority_and_keeps_its_text(db, me):
    """AC-35: the old free-text answer is not a reason to refuse an unrelated change."""
    old = _filed_before_the_table(db, me)
    out = _update(db, me, old.id, priority="High")
    assert out["priority"] == "High" and out["field_values"]["deliverables"] == OLD_TEXT
    assert "deliverable_rows" not in out["field_values"]


def test_an_older_request_takes_a_change_of_another_answer_and_keeps_its_text(db, me):
    old = _filed_before_the_table(db, me)
    sent = {k: v for k, v in sow(department="Finance").items() if k != "deliverable_rows"}  # no free text sent
    out = _update(db, me, old.id, field_values=sent)
    assert out["field_values"]["department"] == "Finance"
    assert out["field_values"]["deliverables"] == OLD_TEXT and "deliverable_rows" not in out["field_values"]
    assert _audit(db, old.id, "intake.deliverables_changed") == []


def test_an_older_request_with_a_zero_value_is_not_refused_for_it(db, me):
    """FR-32 edge: a total value of 0 was allowed when it was filed; only a change to it meets the new rule."""
    old = _filed_before_the_table(db, me, value=0)
    assert _update(db, me, old.id, priority="Low")["priority"] == "Low"
    unchanged = {k: v for k, v in older(value=0).items() if k != "deliverables"}
    assert _update(db, me, old.id, field_values=unchanged)["field_values"]["value"] == 0
    assert "must be more than 0" in _refused(
        lambda: _update(db, me, old.id, field_values={**unchanged, "value": -5}))


def test_entering_rows_for_an_older_request_is_recorded_and_the_text_is_untouched(db, me):
    """AC-34, AC-36: rows are set on top of the old words; nothing converts or rewrites them."""
    old = _filed_before_the_table(db, me)
    out = _update(db, me, old.id, field_values={**older(), "deliverable_rows": ROWS})
    assert out["field_values"]["deliverables"] == OLD_TEXT
    assert out["field_values"]["deliverable_rows"] == ROWS
    (row,) = _audit(db, old.id, "intake.deliverables_changed")
    assert row.before["deliverable_rows"] is None and row.after["deliverable_rows"] == ROWS
    (event,) = _history(db, old.id, "intake.deliverables_changed")
    assert event.title == "Deliverables entered as rows"


def test_rows_for_an_older_request_meet_every_rule(db, me):
    old = _filed_before_the_table(db, me)
    late = [ROWS[0], {**ROWS[1], "due": "2031-01-01"}]
    assert _refused(lambda: _update(db, me, old.id, field_values={**older(), "deliverable_rows": late})
                    ).startswith('"Deliverables", row 2: "Due date" must be between')
    assert service.get_request(db, user=me, request_id=old.id).field_values == older()
    assert _audit(db, old.id, "intake.deliverables_changed") == []
