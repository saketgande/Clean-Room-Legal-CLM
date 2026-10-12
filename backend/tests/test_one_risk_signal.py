"""One risk signal: the contract's band comes only from the playbook review of its
current text (contracts/risk.py). The old AI-weighted score called 11 of 11
reviewed "low" contracts low while each had an open high issue.

Runs against Postgres inside one rolled-back transaction; nothing calls a model.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
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
from app.contracts.risk import band_for, contract_risk, refresh_contract_risk
from app.core.database import engine
from app.organizations.models import Organization
from app.playbooks.models import Playbook, PlaybookDeviation, PlaybookRun, PlaybookVersion

TEXT = "MUTUAL NDA\n1. Liability. Unlimited.\n"


@pytest.mark.parametrize(
    ("devs", "band"),
    [
        ([], "low"),
        ([("low", "open", False)], "low"),
        ([("medium", "open", False), ("low", "open", False)], "medium"),
        ([("high", "open", False)], "high"),
        ([("critical", "open", False)], "high"),
        ([("bogus", "open", False)], "medium"),  # unknown severity reads as medium
        ([("high", "fixed", False), ("low", "open", False)], "high"),  # not yet in the text
        ([("critical", "accepted_fallback", False)], "low"),
        ([("high", "waived", False)], "high"),
        ([("high", "accepted", False)], "high"),
        ([("high", "rejected", False)], "high"),
        ([("high", "escalated", False)], "high"),
        ([("low", "open", True)], "medium"),
        ([("low", "needs_review", False)], "medium"),
        ([("low", "fixed", True)], "medium"),
    ],
)
def test_band_for_table(devs, band):
    """Guards the rule itself: an unresolved high must never read as medium (medium
    clears the workflow AI gate), a concession must not lower the band, and an
    unverified finding can't leave the contract low."""
    assert band_for(devs) == band


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
    def __init__(self, db: Session, *, contract_type: str = "NDA"):
        self.db = db
        org = Organization(name="Risk org", slug=f"risk-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org_id = org.id
        user = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com",
                    full_name="Risk", hashed_password="hash", status="active")
        db.add(user)
        db.flush()
        self.contract = Contract(org_id=org.id, title="NDA", owner_user_id=user.id,
                                 contract_type=contract_type)
        db.add(self.contract)
        db.flush()
        self.file = ContractFile(org_id=org.id, contract_id=self.contract.id, file_label="NDA.docx")
        db.add(self.file)
        self.playbook = Playbook(org_id=org.id, name="NDA playbook", status="published")
        db.add(self.playbook)
        db.flush()
        self.pv = self.publish()

    def publish(self) -> PlaybookVersion:
        number = 1 + len(self.db.scalars(
            select(PlaybookVersion.id).where(PlaybookVersion.playbook_id == self.playbook.id)).all())
        pv = PlaybookVersion(org_id=self.org_id, playbook_id=self.playbook.id,
                             version_number=number, status="published")
        self.db.add(pv)
        self.db.flush()
        return pv

    def version(self, text: str = TEXT) -> ContractVersion:
        c = self.contract
        stored = StorageObject(org_id=self.org_id, storage_key=f"test/{uuid.uuid4().hex}",
                               filename="NDA.docx", mime_type="application/octet-stream",
                               size_bytes=1, sha256_hash=uuid.uuid4().hex)
        self.db.add(stored)
        self.db.flush()
        number = 1 + len(self.db.scalars(
            select(ContractVersion.id).where(ContractVersion.contract_id == c.id)).all())
        if c.current_authoritative_version_id:  # one authoritative version per contract
            self.db.get(ContractVersion, c.current_authoritative_version_id).is_authoritative = False
            self.db.flush()
        version =ContractVersion(org_id=self.org_id, contract_id=c.id, contract_file_id=self.file.id,
                                  version_number=number, storage_object_id=stored.id,
                                  source="manual_edit", is_authoritative=True)
        self.db.add(version)
        self.db.flush()
        snap = ContractTextSnapshot(org_id=self.org_id, contract_id=c.id, contract_version_id=version.id,
                                    extraction_method="text", text=text)
        self.db.add(snap)
        self.db.flush()
        version.text_snapshot_id = snap.id
        c.current_authoritative_version_id = version.id
        self.db.flush()
        return version

    def run(self, version: ContractVersion, *, status="succeeded", model="claude",
            validation="valid", pv: PlaybookVersion | None = None,
            devs: tuple[tuple[str, str], ...] = ()) -> PlaybookRun:
        run = PlaybookRun(org_id=self.org_id, playbook_id=self.playbook.id,
                          playbook_version_id=(pv or self.pv).id, contract_id=self.contract.id,
                          contract_version_id=version.id, status=status, model_name=model,
                          validation_status=validation)
        self.db.add(run)
        self.db.flush()
        for severity, dev_status in devs:
            self.db.add(PlaybookDeviation(org_id=self.org_id, playbook_run_id=run.id,
                                          contract_id=self.contract.id, severity=severity,
                                          issue=f"{severity} {dev_status}", status=dev_status))
        self.db.flush()
        return run


