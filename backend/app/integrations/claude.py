import asyncio
import logging
import time
import weakref
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings
from app.integrations._claude_mock import select_mock_tool, structured_payload_by_tool
from app.integrations._http_retry import resilient_call


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


# A shared HTTP client PER EVENT LOOP. A single module-level client forfeits
# nothing in the API server (one long-lived loop) but crashes Celery workers:
# run_ai_job uses asyncio.run(), which creates and CLOSES a fresh loop per
# job, so a client created on job A's loop dies with "Event loop is closed"
# when job B reuses its pooled connections. Keying by the running loop keeps
# connection pooling within each loop's lifetime; dead loops' entries are
# garbage-collected via the weak keys.
#
# 120s read timeout: long enough for a large structured/tool completion but
# bounded so a stalled upstream connection can't pin a worker for ten minutes.
_CLAUDE_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)


def _client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=_CLAUDE_TIMEOUT)
        _clients[loop] = client
    return client


async def aclose_claude_client() -> None:
    """Close this loop's shared HTTP client. Called on app shutdown."""
    loop = asyncio.get_running_loop()
    client = _clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()


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

        @resilient_call("claude")
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
            if response.status_code >= 400:
                # Surface Anthropic's error body — the part that actually says
                # WHY (e.g. "messages: roles must alternate", "max_tokens too
                # large", "model not found"). raise_for_status() alone discards
                # it, leaving only an opaque "400 Bad Request for url".
                body = ""
                try:
                    body = response.text[:1500]
                except Exception:  # noqa: BLE001
                    body = "<unreadable body>"
                logger.error(
                    "Anthropic /v1/messages %s (model=%s, max_tokens=%s): %s",
                    response.status_code,
                    json_payload.get("model"),
                    json_payload.get("max_tokens"),
                    body,
                    extra={
                        "error_class": "ClaudeAPIError",
                        "status_code": response.status_code,
                    },
                )
            response.raise_for_status()
            return response.json(), response.headers.get("request-id")

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
