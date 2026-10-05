"""Reading an answer's events from Redis (app.assistant.run_events.tail).

No Redis needed: a fake async client stands in for redis.asyncio."""

import asyncio

import redis.asyncio as aioredis

from app.assistant import run_events
from app.assistant.routes import _sse


class FakeAsyncRedis:
    def __init__(self, batches):
        self.batches = list(batches)
        self.reads: list[str] = []
        self.closed = False

    async def xread(self, streams, count, block):
        (last,) = streams.values()
        self.reads.append(last)
        return self.batches.pop(0) if self.batches else None

    async def aclose(self):
        self.closed = True


def _entry(entry_id, event, data="{}"):
    return (entry_id.encode(), {b"event": event.encode(), b"data": data.encode()})


def _collect(fake, monkeypatch, *, after=None, status="running"):
    monkeypatch.setattr(aioredis.Redis, "from_url", staticmethod(lambda *a, **k: fake))

    async def run_status():
        return status

    async def go():
        return [item async for item in run_events.tail("r1", after=after, run_status=run_status, block_ms=1)]

    return asyncio.run(go())


def test_replays_events_in_order_and_stops_at_done(monkeypatch):
    fake = FakeAsyncRedis([
        [(b"k", [_entry("1-0", "message_delta", '{"text": "Hi"}'), _entry("2-0", "done", '{"run_status": "succeeded"}')])],
    ])
    out = _collect(fake, monkeypatch)
    assert out == [("1-0", "message_delta", {"text": "Hi"}), ("2-0", "done", {"run_status": "succeeded"})]
    assert fake.reads == ["0-0"] and fake.closed


def test_a_reconnect_continues_after_the_last_event_seen(monkeypatch):
    fake = FakeAsyncRedis([[(b"k", [_entry("7-0", "done")])]])
    _collect(fake, monkeypatch, after="6-0")
    assert fake.reads == ["6-0"]


def test_a_quiet_running_answer_gets_keep_alive_pings(monkeypatch):
    fake = FakeAsyncRedis([None, [(b"k", [_entry("1-0", "done")])]])
    out = _collect(fake, monkeypatch)
    assert [e for _, e, _ in out] == ["ping", "done"]


def test_a_run_that_already_stopped_ends_even_without_its_events(monkeypatch):
    """Events expire after an hour, and a dead worker never wrote 'done': the
    reader asks the database and ends with the real status."""
    fake = FakeAsyncRedis([])
    out = _collect(fake, monkeypatch, status="interrupted")
    assert out == [(None, "done", {"assistant_run_id": "r1", "run_status": "interrupted"})]


def test_sse_frames_carry_the_event_id_for_reconnects():
    assert _sse("done", {"a": 1}, event_id="5-0") == 'id: 5-0\nevent: done\ndata: {"a": 1}\n\n'
    assert _sse("done", {"a": 1}) == 'event: done\ndata: {"a": 1}\n\n'
