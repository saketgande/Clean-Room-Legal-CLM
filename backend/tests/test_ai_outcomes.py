"""Honest model failures: a failed call or job says which way it failed, in a plain sentence, and never
shows the raw error text (a failed insert prints the contract's own words as its parameters). Covers the
outcome rules, the gateway's checks, the job and ledger records, and the HTTP answer."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import anthropic
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import (
    DataError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.context import text_only_context
from app.ai.controller import AIController
from app.ai.gateway import AICallContext, AIGateway, AIOutputInvalid
from app.ai.models import AISkillRun
from app.ai.outcomes import (
    MODEL_OUTCOMES,
    Outcome,
    PlainError,
    SetupError,
    classify,
    failure_from_exception,
    failure_payload,
    ledger_text,
)
from app.core.database import engine, utcnow
from app.core.enums import JobStatus
from app.core.exceptions import register_exception_handlers
from app.core.models import AICallLog
from app.integrations._http_retry import ProviderUnavailableError
from app.jobs import tasks
from app.jobs.models import JobRun
from app.organizations.models import Organization
from tests.test_ai_gateway import (  # noqa: F401 - the autouse fixture applies here too
    FakeProvider,
    FakeSession,
    _ledger_uses_fake_sessions,
    _response,
)

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
SECRET = "Supplier shall pay INR 5,000,000 to Acme within 3 days"  # a contract's own words


def _status_error(code: int, *, headers: dict | None = None, body=None) -> anthropic.APIStatusError:
    return anthropic.APIStatusError(
        f"error {code}: {SECRET}", response=httpx.Response(code, request=REQUEST, headers=headers or {}), body=body)


# --- the rules --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("exc, outcome", [
    (_status_error(429), Outcome.TRANSIENT),
    (_status_error(529), Outcome.TRANSIENT),
    (_status_error(500), Outcome.TRANSIENT),
    (_status_error(400), Outcome.PERMANENT),
    (_status_error(401), Outcome.PERMANENT),
    (_status_error(413), Outcome.PERMANENT),
    (_status_error(200, body={"type": "error", "error": {"type": "overloaded_error"}}), Outcome.TRANSIENT),
    (_status_error(200, body={"type": "error", "error": {"type": "invalid_request_error"}}), Outcome.PERMANENT),
    (_status_error(200, body={"type": "error", "error": {"type": "billing_error"}}), Outcome.PERMANENT),
    (_status_error(200, body={"type": "error", "error": {"type": "timeout_error"}}), Outcome.TRANSIENT),  # a 504
    (_status_error(200, body="not json at all"), Outcome.TRANSIENT),  # a garbled stream is a bad moment
    (anthropic.APIConnectionError(request=REQUEST), Outcome.TRANSIENT),
    (anthropic.APITimeoutError(request=REQUEST), Outcome.TRANSIENT),
    (httpx.ConnectError("down"), Outcome.TRANSIENT),
    (ProviderUnavailableError("claude", retry_after=30), Outcome.TRANSIENT),
    (HTTPException(429, "Daily AI token cap reached"), Outcome.TRANSIENT),
    (HTTPException(409, "Contract has no extractable authoritative version"), Outcome.INTERNAL),
    (DataError("INSERT ...", {"description": SECRET}, Exception("value too long")), Outcome.SAVE_FAILED),
    (IntegrityError("INSERT ...", {"description": SECRET}, Exception("duplicate key")), Outcome.SAVE_FAILED),
    # Not a failed save: these can happen before anything finished (a read, a deploy without its migration).
    (OperationalError("SELECT ...", {}, Exception("server closed the connection")), Outcome.INTERNAL),
    (ProgrammingError("SELECT ...", {}, Exception("column does not exist")), Outcome.INTERNAL),
    # The message disagrees with the type: classification never reads it.
    (RuntimeError("HTTP 529 overloaded_error: rate limit 429"), Outcome.INTERNAL),
    (anthropic.APIStatusError("overloaded, rate limit, 529", response=httpx.Response(400, request=REQUEST),
                              body=None), Outcome.PERMANENT),
    (PlainError("rate limit 429, Section 429 applies"), Outcome.INTERNAL),
    (PlainError("The new reading found 1 obligations where 9 exist, so the existing ones were kept."),
     Outcome.INTERNAL),
    (SQLAlchemyError("a coding mistake, not a refused write"), Outcome.INTERNAL),
    (SetupError("The AI skill \"x\" is switched off in settings."), Outcome.INTERNAL),
    (RuntimeError("boom"), Outcome.INTERNAL),
])
def test_a_failure_is_classed_by_what_it_is_not_by_its_message(exc, outcome):
    """Guards the rule that decides who may retry and how it reads: the provider's busy and rate-limit answers
    are transient (a rate limit or an overload inside a stream that had already started included), a rejected
    request is permanent, the app's own refusals keep their plain sentence."""
    assert classify(exc)[0] is outcome


