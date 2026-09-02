import asyncio
import base64
import logging
import time
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx

from app.core.config import settings
from app.integrations._claude_mock import select_mock_tool, structured_payload_by_tool
from app.integrations._http_retry import resilient_call

logger = logging.getLogger(__name__)


def run_coro_blocking(make_coro):
    """Run an async coroutine factory to completion from *sync* code, whether or
    not an event loop is already running on this thread.

    Plain ``asyncio.run()`` raises "cannot be called from a running event loop"
    when a sync helper is invoked from inside an async request handler (e.g. the
    Teams webhook → create_request → AI gate/flow/litigation classifiers). In that
    case we run the coroutine on a fresh loop in a one-off worker thread. The
    coroutine is *created inside* the thread that runs it, so the per-loop httpx
    client keying stays consistent.
    """
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())  # no loop on this thread — the common case
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(make_coro())).result()


@dataclass(frozen=True)
class ClaudeProviderResponse:
    raw_response: dict[str, Any]
    content_blocks: list[dict[str, Any]]
    tool_use_blocks: list[dict[str, Any]]
    stop_reason: str | None
    token_usage: dict[str, int | None]
    latency_ms: float
    provider_request_id: str | None
    model: str


# A shared Anthropic client PER EVENT LOOP. A single module-level client
# forfeits nothing in the API server (one long-lived loop) but crashes Celery
# workers: run_ai_job uses asyncio.run(), which creates and CLOSES a fresh
# loop per job, so a client created on job A's loop dies with "Event loop is
# closed" when job B reuses its pooled connections. Keying by the running
# loop keeps connection pooling within each loop's lifetime; dead loops'
# entries are garbage-collected via the weak keys.
#
# 120s read timeout, applied to a STREAMING request: Claude sends the first
# SSE event within a couple seconds of TTFB and keeps sending events for the
# rest of the generation, so this bounds the gap between events, not total
# generation time. It used to wrap a single non-streaming POST instead, where
# the server sends nothing at all until the full answer is ready — so a
# long-but-healthy completion (or a multi-tool-round-trip answer, each round
# with its own budget) could exceed 120s of pure generation and raise before
# any content came back, discarding the whole answer. max_retries=0: retry
# policy is owned by resilient_call's tenacity wrapper below, not the SDK's
# own (stacking both would compound backoff delays).
_ANTHROPIC_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_anthropic_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, anthropic.AsyncAnthropic]" = (
    weakref.WeakKeyDictionary()
)


def _anthropic_client() -> anthropic.AsyncAnthropic:
    loop = asyncio.get_running_loop()
    client = _anthropic_clients.get(loop)
    if client is None:
        client = anthropic.AsyncAnthropic(
            api_key=settings.claude_api_key,
            timeout=_ANTHROPIC_TIMEOUT,
            max_retries=0,
        )
        _anthropic_clients[loop] = client
    return client


async def aclose_claude_client() -> None:
    """Close this loop's shared Anthropic client. Called on app shutdown."""
    loop = asyncio.get_running_loop()
    client = _anthropic_clients.pop(loop, None)
    if client is not None:
        await client.close()


