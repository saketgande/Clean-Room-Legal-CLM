"""F-15 rewrite — generalized stuck-row reaper for AssistantToolCall.

Replaces the ad-hoc ``_reap_stuck_jobs`` at
``backend/app/jobs/routes.py`` and the parallel reconciler at
``backend/app/tabular_review/routes.py``. Agent 2 F-15: tool calls
left in ``RUNNING`` after a permission/validation/flush error have no
reaper, so they pile up forever in a misleading status.

This module exposes:
- ``reap_stuck_rows(...)`` — generic helper that updates rows older
  than ``ttl`` from ``status_running`` to ``status_failed`` with an
  ``error_message`` flag. Reusable across ``JobRun``,
  ``AssistantToolCall``, and ``TabularReviewCell``.
- ``reap_stuck_assistant_tool_calls(...)`` — concrete call for the
  new finding F-15.

Intended location: ``backend/app/jobs/reaper.py``. Hook it from the
existing periodic context (Sprint 2 follows up with a tidy Celery
beat or background-thread caller).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.assistant.models import AssistantToolCall
from app.core.database import utcnow
from app.core.enums import AssistantToolCallStatus, JobStatus

_logger = logging.getLogger(__name__)

DEFAULT_TOOL_CALL_TTL_MINUTES: int = 20


def reap_stuck_rows(
    db: Session,
    *,
    model: Any,
    ttl: timedelta,
    status_col: Any,
    status_running: str,
    status_failed: str,
    started_at_col: Any,
    error_message_col: Any | None,
    error_message_value: str,
) -> int:
    """Flip stuck-RUNNING rows to FAILED; return the affected count."""
    cutoff = utcnow() - ttl
    values: dict[str, Any] = {status_col.key: status_failed}
    if error_message_col is not None:
        values[error_message_col.key] = error_message_value
    stmt = (
        update(model)
        .where(
            status_col == status_running,
            started_at_col.isnot(None),
            started_at_col < cutoff,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    result = db.execute(stmt)
    affected = int(result.rowcount or 0)
    if affected:
        _logger.warning(
            "reaper.rows_failed",
            extra={
                "model": getattr(model, "__name__", str(model)),
                "affected": affected,
                "ttl_minutes": ttl.total_seconds() / 60,
            },
        )
    db.commit()
    return affected


def reap_stuck_assistant_tool_calls(
    db: Session,
    *,
    ttl_minutes: int = DEFAULT_TOOL_CALL_TTL_MINUTES,
) -> int:
    """Flip ``AssistantToolCall`` rows older than TTL from RUNNING to FAILED."""
    return reap_stuck_rows(
        db,
        model=AssistantToolCall,
        ttl=timedelta(minutes=ttl_minutes),
        status_col=AssistantToolCall.status,
        status_running=AssistantToolCallStatus.RUNNING.value,
        status_failed=AssistantToolCallStatus.FAILED.value,
        started_at_col=AssistantToolCall.started_at,
        error_message_col=AssistantToolCall.error_message,
        error_message_value=f"stuck_running_reaped_after_{ttl_minutes}m",
    )


def reap_stuck_jobs(
    db: Session,
    *,
    ttl_minutes: int = DEFAULT_TOOL_CALL_TTL_MINUTES,
) -> int:
    """Refactored ``_reap_stuck_jobs`` (was inline in ``jobs/routes.py``)."""
    from app.jobs.models import JobRun

    return reap_stuck_rows(
        db,
        model=JobRun,
        ttl=timedelta(minutes=ttl_minutes),
        status_col=JobRun.status,
        status_running=JobStatus.RUNNING.value,
        status_failed=JobStatus.FAILED.value,
        started_at_col=JobRun.started_at,
        error_message_col=JobRun.error_message,
        error_message_value=f"stuck_running_reaped_after_{ttl_minutes}m",
    )
