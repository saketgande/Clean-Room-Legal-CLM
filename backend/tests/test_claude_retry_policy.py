"""The Claude client's one retry policy (app/core/retry_policy.py, used by `_post_messages` in claude.py).

The policy is pure, so its rules are checked directly; the loop is checked against a scripted client that plays one
attempt per call (an error before any answer, an error part-way through, a clean finish, a stream that just ends), with
no network and no real sleeping. The budget table in the policy's docstring is pinned to the settings that produce it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from fastapi import HTTPException

from app.ai.outcomes import failure_from_exception
from app.core import retry_policy
from app.core.config import settings
from app.integrations import claude
from app.integrations.claude import ClaudeClient

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(code: int, *, headers: dict | None = None) -> anthropic.APIStatusError:
    return anthropic.APIStatusError(
        f"error {code}", response=httpx.Response(code, request=REQUEST, headers=headers or {}), body={})


def stream_error(kind: str) -> anthropic.APIStatusError:
    """An error event inside a stream the provider answered 200 to."""
    return anthropic.APIStatusError(
        "overloaded", response=httpx.Response(200, request=REQUEST), body={"error": {"type": kind}})


def wait(exc, *, tries=1, waited=0.0, elapsed=0.0, took=1.0, started=False, patient=True):
    token = retry_policy.patient.set(patient)
    try:
        return retry_policy.client_wait(exc, tries=tries, waited=waited, elapsed=elapsed, attempt_seconds=took,
                                        started=started, rnd=lambda: 0.5)
    finally:
        retry_policy.patient.reset(token)


# --- the rules, pure ---------------------------------------------------------------------------------------------


def test_a_busy_provider_is_waited_for_two_four_eight_sixteen_seconds_then_given_up_on():
    """Guards the old 1-2 s of patience (3 tries, 3 s in all): a busy minute outlasted it and every job in flight
    failed. A job's call waits 2, 4, 8, 16 s and stops at the fifth try."""
    got = [wait(status_error(529), tries=n, waited=sum([2, 4, 8, 16][: n - 1])) for n in range(1, 6)]
    assert got == [2.0, 4.0, 8.0, 16.0, None]


def test_a_call_with_a_person_waiting_gives_up_after_two_tries_and_a_few_seconds():
    """Guards a web request or a held thread sleeping 37 s through every outage: only a background job is patient."""
    assert wait(status_error(529), tries=1, patient=False) == 2.0
    assert wait(status_error(529), tries=2, waited=2.0, patient=False) is None
    assert wait(status_error(429, headers={"retry-after": "6"}), patient=False) is None  # 6.5 s > 5 s of sleeping


def test_an_answer_that_had_started_is_tried_once_more_and_no_more():
    """Guards paying for a full answer five times: each try that got part-way can bill the whole prompt again."""
    assert wait(status_error(529), tries=1, started=True) == 2.0
    assert wait(status_error(529), tries=2, started=True, waited=2.0) is None


def test_a_try_that_ran_twenty_seconds_counts_as_started_whatever_it_raised():
    """Guards a 504 that came back after a minute of silence being retried five times like an instant 529."""
    assert wait(status_error(504), tries=2, waited=2.0, took=19.0) == 4.0
    assert wait(status_error(504), tries=2, waited=2.0, took=20.0) is None


def test_a_retry_after_is_slept_when_it_fits_and_handed_back_when_it_does_not():
    """Guards sleeping a worker slot through a long rate-limit window: over 30 s, or over what is left of the
    budget, the error goes up carrying the number, so whoever retries can wait for it."""
    ok = status_error(429, headers={"retry-after": "20"})
    assert wait(ok) == 20.5  # the provider's number plus a little jitter, so workers do not retry in step
    long = status_error(429, headers={"retry-after": "40"})
    assert wait(long) is None and failure_from_exception(long)["retry_after_s"] == 40
    assert wait(ok, waited=30.0) is None  # 30.0 + 20.5 > the 45 s budget
    assert wait(status_error(429, headers={"retry-after": "30"}), waited=14.0) == 30.5


def test_a_retry_after_of_zero_still_waits_a_second():
    """Guards "Retry-After: 0" firing every try back to back."""
    assert wait(status_error(429, headers={"retry-after": "0"})) == 1.5


