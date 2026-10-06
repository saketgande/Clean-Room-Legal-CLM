"""The AI gateway — see gateway.py for the eight steps every call runs."""

from app.ai.gateway.features import AIFeature, FeatureRegistry, feature_registry
from app.ai.gateway.gateway import (
    AIGateway,
    AIGatewayError,
    AIOutputInvalid,
    AIResult,
    ai_gateway,
    gateway_for,
)
from app.ai.gateway.ledger import AICallContext

__all__ = [
    "AICallContext",
    "AIFeature",
    "AIGateway",
    "AIGatewayError",
    "AIOutputInvalid",
    "AIResult",
    "FeatureRegistry",
    "ai_gateway",
    "feature_registry",
    "gateway_for",
]
