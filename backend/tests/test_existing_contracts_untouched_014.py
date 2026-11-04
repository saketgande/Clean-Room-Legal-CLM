"""Spec 014 FR-10 / FR-11: rolling the new standard out must not reach back into work already done.

The failure modes guarded: the update re-reviews or re-renders a contract that was drafted and
reviewed under the old wording; it rewrites or deletes the findings a person has been working through;
it moves a finished review onto the new playbook version (so its findings no longer match the rules
it was made against); or it changes a request that is already filed.

Runs the real ``apply_standard_update`` on a rolled-back transaction, with this organisation's
playbook made to look like one the review read (so the update really publishes a new version).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contract_files.models import (
    ContractEdit,
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.core.database import engine, utcnow
from app.drafting_templates import standard
from app.intake.models import IntakeRequest
from app.organizations.models import Organization
from app.playbooks.models import (
    Playbook,
    PlaybookDeviation,
    PlaybookRule,
    PlaybookRun,
    PlaybookVersion,
)

TEXT = "MASTER SERVICES AGREEMENT\n1. Insurance. The Provider shall hold cover of INR 1 crore.\n"


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


def _snapshot(db: Session, ids: dict) -> dict:
    """Everything a person could see of the old work, as plain values."""
    contract = db.get(Contract, ids["contract"])
    version = db.get(ContractVersion, ids["version"])
    run = db.get(PlaybookRun, ids["run"])
    request = db.get(IntakeRequest, ids["request"])
    db.refresh(contract), db.refresh(version), db.refresh(run), db.refresh(request)
    devs = db.scalars(select(PlaybookDeviation).where(PlaybookDeviation.contract_id == ids["contract"])
                      .order_by(PlaybookDeviation.id)).all()
    edits = db.scalars(select(ContractEdit).where(ContractEdit.contract_id == ids["contract"])
                       .order_by(ContractEdit.id)).all()
    rules = db.scalars(select(PlaybookRule).where(PlaybookRule.playbook_version_id == ids["old_pv"])
                       .order_by(PlaybookRule.id)).all()
    return {
        "text": db.get(ContractTextSnapshot, version.text_snapshot_id).text,
        "versions": [v.id for v in db.scalars(
            select(ContractVersion).where(ContractVersion.contract_id == ids["contract"])
            .order_by(ContractVersion.id)).all()],
        "current": contract.current_authoritative_version_id,
        "run": (run.playbook_version_id, run.contract_version_id, run.status),
        "findings": [(d.id, d.severity, d.issue, d.status, d.suggested_fix, d.playbook_rule_id) for d in devs],
        "edits": [(e.id, e.original_text, e.replacement_text, e.status, e.contract_version_id) for e in edits],
        "old_rules": [(r.id, r.clause_type, r.risk_level, r.preferred_position) for r in rules],
        "request": (request.status, request.stage, request.subject, request.description, request.field_values),
    }


def test_the_standard_update_leaves_existing_contracts_requests_findings_and_changes_alone(db, monkeypatch):
    """FR-10/FR-11: a contract, its review, its findings and suggested changes and the request it came
    from read exactly the same after the update; the only new thing is a later playbook version."""
    org = Organization(name="014 org", slug=f"std-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="T",
                hashed_password="hash", status="active")
    db.add(user)
    db.flush()

    playbook = Playbook(org_id=org.id, name="Master Services Agreement Playbook", status="published")
    db.add(playbook)
    db.flush()
    old_pv = PlaybookVersion(org_id=org.id, playbook_id=playbook.id, version_number=1, status="published")
    db.add(old_pv)
    db.flush()
    playbook.current_version_id = old_pv.id
    rule = PlaybookRule(org_id=org.id, playbook_version_id=old_pv.id, clause_type="insurance",
                        rule_type="require", risk_level="medium", preferred_position="INR 1 crore cover")
    db.add(rule)

    contract = Contract(org_id=org.id, title="MSA", owner_user_id=user.id, contract_type="MSA")
    db.add(contract)
    db.flush()
    cfile = ContractFile(org_id=org.id, contract_id=contract.id, file_label="MSA.docx")
    stored = StorageObject(org_id=org.id, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
                           mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
    db.add_all([cfile, stored])
    db.flush()
    version = ContractVersion(org_id=org.id, contract_id=contract.id, contract_file_id=cfile.id,
                              version_number=1, storage_object_id=stored.id, source="manual_edit",
                              is_authoritative=True)
    db.add(version)
    db.flush()
    snap = ContractTextSnapshot(org_id=org.id, contract_id=contract.id, contract_version_id=version.id,
                                extraction_method="text", text=TEXT)
    db.add(snap)
    db.flush()
    version.text_snapshot_id = snap.id
    contract.current_authoritative_version_id = version.id
    run = PlaybookRun(org_id=org.id, playbook_id=playbook.id, playbook_version_id=old_pv.id,
                      contract_id=contract.id, contract_version_id=version.id, status="succeeded")
    request = IntakeRequest(org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:8]}", requester_user_id=user.id,
                            type_label="MSA", subject="Hosting MSA", description="Please draft an MSA",
                            field_values={"request_form": "msa"}, submitted_at=utcnow())
    db.add_all([run, request])
    db.flush()
    db.add(PlaybookDeviation(org_id=org.id, playbook_run_id=run.id, playbook_rule_id=rule.id,
                             contract_id=contract.id, severity="medium", clause_type="insurance",
                             issue="Cover is below the standard", suggested_fix="Raise to INR 5 crore",
                             status="open"))
    db.add(ContractEdit(org_id=org.id, contract_id=contract.id, contract_version_id=version.id,
                        edit_type="replace", status="proposed", original_text="INR 1 crore",
                        replacement_text="INR 5 crore", rationale="Playbook"))
    db.flush()
    ids = {"contract": contract.id, "version": version.id, "run": run.id, "request": request.id,
           "old_pv": old_pv.id}
    before = _snapshot(db, ids)

    # This organisation's rules count as the ones the review read, so the update publishes for it.
    monkeypatch.setitem(standard.BASELINE_FINGERPRINTS, "msa", frozenset(
        {standard.fingerprint(db.scalars(select(PlaybookRule).where(
            PlaybookRule.playbook_version_id == old_pv.id)).all())}))
    standard.apply_standard_update(db)

    assert _snapshot(db, ids) == before
    versions = db.scalars(select(PlaybookVersion).where(PlaybookVersion.playbook_id == playbook.id)).all()
    assert len(versions) == 2, "the update should have published the standard as a later version"
    assert db.get(PlaybookRun, run.id).playbook_version_id == old_pv.id
    assert db.scalars(select(PlaybookRun).where(PlaybookRun.contract_id == contract.id)).all() == [run], \
        "the update must not start a new review"


def test_replaying_the_update_still_leaves_existing_work_alone(db):
    """A second run (the migration is safe to replay) must not touch findings either."""
    standard.apply_standard_update(db)
    count = db.scalars(select(PlaybookDeviation.id)).all()
    standard.apply_standard_update(db)
    assert db.scalars(select(PlaybookDeviation.id)).all() == count
