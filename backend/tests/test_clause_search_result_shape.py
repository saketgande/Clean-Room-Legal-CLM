"""Clause search tells the page how sure the reading is in WORDS, never a number.

``ClauseExtraction.confidence`` is a short string ("high" / "medium" / "low"). The clause-search
page did ``Math.round(confidence * 100)`` on it and printed "NaN%" on every row. The page now shows
the word; this pins the other half, so the two sides cannot drift apart again.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.core.database import engine, new_uuid
from app.organizations.models import Organization
from app.search.service import SearchService


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


def _world(db, confidence):
    org = Organization(id=new_uuid(), name="Search Co", slug=f"search-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"s-{uuid.uuid4().hex[:6]}@example.com", full_name="Searcher",
                hashed_password="h", status="active")
    db.add(user)
    db.flush()
    contract = Contract(org_id=org.id, title="MSA", owner_user_id=user.id, created_by_user_id=user.id)
    db.add(contract)
    db.flush()
    file = ContractFile(org_id=org.id, contract_id=contract.id, file_label="MSA.docx")
    db.add(file)
    db.flush()
    stored = StorageObject(org_id=org.id, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
                           mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
    db.add(stored)
    db.flush()
    version = ContractVersion(org_id=org.id, contract_id=contract.id, contract_file_id=file.id, version_number=1,
                              storage_object_id=stored.id, source="upload", is_authoritative=True)
    db.add(version)
    db.flush()
    snap = ContractTextSnapshot(org_id=org.id, contract_id=contract.id, contract_version_id=version.id,
                                extraction_method="test", text="Governing law is England.")
    db.add(snap)
    db.flush()
    db.add(ClauseExtraction(org_id=org.id, contract_id=contract.id, contract_version_id=version.id,
                            text_snapshot_id=snap.id, clause_type="governing_law", heading="Governing law",
                            text="Governing law is England.", confidence=confidence))
    db.flush()
    return user


@pytest.mark.parametrize("confidence", ["high", "medium", "low"])
def test_a_clause_hit_carries_confidence_as_a_word(db, confidence):
    user = _world(db, confidence)

    hits = SearchService(db).search_clauses(current_user=user, clause_type="governing_law")

    assert [h["confidence"] for h in hits] == [confidence]
    assert not isinstance(hits[0]["confidence"], (int, float))


def test_a_clause_hit_with_no_stated_confidence_says_none_rather_than_zero(db):
    user = _world(db, None)

    hits = SearchService(db).search_clauses(current_user=user, clause_type="governing_law")

    assert hits[0]["confidence"] is None
