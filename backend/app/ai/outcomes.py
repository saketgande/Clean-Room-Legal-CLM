"""What a failed model call or job says about itself: one outcome from a short list, a plain sentence for
people, and whether a person or a machine may try again. Pure: no database, no network.

A job that fails carries this on `job_run.metadata_json["failure"]`, and `error_message` is the same plain
sentence. The raw exception text is never shown: a failed insert prints the contract's own words as its
parameters, and the Jobs page, the timeline and the notifications all used to show it.

Five model outcomes (plus `ok`), and two that are about the job rather than the model:

  truncated   the answer hit its output limit and was cut off; the same input is cut off again
  refused     the model declined to answer
  unusable    an answer arrived but not one the feature can use (empty, cut short, wrong shape)
  transient   a service was busy, slow or unreachable; asking again later may work
  permanent   a service rejected the request itself; someone has to look
  save_failed the database refused what was written (a constraint or a value it cannot hold)
  internal    anything else

The first five say "the AI service" only when the error came from the AI provider; a failure of any other
service a job depends on (OCR, e-signature, email) reads as "a service this step depends on".

`retryable` says a person may press Retry and it can plausibly help. `auto_retry` says a machine may re-run
it without asking: true for a bad moment at a service (transient), false for everything else, and also false for a
step that ran out of time (a 12-minute job would be paid again) and for the daily AI budget (it clears tomorrow).
How often and how long anything retries is written once, in app/core/retry_policy.py.
"""

from __future__ import annotations

from enum import StrEnum

import anthropic
import httpx
from celery.exceptions import SoftTimeLimitExceeded
from fastapi import HTTPException
from sqlalchemy.exc import DataError, IntegrityError

from app.core.database import utcnow
from app.integrations._http_retry import ProviderUnavailableError


class Outcome(StrEnum):
    OK = "ok"
    TRUNCATED = "truncated"
    REFUSED = "refused"
    UNUSABLE = "unusable"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    SAVE_FAILED = "save_failed"
    INTERNAL = "internal"


MODEL_OUTCOMES = frozenset({Outcome.TRUNCATED, Outcome.REFUSED, Outcome.UNUSABLE, Outcome.TRANSIENT, Outcome.PERMANENT})

# (plain sentence, a person may retry, a machine may re-run)
_RULES: dict[Outcome, tuple[str, bool, bool]] = {
    Outcome.TRUNCATED: (
        "The AI's answer was too long to fit in one pass, so it was cut off. A long contract has to be read in pieces.",
        False, False),
    Outcome.REFUSED: ("The AI declined to answer for this document.", False, False),
    Outcome.UNUSABLE: ("The AI's answer could not be used: it was cut short, empty or in the wrong shape.", True, False),
    Outcome.TRANSIENT: ("The AI service was busy or unreachable. Try again later.", True, True),
    Outcome.PERMANENT: ("The AI service rejected the request. An administrator needs to look.", False, False),
    # Not retryable: the same result is refused the same way, and a retry would pay for the call again.
    Outcome.SAVE_FAILED: ("The step finished, but its result could not be saved. An administrator needs to look.",
                          False, False),
    Outcome.INTERNAL: ("Something went wrong inside Aegis.", True, False),
}

# The same two outcomes for a service that is not the AI provider (OCR, e-signature, email).
_OTHER_SERVICE = {
    Outcome.TRANSIENT: "A service this step depends on was busy or unreachable. Try again later.",
    Outcome.PERMANENT: "A service this step depends on rejected the request. An administrator needs to look.",
}

# Error events the provider sends inside an HTTP 200 stream. These are the ones that mean the request itself is
# wrong (the 4xx family); any other type (overloaded, api, rate limit, timeout, or one we do not know) is a bad moment.
_PERMANENT_STREAM_ERRORS = frozenset({
    "invalid_request_error", "authentication_error", "permission_error", "not_found_error", "request_too_large",
    "billing_error",
})


class PlainError(ValueError):
    """An error whose message is already a plain sentence safe to show to people (a guard that refused)."""

    retryable = True


class SetupError(PlainError, RuntimeError):
    """A plain refusal that retrying cannot fix: a switched-off skill, a missing key. Someone has to change setup.
    Also a RuntimeError, which is what both places raised before, so callers that catch that keep working."""

    retryable = False


def _status_outcome(status: int | None) -> Outcome:
    return Outcome.TRANSIENT if status is not None and (status == 429 or 500 <= status < 600) else Outcome.PERMANENT


def retry_after(exc: BaseException) -> int | None:
    """Seconds the provider (or an open circuit) asked us to wait, when it said so as a whole number."""
    response = getattr(exc, "response", None)
    value = getattr(response, "headers", {}).get("retry-after") if response is not None else None
    if value is None:
        value = getattr(exc, "retry_after", None)  # a provider circuit that is open says when to come back
    return int(value) if value is not None and str(value).isdigit() else None


