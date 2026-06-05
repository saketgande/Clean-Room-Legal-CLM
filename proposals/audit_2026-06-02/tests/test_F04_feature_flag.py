"""F-04 feature-flag enforcement tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_disabled_feature_flag_omits_tool_from_schema(monkeypatch):
    """``_assistant_tool_schemas`` skips a tool when its flag resolves false."""
    from app.ai.tool_policy import AssistantToolPolicy
    from app.ai.tool_registry import tool_registry

    flagged = [
        spec for spec in tool_registry.all() if getattr(spec, "feature_flag", None)
    ]
    if not flagged:
        return
    target = flagged[0]
    AssistantToolPolicy.clear_cache()
    monkeypatch.setattr(
        AssistantToolPolicy,
        "is_enabled",
        classmethod(lambda cls, name, db, org_id: name != target.name),
    )
    from proposals.audit_2026_06_02.rewrites.F04_feature_flag_runtime import (  # type: ignore[import-not-found]
        ai_controller_assistant_tool_schemas as mod,
    )

    user = SimpleNamespace(
        id="u-1",
        org_id="org-A",
        permission_values={target.required_permission, "assistant:use"},
        roles=[SimpleNamespace(name="member")],
    )
    schemas = mod.assistant_tool_schemas(user=user, db=MagicMock(), org_id="org-A")
    names = {schema["name"] for schema in schemas}
    assert target.name not in names


def test_disabled_feature_flag_blocks_inflight_call(monkeypatch):
    """``execute_tool_with_feature_gate`` returns a ``feature_disabled`` shape."""
    import asyncio

    from app.ai.tool_policy import AssistantToolPolicy
    from app.ai.tool_registry import tool_registry

    flagged = [
        spec for spec in tool_registry.all() if getattr(spec, "feature_flag", None)
    ]
    if not flagged:
        return
    target = flagged[0]
    AssistantToolPolicy.clear_cache()
    monkeypatch.setattr(
        AssistantToolPolicy,
        "is_enabled",
        classmethod(lambda cls, name, db, org_id: False),
    )

    from proposals.audit_2026_06_02.rewrites.F04_feature_flag_runtime import (  # type: ignore[import-not-found]
        ai_tool_runtime_execute as mod,
    )

    user = SimpleNamespace(
        id="u-1",
        org_id="org-A",
        permission_values={target.required_permission, "assistant:use", "*"},
        roles=[SimpleNamespace(name="member")],
    )

    class _FakeSelf:
        async def _execute_validated(self, *args, **kwargs):  # noqa: ARG002
            raise AssertionError("should not be reached when feature disabled")

    fake_self = _FakeSelf()
    result = asyncio.run(
        mod.execute_tool_with_feature_gate(
            fake_self,
            db=MagicMock(),
            tool_name=target.name,
            tool_input={},
            user=user,
            session_id="s-1",
        )
    )
    assert result["status"] == "feature_disabled"
    assert result["tool_name"] == target.name
