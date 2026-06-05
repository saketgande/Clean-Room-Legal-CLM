"""F-08 rewrite — structured tool exception handling for streaming generators.

Replaces the bare ``except Exception as exc`` blocks at
``backend/app/ai/controller.py:289-316`` and ``:557-583``. The original
caught everything, swallowed it into ``str(exc)``, and re-fed Claude
the stringified error with no log, no stack trace, no audit signal.

The new shape:
- ``logger.exception(...)`` with structured fields for the assistant
  run, session, iteration, tool name, and exception class.
- A ``resource_timeline_event`` row of type ``assistant.tool.failed``
  is written so the contract / session timeline reflects the failure.
- The error fed back to Claude is a structured dict with a stable
  ``error_code`` derived from the exception class — the model can
  reason about it without parsing free-form strings.
- ``str(exc)`` is sanitized: file paths and stack traces in the
  exception message are stripped before re-feeding (defense in depth
  against tool exceptions exposing internal paths to the conversation
  context).
"""

from __future__ import annotations

import logging
import re
from typing import TypedDict

from fastapi import HTTPException

_logger = logging.getLogger(__name__)


class ToolExceptionPayload(TypedDict):
    """The structured shape that gets re-fed to Claude on a tool failure."""

    error_code: str
    message: str
    error_class: str


_PATH_PATTERN = re.compile(r"(/[A-Za-z0-9_./-]+)+\.py(?::\d+)?")
_TRACEBACK_KEYWORDS = (
    "Traceback (most recent call last)",
    "File \"",
    "in <module>",
)


def sanitize_exception_message(message: str, *, max_length: int = 400) -> str:
    """Strip file paths, line numbers, and traceback fragments from a str(exc)."""
    if not message:
        return ""
    cleaned = _PATH_PATTERN.sub("<path>", message)
    for keyword in _TRACEBACK_KEYWORDS:
        if keyword in cleaned:
            cleaned = cleaned.split(keyword, 1)[0].strip()
            break
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1] + "..."
    return cleaned


def classify_tool_exception(*, exc: BaseException, tool_name: str) -> ToolExceptionPayload:
    """Map an exception to a stable error code suitable for the model context."""
    error_class = type(exc).__name__
    message = sanitize_exception_message(str(exc))
    if isinstance(exc, HTTPException):
        code = f"http_{exc.status_code}"
    elif isinstance(exc, PermissionError):
        code = "permission_denied"
    elif isinstance(exc, TimeoutError):
        code = "timeout"
    elif isinstance(exc, ValueError):
        code = "validation"
    elif isinstance(exc, LookupError):
        code = "not_found"
    elif isinstance(exc, ConnectionError):
        code = "connection_error"
    else:
        code = "internal"
    return ToolExceptionPayload(
        error_code=code,
        message=message,
        error_class=error_class,
    )
