"""Liveness vs readiness (FR-53, AC-33): `/healthz` says the process is up; `/readyz` is 200 only
while the platform's own `/healthz` answers, so a platform outage shows as "not ready" instead
of every tool failing one by one."""

from __future__ import annotations

import httpx
import pytest

from aegis_intake_mcp.config import Config
from aegis_intake_mcp.server import create_app

HEALTH = "http://backend:8000/healthz"


def _app(handler):
    return create_app(Config(health_url=HEALTH), health_transport=httpx.MockTransport(handler))


async def _get(app, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8001"
    ) as c:
        return await c.get(path)


async def test_healthz_is_ok_without_the_platform():
    """Liveness must not depend on the platform, or an outage would restart a healthy process."""

    def down(request):
        raise httpx.ConnectError("platform down")

    response = await _get(_app(down), "/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_200_when_platform_healthy():
    seen: list[str] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    response = await _get(_app(ok), "/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert seen == [HEALTH]  # the one allowed probe target


@pytest.mark.parametrize(
    "reply",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.Response(503),
        httpx.Response(500),
    ],
)
async def test_readyz_503_when_platform_down_or_slow(reply):
    """Down, timing out or unhealthy all read as not ready with a short reason."""

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(reply, Exception):
            raise reply
        return reply

    response = await _get(_app(handler), "/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "backend_unavailable"}
