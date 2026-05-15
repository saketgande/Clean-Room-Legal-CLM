from app.ai.tool_registry import tool_registry


def assistant_tools_snapshot() -> dict[str, dict]:
    return {
        spec.name: {
            "category": spec.category,
            "permission": spec.required_permission,
            "confirmation_policy": spec.confirmation_policy,
            "feature_flag": spec.feature_flag,
            "enabled_by_default": spec.enabled_by_default,
        }
        for spec in tool_registry.all()
    }
