"""Pay once per distinct text: an analysis job on a snapshot whose text is
byte-identical to an earlier snapshot of the same contract reuses that run
instead of calling the model again (jobs/tasks._run_ai_job ->
AIController.carry_forward_job).

Runs the real job runner against Postgres inside one rolled-back transaction;
the paid skill call is replaced by a counter, so nothing is spent.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.models import AISkillRun
from app.ai.prompt_versions import get_active_prompt_bundle
from app.ai.registry import skill_registry
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.core.database import engine, utcnow
from app.core.enums import JobStatus
from app.jobs import tasks
from app.jobs.models import JobRun
from app.organizations.models import Organization
from app.playbooks.models import Playbook, PlaybookRun, PlaybookVersion

TEXT = "MUTUAL NDA\n1. Confidentiality. Each party keeps the other's information secret.\n"


@pytest.fixture
def conn():
    connection = engine.connect()
    trans = connection.begin()
    try:
        yield connection
    finally:
        trans.rollback()
        connection.close()


@pytest.fixture
def db(conn):
    session = Session(bind=conn)
    yield session
    session.close()


@pytest.fixture
def paid(conn, monkeypatch):
    """Every call that would reach the model, by skill; the runner gets sessions
    on the test's transaction."""
    calls: list[str] = []

    async def run_job_skill(_db, *, job, skill_name, input_payload):
        calls.append(skill_name)

    monkeypatch.setattr(tasks, "SessionLocal", lambda: Session(bind=conn))
    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", run_job_skill)
    return calls


class World:
    def __init__(self, db: Session):
        self.db = db
        org = Organization(name="Pay once org", slug=f"pay-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org_id = org.id
        self.user = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com",
                         full_name="Pay Once", hashed_password="hash", status="active")
        db.add(self.user)
        db.flush()

    def contract(self) -> Contract:
        contract = Contract(org_id=self.org_id, title="NDA", owner_user_id=self.user.id, contract_type="NDA")
        self.db.add(contract)
        self.db.flush()
        contract._file = ContractFile(org_id=self.org_id, contract_id=contract.id, file_label="NDA.docx")
        self.db.add(contract._file)
        self.db.flush()
        return contract

    def version(self, contract: Contract, text: str, source: str = "manual_edit") -> ContractVersion:
        stored = StorageObject(
            org_id=self.org_id, storage_key=f"test/{uuid.uuid4().hex}", filename="NDA.docx",
            mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex,
        )
        self.db.add(stored)
        self.db.flush()
        number = 1 + len(self.db.scalars(
            select(ContractVersion.id).where(ContractVersion.contract_id == contract.id)).all())
        if contract.current_authoritative_version_id:
            self.db.get(ContractVersion, contract.current_authoritative_version_id).is_authoritative = False
            self.db.flush()
        version = ContractVersion(
            org_id=self.org_id, contract_id=contract.id, contract_file_id=contract._file.id,
            version_number=number, storage_object_id=stored.id, source=source, is_authoritative=True,
        )
        self.db.add(version)
        self.db.flush()
        snap = ContractTextSnapshot(org_id=self.org_id, contract_id=contract.id, contract_version_id=version.id,
                                    extraction_method="text", text=text)
        self.db.add(snap)
        self.db.flush()
        version.text_snapshot_id = snap.id
        contract.current_authoritative_version_id = version.id
        self.db.flush()
        return version

    def analysed(self, version: ContractVersion, skill: str = "clause_labeling", *,
                 status: str = "succeeded", prompt_hash: str | None = None,
                 output: dict | None = None) -> AISkillRun:
        """A finished skill run on this version, plus the clauses it wrote."""
        spec = skill_registry.get(skill)
        if output is None and skill == "contract_metadata_extraction":
            output = {"jurisdiction": "Delaware", "confidence": "low"}
        run = AISkillRun(
            org_id=self.org_id, skill_name=skill, skill_version=spec.version, execution_mode=spec.execution_mode,
            status=status, resource_type="contract", resource_id=version.contract_id,
            contract_id=version.contract_id, contract_version_id=version.id,
            text_snapshot_id=version.text_snapshot_id, prompt_key=spec.prompt_key,
            prompt_version=spec.prompt_version, prompt_hash=prompt_hash or active_hash(self.db, self.org_id, skill),
            model=active_bundle(self.db, self.org_id, skill).model_name, started_at=utcnow(),
            output_payload=output,
        )
        self.db.add(run)
        self.db.flush()
        if skill in ("clause_labeling", "clause_extraction"):
            start = TEXT.index("1. Confidentiality")
            self.db.add(ClauseExtraction(
                org_id=self.org_id, contract_id=version.contract_id, contract_version_id=version.id,
                text_snapshot_id=version.text_snapshot_id, clause_type="confidentiality", heading="1.",
                text=TEXT[start:-1], start_char=start, end_char=len(TEXT) - 1, confidence="high",
            ))
            self.db.flush()
        return run

    def job(self, version: ContractVersion, job_type: str = "clause_extraction") -> JobRun:
        job = JobRun(
            org_id=self.org_id, job_type=job_type, resource_type="contract", resource_id=version.contract_id,
            created_by_user_id=self.user.id, idempotency_key=f"{job_type}:{version.id}:{version.text_snapshot_id}",
            metadata_json={"contract_version_id": version.id, "text_snapshot_id": version.text_snapshot_id},
        )
        self.db.add(job)
        self.db.flush()
        return job

    def run(self, job: JobRun) -> JobRun:
        asyncio.run(tasks._run_ai_job(job.id))
        self.db.expire_all()
        return self.db.get(JobRun, job.id)


