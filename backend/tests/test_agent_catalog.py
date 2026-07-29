from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.ai import prompt_versions, registry
from app.ai.agent_catalog import STANDALONE_AGENTS, all_agents, get_agent_prompt
from app.intake import gates


def test_standalone_agents_have_default_prompts():
    for agent in STANDALONE_AGENTS:
        assert agent.prompt_key in prompt_versions.DEFAULT_SKILL_PROMPTS


def test_all_agents_lists_skills_and_standalone_with_no_dead_skill():
    ids = {a["id"] for a in all_agents()}
    assert {a.id for a in STANDALONE_AGENTS} <= ids
    assert "contract_brain_query_parse" not in ids
    assert "contract_brain_query_parse" not in {s.name for s in registry.skill_registry.all()}
    assert "contract_brain_query_parse" not in prompt_versions.DEFAULT_SKILL_PROMPTS


def test_get_agent_prompt_falls_back_to_default_when_no_db_override():
    db = MagicMock()
    db.scalar.return_value = None  # no active AIPromptVersion row for this org
    bundle = get_agent_prompt(db, agent_id="flow_router", org_id="org-1")
    assert bundle.skill_prompt == prompt_versions.DEFAULT_SKILL_PROMPTS["flow_router"]
    assert bundle.prompt_key == "flow_router"


def test_classify_gates_trusts_empty_ai_result_but_falls_back_on_none():
    """M5 regression: an AI-classified [] (no gates apply) must NOT trigger the
    keyword fallback — only a None (mock/degraded/error) should."""
    request = SimpleNamespace(org_id="org-1", type_label="NDA", description="", field_values={})

    with patch.object(gates, "_classify_ai", return_value=[]):
        assert gates.classify_gates(MagicMock(), request) == []

    with patch.object(gates, "_classify_ai", return_value=None):
        result = gates.classify_gates(MagicMock(), request)
        assert result == gates.detect_keyword(gates._request_text(request))
