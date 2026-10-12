"""The Claude client's exam seams (specs/005-ai-eval-baseline, T006): the inert
``exam_meter`` hook, ``complete_json``, the four Message Batches methods and the
ledger's ``eval_run_id``. Every test runs on the fake Anthropic client
(tests/exam_fake_anthropic.py) — nothing here can reach the network or spend money.
"""

from __future__ import annotations

import asyncio

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.ai.controller import ai_controller
from app.ai.gateway import AICallContext, AIGateway
from app.ai.models import AISkillRun
from app.core.config import settings
from app.core.database import new_uuid
from app.core.models import AICallLog
from app.integrations import claude
from app.integrations.claude import ClaudeClient
from tests.exam_fake_anthropic import batch_result, install
from tests.test_ai_gateway import (  # noqa: F401 — the autouse fixture applies here too
    CTX,
    FakeProvider,
    FakeSession,
    _ledger_uses_fake_sessions,
    _response,
)


class Deferred(BaseException):
    """Stands in for the runner's capture-mode signal (a BaseException on purpose)."""


class Meter:
    """A recording exam_meter. ``calls`` is shared with the cost-guard recorder so a
    test can assert the ORDER of before / reserve / settle / after."""

    def __init__(self, calls: list, *, replay=None, before_error: BaseException | None = None) -> None:
        self.calls, self.replay, self.before_error = calls, replay, before_error

    def before(self, payload):
        self.calls.append(("before", payload))
        if self.before_error is not None:
            raise self.before_error
        return self.replay

    def after(self, payload, response):
        self.calls.append(("after", payload, response))


@pytest.fixture
def calls(monkeypatch) -> list:
    """The real request path on the fake client, with the daily-cap calls recorded
    (not sent to Redis) so order and absence can be asserted."""
    log: list = []
    install(monkeypatch)
    monkeypatch.setattr(claude, "reserve_tokens", lambda org_id, estimate: log.append(("reserve",)) or ("key", estimate))
    monkeypatch.setattr(claude, "settle_tokens", lambda reservation, used: log.append(("settle", used)))
    return log


def run(coro):
    return asyncio.run(coro)


def ask_text(**kw):
    return ClaudeClient().complete_text(
        org_id="org-A", system_prompt="sys", user_prompt="hi", max_tokens=100, temperature=0.2, **kw
    )


