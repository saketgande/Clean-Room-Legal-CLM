import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings


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
        async with httpx.AsyncClient(timeout=120) as client:
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
        payload_by_tool = {
            "return_contract_metadata_extraction": {
                "title": None,
                "contract_type": None,
                "counterparty_name": None,
                "jurisdiction": None,
                "risk_level": None,
                "value_amount": None,
                "currency": None,
                "effective_date": None,
                "expiration_date": None,
                "confidence": "low",
                "citations": [],
                "notes": "Mock Claude mode returned no extracted metadata.",
            },
            "return_clause_extraction": {"clauses": [], "extraction_notes": "Mock Claude mode returned no clauses."},
            "return_assistant_streaming": {
                "answer": "Mock Claude mode is enabled, so no live legal answer was generated.",
                "citations": [],
                "tool_results": [],
            },
            "return_contract_docx_generation": {
                "title": "Generated Contract",
                "sections": [{"heading": "Overview", "body": "Draft content placeholder generated in mock Claude mode."}],
                "assumptions": ["Mock Claude mode is enabled."],
                "citations": [],
            },
            "return_contract_edit_suggestions": {"edits": [], "summary": "Mock Claude mode returned no edits."},
        }
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


claude_client = ClaudeClient()
