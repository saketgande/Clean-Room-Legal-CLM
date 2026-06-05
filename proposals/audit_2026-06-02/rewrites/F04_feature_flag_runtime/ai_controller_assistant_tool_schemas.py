"""F-04 rewrite — ``AIController._assistant_tool_schemas`` (feature_flag gate at schema-build).

Replaces ``backend/app/ai/controller.py:594-608``. The previous body
filtered by ``enabled_by_default`` and ``has_permission`` only — never
consulted ``AdminSetting`` for the spec's ``feature_flag``. A customer
who disabled e.g. ``feature.ai.edit_suggestions`` still saw the tool
offered to Claude. CC-3 ``AssistantToolPolicy.is_enabled`` is now
consulted on every schema build.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ai.tool_policy import AssistantToolPolicy
from app.ai.tool_registry import tool_registry
from app.auth.models import User
from app.core.rbac import has_permission


def assistant_tool_schemas(
    *,
    user: User,
    db: Session,
    org_id: str,
) -> list[dict[str, Any]]:
    """Build the Claude tool-schema list, gated by per-org feature flags."""
    tools: list[dict[str, Any]] = []
    for tool in tool_registry.all():
        if not tool.enabled_by_default:
            continue
        if not has_permission(user.permission_values, tool.required_permission):
            continue
        if not AssistantToolPolicy.is_enabled(tool.name, db, org_id):
            continue
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
            }
        )
    return tools
