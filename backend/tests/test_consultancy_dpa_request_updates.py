"""Filing and changing a Consultancy or DPA request leaves a record, and changes only what it should.

Failures this guards:
* a filing whose audit row does not hold the answers the draft is made from, exactly as stored (FR-50);
* an answer changed, dropped (a rate when the fee basis goes to Fixed fee, the countries when no data leaves
  India) or first entered on an older request, with no audit row and no history entry showing who and the
  answers before and after, or one written on a second session, or for a refused or unchanged update;
* an older request refused for an answer it never had, or its older 'Several' rewritten, by an unrelated
  update (FR-41, FR-42);
* the same bad answer refused with different words on filing, on update and by the assistant (FR-21, FR-30);
* a name typed into a key individual or a contact that creates a notification, a user, a grant or a link
  (FR-18, FR-19, FR-26), or another organisation's request reachable by an update (FR-52).

Runs against Postgres inside one rolled-back transaction.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.ai.tool_registry import CreateIntakeRequestInput
from app.ai.tool_runtime import ToolRuntime
from app.auth.models import User
from app.core.database import engine, new_uuid, utcnow
from app.core.models import AuditLog, ResourceTimelineEvent
from app.intake import service
from app.intake.models import IntakeRequest
from app.intake.schemas import RequestCreate, RequestUpdate
from app.notifications.models import Notification
from app.organizations.models import Organization
from app.parties.models import Counterparty, LegalEntity
from tests.test_consultancy_dpa_answers import (
    CONSULTANCY,
    DPA_TABLES,
    NEW_KEYS,
    OLD_CONSULTANCY,
    OLD_DPA,
    consultancy,
    dpa,
)
from tests.test_sow_deliverable_rows import ROWS, sow

CONSULTANCY_FILLS = ("advice_only", "consultancy_deliverables", "fee_basis", "fee_rate", "key_individuals",
                     "company_contact_name", "company_contact_role")
DPA_FILLS = ("processing_purpose", "data_types", "data_subjects", "transfer", "transfer_countries",
             "subprocessors", "subprocessor_rows")


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
        org = Organization(id=new_uuid(), name="answers", slug=f"answers-{uuid.uuid4().hex[:8]}")
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


def _file(db: Session, user: User, values: dict) -> dict:
    """File through the real service. The wizard picks its entity and counterparty from the organisation's
    register, so a request that names them gets this organisation's own."""
    if "entity_id" in values:
        entity = db.scalar(select(LegalEntity).where(LegalEntity.org_id == user.org_id))
        if entity is None:
            entity = LegalEntity(org_id=user.org_id, name="Aegis Pharma Ltd")
            party = Counterparty(org_id=user.org_id, name="Acme Advisory LLP")
            db.add_all([entity, party])
            db.flush()
        party = db.scalar(select(Counterparty).where(Counterparty.org_id == user.org_id))
        values = {**values, "entity_id": entity.id, "counterparty_id": party.id}
    return service.create_request(
        db, actor=user, defer_triage=True,
        payload=RequestCreate(type_label="New agreement", description="answers", field_values=values))


def _update(db: Session, user: User, request_id: str, **payload) -> dict:
    return service.update_request(db, actor=user, request_id=request_id, payload=RequestUpdate(**payload))


def _filed_before(db: Session, user: User, values: dict) -> IntakeRequest:
    """A request as it was filed before these questions existed."""
    row = IntakeRequest(
        org_id=user.org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=user.id,
        type_label="New agreement", description="answers", field_values=values, priority="Medium",
        status="open", stage="new", submitted_at=utcnow(), created_by_user_id=user.id, updated_by_user_id=user.id)
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


def _refused(call) -> str:
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 422
    return exc.value.detail


def _stored(db, user, request_id) -> dict:
    return service.get_request(db, user=user, request_id=request_id).field_values


def _answers(values: dict, keys) -> dict:
    return {k: values.get(k) for k in keys}


# --- filing -----------------------------------------------------------------------------------------

def test_filing_a_consultancy_records_the_answers_exactly_as_stored(db, me):
    """FR-50, AC-55: the audit row holds each answer the draft is made from, as stored (tidied, the empty row
    and the unused rate gone), and nothing the request does not have."""
    out = _file(db, me, consultancy())
    stored = out["field_values"]
    (row,) = _audit(db, out["id"], "intake.created")
    assert {k: v for k, v in row.after.items() if k not in ("ref", "type", "priority")} == {
        k: stored[k] for k in CONSULTANCY_FILLS if k in stored}
    assert row.after["company_contact_name"] == "Ravi Shah" and "fee_rate" not in row.after
    assert len(row.after["consultancy_deliverables"]) == 2 and row.after["fee_basis"] == "Fixed fee"


