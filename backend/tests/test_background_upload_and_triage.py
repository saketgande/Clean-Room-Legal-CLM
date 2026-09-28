"""API-01: the upload and intake-submission requests stay small (store, insert,
enqueue, return); text extraction, OCR, metadata and AI triage run in background
jobs, and a retried submission returns the existing record instead of a duplicate."""

import asyncio
import inspect
import uuid
from types import SimpleNamespace

import pytest

import app.jobs.service as job_service
import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contract_files import service as uploads
from app.contract_files.models import ContractTextSnapshot, ContractVersion, StorageObject
from app.contracts import lifecycle
from app.contracts import routes as contract_routes
from app.contracts.models import Contract
from app.intake import routes as intake_routes
from app.intake import service as intake
from app.intake.schemas import RequestCreate
from app.jobs import tasks
from app.jobs.models import JobRun

USER = SimpleNamespace(id="u-1", org_id="org-1")


class FakeDB:
    def __init__(self, scalar=None, by_model=None):
        self._scalar = scalar
        self.by_model = by_model or {}
        self.added = []

    def scalar(self, _stmt):
        return self._scalar

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = str(uuid.uuid4())

    def get(self, model, key):
        if model in self.by_model:
            return self.by_model[model]
        return next((o for o in self.added if getattr(o, "id", None) == key), None)

    def commit(self):
        pass

    def refresh(self, _obj):
        pass

    def rollback(self):
        pass


@pytest.fixture
def quiet_uploads(monkeypatch):
    for name in ("write_audit_log", "write_timeline_event"):
        monkeypatch.setattr(uploads, name, lambda db, **kw: None)


def test_the_upload_request_stores_and_enqueues_without_extracting(monkeypatch, quiet_uploads):
    async def must_not_extract(**kwargs):
        raise AssertionError("text extraction must not run inside the upload request")

    monkeypatch.setattr(uploads, "_resolve_extracted_text", must_not_extract)
    monkeypatch.setattr(uploads.storage_service, "save_bytes", lambda **kw: SimpleNamespace(
        storage_key="org-1/big.pdf", filename="big.pdf", mime_type="application/pdf", size_bytes=12, sha256_hash="h"))
    dispatched = []
    monkeypatch.setattr(uploads, "dispatch_job", lambda db, *, job: dispatched.append(job.job_type) or job)
    chunks = [b"%PDF-1.4 test", b""]

    async def read(_size):
        return chunks.pop(0)

    upload = SimpleNamespace(content_type="application/pdf", filename="big.pdf", read=read)
    db = FakeDB(scalar=None)
    result = asyncio.run(uploads.create_contract_from_upload(db, upload=upload, user=USER, defer_processing=True))
    assert result["extraction_method"] == "pending"
    assert result["text_snapshot_id"] is None
    assert result["queued_jobs"] == ["document_text_extraction"]
    assert dispatched == ["document_text_extraction"]
    assert not [o for o in db.added if isinstance(o, ContractTextSnapshot)]


def test_the_background_job_extracts_text_and_moves_the_contract_to_review(monkeypatch, quiet_uploads):
    version = SimpleNamespace(id="v-1", contract_id="c-1", storage_object_id="so-1", text_snapshot_id=None)
    contract = SimpleNamespace(id="c-1", org_id="org-1", lifecycle_stage="intake")
    db = FakeDB(by_model={
        ContractVersion: version, Contract: contract,
        StorageObject: SimpleNamespace(storage_key="k", mime_type="application/pdf", filename="big.pdf"),
        User: USER,
    })
    monkeypatch.setattr(uploads.storage_service, "read_bytes", lambda key: b"%PDF-1.4")

    async def extracted(**kwargs):
        return uploads._ExtractedText(method="pdf_text", text="1. Term. One year.", quality_score=0.9, page_map=None)

    async def no_metadata(db, **kwargs):
        return None

    monkeypatch.setattr(uploads, "_resolve_extracted_text", extracted)
    monkeypatch.setattr(uploads, "_fill_contract_metadata", no_metadata)
    monkeypatch.setattr(uploads, "_persist_document_elements", lambda db, snapshot, elements: None)
    monkeypatch.setattr(uploads, "_queue_initial_contract_jobs", lambda db, **kw: [])
    monkeypatch.setattr(uploads, "_dispatch_initial_jobs", lambda db, **kw: ([], []))

    def fake_transition(db, *, contract, to_stage, **kwargs):
        contract.lifecycle_stage = to_stage

    monkeypatch.setattr(lifecycle, "transition_contract_stage", fake_transition)
    job = SimpleNamespace(metadata_json={"contract_version_id": "v-1"}, created_by_user_id="u-1")
    outcome = asyncio.run(uploads.process_uploaded_document(db, job=job))
    assert version.text_snapshot_id == outcome["text_snapshot_id"]
    assert contract.lifecycle_stage == "review"

    monkeypatch.setattr(uploads.storage_service, "read_bytes", lambda key: pytest.fail("re-run must not re-extract"))
    assert asyncio.run(uploads.process_uploaded_document(db, job=job))["already_processed"] is True


