# F-08 — Tool exception logging + timeline + structured error code (High)

**Agent 2 finding:** Each tool exception inside the streaming loop is caught with `except Exception as exc:` and turned into `{"error": str(exc), "tool_name": tool_name}` re-fed to Claude. The exception is not logged. No traceback. No alerting. No audit entry. Bugs hide forever.

## Headline change
- New helper module exposes `sanitize_exception_message(...)` and `classify_tool_exception(exc, tool_name) -> ToolExceptionPayload`.
- The streaming generator (F-01 file `ai_controller_stream_assistant_run.py`) now calls `self._classify_tool_exception(...)` on every tool failure, then:
  1. `logger.exception("assistant.tool.failed", extra={tool_name, assistant_run_id, session_id, iteration, error_class})`.
  2. Writes a `resource_timeline_event` row of type `assistant.tool.failed` with `tool_name`, `error_code`, `message`.
  3. Re-feeds Claude with `{"tool_name", "error_code", "message", "error_class"}` instead of free-form `str(exc)`.
- Sanitizer strips file paths and traceback fragments so a tool exception cannot exfiltrate internal paths into the conversation log.

## Cross-cutting dependencies
- Depends on **CC-4** because the `resource_timeline_event` write happens on the generator-scoped DB session.

## Agent 3 done-conditions met
- `tests/test_F08_tool_logging.py::test_tool_exception_is_logged` — a tool that raises produces a `logger.exception` call.
- `tests/test_F08_tool_logging.py::test_tool_exception_writes_timeline` — same exception produces a `resource_timeline_event` row.
- `tests/test_F08_tool_logging.py::test_tool_exception_sanitized` — exception messages with embedded file paths get redacted to `<path>` in the model-facing payload.

## Files
- `ai_controller_tool_exception.py` — `sanitize_exception_message`, `classify_tool_exception`, `ToolExceptionPayload` TypedDict.
- The actual wiring into the generator lives in `F01_sse_session_lifecycle/ai_controller_stream_assistant_run.py`.

## Tests
- `proposals/audit_2026-06-02/tests/test_F08_tool_logging.py`
