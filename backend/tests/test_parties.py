"""Entities and counterparties are real records, and requests link to them.

Both used to be free text: the entity "lookup" offered three hard-coded Acme
names, the counterparty "Look-up" was a label with nothing behind it, and
"Create counterparty" was a link that did nothing. A request therefore carried
a typed name, never a record, so nothing tied it to the counterparty's other
contracts.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import service as intake
from app.intake.models import IntakeRequest
from app.intake.schemas import RequestCreate
from app.parties import service
from app.parties.models import Counterparty, LegalEntity
from app.parties.routes import CounterpartyIn, CounterpartyPatch

_TAG = "test-parties"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for r in s.scalars(select(IntakeRequest).where(IntakeRequest.description == _TAG)):
            s.delete(r)
        s.flush()
        for model in (LegalEntity, Counterparty):
            for row in s.scalars(select(model).where(model.name.ilike(f"{_TAG}%"))):
                s.delete(row)
        s.commit()
        s.close()


@pytest.fixture
def actor(db):
    user = db.scalar(select(User).order_by(User.created_at))
    if user is None:
        pytest.skip("needs a seeded user")
    return user


def _cp(db, actor, name):
    return service.create(db, actor=actor, kind="counterparty",
                          payload=CounterpartyIn(name=name, jurisdiction="India"))


def test_create_counterparty_makes_a_findable_record(db, actor):
    """The defect: "Create counterparty" did nothing and "Look-up" searched nothing."""
    made = _cp(db, actor, f"{_TAG} Globex Corporation")
    hits = service.search(db, actor=actor, kind="counterparty", q="globex corp")
    assert [h["id"] for h in hits if h["id"] == made["id"]] == [made["id"]]


def test_the_same_counterparty_cannot_be_created_twice(db, actor):
    """Case and spacing variants are the same company, not a second record."""
    _cp(db, actor, f"{_TAG} Initech Ltd")
    with pytest.raises(HTTPException) as exc:
        _cp(db, actor, f"  {_TAG.upper()}   initech  ltd ")
    assert exc.value.status_code == 409


def _file(db, actor, fv):
    return intake.create_request(db, actor=actor, defer_triage=True,
                                 payload=RequestCreate(type_label="Contract Question", description=_TAG,
                                                       field_values=fv))


def test_a_filed_request_links_to_the_record_and_uses_its_registered_name(db, actor):
    """The name typed in the browser is not trusted; the register's name is stored."""
    made = _cp(db, actor, f"{_TAG} Umbrella Pharma Pvt Ltd")
    out = _file(db, actor, {"counterparty_id": made["id"], "counterparty": "umbrella"})
    assert out["counterparty_id"] == made["id"]
    assert out["field_values"]["counterparty"] == made["name"]
    assert db.get(IntakeRequest, out["id"]).parties[0]["counterparty_id"] == made["id"]


def test_an_unknown_or_retired_record_is_refused(db, actor):
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, {"counterparty_id": "00000000-0000-0000-0000-000000000000"})
    assert exc.value.status_code == 422
    made = _cp(db, actor, f"{_TAG} Retired Co")
    service.update(db, actor=actor, kind="counterparty", record_id=made["id"],
                   payload=CounterpartyPatch(active=False))
    with pytest.raises(HTTPException):
        _file(db, actor, {"counterparty_id": made["id"]})


def test_inactive_records_are_hidden_from_the_lookup(db, actor):
    made = _cp(db, actor, f"{_TAG} Hidden Co")
    service.update(db, actor=actor, kind="counterparty", record_id=made["id"],
                   payload=CounterpartyPatch(active=False))
    assert all(h["id"] != made["id"] for h in service.search(db, actor=actor, kind="counterparty", q=_TAG))