def test_filing_a_dpa_records_the_answers_exactly_as_stored(db, me):
    out = _file(db, me, dpa())
    (row,) = _audit(db, out["id"], "intake.created")
    assert {k: row.after[k] for k in DPA_FILLS} == _answers(out["field_values"], DPA_FILLS)
    assert row.after["data_subjects"] == ["Customers", "Patients or trial subjects"]


def test_filing_a_dpa_that_needs_no_tables_records_no_tables(db, me):
    out = _file(db, me, dpa(transfer="No", subprocessors="No"))
    (row,) = _audit(db, out["id"], "intake.created")
    assert not set(DPA_TABLES) & set(row.after) and row.after["transfer"] == "No"


def test_a_filing_with_none_of_these_answers_adds_nothing_to_its_audit_row(db, me):
    """An MSA has none of the keys; its audit row is what it was before this feature."""
    from tests.test_intake_form_values_make_sense import MSA

    out = _file(db, me, MSA)
    (row,) = _audit(db, out["id"], "intake.created")
    assert set(row.after) == {"ref", "type", "priority"}


def test_a_refused_filing_files_and_records_nothing(db, me):
    detail = _refused(lambda: _file(db, me, consultancy(key_individuals=[{"name": "A"}, {"name": " a "}])))
    assert detail.startswith('"Consultant\'s key individuals", rows 1 and 2')
    assert db.scalar(select(func.count()).select_from(IntakeRequest).where(IntakeRequest.org_id == me.org_id)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.org_id == me.org_id)) == 0


# --- updating ---------------------------------------------------------------------------------------

def test_changing_two_deliverables_to_one_records_who_and_both_sets_of_answers(db, me):
    """AC-55: the audit row and the history hold every answer before and after, in one transaction."""
    out = _file(db, me, consultancy())
    before = out["field_values"]
    kept = [before["consultancy_deliverables"][1]]
    updated = _update(db, me, out["id"], field_values={**before, "consultancy_deliverables": kept})
    assert updated["field_values"]["consultancy_deliverables"] == kept
    (row,) = _audit(db, out["id"], "intake.answers_changed")
    assert row.actor_user_id == me.id and row.org_id == me.org_id
    assert set(row.before) == set(row.after) == set(CONSULTANCY_FILLS)  # every tracked key, None when absent
    assert row.before == _answers(before, CONSULTANCY_FILLS)
    assert row.after == {**row.before, "consultancy_deliverables": kept}
    assert row.before["advice_only"] is None and row.before["fee_rate"] is None
    (event,) = _history(db, out["id"], "intake.answers_changed")
    assert event.title == "Answers for the draft changed" and event.created_by_user_id == me.id
    assert event.details == {"before": row.before, "after": row.after, "changed": ["consultancy_deliverables"]}
    assert _audit(db, out["id"], "intake.deliverables_changed") == []  # the SOW's own record is not used here


def test_changes_that_leave_the_answers_alone_record_nothing_about_them(db, me):
    out = _file(db, me, consultancy())
    _update(db, me, out["id"], priority="High")
    _update(db, me, out["id"], field_values={**out["field_values"], "department": "Finance"})
    _update(db, me, out["id"], field_values={**out["field_values"], "company_contact_name": " Ravi   Shah "})
    assert _audit(db, out["id"], "intake.answers_changed") == []
    assert _history(db, out["id"], "intake.answers_changed") == []
    assert _stored(db, me, out["id"])["company_contact_name"] == "Ravi Shah"


def test_the_record_is_written_when_the_update_also_moves_the_stage(db, me):
    """Both branches of an update commit the same way, so the record must not depend on the stage."""
    out = _file(db, me, consultancy())
    _update(db, me, out["id"], stage="assigned",
            field_values={**out["field_values"], "company_contact_role": "Director of Quality"})
    assert len(_audit(db, out["id"], "intake.answers_changed")) == 1
    assert len(_history(db, out["id"], "intake.answers_changed")) == 1


def test_a_change_to_fixed_fee_removes_the_rate_and_the_audit_shows_it(db, me):
    """AC-20, FR-16: the rate belongs to a rate basis; leaving it removes it, and the record says so."""
    out = _file(db, me, consultancy(fee_basis="Day rate", fee_rate="25000"))
    assert out["field_values"]["fee_rate"] == 25000
    updated = _update(db, me, out["id"], field_values={**out["field_values"], "fee_basis": "Fixed fee"})
    assert "fee_rate" not in updated["field_values"]
    (row,) = _audit(db, out["id"], "intake.answers_changed")
    assert (row.before["fee_basis"], row.before["fee_rate"]) == ("Day rate", 25000)
    assert (row.after["fee_basis"], row.after["fee_rate"]) == ("Fixed fee", None)
    (event,) = _history(db, out["id"], "intake.answers_changed")
    assert event.details["changed"] == ["fee_basis", "fee_rate"]