def test_a_retried_upload_returns_the_contract_already_created(monkeypatch):
    existing = SimpleNamespace(id="v-1", contract_id="c-1", contract_file_id="f-1", text_snapshot_id=None)
    db = FakeDB(scalar=existing, by_model={Contract: SimpleNamespace(id="c-1", deleted_at=None)})
    duplicate = uploads._recent_duplicate_upload(db, user=USER, content=b"%PDF-1.4 test")
    assert duplicate["contract_version_id"] == "v-1"
    assert duplicate["extraction_method"] == "pending"


@pytest.fixture
def quiet_intake(monkeypatch):
    for name in ("write_audit_log", "write_timeline_event"):
        monkeypatch.setattr(intake, name, lambda db, **kw: None)
    monkeypatch.setattr(intake, "_next_ref", lambda db: "REQ-1")
    monkeypatch.setattr(intake, "serialize_request", lambda db, r: {"id": r.id, "ai_triage": r.ai_triage})


def test_submitting_a_request_queues_triage_instead_of_running_it(monkeypatch, quiet_intake):
    from app.intake import triage_agent

    monkeypatch.setattr(triage_agent, "triage", lambda db, r: pytest.fail("triage must not run in the request"))
    dispatched = []
    monkeypatch.setattr(job_service, "dispatch_job", lambda db, *, job: dispatched.append(job.job_type) or job)
    payload = RequestCreate(type_label="NDA", description="Mutual NDA with Acme")
    result = intake.create_request(FakeDB(scalar=None), actor=USER, payload=payload, defer_triage=True)
    assert result["ai_triage"] == {"status": "pending"}
    # Screening rides alongside triage now: it is a durable job queued in the
    # request's own transaction rather than best-effort work run inline after
    # the commit, where a crash left the request permanently unscreened.
    assert dispatched == ["intake_triage", "intake_screening"]


def test_a_retried_submission_returns_the_existing_request(quiet_intake):
    existing = SimpleNamespace(id="req-existing", ai_triage={"status": "pending"})
    db = FakeDB(scalar=existing)
    payload = RequestCreate(type_label="NDA", description="Mutual NDA with Acme")
    assert intake.create_request(db, actor=USER, payload=payload, defer_triage=True)["id"] == "req-existing"
    assert db.added == []


def test_background_triage_runs_once(monkeypatch):
    calls = []
    request = SimpleNamespace(id="req-1", org_id="org-1", ai_triage={"status": "pending"})
    monkeypatch.setattr(intake, "write_timeline_event", lambda db, **kw: None)

    def apply(db, r):
        calls.append("triage")
        r.ai_triage = {"category": "NDA"}

    monkeypatch.setattr(intake, "_apply_ai_triage", apply)
    monkeypatch.setattr(intake, "_assign_and_notify", lambda db, r, actor: calls.append("assign"))
    monkeypatch.setattr(intake, "_enrich_after_commit", lambda db, r, actor: calls.append("enrich"))

    class DB(FakeDB):
        def get(self, model, key):
            return request if key == "req-1" else USER

    intake.run_intake_triage(DB(), request_id="req-1", actor_id="u-1")
    intake.run_intake_triage(DB(), request_id="req-1", actor_id="u-1")
    assert calls == ["triage", "assign", "enrich"]


def test_the_web_entry_points_opt_in_and_the_worker_handles_both_jobs():
    assert "defer_processing=True" in inspect.getsource(contract_routes.upload_contract)
    assert "defer_triage=True" in inspect.getsource(intake_routes.create_request)
    runner = inspect.getsource(tasks._run_ai_job)
    assert '"document_text_extraction"' in runner and '"intake_triage"' in runner
    assert JobRun  # the job table both halves share
