"""LLM-03: model confidence has one numeric form wherever code compares it, and
enum-like fields only hold their allowed values."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

import app.models  # noqa: F401  (register every mapper)
from app.ai import controller as controller_module
from app.ai.schemas import ContractMetadataOutput, confidence_score
from app.contracts.schemas import ContractUpdate
from app.workflows import service


def test_confidence_has_one_numeric_form():
    assert confidence_score("high") == 0.9
    assert confidence_score(" Medium ") == 0.6
    assert confidence_score(0.42) == 0.42
    assert confidence_score("0.8") == 0.8
    assert confidence_score(7) == 1.0
    assert confidence_score("Moderate to High") == 0.0  # unreadable never clears a gate
    assert confidence_score(True) == 0.0
    assert confidence_score(None) is None


class _SkillOut(BaseModel):
    confidence: str


def _run_skill_step(monkeypatch, confidence):
    async def fake_skill(*_args, **_kwargs):
        return _SkillOut(confidence=confidence)

    monkeypatch.setattr(controller_module.ai_controller, "run_structured_skill", fake_skill)
    request = SimpleNamespace(id="req-1", type_label="Contract review", description="Please review.",
                              screening=None, ai_triage={}, field_values={}, priority="Medium")
    step = {"type": "ai_task", "name": "AI review",
            "config": {"skill": "custom_review_skill", "escalate_role": "legal", "escalate_below_confidence": 0.7}}
    sr = SimpleNamespace(status="running", note=None, result=None, updated_at=None)
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id=None, flow_name="Review")
    outcome = asyncio.run(service._execute_step(SimpleNamespace(get=lambda *_: request), run=run, step=step, sr=sr,
                                                actor=SimpleNamespace(id="u-1")))
    return outcome, sr


def test_a_text_confidence_below_the_threshold_escalates_instead_of_crashing(monkeypatch):
    outcome, sr = _run_skill_step(monkeypatch, "medium")
    assert outcome == "wait" and sr.status == "waiting_human"
    assert "0.6 < 0.7" in sr.note


def test_a_text_confidence_above_the_threshold_advances(monkeypatch):
    outcome, _ = _run_skill_step(monkeypatch, "high")
    assert outcome == "advance"


def test_risk_level_only_holds_low_medium_or_high():
    assert ContractMetadataOutput(risk_level="Moderate to High").risk_level is None
    assert ContractMetadataOutput(risk_level=" HIGH ").risk_level == "high"
    field = TypeAdapter(ContractUpdate.model_fields["risk_level"].annotation)
    assert field.validate_python("low") == "low"
    with pytest.raises(ValidationError):
        field.validate_python("Moderate to High")
