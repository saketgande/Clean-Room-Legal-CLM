"""LLM-01: an empty model output must never look like a safe result: no wiping
of the clauses search and the graph use. (The AI risk score this also guarded is
gone; tests/test_one_risk_signal.py pins that no review is "unknown", not low.)"""

from types import SimpleNamespace

import pytest

from app.ai.controller import ai_controller
from app.ai.schemas import ClauseExtractionOutput


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _stmt):
        return _Rows(self.rows)

    def scalar(self, _stmt):
        return "v-1"  # the extracted version is the current one

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _context():
    return SimpleNamespace(
        version=SimpleNamespace(id="v-1"),
        snapshot=SimpleNamespace(id="s-1", text="1. Term. One year."),
        contract=SimpleNamespace(id="c-1", org_id="org-1"),
    )


def test_empty_clause_extraction_keeps_existing_clauses():
    existing = [SimpleNamespace(is_stale=False, updated_by_user_id=None) for _ in range(3)]
    with pytest.raises(ValueError):
        ai_controller._persist_clauses(
            FakeDB(existing), output=ClauseExtractionOutput(clauses=[]), context=_context(), created_by_user_id="u-1"
        )
    assert not any(c.is_stale for c in existing)


def test_empty_clause_extraction_with_nothing_to_replace_is_a_no_op():
    ai_controller._persist_clauses(
        FakeDB([]), output=ClauseExtractionOutput(clauses=[]), context=_context(), created_by_user_id="u-1"
    )
