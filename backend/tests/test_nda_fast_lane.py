"""The NDA fast lane skips the Approval step, so it must fail closed.

It asks contracts.risk.contract_risk for the band, computed now: "low" only when
Claude reviewed the CURRENT text against the NDA playbook's latest published rules
and nothing unresolved is above low. "Zero open high deviations" alone is also what
no review, a failed AI review or a review of an older version leaves behind, and
the cached risk columns can be stale or hand-set, so neither may open the lane.

Against a real database (rows built directly), one transaction per test,
rolled back at teardown.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every domain's models for cross-domain FKs)
from app.approvals.service import _fast_lane_reason
from app.auth.models import User
from app.contract_files.models import ContractFile, ContractVersion, StorageObject
from app.contracts.models import Contract
from app.core.database import engine
from app.core.enums import PlaybookStatus
from app.organizations.models import Organization
from app.playbooks.models import Playbook, PlaybookDeviation, PlaybookRun, PlaybookVersion


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


def add_version(db: Session, contract: Contract, number: int) -> ContractVersion:
    file = ContractFile(org_id=contract.org_id, contract_id=contract.id, file_label="NDA.docx")
    stored = StorageObject(
        org_id=contract.org_id, storage_key=f"test/{uuid.uuid4().hex}", filename="NDA.docx",
        mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex,
    )
    db.add_all([file, stored])
    db.flush()
    version = ContractVersion(
        org_id=contract.org_id, contract_id=contract.id, contract_file_id=file.id,
        version_number=number, storage_object_id=stored.id,
    )
    db.add(version)
    db.flush()
    return version


def add_review(
    db: Session, contract: Contract, version: ContractVersion, *, model_name: str = "claude",
    validation_status: str = "valid", playbook_status: str = PlaybookStatus.PUBLISHED,
) -> PlaybookRun:
    playbook = Playbook(org_id=contract.org_id, name="NDA playbook", status=playbook_status)
    db.add(playbook)
    db.flush()
    pv = PlaybookVersion(
        org_id=contract.org_id, playbook_id=playbook.id, version_number=1, status=playbook_status,
    )
    db.add(pv)
    db.flush()
    playbook.current_version_id = pv.id
    run = PlaybookRun(
        org_id=contract.org_id, playbook_id=playbook.id, playbook_version_id=pv.id,
        contract_id=contract.id, contract_version_id=version.id, status="succeeded",
        model_name=model_name, validation_status=validation_status,
    )
    db.add(run)
    db.flush()
    return run


def make_fast_lane_ready(db: Session, contract: Contract) -> ContractVersion:
    """Give an NDA everything the fast lane needs: a current version and Claude's
    validated review of it with no findings. Returns the version."""
    version = add_version(db, contract, 1)
    contract.current_authoritative_version_id = version.id
    add_review(db, contract, version)
    db.flush()
    return version


def _nda(db: Session, **fields) -> Contract:
    org = Organization(name="Fast Lane Org", slug=f"fl-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(
        org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Fast Laner",
        hashed_password="hash", status="active",
    )
    db.add(user)
    db.flush()
    contract = Contract(
        org_id=org.id, title="Mutual NDA", owner_user_id=user.id, contract_type="nda",
        **{"value_amount": None, "currency": None, **fields},
    )
    db.add(contract)
    db.flush()
    return contract


def test_nda_with_no_value_and_a_real_review_is_fast_laned(db: Session):
    """Guards against over-correcting: the NDA form never asks a value, so a missing
    value must not switch the fast lane off for every NDA."""
    contract = _nda(db)
    make_fast_lane_ready(db, contract)
    assert _fast_lane_reason(db, contract=contract)


def test_low_nda_with_no_review_is_not_fast_laned(db: Session):
    """Guards against reading "zero open deviations" as "reviewed clean" when no
    playbook review ever ran (no playbook published or matched)."""
    contract = _nda(db)
    version = add_version(db, contract, 1)
    contract.current_authoritative_version_id = version.id
    contract.risk_band = "low"
    contract.risk_summary = {"contract_version_id": version.id}
    db.flush()
    assert _fast_lane_reason(db, contract=contract) is None


def test_keyword_fallback_review_does_not_qualify(db: Session):
    """Guards against a failed AI review (the keyword engine's 'succeeded' run that
    found nothing) skipping approval."""
    contract = _nda(db)
    version = make_fast_lane_ready(db, contract)
    db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).delete()
    add_review(db, contract, version, model_name="deterministic_playbook_engine")
    assert _fast_lane_reason(db, contract=contract) is None


def test_review_of_an_older_version_does_not_qualify(db: Session):
    """Guards against v1's clean review carrying over to a v2 nobody reviewed."""
    contract = _nda(db)
    make_fast_lane_ready(db, contract)
    v2 = add_version(db, contract, 2)
    contract.current_authoritative_version_id = v2.id
    contract.risk_summary = {"contract_version_id": v2.id}
    db.flush()
    assert _fast_lane_reason(db, contract=contract) is None


def test_a_stale_cached_low_band_is_not_trusted(db: Session):
    """Guards against reading the cached risk_band column: a missed refresh can leave it
    'low' while the review of this text has an open high issue."""
    contract = _nda(db)
    make_fast_lane_ready(db, contract)
    run = db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).one()
    db.add(PlaybookDeviation(
        org_id=contract.org_id, playbook_run_id=run.id, contract_id=contract.id,
        severity="high", issue="One-way confidentiality", status="open",
    ))
    contract.risk_band = "low"
    contract.risk_summary = {"band": "low", "contract_version_id": contract.current_authoritative_version_id}
    db.flush()
    assert _fast_lane_reason(db, contract=contract) is None


