"""A fake Anthropic client for the AI exam's tests (not collected by pytest).

Stands in for ``anthropic.AsyncAnthropic`` at the one place ``claude.py`` reaches
it (``_anthropic_client()``), so a test can run the REAL request path — the
``exam_meter`` hook, cost guard, retries, the four batch methods — without a key,
a network or a cent. ``install()`` is the only way tests should wire it in.

``messages.stream(**payload)`` answers from ``_claude_mock.structured_payload_by_tool()``
by the payload's forced tool name (the same canned outputs mock mode uses), or with
the text ``{}`` for a structured-outputs payload, or a short text otherwise.
``messages.batches`` ends every batch immediately; each request's result is
``default_outcome`` unless ``outcomes[custom_id]`` says otherwise — a kind string
(``succeeded`` / ``errored`` / ``expired`` / ``canceled``) or a full result dict.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.config import settings
from app.integrations import claude
from app.integrations._claude_mock import structured_payload_by_tool

USAGE = {"input_tokens": 100, "output_tokens": 20}


def message_for(payload: dict[str, Any], usage: dict[str, int] | None = None) -> dict[str, Any]:
    """The Message a successful call to ``payload`` would answer with."""
    tool = (payload.get("tool_choice") or {}).get("name")
    if tool:
        content = [{"type": "tool_use", "id": f"toolu_{tool}", "name": tool,
                    "input": structured_payload_by_tool().get(tool, {})}]
        stop = "tool_use"
    else:
        text = "{}" if "output_config" in payload else "A fake answer."
        content, stop = [{"type": "text", "text": text}], "end_turn"
    return {"id": "msg_fake", "type": "message", "role": "assistant", "model": payload["model"],
            "content": content, "stop_reason": stop, "usage": dict(usage or USAGE)}


def batch_result(kind: str, payload: dict[str, Any] | None = None, *, error_type: str = "invalid_request_error",
                 usage: dict[str, int] | None = None) -> dict[str, Any]:
    """One entry of a batch's results, shaped as the API returns it."""
    if kind == "succeeded":
        return {"type": "succeeded", "message": message_for(payload, usage)}
    if kind == "errored":
        return {"type": "errored", "error": {"type": "error", "error": {"type": error_type, "message": "fake"}}}
    return {"type": kind}


class _Stream:
    def __init__(self, fake: FakeAnthropic, payload: dict[str, Any]) -> None:
        self.fake, self.payload, self.request_id = fake, payload, "req_fake"

    async def __aenter__(self):
        self.fake.streamed.append(self.payload)
        if self.fake.stream_error is not None:
            raise self.fake.stream_error
        return self

    async def __aexit__(self, *_exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        message = message_for(self.payload, self.fake.usage)
        return SimpleNamespace(to_dict=lambda: message)


class _Batches:
    def __init__(self, fake: FakeAnthropic) -> None:
        self.fake = fake

    async def create(self, *, requests: list[dict[str, Any]]):
        self.fake.batch_creates.append(requests)
        if self.fake.create_error is not None:
            raise self.fake.create_error
        batch_id = f"msgbatch_fake{len(self.fake.batch_creates)}"
        self.fake.batches[batch_id] = requests
        return SimpleNamespace(id=batch_id)

    async def retrieve(self, batch_id: str):
        self.fake.retrieves += 1
        if self.fake.retrieve_errors:
            raise self.fake.retrieve_errors.pop(0)
        total = len(self.fake.batches[batch_id])
        counts = {"processing": 0, "succeeded": total, "errored": 0, "canceled": 0, "expired": 0}
        body = {"id": batch_id, "type": "message_batch", "processing_status": "ended", "request_counts": counts}
        return SimpleNamespace(to_dict=lambda: body)

    async def results(self, batch_id: str):
        async def items():
            for request in self.fake.batches[batch_id]:
                outcome = self.fake.outcomes.get(request["custom_id"], self.fake.default_outcome)
                result = outcome if isinstance(outcome, dict) else batch_result(outcome, request["params"], usage=self.fake.usage)
                yield SimpleNamespace(custom_id=request["custom_id"], result=SimpleNamespace(to_dict=lambda r=result: r))

        return items()

    async def cancel(self, batch_id: str):
        self.fake.cancelled.append(batch_id)


class FakeAnthropic:
    """Records what it was asked and answers from the helpers above. Set
    ``stream_error`` / ``create_error`` to make that call raise, and
    ``retrieve_errors`` (a list) to make the next retrieves raise in turn."""

    def __init__(self, *, usage: dict[str, int] | None = None, default_outcome: str = "succeeded",
                 outcomes: dict[str, Any] | None = None) -> None:
        self.usage, self.default_outcome, self.outcomes = usage, default_outcome, dict(outcomes or {})
        self.streamed: list[dict[str, Any]] = []          # payloads sent to messages.stream
        self.batch_creates: list[list[dict[str, Any]]] = []  # requests of each batches.create call
        self.batches: dict[str, list[dict[str, Any]]] = {}
        self.cancelled: list[str] = []
        self.retrieves = 0
        self.stream_error: BaseException | None = None
        self.create_error: BaseException | None = None
        self.retrieve_errors: list[BaseException] = []
        self.messages = SimpleNamespace(stream=lambda **payload: _Stream(self, payload), batches=_Batches(self))


def install(monkeypatch, fake: FakeAnthropic | None = None) -> FakeAnthropic:
    """Switch the real request path on with a dummy key and ``fake`` as the client.
    Also pins ``exam_meter`` to None (the app's value) so a meter a test installs
    can never leak into the next test."""
    fake = fake or FakeAnthropic()
    monkeypatch.setattr(settings, "mock_claude", False)
    monkeypatch.setattr(settings, "claude_api_key", "test-key")
    monkeypatch.setattr(claude, "_anthropic_client", lambda: fake)
    monkeypatch.setattr(claude, "exam_meter", None)
    return fake
