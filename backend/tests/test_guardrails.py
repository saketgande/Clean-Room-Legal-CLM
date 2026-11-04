from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai import guardrail_service as gs
from app.core.config import Settings, validate_runtime_settings


def _verdict(status: str, rail: str | None = None, content: str = ""):
    return SimpleNamespace(status=SimpleNamespace(value=status), rail=rail, content=content)


@pytest.fixture
def real_rails(monkeypatch):
    """Turn the NeMo layer on (conftest runs with MOCK_GUARDRAILS=true)."""
    monkeypatch.setattr(gs.settings, "mock_guardrails", False)
    monkeypatch.setattr(gs.settings, "guardrails_fail_closed", True)


def _patch_check(monkeypatch, verdict=None, error=None):
    calls = []

    async def fake(messages, rail_type):
        calls.append((rail_type, messages))
        if error:
            raise error
        return verdict

    monkeypatch.setattr(gs, "_check", fake)
    return calls


# --- input rail -------------------------------------------------------------


async def test_input_blocked_by_nemo_names_the_rail(real_rails, monkeypatch):
    calls = _patch_check(monkeypatch, _verdict("blocked", "self check input"))
    result = await gs.apply_input_guardrails("What is the CEO's personal mobile number?")
    assert not result.allowed
    assert result.reason == "nemo_input"
    assert "NeMo Guardrails (self check input)" in result.message
    assert "blocked" in result.message
    assert calls[0][0] == "input"


async def test_input_passes(real_rails, monkeypatch):
    _patch_check(monkeypatch, _verdict("passed"))
    result = await gs.apply_input_guardrails("What is the uptime SLA?")
    assert result.allowed
    assert result.message == "What is the uptime SLA?"


async def test_input_failure_fails_closed(real_rails, monkeypatch):
    _patch_check(monkeypatch, error=RuntimeError("anthropic unreachable"))
    result = await gs.apply_input_guardrails("What is the uptime SLA?")
    assert not result.allowed
    assert result.reason == "unavailable"
    assert result.message == gs.UNAVAILABLE_MESSAGE


async def test_input_failure_can_fail_open_when_configured(real_rails, monkeypatch):
    _patch_check(monkeypatch, error=RuntimeError("anthropic unreachable"))
    monkeypatch.setattr(gs.settings, "guardrails_fail_closed", False)
    assert (await gs.apply_input_guardrails("What is the uptime SLA?")).allowed


async def test_empty_input_skips_nemo(real_rails, monkeypatch):
    calls = _patch_check(monkeypatch, _verdict("blocked"))
    assert (await gs.apply_input_guardrails("   ")).allowed
    assert calls == []


async def test_mock_mode_skips_nemo(monkeypatch):
    monkeypatch.setattr(gs.settings, "mock_guardrails", True)
    calls = _patch_check(monkeypatch, _verdict("blocked"))
    assert (await gs.apply_input_guardrails("anything")).allowed
    assert (await gs.apply_output_guardrails("q", "a")).allowed
    assert calls == []


# --- output rail ----------------------------------------------------------------


async def test_output_blocked_by_nemo_names_the_rail(real_rails, monkeypatch):
    calls = _patch_check(monkeypatch, _verdict("blocked", "self check output"))
    result = await gs.apply_output_guardrails("q", "The CEO's mobile is 415-555-0132")
    assert not result.allowed
    assert result.reason == "nemo_output"
    assert "NeMo Guardrails (self check output)" in result.message
    assert "withheld" in result.message
    rail_type, messages = calls[0]
    assert rail_type == "output"
    assert messages[-1] == {"role": "assistant", "content": "The CEO's mobile is 415-555-0132"}


async def test_output_passes_and_uses_nemo_content(real_rails, monkeypatch):
    _patch_check(monkeypatch, _verdict("passed", content="clean answer"))
    result = await gs.apply_output_guardrails("q", "clean answer")
    assert result.allowed
    assert result.message == "clean answer"


# --- non-streaming gates ----------------------------------------------------------


class _FakeDb:
    def __init__(self):
        self.committed = False

    def commit(self):
        self.committed = True


def _gate_kwargs():
    return {"org_id": "o1", "actor_user_id": "u1", "request_id": None, "resource_type": "contract_brain"}


async def test_input_gate_blocks_audits_and_raises_422(real_rails, monkeypatch):
    _patch_check(monkeypatch, _verdict("blocked", "self check input"))
    audits = []
    monkeypatch.setattr(gs, "write_audit_log", lambda db, **kw: audits.append(kw))
    db = _FakeDb()
    with pytest.raises(HTTPException) as exc:
        await gs.enforce_input_guardrails(db, text="secret prompt text", **_gate_kwargs())
    assert exc.value.status_code == 422
    assert "NeMo Guardrails (self check input)" in exc.value.detail
    assert db.committed
    assert audits[0]["action"] == "assistant.guardrail_blocked"
    assert audits[0]["metadata"]["rail"] == "self check input"
    assert "secret prompt text" not in str(audits[0])


async def test_input_gate_allows_normal_question(real_rails, monkeypatch):
    _patch_check(monkeypatch, _verdict("passed"))
    monkeypatch.setattr(gs, "write_audit_log", lambda db, **kw: pytest.fail("must not audit"))
    await gs.enforce_input_guardrails(_FakeDb(), text="What is the governing law?", **_gate_kwargs())


