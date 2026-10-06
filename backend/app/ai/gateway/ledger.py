"""The single writer for gateway calls to the AI call ledger (a_i_call_log).

Every call that goes through the gateway leaves exactly one row here — on
success, on invalid output, and on failure — so the AI Usage & Cost page and
any audit question ("what did the AI see and say for this request?") have one
place to look.

Transaction ownership: each row is written and committed in its OWN short
session, never on the caller's. An AI call that happened is spend that happened,
whatever the caller does next: many callers never commit after the call (a
GET that only reads, a Gmail message skipped as "not legal", a notice file that
is only read) or roll back on a later error, and a row riding on their
transaction would silently disappear. It also means a ledger failure can never
poison the caller's session. (The old agent logger got the same effect by
committing the caller's session mid-transaction, which also committed whatever
else the caller had pending.)

Exception — ``on=<session>``: a caller that owns a transaction it always
commits (AIController's skill runs, whose citations reference the row by
foreign key and whose rows must roll back with a test's transaction) can ask
for the row on its own session instead. It is added inside a savepoint and
flushed; that caller commits.

What is stored: token counts (including prompt-cache tokens, which today's
writers drop), model, prompt key/version/hash, latency, status, and a small
caller-chosen ``log_input`` summary — never the full prompt. The raw provider
response is stored only when settings.ai_store_raw_outputs is on, as elsewhere.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.gateway.features import AIFeature
from app.ai.prompt_versions import PromptBundle
from app.core.config import settings
from app.core.models import AICallLog
from app.integrations.claude import ClaudeProviderResponse


@dataclass(frozen=True)
class AICallContext:
    """Who and what a call is for. ``resource`` is (type, id) of the record the
    call is about — e.g. ("intake_request", request.id) — so its ledger rows can
    be found from that record."""

    org_id: str
    user_id: str | None = None
    resource: tuple[str, str] | None = None
    request_id: str | None = None
    job_id: str | None = None
    session_id: str | None = None
    assistant_run_id: str | None = None
    tool_call_id: str | None = None
    skill_run_id: str | None = None


def _default_sessions() -> Session:
    from app.core.database import SessionLocal

    return SessionLocal()


def record_call(
    *,
    sessions: Callable[[], Session] | None = None,
    on: Session | None = None,
    feature: AIFeature,
    ctx: AICallContext,
    prompt: PromptBundle,
    model: str,
    log_input: dict[str, Any],
    status: str,
    validation_status: str,
    response: ClaudeProviderResponse | None = None,
    validated_output: dict[str, Any] | None = None,
    validation_error: str | None = None,
    error_class: str | None = None,
    latency_ms: float | None = None,
) -> AICallLog:
    usage = response.token_usage if response is not None else {}
    resource_type, resource_id = ctx.resource if ctx.resource else (None, None)
    row = AICallLog(
        org_id=ctx.org_id,
        request_id=ctx.request_id,
        skill_run_id=ctx.skill_run_id,
        job_id=ctx.job_id,
        session_id=ctx.session_id,
        assistant_run_id=ctx.assistant_run_id,
        tool_call_id=ctx.tool_call_id,
        resource_type=resource_type,
        resource_id=resource_id,
        provider="claude",
        model=response.model if response is not None else model,
        model_config_hash=prompt.model_config_hash,
        prompt_key=prompt.prompt_key,
        prompt_version=prompt.version,
        prompt_hash=prompt.prompt_hash,
        input_payload=log_input,
        output_schema_name=feature.output_model.__name__ if feature.output_model else feature.tool_name,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
        cache_read_input_tokens=usage.get("cache_read_input_tokens"),
        latency_ms=response.latency_ms if response is not None else latency_ms,
        status=status,
        validation_status=validation_status,
        validation_error=validation_error,
        error_class=error_class,
        provider_request_id=response.provider_request_id if response is not None else None,
        stop_reason=response.stop_reason if response is not None else None,
        raw_ai_output=response.raw_response if (response is not None and settings.ai_store_raw_outputs) else None,
        validated_output=validated_output,
        created_by_user_id=ctx.user_id,
        updated_by_user_id=ctx.user_id,
    )
    if on is not None:
        # The caller owns this row's transaction (see the module docstring).
        # A savepoint keeps a failed insert from poisoning the caller's session.
        with on.begin_nested():
            on.add(row)
            on.flush()
        return row
    session = (sessions or _default_sessions)()
    try:
        session.add(row)
        session.commit()  # SessionLocal has expire_on_commit=False, so row.id stays readable
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return row
