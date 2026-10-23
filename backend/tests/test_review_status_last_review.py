"""review-status's last_review: what the newest playbook run really was.

The contract page used to say "I reviewed this draft … it's clean" with no signal that a
playbook review had run at all, so a never-reviewed contract and one whose AI review failed
over to a keyword scan both read as a clean review. last_review is the signal the page now
reads; these checks pin it against real rows (rolled back afterwards).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contract_files.models import ContractFile, ContractVersion, StorageObject
from app.contracts.models import Contract
from app.contracts.service import ContractService
from app.core.database import engine, utcnow
from app.organizations.models import Organization
from app.playbooks.models import Playbook, PlaybookRun, PlaybookVersion


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
    """A contract with two versions and a playbook to run against it."""

    def __init__(self, db: Session):
        self.db = db
        org = Organization(name="Last review test org", slug=f"lr-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org = org.id
        owner = User(
            org_id=self.org, email=f"{uuid.uuid4().hex[:8]}@example.com",
            full_name="Last review tester", hashed_password="hash", status="active",
        )
        db.add(owner)
        db.flush()
        self.contract = Contract(org_id=self.org, title="MSA", owner_user_id=owner.id)
        db.add(self.contract)
        db.flush()
        self.file = ContractFile(org_id=self.org, contract_id=self.contract.id, file_label="MSA.docx")
        db.add(self.file)
        db.flush()
        self.v1, self.v2 = self._version(1), self._version(2)
        self.contract.current_authoritative_version_id = self.v2.id
        self.playbook = Playbook(org_id=self.org, name="MSA playbook")
        db.add(self.playbook)
        db.flush()
        self.pv = PlaybookVersion(
            org_id=self.org, playbook_id=self.playbook.id, version_number=1, status="published"
        )
        db.add(self.pv)
        db.flush()

    def _version(self, number: int) -> ContractVersion:
        stored = StorageObject(
            org_id=self.org, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
            mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex,
        )
        self.db.add(stored)
        self.db.flush()
        version = ContractVersion(
            org_id=self.org, contract_id=self.contract.id, contract_file_id=self.file.id,
            version_number=number, storage_object_id=stored.id,
        )
        self.db.add(version)
        self.db.flush()
        return version

    def run(self, *, version: ContractVersion, model: str, status: str, age_minutes: int,
            org_id: str | None = None) -> PlaybookRun:
        row = PlaybookRun(
            org_id=org_id or self.org, playbook_id=self.playbook.id, playbook_version_id=self.pv.id,
            contract_id=self.contract.id, contract_version_id=version.id, model_name=model,
            status=status, created_at=utcnow() - timedelta(minutes=age_minutes),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def last_review(self) -> dict | None:
        return ContractService(self.db).compute_review_status(contract=self.contract)["last_review"]


def test_a_contract_never_reviewed_reports_no_review(db):
    """A contract that was never reviewed must not report a review."""
    assert World(db).last_review() is None


def test_a_keyword_fallback_is_not_reported_as_an_ai_review(db):
    """The keyword fallback after an AI failure must not be shown as an AI review, whatever its
    status says (older rows were saved as 'succeeded')."""
    w = World(db)
    w.run(version=w.v2, model="deterministic_playbook_engine", status="succeeded", age_minutes=1)
    assert w.last_review() == {"status": "succeeded", "source": "keyword", "contract_version_id": w.v2.id, "on_current_text": True}


def test_the_newest_run_is_the_one_reported(db):
    """The page must judge the latest run, not any run: an old AI review on v1 must not hide a newer
    keyword scan, and vice versa."""
    w = World(db)
    w.run(version=w.v1, model="claude", status="succeeded", age_minutes=10)
    w.run(version=w.v2, model="deterministic_playbook_engine", status="keyword_only", age_minutes=5)
    assert w.last_review()["source"] == "keyword"
    w.run(version=w.v2, model="claude", status="succeeded", age_minutes=1)
    assert w.last_review() == {"status": "succeeded", "source": "ai", "contract_version_id": w.v2.id, "on_current_text": True}


def test_a_run_from_another_org_is_ignored(db):
    """The review lookup stays org-scoped: a run carrying another org's id is not this contract's review."""
    w = World(db)
    w.run(version=w.v1, model="deterministic_playbook_engine", status="keyword_only", age_minutes=10)
    w.run(version=w.v2, model="claude", status="succeeded", age_minutes=1, org_id=str(uuid.uuid4()))
    assert w.last_review()["source"] == "keyword"
