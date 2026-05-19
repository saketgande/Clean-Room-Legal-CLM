import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.integrations._claude_mock import select_mock_tool, structured_payload_by_tool


logger = logging.getLogger(__name__)


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


# A long-lived shared HTTP client. The previous implementation constructed a
# new ``httpx.AsyncClient`` per call, which forfeited connection pooling and
# HTTP/2 reuse. ``httpx.AsyncClient`` is safe to share across asyncio tasks.
_CLAUDE_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
_shared_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=_CLAUDE_TIMEOUT)
    return _shared_client


async def aclose_claude_client() -> None:
    """Close the shared HTTP client. Called on app shutdown."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


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
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
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
                "system": system_prompt,
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
                "system": system_prompt,
                "messages": messages,
                "tools": tools,
                "tool_choice": {"type": "auto"},
            }
        )
        return self._to_provider_response(
            response_json,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider_request_id=request_id,
            model=model or settings.claude_model,
        )

    async def stream_with_tools(self, **kwargs) -> AsyncIterator[dict[str, Any]]:
        # The controller owns the durable stream loop. This provider exposes a small event
        # surface now and can later be swapped for Anthropic's native streaming endpoint.
        response = await self.complete_text(**kwargs)
        for block in response.content_blocks:
            if block.get("type") == "text":
                yield {"event": "message_delta", "text": block.get("text", "")}
        yield {"event": "done", "stop_reason": response.stop_reason}

    async def _post_messages(self, *, json_payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        if not settings.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY is required when mock Claude mode is disabled")
        client = _client()

        async def _do_request() -> tuple[dict[str, Any], str | None]:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.claude_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=json_payload,
            )
            response.raise_for_status()
            return response.json(), response.headers.get("request-id")

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max(1, settings.claude_max_retries)),
                wait=wait_exponential(
                    multiplier=settings.claude_retry_initial_backoff_seconds,
                    max=settings.claude_retry_max_backoff_seconds,
                ),
                retry=retry_if_exception(_should_retry),
                reraise=True,
            ):
                with attempt:
                    return await _do_request()
        except RetryError as exc:  # pragma: no cover - reraise=True covers normal path
            raise exc.last_attempt.exception() from exc

        # Unreachable; AsyncRetrying always returns at least one attempt.
        raise RuntimeError("Claude retry loop terminated without a response")

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
