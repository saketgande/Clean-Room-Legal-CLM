"""LLM-01: an empty model output must never look like a safe result: no risk
score of 0 banded "low", and no wiping of the clauses search and the graph use."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.controller import ai_controller
from app.ai.schemas import ClauseExtractionOutput, ClauseRiskOutput, ContractRiskOutput
from app.contracts import risk


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


def test_empty_risk_assessment_is_unknown_not_low(monkeypatch):
    async def empty_assessment(*args, **kwargs):
        return ContractRiskOutput(clause_risks=[], summary=None)

    monkeypatch.setattr(risk.ai_controller, "run_structured_skill", empty_assessment)
    contract = SimpleNamespace(id="c-1", org_id="org-1", title="MSA", risk_score=12, risk_band="low",
                               risk_level="low", risk_summary=None, updated_by_user_id=None)
    clause = SimpleNamespace(clause_type="limitation_of_liability", text="Liability is capped at fees paid.")
    summary = asyncio.run(risk.compute_contract_risk(FakeDB([clause]), contract=contract, user=SimpleNamespace(id="u-1")))
    assert summary["score"] is None
    assert summary["band"] == "unknown"
    assert (contract.risk_score, contract.risk_band, contract.risk_level) == (None, None, None)


def test_clause_risk_without_a_level_fails_validation():
    with pytest.raises(ValidationError):
        ClauseRiskOutput(clause_type="indemnity", rationale="Uncapped indemnity.", quote="shall indemnify")


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
