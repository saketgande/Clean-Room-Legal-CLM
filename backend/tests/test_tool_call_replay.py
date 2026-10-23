"""AGENT-01: a side-effecting Ask Aegis tool call replayed with the same arguments in
the same session returns the first result instead of acting twice."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.ai import tool_runtime as tr
from app.ai.tool_registry import tool_registry
from app.core.enums import AssistantToolCallStatus

# "*" because this test is about replay, not authorization: the empty set it
# used to carry only passed while RBAC was globally disabled, so it would fail
# the tool_runtime permission gate for a reason unrelated to what it asserts.
USER = SimpleNamespace(id="u-1", org_id="org-1", permission_values={"*"})


class QueueDB:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []
        self.queries = 0

    def scalar(self, _stmt):
        self.queries += 1
        return self.results.pop(0) if self.results else None

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def _prior(status, result=None):
    return SimpleNamespace(id="call-1", status=status, result=result)


def _replay(db, tool="complete_task"):
    return tr.tool_runtime._replayed_call(
        db, spec=tool_registry.get(tool), org_id="org-1", session_id="s-1", idempotency_key="k"
    )


def test_replayed_mutation_returns_the_first_result_without_running_again(monkeypatch):
    monkeypatch.setattr(tr, "is_tool_enabled", lambda db, **kw: True)
    ran = []

    async def fake_execute(db, **kw):
        ran.append(kw["tool_name"])
        return {"completed": True}

    monkeypatch.setattr(tr.tool_runtime, "_execute_validated", fake_execute)
    db = QueueDB(_prior(AssistantToolCallStatus.SUCCEEDED, {"completed": True, "task_id": "t-1"}))
    out = asyncio.run(tr.tool_runtime.execute(
        db, tool_name="complete_task", tool_input={"task_id": "t-1"}, user=USER, session_id="s-1"
    ))
    assert out == {"completed": True, "task_id": "t-1"}
    assert ran == []
    assert db.added == []


def test_replay_while_awaiting_confirmation_returns_the_same_confirmation():
    db = QueueDB(_prior(AssistantToolCallStatus.CONFIRMATION_REQUIRED), SimpleNamespace(id="conf-1"))
    assert _replay(db) == {"confirmation_required": True, "tool_call_id": "call-1", "confirmation_id": "conf-1"}


def test_replay_of_a_call_still_running_is_refused():
    with pytest.raises(HTTPException) as exc:
        _replay(QueueDB(_prior(AssistantToolCallStatus.RUNNING)))
    assert exc.value.status_code == 409


def test_read_only_tools_always_run_fresh():
    db = QueueDB(_prior(AssistantToolCallStatus.SUCCEEDED, {"status": "sent"}))
    assert _replay(db, tool="get_signature_status") is None
    assert db.queries == 0
