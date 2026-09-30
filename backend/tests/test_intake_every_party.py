"""Every party on a request is recorded, and drafts use the register records.

Only the first counterparty was put on a request's parties list, so a second
counterparty, or a novation's incoming party, never appeared. Drafting then
named the organisation as "our side" whatever entity was picked, and took the
counterparty from whatever was typed.
"""

import pytest
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import drafting, screening
from app.intake import service as intake
from app.intake.models import IntakeRequest
from app.intake.schemas import RequestCreate
from app.parties.models import Counterparty, LegalEntity

_TAG = "test-every-party"


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
            for row in s.scalars(select(model).where(model.name.like(f"{_TAG}%"))):
                s.delete(row)
        s.commit()
        s.close()


@pytest.fixture
def actor(db):
    user = db.scalar(select(User).order_by(User.created_at))
    if user is None:
        pytest.skip("needs a seeded user")
    return user


def _file(db, actor, fv):
    out = intake.create_request(db, actor=actor, defer_triage=True,
                                payload=RequestCreate(type_label="Contract Question", description=_TAG,
                                                      field_values=fv))
    return db.get(IntakeRequest, out["id"])


def test_a_second_counterparty_is_recorded(db, actor):
    """The defect: Counterparty 2 never reached the request's parties. The
    relationship note stays on the first counterparty, the primary."""
    r = _file(db, actor, {"counterparty": "Harmless Trading Ltd",
                          "counterparty_2": "Second Trading LLC"})
    names = [p["name"] for p in screening.gather_parties(r)]
    assert names == ["Harmless Trading Ltd", "Second Trading LLC"]
    result = screening.compute_screening(db, r)
    assert result["counterparty"] == "Harmless Trading Ltd"
    assert "sanctions" not in result and "conflicts" not in result
    assert result["relationship"]["note"]


def test_novation_parties_are_recorded(db, actor):
    """A novation's incoming party is the new counterparty; outgoing and
    remaining parties are kept as related."""
    r = _file(db, actor, {"counterparty": "Old Supplier Ltd", "incoming_party": "New Supplier Ltd",
                          "outgoing_party": "Old Supplier Ltd", "remaining_party": "Our Company Ltd"})
    parties = screening.gather_parties(r)
    assert [(p["name"], p["role"]) for p in parties] == [
        ("Old Supplier Ltd", "counterparty"), ("New Supplier Ltd", "counterparty"),
        ("Our Company Ltd", "related")]


def test_the_draft_names_the_picked_entity_and_counterparty(db, actor):
    """The draft said "<organisation name>" as our side whatever entity was picked."""
    e = LegalEntity(org_id=actor.org_id, name=f"{_TAG} Entity UK Ltd", jurisdiction="England and Wales",
                    registered_address="1 Test Street, London", authorised_signatory="A. Director")
    c = Counterparty(org_id=actor.org_id, name=f"{_TAG} Supplier GmbH", jurisdiction="Germany")
    db.add_all([e, c])
    db.commit()
    r = _file(db, actor, {"entity_id": e.id, "entity": "typed", "counterparty_id": c.id, "counterparty": "typed"})
    company, counterparty, lines = drafting._party_details(db, r, "Org Name Inc")
    assert (company, counterparty) == (e.name, c.name)
    assert "England and Wales" in lines[0] and "A. Director" in lines[0] and "Germany" in lines[1]


def test_old_requests_without_records_still_draft(db, actor):
    """Guards over-correction: requests filed before the register keep working."""
    r = _file(db, actor, {"counterparty": "Legacy Counterparty Ltd"})
    company, counterparty, lines = drafting._party_details(db, r, "Org Name Inc")
    assert (company, counterparty, lines) == ("Org Name Inc", "Legacy Counterparty Ltd", [])