def test_a_change_to_a_rate_basis_needs_a_rate(db, me):
    out = _file(db, me, consultancy())
    detail = _refused(lambda: _update(db, me, out["id"], field_values={**out["field_values"], "fee_basis": "Hourly rate"}))
    assert detail == 'Field "Rate per day or hour" is required'
    assert _audit(db, out["id"], "intake.answers_changed") == []
    done = _update(db, me, out["id"], field_values={**out["field_values"], "fee_basis": "Hourly rate",
                                                    "fee_rate": "3500"})
    assert done["field_values"]["fee_rate"] == 3500
    assert len(_audit(db, out["id"], "intake.answers_changed")) == 1


def test_a_dpa_changed_to_no_removes_its_tables_and_a_change_back_needs_entries(db, me):
    """AC-33, FR-29."""
    out = _file(db, me, dpa())
    full = out["field_values"]
    no = _update(db, me, out["id"], field_values={**full, "transfer": "No", "subprocessors": "No"})
    assert not set(DPA_TABLES) & set(no["field_values"])
    (row,) = _audit(db, out["id"], "intake.answers_changed")
    assert row.before["transfer_countries"] == full["transfer_countries"] and row.after["transfer_countries"] is None
    assert row.before["subprocessor_rows"] == full["subprocessor_rows"] and row.after["subprocessor_rows"] is None
    sent = {**{k: v for k, v in full.items() if k not in DPA_TABLES}, "transfer": "No", "subprocessors": "No"}
    assert _refused(lambda: _update(db, me, out["id"], field_values={**sent, "transfer": "Yes"})) == (
        '"Countries outside India" needs at least 1 row.')
    assert _refused(lambda: _update(db, me, out["id"], field_values={**sent, "subprocessors": "Yes"})) == (
        '"Sub-processors" needs at least 1 row.')
    assert len(_audit(db, out["id"], "intake.answers_changed")) == 1  # the refusals wrote nothing


def test_a_refused_update_changes_nothing_and_records_nothing(db, me):
    out = _file(db, me, consultancy())
    before = _stored(db, me, out["id"])
    _refused(lambda: _update(db, me, out["id"], field_values={**before, "key_individuals": [{"name": ""}]}))
    assert _stored(db, me, out["id"]) == before
    assert _audit(db, out["id"], "intake.answers_changed") == []
    assert _history(db, out["id"], "intake.answers_changed") == []


def test_a_sow_update_keeps_its_own_record_and_writes_no_answers_record(db, me):
    """The SOW's questions carry no fills, so its rows are recorded only as intake.deliverables_changed."""
    out = service.create_request(db, actor=me, defer_triage=True, payload=RequestCreate(
        type_label="Statement of Work Request", description="sow", field_values=sow()))
    _update(db, me, out["id"], field_values=sow(deliverable_rows=[ROWS[0]]))
    assert len(_audit(db, out["id"], "intake.deliverables_changed")) == 1
    assert _audit(db, out["id"], "intake.answers_changed") == []


# --- requests filed before these questions (FR-41, FR-42) --------------------------------------------

def test_an_older_consultancy_takes_other_changes_without_the_new_answers(db, me):
    old = _filed_before(db, me, OLD_CONSULTANCY)
    assert _update(db, me, old.id, priority="High")["priority"] == "High"
    out = _update(db, me, old.id, field_values={**OLD_CONSULTANCY, "department": "Finance"})
    assert out["field_values"]["department"] == "Finance" and not set(NEW_KEYS) & set(out["field_values"])
    assert _audit(db, old.id, "intake.answers_changed") == []


def test_an_older_consultancy_that_gets_a_rate_basis_needs_the_rate(db, me):
    """The exemption ends when the basis changes: the same update with the rate is accepted."""
    old = _filed_before(db, me, OLD_CONSULTANCY)
    sent = {**OLD_CONSULTANCY, "fee_basis": "Day rate"}
    assert _refused(lambda: _update(db, me, old.id, field_values=sent)) == 'Field "Rate per day or hour" is required'
    assert _stored(db, me, old.id) == OLD_CONSULTANCY
    done = _update(db, me, old.id, field_values={**sent, "fee_rate": "20000"})
    assert done["field_values"]["fee_rate"] == 20000


