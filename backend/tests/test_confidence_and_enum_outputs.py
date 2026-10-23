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
    confidence: str | None


_UNSET = object()


def _run_skill_step(monkeypatch, confidence, *, threshold=0.7, config=_UNSET):
    calls = []

    async def fake_skill(*_args, **_kwargs):
        calls.append(_kwargs)
        return _SkillOut(confidence=confidence)

    monkeypatch.setattr(controller_module.ai_controller, "run_structured_skill", fake_skill)
    request = SimpleNamespace(id="req-1", type_label="Contract review", description="Please review.",
                              screening=None, ai_triage={}, field_values={}, priority="Medium")
    if config is _UNSET:
        config = {"skill": "custom_review_skill", "escalate_role": "legal"}
        if threshold is not None:
            config["escalate_below_confidence"] = threshold
    step = {"type": "ai_task", "name": "AI review", "config": config}
    sr = SimpleNamespace(status="running", note=None, result=None, updated_at=None)
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id=None, flow_name="Review")
    outcome = asyncio.run(service._execute_step(SimpleNamespace(get=lambda *_: request, commit=lambda: None), run=run, step=step, sr=sr,
                                                actor=SimpleNamespace(id="u-1")))
    sr.skill_calls = calls
    return outcome, sr


def test_a_text_confidence_below_the_threshold_escalates_instead_of_crashing(monkeypatch):
    outcome, sr = _run_skill_step(monkeypatch, "medium")
    assert outcome == "wait" and sr.status == "waiting_human"
    assert "0.6 < 0.7" in sr.note


def test_a_text_confidence_above_the_threshold_advances(monkeypatch):
    outcome, _ = _run_skill_step(monkeypatch, "high")
    assert outcome == "advance"


def test_a_skill_that_gives_no_confidence_waits_for_a_person(monkeypatch):
    """A skill output with no confidence used to fall through to "advance" and show
    the step as Done with no verdict behind it."""
    outcome, sr = _run_skill_step(monkeypatch, None)
    assert outcome == "wait" and sr.status == "waiting_human"
    assert "gave no confidence" in sr.note


def test_an_agent_step_with_no_threshold_never_clears_itself(monkeypatch):
    """A designer- or chat-built agent step has no escalate_below_confidence, and any
    number, even 0.0, used to auto-advance it. With no bar, a person decides."""
    outcome, sr = _run_skill_step(monkeypatch, "high", threshold=None)
    assert outcome == "wait" and sr.status == "waiting_human"
    assert "no auto-advance threshold" in sr.note


def test_a_system_step_with_no_agent_still_advances(monkeypatch):
    """Guards 'Activate & track' (every library workflow's last step: no agent, no
    bar) against being wedged by the fail-closed rule."""
    outcome, sr = _run_skill_step(monkeypatch, "high", config={})
    assert outcome == "advance"
    assert sr.skill_calls == []


def test_a_threshold_with_no_agent_waits(monkeypatch):
    """A step that sets a bar but names no agent checked nothing, so it must not
    pass as if something cleared the bar."""
    outcome, sr = _run_skill_step(monkeypatch, "high", config={"escalate_below_confidence": 0.7})
    assert outcome == "wait" and sr.status == "waiting_human"
    assert "No AI agent" in sr.note
    assert sr.skill_calls == []


@pytest.mark.parametrize("bar", ["70%", "", "abc", True])
def test_an_unreadable_threshold_asks_a_person(monkeypatch, bar):
    """confidence_score reads an unreadable bar as 0.0, so a step whose threshold was written
    as "70%" (the chat's create_workflow writes free-form config) cleared even a "low" answer."""
    outcome, sr = _run_skill_step(monkeypatch, "low", threshold=bar)
    assert outcome == "wait" and "no auto-advance threshold" in sr.note


def test_a_missing_risk_band_is_not_read_as_medium():
    """A missing risk band used to default to "medium" (0.65), which clears the
    library's 0.6 "AI review" bar with no band behind it."""
    import inspect

    source = inspect.getsource(service._execute_step)
    assert 'contract.risk_band or "medium"' not in source
    assert '"high": 0.3}.get(band, ' not in source


def test_risk_level_only_holds_low_medium_or_high():
    assert ContractMetadataOutput(risk_level="Moderate to High").risk_level is None
    assert ContractMetadataOutput(risk_level=" HIGH ").risk_level == "high"
    field = TypeAdapter(ContractUpdate.model_fields["risk_level"].annotation)
    assert field.validate_python("low") == "low"
    with pytest.raises(ValidationError):
        field.validate_python("Moderate to High")
