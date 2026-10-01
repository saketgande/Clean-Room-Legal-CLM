"""LLM-06: the daily token cap holds when many calls run at once, and every real
Claude request reserves before it is sent and settles to its true usage after."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai import cost_guard
from app.core.config import settings
from app.integrations import claude


@pytest.fixture
def live_redis(monkeypatch):
    client = cost_guard._redis_client()
    try:
        client.ping()
    except Exception:
        pytest.skip("Redis is not reachable")
    monkeypatch.setattr(settings, "claude_daily_token_cap_per_org", 100_000)
    org = f"test-org-{uuid.uuid4()}"
    yield client, org
    client.delete(cost_guard._daily_key(org))


def test_fifty_concurrent_calls_stay_within_the_cap(live_redis):
    client, org = live_redis

    def attempt(_):
        try:
            return cost_guard.reserve_tokens(org, 10_000)
        except HTTPException as exc:
            assert exc.status_code == 429
            return None

    with ThreadPoolExecutor(max_workers=50) as pool:
        granted = [r for r in pool.map(attempt, range(50)) if r is not None]
    # 10 reservations of 10,000 reach the 100,000 cap; the other 40 are refused.
    assert len(granted) == 10
    assert int(client.get(cost_guard._daily_key(org))) == 100_000


def test_settling_swaps_the_reservation_for_real_usage(live_redis):
    client, org = live_redis
    cost_guard.settle_tokens(cost_guard.reserve_tokens(org, 8_000), 1_234)
    assert int(client.get(cost_guard._daily_key(org))) == 1_234
    cost_guard.settle_tokens(cost_guard.reserve_tokens(org, 8_000), 0)  # a failed call costs nothing
    assert int(client.get(cost_guard._daily_key(org))) == 1_234


class _FakeStream:
    def __init__(self, usage=None, fail=False):
        self.usage, self.fail, self.request_id = usage or {}, fail, "req-1"

    async def __aenter__(self):
        if self.fail:
            raise RuntimeError("provider down")
        return self

    async def __aexit__(self, *_exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        return SimpleNamespace(to_dict=lambda: {"content": [], "usage": self.usage, "model": "m"})


def _wire(monkeypatch, stream, calls):
    monkeypatch.setattr(settings, "mock_claude", False)
    monkeypatch.setattr(settings, "claude_api_key", "test-key")
    monkeypatch.setattr(
        claude, "reserve_tokens", lambda org_id, estimate: calls.append(("reserve", org_id, estimate)) or ("key", estimate)
    )
    monkeypatch.setattr(claude, "settle_tokens", lambda reservation, used: calls.append(("settle", reservation, used)))
    monkeypatch.setattr(
        claude, "_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(stream=lambda **_p: stream))
    )


def test_client_reserves_before_the_request_and_settles_to_real_usage(monkeypatch):
    calls = []
    _wire(monkeypatch, _FakeStream({"input_tokens": 300, "output_tokens": 45}), calls)
    asyncio.run(claude.ClaudeClient().complete_text(
        org_id="org-A", system_prompt="s" * 400, user_prompt="u" * 400, max_tokens=500, temperature=0,
    ))
    (_, org, estimate), (_, reservation, used) = calls
    assert org == "org-A" and estimate >= 800 // 4 + 500
    assert reservation == ("key", estimate) and used == 345


def test_a_failed_stream_releases_its_reservation(monkeypatch):
    calls = []
    _wire(monkeypatch, _FakeStream(fail=True), calls)

    async def consume():
        async for _chunk in claude.ClaudeClient().stream_with_tools(
            org_id="org-A", system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=100, temperature=0,
        ):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(consume())
    assert calls[-1] == ("settle", ("key", calls[0][2]), 0)


def test_a_spent_cap_stops_the_request_before_it_is_sent(monkeypatch):
    sent = []
    _wire(monkeypatch, _FakeStream(), [])

    def refuse(_org_id, _estimate):
        raise HTTPException(429, "Daily AI token limit reached")

    monkeypatch.setattr(claude, "reserve_tokens", refuse)
    monkeypatch.setattr(
        claude, "_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(stream=lambda **p: sent.append(p)))
    )
    with pytest.raises(HTTPException):
        asyncio.run(claude.ClaudeClient().complete_text(
            org_id="org-A", system_prompt="s", user_prompt="u", max_tokens=100, temperature=0,
        ))
    assert sent == []
