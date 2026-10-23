"""assistant_streaming — the primary Ask Aegis chat prompt — was three generic
sentences while the less user-facing skills (playbook_review and the
like) had detailed, carefully calibrated prompts. Pin that it now
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


def test_a_clear_request_to_change_something_goes_straight_to_the_confirm_card():
    """Bug 14: asked to "send it for approval", the assistant wrote "Shall I go ahead?" in the chat. That is
    a second yes with no button, because the app already shows a Confirm / Cancel card for every change.
    The prompt and the stage tool's description both have to tell it to call the tool straight away."""
    from app.ai.registry import skill_registry
    from app.ai.tool_registry import tool_registry

    prompt = DEFAULT_SKILL_PROMPTS["assistant_streaming"]
    assert "call the tool straight away" in prompt
    assert "Confirm / Cancel card" in prompt
    assert "Shall I go ahead?" in prompt  # named so the model knows exactly what not to write
    assert "do not ask" in tool_registry.get("advance_contract_stage").description.lower()
    assert skill_registry.get("assistant_streaming").prompt_version == "1.0.4"


def test_the_review_prompts_tell_the_model_which_way_a_term_helps():
    """Bug 32: a review note called a 30-day payment term "in the Customer's favour" while asking for 45
    days. Paying sooner helps the party being paid, so the note had it backwards. Both prompts that write
    such notes now state the direction, and the new text ships as a new prompt version."""
    from app.ai.registry import skill_registry

    for key in ("playbook_review", "contract_edit_suggestions"):
        prompt = DEFAULT_SKILL_PROMPTS[key]
        assert "<who_it_helps>" in prompt
        assert "shorter payment period helps the party being paid" in prompt
        assert "name the role" in prompt
        assert skill_registry.get(key).prompt_version == "1.0.1"


def test_the_assistant_is_told_how_to_compare_versions_and_not_to_invent_screens():
    """It was asked to "compare v7 and v6", had no tool that reads an older version, and described a Versions
    tab and a diff view that the app did not have. It now has the two tools, is told to use them (and not to
    ask for pasted text), and is given the list of screens that really exist."""
    from app.ai.registry import skill_registry

    prompt = DEFAULT_SKILL_PROMPTS["assistant_streaming"]

    assert "list_contract_versions" in prompt and "compare_contract_versions" in prompt
    assert "never ask the user to paste an older version" in prompt
    assert "<about_the_app>" in prompt and "Never name a tab" in prompt
    assert "There is no Versions tab or Version History panel" in prompt
    # the new text ships as a new prompt version, so runs can be told apart
    assert skill_registry.get("assistant_streaming").prompt_version == "1.0.4"


def test_every_screen_the_prompt_names_is_in_the_editor():
    """The prompt's list of screens is only worth having while it is true. If a tab or button is renamed in
    the editor, this fails, as a reminder to change the prompt too."""
    import re
    from pathlib import Path

    import pytest

    root = Path(__file__).resolve().parents[2] / "frontend" / "src"
    workspace = root / "app" / "(app)" / "contracts" / "[id]" / "_clm-workspace.tsx"
    document = root / "components" / "contract-document.tsx"
    if not (workspace.exists() and document.exists()):
        pytest.skip("the frontend is not checked out next to the backend")

    prompt = DEFAULT_SKILL_PROMPTS["assistant_streaming"]
    section = re.search(r"<about_the_app>(.*?)</about_the_app>", prompt, re.DOTALL).group(1)
    editor = workspace.read_text() + document.read_text()
    for label in ("Assistant", "Review", "Comments", "Lifecycle", "Changes", "Clauses", "Sources", "Parties", "Compare versions"):
        assert label in section, f"the prompt no longer mentions {label}"
        assert f'"{label}"' in editor or f">{label}" in editor or f"{label}\n" in editor or f"{label}<" in editor, (
            f"the editor has no {label}: update the <about_the_app> section of the assistant prompt"
        )


def test_the_assistant_points_to_the_comments_tab_and_does_not_claim_to_read_comments():
    """The editor has a Comments tab (write, resolve, reopen, delete, and the other side's comments). The chat
    has a tool to add a comment but none to read them, so it must send people to the tab and not pretend."""
    prompt = DEFAULT_SKILL_PROMPTS["assistant_streaming"]
    section = prompt.split("<about_the_app>")[1].split("</about_the_app>")[0]
    assert "Comments (the discussion on the contract" in section
    assert "you cannot read the comments" in section
    assert "send them to the Comments tab" in section
