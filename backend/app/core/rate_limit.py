"""Shared slowapi rate-limiter wiring.

The limiter is keyed on remote IP. Hosts behind a load balancer should set
``X-Forwarded-For`` and trust it via ``ProxyHeadersMiddleware`` (uvicorn
``--proxy-headers``) so the limiter sees the real client.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings


limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.rate_limit_enabled,
    default_limits=[],  # opt-in per endpoint
    headers_enabled=True,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests — slow down and retry shortly.",
            "limit": str(exc.detail) if getattr(exc, "detail", None) else None,
        },
    )
