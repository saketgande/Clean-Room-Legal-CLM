"""Shared slowapi rate-limiter wiring.

The limiter keys on the authenticated user id when one is available and falls
back to the remote IP otherwise — so an authenticated caller gets a stable
bucket regardless of source IP, while unauthenticated traffic is still pinned
per-IP. Hosts behind a load balancer should set ``X-Forwarded-For`` and trust
it via ``ProxyHeadersMiddleware`` (uvicorn ``--proxy-headers``) so the IP
fallback sees the real client.

Counters live in Redis (``storage_uri``) so the limits are enforced across all
worker processes instead of per-process in-memory state.
"""

import json

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings


def rate_limit_key(request: Request) -> str:
    """Prefer the authenticated user id, fall back to the remote address.

    ``request.state.current_user`` is set by ``get_current_user``; reading
    ``.id`` off a committed-then-expired ORM instance can raise
    (DetachedInstanceError), so we guard it and degrade to the IP key.
    """
    current_user = getattr(request.state, "current_user", None)
    if current_user is not None:
        try:
            user_id = getattr(current_user, "id", None)
        except Exception:
            user_id = None
        if user_id:
            return f"user:{user_id}"
    return get_remote_address(request)


limiter = Limiter(
    key_func=rate_limit_key,
    enabled=settings.rate_limit_enabled,
    # Shared counters across workers; falls back to per-endpoint opt-in limits.
    storage_uri=settings.redis_url,
    default_limits=[],  # opt-in per endpoint
    headers_enabled=True,
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    response = JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests — slow down and retry shortly.",
            "limit": str(exc.detail) if getattr(exc, "detail", None) else None,
            "retry_after_seconds": None,
        },
    )
    # Coupling: `_inject_headers` is slowapi-private (same call slowapi's own
    # handler makes). slowapi is pinned exactly (requirements.lock, 0.1.9), and
    # tests/test_rate_limit_handler.py fails if it vanishes or stops adding
    # Retry-After, so an upgrade cannot silently drop the header.
    # slowapi computes Retry-After from the window that was hit. Agents back off
    # on this, so a failure to read it must degrade to "no header", not a 500.
    try:
        app_limiter = getattr(request.app.state, "limiter", None) or limiter
        view_limit = getattr(request.state, "view_rate_limit", None)
        if view_limit is not None:
            response = app_limiter._inject_headers(response, view_limit)
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            response.body = json.dumps(
                {**json.loads(response.body), "retry_after_seconds": int(float(retry_after))},
                ensure_ascii=False,
            ).encode("utf-8")
            response.headers["content-length"] = str(len(response.body))
    except Exception:
        pass
    return response
