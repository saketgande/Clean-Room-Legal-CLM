"""Cross-cutting decision CC-4 — SSE generator lifecycle utility.

Resolves Agent 2 finding F-01 (request-scoped ``db`` reused after the
FastAPI ``get_db`` dependency closes it) and gives F-07 (cost budget /
cancellation) and F-08 (silent tool exceptions) one obvious place to
hook into the streaming generator. Replaces the implicit
session-lifetime contract at
``backend/app/assistant/routes.py:292-379`` and
``backend/app/ai/controller.py:93-325``.

Intended location: ``backend/app/core/streaming.py``. Routes that
produce an SSE ``StreamingResponse`` must open the generator-scoped
session through ``streaming_session`` so commits/rollbacks survive the
request-scoped Session being closed by FastAPI's dependency teardown.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

_logger = logging.getLogger(__name__)


class ClientDisconnected(Exception):
    """Raised when the upstream HTTP client disconnects mid-stream."""


class CostBudgetExceeded(Exception):
    """Raised when a per-run token/cost budget is exhausted."""


class ToolLoopDetected(Exception):
    """Raised when the same idempotency-keyed tool repeats too often."""


@dataclass
class StreamingBudget:
    """Per-run cost/cancellation accounting used by streaming generators."""

    max_tokens: int = 200_000
    max_repeat_per_tool: int = 3
    tokens_used: int = 0
    tool_call_counts: dict[str, int] = field(default_factory=dict)

    def record_tokens(self, total_tokens: int | None) -> None:
        """Accumulate token usage from a Claude call log."""
        if total_tokens is None:
            return
        self.tokens_used += int(total_tokens)

    def check_token_budget(self) -> None:
        """Raise ``CostBudgetExceeded`` when accumulated usage exceeds budget."""
        if self.tokens_used > self.max_tokens:
            raise CostBudgetExceeded(
                f"token_budget_exceeded:{self.tokens_used}/{self.max_tokens}"
            )

    def record_tool_call(self, tool_name: str, idempotency_key: str | None) -> None:
        """Increment the loop detector for this (tool, idempotency_key) pair."""
        if not idempotency_key:
            idempotency_key = f"_no_key:{tool_name}"
        bucket = f"{tool_name}::{idempotency_key}"
        self.tool_call_counts[bucket] = self.tool_call_counts.get(bucket, 0) + 1
        if self.tool_call_counts[bucket] >= self.max_repeat_per_tool:
            raise ToolLoopDetected(
                f"tool_loop_detected:{tool_name}:{idempotency_key}"
            )


@dataclass
class StreamingContext:
    """Per-generator lifecycle handle returned by ``streaming_session``."""

    db: Session
    request: Request
    budget: StreamingBudget

    async def check_disconnect(self) -> None:
        """Raise ``ClientDisconnected`` when the upstream HTTP client is gone."""
        if await self.request.is_disconnected():
            raise ClientDisconnected("client_disconnected")


@asynccontextmanager
async def streaming_session(
    *,
    request: Request,
    budget: StreamingBudget | None = None,
) -> AsyncIterator[StreamingContext]:
    """Open a generator-scoped DB session distinct from ``Depends(get_db)``."""
    db = SessionLocal()
    context = StreamingContext(
        db=db, request=request, budget=budget or StreamingBudget()
    )
    try:
        yield context
        db.commit()
    except ClientDisconnected:
        # Disconnect is a graceful early-exit. Commit whatever work the
        # generator already persisted before bailing.
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def sse_serialize(event: str, payload: dict[str, Any]) -> str:
    """Render a single SSE event line with a strict JSON serializer.

    Replaces ``json.dumps(payload, default=str)`` (Agent 2 finding F-22)
    where the bare ``default=str`` fallback leaked ORM ``__repr__`` and
    ``bytes`` representations into the wire payload. This serializer
    permits only the JSON-native scalars plus ISO-string coercion for
    ``datetime`` and ``UUID``; anything else raises ``TypeError`` and
    the caller is forced to flatten the value before yielding.
    """
    return f"event: {event}\ndata: {json.dumps(payload, default=_safe_default)}\n\n"


def _safe_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()  # type: ignore[no-any-return]
        except Exception as exc:  # pragma: no cover - exotic types
            raise TypeError(f"non-serializable value: {type(value).__name__}") from exc
    raise TypeError(f"non-serializable value: {type(value).__name__}")


def sse_error_event(
    *,
    assistant_run_id: str,
    error_code: str,
    message: str,
) -> str:
    """Render the canonical SSE error event used by the assistant routes."""
    return sse_serialize(
        "error",
        {
            "assistant_run_id": assistant_run_id,
            "error_code": error_code,
            "message": message,
        },
    )
