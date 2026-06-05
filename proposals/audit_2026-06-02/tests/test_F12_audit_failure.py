"""F-12 audit-log failure-mode tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_audit_chain_corrupted_exception_class_exists():
    """The new exception class is importable from the rewrite module."""
    from proposals.audit_2026_06_02.rewrites.F12_audit_log_postgres_error import (  # type: ignore[import-not-found]
        core_audit_record as mod,
    )

    exc = mod.AuditChainCorrupted("approval.requested", "test")
    assert exc.action == "approval.requested"
    assert "audit_chain_corrupted" in str(exc)


def test_sqlite_skips_advisory_lock(monkeypatch):
    """On SQLite, ``_acquire_chain_lock`` is a no-op."""
    from proposals.audit_2026_06_02.rewrites.F12_audit_log_postgres_error import (  # type: ignore[import-not-found]
        core_audit_record as mod,
    )

    monkeypatch.setattr(
        mod.settings, "database_url", "sqlite:///./test.db", raising=False
    )
    db = MagicMock()
    db.execute = MagicMock()
    mod._acquire_chain_lock(db)
    db.execute.assert_not_called()


def test_postgres_lock_failure_raises_audit_chain_corrupted(monkeypatch):
    """A SQLAlchemy error during the advisory lock raises ``AuditChainCorrupted``."""
    from sqlalchemy.exc import SQLAlchemyError

    from proposals.audit_2026_06_02.rewrites.F12_audit_log_postgres_error import (  # type: ignore[import-not-found]
        core_audit_record as mod,
    )

    monkeypatch.setattr(
        mod.settings, "database_url", "postgresql+psycopg://x", raising=False
    )

    class _Boom:
        def execute(self, *args, **kwargs):  # noqa: ARG002
            raise SQLAlchemyError("connection dropped")

        def rollback(self):
            return None

    raised = False
    try:
        mod._acquire_chain_lock(_Boom())
    except mod.AuditChainCorrupted as exc:
        raised = exc.retryable is True
    assert raised