def test_the_gateways_own_errors_carry_their_outcome():
    """Guards a cut-off or refused answer being classed as an unknown error once it reaches the job."""
    for outcome in (Outcome.TRUNCATED, Outcome.REFUSED, Outcome.UNUSABLE):
        exc = AIOutputInvalid("clause_extraction: x", feature_key="clause_extraction", call_id="call-1", outcome=outcome)
        assert classify(exc)[0] is outcome and failure_from_exception(exc)["call_id"] == "call-1"

    class Reply(BaseModel):
        n: int

    # A schema error raised by our own code (not the model's answer, which the gateway classes) is a bug of ours.
    with pytest.raises(ValidationError) as invalid:
        Reply.model_validate({"n": SECRET})
    assert classify(invalid.value)[0] is Outcome.INTERNAL


def test_the_reason_is_a_fixed_sentence_and_never_the_raw_error_text():
    """Guards the leak: a failed insert printed the contract's words, and they reached the Jobs page, the
    timeline and the skill run. Only the app's own plain messages are passed through."""
    for exc in (_status_error(400), DataError("INSERT ...", {"description": SECRET}, Exception(SECRET)),
                RuntimeError(SECRET)):
        assert SECRET not in failure_from_exception(exc)["reason"]
    assert failure_from_exception(PlainError("kept the existing ones"))["reason"] == "kept the existing ones"
    assert "HTTP 400" in failure_from_exception(_status_error(400))["reason"]
    assert "budget" in failure_from_exception(HTTPException(429, "Daily AI token cap reached"))["reason"]


