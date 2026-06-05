"""F-15 stuck tool-call reaper tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock


def test_reaper_emits_update_statement():
    """``reap_stuck_assistant_tool_calls`` issues a single UPDATE statement."""
    from app.assistant.models import AssistantToolCall
    from app.core.enums import AssistantToolCallStatus
    from proposals.audit_2026_06_02.rewrites.F15_stuck_tool_calls_reaper import (  # type: ignore[import-not-found]
        ai_tool_runtime_stuck_reaper as mod,
    )

    db = MagicMock()
    result = MagicMock()
    result.rowcount = 3
    db.execute = MagicMock(return_value=result)
    db.commit = MagicMock()
    affected = mod.reap_stuck_assistant_tool_calls(db, ttl_minutes=20)
    assert affected == 3
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_reaper_handles_zero_rows():
    """No stuck rows → ``rowcount=0``, no warning, commit still happens."""
    from proposals.audit_2026_06_02.rewrites.F15_stuck_tool_calls_reaper import (  # type: ignore[import-not-found]
        ai_tool_runtime_stuck_reaper as mod,
    )

    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = MagicMock(return_value=result)
    db.commit = MagicMock()
    affected = mod.reap_stuck_assistant_tool_calls(db, ttl_minutes=20)
    assert affected == 0
    db.commit.assert_called_once()