def test_hand_set_risk_level_does_not_qualify(db: Session):
    """Guards against a person PATCHing risk_level='low' on a never-reviewed contract."""
    contract = _nda(db)
    version = add_version(db, contract, 1)
    contract.current_authoritative_version_id = version.id
    contract.risk_band = "low"
    contract.risk_level = "low"
    db.flush()
    assert _fast_lane_reason(db, contract=contract) is None


@pytest.mark.parametrize(
    ("validation_status", "playbook_status"),
    [("needs_review", PlaybookStatus.PUBLISHED), ("valid", PlaybookStatus.DRAFT)],
)
def test_needs_review_or_draft_playbook_run_does_not_qualify(db: Session, validation_status, playbook_status):
    """Guards against a review with unverified citations reading as low (its findings,
    even a low one, are floored at medium until a person decides them), and a test-mode
    run against a draft playbook counting as the real review."""
    contract = _nda(db)
    version = make_fast_lane_ready(db, contract)
    db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).delete()
    run = add_review(
        db, contract, version, validation_status=validation_status, playbook_status=playbook_status
    )
    db.add(PlaybookDeviation(
        org_id=contract.org_id, playbook_run_id=run.id, contract_id=contract.id,
        severity="low", issue="Notice address missing", status="needs_review",
    ))
    db.flush()
    assert _fast_lane_reason(db, contract=contract) is None


@pytest.mark.parametrize(("value", "currency"), [(1_000, "USD"), (60_000, "INR"), (1_000, None)])
def test_stated_value_in_another_currency_or_over_cap_does_not_qualify(db: Session, value, currency):
    """Guards against comparing a USD figure (or one in no stated currency) with the INR cap as
    if they were the same money, and against the cap itself being skipped."""
    contract = _nda(db, value_amount=value, currency=currency)
    make_fast_lane_ready(db, contract)
    assert _fast_lane_reason(db, contract=contract) is None


def test_stated_value_under_cap_in_default_currency_qualifies(db: Session):
    """Guards against the currency rule refusing a value the cap can actually judge."""
    contract = _nda(db, value_amount=10_000, currency="INR")
    make_fast_lane_ready(db, contract)
    assert _fast_lane_reason(db, contract=contract)


@pytest.mark.parametrize("status", ["open", "needs_review", "escalated", "rejected", "waived", "accepted"])
def test_an_unresolved_high_deviation_blocks_a_real_review(db: Session, status):
    """Guards the original rule, that an escalated or rejected high issue (still standing
    in the text) is not read as resolved, and that a conceded one (waived / accepted) still
    counts: otherwise whoever waived it could send their own concession down the fast lane
    past the approver whose job is to see conceded risk."""
    contract = _nda(db)
    make_fast_lane_ready(db, contract)
    run = db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).one()
    db.add(PlaybookDeviation(
        org_id=contract.org_id, playbook_run_id=run.id, contract_id=contract.id,
        severity="high", issue="One-way confidentiality", status=status,
    ))
    db.flush()
    assert _fast_lane_reason(db, contract=contract) is None


@pytest.mark.parametrize(("decision", "blocks"), [("accepted_fallback", False), ("fixed", True)])
def test_a_high_deviation_unblocks_only_once_it_is_out_of_the_text(
    db: Session, decision: str, blocks: bool
):
    """The playbook's own fallback accepted resolves the issue on this text. "fixed" does
    not yet: our wording lands in a new version, and until then the deviating wording is
    still here (the new version then gets a fresh review)."""
    contract = _nda(db)
    make_fast_lane_ready(db, contract)
    run = db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).one()
    db.add(PlaybookDeviation(
        org_id=contract.org_id, playbook_run_id=run.id, contract_id=contract.id,
        severity="high", issue="One-way confidentiality", status=decision,
    ))
    db.flush()
    assert (_fast_lane_reason(db, contract=contract) is None) is blocks


def test_a_review_against_a_superseded_playbook_version_does_not_qualify(db: Session):
    """Guards against a review run on rules since replaced: publishing a new playbook
    version never archives the old one, so "published" alone still passed it."""
    contract = _nda(db)
    make_fast_lane_ready(db, contract)
    run = db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).one()
    newer = PlaybookVersion(
        org_id=contract.org_id, playbook_id=run.playbook_id, version_number=2,
        status=PlaybookStatus.PUBLISHED,
    )
    db.add(newer)
    db.flush()
    db.get(Playbook, run.playbook_id).current_version_id = newer.id
    db.flush()
    assert _fast_lane_reason(db, contract=contract) is None


def test_a_playbook_draft_being_edited_does_not_switch_off_the_fast_lane(db: Session):
    """Playbook.current_version_id points at a new DRAFT while someone edits the rules, but
    official reviews still use the latest PUBLISHED version. Requiring current_version_id
    turned the fast lane off for every NDA for as long as any draft was open."""
    contract = _nda(db)
    make_fast_lane_ready(db, contract)
    run = db.query(PlaybookRun).filter(PlaybookRun.contract_id == contract.id).one()
    draft = PlaybookVersion(
        org_id=contract.org_id, playbook_id=run.playbook_id, version_number=2,
        status=PlaybookStatus.DRAFT,
    )
    db.add(draft)
    db.flush()
    db.get(Playbook, run.playbook_id).current_version_id = draft.id
    db.flush()
    assert _fast_lane_reason(db, contract=contract)