def _status_error_from_other_service(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.reducto.example/parse")
    return httpx.HTTPStatusError(f"{code}: {SECRET}", request=request, response=httpx.Response(code, request=request))


def test_only_a_failure_of_the_ai_provider_is_called_an_ai_failure():
    """Guards a failed OCR, e-signature or email call telling an administrator the AI service was down: the Jobs
    page would send them to the wrong place. Other services read as 'a service this step depends on'."""
    ai = [_status_error(400), _status_error(529), anthropic.APIConnectionError(request=REQUEST),
          ProviderUnavailableError("claude", retry_after=30), AIOutputInvalid("x", feature_key="f", call_id=None)]
    other = [_status_error_from_other_service(403), _status_error_from_other_service(503), httpx.ConnectError("down"),
             httpx.ReadTimeout("slow"), ProviderUnavailableError("resend", retry_after=30)]
    for exc in ai:
        assert "AI" in failure_from_exception(exc)["reason"], exc
    for exc in other:
        assert "AI" not in failure_from_exception(exc)["reason"], exc
    assert "HTTP 403" in failure_from_exception(_status_error_from_other_service(403))["reason"]
    assert failure_from_exception(_status_error_from_other_service(503))["outcome"] == "transient"
    assert SECRET not in failure_from_exception(_status_error_from_other_service(403))["reason"]


def test_an_error_inside_a_stream_does_not_claim_an_http_status():
    """Guards 'rejected the request (HTTP 200)': the provider's error event arrives in a 200 response, so there is
    no HTTP status to report, in the sentence, the payload or the ledger line."""
    body = {"type": "error", "error": {"type": "invalid_request_error"}}
    failure = failure_from_exception(_status_error(200, body=body))
    assert failure["outcome"] == "permanent" and failure["status_code"] is None
    assert "HTTP" not in failure["reason"] and "HTTP" not in ledger_text(failure)


def test_a_circuit_that_is_open_says_when_to_come_back():
    """Guards the provider's own wait being lost when the breaker (not the provider) refused the call."""
    assert failure_from_exception(ProviderUnavailableError("claude", retry_after=30))["retry_after_s"] == 30


def test_a_setup_problem_cannot_be_fixed_by_pressing_retry():
    """Guards a switched-off skill or a missing key offering Retry: it fails the same way until someone changes
    settings. A guard that merely refused (PlainError) can still be retried."""
    off = failure_from_exception(SetupError("The AI skill \"x\" is switched off in settings."))
    assert off["retryable"] is False and off["reason"] == "The AI skill \"x\" is switched off in settings."
    assert failure_from_exception(PlainError("kept the existing ones"))["retryable"] is True


def test_the_two_places_that_refuse_to_call_the_model_say_it_is_a_setup_problem(db, org, monkeypatch):
    """Guards 'AI skill is disabled' and 'CLAUDE_API_KEY is required' reaching the Jobs page as the generic
    'Something went wrong inside Aegis' with a Retry button."""
    from app.ai.registry import skill_registry
    from app.core.config import settings
    from app.core.models import AdminSetting
    from app.integrations.claude import _require_api_key

    spec = skill_registry.get("contract_metadata_extraction")
    db.add(AdminSetting(org_id=org.id, key=spec.feature_flag, value=False))
    db.flush()
    with pytest.raises(SetupError, match="switched off"):
        AIController()._ensure_skill_enabled(db, org_id=org.id, spec=spec)

    monkeypatch.setattr(settings, "claude_api_key", "")
    with pytest.raises(SetupError, match="CLAUDE_API_KEY"):
        _require_api_key()
    assert failure_from_exception(SetupError("x"))["retryable"] is False


def test_who_may_retry_and_what_a_machine_may_do_are_kept_apart():
    """Guards a cut-off or refused answer being offered for a pointless retry, and a retry flag standing in for
    permission to re-pay automatically: auto_retry is true only for a provider that was merely busy."""
    shape = {o: failure_payload(o) for o in Outcome if o is not Outcome.OK}
    assert not shape[Outcome.TRUNCATED]["retryable"] and not shape[Outcome.REFUSED]["retryable"]
    assert not shape[Outcome.SAVE_FAILED]["retryable"], "the same result is refused the same way, and a retry re-pays"
    assert shape[Outcome.UNUSABLE]["retryable"] and shape[Outcome.TRANSIENT]["retryable"]
    assert [o for o, f in shape.items() if f["auto_retry"]] == [Outcome.TRANSIENT]
    assert set(shape) >= MODEL_OUTCOMES


def test_the_retry_after_the_provider_sent_is_kept():
    """Guards a rate limit's own wait being thrown away (kept for the retry policy that arrives in step 8)."""
    assert failure_from_exception(_status_error(429, headers={"retry-after": "30"}))["retry_after_s"] == 30
    assert failure_from_exception(_status_error(429))["retry_after_s"] is None


def test_the_ledger_line_names_the_outcome_status_and_request_id():
    """Guards a failed row keeping only a class name: support asks for the status and the request id."""
    failure = failure_from_exception(_status_error(529, headers={"request-id": "req_abc"}))
    assert failure["status_code"] == 529 and failure["request_id"] == "req_abc"
    assert ledger_text(failure).startswith("transient: ") and "HTTP 529, request req_abc" in ledger_text(failure)


# --- the gateway ------------------------------------------------------------------------------------------------


def _clause_reply(stop: str | None, tool_input: dict):
    from app.ai.registry import skill_registry

    reply = _response(tool=skill_registry.get("clause_extraction").return_tool_name, tool_input=tool_input, stop="x")
    return type(reply)(**{**reply.__dict__, "stop_reason": stop})


def _structured(reply, db=None):
    db = db or FakeSession()
    gateway = AIGateway(provider=FakeProvider(reply))
    return db, lambda: gateway.structured_sync(db, "clause_extraction", ctx=AICallContext(org_id="org-1"),
                                               user_prompt="contract")


@pytest.mark.parametrize("stop, outcome, text", [
    ("max_tokens", Outcome.TRUNCATED, "cut off at max_tokens"),
    (None, Outcome.UNUSABLE, "without a stop signal"),
    ("refusal", Outcome.REFUSED, "declined"),
])
def test_the_gateway_names_how_an_answer_failed_and_the_ledger_keeps_it(stop, outcome, text):
    """Guards a stream cut mid-tool-call passing as an answer (the SDK hands back half a call as a valid-looking
    dict with no stop reason), a refusal reading as 'no tool output', and a cut-off one as a generic error."""
    db, call = _structured(_clause_reply(stop, {"clauses": []}))
    with pytest.raises(AIOutputInvalid, match=text) as err:
        call()
    assert err.value.outcome is outcome
    [row] = db.rows
    assert row.status == "validation_failed" and row.validation_error.startswith(f"{outcome.value}: ")


def test_an_ordinary_answer_still_comes_through():
    """Guards the new stop checks refusing the stop reasons real answers end with."""
    for stop in ("tool_use", "end_turn"):
        _, call = _structured(_clause_reply(stop, {"clauses": []}))
        assert call().data.model_dump()["clauses"] == []


def test_a_provider_failure_leaves_a_ledger_row_with_its_status_and_request_id():
    """Guards the failed row keeping only 'RateLimitError': the reason, the HTTP status and the provider's
    request id are what an operator needs."""
    error = _status_error(529, headers={"request-id": "req_xyz"})

    class Down(FakeProvider):
        async def complete_structured(self, **_kw):
            raise error

    db = FakeSession()
    with pytest.raises(anthropic.APIStatusError):
        AIGateway(provider=Down(None)).structured_sync(
            db, "clause_extraction", ctx=AICallContext(org_id="org-1"), user_prompt="contract")
    [row] = db.rows
    assert row.status == "failed" and row.provider_request_id == "req_xyz"
    assert row.validation_error.startswith("transient: ") and "HTTP 529" in row.validation_error


# --- the Claude client ------------------------------------------------------------------------------------------


def _anthropic_whose_stream_dies(error: Exception, requests: list) -> anthropic.AsyncAnthropic:
    """A real SDK client talking to a server that sends the first stream event and then dies mid-body: the failure
    the SDK does NOT wrap (it hands back a bare httpx error). No network."""
    first_event = (b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message",'
                   b'"role":"assistant","model":"m","content":[],"stop_reason":null,"stop_sequence":null,'
                   b'"usage":{"input_tokens":1,"output_tokens":1}}}\n\n')

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield first_event
            raise error

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())

    return anthropic.AsyncAnthropic(api_key="k", max_retries=0,
                                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
def dying_stream(monkeypatch):
    from app.core.config import settings
    from app.integrations import claude

    requests: list = []

    def install(error: Exception, *, attempts: int = 1):
        monkeypatch.setattr(settings, "mock_claude", False)
        monkeypatch.setattr(settings, "claude_api_key", "k")
        monkeypatch.setattr(settings, "claude_retry_inline_tries", attempts)
        monkeypatch.setattr(settings, "claude_retry_base_wait_seconds", 0.0)
        monkeypatch.setattr(claude, "exam_meter", None)
        monkeypatch.setattr(claude, "reserve_tokens", lambda *_a: None)
        monkeypatch.setattr(claude, "settle_tokens", lambda *_a: None)
        monkeypatch.setattr(claude, "_anthropic_client", lambda: _anthropic_whose_stream_dies(error, requests))
        return requests

    return install


def _call_the_client(path: str):
    from app.integrations.claude import ClaudeClient

    messages = [{"role": "user", "content": "hi"}]

    async def streamed():
        async for _event in ClaudeClient().stream_with_tools(
                org_id=None, system_prompt="s", messages=messages, tools=[], max_tokens=10, temperature=0):
            pass

    async def posted():
        await ClaudeClient()._post_messages(org_id=None, json_payload={"model": "m", "max_tokens": 10,
                                                                        "messages": messages})

    asyncio.run({"stream": streamed, "post": posted}[path]())


@pytest.mark.parametrize("path", ["stream", "post"])
@pytest.mark.parametrize("error, wrapped", [
    (httpx.ReadTimeout("slow"), anthropic.APITimeoutError),
    (httpx.RemoteProtocolError("peer closed connection"), anthropic.APIConnectionError),
])
def test_a_stream_that_dies_mid_answer_is_an_ai_failure(dying_stream, path, error, wrapped):
    """Guards the most common transient AI failure (the 120 s read timeout, a dropped connection) being blamed on
    'a service this step depends on': the SDK hands those back as bare httpx errors, which look like OCR's or
    e-signature's. The client wraps them where it knows the call went to Anthropic."""
    dying_stream(error)
    with pytest.raises(wrapped) as err:
        _call_the_client(path)
    failure = failure_from_exception(err.value)
    assert failure["outcome"] == "transient" and "AI service" in failure["reason"]


def test_the_wrapped_stream_error_is_still_retried(dying_stream):
    """Guards the wrapping changing the retry: a stalled stream was retried before and must still be, once."""
    requests = dying_stream(httpx.ReadTimeout("slow"), attempts=2)
    with pytest.raises(anthropic.APITimeoutError):
        _call_the_client("post")
    assert len(requests) == 2


def test_a_missing_key_is_a_setup_problem_at_every_place_the_client_calls_out(monkeypatch):
    """Guards one call site going back to a bare RuntimeError: a missing key would reach the Jobs page as the
    generic 'Something went wrong' with a Retry button that cannot help."""
    from app.core.config import settings
    from app.integrations.claude import ClaudeClient

    monkeypatch.setattr(settings, "mock_claude", False)
    monkeypatch.setattr(settings, "claude_api_key", "")
    for path in ("stream", "post"):
        with pytest.raises(SetupError, match="CLAUDE_API_KEY"):
            _call_the_client(path)
    with pytest.raises(SetupError, match="CLAUDE_API_KEY"):
        asyncio.run(ClaudeClient().create_batch([]))


def test_the_metadata_reading_has_room_for_a_long_contracts_list():
    """Guards the output limit going back to 2048, where 11 metadata readings in the ledger were cut off."""
    from app.ai.registry import skill_registry

    assert skill_registry.get("contract_metadata_extraction").max_tokens >= 4096


# --- a job through the controller -------------------------------------------------------------------------------


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def org(db):
    row = Organization(name="Outcomes org", slug=f"oc-{uuid.uuid4().hex[:8]}")
    db.add(row)
    db.flush()
    return row


def _job(db: Session, org: Organization, **fields) -> JobRun:
    job = JobRun(org_id=org.id, job_type="metadata_extraction", resource_type="contract",
                 resource_id=str(uuid.uuid4()), **{"status": JobStatus.RUNNING, **fields})
    db.add(job)
    db.flush()
    return job


def test_a_failed_metadata_reading_is_a_failed_job_with_a_plain_reason_and_no_guess(db, org):
    """Guards the regex guess ('first line is the title, first date is the start date') being saved as a
    successful job when the model fails: it could dispute a correct start date from the request form. A failed
    reading now fails visibly, with why."""
    class Down:
        async def complete_structured(self, **_kw):
            raise _status_error(529, headers={"request-id": "req_job"})

    job = _job(db, org)
    with pytest.raises(anthropic.APIStatusError):
        asyncio.run(AIController(claude_client=Down()).run_structured_skill(
            db, skill_name="contract_metadata_extraction", org_id=org.id, created_by_user_id=None,
            input_payload={"text": f"The contract says: {SECRET}"}, job_id=job.id, commit=False,
            # The old fallback only fired when the run had a contract's text to guess from, which every real job has.
            contract_context=text_only_context(text=f"Services Agreement. {SECRET}", title="Services Agreement",
                                               contract_type="MSA")))

    run = db.scalars(select(AISkillRun).where(AISkillRun.org_id == org.id)).one()
    assert run.status == "failed" and run.output_payload is None and SECRET not in (run.error_message or "")
    assert job.status == JobStatus.FAILED and SECRET not in job.error_message
    failure = job.metadata_json["failure"]
    assert (failure["outcome"], failure["status_code"], failure["request_id"]) == ("transient", 529, "req_job")
    assert failure["retryable"] is True and failure["reason"] == job.error_message
    [call] = db.scalars(select(AICallLog).where(AICallLog.org_id == org.id)).all()
    assert call.status == "failed" and call.validation_error.startswith("transient: ")


def test_a_provider_failure_keeps_its_ledger_row_when_the_run_owns_the_commit(db, org):
    """Guards the failure branch rolling the session back and, with it, the failed call's own ledger row: the
    record of why the call failed. A rollback is only for a write that failed and left the session unusable."""
    class Down:
        async def complete_structured(self, **_kw):
            raise _status_error(529)

    job = _job(db, org)
    with pytest.raises(anthropic.APIStatusError):
        asyncio.run(AIController(claude_client=Down()).run_structured_skill(
            db, skill_name="contract_metadata_extraction", org_id=org.id, created_by_user_id=None,
            input_payload={"text": "x"}, job_id=job.id, commit=True))

    assert db.scalars(select(AICallLog).where(AICallLog.org_id == org.id)).one().status == "failed"
    assert db.get(JobRun, job.id).metadata_json["failure"]["outcome"] == "transient"


def test_the_run_rolls_back_for_a_refused_database_write_and_only_then(db, org, monkeypatch):
    """Guards both directions: a refused write leaves the session unusable until it is rolled back (the old bug:
    the FAILED marks raised and the run stayed 'running' for ever, and `db.is_active` stays True for a statement
    the server refused, so it cannot be the test), while a provider error must NOT roll back, which would discard
    the failed call's own ledger row."""
    rollbacks = []
    monkeypatch.setattr(db, "rollback", lambda: rollbacks.append(1))  # counted, not done: the test owns the transaction

    def run(error):
        class Down:
            async def complete_structured(self, **_kw):
                raise error

        job = _job(db, org)
        with pytest.raises(type(error)):
            asyncio.run(AIController(claude_client=Down()).run_structured_skill(
                db, skill_name="contract_metadata_extraction", org_id=org.id, created_by_user_id=None,
                input_payload={"text": "x"}, job_id=job.id, commit=True))
        return job

    run(_status_error(529))
    assert rollbacks == []
    job = run(DataError("INSERT ...", {"description": SECRET}, Exception("value too long")))
    assert rollbacks == [1]
    assert job.metadata_json["failure"]["outcome"] == "save_failed" and SECRET not in job.error_message


def test_a_cost_cap_refusal_is_recorded_on_the_skill_run_in_plain_words(db, org, monkeypatch):
    """Guards the daily-cap refusal marking the skill run failed with no reason at all."""
    class Capped:
        async def complete_structured(self, **_kw):
            raise HTTPException(429, "Daily AI token cap reached")

    with pytest.raises(HTTPException):
        asyncio.run(AIController(claude_client=Capped()).run_structured_skill(
            db, skill_name="contract_metadata_extraction", org_id=org.id, created_by_user_id=None,
            input_payload={"text": "x"}, commit=False))

    run = db.scalars(select(AISkillRun).where(AISkillRun.org_id == org.id)).one()
    assert run.status == "failed" and "budget" in run.error_message


# --- the other writers -------------------------------------------------------------------------------------------


def test_a_job_that_succeeds_on_a_retry_keeps_no_old_failure(db, org):
    """Guards the Jobs page showing a green job that still carries last night's failure."""
    job = _job(db, org, metadata_json={"keep": 1, "failure": failure_payload(Outcome.TRANSIENT)})

    tasks._mark_job_succeeded(job)

    assert job.status == JobStatus.SUCCEEDED and job.metadata_json == {"keep": 1}


def test_a_job_queued_again_by_hand_starts_clean(db, org, monkeypatch):
    """Guards 'Retry' on a failed job leaving the old reason and the dead-letter mark beside the new attempt: the
    Jobs page would show a queued job that still reads 'failed and out of retries'."""
    from app.jobs.service import JobsService

    monkeypatch.setattr(tasks.run_ai_job, "delay", lambda _id: type("T", (), {"id": "task-1"})())
    job = _job(db, org, status=JobStatus.FAILED, error_message="old", metadata_json={
        "keep": 1, "failure": failure_payload(Outcome.TRANSIENT), tasks.DEAD_LETTER_META_KEY: {"reason": "x"}})

    JobsService(db).dispatch_job(job=job)

    assert job.status == JobStatus.QUEUED and job.error_message is None and job.metadata_json == {"keep": 1}


def test_the_other_job_writers_clear_an_old_failure_on_success_and_state_a_new_one(db, org):
    """Guards the two long-running job types (issue-fix drafting, template checks) ending 'failed' with no outcome
    for the audit to read, or 'succeeded' still carrying an earlier attempt's failure."""
    from app.contract_files.issue_fixes import _finish
    from app.drafting_templates.checks import _end_job

    stale = {"keep": 1, "failure": failure_payload(Outcome.TRANSIENT)}
    for finish in (lambda j: _finish(db, j, JobStatus.SUCCEEDED), lambda j: _end_job(j, ok=True)):
        job = _job(db, org, metadata_json=dict(stale))
        finish(job)
        assert job.status == JobStatus.SUCCEEDED and job.metadata_json == {"keep": 1}

    job = _job(db, org, metadata_json={"keep": 1})
    _finish(db, job, JobStatus.FAILED, error="The person who started this may not redline this contract.")
    assert job.metadata_json["failure"]["reason"] == job.error_message
    assert job.metadata_json["failure"]["retryable"] is False and job.metadata_json["keep"] == 1

    job = _job(db, org, metadata_json={})
    _end_job(job, ok=False, error="The AI review was not available.")
    assert job.metadata_json["failure"]["outcome"] == "internal" and job.metadata_json["failure"]["reason"] == job.error_message


class _FakeDb:
    """Just enough session for the job writers that take one: they get, scalar, commit, roll back and close."""

    def __init__(self, job, run=None):
        self.job, self.run = job, run

    def get(self, _model, _id):
        return self.job

    def scalar(self, _statement):
        return self.run if self.run is not None else self.job  # the skill run when asked for one, else the job

    def commit(self):
        pass

    rollback = close = commit


def _plain_job(**fields):
    from types import SimpleNamespace

    return SimpleNamespace(**{
        "id": "job-1", "job_type": "metadata_extraction", "resource_id": "r-1", "status": JobStatus.RUNNING,
        "started_at": None, "finished_at": None, "attempt_count": 1, "progress": 0, "metadata_json": {},
        "error_message": None, "error_stack": None, **fields})


def test_a_skill_run_that_failed_makes_a_failed_job_with_its_reason():
    """Guards the sync after a skill run (the path every extraction job takes) leaving a failed job with no outcome."""
    from types import SimpleNamespace

    run = SimpleNamespace(status="failed", error_message="The AI service was busy or unreachable. Try again later.",
                          validation_error=None, id="run-1", validation_status="invalid")
    job = _plain_job()

    tasks._sync_job_from_skill_runs(_FakeDb(job, run), job_id="job-1")

    assert job.status == JobStatus.FAILED and job.error_message == run.error_message
    assert job.metadata_json["failure"]["reason"] == job.error_message


def test_a_job_dead_lettered_after_its_retries_says_so(monkeypatch):
    """Guards the last-resort writer (retries exhausted) leaving a failed job with a machine slug for a message
    and no outcome."""
    job = _plain_job()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: _FakeDb(job))

    tasks._mark_job_dead_letter("job-1", reason="max_retries_exhausted")

    assert job.status == JobStatus.FAILED and tasks.DEAD_LETTER_META_KEY in job.metadata_json
    assert "max_retries_exhausted" not in job.error_message
    assert job.metadata_json["failure"]["reason"] == job.error_message


