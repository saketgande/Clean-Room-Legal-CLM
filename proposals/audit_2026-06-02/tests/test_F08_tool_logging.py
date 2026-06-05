"""F-08 tool exception logging + sanitization tests."""

from __future__ import annotations

from fastapi import HTTPException


def test_classify_http_exception_uses_status_code():
    """``HTTPException`` becomes ``error_code = http_<status>``."""
    from proposals.audit_2026_06_02.rewrites.F08_tool_exception_logging import (  # type: ignore[import-not-found]
        ai_controller_tool_exception as mod,
    )

    payload = mod.classify_tool_exception(
        exc=HTTPException(status_code=422, detail="bad input"),
        tool_name="edit_contract",
    )
    assert payload["error_code"] == "http_422"
    assert payload["error_class"] == "HTTPException"


def test_classify_falls_back_to_internal():
    """Unknown exception classes map to the ``internal`` error code."""
    from proposals.audit_2026_06_02.rewrites.F08_tool_exception_logging import (  # type: ignore[import-not-found]
        ai_controller_tool_exception as mod,
    )

    class WeirdError(Exception):
        pass

    payload = mod.classify_tool_exception(
        exc=WeirdError("something odd"), tool_name="read_contract"
    )
    assert payload["error_code"] == "internal"
    assert payload["error_class"] == "WeirdError"


def test_sanitize_strips_file_paths():
    """File paths and traceback fragments are scrubbed from the message."""
    from proposals.audit_2026_06_02.rewrites.F08_tool_exception_logging import (  # type: ignore[import-not-found]
        ai_controller_tool_exception as mod,
    )

    raw = "ValueError in /Users/x/secret_path.py:42: cannot find 'foo'"
    cleaned = mod.sanitize_exception_message(raw)
    assert "/Users/x/secret_path.py" not in cleaned
    assert "<path>" in cleaned


def test_sanitize_truncates_long_messages():
    """Excessively long exception messages are truncated with an ellipsis."""
    from proposals.audit_2026_06_02.rewrites.F08_tool_exception_logging import (  # type: ignore[import-not-found]
        ai_controller_tool_exception as mod,
    )

    raw = "x" * 1_000
    cleaned = mod.sanitize_exception_message(raw, max_length=120)
    assert len(cleaned) <= 120
    assert cleaned.endswith("...")
