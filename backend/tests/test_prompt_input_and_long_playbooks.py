"""LLM-05: the model always sees a skill's input as given (redaction is only for
what gets stored), and a long source document is read in parts, not cut off."""

import asyncio
from types import SimpleNamespace

from app.ai import controller as controller_module
from app.ai.prompt_builder import prompt_builder
from app.ai.registry import skill_registry
from app.ai.schemas import PlaybookGenerationOutput
from app.playbooks import service as playbook_service


def test_prompt_keeps_a_long_text_input_while_the_stored_copy_is_redacted():
    body = "The Supplier shall indemnify the Customer. " * 200
    built = prompt_builder.build_structured_skill_prompt(
        spec=skill_registry.get("playbook_generation"),
        prompt_bundle=SimpleNamespace(skill_prompt="Derive rules.", shared_system_prompt="system"),
        input_payload={"text": body},
    )
    assert body in built.user_prompt
    assert "<redacted text length" not in built.user_prompt
    assert controller_module._redacted_input({"text": body})["text"].startswith("<redacted text length=")


def _generate(monkeypatch, text):
    seen, created = [], {}

    async def fake_skill(*_args, input_payload, **_kwargs):
        seen.append(input_payload)
        part = input_payload["document_part"].split(" of ")[0]
        return PlaybookGenerationOutput(suggested_name="MSA playbook", rules=[
            {"clause_type": "governing_law", "preferred_position": f"from part {part}"},
            {"clause_type": f"clause_from_part_{part}", "preferred_position": "hold firm"},
        ])

    monkeypatch.setattr(controller_module.ai_controller, "run_structured_skill", fake_skill)
    monkeypatch.setattr(playbook_service, "create_initial_playbook", lambda db, **kw: created.update(kw) or SimpleNamespace(**kw))
    db = SimpleNamespace(commit=lambda: None, refresh=lambda _obj: None)
    asyncio.run(playbook_service.generate_playbook_from_text(
        db, user=SimpleNamespace(id="u-1", org_id="org-1"), source_text=text,
    ))
    return seen, created


def test_the_last_third_of_a_long_msa_is_read(monkeypatch):
    text = ("Recitals and definitions. " * 3000) + ("Termination for convenience on 90 days notice. " * 2000)
    seen, created = _generate(monkeypatch, text)
    assert len(seen) == 3
    assert text.strip()[-5000:] in seen[-1]["source_document"]
    types = [rule["clause_type"] for rule in created["generated_rules"]]
    assert "clause_from_part_3" in types and types.count("governing_law") == 1
    assert "were read" not in created["description"]


def test_text_beyond_the_part_limit_is_noted_on_the_draft(monkeypatch):
    text = "Clause text that keeps going. " * 20_000
    seen, created = _generate(monkeypatch, text)
    assert len(seen) == playbook_service._GENERATION_MAX_PARTS
    assert "characters were read" in created["description"]
