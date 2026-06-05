"""F-09 contract-handle TOCTOU tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_existing_handle_returned_without_allocate(monkeypatch):
    """``allocate_contract_handle`` returns the pre-existing row without inserting."""
    from app.ai import session_state

    existing = SimpleNamespace(
        id="h-1",
        org_id="org-A",
        session_id="s-1",
        contract_id="c-1",
        handle="contract-7",
    )
    db = MagicMock()
    db.scalar = MagicMock(return_value=existing)
    db.add = MagicMock()
    record = session_state.allocate_contract_handle(
        db,
        org_id="org-A",
        session_id="s-1",
        contract_id="c-1",
        user_id="u-1",
    )
    assert record is existing
    db.add.assert_not_called()


def test_advisory_lock_skipped_on_sqlite(monkeypatch):
    """SQLite test path does NOT execute the ``pg_advisory_xact_lock`` query."""
    from app.ai import session_state

    monkeypatch.setattr(
        session_state.settings,
        "database_url",
        "sqlite:///./test.db",
        raising=False,
    )
    db = MagicMock()
    db.execute = MagicMock()
    session_state._acquire_session_lock(db, "s-1")
    db.execute.assert_not_called()


def test_lock_keys_are_session_specific():
    """Different session_ids produce different lock keys."""
    from app.ai.session_state import _session_lock_key

    key_a = _session_lock_key("session-a")
    key_b = _session_lock_key("session-b")
    assert key_a != key_b