def active_bundle(db: Session, org_id: str, skill: str):
    spec = skill_registry.get(skill)
    return get_active_prompt_bundle(
        db, org_id=org_id, prompt_key=spec.prompt_key, default_version=spec.prompt_version,
        model_config={"temperature": spec.temperature, "max_tokens": spec.max_tokens},
    )


def active_hash(db: Session, org_id: str, skill: str) -> str:
    return active_bundle(db, org_id, skill).prompt_hash


def _live_clauses(db: Session, contract: Contract) -> list[ClauseExtraction]:
    return db.scalars(select(ClauseExtraction).where(
        ClauseExtraction.contract_id == contract.id, ClauseExtraction.is_stale.is_(False))).all()


def test_identical_text_reuses_the_clauses_without_a_model_call(db, paid):
    """A signed copy / no-op save paid for clause extraction again (7 of 7 signed
    copies did). It must run no skill, re-point the donor's clauses at the new
    version with the same offsets, say where they came from, and still chain
    the knowledge-graph rebuild retrieval depends on."""
    w = World(db)
    contract = w.contract()
    v1 = w.version(contract, TEXT)
    w.analysed(v1)
    v2 = w.version(contract, TEXT, source="signed")

    job = w.run(w.job(v2))

    assert paid == []
    assert job.status == JobStatus.SUCCEEDED
    assert job.metadata_json["carried_from_snapshot_id"] == v1.text_snapshot_id
    live = _live_clauses(db, contract)
    assert [(c.contract_version_id, c.text_snapshot_id) for c in live] == [(v2.id, v2.text_snapshot_id)]
    assert TEXT[live[0].start_char:live[0].end_char] == live[0].text
    assert db.scalar(select(JobRun).where(
        JobRun.job_type == "contract_brain_ingestion", JobRun.resource_id == contract.id,
        JobRun.metadata_json["contract_version_id"].as_string() == v2.id,
    )) is not None


