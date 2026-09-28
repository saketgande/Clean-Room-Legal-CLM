"""JOB-04: the daily sweeps touch only actionable rows, in bounded batches.
API-04: repeated graph-rebuild clicks reuse the job already in flight."""

import asyncio
import inspect
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.contract_brain import routes as brain_routes
from app.jobs import tasks


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class RecordingDB:
    def __init__(self):
        self.statements = []

    def execute(self, stmt):
        self.statements.append(_sql(stmt))
        return SimpleNamespace(rowcount=0)

    def scalars(self, stmt):
        self.statements.append(_sql(stmt))
        return SimpleNamespace(all=list)

    def commit(self):
        pass

    def close(self):
        pass


def test_obligation_statuses_are_recomputed_without_loading_every_obligation(monkeypatch):
    db = RecordingDB()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    asyncio.run(tasks._send_obligation_reminders())
    updates = [s for s in db.statements if s.startswith("UPDATE obligation")]
    assert len(updates) == 3
    selects = [s for s in db.statements if s.startswith("SELECT")]
    assert all("FROM obligation_reminder" in s and "LIMIT" in s for s in selects)


def test_renewal_sweep_selects_only_actionable_unnotified_events_in_batches(monkeypatch):
    db = RecordingDB()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    asyncio.run(tasks._run_renewal_window_check())
    [query] = db.statements
    assert "LIMIT" in query
    assert "renewal_event.expiration_date >=" in query
    assert "renewal_event.metadata_json ->>" in query
    assert "contract.renewal_due IS false" in query


def test_a_repeat_rebuild_click_returns_the_job_in_flight():
    running = SimpleNamespace(id="job-1", status="running")
    db = SimpleNamespace(scalar=lambda stmt: running)
    assert brain_routes._active_ingestion_job(db, org_id="org-1", contract_id="c-1") is running
    source = inspect.getsource(brain_routes.trigger_brain_ingestion)
    assert "_active_ingestion_job(" in source
    assert 'require_permission("contract:update")' in source
    assert "@limiter.limit(" in source
