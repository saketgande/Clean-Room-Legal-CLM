"""Per-org daily Claude token cap.

A single Redis counter per ``(org, UTC date)`` accumulates the total tokens an
org has spent on Claude today. ``enforce_daily_token_cap`` is called *before*
each Claude request and rejects with HTTP 429 once the counter has reached the
configured cap; ``record_token_usage`` is called *after* a successful call to
add that call's actual token total to the counter.

Design choices:
  * **Fail open.** Redis is an enforcement convenience, not the system of
    record (every call is also logged + metered in Postgres). A Redis blip must
    not take down all AI, so any Redis error here is logged and swallowed — the
    call proceeds. Failing closed would convert a cache outage into a full AI
    outage.
  * **No-op when the cap is <= 0.** Treated as "unlimited" so local/dev and the
    test suite are unaffected unless an operator sets a positive cap.
  * **Short-lived client.** Mirrors ``app.debug.routes`` — a lazily imported,
    timeout-bounded client so a missing/parked Redis never affects import time
    or latency beyond the small connect/socket timeout.
"""

import logging
from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

# Counter lives for two days so the previous UTC day rolls off on its own without
# a sweeper; the key already encodes the date so this is just garbage collection.
_KEY_TTL_SECONDS = 60 * 60 * 24 * 2


def _daily_key(org_id: str) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"ai:token_cap:{org_id}:{today}"


def _redis_client():
    """Return a short-lived, timeout-bounded Redis client (or raise)."""
    import redis

    return redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )


def enforce_daily_token_cap(org_id: str) -> None:
    """Reject (HTTP 429) when ``org_id`` has already spent its daily token cap.

    Called before each Claude request. No-op when the cap is <= 0. Fails open on
    any Redis error so a cache outage never blocks AI.
    """
    cap = settings.claude_daily_token_cap_per_org
    if cap <= 0:
        return
    try:
        client = _redis_client()
        try:
            spent_raw = client.get(_daily_key(org_id))
        finally:
            client.close()
    except Exception:  # noqa: BLE001 — fail open: never let Redis break AI
        logger.warning("ai token cap check skipped (redis unavailable)", exc_info=True)
        return
    spent = int(spent_raw) if spent_raw is not None else 0
    if spent >= cap:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Daily AI token limit reached for this organization. Try again tomorrow.",
        )


def record_token_usage(org_id: str, total_tokens: int | None) -> None:
    """Add ``total_tokens`` to the org's daily counter via Redis INCRBY.

    Called after a Claude call completes. No-op when the cap is disabled (<= 0)
    or there is nothing to record. Fails open on any Redis error.
    """
    if settings.claude_daily_token_cap_per_org <= 0:
        return
    if not total_tokens or total_tokens <= 0:
        return
    try:
        client = _redis_client()
        try:
            key = _daily_key(org_id)
            client.incrby(key, int(total_tokens))
            client.expire(key, _KEY_TTL_SECONDS)
        finally:
            client.close()
    except Exception:  # noqa: BLE001 — fail open: accounting is best-effort
        logger.warning("ai token cap accounting skipped (redis unavailable)", exc_info=True)
