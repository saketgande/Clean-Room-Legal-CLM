"""assistant_streaming — the primary Ask Aegis chat prompt — was three generic
sentences while the less user-facing skills (contract_risk_assessment,
playbook_review) had detailed, carefully calibrated prompts. Pin that it now
actually instructs the assistant's specific behaviors: preferring tool calls
over guessing, resolving names via find_contracts, treating confirmation-gated
tools as proposals rather than completed actions, and not over-asking on
ambiguous requests."""

from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS


def test_assistant_streaming_prompt_covers_its_actual_tool_behaviors():
    prompt = DEFAULT_SKILL_PROMPTS["assistant_streaming"]

    assert "find_contracts" in prompt
    assert "my_attention_items" in prompt
    assert "confirmation" in prompt.lower()
    assert "ambiguous" in prompt.lower()
    # Not just longer — substantively longer than the old three-sentence prompt.
    assert len(prompt) > 500
