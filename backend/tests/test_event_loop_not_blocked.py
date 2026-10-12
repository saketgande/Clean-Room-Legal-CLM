"""PERF-01: CPU-bound parsing and sync-over-async bridges must not freeze the event
loop that every other request shares."""

import asyncio
import concurrent.futures
import time

import pytest

from app.contract_files import service
from app.contract_files.text_extraction import TextExtractionResult
from app.integrations import claude


def test_document_parsing_runs_off_the_event_loop(monkeypatch):
    def slow_parse(content, *, mime_type, filename):
        time.sleep(0.3)  # stands in for parsing a large PDF
        return TextExtractionResult(text="hello", method="pdf_text", quality_score=0.9)

    monkeypatch.setattr(service, "extract_text", slow_parse)

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        result = await service._resolve_extracted_text(content=b"%PDF", mime_type="application/pdf", filename="big.pdf")
        task.cancel()
        return result, ticks

    result, ticks = asyncio.run(scenario())
    assert result.text == "hello"
    assert ticks >= 10, "the event loop was blocked while the document was parsed"


def test_the_sync_over_async_bridge_times_out_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(claude, "_BLOCKING_CALL_TIMEOUT_SECONDS", 0.2)

    async def inside_a_running_loop():
        async def slow():
            await asyncio.sleep(1)

        with pytest.raises(concurrent.futures.TimeoutError):
            claude.run_coro_blocking(lambda: slow())

    asyncio.run(inside_a_running_loop())
