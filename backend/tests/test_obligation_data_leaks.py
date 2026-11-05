"""Who may read or change what hangs off a contract: the AI-run list, the job list, the Word add-in's
link and the obligation write paths each trusted a login or a read-level grant where the contract's own
access rules say more. Real rows, one rolled-back transaction, RBAC on (dev compose runs with it off,
which would make every user an admin and every check a pass)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai import routes as ai_routes
from app.ai.models import AISkillRun
from app.auth.models import User
from app.contract_files.models import ContractFile, ContractVersion, StorageObject
from app.contracts.models import Contract
from app.contracts.service import ContractService
from app.core.config import settings
from app.core.database import engine
from app.grants.models import ResourceGrant
from app.jobs import routes as jobs_routes
from app.jobs.models import JobRun
from app.obligations.service import ObligationsService
from app.organizations.models import Organization
from app.word_addin import service as word_service
from app.word_addin.service import WordAddinService


@pytest.fixture
def w(monkeypatch):
    monkeypatch.setattr(settings, "disable_rbac", False)
    connection = engine.connect()
    trans = connection.begin()
    db = Session(bind=connection, expire_on_commit=False)
    org = Organization(name="Leaks org", slug=f"lk-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()

    def user(label: str) -> User:
        row = User(org_id=org.id, email=f"{label}-{uuid.uuid4().hex[:6]}@example.com", full_name=label,
                   hashed_password="hash", status="active")
        db.add(row)
        db.flush()
        return row

    owner, member = user("owner"), user("member")

    def contract(title: str) -> Contract:
        row = Contract(org_id=org.id, title=title, owner_user_id=owner.id)
        db.add(row)
        db.flush()
        return row

    def grant(on: Contract, level: str) -> None:
        db.add(ResourceGrant(org_id=org.id, principal_type="user", principal_id=member.id,
                             resource_type="contract", resource_id=on.id, access_level=level))
        db.flush()

    try:
        yield SimpleNamespace(db=db, org=org, owner=owner, member=member, contract=contract, grant=grant)
    finally:
        db.close()
        trans.rollback()
        connection.close()


# --- grant level ---------------------------------------------------------------------------------------------


def test_a_read_grant_opens_a_contract_but_changing_its_obligations_needs_update(w):
    """Guards the grant ladder (read < comment < update < share < owner) never being enforced: a read
    grant plus an obligation:update role edited, completed and re-extracted a contract it could only read."""
    mine = w.contract("Mine")
    svc = ContractService(w.db)

    with pytest.raises(HTTPException) as nothing:
        svc.get_contract_for_user(contract_id=mine.id, user=w.member)
    assert nothing.value.status_code == 404

    w.grant(mine, "read")
    assert svc.get_contract_for_user(contract_id=mine.id, user=w.member).id == mine.id
    with pytest.raises(HTTPException) as reader:
        svc.get_contract_for_user(contract_id=mine.id, user=w.member, edit=True)
    assert reader.value.status_code == 403

    w.grant(mine, "update")
    assert svc.get_contract_for_user(contract_id=mine.id, user=w.member, edit=True).id == mine.id
    assert svc.get_contract_for_user(contract_id=mine.id, user=w.owner, edit=True).id == mine.id


def test_a_reader_cannot_start_an_extraction(w):
    """Guards POST /obligations/extract: it rewrites a contract's obligations, and ran for a reader."""
    mine = w.contract("Mine")
    w.grant(mine, "read")
    jobs_before = w.db.query(JobRun).count()

    with pytest.raises(HTTPException) as err:
        ObligationsService(w.db).trigger_extraction(contract_id=mine.id, current_user=w.member)

    assert err.value.status_code == 403
    assert w.db.query(JobRun).count() == jobs_before


# --- the AI-run list and the job list -------------------------------------------------------------------------


def _skill_run(w, contract: Contract | None) -> AISkillRun:
    run = AISkillRun(org_id=w.org.id, skill_name="obligation_extraction", skill_version="1", execution_mode="job",
                     contract_id=contract.id if contract else None, prompt_key="obligation_extraction",
                     prompt_version="1", prompt_hash="x" * 64, model="m",
                     error_message="INSERT ... parameters: 'Supplier shall pay INR 5,000,000'")
    w.db.add(run)
    w.db.flush()
    return run


