"""How often anything in Aegis tries again, written once.

Every layer that can repeat a call or a job is in this table, so nobody has to add the layers up in their head to
learn how many times one failed model call can be paid for. `tests/test_claude_retry_policy.py` pins every number
below to the settings and constants that produce it.

  layer                         where                      limit                        waits                          then
  ---------------------------   ------------------------   --------------------------   ----------------------------   ------------------------------
  1  the model call, provider   claude.py                  5 tries in a background      Retry-After if it is 30 s or   a wait that does not fit is NOT
     said no before any answer  `_post_messages`           job, 2 anywhere else         less, else 2, 4, 8, 16 s       slept: the error goes up with
     (HTTP 429, 5xx, 529)                                  (claude_retry_tries,         (+/- 25% jitter); sleeping     `failure.retry_after_s`
                                                           claude_retry_inline_tries)   at most 45 s in a job, 5 s
                                                                                        elsewhere
  1b the same, but the answer    same                      2 tries, anywhere (COSTLY_   same                           same
     had started (any stream                               TRIES): each one can pay
     event arrived) or the try                             for a full answer again
     ran 20 s or more
  1c anything else (a 4xx, a     same                      1 try                        -                              the error goes up
     guard, not the AI provider)
  -  the whole call              same                      no new try BEGINS after      -                              the error goes up
                                                           240 s (claude_retry_deadline_
                                                           seconds), counting the time
                                                           spent inside tries; a try
                                                           that has begun is not cut
                                                           short (only the 120 s gap
                                                           between stream events ends it)
  2  a job after a transport     tasks.run_ai_job           3 re-runs (Celery            Celery's exponential backoff   FAILED, dead-letter marker
     error (httpx, connection,   (`autoretry_for`)         `max_retries`), and only     (1, 2, 4 s, jittered, capped   `max_retries_exhausted`
     timeout: OCR, e-signature)                             for those errors             at 10 min)
  2b a job after a model-call    -                          NOT re-run by a machine:     -                              FAILED, `retryable`: a person
     error (busy, rate limited)                             a person presses Retry                                      decides (the error says when)
  3  a job whose worker died     tasks._reclaim_stale_jobs  3 runs in all                -                              re-queued, then dead-lettered
     (RUNNING past its lease)                               (MAX_JOB_ATTEMPTS counts                                    `lease_expired`
                                                           every run of the job)
  4  the daily sweep (Step 9)    not built yet              0 re-runs of a failed job;   -                              the owner is told once
                                                           it queues ONE job for a
                                                           contract nobody ever read
  5  a person                    Retry button, the          one job in flight at a       -                              the button is hidden when
                                 assistant's extract tool   time (409 otherwise)                                        `failure.retryable` is false

Worst case for one model call inside a background job: 5 tries (waiting at most 45 s), or 2 when the answer had
started. Layer 2 re-runs the whole job, so a transport error that survives it is paid for up to 4 runs in all. A
job that ran out of time (Celery's soft limit) or hit the daily AI budget is never re-run by a machine
(`auto_retry` is false for both: outcomes.py).

Not counted anywhere: the input tokens of an abandoned try that had already started. The daily cap and the exam's
spending ceiling only see the call that finished, so a costly retry is paid for and not metered (at most one extra
prompt per call).

Pure: no database, no network, no sleeping. The client does the sleeping.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from contextvars import ContextVar

from app.core.config import settings

# True inside a background job (set by run_ai_job). A job holds a worker slot it would hold anyway and a failed job can
# be retried, so it waits for a busy provider; a request a person is waiting on, or a thread held open, gives up fast.
patient: ContextVar[bool] = ContextVar("claude_retry_patient", default=False)

COSTLY_TRIES = 2  # an answer that had started: try once more and no more
COSTLY_AFTER_SECONDS = 20.0  # a try that ran this long before failing was probably generating, whatever it raised
MAX_WAIT_SECONDS = 30.0  # the longest single sleep; a longer Retry-After is handed back instead of slept on


def limits() -> tuple[int, float]:
    """(tries, seconds of sleeping) allowed for a call made now."""
    if patient.get():
        return max(1, settings.claude_retry_tries), settings.claude_retry_budget_seconds
    return max(1, settings.claude_retry_inline_tries), settings.claude_retry_inline_budget_seconds


def client_wait(
    exc: BaseException,
    *,
    tries: int,
    waited: float,
    elapsed: float,
    attempt_seconds: float,
    started: bool,
    rnd: Callable[[], float] = random.random,
) -> float | None:
    """Seconds to sleep before the next try of a model call, or None to stop and let the error go up.

    `tries` is how many have been made, `waited` the seconds already slept, `elapsed` the seconds since the first try
    began, `attempt_seconds` how long the try that just failed ran, and `started` whether any stream event arrived.
    Only the AI provider's own bad moments are retried: anything else (a guard, a 4xx, another service) and anything
    the worker's time limit raised is not."""
    # Lazy: app.ai imports the Claude client, which imports this module.
    from app.ai.outcomes import Outcome, classify, retry_after

    # The worker's own time limit (SoftTimeLimitExceeded, an ordinary Exception) classifies as not-the-provider's, so it
    # is never retried here: a second paid try would outlive a worker that was told to stop.
    outcome, facts = classify(exc)
    if outcome is not Outcome.TRANSIENT or not facts.get("ai"):
        return None
    allowed, budget = limits()
    if started or attempt_seconds >= COSTLY_AFTER_SECONDS:
        allowed = min(allowed, COSTLY_TRIES)
    if tries >= allowed:
        return None
    asked = retry_after(exc)
    if asked is not None:
        if asked > MAX_WAIT_SECONDS:
            return None
        # A floor and a little jitter: "0" would fire every try back to back, and every worker is told the same number.
        wait = max(asked, 1) + rnd()
    else:
        wait = min(MAX_WAIT_SECONDS, settings.claude_retry_base_wait_seconds * 2 ** (tries - 1)) * (0.75 + 0.5 * rnd())
    if waited + wait > budget or elapsed + wait > settings.claude_retry_deadline_seconds:
        return None
    return wait