@pytest.mark.parametrize("job_type", ["nonsense_job", "contract_review"])
def test_the_two_failures_the_worker_writes_itself_carry_an_outcome(monkeypatch, job_type):
    """Guards an unknown job type and a review that did not run failing with a message and no outcome."""
    job = _plain_job(job_type=job_type)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: _FakeDb(job))
    monkeypatch.setattr(tasks, "_should_run", lambda *_a: True)
    monkeypatch.setattr(tasks.ai_controller, "carry_forward_job", lambda *_a, **_k: None)

    async def incomplete(*_a, **_k):
        return "incomplete"

    monkeypatch.setattr(tasks, "_maybe_auto_review", incomplete)

    asyncio.run(tasks._run_ai_job("job-1"))

    assert job.status == JobStatus.FAILED
    assert job.metadata_json["failure"]["reason"] == job.error_message
    assert (job.metadata_json["failure"]["retryable"] is False) is (job_type == "nonsense_job")


def test_finishing_a_job_through_the_controller_clears_an_old_failure():
    """Guards a job that succeeded on a retry (finished by the controller, not the worker) keeping last night's failure."""
    job = _plain_job(metadata_json={"keep": 1, "failure": failure_payload(Outcome.TRANSIENT)})

    AIController()._finish_job(_FakeDb(job), job_id="job-1", status="succeeded", error_message=None)

    assert job.metadata_json == {"keep": 1}


