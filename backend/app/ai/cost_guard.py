"""Per-org daily Claude token cap.

A single Redis counter per ``(org, UTC date)`` holds the tokens an org has spent
on Claude today. Metering lives inside the Claude client (``ClaudeClient``), so
no call path can skip it:

  * ``reserve_tokens`` runs *before* each request. One atomic Lua script rejects
    with HTTP 429 once the counter has reached the cap, and otherwise adds a
    reservation (an estimate of the call's tokens). Checking and reserving are a
    single step, so many calls at once can't all pass on the same stale reading.
  * ``settle_tokens`` runs *after* the request and swaps the reservation for the
    call's real token total (zero when the call failed).

Design choices:
  * **Fail open.** Redis is an enforcement convenience, not the system of
    record (every call is also logged + metered in Postgres). A Redis blip must
    not take down all AI, so any Redis error here is logged and swallowed — the
    call proceeds. Failing closed would convert a cache outage into a full AI
    outage.
  * **No-op when the cap is <= 0.** Treated as "unlimited" so local/dev and the
    test suite are unaffected unless an operator sets a positive cap.
  * **One pooled client.** Created lazily on first use and reused, with short
    connect/socket timeouts so a missing Redis never affects import time and
    adds at most a small delay.
"""

import logging
from datetime import UTC, datetime
from functools import lru_cache

from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

# Counter lives for two days so the previous UTC day rolls off on its own without
# a sweeper; the key already encodes the date so this is just garbage collection.
_KEY_TTL_SECONDS = 60 * 60 * 24 * 2

# KEYS[1] = counter, ARGV = cap, tokens to reserve, TTL.
# Returns -1 when the cap is already spent; otherwise reserves and returns 1.
_RESERVE_SCRIPT = """
local spent = tonumber(redis.call('GET', KEYS[1]) or '0')
if spent >= tonumber(ARGV[1]) then
  return -1
end
redis.call('INCRBY', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""


def _daily_key(org_id: str) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"ai:token_cap:{org_id}:{today}"


@lru_cache(maxsize=1)
def _redis_client():
    """One shared, timeout-bounded Redis client; its connection pool is reused."""
    import redis

    return redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )


def reserve_tokens(org_id: str | None, estimate: int) -> tuple[str, int] | None:
    """Reject (HTTP 429) when ``org_id`` has already spent its daily token cap;
    otherwise reserve ``estimate`` tokens against it.

    Returns the reservation to hand to ``settle_tokens``, or None when nothing
    was reserved (no org, cap disabled, or Redis unavailable).
    """
    cap = settings.claude_daily_token_cap_per_org
    if not org_id or cap <= 0:
        return None
    key = _daily_key(org_id)
    reserved = max(int(estimate), 0)
    try:
        allowed = _redis_client().eval(_RESERVE_SCRIPT, 1, key, cap, reserved, _KEY_TTL_SECONDS)
    except Exception:
        logger.warning("ai token cap check skipped (redis unavailable)", exc_info=True)
        return None
    if int(allowed) < 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Daily AI token limit reached for this organization. Try again tomorrow.",
        )
    return key, reserved


def settle_tokens(reservation: tuple[str, int] | None, actual_tokens: int | None) -> None:
    """Replace a reservation with the call's real token total (0 if the call failed).

    Uses the reservation's own key, so a call that crosses midnight UTC settles
    against the day it was reserved on.
    """
    if reservation is None:
        return
    key, reserved = reservation
    delta = max(int(actual_tokens or 0), 0) - reserved
    if delta == 0:
        return
    try:
        _redis_client().incrby(key, delta)
    except Exception:
        logger.warning("ai token cap accounting skipped (redis unavailable)", exc_info=True)
