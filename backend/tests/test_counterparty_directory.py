"""The intake form's "Name of Counterparty" look-up: an org-wide, distinct
directory merging ContractParty(party_type='counterparty') rows with any
Contract.counterparty_name that never got its own party row."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contracts.models import Contract, ContractParty
from app.contracts.service import list_counterparty_directory
from app.core.database import engine, new_uuid
from app.organizations.models import Organization


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


def _org(db):
    org = Organization(id=new_uuid(), name="Dir Co", slug=f"dir-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db, org_id):
    u = User(org_id=org_id, email=f"u-{uuid.uuid4().hex[:8]}@example.com", full_name="U",
             hashed_password="h", status="active")
    db.add(u)
    db.flush()
    return u


def _contract(db, org_id, owner, *, counterparty_name=None):
    c = Contract(
        org_id=org_id, title="C", owner_user_id=owner.id, contract_type="nda", risk_band="low",
        risk_level="low", jurisdiction="US", currency="USD", counterparty_name=counterparty_name,
        created_by_user_id=owner.id, updated_by_user_id=owner.id,
    )
    db.add(c)
    db.flush()
    return c


def test_merges_party_rows_and_bare_contract_counterparty_names(db):
    org = _org(db)
    owner = _user(db, org.id)
    with_party = _contract(db, org.id, owner)
    db.add(ContractParty(org_id=org.id, contract_id=with_party.id, name="Acme Ltd",
                         party_type="counterparty", contact_email="legal@acme.com"))
    _contract(db, org.id, owner, counterparty_name="Bravo Inc")  # no party row at all
    db.flush()

    names = {o["name"]: o["contact_email"] for o in list_counterparty_directory(db, org_id=org.id)}

    assert names["Acme Ltd"] == "legal@acme.com"
    assert names["Bravo Inc"] is None


def test_dedupes_by_name_and_filters_by_query(db):
    org = _org(db)
    owner = _user(db, org.id)
    c1 = _contract(db, org.id, owner, counterparty_name="Acme Ltd")
    db.add(ContractParty(org_id=org.id, contract_id=c1.id, name="Acme Ltd",
                         party_type="counterparty", contact_email="a@acme.com"))
    _contract(db, org.id, owner, counterparty_name="Zenith Corp")
    db.flush()

    all_options = list_counterparty_directory(db, org_id=org.id)
    assert [o["name"] for o in all_options].count("Acme Ltd") == 1

    filtered = list_counterparty_directory(db, org_id=org.id, q="acm")
    assert [o["name"] for o in filtered] == ["Acme Ltd"]


def test_scoped_to_org(db):
    org_a = _org(db)
    org_b = _org(db)
    owner_a = _user(db, org_a.id)
    owner_b = _user(db, org_b.id)
    _contract(db, org_a.id, owner_a, counterparty_name="Only In A")
    _contract(db, org_b.id, owner_b, counterparty_name="Only In B")

    names = {o["name"] for o in list_counterparty_directory(db, org_id=org_a.id)}
    assert "Only In A" in names
    assert "Only In B" not in names


def test_ignores_non_counterparty_party_rows(db):
    org = _org(db)
    owner = _user(db, org.id)
    c = _contract(db, org.id, owner)
    db.add(ContractParty(org_id=org.id, contract_id=c.id, name="Internal Signer",
                         party_type="signer", contact_email="signer@example.com"))
    db.flush()

    names = {o["name"] for o in list_counterparty_directory(db, org_id=org.id)}
    assert "Internal Signer" not in names