def test_a_failed_tabular_cell_says_why_in_plain_words_and_never_the_error_text():
    """Guards the review grid (and its tooltip) showing 'Extraction failed: ' plus the first line of the raw error:
    a refused insert's first line is the contract's own words. Classified by type, like every other failure."""
    from app.jobs.tasks import _humanize_cell_error

    refused = DataError("INSERT ...", {"answer": SECRET}, Exception(SECRET))
    for exc in (refused, RuntimeError(SECRET), ValueError(SECRET), _status_error(529)):
        assert SECRET not in _humanize_cell_error(exc)
    assert "could not be saved" in _humanize_cell_error(refused)
    assert "busy or unreachable" in _humanize_cell_error(_status_error(529)) and "Re-run" in _humanize_cell_error(
        _status_error(529))
    assert "Re-run" not in _humanize_cell_error(_status_error(400))  # a rejected request fails the same way again
    assert "worker error" in _humanize_cell_error(RuntimeError("Event loop is closed"))
    assert "no extracted text" in _humanize_cell_error(RuntimeError("no text snapshot"))
    # A message that merely looks like a rate limit does not make an unrelated failure one.
    assert "rate-limiting" not in _humanize_cell_error(RuntimeError("Section 429 applies"))


def test_a_job_the_worker_gave_up_on_records_why(db, org):
    """Guards the dead-lettered job being the one failure with no outcome, so a later audit reads it as unknown."""
    job = _job(db, org, started_at=utcnow() - timedelta(days=9000), attempt_count=tasks.MAX_JOB_ATTEMPTS,
               error_message="The AI service was busy or unreachable. Try again later.")

    tasks._reclaim_stale_jobs(db, now=utcnow())

    assert job.status == JobStatus.FAILED
    assert job.metadata_json["failure"]["outcome"] == "internal" and job.metadata_json["failure"]["retryable"]
    # One sentence in both places: an earlier attempt's message must not contradict the stored reason.
    assert job.error_message == job.metadata_json["failure"]["reason"]