def _cached_system(system_prompt: str) -> Any:
    """The system prompt as a cache-marked content block so Anthropic serves it
    from cache on repeat turns instead of re-billing the whole thing. Falls back
    to a plain string when caching is off. (A prefix under the model's minimum
    cacheable length is silently ignored by the API, so this is always safe.)"""
    if not settings.ai_prompt_caching or not system_prompt:
        return system_prompt
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _cached_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the tool list for caching. One breakpoint on the LAST tool caches
    the entire tools array up to it — so all 47 assistant tool schemas are sent
    once, then read from cache on every subsequent turn and loop iteration."""
    if not settings.ai_prompt_caching or not tools:
        return tools
    marked = [dict(tool) for tool in tools]
    marked[-1] = {**marked[-1], "cache_control": {"type": "ephemeral"}}
    return marked


class ClaudeClient:
    provider = "claude"

    async def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tool_name: str,
        input_schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> ClaudeProviderResponse:
        if settings.mock_claude:
            return self._mock_structured_response(tool_name=tool_name, model=model or settings.claude_model)

        started = time.perf_counter()
        response_json, request_id = await self._post_messages(
            json_payload={
                "model": model or settings.claude_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": _cached_system(system_prompt),
                "messages": [{"role": "user", "content": user_prompt}],
                "tools": _cached_tools(
                    [
                        {
                            "name": tool_name,
                            "description": f"Return structured data for {tool_name}.",
                            "input_schema": input_schema,
                        }
                    ]
                ),
                "tool_choice": {"type": "tool", "name": tool_name},
            }
        )
        return self._to_provider_response(
            response_json,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider_request_id=request_id,
            model=model or settings.claude_model,
        )

    async def complete_vision_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_bytes: bytes,
        image_media_type: str,
        tool_name: str,
        input_schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> ClaudeProviderResponse:
        """Same shape as complete_structured, but the user turn includes an
        inline base64 image block (Claude's native vision support). Used by
        the trademarks module's ip_india_journal document-extraction template
        to read a rendered PDF page directly, the way a human would."""
        if settings.mock_claude:
            return self._mock_structured_response(tool_name=tool_name, model=model or settings.claude_model)

        started = time.perf_counter()
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        response_json, request_id = await self._post_messages(
            json_payload={
                "model": model or settings.claude_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image_media_type,
                                    "data": image_b64,
                                },
                            },
                        ],
                    }
                ],
                "tools": [
                    {
                        "name": tool_name,
                        "description": f"Return structured data for {tool_name}.",
                        "input_schema": input_schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": tool_name},
            }
        )
        return self._to_provider_response(
            response_json,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider_request_id=request_id,
            model=model or settings.claude_model,
        )

    async def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> ClaudeProviderResponse:
        if settings.mock_claude:
            raw = {
                "id": "mock-text",
                "model": model or settings.claude_model,
                "content": [{"type": "text", "text": "Mock Claude mode is enabled."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
            return self._to_provider_response(raw, latency_ms=0.0, provider_request_id="mock-text", model=model or settings.claude_model)

        started = time.perf_counter()
        response_json, request_id = await self._post_messages(
            json_payload={
                "model": model or settings.claude_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": _cached_system(system_prompt),
                "messages": [{"role": "user", "content": user_prompt}],
            }
        )
        return self._to_provider_response(
            response_json,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider_request_id=request_id,
            model=model or settings.claude_model,
        )

    async def complete_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> ClaudeProviderResponse:
        if not tools:
            return await self.complete_text(
                system_prompt=system_prompt,
                user_prompt=_last_user_text(messages),
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
            )
        if settings.mock_claude:
            return self._mock_tool_response(messages=messages, tools=tools, model=model or settings.claude_model)

        started = time.perf_counter()
        response_json, request_id = await self._post_messages(
            json_payload={
                "model": model or settings.claude_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": _cached_system(system_prompt),
                "messages": messages,
                "tools": _cached_tools(tools),
                "tool_choice": {"type": "auto"},
            }
        )
        return self._to_provider_response(
            response_json,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider_request_id=request_id,
            model=model or settings.claude_model,
        )

    async def stream_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Like complete_with_tools, but yields text as Claude generates it
        instead of buffering the whole answer before returning.

        Yields ``{"type": "text_delta", "text": ...}`` per chunk, then exactly
        one ``{"type": "final", "response": ClaudeProviderResponse}`` — callers
        that need the aggregated response (for tool-use bookkeeping, cost
        logging) get it from that last event.

        # ponytail: no retry here (unlike _post_messages) — once a chunk has
        # been yielded it's likely already on screen, so restarting the whole
        # generation would duplicate visible output. A failure mid-stream just
        # propagates; the caller already persists whatever text arrived before
        # a failure (see _persist_assistant_answer in assistant/routes.py).
        """
        if settings.mock_claude:
            response = (
                self._mock_tool_response(messages=messages, tools=tools, model=model or settings.claude_model)
                if tools
                else await self.complete_text(
                    system_prompt=system_prompt,
                    user_prompt=_last_user_text(messages),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=model,
                )
            )
            text = "".join(block.get("text", "") for block in response.content_blocks if block.get("type") == "text")
            if text:
                yield {"type": "text_delta", "text": text}
            yield {"type": "final", "response": response}
            return

        if not settings.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY is required when mock Claude mode is disabled")

        json_payload: dict[str, Any] = {
            "model": model or settings.claude_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": _cached_system(system_prompt),
            "messages": messages,
        }
        if tools:
            json_payload["tools"] = _cached_tools(tools)
            json_payload["tool_choice"] = {"type": "auto"}

        client = _anthropic_client()
        started = time.perf_counter()
        try:
            async with client.messages.stream(**json_payload) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield {"type": "text_delta", "text": event.delta.text}
                final_message = await stream.get_final_message()
                request_id = stream.request_id
        except anthropic.APIStatusError as exc:
            logger.error(
                "Anthropic /v1/messages %s (model=%s, max_tokens=%s): %s",
                exc.status_code,
                json_payload.get("model"),
                json_payload.get("max_tokens"),
                str(exc.body)[:1500],
                extra={
                    "error_class": "ClaudeAPIError",
                    "status_code": exc.status_code,
                },
            )
            raise

        yield {
            "type": "final",
            "response": self._to_provider_response(
                final_message.to_dict(),
                latency_ms=(time.perf_counter() - started) * 1000,
                provider_request_id=request_id,
                model=model or settings.claude_model,
            ),
        }

    async def _post_messages(self, *, json_payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        if not settings.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY is required when mock Claude mode is disabled")
        client = _anthropic_client()

        @resilient_call("claude")
        async def _do_request() -> tuple[dict[str, Any], str | None]:
            try:
                # Always request via the streaming endpoint, even for callers
                # that only want the final aggregated answer: draining the
                # stream (rather than calling client.messages.create()
                # directly) is what keeps the read-timeout resetting on each
                # SSE event instead of covering the whole generation — see the
                # comment on _ANTHROPIC_TIMEOUT above.
                async with client.messages.stream(**json_payload) as stream:
                    async for _event in stream:
                        pass
                    final_message = await stream.get_final_message()
                    # Message.to_dict() doesn't carry this — it's the SDK
                    # stream object's own property (from the response header).
                    request_id = stream.request_id
            except anthropic.APIStatusError as exc:
                # Surface Anthropic's error body — the part that actually says
                # WHY (e.g. "messages: roles must alternate", "max_tokens too
                # large", "model not found").
                logger.error(
                    "Anthropic /v1/messages %s (model=%s, max_tokens=%s): %s",
                    exc.status_code,
                    json_payload.get("model"),
                    json_payload.get("max_tokens"),
                    str(exc.body)[:1500],
                    extra={
                        "error_class": "ClaudeAPIError",
                        "status_code": exc.status_code,
                    },
                )
                raise
            return final_message.to_dict(), request_id

        return await _do_request()

    def _to_provider_response(
        self,
        raw_response: dict[str, Any],
        *,
        latency_ms: float,
        provider_request_id: str | None,
        model: str,
    ) -> ClaudeProviderResponse:
        content_blocks = raw_response.get("content") or []
        tool_use_blocks = [block for block in content_blocks if block.get("type") == "tool_use"]
        usage = raw_response.get("usage") or {}
        return ClaudeProviderResponse(
            raw_response=raw_response,
            content_blocks=content_blocks,
            tool_use_blocks=tool_use_blocks,
            stop_reason=raw_response.get("stop_reason"),
            token_usage={
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "total_tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
                # Prompt-cache accounting: cache_creation is billed once (a bit
                # above normal input), cache_read is the cheap hit on later turns.
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            },
            latency_ms=latency_ms,
            provider_request_id=provider_request_id,
            model=raw_response.get("model") or model,
        )

    def _mock_structured_response(self, *, tool_name: str, model: str) -> ClaudeProviderResponse:
        payload_by_tool = structured_payload_by_tool()
        content = [
            {
                "type": "tool_use",
                "id": f"mock-{tool_name}",
                "name": tool_name,
                "input": payload_by_tool.get(tool_name, {}),
            }
        ]
        raw = {
            "id": f"mock-{tool_name}",
            "model": model,
            "content": content,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        return self._to_provider_response(raw, latency_ms=0.0, provider_request_id=f"mock-{tool_name}", model=model)

    def _mock_tool_response(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
    ) -> ClaudeProviderResponse:
        has_tool_result = any(_message_has_tool_result(message) for message in messages)
        if has_tool_result:
            raw = {
                "id": "mock-assistant-final",
                "model": model,
                "content": [
                    {
                        "type": "text",
                        "text": "Mock Claude mode used the available assistant tool and returned this final answer.",
                    }
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
            return self._to_provider_response(raw, latency_ms=0.0, provider_request_id="mock-assistant-final", model=model)

        user_text = _last_user_text(messages)
        tool_names = {tool["name"] for tool in tools}
        selected_tool, tool_input = select_mock_tool(user_text=user_text, tool_names=tool_names)

        if selected_tool:
            raw = {
                "id": f"mock-{selected_tool}",
                "model": model,
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"mock-tool-use-{selected_tool}",
                        "name": selected_tool,
                        "input": tool_input,
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        else:
            raw = {
                "id": "mock-assistant-no-tool",
                "model": model,
                "content": [
                    {
                        "type": "text",
                        "text": "Mock Claude mode is enabled, and no assistant tool was needed for this turn.",
                    }
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        return self._to_provider_response(raw, latency_ms=0.0, provider_request_id=raw["id"], model=model)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts = [block.get("text", "") for block in content if block.get("type") == "text"]
                return " ".join(text_parts)
    return ""


def _message_has_tool_result(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(block.get("type") == "tool_result" for block in content)


claude_client = ClaudeClient()
