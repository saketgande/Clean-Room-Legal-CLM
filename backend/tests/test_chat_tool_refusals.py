"""A rule saying "no" to a chat action is shown as "not done", not as a failure.

Asking the chat to move a contract to the stage it is already in is refused by a rule ("already in
Signature"). The chat step turned red and read "Moving the contract didn't work" above an answer that
calmly explained why. A refusal is now marked as one, so the page can show it as "not done" and keep
the red for tools that really broke.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.ai.controller import _is_refusal, _safe_tool_error
from app.assistant.runner import accumulate_block


class _Probe(BaseModel):
    n: int


def _validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _Probe(n="not a number")
    return caught.value


@pytest.mark.parametrize("code", [403, 404, 409, 422])
def test_a_rule_refusing_with_a_message_is_a_refusal(code):
    exc = HTTPException(code, "“MSA” is already in Signature.")
    assert _is_refusal(exc) is True
    assert _safe_tool_error(exc) == "“MSA” is already in Signature."


def test_a_server_error_is_not_a_refusal():
    assert _is_refusal(HTTPException(500, "database unavailable")) is False


def test_a_tool_that_broke_is_not_a_refusal():
    assert _is_refusal(RuntimeError("boom")) is False
    assert _is_refusal(_validation_error()) is False


def test_a_refusal_without_a_readable_message_is_not_treated_as_one():
    assert _is_refusal(HTTPException(409, {"code": "x"})) is False


def _steps(finished_payload):
    blocks: list[dict] = []
    accumulate_block(blocks, {"event": "tool_started", "payload": {"tool_name": "advance_contract_stage"}})
    accumulate_block(blocks, {"event": "tool_finished", "payload": finished_payload})
    return blocks


def test_the_saved_step_of_a_refused_tool_says_refused():
    assert _steps({"error": "already in Signature", "refused": True})[0]["status"] == "refused"


def test_the_saved_step_of_a_tool_that_broke_still_says_error():
    assert _steps({"error": "Tool execution failed", "refused": False})[0]["status"] == "error"
    assert _steps({"error": "Tool execution failed"})[0]["status"] == "error"


def test_the_saved_step_of_a_tool_that_worked_says_done():
    assert _steps({"result": {"ok": True}})[0]["status"] == "done"