def test_a_job_the_page_load_reaper_timed_out_records_why(db, org):
    """Guards the other place that fails jobs (the Jobs page reaping a job queued for 20 minutes)."""
    from app.jobs import routes

    job = _job(db, org, status=JobStatus.QUEUED)
    job.created_at = utcnow() - timedelta(days=9000)
    db.flush()

    routes._reap_stuck_jobs(db, org_id=org.id)

    assert job.status == JobStatus.FAILED and job.metadata_json["failure"]["outcome"] == "internal"


# --- the HTTP answer ----------------------------------------------------------------------------------------------


def _client(**app_options) -> TestClient:
    """The app as served: the real handlers behind CORS (the browser app is on another origin)."""
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(**app_options)
    register_exception_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"])
    errors = {
        "busy": _status_error(429, headers={"retry-after": "30"}),
        "rejected": _status_error(400),
        "cut": AIOutputInvalid("x", feature_key="f", call_id=None, outcome=Outcome.TRUNCATED),
        "bug": RuntimeError(SECRET),
        "other": httpx.ConnectError(SECRET),  # some other service being down is not a model failure
        "not_model": AIOutputInvalid("x", feature_key="f", call_id=None, outcome=Outcome.INTERNAL),
    }

    @app.get("/fail/{kind}")
    def fail(kind: str):
        raise errors[kind]

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client():
    return _client()