def classify(exc: BaseException) -> tuple[Outcome, dict]:
    """The outcome of a failure and the facts worth keeping (status code, provider request id, stop reason,
    and `ai`: whether it came from the AI provider). The gateway tags its own errors; everything else is
    recognised by type, never by message text."""
    facts: dict = {"ai": False}
    if isinstance(exc, SoftTimeLimitExceeded):
        # The worker's own time limit. Not a bad moment at a service: running it again would pay for another
        # full-length attempt, so a person decides.
        facts["auto_retry"] = False
        return Outcome.TRANSIENT, facts
    outcome = getattr(exc, "outcome", None)
    if outcome in Outcome.__members__.values():
        facts["stop_reason"] = getattr(exc, "stop_reason", None)
        facts["ai"] = True
        return Outcome(outcome), facts
    if isinstance(exc, anthropic.APIError) or getattr(exc, "provider", None) == "claude":
        facts["ai"] = True
    if isinstance(exc, anthropic.APIStatusError):
        facts["request_id"] = getattr(exc, "request_id", None)
        if exc.status_code == 200:  # an error event inside a stream that had already started: no HTTP status to show
            body = exc.body if isinstance(exc.body, dict) else {}
            kind = (body.get("error") or {}).get("type") if isinstance(body.get("error"), dict) else body.get("type")
            return (Outcome.PERMANENT if kind in _PERMANENT_STREAM_ERRORS else Outcome.TRANSIENT), facts
        facts["status_code"] = exc.status_code
        return _status_outcome(exc.status_code), facts
    if isinstance(exc, httpx.HTTPStatusError):
        facts["status_code"] = exc.response.status_code
        return _status_outcome(exc.response.status_code), facts
    if isinstance(exc, (anthropic.APIConnectionError, httpx.TransportError, ConnectionError, TimeoutError,
                        ProviderUnavailableError)):
        return Outcome.TRANSIENT, facts
    if isinstance(exc, HTTPException):
        facts["status_code"] = exc.status_code
        # The daily AI budget (429) clears tomorrow, not in a minute: no machine retries it. Any other refusal is the
        # app's own, in plain words.
        if exc.status_code == 429:
            facts["auto_retry"] = False
            return Outcome.TRANSIENT, facts
        return Outcome.INTERNAL, facts
    if isinstance(exc, PlainError):
        return Outcome.INTERNAL, facts
    # The database refused a value or a constraint, which only a write can cause. Every other database error (a
    # dropped connection, a statement timeout, a missing column) can happen before anything finished, so it is
    # "something went wrong inside Aegis", not a failed save.
    if isinstance(exc, (IntegrityError, DataError)):
        return Outcome.SAVE_FAILED, facts
    return Outcome.INTERNAL, facts


def reason_for(outcome: Outcome, exc: BaseException | None = None, facts: dict | None = None) -> str:
    """The plain sentence people see. For a refusal by the app itself (an HTTPException or a PlainError) it is
    that error's own message, which is a sentence by construction; for everything else it is fixed text."""
    facts = facts or {}
    if isinstance(exc, SoftTimeLimitExceeded):
        return "The step took longer than the time allowed and was stopped. Try again, or ask an administrator if it keeps happening."
    if isinstance(exc, PlainError):
        return str(exc)
    if isinstance(exc, HTTPException) and isinstance(exc.detail, str):
        return "The daily AI budget is used up. It resets tomorrow." if exc.status_code == 429 else exc.detail
    other = outcome in _OTHER_SERVICE and not facts.get("ai", True)
    if outcome is Outcome.PERMANENT and facts.get("status_code"):
        who = "A service this step depends on" if other else "The AI service"
        return f"{who} rejected the request (HTTP {facts['status_code']}). An administrator needs to look."
    return _OTHER_SERVICE[outcome] if other else _RULES[outcome][0]


def failure_payload(outcome: Outcome, reason: str | None = None, **extra) -> dict:
    """The `failure` object stored on a failed job."""
    _, retryable, auto = _RULES[outcome]
    return {
        "outcome": outcome.value, "reason": reason or _RULES[outcome][0], "retryable": retryable,
        "auto_retry": auto, "retry_after_s": None, "stop_reason": None, "status_code": None,
        "request_id": None, "call_id": None, "at": utcnow().isoformat(), **extra,
    }


def failure_from_exception(exc: BaseException, *, call_id: str | None = None) -> dict:
    outcome, facts = classify(exc)
    payload = failure_payload(
        outcome, reason_for(outcome, exc, facts), call_id=call_id or getattr(exc, "call_id", None),
        retry_after_s=retry_after(exc), **{k: v for k, v in facts.items() if v is not None and k != "ai"},
    )
    if isinstance(exc, PlainError):
        payload["retryable"] = exc.retryable
    return payload


def ledger_text(failure: dict) -> str:
    """One line for the AI call ledger: 'transient: <sentence> (HTTP 529, request req_abc)'."""
    bits = [f"HTTP {failure['status_code']}"] if failure.get("status_code") else []
    if failure.get("request_id"):
        bits.append(f"request {failure['request_id']}")
    return f"{failure['outcome']}: {failure['reason']}" + (f" ({', '.join(bits)})" if bits else "")
