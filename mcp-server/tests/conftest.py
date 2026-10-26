"""Shared fixtures: a fake Aegis platform behind httpx.MockTransport. No test here (or in the
tests that build on this) can reach a real network."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from aegis_intake_mcp.aegis_client import AegisClient
from aegis_intake_mcp.config import Config

API = "http://backend:8000/api/v1"
KEY = "Bearer clm_" + "a" * 32

Reply = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


class FakePlatform:
    """Routes are matched on (method, path under /api/v1). Each route holds a queue of
    replies; the last one repeats. A reply is a Response, an exception to raise (e.g.
    httpx.ConnectTimeout), or a callable taking the request."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self._routes: dict[tuple[str, str], list[Reply]] = {}

    def on(
        self, method: str, path: str, *replies: Reply | None, status: int = 200, json: Any = None
    ) -> FakePlatform:
        queue = [r for r in replies if r is not None]
        if not queue:
            queue = [httpx.Response(status, json=json)]
        self._routes[(method.upper(), path)] = queue
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path.removeprefix("/api/v1")
        queue = self._routes.get((request.method, path))
        if queue is None:
            return httpx.Response(404, json={"detail": "no such fake route"})
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply(request)
        return reply

    def calls_to(self, method: str, path: str) -> list[httpx.Request]:
        return [
            c
            for c in self.calls
            if c.method == method and c.url.path.removeprefix("/api/v1") == path
        ]


@pytest.fixture
def config() -> Config:
    return Config(api_base_url=API)


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()


@pytest.fixture
async def client(config: Config, platform: FakePlatform):
    # Zero backoff so retry tests do not sleep; the production default is 1 s.
    c = AegisClient(
        config, transport=httpx.MockTransport(platform.handler), retry_backoff_seconds=0
    )
    yield c
    await c.aclose()