def test_identical_text_metadata_job_succeeds_without_a_model_call(db, paid):
    """Metadata is contract-level: identical text gives identical values, so the
    job is satisfied by the earlier run and writes nothing."""
    w = World(db)
    contract = w.contract()
    v1 = w.version(contract, TEXT)
    w.analysed(v1, "contract_metadata_extraction")
    v2 = w.version(contract, TEXT)

    job = w.run(w.job(v2, "metadata_extraction"))

    assert paid == []
    assert job.status == JobStatus.SUCCEEDED
    assert job.metadata_json["carried_from_snapshot_id"] == v1.text_snapshot_id


def test_changed_text_pays(db, paid):
    """One changed character is a different contract text: it must be analysed."""
    w = World(db)
    contract = w.contract()
    w.analysed(w.version(contract, TEXT))
    v2 = w.version(contract, TEXT.replace("secret", "confidential"))

    w.run(w.job(v2))

    assert paid == ["clause_extraction"]


def test_a_donor_under_a_different_prompt_is_not_reused(db, paid):
    """A prompt change must re-run: the old prompt's answer is not this prompt's."""
    w = World(db)
    contract = w.contract()
    w.analysed(w.version(contract, TEXT), prompt_hash="0" * 64)
    v2 = w.version(contract, TEXT)

    w.run(w.job(v2))

    assert paid == ["clause_extraction"]


def test_a_donor_that_did_not_succeed_is_not_reused(db, paid):
    """A needs_review (e.g. fallback) run is not an answer worth copying."""
    w = World(db)
    contract = w.contract()
    w.analysed(w.version(contract, TEXT), "contract_metadata_extraction", status="needs_review")
    v2 = w.version(contract, TEXT)

    w.run(w.job(v2, "metadata_extraction"))

    assert paid == ["contract_metadata_extraction"]


def test_a_donor_on_another_contract_is_not_reused(db, paid):
    """Same template, same facts, different contract: never borrow across contracts."""
    w = World(db)
    other = w.contract()
    w.analysed(w.version(other, TEXT))
    contract = w.contract()
    v1 = w.version(contract, TEXT)

    w.run(w.job(v1))

    assert paid == ["clause_extraction"]
    assert _live_clauses(db, contract) == []


def _reviewed(w: World, contract: Contract, version: ContractVersion, *, current_playbook: bool = True) -> None:
    playbook = Playbook(org_id=w.org_id, name="NDA playbook")
    w.db.add(playbook)
    w.db.flush()
    pv = PlaybookVersion(org_id=w.org_id, playbook_id=playbook.id, version_number=1, status="published")
    w.db.add(pv)
    w.db.flush()
    playbook.current_version_id = pv.id
    if not current_playbook:  # the rules were replaced: a newer published version exists
        w.db.add(PlaybookVersion(org_id=w.org_id, playbook_id=playbook.id, version_number=2, status="published"))
        w.db.flush()
    w.db.add(PlaybookRun(
        org_id=w.org_id, playbook_id=playbook.id, playbook_version_id=pv.id, contract_id=contract.id,
        contract_version_id=version.id, status="succeeded", model_name="claude", validation_status="valid",
    ))
    contract.metadata_json = {"auto_review_pending": True}
    w.db.flush()


@pytest.fixture
def reviews(monkeypatch):
    from app.playbooks.service import PlaybooksService

    calls: list[str] = []

    async def review(self, **_k):
        calls.append("playbook")
        return {"ran": True}

    monkeypatch.setattr(PlaybooksService, "auto_review_contract", review)
    return calls


def test_carried_clauses_skip_a_review_that_still_holds(db, paid, reviews):
    """An unchanged counterparty return re-ran the paid playbook review for the text
    Claude had just reviewed (the risk band is computed from that review, free)."""
    w = World(db)
    contract = w.contract()
    v1 = w.version(contract, TEXT)
    w.analysed(v1)
    _reviewed(w, contract, v1)
    v2 = w.version(contract, TEXT, source="counterparty_revision")

    w.run(w.job(v2))

    assert paid == [] and reviews == []
    assert "auto_review_pending" not in (db.get(Contract, contract.id).metadata_json or {})


