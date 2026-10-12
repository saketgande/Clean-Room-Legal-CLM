"""The review checklist says when the contract text still has fill-in blanks.

The gate to approval counts decisions, not text, so a contract with "[INSERT CORRECT EXPIRY
DATE]" in clause 7.1 looked ready. The blanks item is advisory here (approvers see the text);
the hard stop is at sending for signature (signatures.service.ensure_text_is_complete).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.contracts.service import ContractService
from app.core.database import engine
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


def _contract_with_text(db: Session, text: str | None) -> Contract:
    org = Organization(name="Blanks org", slug=f"bl-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    owner = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Owner",
                 hashed_password="h", status="active")
    db.add(owner)
    db.flush()
    contract = Contract(org_id=org.id, title="MSA", owner_user_id=owner.id, lifecycle_stage="review")
    db.add(contract)
    db.flush()
    file = ContractFile(org_id=org.id, contract_id=contract.id, file_label="MSA.docx")
    db.add(file)
    db.flush()
    stored = StorageObject(org_id=org.id, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
                           mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
    db.add(stored)
    db.flush()
    version = ContractVersion(org_id=org.id, contract_id=contract.id, contract_file_id=file.id,
                              version_number=1, storage_object_id=stored.id, source="upload",
                              is_authoritative=True)
    db.add(version)
    db.flush()
    if text is not None:
        snap = ContractTextSnapshot(org_id=org.id, contract_id=contract.id, contract_version_id=version.id,
                                    extraction_method="test", extraction_quality_score=1.0, text=text,
                                    validation_status="complete")
        db.add(snap)
        db.flush()
        version.text_snapshot_id = snap.id
    contract.current_authoritative_version_id = version.id
    db.flush()
    return contract


def _item(db, contract, key="blanks"):
    status = ContractService(db).compute_review_status(contract=contract)
    return next((c for c in status["checklist"] if c["key"] == key), None), status


def test_a_blank_in_the_current_text_is_listed(db):
    contract = _contract_with_text(db, "7.1 This Agreement ends on [INSERT CORRECT EXPIRY DATE]. See also [●].")
    item, status = _item(db, contract)
    assert item["status"] == "todo" and item["count"] == 2
    assert "[INSERT CORRECT EXPIRY DATE]" in item["detail"]
    assert status["blanks"] == ["[INSERT CORRECT EXPIRY DATE]", "[●]"]


def test_clean_text_shows_the_item_as_done(db):
    contract = _contract_with_text(db, "7.1 This Agreement ends on 1 January 2027. [Intentionally omitted]")
    item, status = _item(db, contract)
    assert item["status"] == "done" and item["count"] == 0 and status["blanks"] == []


def test_a_blank_is_advice_here_it_does_not_stop_approval(db):
    contract = _contract_with_text(db, "Ends on [TBD].")
    _, status = _item(db, contract)
    assert status["ready_for_approval"] is True


def test_a_contract_with_no_text_yet_has_no_blank_item_problem(db):
    contract = _contract_with_text(db, None)
    item, status = _item(db, contract)
    assert item["status"] == "done" and status["blanks"] == []
