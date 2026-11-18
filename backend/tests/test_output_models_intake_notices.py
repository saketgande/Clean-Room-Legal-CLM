"""Output models of litigation_intake_agent, notice_extraction_agent, intake_form_read and email_triage_agent.

Failure modes guarded, per model: (1) a key dropped or renamed, or the required set changing (a good answer
would then fail validation and the request take the keyword fallback), (2) a type loosened without notice,
(3) a real Claude answer no longer validating, (4) the leniency lost: one odd value (a bad date, an unlisted
word, a number where text was expected) must empty that field, never fail the whole answer, as the old
``_clean_*`` helpers behaved, (5) a copied list drifting from the one the rest of the code uses,
(6) a prompt again describing keys the tool does not have.

Frozen "before" schemas are in tests/fixtures/<feature>_schema_before.json.
"""
import json
import pathlib

import pytest
from pydantic import ValidationError

from app.ai.gateway.features import feature_registry
from app.ai.output_format import _types
from app.ai.output_format.email_triage import CATEGORIES, EmailTriageOutput
from app.ai.output_format.intake_form_read import IntakeFormReadOutput
from app.ai.output_format.litigation_intake import LitigationIntakeOutput
from app.ai.output_format.notice_extraction import (
    NOTICE_TYPES,
    PERIOD_UNITS,
    RUNS_FROM,
    NoticeExtractionOutput,
)
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS

FIX = pathlib.Path(__file__).parent / "fixtures"


def _before(name: str) -> dict:
    return json.loads((FIX / f"{name}_schema_before.json").read_text(encoding="utf-8"))


def _types_of(prop: dict) -> set[str]:
    out: set[str] = set()
    for alt in prop.get("anyOf", [prop]):
        t = alt.get("type")
        out.update(t if isinstance(t, list) else [t])
    return out - {None}


def _enum_of(prop: dict) -> set:
    values: set = set()
    for alt in prop.get("anyOf", [prop]):
        values.update(alt.get("enum") or [])
    values.update(prop.get("enum") or [])
    return values - {None}


def _same_top_level(model, name):
    old, new = _before(name), model.model_json_schema()
    assert set(new["properties"]) == set(old["properties"]), "a key was added or dropped"
    assert set(new["required"]) == set(old["required"]), "the required set changed"
    for key, prop in old["properties"].items():
        assert _types_of(new["properties"][key]) >= _types_of(prop) - {"object"} or "$ref" in new["properties"][key], key
    return old, new


# --- real answers captured 2026-11-10 ---------------------------------------------------------------
LITIGATION = {
    "matter_type": "Contract Dispute / Legal Notice — Alleged Breach of Supply Agreement",
    "legal_hold_required": True, "outside_counsel_likely": True, "settlement_posture": "likely",
    "summary": "We received a legal notice from Acme's counsel demanding INR 15 lakh within 15 days.",
    "assessment_confidence": 0.78, "flow_id": "5bbf8bf1-f0ed-4c16-93c4-968fbf9587a0", "flow_confidence": 0.93,
    "flow_reasoning": "A textbook fit for the Legal Notice Response workflow.",
    "key_parties": ["Our Company (Respondent)", "Acme (Claimant)"],
    "statutory_deadlines": [
        {"what": "Reply to legal notice", "date": "15 days from date of receipt of notice", "source": "Demand notice"},
        {"what": "Limitation period to file suit", "date": "3 years from date of breach", "source": "Limitation Act, 1963"},
    ],
}
NOTICE = {
    "confidence": 0.97, "counterparty_name": "Acme Components Ltd", "counterparty_ref": "ACM/LGL/2026/114",
    "notice_date": "2026-11-02", "notice_type": "demand", "subject": "Demand for payment of INR 15,00,000",
    "demanded_action": "Pay invoice no. 4471 within fifteen (15) days of receipt of this notice.",
    "deadline": {"stated": True, "explicit_date": None, "period_count": 15, "period_unit": "days",
                 "runs_from": "receipt", "runs_from_text": "of receipt of this notice", "clear_days": False,
                 "quote": "You are called upon to pay the said sum within fifteen (15) days of receipt of this notice."},
}
EMAIL = {"is_clm_related": True, "category": "NDA", "confidence": 0.95}
FORM = {
    "summary": "The requester wants a supply agreement but filed it as an NDA.",
    "mismatches": [{"field": "agreement_type", "form_says": "NDA", "text_says": "Supply agreement", "same": False}],
    "bespoke_asks": ["Counterparty's own paper requested"],
    "negotiation_points": ["Agreement type must be resolved first"],
}


# --- same keys and required set as before -----------------------------------------------------------
@pytest.mark.parametrize("model,name", [
    (LitigationIntakeOutput, "litigation_intake"), (NoticeExtractionOutput, "notice_extraction"),
    (EmailTriageOutput, "email_triage"), (IntakeFormReadOutput, "intake_form_read"),
])
def test_same_keys_and_required_set_as_the_old_dict(model, name):
    _same_top_level(model, name)


