"""An AI answer cut off at max_tokens fails the run instead of saving "nothing".

The failure this guards: clause extraction on the 2.4-4k-word drafting
templates ran into its 4096-token limit. The cut-off tool call arrived with an
empty input, validated as ``{"clauses": []}``, and the run was recorded as a
success — so every drafted contract silently had no clauses and no risk score.
"""

import pytest

from app.ai.gateway import AICallContext, AIGateway, AIOutputInvalid
from app.ai.registry import skill_registry
from app.integrations.claude import ClaudeProviderResponse
from tests.test_ai_gateway import (  # noqa: F401 — the autouse fixture applies here too
    FakeProvider,
    FakeSession,
    _ledger_uses_fake_sessions,
)

# Skills now run through the AI gateway, which owns this check for every
# structured feature (it used to live in AIController._extract_structured_output).


def _response(stop_reason: str, tool_input: dict) -> ClaudeProviderResponse:
    spec = skill_registry.get("clause_extraction")
    block = {"type": "tool_use", "id": "t1", "name": spec.return_tool_name, "input": tool_input}
    return ClaudeProviderResponse(
        raw_response={}, content_blocks=[block], tool_use_blocks=[block], stop_reason=stop_reason,
        token_usage={}, latency_ms=1.0, provider_request_id=None, model="m",
    )


def _run(response: ClaudeProviderResponse):
    return AIGateway(provider=FakeProvider(response)).structured_sync(
        FakeSession(), "clause_extraction", ctx=AICallContext(org_id="org-1"), user_prompt="contract",
    )


def test_a_cut_off_answer_is_an_error_not_an_empty_result():
    with pytest.raises(AIOutputInvalid, match="cut off at max_tokens"):
        _run(_response("max_tokens", {}))


def test_a_complete_answer_still_comes_through():
    out = _run(_response("tool_use", {"clauses": []}))
    assert out.data.model_dump()["clauses"] == []


def test_clause_extraction_has_room_for_a_full_length_contract():
    assert skill_registry.get("clause_extraction").max_tokens >= 8000
