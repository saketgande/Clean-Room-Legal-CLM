"""The request page's "Approval chain" must read the approvals that were really made.

A request that already has a contract is approved ON THE CONTRACT: the approval rows carry
``contract_id`` and no ``intake_request_id``. The page's chain used to look only at
``intake_request_id``, found nothing, and showed the workflow's *plan* ("Finance - waiting,
0/1") for a request whose Finance approval was done and which was already at Signature.

The chain now follows the contract's latest submission for its current version, the same
rows the approval gate itself uses (``_current_version_approved``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approvals.models import ApprovalRequest
from app.auth.models import User
from app.contract_files.models import ContractFile, ContractVersion, StorageObject
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import ApprovalStatus
from app.intake import service as intake_service
from app.intake.models import IntakeRequest
from app.organizations.models import Organization
from app.workflows.models import WorkflowRun


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
    org = Organization(id=new_uuid(), name="Chain Co", slug=f"chain-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db, org_id, label):
    u = User(org_id=org_id, email=f"{label}-{uuid.uuid4().hex[:6]}@example.com", full_name=label,
             hashed_password="h", status="active")
    db.add(u)
    db.flush()
    return u


class World:
    """A requester, a request, its contract and the workflow run that joins them."""

    def __init__(self, db, *, with_contract=True):
        self.db = db
        self.org = _org(db)
        self.requester = _user(db, self.org.id, "requester")
        self.approver = _user(db, self.org.id, "approver")
        self.contract = None
        if with_contract:
            self.contract = Contract(org_id=self.org.id, title="MSA", owner_user_id=self.requester.id)
            db.add(self.contract)
            db.flush()
            self.old_version = self._version(1)
            self.current_version = self._version(2)
            self.contract.current_authoritative_version_id = self.current_version.id
            db.flush()
        self.request = IntakeRequest(
            org_id=self.org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form",
            requester_user_id=self.requester.id, type_label="New agreement Request", description="t",
            field_values={}, priority="Medium", status="open", stage="new", submitted_at=utcnow(),
            created_by_user_id=self.requester.id, updated_by_user_id=self.requester.id,
            contract_id=self.contract.id if self.contract else None,
        )
        db.add(self.request)
        db.flush()
        db.add(WorkflowRun(
            org_id=self.org.id, request_id=self.request.id, flow_name="Ladder", flow_version=1,
            steps=[{"id": "s0", "type": "approval", "name": "Finance approval",
                    "config": {"assign_by": "Specific person", "assignee_user_id": self.approver.id}}],
            status="waiting", current_index=0, contract_id=self.contract.id if self.contract else None,
        ))
        db.flush()

    def _version(self, number):
        file = ContractFile(org_id=self.org.id, contract_id=self.contract.id, file_label=f"MSA v{number}.docx")
        self.db.add(file)
        self.db.flush()
        stored = StorageObject(org_id=self.org.id, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
                               mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
        self.db.add(stored)
        self.db.flush()
        version = ContractVersion(org_id=self.org.id, contract_id=self.contract.id, contract_file_id=file.id,
                                  version_number=number, storage_object_id=stored.id, source="upload",
                                  is_authoritative=(number == 2))
        self.db.add(version)
        self.db.flush()
        return version

    def approval(self, *, status, batch, age_minutes=0, old_version=False, on_contract=True):
        version = None
        if self.contract is not None:
            version = self.old_version if old_version else self.current_version
        row = ApprovalRequest(
            org_id=self.org.id,
            contract_id=self.contract.id if (on_contract and self.contract) else None,
            intake_request_id=None if (on_contract and self.contract) else self.request.id,
            contract_version_id=version.id if (version and on_contract) else None, status=status, requested_by_user_id=self.requester.id,
            approver_user_id=self.approver.id, step_order=1, mode="any",
            due_at=datetime.now(UTC) + timedelta(days=3), metadata_json={"submission_batch_id": batch},
            created_by_user_id=self.requester.id, updated_by_user_id=self.requester.id,
        )
        self.db.add(row)
        self.db.flush()
        row.created_at = row.created_at - timedelta(minutes=age_minutes)
        self.db.flush()
        return row

    def chain(self):
        return intake_service.get_approval_chain(self.db, actor=self.requester, request_id=self.request.id)


def test_an_approval_made_on_the_contract_shows_on_the_request(db):
    w = World(db)
    w.approval(status=ApprovalStatus.APPROVED, batch="b1")

    chain = w.chain()

    assert [(r["status"], r["approval_request_id"] is not None) for r in chain] == [("approved", True)]


def test_a_rejection_that_was_resubmitted_and_approved_shows_only_the_latest_submission(db):
    w = World(db)
    w.approval(status=ApprovalStatus.REJECTED, batch="old", age_minutes=30)
    new = w.approval(status=ApprovalStatus.APPROVED, batch="new")

    chain = w.chain()

    assert [r["approval_request_id"] for r in chain] == [new.id]
    assert chain[0]["status"] == "approved"


def test_a_pending_submission_shows_who_it_is_with(db):
    w = World(db)
    w.approval(status=ApprovalStatus.PENDING, batch="b1")

    chain = w.chain()

    assert [r["status"] for r in chain] == ["pending"]
    assert chain[0]["approver_label"] == "approver"


def test_cancelled_rows_are_not_part_of_the_chain(db):
    w = World(db)
    w.approval(status=ApprovalStatus.CANCELLED, batch="b0", age_minutes=10)
    live = w.approval(status=ApprovalStatus.PENDING, batch="b1")

    assert [r["approval_request_id"] for r in w.chain()] == [live.id]


def test_an_approval_of_an_older_version_does_not_count_for_the_current_one(db):
    """The gate wants the CURRENT version approved, so the page must not say "approved"
    for a version that has been replaced; it falls back to the plan instead."""
    w = World(db)
    w.approval(status=ApprovalStatus.APPROVED, batch="b1", old_version=True)

    assert [r["status"] for r in w.chain()] == ["planned"]


def test_a_request_with_no_contract_still_reads_its_own_approvals(db):
    w = World(db, with_contract=False)
    row = w.approval(status=ApprovalStatus.PENDING, batch="b1", on_contract=False)

    chain = w.chain()

    assert [r["approval_request_id"] for r in chain] == [row.id]
