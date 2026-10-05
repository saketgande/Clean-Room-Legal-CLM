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


def test_a_general_legal_question_is_answered_before_anything_is_filed():
    """The Legal Intake "General legal question" card used to open a keyword
    script that could only file a ticket (and filed most things as NDAs). It
    now opens Ask Aegis, which must answer first and file with Legal only when
    a lawyer is needed and the user agrees — as a general question."""
    prompt = DEFAULT_SKILL_PROMPTS["assistant_streaming"]
    assert "Answer it first" in prompt
    assert "create_intake_request" in prompt
    assert "Legal Question — General" in prompt
    assert "Only when the user says yes" in prompt