def test_an_unreadable_retry_after_falls_back_to_the_ordinary_wait():
    assert wait(status_error(529, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) == 2.0


def test_no_new_try_begins_after_the_deadline_counting_time_spent_inside_tries():
    """Guards five 100 s tries plus sleeping running into Celery's 12-minute soft limit: the budget counts sleeping
    only, so the deadline counts everything. (A try already under way is not cut short.)"""
    assert wait(status_error(529), tries=2, waited=2.0, elapsed=237.0) is None
    assert wait(status_error(529), tries=2, waited=2.0, elapsed=230.0) == 4.0


def test_only_the_ai_providers_own_bad_moments_are_retried():
    """Guards the loop retrying a guard, a 4xx, another service's error or the worker's own time limit: Celery's
    SoftTimeLimitExceeded is an ordinary Exception, so a loop that retried it would start a second paid call after
    the worker was told to stop."""
    never = [
        status_error(400), status_error(401), status_error(404), RuntimeError("boom"), ValueError("bad"),
        HTTPException(429, "budget"), httpx.ConnectError("other service"), SoftTimeLimitExceeded(),
        TimeoutError("not the provider's"),
    ]
    assert [wait(e) for e in never] == [None] * len(never)


@pytest.mark.parametrize("exc", [
    status_error(429), status_error(500), status_error(529), anthropic.APIConnectionError(request=REQUEST),
    anthropic.APITimeoutError(request=REQUEST), stream_error("overloaded_error"),
])
def test_every_kind_of_busy_provider_is_retried(exc):
    assert wait(exc) is not None


def test_an_error_event_that_says_the_request_itself_is_wrong_is_not_retried():
    assert wait(stream_error("invalid_request_error")) is None


def test_the_written_budget_matches_what_runs():
    """Guards the table in the policy's docstring going out of date: every number in it is read from the settings,
    constants and task options that really decide it."""
    from app.jobs import tasks

    d = retry_policy.__doc__
    assert (settings.claude_retry_tries, settings.claude_retry_budget_seconds) == (5, 45.0)
    assert (settings.claude_retry_inline_tries, settings.claude_retry_inline_budget_seconds) == (2, 5.0)
    assert (settings.claude_retry_base_wait_seconds, settings.claude_retry_deadline_seconds) == (2.0, 240.0)
    assert (retry_policy.COSTLY_TRIES, retry_policy.COSTLY_AFTER_SECONDS, retry_policy.MAX_WAIT_SECONDS) == (2, 20.0, 30.0)
    for text in ("5 tries", "2 anywhere else", "45 s", "5 s", "2, 4, 8, 16", "20 s or more", "30 s", "240 s",
                 "3 re-runs", "max_retries_exhausted", "3 runs in all", "4 runs in all", "MAX_JOB_ATTEMPTS"):
        assert text in d, text
    assert tasks.MAX_JOB_ATTEMPTS == 3 and tasks.run_ai_job.max_retries == 3
    assert tasks.run_ai_job.autoretry_for == tasks.TRANSIENT_ERRORS
    # Not retried by a machine, in the same words: the two outcomes that must never re-run by themselves.
    assert failure_from_exception(SoftTimeLimitExceeded())["auto_retry"] is False
    assert failure_from_exception(HTTPException(429, "x"))["auto_retry"] is False
    assert failure_from_exception(status_error(529))["auto_retry"] is True


# --- the loop, on a scripted client ------------------------------------------------------------------------------


class Attempt:
    """One scripted try. `enter`: raised before any event. `events` then `after`: events arrive, then it raises. `stop`:
    the stop reason the finished message carries. `takes`: seconds the try appears to run for."""

    def __init__(self, *, enter=None, events=(), after=None, stop="end_turn", takes=0.0, final_error=None):
        self.enter, self.events, self.after, self.stop, self.takes = enter, list(events), after, stop, takes
        self.final_error = final_error  # raised by get_final_message (what the SDK does for a stream with no events)


class Script:
    def __init__(self, attempts, clock):
        self.attempts, self.clock, self.calls = list(attempts), clock, 0
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **_payload):
        self.calls += 1
        return _Stream(self.attempts.pop(0), self.clock)


