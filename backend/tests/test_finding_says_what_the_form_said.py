"""A finding that contradicts the request form says so (round 2, bug 42).

The form said "No" to personal data, yet the review (which reads the contract, not the form) raised a high
data-protection finding with no mention of that answer. The finding is kept, since a requester's "No" can be
wrong, but it now carries what the form said so the reader can see the disagreement instead of guessing at it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contract_files.models import ContractFile, ContractVersion, StorageObject
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.intake.models import IntakeRequest
from app.organizations.models import Organization
from app.playbooks.models import Playbook, PlaybookRun, PlaybookVersion
from app.playbooks.service import EvaluatedDeviation, _create_deviation, form_context_note

NOTE = "The request form says no personal data is shared or processed."


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


class World:
    def __init__(self, db):
        self.db = db
        self.org = Organization(id=new_uuid(), name="Form Co", slug=f"form-co-{uuid.uuid4().hex[:8]}")
        db.add(self.org)
        db.flush()
        self.user = User(org_id=self.org.id, email=f"u-{uuid.uuid4().hex[:6]}@example.com", full_name="U",
                         hashed_password="h", status="active")
        db.add(self.user)
        db.flush()
        self.contract = Contract(org_id=self.org.id, title="MSA", owner_user_id=self.user.id)
        db.add(self.contract)
        db.flush()
        file = ContractFile(org_id=self.org.id, contract_id=self.contract.id, file_label="MSA.docx")
        db.add(file)
        db.flush()
        stored = StorageObject(org_id=self.org.id, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
                               mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
        db.add(stored)
        db.flush()
        self.version = ContractVersion(org_id=self.org.id, contract_id=self.contract.id, contract_file_id=file.id,
                                       version_number=1, storage_object_id=stored.id, source="upload",
                                       is_authoritative=True)
        db.add(self.version)
        db.flush()
        playbook = Playbook(org_id=self.org.id, name="PB")
        db.add(playbook)
        db.flush()
        pv = PlaybookVersion(org_id=self.org.id, playbook_id=playbook.id, version_number=1)
        db.add(pv)
        db.flush()
        self.run = PlaybookRun(org_id=self.org.id, playbook_id=playbook.id, playbook_version_id=pv.id,
                               contract_id=self.contract.id, contract_version_id=self.version.id, status="running")
        db.add(self.run)
        db.flush()

    def request(self, **field_values):
        row = IntakeRequest(
            org_id=self.org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=self.user.id,
            type_label="New agreement Request", description="t", field_values=field_values, priority="Medium",
            status="open", stage="new", submitted_at=utcnow(), created_by_user_id=self.user.id,
            updated_by_user_id=self.user.id, contract_id=self.contract.id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def finding(self, clause_type="data_protection", issue="No Compliance sign-off on record."):
        evaluated = EvaluatedDeviation(rule=None, severity="high", clause_type=clause_type, issue=issue,
                                       suggested_fix=None, citation=None, original_text=None, replacement_text=None)
        return _create_deviation(self.db, user=self.user, run=self.run, contract=self.contract, evaluated=evaluated)


def test_a_data_protection_finding_says_the_form_answered_no(db):
    w = World(db)
    w.request(personal_data="No")
    row = w.finding()
    assert row.issue.startswith("No Compliance sign-off on record. ")
    assert NOTE in row.issue
    assert "Check that with the requester" in row.issue
    assert row.severity == "high"  # kept, not softened or dropped


def test_it_adds_nothing_when_the_form_said_yes(db):
    w = World(db)
    w.request(personal_data="Yes")
    assert w.finding().issue == "No Compliance sign-off on record."


def test_it_adds_nothing_when_no_request_asked_the_question(db):
    w = World(db)
    assert w.finding().issue == "No Compliance sign-off on record."
    w.request(why="an amendment has no personal data question")
    assert w.finding().issue == "No Compliance sign-off on record."


def test_it_only_speaks_about_the_topic_the_form_answered(db):
    w = World(db)
    w.request(personal_data="No")
    assert w.finding(clause_type="limitation_of_liability", issue="Cap too low.").issue == "Cap too low."


def test_the_newest_request_that_asked_wins(db):
    w = World(db)
    older = w.request(personal_data="No")
    older.created_at = older.created_at.replace(year=older.created_at.year - 1)
    w.request(personal_data="Yes")
    assert form_context_note(db, contract=w.contract, clause_type="data_protection") is None