# --- complete_json ---------------------------------------------------------
def test_complete_json_uses_structured_outputs_and_no_tool_or_temperature(calls):
    """claude-sonnet-5-5 answers 400 to a forced tool_choice and to a non-default
    temperature; a payload that carried either would fail every pre-fill call."""
    fake = claude._anthropic_client()
    schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}
    response = run(ClaudeClient().complete_json(
        org_id="org-A", system_prompt="sys", user_prompt="the contract", json_schema=schema,
        max_tokens=900, model="claude-sonnet-5-5",
    ))
    [payload] = fake.streamed
    assert payload == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 900,
        "system": claude._cached_system("sys"),
        "messages": [{"role": "user", "content": "the contract"}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    assert not {"temperature", "tools", "tool_choice"} & payload.keys()
    assert response.content_blocks[0]["text"] == "{}" and response.stop_reason == "end_turn"
    assert response.model == "claude-sonnet-5-5"
    assert [c[0] for c in calls] == ["reserve", "settle"]  # goes through the cap like every call


def test_complete_json_in_mock_mode_makes_no_request(monkeypatch):
    """Mock mode must answer locally: reaching the client in CI would need a key."""
    monkeypatch.setattr(settings, "mock_claude", True)
    monkeypatch.setattr(claude, "_anthropic_client", lambda: pytest.fail("mock mode reached the client"))
    response = run(ClaudeClient().complete_json(
        org_id=None, system_prompt="s", user_prompt="u", json_schema={}, max_tokens=10, model="claude-sonnet-5-5",
    ))
    assert response.content_blocks == [{"type": "text", "text": "{}"}]
    assert response.stop_reason == "end_turn" and response.model == "claude-sonnet-5-5"


# --- the hook, when it is None (the app) -----------------------------------
def test_with_no_meter_the_request_path_is_exactly_what_it_was(calls):
    """FR-41's safety claim: the app never sets exam_meter, so a structured call must
    send the same payload, reserve once, settle to real usage once, and nothing else."""
    assert claude.exam_meter is None
    fake = claude._anthropic_client()
    schema = {"type": "object"}
    response = run(ClaudeClient().complete_structured(
        org_id="org-A", system_prompt="sys", user_prompt="hi", tool_name="return_clause_labeling",
        input_schema=schema, max_tokens=300, temperature=0.1,
    ))
    [payload] = fake.streamed
    assert payload == {
        "model": settings.claude_model,
        "max_tokens": 300,
        "temperature": 0.1,
        "system": claude._cached_system("sys"),
        "messages": [{"role": "user", "content": "hi"}],
        "tools": claude._cached_tools([{
            "name": "return_clause_labeling",
            "description": "Return structured data for return_clause_labeling.",
            "input_schema": schema,
        }]),
        "tool_choice": {"type": "tool", "name": "return_clause_labeling"},
    }
    assert [c[0] for c in calls] == ["reserve", "settle"] and calls[1] == ("settle", 120)
    assert response.tool_use_blocks[0]["name"] == "return_clause_labeling"
    assert response.provider_request_id == "req_fake"


# --- the hook, when an exam installs one -----------------------------------
def test_a_meter_that_refuses_stops_the_request_before_the_cap_reserves(calls, monkeypatch):
    """The spending ceiling must hold BEFORE anything is sent: a refusal that came
    after the request (or after the reservation) would already have spent the money."""
    fake = claude._anthropic_client()
    monkeypatch.setattr(claude, "exam_meter", Meter(calls, before_error=RuntimeError("ceiling reached")))
    with pytest.raises(RuntimeError, match="ceiling reached"):
        run(ask_text())
    assert fake.streamed == [] and [c[0] for c in calls] == ["before"]


def test_a_replayed_answer_is_returned_with_no_request(calls, monkeypatch):
    """Batch replay hands the stored answer back at the boundary: a request sent
    anyway would pay twice for the same question."""
    fake = claude._anthropic_client()
    stored = ({"content": [{"type": "text", "text": "from the batch"}], "model": "m",
               "usage": {"input_tokens": 7, "output_tokens": 3}, "stop_reason": "end_turn"},
              "msgbatch_x:abc")
    monkeypatch.setattr(claude, "exam_meter", Meter(calls, replay=stored))
    response = run(ask_text())
    assert response.content_blocks[0]["text"] == "from the batch"
    assert response.provider_request_id == "msgbatch_x:abc"
    assert response.token_usage["total_tokens"] == 10
    assert fake.streamed == [] and [c[0] for c in calls] == ["before"]  # no reserve, no settle, no after


def test_after_gets_the_response_once_the_cap_has_settled(calls, monkeypatch):
    """The meter bills what the call really cost, so it must see the real response,
    and after the cap's own settle (the documented order)."""
    monkeypatch.setattr(claude, "exam_meter", Meter(calls))
    run(ask_text())
    assert [c[0] for c in calls] == ["before", "reserve", "settle", "after"]
    _, payload, response = calls[-1]
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert response["usage"] == {"input_tokens": 100, "output_tokens": 20}


def test_after_gets_none_when_the_request_failed(calls, monkeypatch):
    """A failed request costs nothing: the meter must be told it failed (None) so it
    releases its reservation instead of counting spend that never happened."""
    fake = claude._anthropic_client()
    fake.stream_error = RuntimeError("provider down")
    monkeypatch.setattr(claude, "exam_meter", Meter(calls))
    with pytest.raises(RuntimeError, match="provider down"):
        run(ask_text())
    assert [c[0] for c in calls] == ["before", "reserve", "settle", "after"]
    assert calls[-1][2] is None and calls[2] == ("settle", 0)


def test_the_hook_runs_once_per_call_not_once_per_retry(calls, monkeypatch):
    """before() sits outside the retry wrapper: a transient error retried inside would
    otherwise admit (and reserve worst-case spend for) the same call several times."""
    monkeypatch.setattr(settings, "claude_retry_initial_backoff_seconds", 0.0)
    fake = claude._anthropic_client()
    fake.stream_error = anthropic.APIConnectionError(request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(claude, "exam_meter", Meter(calls))
    with pytest.raises(anthropic.APIConnectionError):
        run(ask_text())
    assert len(fake.streamed) == settings.claude_max_retries > 1
    assert [c[0] for c in calls].count("before") == 1 and [c[0] for c in calls].count("after") == 1


# --- Deferred passes through the app's own error handling -------------------
def test_deferred_is_not_recorded_as_a_failed_call_by_the_gateway(calls, monkeypatch):
    """Capture aborts the app operation with a BaseException on purpose: the gateway's
    `except Exception` must not turn it into a FAILED ledger row (it would put a
    failure into the exam's ledger for a call that was only recorded for the batch)."""
    fake = claude._anthropic_client()
    monkeypatch.setattr(claude, "exam_meter", Meter(calls, before_error=Deferred()))
    db = FakeSession()
    with pytest.raises(Deferred):
        AIGateway(provider=ClaudeClient()).structured_sync(
            db, "notice_extraction_agent", ctx=CTX, user_prompt="x", input_schema={"type": "object"}
        )
    assert db.rows == [] and fake.streamed == []


def test_an_ordinary_refusal_is_still_recorded_by_the_gateway(calls, monkeypatch):
    """The contrast that makes the test above meaningful: a normal exception from the
    meter (ceiling reached) IS a failed call and must keep leaving its FAILED row."""
    monkeypatch.setattr(claude, "exam_meter", Meter(calls, before_error=RuntimeError("ceiling reached")))
    db = FakeSession()
    with pytest.raises(RuntimeError, match="ceiling reached"):
        AIGateway(provider=ClaudeClient()).structured_sync(
            db, "notice_extraction_agent", ctx=CTX, user_prompt="x", input_schema={"type": "object"}
        )
    [row] = db.rows
    assert row.status == "failed" and row.error_class == "RuntimeError"


def test_deferred_leaves_a_skill_run_without_fallback_output_or_ledger_row(calls, monkeypatch, db_session):
    """The controller's `except Exception` writes fallback metadata and a FAILED run;
    a capture abort must skip all of that, or the exam DB would hold invented output."""
    org_id = new_uuid()
    monkeypatch.setattr(claude, "exam_meter", Meter(calls, before_error=Deferred()))
    with pytest.raises(Deferred):
        run(ai_controller.run_structured_skill(
            db_session, skill_name="contract_metadata_extraction", org_id=org_id, created_by_user_id=None,
            input_payload={"text": "x"}, commit=False,
        ))
    assert db_session.scalars(select(AICallLog).where(AICallLog.org_id == org_id)).all() == []
    [skill_run] = db_session.scalars(select(AISkillRun).where(AISkillRun.org_id == org_id)).all()
    assert skill_run.status == "running" and skill_run.output_payload is None


# --- streaming --------------------------------------------------------------
def consume_stream(**kw):
    async def go():
        return [e async for e in ClaudeClient().stream_with_tools(
            org_id="org-A", system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=100, temperature=0, **kw,
        )]

    return run(go())


def test_stream_with_tools_meters_like_a_plain_call(calls, monkeypatch):
    """The streaming path is a second paid choke point: it must tell the meter too, or
    a streamed call would slip past the ceiling."""
    monkeypatch.setattr(claude, "exam_meter", Meter(calls))
    events = consume_stream()
    assert events[-1]["type"] == "final"
    assert [c[0] for c in calls] == ["before", "reserve", "settle", "after"]
    assert calls[-1][2]["usage"] == {"input_tokens": 100, "output_tokens": 20}


def test_stream_with_tools_tells_the_meter_when_the_stream_breaks(calls, monkeypatch):
    """A failed stream costs nothing, so the meter gets None, as for non-streaming calls."""
    claude._anthropic_client().stream_error = RuntimeError("cut")
    monkeypatch.setattr(claude, "exam_meter", Meter(calls))
    with pytest.raises(RuntimeError, match="cut"):
        consume_stream()
    assert calls[-1][0] == "after" and calls[-1][2] is None


def test_a_replay_offered_to_stream_with_tools_raises(calls, monkeypatch):
    """The exam never streams. A stored answer offered here would be ignored and the
    request paid for anyway — it must fail loudly, with nothing sent or reserved."""
    fake = claude._anthropic_client()
    monkeypatch.setattr(claude, "exam_meter", Meter(calls, replay=({"content": []}, "msgbatch_x:abc")))
    with pytest.raises(RuntimeError, match="replay"):
        consume_stream()
    assert fake.streamed == [] and [c[0] for c in calls] == ["before"]


# --- Message Batches --------------------------------------------------------
REQUESTS = [
    {"custom_id": "a", "params": {"model": "claude-sonnet-4-6", "max_tokens": 5,
                                  "messages": [{"role": "user", "content": "1"}]}},
    {"custom_id": "b", "params": {"model": "claude-sonnet-4-6", "max_tokens": 5,
                                  "messages": [{"role": "user", "content": "2"}],
                                  "tools": [{"name": "t"}], "tool_choice": {"type": "tool", "name": "t"}}},
]


def test_create_batch_sends_the_requests_as_given(calls):
    """FR-41: the batch must carry the app's exact payloads ("same model and settings")."""
    fake = claude._anthropic_client()
    batch_id = run(ClaudeClient().create_batch(REQUESTS))
    assert fake.batch_creates == [REQUESTS] and fake.batches[batch_id] == REQUESTS


def test_create_batch_is_not_retried(calls, monkeypatch):
    """A lost reply to a create that DID succeed, retried, would make and bill a second
    batch; the transient error must reach the caller after exactly one attempt."""
    monkeypatch.setattr(settings, "claude_retry_initial_backoff_seconds", 0.0)
    fake = claude._anthropic_client()
    fake.create_error = anthropic.APIConnectionError(request=httpx.Request("POST", "http://x"))
    with pytest.raises(anthropic.APIConnectionError):
        run(ClaudeClient().create_batch(REQUESTS))
    assert len(fake.batch_creates) == 1


def test_batch_status_is_retried_on_a_transient_error(calls, monkeypatch):
    """Polling a 24-hour batch must survive one network blip; retrieve is free to repeat."""
    monkeypatch.setattr(settings, "claude_retry_initial_backoff_seconds", 0.0)
    fake = claude._anthropic_client()
    batch_id = run(ClaudeClient().create_batch(REQUESTS))
    fake.retrieve_errors = [anthropic.APIConnectionError(request=httpx.Request("GET", "http://x"))]
    status = run(ClaudeClient().batch_status(batch_id))
    assert status["id"] == batch_id and status["processing_status"] == "ended"
    assert status["request_counts"]["succeeded"] == 2 and fake.retrieves == 2


def test_batch_results_map_every_custom_id_to_its_result(calls):
    """Replay finds each answer by custom_id; an errored or expired request must come
    back as such (never as a missing key) so the runner can mark its item failed."""
    fake = claude._anthropic_client()
    fake.outcomes = {"b": "expired"}
    batch_id = run(ClaudeClient().create_batch(REQUESTS))
    results = run(ClaudeClient().batch_results(batch_id))
    assert set(results) == {"a", "b"}
    assert results["a"]["type"] == "succeeded" and results["a"]["message"]["usage"]["output_tokens"] == 20
    assert results["b"] == {"type": "expired"}
    assert batch_result("errored", error_type="overloaded_error")["error"]["error"]["type"] == "overloaded_error"


def test_cancel_batch_cancels_it(calls):
    """--abandon relies on this actually reaching the provider."""
    fake = claude._anthropic_client()
    batch_id = run(ClaudeClient().create_batch(REQUESTS))
    assert run(ClaudeClient().cancel_batch(batch_id)) is None
    assert fake.cancelled == [batch_id]


@pytest.mark.parametrize("call", [
    lambda c: c.create_batch(REQUESTS),
    lambda c: c.batch_status("msgbatch_x"),
    lambda c: c.batch_results("msgbatch_x"),
    lambda c: c.cancel_batch("msgbatch_x"),
])
def test_every_batch_method_refuses_in_mock_mode(call, monkeypatch):
    """CI and dev mocks must never open a provider batch: all four refuse before the client."""
    monkeypatch.setattr(settings, "mock_claude", True)
    monkeypatch.setattr(claude, "_anthropic_client", lambda: pytest.fail("mock mode reached the client"))
    with pytest.raises(RuntimeError, match="MOCK_CLAUDE"):
        run(call(ClaudeClient()))


def test_batch_methods_need_a_key(monkeypatch):
    """No key must be a clear error, not an SDK auth failure after a half-built request."""
    install(monkeypatch)
    monkeypatch.setattr(settings, "claude_api_key", "")
    with pytest.raises(RuntimeError, match="CLAUDE_API_KEY"):
        run(ClaudeClient().create_batch(REQUESTS))


# --- the ledger -------------------------------------------------------------
def test_record_call_writes_the_exam_run_id_and_defaults_to_none():
    """FR-34: exam rows are told apart from real usage by eval_run_id; app code builds a
    context without it, and its rows must stay NULL or they would vanish from real usage."""
    for eval_run_id in ("run-1", None):
        db = FakeSession()
        ctx = AICallContext(org_id="org-1", eval_run_id=eval_run_id) if eval_run_id else AICallContext(org_id="org-1")
        AIGateway(provider=FakeProvider(_response(tool="suggest_flow", tool_input={}))).structured_sync(
            db, "flow_router", ctx=ctx, user_prompt="x", input_schema={}
        )
        assert db.rows[0].eval_run_id == eval_run_id
    assert AICallContext(org_id="o").eval_run_id is None

