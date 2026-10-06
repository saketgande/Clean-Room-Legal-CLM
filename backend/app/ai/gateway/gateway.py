"""The AI gateway: the one way application code is meant to call Claude.

Every call runs the same eight steps, whichever feature asks:

  1. look up the feature in the registry (features.py)
  2. load its prompt — an org's active prompt-table override, else the
     shipped default (prompt_versions.get_active_prompt_bundle)
  3. build the system prompt; append the anti-injection guard if the
     feature says so (it is appended here, never stored in the editable
     prompt text, so an admin override can't remove it)
  4. the per-org daily token cap      } both inside the existing Claude client
  5. call Claude (retries, caching)   } (integrations/claude.py), unchanged
  6. check the output: a cut-off answer, a missing tool call, or a reply that
     doesn't match the feature's output model is rejected
  7. record the call in the AI ledger — success, invalid output and failure
     alike, including prompt-cache tokens (ledger.py)
  8. return a typed result carrying the ledger row's id (``call_id``)

Errors: a provider/network/cap error is recorded and then re-raised unchanged
(so a cap breach still reaches the user as the client's HTTP 429). Bad output
is recorded and raised as AIOutputInvalid. Callers that have a non-AI fallback
catch those and fall back, as they do today.

Sync and async: each method has an async form and a ``*_sync`` form for the
sync call sites (intake agents, notices). In the sync form only the network
call goes through run_coro_blocking; the prompt lookup stays on the caller's
thread and session. The ledger row is written on its own session (ledger.py),
so the caller's transaction is never touched.

Phase 1 (see backend/ARCHITECTURE.md, "AI gateway"): built beside today's call
paths; no production code calls it yet.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD
from app.ai.gateway.features import AIFeature, FeatureRegistry, PromptLayout, feature_registry
from app.ai.gateway.ledger import AICallContext, record_call
from app.ai.prompt_versions import PromptBundle, get_active_prompt_bundle
from app.core.config import settings
from app.core.enums import AICallStatus, AIValidationStatus
from app.integrations.claude import ClaudeProviderResponse, run_coro_blocking

logger = logging.getLogger(__name__)


class AIGatewayError(Exception):
    """Base for errors the gateway raises itself. ``call_id`` is the ledger row."""

    def __init__(self, message: str, *, feature_key: str, call_id: str | None) -> None:
        super().__init__(message)
        self.feature_key = feature_key
        self.call_id = call_id


class AIOutputInvalid(AIGatewayError):
    """Claude answered, but not with something the feature can use."""


@dataclass(frozen=True)
class AIResult:
    feature_key: str
    call_id: str
    # Structured calls: the validated model instance, or the tool-input dict
    # when the feature has no output model. Text calls: the text.
    data: Any
    model: str
    prompt_version: str
    stop_reason: str | None
    usage: dict[str, int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class _Plan:
    feature: AIFeature
    prompt: PromptBundle
    system_prompt: str
    model: str
    max_tokens: int
    # Session the ledger row goes on (None = the ledger's own session).
    record_on: Session | None = None


def _clamp(max_tokens: int) -> int:
    ceiling = settings.claude_max_tokens_ceiling
    return min(max_tokens, ceiling) if ceiling and ceiling > 0 else max_tokens


def _text_of(response: ClaudeProviderResponse) -> str:
    return "".join(b.get("text", "") for b in response.content_blocks if b.get("type") == "text")


class AIGateway:
    def __init__(self, *, provider=None, registry: FeatureRegistry | None = None, ledger_sessions=None) -> None:
        # provider=None resolves the shared client lazily, through the DI
        # provider, so tests and callers can pass a fake instead.
        self._provider = provider
        self._registry = registry or feature_registry
        # Factory for the ledger's own sessions (None = SessionLocal); tests pass a fake.
        self._ledger_sessions = ledger_sessions

    @property
    def provider(self):
        if self._provider is None:
            from app.integrations.dependencies import get_claude_client

            return get_claude_client()
        return self._provider

    @property
    def registry(self) -> FeatureRegistry:
        return self._registry

    # -- steps 1-3 -------------------------------------------------------
    def prompt_for(self, db: Session, feature_key: str, *, org_id: str) -> PromptBundle:
        """The prompt a feature would use for this org (steps 1-2). Skills need
        it to build their user turn (prompt_builder) before calling."""
        feature = self._registry.get(feature_key)
        return get_active_prompt_bundle(
            db,
            org_id=org_id,
            prompt_key=feature.prompt_key,
            default_version=feature.default_prompt_version,
            model_config={"temperature": feature.temperature},
        )

    def _plan(
        self,
        db: Session,
        feature_key: str,
        ctx: AICallContext,
        *,
        prompt: PromptBundle | None,
        system_context: str | None = None,
        record_on: Session | None = None,
    ) -> _Plan:
        feature = self._registry.get(feature_key)
        bundle = prompt or self.prompt_for(db, feature_key, org_id=ctx.org_id)
        if bundle.prompt_key != feature.prompt_key:
            raise ValueError(f"{feature_key}: prompt {bundle.prompt_key!r} belongs to another feature")
        parts = [bundle.skill_prompt if feature.layout == PromptLayout.FEATURE_SYSTEM else bundle.shared_system_prompt]
        if system_context:
            parts.append(system_context)
        if feature.guard:
            parts.append(UNTRUSTED_INPUT_GUARD)
        return _Plan(
            feature=feature,
            prompt=bundle,
            system_prompt="\n\n".join(parts),
            model=feature.model or bundle.model_name,
            max_tokens=_clamp(feature.max_tokens),
            record_on=record_on,
        )

    # -- steps 6-8 -------------------------------------------------------
    def _record_failure(self, db: Session, plan: _Plan, ctx: AICallContext, log_input: dict, exc: Exception, started: float) -> None:
        try:
            record_call(
                sessions=self._ledger_sessions, on=plan.record_on,
                feature=plan.feature,
                ctx=ctx,
                prompt=plan.prompt,
                model=plan.model,
                log_input=log_input,
                status=AICallStatus.FAILED,
                validation_status=AIValidationStatus.NOT_VALIDATED,
                error_class=type(exc).__name__,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception:
            # Never let a ledger problem replace the real error.
            logger.exception("AI ledger write failed for %s", plan.feature.key)

    def _finish_structured(
        self, db: Session, plan: _Plan, ctx: AICallContext, log_input: dict, response: ClaudeProviderResponse
    ) -> AIResult:
        feature = plan.feature
        error: str | None = None
        data: Any = None
        if response.stop_reason == "max_tokens":
            error = f"answer cut off at max_tokens ({plan.max_tokens})"
        else:
            block = next((b for b in response.tool_use_blocks if b.get("name") == feature.tool_name), None)
            raw = block.get("input") if block else None
            if not isinstance(raw, dict):
                error = f"no {feature.tool_name} tool output"
            elif feature.output_model is not None:
                try:
                    data = feature.output_model.model_validate(raw)
                except ValidationError as exc:
                    error = f"output failed {feature.output_model.__name__} validation: {exc.error_count()} error(s)"
            else:
                data = raw

        if error is not None:
            row = record_call(
                sessions=self._ledger_sessions, on=plan.record_on, feature=feature, ctx=ctx, prompt=plan.prompt, model=plan.model, log_input=log_input,
                status=AICallStatus.VALIDATION_FAILED, validation_status=AIValidationStatus.INVALID,
                response=response, validation_error=error,
            )
            raise AIOutputInvalid(f"{feature.key}: {error}", feature_key=feature.key, call_id=row.id)

        validated = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        row = record_call(
            sessions=self._ledger_sessions, on=plan.record_on, feature=feature, ctx=ctx, prompt=plan.prompt, model=plan.model, log_input=log_input,
            status=AICallStatus.SUCCEEDED,
            validation_status=AIValidationStatus.VALID if feature.output_model else AIValidationStatus.NOT_VALIDATED,
            response=response, validated_output=validated,
        )
        return self._result(plan, row.id, data, response)

    def _finish_text(
        self, db: Session, plan: _Plan, ctx: AICallContext, log_input: dict, response: ClaudeProviderResponse
    ) -> AIResult:
        text = _text_of(response).strip()
        if not text:
            row = record_call(
                sessions=self._ledger_sessions, on=plan.record_on, feature=plan.feature, ctx=ctx, prompt=plan.prompt, model=plan.model, log_input=log_input,
                status=AICallStatus.VALIDATION_FAILED, validation_status=AIValidationStatus.INVALID,
                response=response, validation_error="empty answer",
            )
            raise AIOutputInvalid(f"{plan.feature.key}: empty answer", feature_key=plan.feature.key, call_id=row.id)
        row = record_call(
            sessions=self._ledger_sessions, on=plan.record_on, feature=plan.feature, ctx=ctx, prompt=plan.prompt, model=plan.model, log_input=log_input,
            status=AICallStatus.SUCCEEDED, validation_status=AIValidationStatus.NOT_VALIDATED,
            response=response, validated_output={"text": text},
        )
        return self._result(plan, row.id, text, response)

    @staticmethod
    def _result(plan: _Plan, call_id: str, data: Any, response: ClaudeProviderResponse) -> AIResult:
        return AIResult(
            feature_key=plan.feature.key,
            call_id=call_id,
            data=data,
            model=response.model,
            prompt_version=plan.prompt.version,
            stop_reason=response.stop_reason,
            usage=dict(response.token_usage),
        )

    def _require_structured(self, plan: _Plan, input_schema: dict | None) -> dict:
        feature = plan.feature
        if not feature.tool_name:
            raise ValueError(f"{feature.key} is a text feature; use text()/text_sync()")
        if feature.output_model is not None:
            return feature.output_model.model_json_schema()
        if input_schema is None:
            raise ValueError(f"{feature.key} has no output model; pass input_schema")
        return input_schema

    # -- the call itself (steps 4-5), wrapped ----------------------------
    async def _call_async(self, db, plan, ctx, log_input, make_coro: Callable[[], Awaitable[ClaudeProviderResponse]]):
        started = time.perf_counter()
        try:
            return await make_coro()
        except Exception as exc:
            self._record_failure(db, plan, ctx, log_input, exc, started)
            raise

    def _call_sync(self, db, plan, ctx, log_input, make_coro: Callable[[], Awaitable[ClaudeProviderResponse]]):
        started = time.perf_counter()
        try:
            return run_coro_blocking(make_coro)
        except Exception as exc:
            self._record_failure(db, plan, ctx, log_input, exc, started)
            raise

    # -- public API ------------------------------------------------------
    def _structured_coro(self, plan: _Plan, ctx: AICallContext, user_prompt: str, schema: dict):
        return lambda: self.provider.complete_structured(
            org_id=ctx.org_id, system_prompt=plan.system_prompt, user_prompt=user_prompt,
            tool_name=plan.feature.tool_name, input_schema=schema,
            max_tokens=plan.max_tokens, temperature=plan.feature.temperature, model=plan.model,
        )

    async def structured(
        self, db: Session, feature_key: str, *, ctx: AICallContext, user_prompt: str,
        input_schema: dict | None = None, log_input: dict | None = None, prompt: PromptBundle | None = None,
        record_on: Session | None = None,
    ) -> AIResult:
        plan = self._plan(db, feature_key, ctx, prompt=prompt, record_on=record_on)
        schema = self._require_structured(plan, input_schema)
        log_input = log_input or {}
        response = await self._call_async(db, plan, ctx, log_input, self._structured_coro(plan, ctx, user_prompt, schema))
        return self._finish_structured(db, plan, ctx, log_input, response)

    def structured_sync(
        self, db: Session, feature_key: str, *, ctx: AICallContext, user_prompt: str,
        input_schema: dict | None = None, log_input: dict | None = None, prompt: PromptBundle | None = None,
    ) -> AIResult:
        plan = self._plan(db, feature_key, ctx, prompt=prompt)
        schema = self._require_structured(plan, input_schema)
        log_input = log_input or {}
        response = self._call_sync(db, plan, ctx, log_input, self._structured_coro(plan, ctx, user_prompt, schema))
        return self._finish_structured(db, plan, ctx, log_input, response)

    def _vision_coro(self, plan, ctx, user_prompt, image_bytes, media_type, schema):
        return lambda: self.provider.complete_vision_structured(
            org_id=ctx.org_id, system_prompt=plan.system_prompt, user_prompt=user_prompt,
            image_bytes=image_bytes, image_media_type=media_type,
            tool_name=plan.feature.tool_name, input_schema=schema,
            max_tokens=plan.max_tokens, temperature=plan.feature.temperature, model=plan.model,
        )

    async def vision(
        self, db: Session, feature_key: str, *, ctx: AICallContext, user_prompt: str,
        image_bytes: bytes, image_media_type: str,
        input_schema: dict | None = None, log_input: dict | None = None, prompt: PromptBundle | None = None,
    ) -> AIResult:
        plan = self._plan(db, feature_key, ctx, prompt=prompt)
        schema = self._require_structured(plan, input_schema)
        log_input = log_input or {}
        response = await self._call_async(
            db, plan, ctx, log_input, self._vision_coro(plan, ctx, user_prompt, image_bytes, image_media_type, schema)
        )
        return self._finish_structured(db, plan, ctx, log_input, response)

    def _text_coro(self, plan, ctx, user_prompt):
        return lambda: self.provider.complete_text(
            org_id=ctx.org_id, system_prompt=plan.system_prompt, user_prompt=user_prompt,
            max_tokens=plan.max_tokens, temperature=plan.feature.temperature, model=plan.model,
        )

    def _require_text(self, plan: _Plan) -> None:
        if plan.feature.tool_name:
            raise ValueError(f"{plan.feature.key} is a structured feature; use structured()")

    async def text(
        self, db: Session, feature_key: str, *, ctx: AICallContext, user_prompt: str,
        log_input: dict | None = None, prompt: PromptBundle | None = None,
    ) -> AIResult:
        plan = self._plan(db, feature_key, ctx, prompt=prompt)
        self._require_text(plan)
        log_input = log_input or {}
        response = await self._call_async(db, plan, ctx, log_input, self._text_coro(plan, ctx, user_prompt))
        return self._finish_text(db, plan, ctx, log_input, response)

    def text_sync(
        self, db: Session, feature_key: str, *, ctx: AICallContext, user_prompt: str,
        log_input: dict | None = None, prompt: PromptBundle | None = None,
    ) -> AIResult:
        plan = self._plan(db, feature_key, ctx, prompt=prompt)
        self._require_text(plan)
        log_input = log_input or {}
        response = self._call_sync(db, plan, ctx, log_input, self._text_coro(plan, ctx, user_prompt))
        return self._finish_text(db, plan, ctx, log_input, response)

    async def stream(
        self, db: Session, feature_key: str, *, ctx: AICallContext, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]], system_context: str | None = None,
        log_input: dict | None = None, prompt: PromptBundle | None = None,
        record_on: Session | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """One streamed model round (the assistant's tool loop stays with the
        caller). Yields the client's ``text_delta`` events as they arrive, then
        one ``{"type": "final", "response": ..., "call_id": ...}``. The round is
        recorded when it ends, or as failed if the stream breaks.

        Used by the Ask Aegis tool loop (AIController.stream_assistant_run /
        resume_assistant_run), one call per model round."""
        plan = self._plan(db, feature_key, ctx, prompt=prompt, system_context=system_context, record_on=record_on)
        log_input = log_input or {}
        started = time.perf_counter()
        final: ClaudeProviderResponse | None = None
        try:
            async for event in self.provider.stream_with_tools(
                org_id=ctx.org_id, system_prompt=plan.system_prompt, messages=messages, tools=tools,
                max_tokens=plan.max_tokens, temperature=plan.feature.temperature, model=plan.model,
            ):
                if event.get("type") == "final":
                    final = event["response"]
                else:
                    yield event
        except Exception as exc:
            self._record_failure(db, plan, ctx, log_input, exc, started)
            raise
        if final is None:
            exc = RuntimeError("stream ended without a final response")
            self._record_failure(db, plan, ctx, log_input, exc, started)
            raise exc
        row = record_call(
            sessions=self._ledger_sessions, on=plan.record_on, feature=plan.feature, ctx=ctx, prompt=plan.prompt, model=plan.model, log_input=log_input,
            status=AICallStatus.SUCCEEDED, validation_status=AIValidationStatus.NOT_VALIDATED,
            response=final,
            validated_output={
                "text": _text_of(final),
                "tool_uses": [{"id": b.get("id"), "name": b.get("name")} for b in final.tool_use_blocks],
            },
        )
        yield {"type": "final", "response": final, "call_id": row.id}


ai_gateway = AIGateway()


def gateway_for(provider=None) -> AIGateway:
    """The shared gateway, or one bound to an injected Claude client (services
    that take a ``claude_client`` through DI keep honouring it)."""
    return ai_gateway if provider is None else AIGateway(provider=provider)
