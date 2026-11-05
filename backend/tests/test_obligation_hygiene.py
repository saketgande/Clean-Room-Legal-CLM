"""Two small honesty fixes that land before any extraction baseline: the extraction answer is bounded to what
the database can store, and the "now active" notice says what really happened to the extraction. Real rows in
one rolled-back transaction."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.schemas import ObligationExtractionOutput, ObligationOutput
from app.auth.models import User
from app.contracts.lifecycle import ContractLifecycleService, activation_note
from app.contracts.models import Contract
from app.core.database import engine, new_uuid
from app.core.enums import UserStatus
from app.notifications.models import Notification
from app.obligations.models import Obligation
from app.organizations.models import Organization

BOTH = ("obligation_extraction", "renewal_extraction")


def _row(**fields) -> dict:
    return {"description": "Supplier shall deliver the report.", **fields}


# --- the answer is bounded to the columns ------------------------------------------------------------------------


def test_the_bounds_are_the_stored_column_sizes():
    """Guards the two drifting apart: the schema bound is only honest while it equals the column. A column made
    smaller without the bound would bring back the whole-save failure at the INSERT."""
    assert Obligation.__table__.c.obligation_type.type.length == 160
    assert Obligation.__table__.c.responsible_party.type.length == 255
    props = ObligationOutput.model_json_schema()["properties"]
    for name, size in (("obligation_type", 160), ("responsible_party", 255)):
        assert {"type": "string", "maxLength": size} in props[name]["anyOf"]


@pytest.mark.parametrize("field, size", [("obligation_type", 160), ("responsible_party", 255)])
def test_a_value_one_character_too_long_is_refused_and_the_limit_itself_is_fine(field, size):
    """Guards an overlong value reaching the INSERT (where it failed the whole save with the contract's words in
    the error) instead of failing validation, which the job reports as an unusable answer."""
    assert ObligationOutput(**_row(**{field: "x" * size})).model_dump()[field] == "x" * size
    with pytest.raises(ValidationError):
        ObligationOutput(**_row(**{field: "x" * (size + 1)}))
    with pytest.raises(ValidationError):
        ObligationExtractionOutput(obligations=[_row(**{field: "x" * (size + 1)})])


def test_a_missing_value_is_still_allowed():
    """Guards the bound turning 'the contract does not say' into a failure."""
    row = ObligationOutput(**_row())
    assert row.obligation_type is None and row.responsible_party is None


# --- the "now active" notice says what happened -----------------------------------------------------------------


def test_the_notice_never_says_monitored_unless_the_rows_exist():
    """Guards 'Obligations and renewals are being monitored' going to the owner when nothing was queued: a
    contract with no readable text has nothing monitoring it."""
    assert "already on file" in activation_note(wanted=(), scheduled=[], has_text=True)
    for scheduled, has_text in (([], True), ([], False), (["renewal_extraction"], True)):
        note = activation_note(wanted=BOTH, scheduled=scheduled, has_text=has_text)
        assert "are being monitored" not in note
        assert "not queued again" in note or "were not extracted" in note


def test_the_notice_names_what_was_queued_and_what_was_not():
    both = activation_note(wanted=BOTH, scheduled=list(BOTH), has_text=True)
    assert both == "Obligation and renewal extraction were queued automatically."
    one = activation_note(wanted=BOTH, scheduled=["renewal_extraction"], has_text=True)
    assert one.startswith("Renewal extraction was queued automatically.") and "obligations was not queued again" in one
    assert "check the Obligations page" in one and "tab" not in one  # the contract workspace has no such tab
    only = activation_note(wanted=("obligation_extraction",), scheduled=["obligation_extraction"], has_text=True)
    assert only == "Obligation extraction was queued automatically."
    none = activation_note(wanted=BOTH, scheduled=[], has_text=False)
    assert "no readable text" in none and "not being monitored" in none
    again = activation_note(wanted=BOTH, scheduled=[], has_text=True)
    assert "was not queued again" in again and "check the Obligations and Renewals pages" in again


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def test_a_contract_with_no_text_entering_active_tells_its_owner_nothing_is_monitored(db):
    """Guards the real path: the notice the owner reads when a contract goes Active with no text snapshot."""
    org = Organization(id=new_uuid(), name="Active org", slug=f"act-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    owner, actor = (
        User(org_id=org.id, email=f"{n}-{uuid.uuid4().hex[:6]}@example.com", full_name=n, hashed_password="x",
             status=UserStatus.ACTIVE)
        for n in ("owner", "actor")
    )
    db.add_all([owner, actor])
    db.flush()
    contract = Contract(org_id=org.id, title="No text yet", owner_user_id=owner.id)
    db.add(contract)
    db.flush()

    ContractLifecycleService(db)._on_enter_active(contract=contract, actor_user_id=actor.id)

    [note] = db.scalars(select(Notification).where(Notification.user_id == owner.id)).all()
    assert note.event_type == "lifecycle.entered_active"
    assert "no readable text" in note.body and "are being monitored" not in note.body