def test_every_field_of_every_model_is_described():
    for model in (LitigationIntakeOutput, NoticeExtractionOutput, EmailTriageOutput, IntakeFormReadOutput):
        schema = model.model_json_schema()
        for key, prop in schema["properties"].items():
            assert prop.get("description") or "$ref" in prop or "anyOf" in prop, f"{model.__name__}.{key}"


def test_litigation_deadline_keeps_its_three_old_keys_and_adds_a_real_date():
    deadline = LitigationIntakeOutput.model_json_schema()["$defs"]["StatutoryDeadline"]
    old = _before("litigation_intake")["properties"]["statutory_deadlines"]["items"]
    assert set(deadline["properties"]) == set(old["properties"]) | {"due_date"}
    assert deadline["required"] == old["required"] == ["what"]
    assert deadline["properties"]["due_date"]["anyOf"][0]["format"] == "date"


def test_settlement_posture_gains_the_fourth_word_the_prompt_always_offered():
    posture = LitigationIntakeOutput.model_json_schema()["properties"]["settlement_posture"]
    old = _enum_of(_before("litigation_intake")["properties"]["settlement_posture"])
    assert old == {"none", "proposed", "likely"} and _enum_of(posture) == old | {"unknown"}


def test_notice_vocabularies_and_ranges_are_unchanged():
    new = NoticeExtractionOutput.model_json_schema()
    old = _before("notice_extraction")
    old_deadline = old["properties"]["deadline"]["properties"]
    deadline = new["$defs"]["NoticeDeadlineReading"]["properties"]
    assert set(deadline) == set(old_deadline)
    assert _enum_of(deadline["period_unit"]) == _enum_of(old_deadline["period_unit"])
    assert _enum_of(deadline["runs_from"]) == _enum_of(old_deadline["runs_from"])
    assert _enum_of(new["properties"]["notice_type"]) == _enum_of(old["properties"]["notice_type"])
    assert deadline["period_count"]["minimum"] == 0
    assert new["$defs"]["NoticeDeadlineReading"]["required"] == ["stated"]


def test_email_categories_and_form_read_mismatch_keys_are_unchanged():
    old_cat = _enum_of(_before("email_triage")["properties"]["category"])
    assert _enum_of(EmailTriageOutput.model_json_schema()["properties"]["category"]) == old_cat
    old_item = _before("intake_form_read")["properties"]["mismatches"]["items"]
    item = IntakeFormReadOutput.model_json_schema()["$defs"]["FormMismatch"]
    assert set(item["properties"]) == set(old_item["properties"]) and item["required"] == old_item["required"]


# --- copied lists match the lists the code really uses ----------------------------------------------
def test_copied_vocabularies_match_the_modules_that_use_them():
    from app.intake import email_triage_agent
    from app.notices import extraction
    from app.notices.models import NOTICE_TYPES as DB_NOTICE_TYPES

    assert NOTICE_TYPES == DB_NOTICE_TYPES
    assert PERIOD_UNITS == extraction.PERIOD_UNITS and RUNS_FROM == extraction.RUNS_FROM
    assert CATEGORIES == email_triage_agent._CATEGORIES


# --- real answers validate, and come back as the same plain dict the code reads ---------------------
def test_real_answers_validate_and_round_trip():
    out = LitigationIntakeOutput.model_validate(LITIGATION).model_dump(mode="json")
    assert out["settlement_posture"] == "likely"
    assert out["statutory_deadlines"][0]["date"] == "15 days from date of receipt of notice"
    assert out["statutory_deadlines"][0]["due_date"] is None  # a period, so no calendar date
    assert NoticeExtractionOutput.model_validate(NOTICE).model_dump(mode="json")["notice_date"] == "2026-11-02"
    assert EmailTriageOutput.model_validate(EMAIL).model_dump(mode="json") == EMAIL
    assert IntakeFormReadOutput.model_validate(FORM).model_dump(mode="json") == FORM


def test_the_notice_deadline_reading_still_feeds_the_deadline_cleaner():
    from app.notices.extraction import clean_deadline

    data = NoticeExtractionOutput.model_validate(NOTICE).model_dump(mode="json")
    cleaned = clean_deadline(data["deadline"], text=data["demanded_action"])
    assert cleaned["stated"] is True and cleaned["period_count"] == 15
    assert cleaned["period_unit"] == "days" and cleaned["runs_from"] == "receipt"


def test_a_stated_calendar_date_comes_through_as_text_the_cleaner_expects():
    answer = {**NOTICE, "deadline": {"stated": True, "explicit_date": "2026-12-01", "quote": "reply by 1 December 2026"}}
    data = NoticeExtractionOutput.model_validate(answer).model_dump(mode="json")
    assert data["deadline"]["explicit_date"] == "2026-12-01"


