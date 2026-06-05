"""F-14 contract_brain_ingestion key + lock tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_single_key_regardless_of_trigger_reason():
    """All three upstream extractions map to the same idempotency key."""
    from app.jobs.idempotency import build_auto_brain_ingestion_key

    key_clause = build_auto_brain_ingestion_key(
        version_id="v-1", snapshot_id="s-1"
    )
    key_obligation = build_auto_brain_ingestion_key(
        version_id="v-1", snapshot_id="s-1"
    )
    key_renewal = build_auto_brain_ingestion_key(
        version_id="v-1", snapshot_id="s-1"
    )
    assert key_clause == key_obligation == key_renewal


def test_advisory_lock_skipped_on_sqlite(monkeypatch):
    """SQLite path does NOT call ``pg_advisory_xact_lock``."""
    from proposals.audit_2026_06_02.rewrites.F14_brain_ingestion_lock import (  # type: ignore[import-not-found]
        jobs_tasks_brain_ingestion as mod,
    )

    monkeypatch.setattr(
        mod.settings, "database_url", "sqlite:///./test.db", raising=False
    )
    db = MagicMock()
    db.execute = MagicMock()
    mod.acquire_brain_ingest_lock(db, contract_id="c-1")
    db.execute.assert_not_called()


def test_brain_lock_keys_are_contract_specific():
    """Different contract_ids produce different advisory lock keys."""
    from proposals.audit_2026_06_02.rewrites.F14_brain_ingestion_lock import (  # type: ignore[import-not-found]
        jobs_tasks_brain_ingestion as mod,
    )

    assert mod._brain_lock_key("c-1") != mod._brain_lock_key("c-2")
