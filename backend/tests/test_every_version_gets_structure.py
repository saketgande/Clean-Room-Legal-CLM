"""Every new version gets clause structure, and every new current version is analysed.

Guards two gaps found 2026-10-08:
* Copies of text the contract already had (restore, signed copy, replicate) got no
  ContractDocumentElement rows, so clause extraction on them fell back to the old
  full-text skill that cuts off on long contracts, and the Documents reader's clause
  tree was lost. A redraft got no elements either.
* PUT /text, the revision "our reply" and the assistant redraft made a current
  version that never got metadata, clause or embedding jobs.

Runs the real routes and database; jobs are created but never dispatched.
"""

import asyncio
import io
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.contract_files.service as files_service
import app.models  # noqa: F401
from app.contract_files.models import ContractDocumentElement, ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.core.database import SessionLocal, utcnow
from app.jobs.models import JobRun

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_A = "1. Term. This Agreement lasts one (1) year.\n\n2. Fees. Fees are due in thirty (30) days."
TEXT_B = "1. Term. This Agreement lasts two (2) years.\n\n2. Fees. Fees are due in sixty (60) days."


def _word(text: str) -> bytes:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def contract(monkeypatch):
    """A contract with v2 = TEXT_A (structured) and v3 = TEXT_B, made through the API."""
    from app.auth.models import User
    from app.core.rate_limit import limiter
    from app.core.security import create_access_token
    from app.main import app

    monkeypatch.setattr(files_service, "dispatch_job", lambda db, *, job: job)
    # Parallel suites share the per-user upload limit; this fixture is not testing it.
    monkeypatch.setattr(limiter, "enabled", False)
    c = TestClient(app)
    # A token minted directly, not /auth/login: parallel suites trip the login rate limit.
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        if admin is None:
            pytest.skip("no seeded admin in this database")
        token, _ = create_access_token(admin.id, {"org_id": admin.org_id, "role_id": admin.active_role_id})
    h = {"authorization": "Bearer " + token}
    up = c.post("/api/v1/contracts/upload", headers=h, data={"title": "Every version structure test"},
                files={"file": ("t.docx", _word("1. Term."), DOCX)})
    assert up.status_code == 202, up.text
    cid = up.json()["contract"]["id"]
    try:
        versions = []
        for text in (TEXT_A, TEXT_B):
            r = c.put(f"/api/v1/contracts/{cid}/text", headers=h, json={"text": text})
            assert r.status_code == 200, r.text
            versions.append(r.json()["id"])
        yield SimpleNamespace(client=c, headers=h, id=cid, v_a=versions[0], v_b=versions[1])
    finally:
        # Never leave queued jobs: the stale-job sweeper would send them later.
        db = SessionLocal()
        for job in db.query(JobRun).filter(JobRun.resource_type == "contract", JobRun.resource_id == cid,
                                           JobRun.status == "queued"):
            job.status = "cancelled"
        db.get(Contract, cid).deleted_at = utcnow()
        db.commit()
        db.close()


def _elements(db, version_id):
    snap_id = db.get(ContractVersion, version_id).text_snapshot_id
    return db.scalars(select(ContractDocumentElement).where(ContractDocumentElement.text_snapshot_id == snap_id)
                      .order_by(ContractDocumentElement.seq)).all()


def _job_types(db, version_id):
    return {j.job_type for j in db.scalars(select(JobRun).where(JobRun.idempotency_key.like(f"%:{version_id}:%")))}


def _mark_as_reader_tree(db, version_id):
    """Make v_a's elements look like the Documents reader's (a tree, source docx), so a
    copy is told apart from a fresh flat split, which would give neither."""
    rows = _elements(db, version_id)
    assert len(rows) >= 2
    for r in rows:
        r.source = "docx"
    rows[1].parent_id = rows[0].id
    db.commit()
    return rows


def _assert_copied(db, donor_rows, version_id):
    copied = _elements(db, version_id)
    snap = db.get(ContractTextSnapshot, db.get(ContractVersion, version_id).text_snapshot_id)
    assert snap.structure_status == "structured" and snap.element_count == len(donor_rows)
    assert [(e.seq, e.char_start, e.char_end, e.block_id, e.source) for e in copied] == [
        (d.seq, d.char_start, d.char_end, d.block_id, "docx") for d in donor_rows
    ]
    assert not {e.id for e in copied} & {d.id for d in donor_rows}
    assert copied[1].parent_id == copied[0].id  # remapped to the copy, not the donor