class _Stream:
    request_id = "req_scripted"

    def __init__(self, attempt, clock):
        self.attempt, self.clock, self.sent = attempt, clock, 0

    async def __aenter__(self):
        if self.attempt.enter is not None:
            self.clock.t += self.attempt.takes
            raise self.attempt.enter
        return self

    async def __aexit__(self, *_exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.sent < len(self.attempt.events):
            self.sent += 1
            return self.attempt.events[self.sent - 1]
        self.clock.t += self.attempt.takes
        if self.attempt.after is not None:
            raise self.attempt.after
        raise StopAsyncIteration

    async def get_final_message(self):
        if self.attempt.final_error is not None:
            raise self.attempt.final_error
        message = {"id": "msg", "type": "message", "role": "assistant", "model": "m", "stop_reason": self.attempt.stop,
                   "content": [{"type": "text", "text": "hello"}], "usage": {"input_tokens": 100, "output_tokens": 5}}
        return SimpleNamespace(to_dict=lambda: message)


EVENT = SimpleNamespace(type="message_start")


@pytest.fixture
def client(monkeypatch):
    """The real `_post_messages` on a scripted client, with the clock under the test's control, sleeping recorded
    instead of done and the daily-cap calls recorded."""
    state = SimpleNamespace(t=1000.0, sleeps=[], metered=[], script=None)

    async def fake_sleep(seconds):
        state.sleeps.append(seconds)
        state.t += seconds

    monkeypatch.setattr(settings, "mock_claude", False)
    monkeypatch.setattr(settings, "claude_api_key", "k")
    monkeypatch.setattr(claude, "exam_meter", None)
    monkeypatch.setattr(claude, "_sleep", fake_sleep)
    monkeypatch.setattr(claude, "_clock", lambda: state.t)
    monkeypatch.setattr(claude, "reserve_tokens", lambda *_a: state.metered.append("reserve") or "r")
    monkeypatch.setattr(claude, "settle_tokens", lambda _r, used: state.metered.append(("settle", used)))

    def play(*attempts, patient=True):
        state.script = Script(attempts, state)
        monkeypatch.setattr(claude, "_anthropic_client", lambda: state.script)

        async def go():
            retry_policy.patient.set(patient)
            return await ClaudeClient()._post_messages(org_id="o", json_payload={"model": "m", "max_tokens": 5,
                                                                                   "messages": []})

        return asyncio.run(go())

    state.play = play
    return state


def test_a_busy_provider_that_recovers_gives_its_answer_after_waiting(client):
    response, request_id = client.play(Attempt(enter=status_error(529)), Attempt(enter=status_error(529)), Attempt())
    assert response["content"][0]["text"] == "hello" and request_id == "req_scripted"
    first, second = client.sleeps  # 2 s and 4 s, each give or take a quarter of jitter
    assert client.script.calls == 3 and 1.5 <= first <= 2.5 and 3.0 <= second <= 5.0


def test_a_retry_after_that_fits_is_what_the_loop_sleeps(client):
    client.play(Attempt(enter=status_error(429, headers={"retry-after": "20"})), Attempt())
    assert client.sleeps == [pytest.approx(20.5, abs=0.5)]


def test_a_retry_after_that_does_not_fit_is_not_slept_and_the_error_carries_it(client):
    with pytest.raises(anthropic.APIStatusError) as err:
        client.play(Attempt(enter=status_error(429, headers={"retry-after": "90"})), Attempt())
    assert client.script.calls == 1 and client.sleeps == []
    assert failure_from_exception(err.value)["retry_after_s"] == 90


def test_a_job_stops_after_five_tries_and_a_request_after_two(client):
    with pytest.raises(anthropic.APIStatusError):
        client.play(*[Attempt(enter=status_error(529)) for _ in range(8)])
    assert client.script.calls == 5 and len(client.sleeps) == 4
    with pytest.raises(anthropic.APIStatusError):
        client.play(*[Attempt(enter=status_error(529)) for _ in range(8)], patient=False)
    assert client.script.calls == 2


def test_an_answer_that_dies_part_way_is_tried_once_more(client):
    """Guards paying for the same long answer up to five times."""
    with pytest.raises(anthropic.APIConnectionError):
        client.play(*[Attempt(events=[EVENT], after=httpx.RemoteProtocolError("closed")) for _ in range(5)])
    assert client.script.calls == 2


def test_a_stream_that_just_ends_without_a_stop_reason_is_a_dropped_connection(client):
    """Guards a cut-off answer being returned as if it were finished: the connection closed cleanly, so nothing was
    raised and neither the loop nor anything above it noticed."""
    response, _ = client.play(Attempt(events=[EVENT], stop=None), Attempt(events=[EVENT]))
    assert response["stop_reason"] == "end_turn" and client.script.calls == 2
    with pytest.raises(anthropic.APIConnectionError):
        client.play(*[Attempt(events=[EVENT], stop=None) for _ in range(5)])
    assert client.script.calls == 2


def test_a_connect_timeout_is_a_fast_failure_and_a_read_timeout_after_a_wait_is_not(client):
    """Guards a ten-second connect timeout (nothing reached the provider) using up the one retry a costly failure
    gets: it is retried like any other 'said no at once'."""
    connect = Attempt(enter=httpx.ConnectTimeout("no route"), takes=10.0)
    client.play(connect, connect, connect, Attempt())
    assert client.script.calls == 4
    with pytest.raises(anthropic.APITimeoutError):
        # 30 s: past the 20 s rule and nowhere near the 240 s deadline, so only the rule can stop it at two.
        client.play(*[Attempt(enter=httpx.ReadTimeout("slow"), takes=30.0) for _ in range(5)])
    assert client.script.calls == 2


def test_a_provider_that_rejects_the_request_is_asked_once(client):
    with pytest.raises(anthropic.APIStatusError):
        client.play(Attempt(enter=status_error(400)), Attempt())
    assert client.script.calls == 1 and client.sleeps == []


def test_the_workers_time_limit_stops_the_loop_at_once(client):
    """Guards a second paid call after the worker was told to stop: SoftTimeLimitExceeded is an ordinary Exception,
    and it can land anywhere inside a try."""
    with pytest.raises(SoftTimeLimitExceeded):
        client.play(Attempt(events=[EVENT], after=SoftTimeLimitExceeded()), Attempt())
    assert client.script.calls == 1 and client.sleeps == []


def test_a_capture_signal_passes_through_the_loop(client):
    """The exam stops an app operation with a BaseException on purpose: the loop catches Exception only."""
    class Deferred(BaseException):
        pass

    with pytest.raises(Deferred):
        client.play(Attempt(enter=Deferred()), Attempt())
    assert client.script.calls == 1 and client.sleeps == []


def test_a_first_try_that_ran_to_the_deadline_is_not_followed_by_another(client):
    """Guards the deadline not reaching the loop (elapsed measured from the first try): a try that failed after 239 s
    would otherwise be given its one retry, and a second multi-minute answer cannot finish inside the soft limit."""
    with pytest.raises(anthropic.APIStatusError):
        client.play(Attempt(enter=status_error(529), takes=239.0), Attempt())
    assert client.script.calls == 1 and client.sleeps == []
    client.play(Attempt(enter=status_error(529), takes=200.0), Attempt())  # 200 s + a wait still fits
    assert client.script.calls == 2


def test_the_sleeping_budget_is_spent_across_tries_not_per_try(client):
    """Guards the budget being re-read at zero every try: two Retry-After of 30 s are 61 s of sleeping against 45 s."""
    ra30 = {"retry-after": "30"}
    with pytest.raises(anthropic.APIStatusError):
        client.play(Attempt(enter=status_error(429, headers=ra30)), Attempt(enter=status_error(429, headers=ra30)),
                    Attempt())
    assert client.script.calls == 2 and len(client.sleeps) == 1 and client.sleeps[0] >= 30


def test_a_200_that_closes_with_no_event_at_all_is_a_dropped_connection(client):
    """Guards the SDK's internal failure (it has no message to hand back and fails an assert) reading as "Something
    went wrong inside Aegis" and not being retried: nothing was generated, so it is a fast failure."""
    gone = AssertionError("no snapshot")
    response, _ = client.play(Attempt(final_error=gone), Attempt(final_error=gone), Attempt(events=[EVENT]))
    assert response["stop_reason"] == "end_turn" and client.script.calls == 3
    with pytest.raises(anthropic.APIConnectionError):
        client.play(*[Attempt(final_error=gone) for _ in range(8)])
    assert client.script.calls == 5  # not costly: no answer had started


def test_an_assert_after_events_arrived_is_not_mistaken_for_a_dropped_connection(client):
    """Guards swallowing a real bug: only the no-event case is the SDK's empty-stream failure."""
    with pytest.raises(AssertionError):
        client.play(Attempt(events=[EVENT], final_error=AssertionError("a bug")), Attempt())
    assert client.script.calls == 1


def test_the_daily_cap_is_reserved_and_settled_once_however_many_tries(client):
    """Guards metering per try: a reservation per attempt would count one logical call as three."""
    client.play(Attempt(enter=status_error(529)), Attempt(enter=status_error(529)), Attempt())
    assert client.metered == ["reserve", ("settle", 105)]