@pytest.mark.parametrize("kind, status, outcome", [
    ("busy", 503, "transient"), ("rejected", 502, "permanent"), ("cut", 422, "truncated")])
def test_a_failed_model_call_answers_with_what_happened(client, kind, status, outcome):
    """Guards 'Internal server error' for a provider that was only busy: a metadata reading that used to return
    the guess now returns its reason, with Retry-After when the provider sent one."""
    response = client.get(f"/fail/{kind}")
    assert response.status_code == status and response.json()["outcome"] == outcome
    assert SECRET not in response.text
    assert (response.headers.get("retry-after") == "30") is (kind == "busy")


@pytest.mark.parametrize("kind", ["bug", "other", "not_model"])
def test_any_other_error_is_still_a_plain_500_that_shows_nothing(client, kind):
    """Guards the handlers turning every error into a model failure (an httpx error from some other service,
    a gateway error that is not about the model), or leaking an app bug's message."""
    response = client.get(f"/fail/{kind}")
    assert response.status_code == 500 and response.json()["detail"] == "Internal server error"
    assert SECRET not in response.text


@pytest.mark.parametrize("options", [{}, {"debug": True}])
def test_the_model_failure_reply_reaches_the_browser(options):
    """Guards the plain sentence being invisible to the app: a reply built in the catch-all 500 handler sits
    outside the CORS middleware (the browser reports a network error and hides the status), and `debug=True`
    replaces it with a traceback page. Specific handlers run inside CORS and are not replaced."""
    response = _client(**options).get("/fail/busy", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 503 and response.json()["outcome"] == "transient"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
