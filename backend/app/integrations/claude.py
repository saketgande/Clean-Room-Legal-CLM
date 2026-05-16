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
            "return_obligation_extraction": {
                "obligations": [],
                "extraction_notes": "Mock Claude mode returned no obligations.",
            },
            "return_renewal_extraction": {"confidence": "low", "needs_review": True},
            "return_tabular_cell_extraction": {
                "answer": "",
                "not_found": True,
                "confidence": "low",
                "citations": [],
            },
            "return_tabular_review_chat": {
                "answer": "Mock Claude mode is enabled, so no live tabular-review answer was generated.",
                "citations": [],
            },
            "return_contract_brain_query_parse": {
                "query_scope": "portfolio",
                "target_clause_types": [],
                "needs_vector_search": True,
                "needs_graph_search": True,
                "needs_full_text_search": True,
            },
            "return_contract_brain_answer": {
                "answer": "Mock Claude mode is enabled, so no live Contract Brain answer was generated.",
                "citations": [],
                "confidence": "low",
                "limitations": "Mock mode: no model reasoning performed.",
            },
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
            "return_playbook_review": {
                "deviations": [],
                "summary": "Mock Claude mode returned no playbook deviations.",
                "citations": [],
            },
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

        user_text = _last_user_text(messages).lower()
        tool_names = {tool["name"] for tool in tools}
        selected_tool = None
        tool_input: dict[str, Any] = {}
        if "find" in user_text and "find_in_contract" in tool_names:
            selected_tool = "find_in_contract"
            tool_input = {"contract_handle": "contract-0", "query": _mock_query(user_text)}
        elif "playbook" in user_text and "redline" in user_text and "redline_against_playbook" in tool_names:
            selected_tool = "redline_against_playbook"
            tool_input = {"contract_handle": "contract-0", "playbook_id": "mock-playbook-id"}
        elif "playbook" in user_text and "run_playbook_review" in tool_names:
            selected_tool = "run_playbook_review"
            tool_input = {"contract_handle": "contract-0", "playbook_id": "mock-playbook-id"}
        elif ("edit" in user_text or "redline" in user_text) and "edit_contract" in tool_names:
            selected_tool = "edit_contract"
            tool_input = {"contract_handle": "contract-0", "instructions": "Apply the requested contract edit."}
        elif ("generate" in user_text or "draft" in user_text) and "generate_contract_docx" in tool_names:
            selected_tool = "generate_contract_docx"
            tool_input = {
                "title": "Generated Contract",
                "instructions": "Generate a contract draft from the user request.",
            }
        elif "replicate" in user_text and "replicate_contract_version" in tool_names:
            selected_tool = "replicate_contract_version"
            tool_input = {"contract_handle": "contract-0"}
        elif "status" in user_text and "get_contract_status" in tool_names:
            selected_tool = "get_contract_status"
            tool_input = {"contract_handle": "contract-0"}
        elif ("workflow" in user_text or "workflows" in user_text) and "list_workflows" in tool_names:
            selected_tool = "list_workflows"
            tool_input = {}
        elif ("project" in user_text or "contracts" in user_text) and "list_project_contracts" in tool_names:
            selected_tool = "list_project_contracts"
            tool_input = {"project_id": "mock-project-id"}
        elif ("contract" in user_text or "summar" in user_text or "read" in user_text) and "read_contract" in tool_names:
            selected_tool = "read_contract"
            tool_input = {"contract_handle": "contract-0"}

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


def _mock_query(user_text: str) -> str:
    quoted = user_text.split('"')
    if len(quoted) >= 3 and quoted[1].strip():
        return quoted[1].strip()
    words = [word.strip(".,?!:;") for word in user_text.split()]
    ignored = {"find", "in", "the", "contract", "for", "show", "me", "search"}
    for word in reversed(words):
        if word and word not in ignored:
            return word
    return "agreement"


claude_client = ClaudeClient()
