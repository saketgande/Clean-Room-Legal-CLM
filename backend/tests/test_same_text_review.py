"""A review counts for the versions right after it that carry the same words.

A signed copy (or a no-op editor save) is a new version with byte-identical text, so
the review of the version it copied (and so the risk band computed from it) is still a
review of the text now current. Only CONSECUTIVE identical versions count: a restore of older text must not
inherit that text's old review, because its issues may have been fixed in between.

Against a real database (rows built directly), one transaction per test, rolled back.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.approvals.service import _fast_lane_reason
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.contracts.service import ContractService, same_text_version_ids
from app.core.enums import ContractVersionSource
from app.playbooks.models import PlaybookDeviation, PlaybookRun
from tests import test_nda_fast_lane
from tests.test_nda_fast_lane import _nda, add_review, add_version

db = test_nda_fast_lane.db  # the same rolled-back-transaction fixture

NDA_TEXT = "1. Confidentiality. Each party keeps the other's information secret."
FIXED_TEXT = "1. Confidentiality. Each party keeps the other's information secret for 3 years."


def text_version(
    db: Session, contract: Contract, number: int, text: str, *,
    source: str = ContractVersionSource.UPLOAD, method: str = "documents_reader",
) -> ContractVersion:
    version = add_version(db, contract, number)
    version.source = source
    snapshot = ContractTextSnapshot(
        org_id=contract.org_id, contract_id=contract.id, contract_version_id=version.id,
        extraction_method=method, text=text,
    )
    db.add(snapshot)
    db.flush()
    version.text_snapshot_id = snapshot.id
    db.flush()
    return version


def reviewed_low_nda(db: Session) -> tuple[Contract, ContractVersion]:
    """An NDA whose v1 Claude reviewed with no findings (so its band is low)."""
    contract = _nda(db)
    v1 = text_version(db, contract, 1, NDA_TEXT)
    contract.current_authoritative_version_id = v1.id
    add_review(db, contract, v1)
    db.flush()
    return contract, v1


def make_current(db: Session, contract: Contract, version: ContractVersion) -> None:
    contract.current_authoritative_version_id = version.id
    db.flush()


def test_a_signed_copy_of_a_reviewed_low_risk_nda_still_fast_lanes(db: Session):
    """Guards against every signed copy losing its review: the copy is a new version with
    the same words, so the fast lane sent clean NDAs back to Approval (or paid for a
    second review) for text Claude had already reviewed. The redline proposal made in
    between was never current, so it must not break the chain."""
    contract, v1 = reviewed_low_nda(db)
    text_version(
        db, contract, 2, FIXED_TEXT,
        source=ContractVersionSource.PLAYBOOK_REDLINE, method="playbook_redline_text",
    )
    signed = text_version(db, contract, 3, NDA_TEXT, source=ContractVersionSource.SIGNED)
    make_current(db, contract, signed)
    assert same_text_version_ids(db, contract) == [signed.id, v1.id]
    assert _fast_lane_reason(db, contract=contract)


def test_a_restore_of_reviewed_then_fixed_text_gets_no_old_review(db: Session):
    """Guards the restore hazard: v1's high issue was fixed in v2 (deviation 'fixed', which
    the fast lane counts as resolved), then v1 was restored. Matching ANY same-text version
    would credit v1's review to the restore and fast-lane the problem wording. The
    accepted-redline v2 shares a proposal's source but WAS current, so it must not be
    skipped as a proposal."""
    contract, _ = reviewed_low_nda(db)
    run = db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).one()
    db.add(PlaybookDeviation(
        org_id=contract.org_id, playbook_run_id=run.id, contract_id=contract.id,
        severity="high", issue="No term on confidentiality", status="fixed",
    ))
    v2 = text_version(
        db, contract, 2, FIXED_TEXT,
        source=ContractVersionSource.PLAYBOOK_REDLINE, method="accepted_redline_text",
    )
    make_current(db, contract, v2)
    restored = text_version(db, contract, 3, NDA_TEXT, source=ContractVersionSource.RESTORED)
    make_current(db, contract, restored)
    assert same_text_version_ids(db, contract) == [restored.id]
    assert _fast_lane_reason(db, contract=contract) is None


def test_last_review_is_on_current_text_for_an_identical_copy_only(db: Session):
    """Guards the contract page saying 'I reviewed an earlier version' after a signed copy
    or a no-op save, while still saying so once the words really changed."""
    contract, v1 = reviewed_low_nda(db)
    copy = text_version(db, contract, 2, NDA_TEXT, source=ContractVersionSource.SIGNED)
    make_current(db, contract, copy)
    last = ContractService(db).compute_review_status(contract=contract)["last_review"]
    assert (last["contract_version_id"], last["on_current_text"]) == (v1.id, True)

    changed = text_version(db, contract, 3, FIXED_TEXT, source=ContractVersionSource.MANUAL_EDIT)
    make_current(db, contract, changed)
    last = ContractService(db).compute_review_status(contract=contract)["last_review"]
    assert last["on_current_text"] is False