# --- one odd value empties that field, never the whole answer ---------------------------------------
@pytest.mark.parametrize("patch,field,expected", [
    ({"notice_date": "sometime in May"}, "notice_date", None),
    ({"notice_type": "weird"}, "notice_type", None),
    ({"counterparty_ref": 4471}, "counterparty_ref", "4471"),
    ({"subject": ["not", "text"]}, "subject", None),
    ({"deadline": "none stated"}, "deadline", None),
    ({"confidence": 1.7}, "confidence", 1.0),
])
def test_notice_forgives_an_odd_value(patch, field, expected):
    out = NoticeExtractionOutput.model_validate({**NOTICE, **patch}).model_dump(mode="json")
    assert out[field] == expected


def test_notice_deadline_forgives_an_odd_value_inside_the_reading():
    odd = {"stated": True, "explicit_date": "next week", "period_count": -3, "period_unit": "fortnights", "runs_from": "whenever"}
    d = NoticeExtractionOutput.model_validate({**NOTICE, "deadline": odd}).model_dump(mode="json")["deadline"]
    assert (d["explicit_date"], d["period_count"], d["period_unit"], d["runs_from"]) == (None, None, None, None)
    assert d["stated"] is True


@pytest.mark.parametrize("posture,expected", [("Likely", "likely"), (" NONE ", "none"), ("maybe", "unknown")])
def test_litigation_settlement_posture_is_case_folded_and_unlisted_becomes_unknown(posture, expected):
    out = LitigationIntakeOutput.model_validate({**LITIGATION, "settlement_posture": posture})
    assert out.settlement_posture == expected


def test_litigation_deadline_date_words_are_kept_and_a_bad_due_date_is_dropped():
    answer = {**LITIGATION, "statutory_deadlines": [{"what": "Reply", "date": "UNKNOWN - approaching", "due_date": "soon"}]}
    d = LitigationIntakeOutput.model_validate(answer).model_dump(mode="json")["statutory_deadlines"][0]
    assert d["date"] == "UNKNOWN - approaching" and d["due_date"] is None and d["source"] is None


def test_litigation_a_real_due_date_is_kept_as_text():
    answer = {**LITIGATION, "statutory_deadlines": [{"what": "Reply", "date": "1 Dec 2026", "due_date": "2026-12-01"}]}
    assert LitigationIntakeOutput.model_validate(answer).model_dump(mode="json")["statutory_deadlines"][0]["due_date"] == "2026-12-01"


@pytest.mark.parametrize("category,expected", [("Weird", None), (None, None), ("Litigation", "Litigation")])
def test_email_category_outside_the_list_becomes_null(category, expected):
    assert EmailTriageOutput.model_validate({**EMAIL, "category": category}).category == expected


def test_required_keys_are_still_required():
    for model, answer, key in [
        (LitigationIntakeOutput, LITIGATION, "legal_hold_required"), (LitigationIntakeOutput, LITIGATION, "flow_id"),
        (NoticeExtractionOutput, NOTICE, "confidence"), (EmailTriageOutput, EMAIL, "is_clm_related"),
        (IntakeFormReadOutput, FORM, "mismatches"),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate({k: v for k, v in answer.items() if k != key})


# --- the shared types --------------------------------------------------------------------------------
def test_shared_types_behave_as_documented():
    from pydantic import BaseModel

    class Probe(BaseModel):
        score: _types.Score
        day: _types.LenientDate = None
        count: _types.NonNegInt = None
        text: _types.Text = None

    assert Probe(score="0.5").score == 0.5 and Probe(score=-2).score == 0.0
    assert Probe(score=1, day="2026-02-30").day is None  # not a real date
    assert str(Probe(score=1, day="2026-03-04T10:00:00Z").day) == "2026-03-04"
    assert Probe(score=1, count=True).count is None and Probe(score=1, count=0).count == 0
    assert Probe(score=1, text=12).text == "12" and Probe(score=1, text={"a": 1}).text is None
    with pytest.raises(ValidationError):
        Probe(score="high")


# --- registry, prompt versions and prompts ----------------------------------------------------------
def test_registered_with_their_models_and_new_prompt_version():
    expect = {
        "litigation_intake_agent": LitigationIntakeOutput, "notice_extraction_agent": NoticeExtractionOutput,
        "email_triage_agent": EmailTriageOutput, "intake_form_read": IntakeFormReadOutput,
    }
    for key, model in expect.items():
        feature = feature_registry.get(key)
        assert feature.output_model is model and feature.default_prompt_version == "1.1.0", key
    assert feature_registry.get("trademark_conflict_explain").output_model is None  # text answer, no model


@pytest.mark.parametrize("key,ghosts", [
    ("litigation_intake_agent", ("outside_counsel_likely_needed", "immediate_actions", "matter_status")),
    ("notice_extraction_agent", ("notice_direction", "date_received", "service_address", "key_allegations")),
    ("email_triage_agent", ('"signals"', '"reason"')),
    ("intake_form_read", ("bespoke_asks:", "negotiation_points:")),
])
def test_prompts_no_longer_list_keys_the_tool_does_not_have(key, ghosts):
    prompt = DEFAULT_SKILL_PROMPTS[key]
    for ghost in ghosts:
        assert ghost not in prompt, ghost
