"""Output models for the five task features (renewal, playbook expand, journal vision, clause parents, Nice class).

Failure modes guarded: (1) a key, type, enum or required set drifting from the hand-written schema production sent
before (frozen in tests/fixtures/*_schema_before.json), (2) a malformed row failing the whole answer where the old
code dropped just that row, (3) a stored shape gaining keys the consumers do not know, (4) the prompts again
describing a shape that disagrees with the tool (the renewal prompt once asked for "recommendation" while the tool
and every reader use "decision"), (5) tasks not registered with their model.
"""
import json
import pathlib

import pytest
from pydantic import ValidationError

from app.ai.gateway.features import feature_registry
from app.ai.output_format.clause_hierarchy import ClauseHierarchyOutput
from app.ai.output_format.playbook_expand import PlaybookExpandOutput
from app.ai.output_format.renewal_recommendation import RenewalRecommendationOutput
from app.ai.output_format.trademark_class_suggest import NiceClassSuggestion
from app.ai.output_format.trademark_journal_vision import JournalPageOutput
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS
from app.documents.hierarchy import rows_from

FIX = pathlib.Path(__file__).parent / "fixtures"


def _old(name):
    return json.loads((FIX / f"{name}_schema_before.json").read_text(encoding="utf-8"))


def _types(prop):
    out = set()
    for alt in prop.get("anyOf", [prop]):
        t = alt.get("type")
        out.update(t if isinstance(t, list) else [t])
    return out


def _item(schema):
    """The one list-item model inside a schema (follows $ref)."""
    arr = next(iter(schema["properties"].values()))
    item = arr["items"]
    if "$ref" in item:
        item = schema["$defs"][item["$ref"].split("/")[-1]]
    return item


CASES = {
    "renewal_recommendation": RenewalRecommendationOutput,
    "playbook_expand": PlaybookExpandOutput,
    "trademark_class_suggest": NiceClassSuggestion,
    "trademark_journal_vision": JournalPageOutput,
    "clause_hierarchy": ClauseHierarchyOutput,
}
LISTS = ("playbook_expand", "trademark_journal_vision", "clause_hierarchy")


@pytest.mark.parametrize("name", list(CASES))
def test_same_keys_required_set_and_types_as_the_old_schema(name):
    old, new = _old(name), CASES[name].model_json_schema()
    if name in LISTS:
        assert set(new["properties"]) == set(old["properties"]) and set(new["required"]) == set(old["required"])
        old, new = _item(old), _item(new)
    assert set(new["properties"]) == set(old["properties"])
    assert set(new["required"]) == set(old["required"])
    for key, prop in old["properties"].items():
        got = new["properties"][key]
        assert _types(got) - {"null"} == _types(prop) - {"null"}, key
        if "enum" in prop:
            enum = got.get("enum") or next(a["enum"] for a in got["anyOf"] if "enum" in a)
            assert enum == prop["enum"], key


def test_class_suggest_descriptions_are_unchanged():
    new = NiceClassSuggestion.model_json_schema()["properties"]
    old = _old("trademark_class_suggest")["properties"]
    assert {k: v["description"] for k, v in new.items()} == {k: v["description"] for k, v in old.items()}


def test_renewal_answer_keeps_the_three_stored_keys():
    answer = {"decision": "renegotiate", "rationale": "High risk on file.", "confidence": "medium"}
    assert RenewalRecommendationOutput.model_validate(answer).model_dump(mode="json") == answer


@pytest.mark.parametrize("field,bad", [("decision", "extend"), ("confidence", "HIGH")])
def test_renewal_value_outside_the_list_is_refused_so_the_heuristic_takes_over(field, bad):
    answer = {"decision": "renew", "rationale": "x", "confidence": "low", field: bad}
    with pytest.raises(ValidationError):
        RenewalRecommendationOutput.model_validate(answer)


def test_playbook_rules_drop_bad_rows_and_default_like_the_old_reader():
    out = PlaybookExpandOutput.model_validate({"rules": [
        {"clause_type": "confidentiality", "preferred_position": "Mutual", "risk_level": "low", "rationale": "x"},
        {"preferred_position": "no clause type", "risk_level": "low", "rationale": "y"},
        {"clause_type": "termination", "preferred_position": "p", "risk_level": "severe", "rationale": "z"},
    ]})
    rules = out.model_dump(mode="json", exclude_none=True)["rules"]
    assert [r["clause_type"] for r in rules] == ["confidentiality", "termination"]
    assert rules[1]["risk_level"] == "medium" and rules[0]["approval_required"] is False
    assert "rule_type" not in rules[0] and "fallback_position" not in rules[0]


def test_playbook_rules_must_be_a_list():
    with pytest.raises(ValidationError):
        PlaybookExpandOutput.model_validate({"rules": "none"})


def test_hierarchy_rows_match_what_rows_from_accepted():
    payload = {"answers": [
        {"seq": 1, "parent": None}, {"seq": 2, "parent": 1}, {"seq": 3},
        {"seq": True, "parent": None}, {"seq": 4, "parent": "1"}, {"seq": "5", "parent": None},
    ]}
    dumped = ClauseHierarchyOutput.model_validate(payload).model_dump(mode="json")
    assert rows_from(dumped) == rows_from(payload) == [(1, None), (2, 1)]


JOURNAL_ENTRY = {
    "product_name": "ZENITH", "mark_type": "word", "tm_id": "0123456", "tm_date": "01/02/2024",
    "address": "Zenith Pharma\nMumbai", "used_since": None, "proposed_to_be_used": True,
    "jurisdiction": "Mumbai", "goods_services": "Pharmaceuticals", "has_product_image": False,
}


def test_journal_entry_round_trips_into_the_vision_dataclass():
    from app.trademarks.extraction.vision import VisionEntry

    dumped = JournalPageOutput.model_validate({"entries": [JOURNAL_ENTRY]}).model_dump(mode="json")
    assert dumped["entries"][0] == JOURNAL_ENTRY
    assert VisionEntry(**dumped["entries"][0]).tm_id == "0123456"


def test_journal_entry_missing_a_key_or_with_an_unknown_mark_type_is_refused():
    for bad in ({k: v for k, v in JOURNAL_ENTRY.items() if k != "tm_id"}, {**JOURNAL_ENTRY, "mark_type": "color"}):
        with pytest.raises(ValidationError):
            JournalPageOutput.model_validate({"entries": [bad]})


def test_tasks_are_registered_with_their_model_and_untouched_ones_are_not():
    for key, model in CASES.items():
        assert feature_registry.get(key).output_model is model
    assert feature_registry.get("trademark_conflict_explain").output_model is None
    assert feature_registry.get("renewal_recommendation").default_prompt_version == "1.1.0"
    assert feature_registry.get("trademark_journal_vision").default_prompt_version == "1.1.0"


def test_prompts_no_longer_describe_a_shape_the_tool_does_not_have():
    for key in ("renewal_recommendation", "trademark_journal_vision"):
        prompt = DEFAULT_SKILL_PROMPTS[key]
        for stale in ("quality_flags", "page_quality_notes", "critical_missing_fact", '"recommendation"'):
            assert stale not in prompt, (key, stale)
