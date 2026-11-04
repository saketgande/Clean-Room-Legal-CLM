"""A counterparty typed in chat must end up in the register and on the request, not only in the text.
The request page showed 'Counterparty —' because chat filed a bare name."""

import pytest
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake.chat_filing import new_party_problem, register_lookup
from app.parties import service
from app.parties.models import Counterparty
from app.parties.routes import CounterpartyIn

NAME = "test-chat-cp Logitech"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for row in s.scalars(select(Counterparty).where(Counterparty.name.ilike("test-chat-cp%"))):
            s.delete(row)
        s.commit()
        s.close()


@pytest.fixture
def actor(db):
    user = db.scalar(select(User).order_by(User.created_at))
    if user is None:
        pytest.skip("needs a seeded user")
    return user


def test_a_new_counterparty_is_asked_for_its_details_before_filing(db, actor):
    msg = new_party_problem(db, actor, counterparty=NAME, jurisdiction=None, email=None)
    assert "jurisdiction" in msg and "contact email" in msg
    assert new_party_problem(db, actor, counterparty=NAME, jurisdiction="India", email="a@b.com") is None


def test_a_registered_counterparty_is_matched_by_name_and_needs_nothing_more(db, actor):
    service.create(db, actor=actor, kind="counterparty",
                   payload=CounterpartyIn(name=NAME, jurisdiction="India", contact_email="legal@example.com"))
    exact, _ = register_lookup(db, actor, NAME.upper())
    assert exact and exact["name"] == NAME
    assert new_party_problem(db, actor, counterparty=NAME, jurisdiction=None, email=None) is None


def test_a_near_name_is_put_to_the_user_instead_of_creating_a_duplicate(db, actor):
    service.create(db, actor=actor, kind="counterparty",
                   payload=CounterpartyIn(name=NAME + " India", jurisdiction="India", contact_email="l@example.com"))
    assert NAME + " India" in new_party_problem(db, actor, counterparty=NAME, jurisdiction=None, email=None)