def test_carried_clauses_still_review_when_the_old_review_is_against_a_superseded_playbook(db, paid, reviews):
    """Publishing never archives the older playbook version, so a review against
    it is not this playbook's review: the auto review must run."""
    w = World(db)
    contract = w.contract()
    v1 = w.version(contract, TEXT)
    w.analysed(v1)
    _reviewed(w, contract, v1, current_playbook=False)
    v2 = w.version(contract, TEXT, source="counterparty_revision")

    w.run(w.job(v2))

    assert paid == []
    # The playbook review only: the paid AI risk score is gone; the review refreshes the band.
    assert reviews == ["playbook"]


def test_text_restored_to_earlier_wording_gets_that_texts_metadata_back(db, paid):
    """Carrying metadata used to write nothing. Text X -> Y -> X again (a restore, or an edit
    back to old wording) kept the values extracted from Y, though the contract now says X. The
    donor's answer for X is written again through the normal path, still with no model call."""
    w = World(db)
    contract = w.contract()
    v1 = w.version(contract, TEXT)
    first = w.analysed(v1, "contract_metadata_extraction")
    w.version(contract, TEXT.replace("secret", "private"))
    contract.metadata_json = {"latest_metadata_skill_run_id": "run-for-the-other-text"}
    db.flush()
    v3 = w.version(contract, TEXT, source="restored")

    job = w.run(w.job(v3, "metadata_extraction"))

    assert paid == []
    assert job.metadata_json["carried_from_snapshot_id"] == v1.text_snapshot_id
    db.refresh(contract)
    assert contract.metadata_json["latest_metadata_skill_run_id"] == first.id
    assert contract.metadata_json["ai_suggestions"]["metadata"]["jurisdiction"] == "Delaware"


def test_a_later_unfinished_run_is_never_reused_or_mixed_in(db, paid):
    """The donor used to be the newest SUCCEEDED run, copying every row made since it started,
    so a later needs_review run's rows on the same snapshot came along too (duplicates). Only
    the newest run of the skill may be reused, and only when it succeeded."""
    w = World(db)
    contract = w.contract()
    v1 = w.version(contract, TEXT)
    w.analysed(v1)
    w.analysed(v1, status="needs_review")
    v2 = w.version(contract, TEXT, source="signed")

    w.run(w.job(v2))

    assert paid == ["clause_extraction"]


def test_identical_text_replays_obligations_counted_from_todays_anchors(db, paid):
    """The reused answer is the AI's wording only: replaying it must not call the
    model, and the due date must follow the contract's CURRENT effective date,
    not the one the donor run saw (a person corrected it in between)."""
    from datetime import date

    from app.obligations.models import Obligation

    w = World(db)
    contract = w.contract()
    contract.effective_date = date(2026, 10, 15)
    contract.metadata_json = {"field_sources": {"effective_date": "user"}}
    v1 = w.version(contract, TEXT)
    w.analysed(v1, "obligation_extraction", output={"obligations": [{
        "description": "Return confidential information", "obligation_type": "return",
        "timing": {"kind": "deadline", "period_number": 3, "period_unit": "months",
                   "anchor": "effective_date", "stated_in_text": True},
        "citations": [{"quote": "within three (3) months of the Effective Date"}],
    }]})
    contract.effective_date = date(2026, 12, 1)
    v2 = w.version(contract, TEXT, source="signed")

    job = w.run(w.job(v2, "obligation_extraction"))

    assert paid == []
    assert job.metadata_json["carried_from_snapshot_id"] == v1.text_snapshot_id
    [ob] = db.scalars(select(Obligation).where(Obligation.contract_id == contract.id)).all()
    assert ob.due_date == date(2027, 3, 1)
    assert ob.metadata_json["deadline"]["formula"].startswith("due Mon 1 Mar 2027 = effective date")
