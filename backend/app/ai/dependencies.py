"""FastAPI-native dependency providers for the ai module.

Part of the DI migration (see backend/DI_MIGRATION.md).

AIController and ToolRuntime are stateless singletons (db is passed per method
call, never stored on self) — same shape as the integration clients
(docusign_client, storage_service, ...). They get the same treatment: a
Singleton-lifetime provider that returns the existing module instance
unchanged, rather than a per-request Scoped service.
"""

from typing import TYPE_CHECKING

from app.ai.controller import AIController, ai_controller
from app.ai.registry import SkillRegistry, skill_registry
from app.ai.tool_registry import ToolRegistry, tool_registry
from app.ai.tool_runtime import ToolRuntime, tool_runtime

if TYPE_CHECKING:
    from app.ai.gateway import AIGateway


def get_ai_controller() -> AIController:
    return ai_controller


def get_tool_runtime() -> ToolRuntime:
    return tool_runtime


def get_skill_registry() -> SkillRegistry:
    return skill_registry


def get_tool_registry() -> ToolRegistry:
    return tool_registry


def get_ai_gateway() -> "AIGateway":
    # Deferred import: the gateway is new (Phase 1 of the gateway rollout) and
    # nothing routes through it yet; importing it lazily keeps this module's
    # import cost and cycle surface unchanged for existing callers.
    from app.ai.gateway import ai_gateway

    return ai_gateway