async def test_output_gate_blocks_and_raises_422(real_rails, monkeypatch):
    _patch_check(monkeypatch, _verdict("blocked", "self check output"))
    monkeypatch.setattr(gs, "write_audit_log", lambda db, **kw: None)
    with pytest.raises(HTTPException) as exc:
        await gs.enforce_output_guardrails(_FakeDb(), user_text="q", answer="leaky answer", **_gate_kwargs())
    assert exc.value.status_code == 422
    assert "withheld" in exc.value.detail


# --- settings ----------------------------------------------------------------------


def test_production_rejects_mock_guardrails_and_fail_open():
    base = {
        "environment": "production",
        "secret_key": "x" * 40,
        "setup_token": "y" * 32,
        "mock_claude": False,
        "mock_docusign": False,
        "mock_reducto": False,
        "mock_resend": False,
        "mock_sendgrid": False,
        "mock_signa": False,
        "mock_tmsearch": False,
        "mock_serper": False,
        "docusign_connect_hmac_key": "z" * 40,
        "app_base_url": "https://app.example.com",
        "cors_origins": "https://app.example.com",
        "allowed_hosts": "app.example.com",
        "refresh_cookie_secure": True,
        "refresh_cookie_samesite": "strict",
        "expose_refresh_token_in_body": False,
        "expose_password_reset_token_in_response": False,
    }
    with pytest.raises(RuntimeError, match="MOCK_GUARDRAILS"):
        validate_runtime_settings(Settings(**base, mock_guardrails=True))
    with pytest.raises(RuntimeError, match="GUARDRAILS_FAIL_CLOSED"):
        validate_runtime_settings(Settings(**base, guardrails_fail_closed=False))


# --- rail-specific block messages ---------------------------------------------------


@pytest.mark.parametrize(
    ("rail", "label", "phrase"),
    [
        ("detect sensitive data on input", "detect sensitive data on input", "sensitive personal or financial data"),
        ("topic safety check input $model=topic_control", "topic safety check input", "outside the topics"),
        ("content safety check input $model=content_safety", "content safety check input", "unsafe content"),
        ("self check input", "self check input", "compliance policy"),
    ],
)
async def test_block_message_names_the_firing_rail(real_rails, monkeypatch, rail, label, phrase):
    _patch_check(monkeypatch, _verdict("blocked", rail))
    result = await gs.apply_input_guardrails("some question")
    assert f"NeMo Guardrails ({label})" in result.message
    assert "$model" not in result.message
    assert phrase in result.message
    assert result.rail == rail


# --- evidence plumbing (self check facts) ------


async def test_output_rail_gets_evidence_as_context(real_rails, monkeypatch):
    calls = _patch_check(monkeypatch, _verdict("passed", content="ans"))
    await gs.apply_output_guardrails("q", "ans", evidence="contract text")
    rail_type, messages = calls[0]
    assert rail_type == "output"
    assert messages[0] == {
        "role": "context",
        "content": {"relevant_chunks": "contract text", "check_facts": True},
    }
    assert messages[-1] == {"role": "assistant", "content": "ans"}


async def test_output_rail_without_evidence_sends_no_context_message(real_rails, monkeypatch):
    calls = _patch_check(monkeypatch, _verdict("passed", content="ans"))
    await gs.apply_output_guardrails("q", "ans")
    assert all(m["role"] != "context" for m in calls[0][1])


@pytest.mark.parametrize(
    ("rail", "label", "phrase"),
    [
        ("self check facts", "self check facts", "not supported by the contract text"),
        ("injection detection", "injection detection", "injection patterns"),
        ("context bloat detection on input", "context bloat detection on input", "oversized or padded"),
    ],
)
async def test_new_rails_have_specific_block_messages(real_rails, monkeypatch, rail, label, phrase):
    _patch_check(monkeypatch, _verdict("blocked", rail))
    result = await gs.apply_output_guardrails("q", "a")
    assert f"NeMo Guardrails ({label})" in result.message
    assert phrase in result.message


# --- retrieval gate (context bloat detection on retrieved contract text) ----------------------


def _fake_bloat_module(monkeypatch, blocked: bool):
    import sys
    import types

    calls = []

    async def context_bloat_detection(source, text, config):
        calls.append((source, text))
        return SimpleNamespace(is_blocked=blocked, reason="high_repetition", metadata={"metrics": {}})

    module = types.ModuleType("nemoguardrails.library.context_bloat_detection.actions")
    module.context_bloat_detection = context_bloat_detection
    for name in (
        "nemoguardrails",
        "nemoguardrails.library",
        "nemoguardrails.library.context_bloat_detection",
    ):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "nemoguardrails.library.context_bloat_detection.actions", module)
    monkeypatch.setattr(gs, "get_rails_instance", lambda: SimpleNamespace(config=object()))
    return calls


async def test_retrieval_gate_blocks_padded_text(real_rails, monkeypatch):
    calls = _fake_bloat_module(monkeypatch, blocked=True)
    result = await gs.apply_retrieval_guardrails("q", "lorem ipsum " * 100)
    assert not result.allowed
    assert result.reason == "nemo_retrieval"
    assert "context bloat detection on retrieval" in result.message
    assert calls[0][0] == "retrieval"


async def test_retrieval_gate_allows_normal_text(real_rails, monkeypatch):
    _fake_bloat_module(monkeypatch, blocked=False)
    assert (await gs.apply_retrieval_guardrails("q", "Governing law: Delaware.")).allowed


async def test_retrieval_gate_is_skipped_in_mock_mode(monkeypatch):
    monkeypatch.setattr(gs.settings, "mock_guardrails", True)
    assert (await gs.apply_retrieval_guardrails("q", "anything")).allowed
