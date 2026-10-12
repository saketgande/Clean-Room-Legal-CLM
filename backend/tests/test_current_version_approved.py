"""Is a contract's current version approved?  (Bug 1: stuck after reject + resubmit)

`_current_version_approved` gates Approval -> Signature, and it is what a
workflow's approval step waits on. It used to require EVERY non-cancelled approval
row of the version to be approved. A rejected row is never cancelled, so after

    reject  ->  resubmit the same version  ->  approve

the old REJECTED row sat beside the new APPROVED one and the contract could never
leave Approval: "Waiting for the approvers" with nobody left to approve.

The rule is now: the version is approved when its LATEST submission (the rows
created together, sharing a submission_batch_id) has every live rung approved.
Earlier submissions are history.

Real database, like the rest of the db_session tests: rows are flushed, never
committed, and rolled back when the test ends.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app.models  # noqa: F401  (register every mapper)
from app.approvals.models import ApprovalRequest
from app.approvals.submissions import latest_submission
from app.auth.models import User
from app.contract_files.models import ContractFile, ContractVersion, StorageObject
from app.contracts.lifecycle import _current_version_approved
from app.contracts.models import Contract
from app.core.enums import ApprovalStatus

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ORG = "org-bug1-test"


# --------------------------------------------------------------------------- builders

def _contract_with_version(db, *, version_number=1):
    """A contract with one authoritative version — the minimum the approval rows'
    foreign keys need. Flushed only; the db_session fixture rolls it back."""
    tag = uuid.uuid4().hex[:10]
    user = User(email=f"bug1-{tag}@example.com", full_name="Bug One", hashed_password="x", org_id=ORG)
    db.add(user)
    db.flush()
    contract = Contract(title=f"Bug 1 {tag}", owner_user_id=user.id, org_id=ORG)
    db.add(contract)
    db.flush()
    stored = StorageObject(
        storage_key=f"bug1/{tag}", filename="c.docx", mime_type="application/octet-stream",
        size_bytes=1, sha256_hash=tag, org_id=ORG,
    )
    cfile = ContractFile(contract_id=contract.id, file_label="main", org_id=ORG)
    db.add_all([stored, cfile])
    db.flush()
    version = ContractVersion(
        contract_id=contract.id, contract_file_id=cfile.id, version_number=version_number,
        storage_object_id=stored.id, org_id=ORG,
    )
    db.add(version)
    db.flush()
    contract.current_authoritative_version_id = version.id
    db.flush()
    return contract, version, user


def _rung(db, contract, version, user, status, *, batch, step=1, minutes=0, seconds=0):
    """One approval row. `minutes`/`seconds` place it on the timeline so 'which
    submission is the latest' never depends on how fast the test ran."""
    meta = {"submission_batch_id": batch} if batch else {}
    row = ApprovalRequest(
        org_id=ORG, contract_id=contract.id, contract_version_id=version.id,
        requested_by_user_id=user.id, status=status, step_order=step, metadata_json=meta,
        created_at=T0 + timedelta(minutes=minutes, seconds=seconds),
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- the bug

def test_approved_when_the_latest_submission_is_approved_after_an_earlier_rejection(db_session):
    """The reported bug: reject, resubmit the SAME version, approve."""
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.REJECTED, batch="first", minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="second", minutes=5)
    assert _current_version_approved(db_session, c) is True


def test_the_same_holds_for_a_two_rung_chain_resubmitted_after_a_rejection(db_session):
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="first", step=1, minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.REJECTED, batch="first", step=2, seconds=1)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="second", step=1, minutes=5)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="second", step=2, minutes=5, seconds=1)
    assert _current_version_approved(db_session, c) is True


# --------------------------------------------------------------------------- must stay blocked

def test_a_rejection_inside_the_latest_submission_still_blocks(db_session):
    """Guards the naive fix. Rung 1 approved + rung 2 rejected in the SAME
    submission must NOT look approved, so rejected rows can't simply be ignored."""
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="only", step=1, minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.REJECTED, batch="only", step=2, seconds=1)
    assert _current_version_approved(db_session, c) is False


def test_a_pending_resubmission_blocks_even_though_an_earlier_submission_was_approved(db_session):
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="first", minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.PENDING, batch="second", minutes=5)
    assert _current_version_approved(db_session, c) is False


def test_a_resubmission_after_a_rejection_blocks_until_it_is_approved(db_session):
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.REJECTED, batch="first", minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.PENDING, batch="second", minutes=5)
    assert _current_version_approved(db_session, c) is False


