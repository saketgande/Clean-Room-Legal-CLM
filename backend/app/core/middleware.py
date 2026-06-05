import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings
from app.core.request_log_queue import enqueue as enqueue_request_log
from app.core.sanitize import parse_sensitive_keys, redact_query_string

logger = logging.getLogger("app.requests")

# Parse the sensitive-key list once at import time. Settings is immutable
# in-process so we don't need to re-parse on every request.
_SENSITIVE_QUERY_KEYS = parse_sensitive_keys(settings.request_log_sensitive_query_keys)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        error_class: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            error_class = exc.__class__.__name__
            raise
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            current_user = getattr(request.state, "current_user", None)

            def _attr(obj: object, name: str) -> str | None:
                # Observability must never crash a request. A committed-then-
                # expired ORM instance read here raises DetachedInstanceError
                # (not AttributeError), which getattr's default won't catch.
                try:
                    return getattr(obj, name, None)
                except Exception:
                    return None

            user_id = _attr(current_user, "id")
            org_id = _attr(current_user, "org_id")
            logger.info(
                "request.completed",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "org_id": org_id,
                    "method": request.method,
                    "route": str(request.url.path),
                    "status_code": status_code,
                    "latency_ms": round(latency_ms, 2),
                    "error_class": error_class,
                },
            )
            raw_query = str(request.url.query) if request.url.query else None
            if raw_query and settings.request_log_redact_query:
                logged_query = redact_query_string(raw_query, sensitive_keys=_SENSITIVE_QUERY_KEYS)
            else:
                logged_query = raw_query
            # Defer the DB write to the batched background writer. The previous
            # shape opened a fresh session and committed inline on every
            # request — one extra DB round-trip per API call. The writer
            # handles its own session and falls back to inline write on
            # queue overflow.
            enqueue_request_log(
                {
                    "request_id": request_id,
                    "user_id": user_id,
                    "org_id": org_id,
                    "method": request.method,
                    "route": str(request.url.path),
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "error_class": error_class,
                    "request_metadata": {"query": logged_query},
                }
            )
            if "response" in locals():
                response.headers["X-Request-ID"] = request_id


# Static header set applied to every response. Computed once at import time —
# Settings is immutable in-process, so HSTS inclusion is decided here.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
if settings.force_https:
    # Two years, subdomains, and preload-list eligible. Only meaningful over
    # HTTPS, so gated on the same flag that turns on the redirect middleware.
    _SECURITY_HEADERS["Strict-Transport-Security"] = (
        "max-age=63072000; includeSubDomains; preload"
    )

# Locked-down CSP for JSON API responses (no document/script context at all).
_API_CSP = "default-src 'none'; frame-ancestors 'none'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every response.

    Header values are static, so we apply the precomputed map and only special-
    case the API prefix, which gets a strict Content-Security-Policy on top.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.path.startswith(settings.api_v1_prefix):
            response.headers.setdefault("Content-Security-Policy", _API_CSP)
        return response