def test_no_review_is_unknown_never_low(db):
    """A never-reviewed contract used to be able to carry a "low" band."""
    w = World(db)
    w.version()
    out = contract_risk(db, w.contract)
    assert out["band"] == "unknown" and "Never reviewed" in out["note"]


def test_a_keyword_scan_is_not_a_review(db):
    """A failed AI review leaves a keyword scan with few findings; it must not read as low."""
    w = World(db)
    w.run(w.version(), status="keyword_only", model="deterministic_playbook_engine")
    out = contract_risk(db, w.contract)
    assert out["band"] == "unknown" and "keyword scan" in out["note"]


def test_a_review_of_older_text_does_not_carry_to_new_text(db):
    """The old band was never recomputed when the text changed (Initech MSA stayed high
    with every issue fixed); a review of different text must not speak for this one."""
    w = World(db)
    w.run(w.version(), devs=(("low", "open"),))
    w.version("MUTUAL NDA\n1. Liability. Capped.\n")
    out = contract_risk(db, w.contract)
    assert out["band"] == "unknown" and "text changed" in out["note"]


def test_identical_text_keeps_its_review(db):
    """A signed copy or no-op save has the same words, so the review still holds."""
    w = World(db)
    w.run(w.version(), devs=(("medium", "open"),))
    w.version()
    assert contract_risk(db, w.contract)["band"] == "medium"


def test_a_review_against_superseded_rules_is_unknown(db):
    """Publishing never archives the old version; a review against replaced rules
    must not stand for the rules in force."""
    w = World(db)
    w.run(w.version())
    w.publish()
    out = contract_risk(db, w.contract)
    assert out["band"] == "unknown" and "re-published" in out["note"]


def test_no_playbook_for_the_type_is_unknown(db):
    """Some other playbook's clean run must not make, say, a lease "low"."""
    w = World(db, contract_type="Lease")
    w.run(w.version())
    out = contract_risk(db, w.contract)
    assert out["band"] == "unknown" and "No playbook" in out["note"]
    assert out["playbook_id"] is None


def test_needs_review_run_counts_but_floors_at_medium(db):
    """A run whose quotes didn't all verify still counts (else the contract stays
    unknown forever, since a run's validation never changes), but its findings
    can't make the contract low until a person decides them."""
    w = World(db)
    w.run(w.version(), validation="needs_review", devs=(("low", "open"),))
    out = contract_risk(db, w.contract)
    assert out["band"] == "medium"
    assert out["counts"] == {"critical": 0, "high": 0, "medium": 1, "low": 0}


def test_waived_high_still_high_and_named_as_conceded(db):
    """A concession leaves the deviating wording in; whoever conceded must not be
    able to send their own concession down the fast lane."""
    w = World(db)
    w.run(w.version(), devs=(("high", "waived"), ("low", "open")))
    out = contract_risk(db, w.contract)
    assert out["band"] == "high"
    top = out["drivers"][0]
    assert (top["severity"], top["status"], top["conceded"]) == ("high", "waived", True)
    assert out["drivers"][1]["conceded"] is False


def test_a_fixed_high_still_counts_until_its_new_version_lands(db):
    """Accepting our wording for one issue marks it "fixed" at once, but the edit lands
    in a new version only when the batch is decided; meanwhile the deviating wording is
    still in this text. Treating "fixed" as resolved dropped the band (even to low) on
    unchanged text, which the fast lane and authority read."""
    w = World(db)
    w.run(w.version(), devs=(("high", "fixed"), ("low", "open")))
    out = contract_risk(db, w.contract)
    assert out["band"] == "high"


def test_findings_of_every_qualifying_run_count(db):
    """Re-running the review on unchanged text must not fish for a cleaner answer."""
    w = World(db)
    v = w.version()
    w.run(v, devs=(("critical", "open"),))
    w.run(v)
    out = contract_risk(db, w.contract)
    assert out["band"] == "high" and len(out["playbook_run_ids"]) == 2


def test_refresh_caches_band_and_clears_score(db):
    """The columns are a cache: unknown must cache as NULL, never a stale band,
    and the old 0-100 score must not survive."""
    w = World(db)
    w.contract.risk_score, w.contract.risk_band, w.contract.risk_level = 30, "low", "low"
    w.version()
    out = refresh_contract_risk(db, w.contract)
    assert (w.contract.risk_band, w.contract.risk_level, w.contract.risk_score) == (None, None, None)
    assert w.contract.risk_summary is out and out["band"] == "unknown"
    w.run(db.get(ContractVersion, w.contract.current_authoritative_version_id), devs=(("high", "open"),))
    refresh_contract_risk(db, w.contract)
    assert (w.contract.risk_band, w.contract.risk_level) == ("high", "high")
