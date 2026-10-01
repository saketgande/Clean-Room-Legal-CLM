"""A job whose handler failed mid-flush must still land in FAILED, and NUL bytes
in extracted text must never reach Postgres (which rejects them in TEXT)."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import PendingRollbackError

import app.models  # noqa: F401  (register every mapper)
from app.contract_files.models import ContractTextSnapshot
from app.core.enums import JobStatus
from app.jobs import tasks


class BrokenFlushSession:
    """Mimics a session after a failed flush: every use raises until rollback()."""

    def __init__(self, job):
        self.job = job
        self.broken = False

    def scalar(self, _stmt):
        return self.job

    def get(self, _model, _key):
        if self.broken:
            raise PendingRollbackError("rolled back due to a previous exception during flush")
        return self.job

    def commit(self):
        if self.broken:
            raise PendingRollbackError("rolled back due to a previous exception during flush")

    def rollback(self):
        self.broken = False

    def close(self):
        pass


def test_failed_flush_marks_job_failed_instead_of_stuck_running(monkeypatch):
    """Without a rollback in the except block the FAILED write raised
    PendingRollbackError, so the job stayed RUNNING and never retried."""
    job = SimpleNamespace(
        id="job-1", job_type="metadata_extraction", resource_id="k-1", status=JobStatus.QUEUED,
        started_at=None, finished_at=None, attempt_count=0, progress=0, metadata_json={},
        error_message=None, error_stack=None,
    )
    db = BrokenFlushSession(job)

    async def failing_skill(*_a, **_k):
        db.broken = True
        raise ValueError("PostgreSQL text fields cannot contain NUL (0x00) bytes")

    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "_should_run", lambda *_a: True)
    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", failing_skill)

    with pytest.raises(ValueError):
        asyncio.run(tasks._run_ai_job("job-1"))
    assert job.status == JobStatus.FAILED
    assert "NUL" in job.error_message


def test_snapshot_text_strips_nul_bytes():
    """pypdf/OCR output with a NUL crashed the snapshot INSERT."""
    assert ContractTextSnapshot(text="MZ\x00exe\x00").text == "MZexe"
