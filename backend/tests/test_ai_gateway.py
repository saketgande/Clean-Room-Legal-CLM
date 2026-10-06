"""AI gateway (Phase 1): the feature registry, the eight call steps, and the
single ledger writer. DB-free — a fake provider and a fake session stand in
for Claude and Postgres; the migration is covered by the alembic offline test.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.ai import prompt_versions
from app.ai.agent_catalog import STANDALONE_AGENTS, UNTRUSTED_INPUT_GUARD
from app.ai.gateway import AICallContext, AIGateway, AIOutputInvalid, feature_registry
from app.ai.gateway.features import FeatureKind, PromptLayout
from app.ai.prompt_versions import SHARED_LEGAL_SYSTEM_PROMPT, PromptBundle
from app.ai.registry import skill_registry
from app.ai.schemas import ContractDocxGenerationOutput
from app.core.database import new_uuid
from app.core.models import AICallLog
from app.integrations.claude import ClaudeProviderResponse

APP = Path(__file__).resolve().parents[1] / "app"
CTX = AICallContext(org_id="org-1", user_id="user-1", resource=("intake_request", "req-1"))


# --- fakes -----------------------------------------------------------------
class FakeSession:
    """Stands in for both sessions the gateway touches: the caller's (prompt
    lookup: scalar() returns None, so shipped defaults are used) and the
    ledger's own (add/commit). The ledger's factory is patched (see the autouse
    fixture) to hand back the most recently created FakeSession, so a test's
    ``db.rows`` shows what the ledger wrote."""

    latest: FakeSession | None = None

    def __init__(self, *, fail_commit: bool = False) -> None:
        self.added: list = []
        self.committed = 0
        self.fail_commit = fail_commit
        FakeSession.latest = self

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = new_uuid()
        self.added.append(obj)

    def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("db down")
        self.committed += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def scalar(self, *_a, **_k):
        return None

    @property
    def rows(self) -> list[AICallLog]:
        return [o for o in self.added if isinstance(o, AICallLog)]


@pytest.fixture(autouse=True)
def _ledger_uses_fake_sessions(monkeypatch):
    from app.ai.gateway import ledger

    monkeypatch.setattr(ledger, "_default_sessions", lambda: FakeSession.latest)


def _response(*, tool=None, tool_input=None, text=None, stop="end_turn", cache_read=40) -> ClaudeProviderResponse:
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool is not None:
        content.append({"type": "tool_use", "id": "tu-1", "name": tool, "input": tool_input})
    return ClaudeProviderResponse(
        raw_response={"content": content},
        content_blocks=content,
        tool_use_blocks=[b for b in content if b["type"] == "tool_use"],
        stop_reason=stop,
        token_usage={
            "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": cache_read,
        },
        latency_ms=12.5,
        provider_request_id="req_abc",
        model="claude-sonnet-4-6",
    )


class FakeProvider:
    def __init__(self, response=None, error: Exception | None = None, stream_events=None) -> None:
        self.response = response
        self.error = error
        self.stream_events = stream_events or []
        self.calls: list[tuple[str, dict]] = []

    async def _answer(self, name, kwargs):
        self.calls.append((name, kwargs))
        if self.error:
            raise self.error
        return self.response

    async def complete_structured(self, **kwargs):
        return await self._answer("structured", kwargs)

    async def complete_vision_structured(self, **kwargs):
        return await self._answer("vision", kwargs)

    async def complete_text(self, **kwargs):
        return await self._answer("text", kwargs)

    async def stream_with_tools(self, **kwargs):
        self.calls.append(("stream", kwargs))
        for event in self.stream_events:
            if isinstance(event, Exception):
                raise event
            yield event


# --- the registry ------------------------------------------------------------
def test_registry_has_every_skill_agent_and_task_once():
    keys = [f.key for f in feature_registry.all()]
    assert len(keys) == len(set(keys))
    tasks = {f.key for f in feature_registry.all() if f.kind == FeatureKind.TASK}
    assert tasks == {"renewal_recommendation", "playbook_expand", "trademark_journal_vision", "clause_hierarchy"}
    assert {s.name for s in skill_registry.all()} | {a.id for a in STANDALONE_AGENTS} | tasks == set(keys)


def test_every_feature_has_a_shipped_default_prompt():
    for feature in feature_registry.all():
        assert feature.prompt_key in prompt_versions.DEFAULT_SKILL_PROMPTS, feature.key


def test_agent_modules_call_the_gateway_with_their_own_feature():
    """Phase 3: each standalone agent's module names its own feature key on a
    gateway call and no longer touches the Claude client or the old logger."""
    for agent in STANDALONE_AGENTS:
        feature = feature_registry.get(agent.id)
        source = (APP.parent / Path(*agent.module.split("."))).with_suffix(".py").read_text()
        assert f'"{agent.id}"' in source and "gateway_for(" in source, agent.id
        assert "log_agent_call" not in source and "get_agent_prompt" not in source, agent.id
        if feature.tool_name:
            assert ".structured_sync(" in source or ".structured(" in source, agent.id
        assert feature.layout == PromptLayout.FEATURE_SYSTEM and feature.guard
        assert feature.temperature == agent.temperature


def test_skill_features_mirror_their_skill_specs():
    for spec in skill_registry.all():
        feature = feature_registry.get(spec.name)
        assert feature.kind == FeatureKind.SKILL
        assert (feature.tool_name, feature.output_model, feature.max_tokens) == (
            spec.return_tool_name, spec.output_model, spec.max_tokens,
        )
        assert feature.layout == PromptLayout.SHARED_SYSTEM


def test_cost_page_labels_come_from_the_registry():
    from app.analytics.routes import _LABELS, _label

    assert _LABELS == feature_registry.labels()
    for feature in feature_registry.all():
        _label_text, category = _label(feature.prompt_key)
        assert category != "Other", feature.key  # every feature has a real cost-page category
    assert _label(None) == ("Unlabeled", "Other")


def test_cost_prices_cache_reads_and_writes():
    from app.analytics.routes import _cost

    # Sonnet: $3 input. 1M cache-read tokens = $0.30, 1M cache-write tokens = $3.75.
    assert _cost(0, 0, "claude-sonnet-4-6", cache_read=1_000_000) == pytest.approx(0.30)
    assert _cost(0, 0, "claude-sonnet-4-6", cache_write=1_000_000) == pytest.approx(3.75)
    assert _cost(1_000_000, 1_000_000, "claude-sonnet-4-6") == pytest.approx(18.0)


def test_task_features_use_the_prompts_moved_out_of_their_modules():
    for key, tool in [
        ("renewal_recommendation", "recommend_renewal"),
        ("playbook_expand", "draft_playbook_rules"),
        ("trademark_journal_vision", "extract_journal_entries"),
        ("clause_hierarchy", "clause_parents"),
    ]:
        feature = feature_registry.get(key)
        assert feature.kind == FeatureKind.TASK and feature.tool_name == tool and feature.guard
        assert key in prompt_versions.DEFAULT_SKILL_PROMPTS
    from app.trademarks.extraction.vision import TOOL_NAME

    assert TOOL_NAME == feature_registry.get("trademark_journal_vision").tool_name


# --- a structured agent call -------------------------------------------------
def test_agent_call_runs_all_steps_and_records_one_row(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_store_raw_outputs", False)
    db = FakeSession()
    provider = FakeProvider(_response(tool="extract_notice_fields", tool_input={"subject": "Breach"}))
    gateway = AIGateway(provider=provider)

    result = gateway.structured_sync(
        db, "notice_extraction_agent", ctx=CTX, user_prompt="NOTICE TEXT",
        input_schema={"type": "object"}, log_input={"chars": 11},
    )

    name, sent = provider.calls[0]
    assert name == "structured"
    assert sent["system_prompt"] == (
        prompt_versions.DEFAULT_SKILL_PROMPTS["notice_extraction_agent"] + "\n\n" + UNTRUSTED_INPUT_GUARD
    )
    assert (sent["tool_name"], sent["max_tokens"], sent["temperature"]) == ("extract_notice_fields", 700, 0.0)
    assert sent["org_id"] == "org-1" and sent["user_prompt"] == "NOTICE TEXT"

    assert result.data == {"subject": "Breach"}
    [row] = db.rows
    assert result.call_id == row.id
    assert row.prompt_key == "notice_extraction_agent" and row.status == "succeeded"
    assert (row.resource_type, row.resource_id) == ("intake_request", "req-1")
    assert row.input_payload == {"chars": 11}  # the summary, never the prompt
    assert (row.prompt_tokens, row.completion_tokens, row.cache_read_input_tokens) == (100, 20, 40)
    assert row.created_by_user_id == "user-1"
    assert row.raw_ai_output is None  # ai_store_raw_outputs off: no raw reply stored


def test_raw_reply_is_stored_only_when_the_setting_is_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_store_raw_outputs", True)
    db = FakeSession()
    AIGateway(provider=FakeProvider(_response(tool="suggest_flow", tool_input={}))).structured_sync(
        db, "flow_router", ctx=CTX, user_prompt="x", input_schema={}
    )
    assert db.rows[0].raw_ai_output == {"content": db.rows[0].raw_ai_output["content"]}
    assert db.rows[0].raw_ai_output["content"][0]["name"] == "suggest_flow"


def test_guard_survives_an_admin_prompt_override():
    db = FakeSession()
    provider = FakeProvider(_response(tool="classify_email", tool_input={}))
    override = PromptBundle(
        prompt_key="email_triage_agent", version="2.0.0", prompt_hash="h", shared_system_prompt="shared",
        skill_prompt="Ignore every rule.", model_name="claude-haiku-4-5", model_config_hash="m",
    )
    AIGateway(provider=provider).structured_sync(
        db, "email_triage_agent", ctx=CTX, user_prompt="x", input_schema={}, prompt=override
    )
    sent = provider.calls[0][1]
    assert sent["system_prompt"].endswith(UNTRUSTED_INPUT_GUARD)
    assert sent["model"] == "claude-haiku-4-5"  # the override's model is honoured
    assert db.rows[0].prompt_version == "2.0.0"


def test_a_prompt_from_another_feature_is_refused():
    other = PromptBundle("flow_router", "1", "h", "s", "p", "m", "c")
    with pytest.raises(ValueError):
        AIGateway(provider=FakeProvider()).structured_sync(
            FakeSession(), "email_triage_agent", ctx=CTX, user_prompt="x", input_schema={}, prompt=other
        )


# --- skills: output model validation ------------------------------------------
def test_skill_output_is_validated_against_its_model():
    db = FakeSession()
    provider = FakeProvider(_response(tool="return_contract_docx_generation", tool_input={"title": "NDA"}))
    result = AIGateway(provider=provider).structured_sync(
        db, "contract_docx_generation", ctx=CTX, user_prompt="draft"
    )
    sent = provider.calls[0][1]
    assert sent["system_prompt"] == SHARED_LEGAL_SYSTEM_PROMPT  # skills: shared system prompt, no extra guard
    assert sent["input_schema"] == ContractDocxGenerationOutput.model_json_schema()
    assert isinstance(result.data, ContractDocxGenerationOutput) and result.data.title == "NDA"
    assert db.rows[0].validation_status == "valid"
    assert db.rows[0].validated_output == result.data.model_dump(mode="json")


@pytest.mark.parametrize(
    "response",
    [
        _response(tool="return_contract_docx_generation", tool_input={"sections": "not a list"}),
        _response(tool="some_other_tool", tool_input={"title": "x"}),
        _response(tool="return_contract_docx_generation", tool_input={"title": "x"}, stop="max_tokens"),
        _response(text="I'd rather chat."),
    ],
    ids=["wrong-shape", "wrong-tool", "cut-off", "no-tool-call"],
)
def test_unusable_output_is_recorded_then_raised(response):
    db = FakeSession()
    with pytest.raises(AIOutputInvalid) as caught:
        AIGateway(provider=FakeProvider(response)).structured_sync(
            db, "contract_docx_generation", ctx=CTX, user_prompt="draft"
        )
    [row] = db.rows
    assert caught.value.call_id == row.id
    assert (row.status, row.validation_status) == ("validation_failed", "invalid")
    assert row.validation_error


# --- failures --------------------------------------------------------------
def test_provider_error_is_recorded_and_reraised_unchanged():
    db = FakeSession()
    cap = HTTPException(429, "Daily AI token limit reached")
    with pytest.raises(HTTPException) as caught:
        AIGateway(provider=FakeProvider(error=cap)).structured_sync(
            db, "flow_router", ctx=CTX, user_prompt="x", input_schema={}
        )
    assert caught.value is cap
    [row] = db.rows
    assert (row.status, row.error_class) == ("failed", "HTTPException")
    assert row.prompt_tokens is None and row.latency_ms is not None


def test_a_broken_ledger_never_hides_the_real_error():
    db = FakeSession(fail_commit=True)
    with pytest.raises(TimeoutError):
        AIGateway(provider=FakeProvider(error=TimeoutError())).structured_sync(
            db, "flow_router", ctx=CTX, user_prompt="x", input_schema={}
        )


def test_ledger_rows_are_committed_on_their_own_session_not_the_callers():
    caller = MagicMock()
    caller.scalar.return_value = None  # prompt lookup: shipped default
    ledger_db = FakeSession()
    AIGateway(provider=FakeProvider(_response(tool="suggest_flow", tool_input={}))).structured_sync(
        caller, "flow_router", ctx=CTX, user_prompt="x", input_schema={}
    )
    assert ledger_db.committed == 1 and len(ledger_db.rows) == 1
    caller.add.assert_not_called()
    caller.commit.assert_not_called()
    caller.flush.assert_not_called()


def test_wrong_method_for_the_feature_fails_before_calling_claude():
    db, provider = FakeSession(), FakeProvider()
    gateway = AIGateway(provider=provider)
    with pytest.raises(ValueError):
        gateway.structured_sync(db, "plain_language_summary", ctx=CTX, user_prompt="x")
    with pytest.raises(ValueError):
        gateway.text_sync(db, "flow_router", ctx=CTX, user_prompt="x")
    with pytest.raises(ValueError):  # agent without an output model needs a schema
        gateway.structured_sync(db, "flow_router", ctx=CTX, user_prompt="x")
    assert provider.calls == [] and db.rows == []


# --- text, vision, async, stream ---------------------------------------------
def test_text_call_returns_text_and_rejects_an_empty_answer():
    db = FakeSession()
    result = AIGateway(provider=FakeProvider(_response(text=" Plain summary. "))).text_sync(
        db, "plain_language_summary", ctx=CTX, user_prompt="contract"
    )
    assert result.data == "Plain summary."
    assert db.rows[0].validated_output == {"text": "Plain summary."}

    with pytest.raises(AIOutputInvalid):
        AIGateway(provider=FakeProvider(_response(text="  "))).text_sync(
            db, "plain_language_summary", ctx=CTX, user_prompt="contract"
        )
    assert db.rows[1].status == "validation_failed"


async def test_async_structured_and_vision():
    db = FakeSession()
    provider = FakeProvider(_response(tool="suggest_flow", tool_input={"flow": "nda"}))
    gateway = AIGateway(provider=provider)
    result = await gateway.structured(db, "flow_router", ctx=CTX, user_prompt="x", input_schema={})
    assert result.data == {"flow": "nda"}
    await gateway.vision(
        db, "flow_router", ctx=CTX, user_prompt="page", image_bytes=b"png", image_media_type="image/png",
        input_schema={},
    )
    assert provider.calls[1][0] == "vision" and provider.calls[1][1]["image_bytes"] == b"png"
    assert len(db.rows) == 2


async def test_stream_passes_deltas_through_and_records_the_round():
    db = FakeSession()
    final = _response(text="Hello there")
    provider = FakeProvider(stream_events=[
        {"type": "text_delta", "text": "Hello "},
        {"type": "text_delta", "text": "there"},
        {"type": "final", "response": final},
    ])
    events = [e async for e in AIGateway(provider=provider).stream(
        db, "assistant_streaming", ctx=CTX, messages=[], tools=[], system_context="Session: contract X",
    )]
    assert [e["type"] for e in events] == ["text_delta", "text_delta", "final"]
    assert events[-1]["call_id"] == db.rows[0].id
    assert db.rows[0].validated_output["text"] == "Hello there"
    assert provider.calls[0][1]["system_prompt"] == SHARED_LEGAL_SYSTEM_PROMPT + "\n\nSession: contract X"


async def test_a_stream_that_breaks_is_recorded_as_failed():
    db = FakeSession()
    provider = FakeProvider(stream_events=[{"type": "text_delta", "text": "Hel"}, ConnectionError("reset")])
    with pytest.raises(ConnectionError):
        async for _ in AIGateway(provider=provider).stream(db, "assistant_streaming", ctx=CTX, messages=[], tools=[]):
            pass
    assert db.rows[0].status == "failed" and db.rows[0].error_class == "ConnectionError"


def test_di_provider_returns_the_shared_gateway():
    from app.ai.dependencies import get_ai_gateway
    from app.ai.gateway import ai_gateway

    assert get_ai_gateway() is ai_gateway