def test_the_ai_run_list_leaves_out_runs_on_contracts_you_cannot_open(w):
    """Guards GET /ai/skill-runs listing the whole org's runs - contract id, skill and error text - to
    anyone who may use the assistant."""
    seen, hidden = w.contract("Shared"), w.contract("Private")
    w.grant(seen, "read")
    mine, theirs, none = _skill_run(w, seen), _skill_run(w, hidden), _skill_run(w, None)

    ids = {r.id for r in ai_routes.list_skill_runs(db=w.db, current_user=w.member)}

    assert mine.id in ids and none.id in ids and theirs.id not in ids


def _job(w, contract: Contract) -> JobRun:
    job = JobRun(org_id=w.org.id, job_type="obligation_extraction", resource_type="contract",
                 resource_id=contract.id, error_message="INSERT ... 'Supplier shall pay INR 5,000,000'")
    w.db.add(job)
    w.db.flush()
    return job


def test_the_job_routes_hide_a_job_on_a_contract_you_cannot_open(w):
    """Guards job error text (a failed insert prints the contract's own words) reaching someone a wall or
    clearance seals off from that contract: the list, the detail and the actions all go through one check."""
    seen, hidden = w.contract("Shared"), w.contract("Private")
    w.grant(seen, "read")
    mine, theirs = _job(w, seen), _job(w, hidden)
    orphan = JobRun(org_id=w.org.id, job_type="obligation_extraction", resource_type="contract",
                    resource_id=str(uuid.uuid4()))  # its contract is gone: nothing to protect
    w.db.add(orphan)
    w.db.flush()

    ids = {j.id for j in jobs_routes.list_jobs(job_status=None, limit=100, offset=0, db=w.db,
                                               current_user=w.member)}
    assert mine.id in ids and orphan.id in ids and theirs.id not in ids
    assert jobs_routes.get_job(job_id=mine.id, db=w.db, current_user=w.member).id == mine.id
    for act in (jobs_routes.get_job, jobs_routes.cancel_job):
        with pytest.raises(HTTPException) as err:
            act(job_id=theirs.id, db=w.db, current_user=w.member)
        assert err.value.status_code == 404


# --- the Word add-in's link ------------------------------------------------------------------------------------


class _Upload:
    def __init__(self, content: bytes):
        self._content, self.filename = content, "c.docx"

    async def read(self, _n=-1):
        data, self._content = self._content, b""
        return data

    async def seek(self, _pos):
        pass


def _stored_copy(w, contract: Contract, content: bytes) -> None:
    file = ContractFile(org_id=w.org.id, contract_id=contract.id, file_label="c.docx")
    stored = StorageObject(org_id=w.org.id, storage_key=f"test/{uuid.uuid4().hex}", filename="c.docx",
                           mime_type="application/octet-stream", size_bytes=len(content),
                           sha256_hash=hashlib.sha256(content).hexdigest())
    w.db.add_all([file, stored])
    w.db.flush()
    w.db.add(ContractVersion(org_id=w.org.id, contract_id=contract.id, contract_file_id=file.id,
                             version_number=1, storage_object_id=stored.id, is_authoritative=True))
    w.db.flush()


def test_the_word_link_does_not_hand_back_a_contract_you_cannot_open(w, monkeypatch):
    """Guards POST /word/link answering a file-hash match with another user's contract id, title and stage
    across a wall: a match is not a right to see it. With no right to add contracts either, the caller is
    refused; with it, they get a contract of their own."""
    content = b"the same bytes"
    _stored_copy(w, w.contract("Private"), content)
    service = WordAddinService(w.db)

    with pytest.raises(HTTPException) as err:
        asyncio.run(service.resolve_contract_for_document(upload=_Upload(content), user=w.member))
    assert err.value.status_code == 403

    created = SimpleNamespace(id="new-id", title="Mine", lifecycle_stage="intake")

    async def fake_create(db, *, upload, user, request_id):
        return {"contract": created}

    monkeypatch.setattr(word_service, "has_permission", lambda perms, needed: True)
    monkeypatch.setattr(word_service, "create_contract_from_upload", fake_create)
    result = asyncio.run(service.resolve_contract_for_document(upload=_Upload(content), user=w.member))
    assert result["created"] is True and result["contract_id"] == "new-id"


def test_the_word_link_still_links_a_contract_you_can_open(w):
    """Guards the link breaking for its real use: re-opening a document of a contract you may read."""
    content = b"my own bytes"
    mine = w.contract("Shared")
    w.grant(mine, "read")
    _stored_copy(w, mine, content)

    result = asyncio.run(WordAddinService(w.db).resolve_contract_for_document(upload=_Upload(content),
                                                                              user=w.member))

    assert result["created"] is False and result["contract_id"] == mine.id
