"""Live events of an Ask Aegis answer, kept in Redis.

The worker that produces an answer appends every event (text chunks, tool
steps, confirmation requests, errors, ``done``) to a Redis Stream per run. The
browser never owns the work: it only *reads* that stream, so leaving the page
stops the reading, not the answer, and coming back replays it from the start
(or continues after the last event id it saw).

Redis keys per run (all expire, so nothing here grows without bound):
  assistant:run:<id>:events  the event stream (XADD, capped length, 1 h TTL)
  assistant:run:<id>:cancel  set by the Stop button; the worker checks it
  assistant:run:<id>:claim   set by the worker that runs the answer, so a
                             redelivered task can't run the same answer twice

Redis failures never fail an answer: publishing is best-effort and the answer
is always saved to the database, which the page falls back to.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger(__name__)

EVENTS_TTL_SECONDS = 3600
# Approximate cap on entries per run. Text arrives in small chunks, so this is
# generous for one answer; it exists so a runaway run can't fill Redis.
EVENTS_MAXLEN = 20_000
TERMINAL_EVENT = "done"


def events_key(run_id: str) -> str:
    return f"assistant:run:{run_id}:events"


def cancel_key(run_id: str) -> str:
    return f"assistant:run:{run_id}:cancel"


def claim_key(run_id: str) -> str:
    return f"assistant:run:{run_id}:claim"


@lru_cache(maxsize=1)
def _redis():
    """One shared, timeout-bounded sync client (its pool is reused)."""
    import redis

    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=5)


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def publish(run_id: str, event: str, payload: dict) -> str | None:
    """Append one event; returns its stream id, or None if Redis is unavailable."""
    try:
        pipe = _redis().pipeline(transaction=False)
        pipe.xadd(
            events_key(run_id),
            {"event": event, "data": json.dumps(payload, default=str)},
            maxlen=EVENTS_MAXLEN,
            approximate=True,
        )
        pipe.expire(events_key(run_id), EVENTS_TTL_SECONDS)
        entry_id, _ = pipe.execute()
        return _text(entry_id)
    except Exception:
        logger.warning("assistant run %s: could not publish %s event", run_id, event, exc_info=True)
        return None


def request_cancel(run_id: str) -> bool:
    try:
        _redis().set(cancel_key(run_id), "1", ex=EVENTS_TTL_SECONDS)
        return True
    except Exception:
        logger.warning("assistant run %s: could not record cancel", run_id, exc_info=True)
        return False


def cancel_requested(run_id: str) -> bool:
    try:
        return bool(_redis().exists(cancel_key(run_id)))
    except Exception:
        return False


def claim(run_id: str, *, ttl_seconds: int) -> bool | None:
    """True if this worker now owns the run, False if another already claimed it,
    None if Redis can't tell (the caller proceeds: the DB status check still
    stops finished runs from running again)."""
    try:
        return bool(_redis().set(claim_key(run_id), "1", nx=True, ex=ttl_seconds))
    except Exception:
        logger.warning("assistant run %s: could not claim", run_id, exc_info=True)
        return None


def reset_events(run_id: str) -> None:
    """Start a fresh event stream for a run that continues (a confirmed action).

    The old stream ends with the ``done`` that paused the run for confirmation;
    a reader replaying it would stop there and never see the continuation."""
    try:
        _redis().delete(events_key(run_id), cancel_key(run_id))
    except Exception:
        logger.warning("assistant run %s: could not reset events", run_id, exc_info=True)


def release_claim(run_id: str) -> None:
    """Free the claim once a run has stopped, so a confirmed action can resume it."""
    try:
        _redis().delete(claim_key(run_id))
    except Exception:
        logger.warning("assistant run %s: could not release claim", run_id, exc_info=True)


async def tail(
    run_id: str,
    *,
    after: str | None,
    run_status: Callable[[], Awaitable[str | None]],
    block_ms: int = 15_000,
    max_seconds: float = 1800.0,
) -> AsyncIterator[tuple[str | None, str, dict]]:
    """Yield ``(event_id, event, payload)`` for a run, starting after ``after``
    (or from the first event). Ends after ``done``.

    When nothing arrives for ``block_ms`` it asks the database how the run is
    doing: a run that already stopped without a visible ``done`` (its events
    expired, or its worker died) gets a synthesized ``done`` carrying the real
    status, so a reader never waits forever. Otherwise it yields a ``ping`` so
    proxies keep the connection open.
    """
    import redis.asyncio as aioredis

    client = aioredis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=block_ms / 1000 + 10
    )
    key = events_key(run_id)
    last = after or "0-0"
    deadline = time.monotonic() + max_seconds
    try:
        while True:
            response = await client.xread({key: last}, count=200, block=block_ms)
            if response:
                for _stream, entries in response:
                    for entry_id, fields in entries:
                        last = _text(entry_id)
                        event = _text(fields.get(b"event", b"message"))
                        try:
                            payload = json.loads(_text(fields.get(b"data", b"{}")))
                        except ValueError:
                            payload = {}
                        yield last, event, payload
                        if event == TERMINAL_EVENT:
                            return
                continue
            status = await run_status()
            if status != "running" or time.monotonic() > deadline:
                yield None, TERMINAL_EVENT, {"assistant_run_id": run_id, "run_status": status}
                return
            yield None, "ping", {}
    finally:
        await client.aclose()
