"""An eval scorecard's LLM judge found the contract_risk_assessment skill
shipping "low" risk clause calls with no supporting quote at all — 6 of 9
clauses in a real run — so a lawyer had no way to verify those weren't
invented. Pin that every risk call, not just medium/high, must now be
grounded in a real quote."""

import pytest
from pydantic import ValidationError

from app.ai.schemas import ClauseRiskOutput


def test_clause_risk_quote_is_required():
    field = ClauseRiskOutput.model_fields["quote"]
    assert field.annotation is str
    assert field.is_required()


def test_clause_risk_without_a_quote_fails_validation():
    with pytest.raises(ValidationError):
        ClauseRiskOutput(clause_type="confidentiality", risk="low", rationale="Standard mutual NDA language.")


def test_clause_risk_with_a_quote_validates():
    out = ClauseRiskOutput(clause_type="confidentiality", risk="low", rationale="Standard mutual NDA language.",
                            quote="Each Party may disclose Confidential Information only to employees with a need to know.")
    assert out.quote
