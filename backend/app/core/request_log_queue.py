"""Background batched writer for ``RequestLog`` rows.

The previous shape opened a fresh ``SessionLocal``, INSERTed one row, and
committed on every request — one extra DB round-trip per API call. This
module replaces that with an in-process queue drained by a daemon thread
that flushes in batches via ``bulk_insert_mappings``.

Trade-offs:

* Up to ``flush_interval_seconds`` of request-log rows may be lost on a hard
  crash. Request logs are observability data, not audit data — the audit log
  has its own durable write path; this is an acceptable trade.
* When the queue is full (writer can't keep up), incoming rows are dropped
  rather than blocking the request. The drop count is logged so an operator
  can size up the queue or the worker.
* The synchronous path is preserved behind a config flag for environments
  that want the old behaviour, and used automatically as a fallback when
  enqueue fails.
"""

from __future__ import annotations

import atexit
import logging
import queue
import threading
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal, new_uuid, utcnow
from app.core.models import RequestLog


logger = logging.getLogger(__name__)


_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue(
    maxsize=max(1, settings.request_log_queue_max_items)
)
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_DROPPED = 0
_DROPPED_LOCK = threading.Lock()


def _flush_sync(row: dict[str, Any]) -> None:
    """Write a single row inline. Used when async batching is disabled or as a
    fallback when enqueue fails (queue full and we don't want to lose the row)."""
    db = SessionLocal()
    try:
        db.add(RequestLog(**row))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("request_log sync write failed", exc_info=True)
    finally:
        db.close()


def _flush_batch(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    now = utcnow()
    for row in rows:
        # bulk_insert_mappings does NOT execute column defaults — populate
        # id/created_at/updated_at manually so the rows are valid.
        row.setdefault("id", new_uuid())
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
    db = SessionLocal()
    try:
        db.bulk_insert_mappings(RequestLog, rows)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("request_log batch flush failed (%d rows)", len(rows), exc_info=True)
    finally:
        db.close()


def _worker() -> None:
    interval = max(0.1, settings.request_log_flush_interval_seconds)
    batch_size = max(1, settings.request_log_batch_size)
    while not _STOP.is_set() or not _QUEUE.empty():
        batch: list[dict[str, Any]] = []
        try:
            batch.append(_QUEUE.get(timeout=interval))
        except queue.Empty:
            continue
        # Drain whatever else is already queued, up to batch_size.
        while len(batch) < batch_size:
            try:
                batch.append(_QUEUE.get_nowait())
            except queue.Empty:
                break
        _flush_batch(batch)


def enqueue(row: dict[str, Any]) -> None:
    """Submit a row for the batched writer; fall back to inline write on overflow.

    ``row`` must contain all RequestLog columns; ``id``/``created_at``/
    ``updated_at`` will be populated by the worker if absent.
    """
    if not settings.request_log_async_enabled:
        _flush_sync(row)
        return
    try:
        _QUEUE.put_nowait(row)
    except queue.Full:
        # Don't lose the row: write inline. Bump a counter so an operator can
        # see the writer is falling behind.
        global _DROPPED
        with _DROPPED_LOCK:
            _DROPPED += 1
            should_log = _DROPPED % 100 == 1
        if should_log:
            logger.warning(
                "request_log queue full — falling back to inline write (total=%d)",
                _DROPPED,
            )
        _flush_sync(row)


def start_writer() -> None:
    """Start the daemon thread if it's not already running. Safe to call twice."""
    global _THREAD
    if not settings.request_log_async_enabled:
        return
    if _THREAD is not None and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(
        target=_worker, name="request-log-writer", daemon=True
    )
    _THREAD.start()


def stop_writer(timeout: float = 5.0) -> None:
    """Signal the worker to exit after draining and wait for it.

    Registered via ``atexit`` so a clean interpreter shutdown still drains the
    queue. Called explicitly from the FastAPI shutdown hook for the graceful
    path under uvicorn.
    """
    _STOP.set()
    if _THREAD is not None and _THREAD.is_alive():
        _THREAD.join(timeout=timeout)


atexit.register(stop_writer)
