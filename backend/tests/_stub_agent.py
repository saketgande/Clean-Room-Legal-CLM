"""A stand-in agent with the OLD shape (a hand-written dict schema, no output model).

Several gateway tests need "a structured agent that has no output model" to prove the dict path still works
(a shape check only, the caller's ``input_schema`` is what Claude is sent). They used to borrow a real agent
for it, but every agent moves onto an output model one by one, so the borrowed one stopped being a dict agent
and its tests started failing on their placeholder answers. This one never moves.

It is registered only while a test runs (autouse ``dict_agent_registered``, active in any module that imports it), never in the live registry, so nothing
that counts or lists the real features sees it. It borrows ``flow_router``'s prompt only because the gateway
needs some shipped prompt to load.
"""
import pytest

from app.ai.gateway.features import AIFeature, FeatureKind, PromptLayout, feature_registry

STUB_DICT_AGENT = "stub_dict_agent"


@pytest.fixture(autouse=True)
def dict_agent_registered(monkeypatch):
    feature = AIFeature(
        key=STUB_DICT_AGENT, kind=FeatureKind.AGENT, label="Stub", category="Other",
        prompt_key="flow_router", default_prompt_version="1.0.0", layout=PromptLayout.FEATURE_SYSTEM,
        guard=True, max_tokens=600, temperature=0.0, tool_name="suggest_flow", output_model=None,
    )
    real_get = feature_registry.get

    # Only get() knows the stand-in: all() and labels() (which tests count and list) keep the real 33.
    monkeypatch.setattr(
        feature_registry, "get", lambda key: feature if key == STUB_DICT_AGENT else real_get(key)
    )
