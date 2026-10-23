"""Only an approved version may go out for signature.

Approval belongs to a VERSION, but the signature gate looked only at the contract's STAGE. A
contract approved at V4 and then edited (V5, never approved) stayed at Signature and could be sent
with the new text: nobody had approved what the signers saw. The stage stays where it is; sending
is what now refuses, in both places that send (the Signatures page and the chat).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approvals.models import ApprovalRequest
from app.auth.models import User
from app.contract_files.models import ContractFile, ContractVersion, StorageObject
from app.contracts.lifecycle import version_approval_state
from app.contracts.models import Contract
from app.core.database import engine, new_uuid
from app.core.enums import ApprovalStatus
from app.organizations.models import Organization
from app.signatures.service import SignaturesService


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
        self.org = Organization(id=new_uuid(), name="Sig Co", slug=f"sig-co-{uuid.uuid4().hex[:8]}")
        db.add(self.org)
        db.flush()
        self.owner = User(org_id=self.org.id, email=f"o-{uuid.uuid4().hex[:6]}@example.com", full_name="Owner",
                          hashed_password="h", status="active")
        db.add(self.owner)
        db.flush()
        self.contract = Contract(org_id=self.org.id, title="MSA", owner_user_id=self.owner.id,
                                 lifecycle_stage="signature")
        db.add(self.contract)
        db.flush()
        self.file = ContractFile(org_id=self.org.id, contract_id=self.contract.id, file_label="MSA.docx")
        db.add(self.file)
        db.flush()
        self.v1 = self.version(1)
        self.v2 = self.version(2)
        self.contract.current_authoritative_version_id = self.v2.id
        db.flush()

    def version(self, number):
        stored = StorageObject(org_id=self.org.id, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
                               mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
        self.db.add(stored)
        self.db.flush()
        v = ContractVersion(org_id=self.org.id, contract_id=self.contract.id, contract_file_id=self.file.id,
                            version_number=number, storage_object_id=stored.id, source="upload",
                            is_authoritative=(number == 2))
        self.db.add(v)
        self.db.flush()
        return v

    def approval(self, version, status, *, batch="b1", age=0):
        row = ApprovalRequest(
            org_id=self.org.id, contract_id=self.contract.id, contract_version_id=version.id, status=status,
            requested_by_user_id=self.owner.id, approver_user_id=self.owner.id, step_order=1, mode="any",
            due_at=datetime.now(UTC) + timedelta(days=3), metadata_json={"submission_batch_id": batch},
            created_by_user_id=self.owner.id, updated_by_user_id=self.owner.id,
        )
        self.db.add(row)
        self.db.flush()
        row.created_at = row.created_at - timedelta(minutes=age)
        self.db.flush()
        return row

    def state(self, version=None):
        return version_approval_state(self.db, self.contract, version.id if version else None)

    def guard(self, version=None, *, override=False):
        SignaturesService(self.db).ensure_version_is_approved(
            contract=self.contract, version=version or self.v2, override=override)


# --- where approval stands for a version --------------------------------------

def test_a_contract_that_never_went_through_approval_has_nothing_out_of_date(db):
    assert World(db).state() == "none"


def test_a_fully_approved_version_is_approved(db):
    w = World(db)
    w.approval(w.v2, ApprovalStatus.APPROVED)
    assert w.state() == "approved"


def test_a_version_edited_after_approval_is_changed(db):
    w = World(db)
    w.approval(w.v1, ApprovalStatus.APPROVED)  # approved at V1, now on V2
    assert w.state() == "changed"
    assert w.state(w.v1) == "approved"


def test_a_version_with_the_approvers_is_pending_and_a_rejected_one_is_rejected(db):
    w = World(db)
    w.approval(w.v2, ApprovalStatus.PENDING)
    assert w.state() == "pending"
    w2 = World(db)
    w2.approval(w2.v2, ApprovalStatus.REJECTED)
    assert w2.state() == "rejected"


def test_a_rejection_that_was_resubmitted_and_approved_is_approved(db):
    w = World(db)
    w.approval(w.v2, ApprovalStatus.REJECTED, batch="old", age=30)
    w.approval(w.v2, ApprovalStatus.APPROVED, batch="new")
    assert w.state() == "approved"


def test_cancelled_rows_do_not_count_as_ever_having_been_through_approval(db):
    w = World(db)
    w.approval(w.v2, ApprovalStatus.CANCELLED)
    assert w.state() == "none"


# --- the guard on sending -----------------------------------------------------

def test_an_approved_version_may_be_sent(db):
    w = World(db)
    w.approval(w.v2, ApprovalStatus.APPROVED)
    w.guard()


def test_a_contract_that_never_needed_approval_may_be_sent(db):
    World(db).guard()


def test_a_version_edited_after_approval_may_not_be_sent_and_the_message_says_why(db):
    w = World(db)
    w.approval(w.v1, ApprovalStatus.APPROVED)
    with pytest.raises(HTTPException) as exc:
        w.guard()
    assert exc.value.status_code == 409
    assert "changed after the contract was approved" in exc.value.detail
    assert "approved again" in exc.value.detail


@pytest.mark.parametrize("status,words", [(ApprovalStatus.PENDING, "waiting for approval"),
                                          (ApprovalStatus.REJECTED, "was rejected")])
def test_a_version_still_with_the_approvers_or_rejected_may_not_be_sent(db, status, words):
    w = World(db)
    w.approval(w.v2, status)
    with pytest.raises(HTTPException) as exc:
        w.guard()
    assert words in exc.value.detail


def test_someone_who_may_override_the_signature_gate_may_send_it_anyway(db):
    w = World(db)
    w.approval(w.v1, ApprovalStatus.APPROVED)
    w.guard(override=True)


# --- it runs in both places that send, before anything is dispatched ----------

def test_both_send_paths_check_approval_before_dispatching():
    import inspect

    from app.ai import tool_runtime
    from app.signatures import routes

    for source in (inspect.getsource(routes.send_for_signature),
                   inspect.getsource(tool_runtime.ToolRuntime._send_for_signature)):
        assert "ensure_version_is_approved" in source
        assert source.index("ensure_version_is_approved") < source.index("create_envelope")


# --- the page can say it, too ---------------------------------------------------

def test_the_review_status_says_where_approval_stands_for_the_version_on_screen(db):
    from app.contracts.service import ContractService

    w = World(db)
    assert ContractService(db).compute_review_status(contract=w.contract)["version_approval"] == "none"
    w.approval(w.v1, ApprovalStatus.APPROVED)
    assert ContractService(db).compute_review_status(contract=w.contract)["version_approval"] == "changed"
    w.approval(w.v2, ApprovalStatus.APPROVED, batch="b2")
    assert ContractService(db).compute_review_status(contract=w.contract)["version_approval"] == "approved"
