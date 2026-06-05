"""F-01 lifecycle tests — generator-scoped DB session + disconnect-stops-loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def test_streaming_session_opens_fresh_db_session(monkeypatch):
    """``streaming_session`` yields a Session distinct from the request's."""
    import asyncio

    from app.core.streaming import streaming_session

    fake_session = MagicMock(name="generator_db")
    fake_factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr("app.core.streaming.SessionLocal", fake_factory)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

    async def _drive() -> object:
        async with streaming_session(request=request) as ctx:
            assert ctx.db is fake_session
            return ctx.db

    result = asyncio.run(_drive())
    assert result is fake_session
    fake_session.commit.assert_called_once()
    fake_session.close.assert_called_once()


def test_streaming_session_rolls_back_on_exception(monkeypatch):
    """Exceptions inside the generator trigger ``rollback()``, not ``commit()``."""
    import asyncio

    from app.core.streaming import streaming_session

    fake_session = MagicMock(name="generator_db")
    monkeypatch.setattr(
        "app.core.streaming.SessionLocal", MagicMock(return_value=fake_session)
    )
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

    async def _drive() -> None:
        async with streaming_session(request=request):
            raise RuntimeError("kaboom")

    try:
        asyncio.run(_drive())
    except RuntimeError:
        pass
    fake_session.rollback.assert_called_once()
    fake_session.commit.assert_not_called()
    fake_session.close.assert_called_once()


def test_client_disconnect_stops_loop(monkeypatch):
    """The streaming context's disconnect check propagates ``ClientDisconnected``."""
    import asyncio

    from app.core.streaming import ClientDisconnected, StreamingContext, StreamingBudget

    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    ctx = StreamingContext(db=MagicMock(), request=request, budget=StreamingBudget())

    async def _drive() -> None:
        await ctx.check_disconnect()

    raised = False
    try:
        asyncio.run(_drive())
    except ClientDisconnected:
        raised = True
    assert raised
