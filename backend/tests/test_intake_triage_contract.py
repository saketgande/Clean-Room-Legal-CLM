"""The intake_triage output model must keep telling Claude exactly what the hand-written schema did.

Failure modes guarded: (1) the model's JSON Schema drifting from the one production sent before the move
(a dropped enum value or description changes what Claude answers), (2) a field becoming required that the
old schema left optional (a good answer then fails validation and the request falls to the keyword
fallback), (3) a real Claude answer no longer validating, (4) the pure-contract package picking up a
framework import and stopping being portable.
"""
import json
import pathlib

import pytest
from pydantic import ValidationError

from app.ai.gateway.features import feature_registry
from app.ai.output_format.intake_triage import IntakeTriageOutput
from app.intake import triage_agent

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "intake_triage_schema_before.json"

# What Claude returned for a real request (Zenith Pharma NDA on their paper), captured 2026-11-10.
REAL_ANSWER = {
    "category": "NDA",
    "sub_type": "NDA — counterparty paper (their draft)",
    "complexity": "standard",
    "risk": "medium",
    "urgency": "High",
    "confidence": 0.92,
    "reasoning": "R&D needs Legal to review an NDA draft sent by Zenith Pharma for a joint formulation study.",
    "business_unit": "R&D",
    "estimated_value": 20000000,
    "jurisdiction": "India",
    "key_asks": ["Review Zenith Pharma's NDA draft", "Turnaround needed before Friday"],
    "missing_info": ["Zenith Pharma's NDA draft document not yet attached/uploaded"],
    "recommended_workflow_id": "a54ca0a9-0305-486e-bfee-f4050613ea3c",
    "workflow_reasoning": "Counterparty insists on its own paper, which maps to the 'NDA — their paper' workflow.",
    "needs_human": False,
}


def _resolve(prop: dict, defs: dict) -> dict:
    if "$ref" in prop:
        prop = {**defs[prop["$ref"].split("/")[-1]], **{k: v for k, v in prop.items() if k != "$ref"}}
    return prop


def _facts(prop: dict, defs: dict) -> dict:
    """What matters to Claude about one field: its types, allowed values and description."""
    prop = _resolve(prop, defs)
    types: set[str] = set()
    for alt in prop.get("anyOf", [prop]):
        alt = _resolve(alt, defs)
        t = alt.get("type")
        types.update(t if isinstance(t, list) else [t])
        if alt.get("type") == "array":
            types.add("array")
    if isinstance(prop.get("type"), list):
        types = set(prop["type"])
    return {"types": types, "enum": prop.get("enum"), "description": prop.get("description")}


def test_model_schema_says_what_the_old_dict_said():
    old = json.loads(FIXTURE.read_text(encoding="utf-8"))
    new = IntakeTriageOutput.model_json_schema()
    assert set(new["properties"]) == set(old["properties"])
    for name, old_prop in old["properties"].items():
        o, n = _facts(old_prop, {}), _facts(new["properties"][name], new.get("$defs", {}))
        assert n["enum"] == o["enum"], f"{name}: allowed values changed"
        assert n["description"] == o["description"], f"{name}: description changed"
        assert n["types"] == o["types"], f"{name}: type changed ({o['types']} -> {n['types']})"


def test_required_fields_are_the_ones_the_old_schema_required():
    old = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(IntakeTriageOutput.model_json_schema()["required"]) == set(old["required"])


def test_a_real_claude_answer_validates_and_maps_to_the_stored_shape():
    out = IntakeTriageOutput.model_validate(REAL_ANSWER)
    flows = [{"id": REAL_ANSWER["recommended_workflow_id"], "name": "NDA — their paper", "steps": []}]
    request = type("R", (), {"id": "r1", "description": "", "subject": "", "type_label": "Email", "field_values": {}})()
    triaged = triage_agent._to_triage(request, out.model_dump(mode="json"), flows)
    assert triaged["category"] == "NDA" and triaged["source"] == "llm"
    assert triaged["understanding"]["urgency"] == "High"
    assert triaged["flow_suggestion"]["flow_id"] == REAL_ANSWER["recommended_workflow_id"]


def test_only_the_required_fields_are_enough():
    minimal = {k: REAL_ANSWER[k] for k in json.loads(FIXTURE.read_text(encoding="utf-8"))["required"]}
    out = IntakeTriageOutput.model_validate(minimal)
    assert out.key_asks == [] and out.recommended_workflow_id is None and out.workflow_reasoning == ""


@pytest.mark.parametrize("field,bad", [("category", "Employment"), ("risk", "moderate"), ("urgency", "high"), ("complexity", "hard")])
def test_a_value_outside_the_allowed_list_is_refused(field, bad):
    """Refused, so the request takes the flagged keyword fallback instead of a silently wrong class."""
    with pytest.raises(ValidationError):
        IntakeTriageOutput.model_validate({**REAL_ANSWER, field: bad})


def test_the_feature_is_registered_with_its_model_and_other_agents_are_untouched():
    assert feature_registry.get("intake_triage").output_model is IntakeTriageOutput
    assert feature_registry.get("trademark_conflict_explain").output_model is None  # text answer, no model


def test_the_contracts_package_imports_no_framework():
    """Portable means usable under any runner: no anthropic, gateway, database, web framework or agent library."""
    root = pathlib.Path(__file__).parents[1] / "app" / "ai" / "output_format"
    banned = ("anthropic", "app.ai.gateway", "app.core", "sqlalchemy", "fastapi", "langchain", "langgraph", "celery")
    for path in root.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("import ", "from ")):
                assert not any(b in line for b in banned), f"{path.name}: {line}"
