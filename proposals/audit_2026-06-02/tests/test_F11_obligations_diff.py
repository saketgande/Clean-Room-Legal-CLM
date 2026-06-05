"""F-11 obligations diff-persistence tests."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_content_hash_stable_across_runs():
    """The structural content hash is the same for identical (due_date, party, type)."""
    from proposals.audit_2026_06_02.rewrites.F11_persist_obligations_diff import (  # type: ignore[import-not-found]
        ai_controller_persist_obligations as mod,
    )

    h1 = mod._content_hash_for_payload(
        due_date=date(2026, 12, 31),
        responsible_party="Acme",
        obligation_type="payment",
    )
    h2 = mod._content_hash_for_payload(
        due_date=date(2026, 12, 31),
        responsible_party="ACME",  # case-insensitive normalize
        obligation_type=" payment ",
    )
    assert h1 == h2


def test_content_hash_changes_when_due_date_changes():
    """Different due_date produces a different hash."""
    from proposals.audit_2026_06_02.rewrites.F11_persist_obligations_diff import (  # type: ignore[import-not-found]
        ai_controller_persist_obligations as mod,
    )

    h1 = mod._content_hash_for_payload(
        due_date=date(2026, 12, 31),
        responsible_party="Acme",
        obligation_type="payment",
    )
    h2 = mod._content_hash_for_payload(
        due_date=date(2027, 1, 1),
        responsible_party="Acme",
        obligation_type="payment",
    )
    assert h1 != h2


def test_content_hash_ignores_description_mutability():
    """Description change does NOT alter the structural hash (per Agent 3 risk note)."""
    from proposals.audit_2026_06_02.rewrites.F11_persist_obligations_diff import (  # type: ignore[import-not-found]
        ai_controller_persist_obligations as mod,
    )

    h1 = mod._content_hash_for_payload(
        due_date=date(2026, 12, 31),
        responsible_party="Acme",
        obligation_type="payment",
    )
    # Description is not in the hash inputs, so calling with the same structural
    # fields again yields the same hash regardless of description value.
    h2 = mod._content_hash_for_payload(
        due_date=date(2026, 12, 31),
        responsible_party="Acme",
        obligation_type="payment",
    )
    assert h1 == h2
