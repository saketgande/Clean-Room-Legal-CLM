"""The Trademark Suite's three Claude calls now go through the AI gateway.

Class suggestion, conflict explanation and the daily portfolio digest used to
call the Claude client directly (hardcoded prompts, no ledger row, the old
``enforce_daily_token_cap``). They are now the gateway features
``trademark_class_suggest``, ``trademark_conflict_explain`` and
``trademark_portfolio_digest``.

DB-free, like the other gateway tests: a fake provider stands in for Claude and
a fake session for Postgres and the AI ledger. Each test runs the real
``TrademarksService`` method.
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD
from app.ai.gateway import feature_registry
from app.ai.gateway.features import FeatureKind, PromptLayout
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS
from app.trademarks import service as trademarks_service
from app.trademarks.nice_classes import NICE_CLASSES
from app.trademarks.schemas import ClassSuggestionRequest, ExplainConflictRequest
from app.trademarks.service import TrademarksService
from tests.test_ai_gateway import (  # noqa: F401 — the autouse fixture applies here too
    FakeProvider,
    FakeSession,
    _ledger_uses_fake_sessions,
    _response,
)

CLASS_KEY = "trademark_class_suggest"
EXPLAIN_KEY = "trademark_conflict_explain"
DIGEST_KEY = "trademark_portfolio_digest"


class _Session(FakeSession):
    """FakeSession plus what the digest path needs from a real Session."""

    def __init__(self, *, existing_digest=None, override=None) -> None:
        super().__init__()
        self.existing_digest = existing_digest
        self.override = override  # a prompt-table row, or None = shipped default

    def scalar(self, stmt, *_a, **_k):
        text = str(stmt)
        if "a_i_prompt_version" in text:
            return self.override
        return self.existing_digest

    def refresh(self, _obj) -> None:
        pass

    @property
    def digests(self) -> list:
        return [o for o in self.added if type(o).__name__ == "PortfolioDigest"]


@pytest.fixture(autouse=True)
def _clean_caches():
    """The service keeps two in-process caches; start every test with them empty."""
    trademarks_service._classify_cache._store.clear()
    trademarks_service._explain_cache._store.clear()
    yield
    trademarks_service._classify_cache._store.clear()
    trademarks_service._explain_cache._store.clear()


def _system(key: str) -> str:
    return DEFAULT_SKILL_PROMPTS[key] + "\n\n" + UNTRUSTED_INPUT_GUARD


def _class_answer(nice_class="25", reasoning="Clothing."):
    return _response(tool="suggest_nice_class", tool_input={"nice_class": nice_class, "reasoning": reasoning})


def _suggest(service, org_id="org-1", name="ZEBRA TEES", description="t-shirts", user_id="user-1"):
    request = ClassSuggestionRequest(name=name, description=description)
    return asyncio.run(service.suggest_nice_class(org_id=org_id, request=request, user_id=user_id))


def _conflict(**over) -> ExplainConflictRequest:
    data = {"trademark_name": "ZEBRA", "source": "uspto", "conflict_name": "ZEBRO", "match_score": 0.91,
            "risk_level": "high", "status": "registered", "jurisdiction": "US"}
    return ExplainConflictRequest(**{**data, **over})


# --- the registry -----------------------------------------------------------------
def test_the_three_features_are_registered_with_the_budgets_they_had_before():
    expected = {
        CLASS_KEY: ("suggest_nice_class", 120, 0.1),
        EXPLAIN_KEY: (None, 120, 0.3),
        DIGEST_KEY: (None, 200, 0.4),
    }
    for key, (tool, max_tokens, temperature) in expected.items():
        feature = feature_registry.get(key)
        assert feature.kind == FeatureKind.TASK and feature.layout == PromptLayout.FEATURE_SYSTEM
        assert feature.guard, key  # user-typed names and descriptions reach the model
        assert (feature.tool_name, feature.max_tokens, feature.temperature) == (tool, max_tokens, temperature), key
        assert feature.category == "Analysis & extraction" and key in DEFAULT_SKILL_PROMPTS


def test_moved_prompts_are_byte_for_byte_what_the_service_had():
    """sha256 of the three prompts as they were hardcoded in trademarks/service.py."""
    expected = {
        CLASS_KEY: "2ee9ad7a247ab86c539ef0141fc2b0a4f210b496c37349e34e1213afa46adbad",
        EXPLAIN_KEY: "61207b4107b45a759879aa0c556b8573262e2b3cfe3f14cb00decc1bad2e8aa0",
        DIGEST_KEY: "becaff4fba5185a57d462508e86fc3ee910cb9215f6c7c0bdd67d8af7d7314e7",
    }
    for key, digest in expected.items():
        assert hashlib.sha256(DEFAULT_SKILL_PROMPTS[key].encode()).hexdigest() == digest, key


def test_the_class_prompt_still_shows_claude_all_45_classes():
    prompt = DEFAULT_SKILL_PROMPTS[CLASS_KEY]
    assert len(NICE_CLASSES) == 45
    for entry in NICE_CLASSES:
        assert f'{entry["class"]}: {entry["heading"]}' in prompt, entry["class"]


# --- class suggestion -----------------------------------------------------------------
def test_class_suggestion_goes_through_the_gateway_and_is_recorded():
    db = _Session()
    provider = FakeProvider(_class_answer())

    out = _suggest(TrademarksService(db, claude_client=provider))

    assert (out.nice_class, out.heading, out.reasoning, out.cached) == (
        "25", "Clothing, footwear, and headwear", "Clothing.", False,
    )
    name, sent = provider.calls[0]
    assert name == "structured" and sent["org_id"] == "org-1"  # the client enforces the daily cap with this
    assert sent["system_prompt"] == _system(CLASS_KEY)
    assert (sent["tool_name"], sent["max_tokens"], sent["temperature"]) == ("suggest_nice_class", 120, 0.1)
    assert sent["user_prompt"] == 'Trademark name: "ZEBRA TEES"\nDescription: "t-shirts"'
    [row] = db.rows
    assert (row.prompt_key, row.status, row.org_id, row.created_by_user_id) == (CLASS_KEY, "succeeded", "org-1", "user-1")
    assert row.input_payload == {"name_chars": 10, "description_chars": 8}  # sizes only, never the text


def test_class_suggestion_is_cached_but_only_within_one_org():
    db = _Session()
    provider = FakeProvider(_class_answer())
    service = TrademarksService(db, claude_client=provider)

    first = _suggest(service)
    again = _suggest(service)
    other_org = _suggest(service, org_id="org-2")  # an org can override the prompt: never share answers

    assert (first.cached, again.cached, other_org.cached) == (False, True, False)
    assert len(provider.calls) == 2 and len(db.rows) == 2  # a cache hit is not an AI call, so no ledger row


def test_an_admin_prompt_override_reaches_claude_and_the_guard_still_follows_it():
    override = SimpleNamespace(
        prompt_key=CLASS_KEY, version="2.0.0", prompt_hash="h", prompt_text="ORG OVERRIDE TEXT",
        model_name=None, model_config_hash=None,
    )
    db = _Session(override=override)
    provider = FakeProvider(_class_answer())

    _suggest(TrademarksService(db, claude_client=provider))

    assert provider.calls[0][1]["system_prompt"] == "ORG OVERRIDE TEXT\n\n" + UNTRUSTED_INPUT_GUARD
    assert db.rows[0].prompt_version == "2.0.0"


def test_a_class_number_that_does_not_exist_is_a_502_and_is_not_cached():
    db = _Session()
    provider = FakeProvider(_class_answer(nice_class="99"))
    service = TrademarksService(db, claude_client=provider)

    with pytest.raises(HTTPException) as caught:
        _suggest(service)
    assert caught.value.status_code == 502 and "invalid class number" in caught.value.detail
    with pytest.raises(HTTPException):
        _suggest(service)
    assert len(provider.calls) == 2  # the bad answer was not remembered


def test_no_tool_answer_is_a_502_and_the_bad_output_is_recorded():
    db = _Session()
    with pytest.raises(HTTPException) as caught:
        _suggest(TrademarksService(db, claude_client=FakeProvider(_response(text="I think class 25"))))
    assert (caught.value.status_code, caught.value.detail) == (502, "Classifier returned no result")
    assert db.rows[0].status == "validation_failed"


def test_a_cut_off_answer_is_rejected_rather_than_trusted():
    db = _Session()
    cut_off = _response(tool="suggest_nice_class", tool_input={"nice_class": "2"}, stop="max_tokens")
    with pytest.raises(HTTPException) as caught:
        _suggest(TrademarksService(db, claude_client=FakeProvider(cut_off)))
    assert caught.value.status_code == 502
    assert db.rows[0].status == "validation_failed" and "cut off" in db.rows[0].validation_error


def test_a_provider_failure_propagates_for_the_route_to_turn_into_502_and_is_recorded():
    db = _Session()
    with pytest.raises(TimeoutError):
        _suggest(TrademarksService(db, claude_client=FakeProvider(error=TimeoutError())))
    assert (db.rows[0].status, db.rows[0].error_class) == ("failed", "TimeoutError")


def test_a_daily_cap_breach_still_reaches_the_user_as_429():
    """The client raises HTTP 429 when the org is over its cap; the gateway records it and
    re-raises it unchanged, and the route lets HTTPException through (not a 502)."""
    db = _Session()
    cap = HTTPException(429, "Daily AI token limit reached for this organization. Try again tomorrow.")
    with pytest.raises(HTTPException) as caught:
        _suggest(TrademarksService(db, claude_client=FakeProvider(error=cap)))
    assert caught.value.status_code == 429
    assert db.rows[0].status == "failed" and db.rows[0].error_class == "HTTPException"


# --- conflict explanation --------------------------------------------------------------
def test_conflict_explanation_goes_through_the_gateway_and_is_recorded():
    db = _Session()
    provider = FakeProvider(_response(text="  High risk: near-identical sound, same class.  "))
    service = TrademarksService(db, claude_client=provider)

    out = asyncio.run(service.explain_conflict(org_id="org-1", request=_conflict(), user_id="user-1"))

    assert (out.explanation, out.cached) == ("High risk: near-identical sound, same class.", False)
    name, sent = provider.calls[0]
    assert name == "text" and sent["org_id"] == "org-1"
    assert sent["system_prompt"] == _system(EXPLAIN_KEY)
    assert (sent["max_tokens"], sent["temperature"]) == (120, 0.3)
    assert sent["user_prompt"] == (
        'Searched mark: "ZEBRA"\nConflict: "ZEBRO" (source: uspto)\nMatch strength: 91%\n'
        "Computed risk level: high\nStatus: registered\nJurisdiction: US"
    )
    [row] = db.rows
    assert (row.prompt_key, row.status, row.created_by_user_id) == (EXPLAIN_KEY, "succeeded", "user-1")
    assert row.input_payload == {"source": "uspto", "has_match_score": True}


def test_conflict_explanation_is_cached_per_org():
    db = _Session()
    provider = FakeProvider(_response(text="Some risk."))
    service = TrademarksService(db, claude_client=provider)

    runs = [asyncio.run(service.explain_conflict(org_id=org, request=_conflict())).cached
            for org in ("org-1", "org-1", "org-2")]

    assert runs == [False, True, False] and len(provider.calls) == 2


def test_an_empty_explanation_is_a_502_not_a_blank_answer():
    db = _Session()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(TrademarksService(db, claude_client=FakeProvider(_response(text="   "))).explain_conflict(
            org_id="org-1", request=_conflict()))
    assert (caught.value.status_code, caught.value.detail) == (502, "Explainer returned no explanation")
    assert db.rows[0].status == "validation_failed"


# --- portfolio digest ------------------------------------------------------------------
USER = SimpleNamespace(id="user-1", org_id="org-1")
STATS = {"new_trademarks_count": 3, "by_jurisdiction": {"US": 2, "IN": 1}}
SAMPLES = [{"name": "ZEBRA", "jurisdiction": "US"}, {"name": "ZEBRO", "jurisdiction": "IN"}]


def _digest(service, *, user=USER, force=False):
    return asyncio.run(service.get_or_create_portfolio_digest(user=user, force=force))


def test_digest_with_new_trademarks_goes_through_the_gateway_and_is_saved(monkeypatch):
    db = _Session()
    provider = FakeProvider(_response(text="Three new marks came in, mostly US."))
    service = TrademarksService(db, claude_client=provider)
    monkeypatch.setattr(service, "_gather_digest_stats", lambda **_k: (STATS, SAMPLES))

    out = _digest(service)

    assert (out.summary, out.generated, out.stats) == ("Three new marks came in, mostly US.", True, STATS)
    name, sent = provider.calls[0]
    assert name == "text" and sent["org_id"] == "org-1"
    assert sent["system_prompt"] == _system(DIGEST_KEY)
    assert (sent["max_tokens"], sent["temperature"]) == (200, 0.4)
    assert "New trademark records in the last 24h: 3" in sent["user_prompt"]
    assert "- ZEBRA (US)" in sent["user_prompt"] and "- ZEBRO (IN)" in sent["user_prompt"]
    [row] = db.rows
    assert (row.prompt_key, row.status, row.created_by_user_id) == (DIGEST_KEY, "succeeded", "user-1")
    assert row.input_payload == {"new_trademarks": 3, "samples": 2}
    [saved] = db.digests
    assert saved.summary == "Three new marks came in, mostly US." and saved.org_id == "org-1"


def test_digest_with_nothing_new_makes_no_ai_call_at_all(monkeypatch):
    db = _Session()
    provider = FakeProvider(_response(text="should never be asked"))
    service = TrademarksService(db, claude_client=provider)
    monkeypatch.setattr(service, "_gather_digest_stats", lambda **_k: ({"new_trademarks_count": 0, "by_jurisdiction": {}}, []))

    out = _digest(service)

    assert out.summary.startswith("No new trademark records") and out.generated is True
    assert provider.calls == [] and db.rows == []  # no spend, no ledger row


def test_todays_digest_is_reused_without_an_ai_call():
    today = SimpleNamespace(digest_date=__import__("datetime").date.today(), summary="Already written.", stats={})
    db = _Session(existing_digest=today)
    provider = FakeProvider(_response(text="should never be asked"))

    out = _digest(TrademarksService(db, claude_client=provider))

    assert (out.summary, out.generated) == ("Already written.", False)
    assert provider.calls == [] and db.rows == []


def test_an_empty_digest_is_a_502_and_nothing_is_saved(monkeypatch):
    db = _Session()
    service = TrademarksService(db, claude_client=FakeProvider(_response(text="")))
    monkeypatch.setattr(service, "_gather_digest_stats", lambda **_k: (STATS, SAMPLES))

    with pytest.raises(HTTPException) as caught:
        _digest(service)

    assert (caught.value.status_code, caught.value.detail) == (502, "Digest returned no summary")
    assert db.digests == [] and db.rows[0].status == "validation_failed"


def test_a_digest_provider_failure_is_recorded_and_saves_nothing(monkeypatch):
    db = _Session()
    service = TrademarksService(db, claude_client=FakeProvider(error=TimeoutError()))
    monkeypatch.setattr(service, "_gather_digest_stats", lambda **_k: (STATS, SAMPLES))

    with pytest.raises(TimeoutError):
        _digest(service)

    assert db.digests == [] and (db.rows[0].status, db.rows[0].error_class) == ("failed", "TimeoutError")