def test_every_rung_of_the_latest_submission_must_be_approved(db_session):
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="only", step=1, minutes=0)
    waiting = _rung(db_session, c, v, u, ApprovalStatus.WAITING, batch="only", step=2, seconds=1)
    assert _current_version_approved(db_session, c) is False
    waiting.status = ApprovalStatus.APPROVED
    db_session.flush()
    assert _current_version_approved(db_session, c) is True


def test_no_approval_rows_is_not_approved(db_session):
    c, _v, _u = _contract_with_version(db_session)
    assert _current_version_approved(db_session, c) is False


def test_cancelled_rows_never_count_for_or_against(db_session):
    """Unchanged behaviour: a cancelled rung is neither approved nor blocking, and a
    cancelled newer submission does not hide an approved earlier one."""
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch="first", minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.CANCELLED, batch="second", minutes=5)
    assert _current_version_approved(db_session, c) is True


def test_approvals_of_another_version_do_not_count(db_session):
    c, v1, u = _contract_with_version(db_session)
    _rung(db_session, c, v1, u, ApprovalStatus.APPROVED, batch="old-version", minutes=0)
    # The contract now points at a newer version nobody has approved.
    tag = uuid.uuid4().hex[:8]
    stored = StorageObject(
        storage_key=f"bug1/{tag}", filename="c2.docx", mime_type="application/octet-stream",
        size_bytes=1, sha256_hash=tag, org_id=ORG,
    )
    db_session.add(stored)
    db_session.flush()
    v2 = ContractVersion(
        contract_id=c.id, contract_file_id=v1.contract_file_id, version_number=2,
        storage_object_id=stored.id, org_id=ORG,
    )
    db_session.add(v2)
    db_session.flush()
    c.current_authoritative_version_id = v2.id
    db_session.flush()
    assert _current_version_approved(db_session, c) is False


# --------------------------------------------------------------------------- older rows without a batch id

def test_legacy_rows_without_a_batch_id_use_the_newest_chain(db_session):
    """Chains created before submission_batch_id existed have no id; they are told
    apart by when they were created, as the chain-progress view already does."""
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.REJECTED, batch=None, minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch=None, minutes=60)
    assert _current_version_approved(db_session, c) is True


def test_legacy_rungs_created_together_are_one_chain(db_session):
    c, v, u = _contract_with_version(db_session)
    _rung(db_session, c, v, u, ApprovalStatus.APPROVED, batch=None, step=1, minutes=0)
    _rung(db_session, c, v, u, ApprovalStatus.WAITING, batch=None, step=2, seconds=2)
    assert _current_version_approved(db_session, c) is False


# --------------------------------------------------------------------------- the helper alone (no database)

def _row(status, *, batch=None, seconds=0):
    return SimpleNamespace(
        status=status,
        created_at=T0 + timedelta(seconds=seconds),
        metadata_json={"submission_batch_id": batch} if batch else {},
    )


def test_latest_submission_of_nothing_is_nothing():
    assert latest_submission([]) == []


def test_latest_submission_picks_the_rows_of_the_newest_batch():
    old = _row("rejected", batch="a", seconds=0)
    new1 = _row("approved", batch="b", seconds=100)
    new2 = _row("pending", batch="b", seconds=101)
    assert latest_submission([old, new1, new2]) == [new1, new2]


def test_latest_submission_does_not_depend_on_input_order():
    old = _row("rejected", batch="a", seconds=0)
    new = _row("approved", batch="b", seconds=100)
    assert latest_submission([new, old]) == [new]


def test_a_new_batch_is_not_mixed_with_older_rows_that_have_no_batch_id():
    legacy = _row("rejected", batch=None, seconds=0)
    new = _row("approved", batch="b", seconds=100)
    assert latest_submission([legacy, new]) == [new]


def test_legacy_rows_further_than_the_window_apart_are_separate_chains():
    first = _row("rejected", batch=None, seconds=0)
    second = _row("approved", batch=None, seconds=3600)
    assert latest_submission([first, second]) == [second]


@pytest.mark.parametrize("gap", [0, 5, 10])
def test_legacy_rows_within_the_window_are_one_chain(gap):
    first = _row("approved", batch=None, seconds=0)
    second = _row("waiting", batch=None, seconds=gap)
    assert latest_submission([first, second]) == [first, second]
