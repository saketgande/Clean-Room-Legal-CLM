from enum import StrEnum
from typing import Any


class AIErrorCode(StrEnum):
    PROVIDER_DOWN = "provider_down"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    USAGE_LIMIT_EXCEEDED = "usage_limit_exceeded"
    VALIDATION_FAILED = "validation_failed"
    CITATION_FAILED = "citation_failed"
    PROMPT_NOT_ACTIVE = "prompt_not_active"
    SKILL_NOT_ENABLED = "skill_not_enabled"
    TOOL_NOT_ENABLED = "tool_not_enabled"
    PERMISSION_DENIED = "permission_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    STALE_RESOURCE = "stale_resource"
    RESOURCE_NOT_FOUND = "resource_not_found"
    TOOL_INPUT_INVALID = "tool_input_invalid"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    STREAM_INTERRUPTED = "stream_interrupted"
    EVAL_FAILED = "eval_failed"


class AIError(Exception):
    def __init__(
        self,
        code: AIErrorCode,
        message: str,
        *,
        retryable: bool = False,
        user_action: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.user_action = user_action
        self.details = details or {}

    def as_payload(self, **ids: str | None) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "user_action": self.user_action,
            "details": self.details,
            **{key: value for key, value in ids.items() if value},
        }
