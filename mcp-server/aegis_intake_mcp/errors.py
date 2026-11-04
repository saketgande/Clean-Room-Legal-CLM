"""The one error type every tool failure is expressed in. The server turns it into
`CallToolResult(isError=True)` carrying `to_json()` (plan: "Error result")."""

from __future__ import annotations

import json
from typing import Any

# The closed set of codes agents can see (plan error table).
CODES = frozenset(
    {
        "authentication_failed",
        "permission_denied",
        "not_found",
        "conflict",
        "too_large",
        "unsupported_file_type",
        "invalid_input",
        "rate_limited",
        "service_unavailable",
        "outcome_unknown",
    }
)


class ToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        idempotency_key: str | None = None,
        field: str | None = None,
        platform_request_id: str | None = None,
    ) -> None:
        if code not in CODES:
            raise ValueError(f"unknown tool error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.idempotency_key = idempotency_key
        self.field = field
        self.platform_request_id = platform_request_id

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retry_after_seconds": self.retry_after_seconds,
                "idempotency_key": self.idempotency_key,
                "field": self.field,
                "platform_request_id": self.platform_request_id,
            }
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False)
