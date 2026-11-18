"""The flow_router output model must keep telling Claude what the hand-written schema did, only tighter.

Failure modes guarded: (1) a key dropped or renamed, or the required set changing (a good answer then
fails and the request falls to the rule-based pick), (2) a type loosened, (3) a real Claude answer no
longer validating, (4) the 0..1 score no longer clamped (an out-of-range number would fail the whole
answer, where the old code clamped it), (5) the prompt again describing keys the tool does not have
(its OUTPUT block once said "reason" and "matched_factors" while the tool says "reasoning").

Known, accepted differences from the dict era: a confidence that is not a number at all, or an
alternative without "why", now fails validation and the request takes the flagged rule-based pick
(source "degraded"); before, the code kept the model's pick with the rule-based confidence.
"""
import json
import pathlib

import pytest
from pydantic import ValidationError

from app.ai.gateway.features import feature_registry
from app.ai.output_format.flow_router import FlowRouterOutput
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "flow_router_schema_before.json"

# What Claude returned for a real request (Globex MSA on their paper), captured 2026-11-11.
REAL_ANSWER = {
    "flow_id": "ee2faae9-cebc-435e-98a1-7c5d5c5de12f",
    "confidence": 0.78,
    "reasoning": "The request is an MSA review on the counterparty's paper; the generic MSA flow is the best specific match.",
    "needs_human": True,
    "alternatives": [{"flow_id": "48f7b9c1-c77c-4f30-9865-444d7549f927", "why": "Default ladder, but less specific."}],
}

# Descriptions that were reworded on purpose (they now carry what the prompt's OUTPUT block said in prose).
REWORDED = {"flow_id", "confidence", "reasoning"}


def _types(prop: dict) -> set[str]:
    out: set[str] = set()
    for alt in prop.get("anyOf", [prop]):
        t = alt.get("type")
        out.update(t if isinstance(t, list) else [t])
    return out


def test_same_keys_same_types_same_required_set_as_before():
    old = json.loads(FIXTURE.read_text(encoding="utf-8"))
    new = FlowRouterOutput.model_json_schema()
    assert set(new["properties"]) == set(old["properties"])
    assert set(new["required"]) == set(old["required"])
    for name, old_prop in old["properties"].items():
        assert _types(new["properties"][name]) == _types(old_prop), f"{name}: type changed"


def test_unreworded_descriptions_are_unchanged_and_every_field_is_described():
    old = json.loads(FIXTURE.read_text(encoding="utf-8"))
    new = FlowRouterOutput.model_json_schema()
    for name, prop in new["properties"].items():
        assert prop.get("description"), f"{name}: no description"
        if name not in REWORDED and "description" in old["properties"][name]:
            assert prop["description"] == old["properties"][name]["description"], name


def test_confidence_tells_claude_the_range():
    conf = FlowRouterOutput.model_json_schema()["properties"]["confidence"]
    assert conf["minimum"] == 0 and conf["maximum"] == 1


def test_a_real_claude_answer_validates_and_round_trips():
    out = FlowRouterOutput.model_validate(REAL_ANSWER)
    assert out.model_dump(mode="json") == REAL_ANSWER


def test_no_workflow_fits_is_a_valid_answer():
    out = FlowRouterOutput.model_validate(
        {"flow_id": None, "confidence": 0.2, "reasoning": "Nothing in the catalog fits.", "needs_human": True}
    )
    assert out.flow_id is None and out.alternatives == []


@pytest.mark.parametrize("raw,expected", [(1.4, 1.0), (-0.3, 0.0), ("0.8", 0.8), (1, 1.0)])
def test_an_out_of_range_or_text_number_is_clamped_like_the_old_code_did(raw, expected):
    assert FlowRouterOutput.model_validate({**REAL_ANSWER, "confidence": raw}).confidence == expected


@pytest.mark.parametrize("bad", ["high", None, True])
def test_a_confidence_that_is_not_a_number_is_refused(bad):
    with pytest.raises(ValidationError):
        FlowRouterOutput.model_validate({**REAL_ANSWER, "confidence": bad})


@pytest.mark.parametrize("missing", ["flow_id", "confidence", "reasoning", "needs_human"])
def test_the_four_required_keys_are_required(missing):
    with pytest.raises(ValidationError):
        FlowRouterOutput.model_validate({k: v for k, v in REAL_ANSWER.items() if k != missing})


def test_registered_with_its_model_and_prompt_version():
    f = feature_registry.get("flow_router")
    assert f.output_model is FlowRouterOutput
    assert f.default_prompt_version == "1.1.0"
    assert feature_registry.get("trademark_conflict_explain").output_model is None  # text answer, no model


def test_the_prompt_no_longer_describes_keys_the_tool_does_not_have():
    prompt = DEFAULT_SKILL_PROMPTS["flow_router"]
    for stale in ('"reason"', "matched_factors", "missing_routing_facts"):
        assert stale not in prompt
