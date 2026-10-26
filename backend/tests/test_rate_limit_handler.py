"""The 429 handler must tell agents how long to back off.

Guards the handler regressing to a bare body: MCP/API-key callers rely on
`Retry-After` / `retry_after_seconds` rather than guessing a delay.
"""

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.rate_limit import rate_limit_exceeded_handler


def _app() -> FastAPI:
    # In-memory limiter: the handler is under test, not Redis.
    local = Limiter(key_func=get_remote_address, headers_enabled=True)
    app = FastAPI()
    app.state.limiter = local
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    @app.get("/x")
    @local.limit("1/minute")
    def x(request: Request, response: Response):
        return {"ok": True}

    return app


def test_429_has_retry_after_header_and_body_seconds():
    client = TestClient(_app())
    assert client.get("/x").status_code == 200
    response = client.get("/x")
    assert response.status_code == 429
    header = int(response.headers["Retry-After"])
    body = response.json()
    assert 0 < header <= 60
    assert body["retry_after_seconds"] == header
    # Existing keys are preserved for current clients.
    assert body["detail"].startswith("Too many requests")
    assert "1 per 1 minute" in body["limit"]


def test_retry_after_comes_from_the_slowapi_private_hook():
    """The handler leans on slowapi's private `_inject_headers`; its guard swallows
    errors, so without this a slowapi upgrade would silently drop Retry-After."""
    assert callable(getattr(Limiter, "_inject_headers", None))
    client = TestClient(_app())
    client.get("/x")
    assert "Retry-After" in client.get("/x").headers


def test_handler_degrades_without_view_limit():
    """No slowapi state on the request: still a 429, retry_after_seconds null."""
    request = Request({"type": "http", "headers": [], "app": FastAPI()})
    response = rate_limit_exceeded_handler(request, _exc())
    assert response.status_code == 429
    assert b'"retry_after_seconds":null' in response.body.replace(b" ", b"")


def _exc() -> RateLimitExceeded:
    class _Limit:
        error_message = None
        limit = "1 per 1 minute"

        def __str__(self):
            return "1 per 1 minute"

    return RateLimitExceeded(_Limit())