def test_entering_the_answers_for_an_older_request_is_recorded_as_entered(db, me):
    """AC-55: the first entry is 'entered', every tracked key was absent before, and a later change says 'changed'."""
    old = _filed_before(db, me, OLD_CONSULTANCY)
    done = {**OLD_CONSULTANCY, "fee_basis": "Day rate", "fee_rate": "25000",
            "consultancy_deliverables": [{"name": "Report", "due": "2026-12-15"}],
            "key_individuals": [{"name": "Anita Rao"}], "company_contact_name": "Ravi Shah",
            "company_contact_role": "Head of Quality"}
    out = _update(db, me, old.id, field_values=done)
    assert out["field_values"]["fee_rate"] == 25000
    (row,) = _audit(db, old.id, "intake.answers_changed")
    assert set(row.before.values()) == {None} and row.after["company_contact_name"] == "Ravi Shah"
    (event,) = _history(db, old.id, "intake.answers_changed")
    assert event.title == "Answers for the draft entered"
    _update(db, me, old.id, field_values={**out["field_values"], "key_individuals": [{"name": "Dev Menon"}]})
    assert [e.title for e in sorted(_history(db, old.id, "intake.answers_changed"), key=lambda e: e.created_at)] == [
        "Answers for the draft entered", "Answers for the draft changed"]


def test_an_older_dpa_with_several_keeps_it_through_other_updates(db, me):
    """AC-45: priority, another answer, or the answers sent back all leave 'Several' as filed."""
    old = _filed_before(db, me, OLD_DPA)
    assert _update(db, me, old.id, priority="Low")["field_values"]["data_subjects"] == "Several"
    out = _update(db, me, old.id, field_values={**OLD_DPA, "our_role": "Processor: we act on their instructions"})
    assert out["field_values"]["data_subjects"] == "Several" and out["field_values"]["data_types"] == "Financial"
    assert _audit(db, old.id, "intake.answers_changed") == []


def test_an_older_dpa_replacing_several_with_ticks_is_recorded(db, me):
    old = _filed_before(db, me, OLD_DPA)
    out = _update(db, me, old.id, field_values={**OLD_DPA, "data_subjects": ["Customers"], "data_types": ["Financial"]})
    assert out["field_values"]["data_subjects"] == ["Customers"]
    (row,) = _audit(db, old.id, "intake.answers_changed")
    assert row.before["data_subjects"] == "Several" and row.after["data_subjects"] == ["Customers"]


# --- one validator for every route -----------------------------------------------------------------

@pytest.mark.parametrize("make, bad, message", [
    (consultancy, {"consultancy_deliverables": [{"name": "Report", "due": "2031-01-01"}]},
     '"Deliverables", row 1: "Due date" must be between 1 November 2026 and 30 April 2027 (Start date to End date).'),
    (dpa, {"subprocessor_rows": [{"name": "", "country": "Singapore", "processing": "Hosting"}]},
     '"Sub-processors", row 1: "Sub-processor name" is required.'),
    (consultancy, {"fee_basis": "Day rate", "fee_rate": "25.001"},
     'Field "Rate per day or hour" can have at most 2 decimal places'),
], ids=["due-date-outside-the-window", "sub-processor-with-no-name", "rate-with-three-decimals"])
def test_filing_updating_and_the_assistant_refuse_the_same_answer_with_the_same_words(db, me, make, bad, message):
    """AC-25, AC-34: one validator, so the message does not depend on the route a request took."""
    good = make()
    assert _refused(lambda: _file(db, me, make(**bad))) == message
    out = _file(db, me, good)
    assert _refused(lambda: _update(db, me, out["id"], field_values=make(**bad))) == message
    payload = CreateIntakeRequestInput(type_label="New agreement", description="answers", field_values=make(**bad))
    assert _refused(lambda: ToolRuntime()._create_intake_request(db, payload=payload, user=me)) == message


# --- names are text ---------------------------------------------------------------------------------

def test_a_key_individual_or_contact_who_shares_a_users_name_gives_that_user_nothing(db, me):
    """FR-18, FR-19, FR-26, AC-22, AC-57: names are typed text. No lookup, notification, account, grant or
    access follows, and the named user cannot read the request through the name."""
    org = db.get(Organization, me.org_id)
    anita = _user(db, "Anita Rao", org=org)
    people = db.scalar(select(func.count()).select_from(User).where(User.org_id == me.org_id))
    out = _file(db, me, consultancy(company_contact_name="Anita Rao"))
    assert out["field_values"]["key_individuals"][0] == {"name": "Anita Rao"}
    assert db.scalar(select(func.count()).select_from(User).where(User.org_id == me.org_id)) == people
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == anita.id)) == 0
    with pytest.raises(HTTPException) as exc:
        service.get_request(db, user=anita, request_id=out["id"])
    assert exc.value.status_code == 404


def test_another_organisations_request_cannot_be_updated(db, me):
    """FR-52: a request is found only inside its own organisation, and a refused attempt records nothing."""
    out = _file(db, me, consultancy())
    stranger = _user(db, "Stranger")
    with pytest.raises(HTTPException) as exc:
        _update(db, stranger, out["id"], field_values={**out["field_values"], "company_contact_role": "Other"})
    assert exc.value.status_code == 404
    assert _audit(db, out["id"], "intake.answers_changed") == []
    assert _stored(db, me, out["id"])["company_contact_role"] == CONSULTANCY["company_contact_role"]

