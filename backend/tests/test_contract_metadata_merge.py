"""A contract's metadata_json is written by a dozen code paths that each read it, edit it and assign the whole
thing back, from sessions that keep their instances after a commit. The save now applies only what its own
session changed (contracts/models.py). Two sessions share one rolled-back transaction here."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contracts.models import Contract, merge_json
from app.core.database import engine
from app.organizations.models import Organization


@pytest.fixture
def sessions():
    connection = engine.connect()
    trans = connection.begin()
    mine = Session(bind=connection, expire_on_commit=False)
    theirs = Session(bind=connection, expire_on_commit=False)
    try:
        yield mine, theirs
    finally:
        mine.close()
        theirs.close()
        trans.rollback()
        connection.close()


def _contract(db: Session, meta: dict) -> Contract:
    org = Organization(name="Merge org", slug=f"mg-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    owner = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Em Erge",
                 hashed_password="hash", status="active")
    db.add(owner)
    db.flush()
    contract = Contract(org_id=org.id, title="MSA", owner_user_id=owner.id, metadata_json=meta)
    db.add(contract)
    db.flush()
    return contract


def _stored(db: Session, contract: Contract) -> dict:
    db.expire_all()
    return db.get(Contract, contract.id).metadata_json


def test_a_stale_copy_dropping_one_flag_keeps_the_provenance_another_session_saved(sessions):
    """Guards the audit's wipe: _maybe_auto_review read the contract, ran a 4-minute review, then popped
    its flags from that old copy and saved it, erasing field_sources and the AI's suggestions the
    metadata job wrote meanwhile - so an AI-guessed expiry became a trusted anchor."""
    mine, theirs = sessions
    contract = _contract(mine, {"auto_review_pending": True, "field_sources": {}})
    other = theirs.get(Contract, contract.id)
    other.metadata_json = {
        **other.metadata_json,
        "field_sources": {"expiration_date": "ai"},
        "ai_suggestions": {"metadata": {"expiration_date": "2029-10-16"}},
    }
    theirs.flush()

    stale = dict(contract.metadata_json)
    stale.pop("auto_review_pending")
    contract.metadata_json = stale
    mine.flush()

    expected = {"field_sources": {"expiration_date": "ai"},
                "ai_suggestions": {"metadata": {"expiration_date": "2029-10-16"}}}
    assert _stored(theirs, contract) == expected
    assert contract.metadata_json == expected  # this session's own copy is brought up to date too


def test_two_sessions_marking_different_fields_both_keep_their_mark(sessions):
    """Guards field_sources, one dict that every writer replaces: a person's mark and the AI's mark
    saved from two sessions must both survive."""
    mine, theirs = sessions
    contract = _contract(mine, {"field_sources": {}})
    other = theirs.get(Contract, contract.id)
    other.metadata_json = {**other.metadata_json, "field_sources": {"effective_date": "user"}}
    theirs.flush()

    contract.metadata_json = {**contract.metadata_json, "field_sources": {"expiration_date": "ai"}}
    mine.flush()

    assert _stored(theirs, contract) == {"field_sources": {"effective_date": "user", "expiration_date": "ai"}}


def test_a_save_nobody_raced_is_written_as_is(sessions):
    """Guards the merge changing an ordinary save: with no other writer the value is exactly what was set."""
    mine, theirs = sessions
    contract = _contract(mine, {"a": 1, "field_sources": {"x": "ai"}})
    contract.metadata_json = {"a": 2, "field_sources": {}}
    mine.flush()
    assert _stored(theirs, contract) == {"a": 2, "field_sources": {}}


@pytest.mark.parametrize("base, mine, theirs, expected", [
    ({"a": 1}, {}, {"a": 1, "b": 2}, {"b": 2}),                                    # I removed a, they added b
    ({"a": 1}, {"a": 1}, {"a": 5}, {"a": 5}),                                      # I left it alone: theirs stays
    ({"a": 1}, {"a": 2}, {"a": 5}, {"a": 2}),                                      # we both changed it: mine wins
    ({"s": {"x": 1, "y": 2}}, {"s": {"x": 1}}, {"s": {"x": 1, "y": 3, "z": 4}},
     {"s": {"x": 1, "z": 4}}),                                                     # I removed s.y, they added s.z
    ({"l": [1]}, {"l": [1, 2]}, {"l": [1, 9]}, {"l": [1, 2]}),                    # lists are values, not merged
])
def test_merge_json_applies_only_what_this_side_changed(base, mine, theirs, expected):
    """Guards the three-way rule itself, at every depth objects reach."""
    assert merge_json(base, mine, theirs) == expected
