"""A request that names no counterparty must not leave "the Counterparty" on the contract.

"the Counterparty" is wording for the SENTENCES of a draft ("the Counterparty shall keep the
information confidential"). It was also being used as the contract's title ("Mutual NDA — the
Counterparty"), its file name and its recorded counterparty, so the contract list and the
notifications read like a template (17 contracts in one database carried it as the party name).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.intake.drafting import UNNAMED_COUNTERPARTY, DraftingService, _named, _with_counterparty
from app.intake.models import IntakeRequest
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


def test_only_a_real_name_counts_as_a_name():
    assert _named("Acme Ltd") == "Acme Ltd"
    assert _named("  Acme Ltd ") == "Acme Ltd"
    assert _named(UNNAMED_COUNTERPARTY) is None
    assert _named("") is None
    assert _named(None) is None


def test_a_title_names_the_other_side_only_when_there_is_one():
    assert _with_counterparty("Mutual NDA", "Acme Ltd") == "Mutual NDA — Acme Ltd"
    assert _with_counterparty("Mutual NDA", UNNAMED_COUNTERPARTY) == "Mutual NDA"
    assert _with_counterparty("Mutual NDA", "Acme Ltd", sep=" - ") == "Mutual NDA - Acme Ltd"


async def _draft(db, field_values):
    org = Organization(id=new_uuid(), name="Draft Co", slug=f"draft-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    actor = User(org_id=org.id, email=f"d-{uuid.uuid4().hex[:8]}@example.com", full_name="Drafter",
                 hashed_password="h", status="active")
    db.add(actor)
    db.flush()
    req = IntakeRequest(
        org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=actor.id,
        type_label="NDA", description="t", field_values=field_values, priority="Medium", status="open",
        stage="new", submitted_at=utcnow(), created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    db.add(req)
    db.flush()
    seen: dict = {}
    service = DraftingService(db)

    async def _create(*, upload, user, title, counterparty_name=None, **kwargs):
        seen.update(title=title, filename=upload.filename, counterparty_name=counterparty_name,
                    text=upload.file.read().decode("utf-8"))
        contract = Contract(org_id=org.id, title=title, owner_user_id=user.id, counterparty_name=counterparty_name,
                            created_by_user_id=user.id, updated_by_user_id=user.id)
        db.add(contract)
        db.flush()
        return {"contract": contract}

    service.files.create_contract_from_upload = _create
    await service.draft_contract_for_request(actor=actor, request=req)
    return seen


async def test_a_draft_for_a_request_with_no_counterparty_is_not_titled_with_the_stand_in(db):
    seen = await _draft(db, {"doc_type": "nda"})
    assert "Counterparty" not in seen["title"]
    assert "Counterparty" not in seen["filename"]
    assert seen["counterparty_name"] is None


async def test_a_draft_for_a_named_counterparty_keeps_its_name_everywhere(db):
    seen = await _draft(db, {"doc_type": "nda", "counterparty": "Acme Ltd"})
    assert seen["title"].endswith("— Acme Ltd")
    assert "Acme Ltd" in seen["filename"]
    assert seen["counterparty_name"] == "Acme Ltd"
