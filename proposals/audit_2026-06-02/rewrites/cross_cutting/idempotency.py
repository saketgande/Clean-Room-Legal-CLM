"""Cross-cutting decision CC-5 — structured idempotency-key builder.

Replaces the inline f-strings that bake ``utcnow().timestamp()`` into
manual-trigger keys at ``backend/app/obligations/routes.py:161`` and
``backend/app/ai/tool_runtime.py:1242``, plus the
three-reasons-three-jobs key at
``backend/app/jobs/tasks.py:216-251``. Resolves Agent 2 findings F-10
(timestamp keys defeat the unique constraint) and F-14
(``contract_brain_ingestion`` race from three siblings keyed by
``:reason``).

Intended location: ``backend/app/jobs/idempotency.py``. Manual triggers
debounce by minute bucket so a user double-clicking inside the same
bucket is deduped, but a deliberate re-extract one bucket later
proceeds.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal


JobTrigger = Literal["upload", "manual", "assistant", "auto"]

# Default debounce window for manual triggers (CC-5: "5-minute bucket").
DEFAULT_DEBOUNCE_WINDOW_MINUTES: int = 5


def minute_bucket(dt: datetime, *, window: int = DEFAULT_DEBOUNCE_WINDOW_MINUTES) -> str:
    """Return ``YYYYMMDDHHMM`` truncated to a ``window``-minute bucket."""
    if window <= 0:
        raise ValueError("window must be positive")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    minute = (dt.minute // window) * window
    truncated = dt.replace(minute=minute, second=0, microsecond=0)
    return truncated.strftime("%Y%m%d%H%M")


def build_debounce_token(
    *,
    user_id: str | None,
    now: datetime,
    window: int = DEFAULT_DEBOUNCE_WINDOW_MINUTES,
) -> str:
    """Build a debounce token: ``<user_id>:<minute_bucket>``."""
    actor = user_id or "anon"
    return f"{actor}:{minute_bucket(now, window=window)}"


def build_idempotency_key(
    job_type: str,
    *,
    version_id: str | None,
    snapshot_id: str | None,
    trigger: JobTrigger,
    debounce_token: str | None = None,
    extra: str | None = None,
) -> str:
    """Build a deterministic idempotency key for a JobRun."""
    if not job_type:
        raise ValueError("job_type is required")
    parts: list[str] = [job_type, version_id or "_no_version", snapshot_id or "_no_snapshot", trigger]
    if debounce_token:
        parts.append(debounce_token)
    if extra:
        parts.append(extra)
    return ":".join(parts)


def build_auto_brain_ingestion_key(
    *,
    version_id: str,
    snapshot_id: str | None,
) -> str:
    """Single canonical key for an auto-enqueued brain-ingestion job."""
    return build_idempotency_key(
        "contract_brain_ingestion",
        version_id=version_id,
        snapshot_id=snapshot_id,
        trigger="auto",
    )