def test_put_text_queues_analysis_for_the_new_version(contract):
    """A direct text edit made a current version with no metadata/clause/embedding jobs."""
    db = SessionLocal()
    try:
        assert _job_types(db, contract.v_b) >= set(files_service.INITIAL_CONTRACT_AI_JOB_TYPES)
    finally:
        db.close()


def test_restore_copies_the_clause_tree_and_is_analysed(contract):
    """A restore copied the text but no elements, losing the reader's clause tree."""
    db = SessionLocal()
    try:
        donor = _mark_as_reader_tree(db, contract.v_a)
        r = contract.client.post(f"/api/v1/contracts/{contract.id}/versions/{contract.v_a}/restore",
                                 headers=contract.headers)
        assert r.status_code == 200, r.text
        db.expire_all()
        _assert_copied(db, donor, r.json()["id"])
        assert _job_types(db, r.json()["id"]) >= set(files_service.INITIAL_CONTRACT_AI_JOB_TYPES)
    finally:
        db.close()


def test_signed_copy_and_replicate_get_the_clause_tree(contract):
    """Signed copies (7 of 7 in the dev DB) and replicas had no elements at all."""
    from app.ai.tool_registry import ContractHandleInput
    from app.ai.tool_runtime import tool_runtime
    from app.auth.models import User
    from app.signatures.service import SignaturesService

    db = SessionLocal()
    try:
        donor = _mark_as_reader_tree(db, contract.v_b)
        user = db.scalar(select(User).where(User.email == "admin@example.com"))
        k = db.get(Contract, contract.id)
        signed = SignaturesService(db)._create_signed_version(
            user=user, contract=k, base_version=db.get(ContractVersion, contract.v_b))
        db.commit()
        _assert_copied(db, donor, signed.id)
        out = tool_runtime._replicate_contract_version(
            db, payload=ContractHandleInput(contract_id=contract.id), user=user, session_id="test")
        db.commit()
        _assert_copied(db, donor, out["contract_version_id"])
    finally:
        db.close()


def test_redraft_is_structured_current_and_analysed(contract, monkeypatch):
    """A redraft had no elements and no jobs, and inserting it as a second authoritative
    version broke uq_contract_version_authoritative."""
    from app.ai.controller import ai_controller
    from app.ai.tool_registry import RedraftContractInput
    from app.ai.tool_runtime import tool_runtime
    from app.auth.models import User

    async def fake_skill(*a, **k):
        return SimpleNamespace(title="Redraft", assumptions=[], sections=[
            SimpleNamespace(heading="1. Term", body="This Agreement lasts three (3) years."),
            SimpleNamespace(heading="2. Fees", body="Fees are due in ten (10) days."),
        ])

    monkeypatch.setattr(ai_controller, "run_structured_skill", fake_skill)
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "admin@example.com"))
        out = asyncio.run(tool_runtime._redraft_contract(
            db, payload=RedraftContractInput(contract_id=contract.id), user=user, session_id="test"))
        db.expire_all()
        vid = out["contract_version_id"]
        assert db.get(Contract, contract.id).current_authoritative_version_id == vid
        assert _elements(db, vid)
        assert _job_types(db, vid) >= set(files_service.INITIAL_CONTRACT_AI_JOB_TYPES)
    finally:
        db.close()


def test_revision_reply_is_analysed(contract):
    """'Our reply' versions (48 in the dev DB) never got metadata, clause or embedding jobs."""
    c, h, cid = contract.client, contract.headers, contract.id
    theirs = _word("1. Term. This Agreement lasts five (5) years.\n\n2. Fees. Fees are due in sixty (60) days.")
    r = c.post(f"/api/v1/contracts/{cid}/counterparty-revision", headers=h, files={"file": ("theirs.docx", theirs, DOCX)})
    assert r.status_code == 201, r.text
    rnd = c.get(f"/api/v1/contracts/{cid}/revisions/current", headers=h).json()
    for ch in rnd["changes"]:
        if ch["decision"] == "open":
            d = c.post(f"/api/v1/contracts/{cid}/revisions/{rnd['id']}/changes/{ch['id']}", headers=h,
                       json={"decision": "kept"})
            assert d.status_code == 200, d.text
    done = c.post(f"/api/v1/contracts/{cid}/revisions/{rnd['id']}/finish", headers=h).json()
    assert done["outcome"] == "counter", done
    db = SessionLocal()
    try:
        assert _job_types(db, done["outcome_version_id"]) >= set(files_service.INITIAL_CONTRACT_AI_JOB_TYPES)
    finally:
        db.close()
