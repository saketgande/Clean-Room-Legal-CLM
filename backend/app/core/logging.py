import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

# LB/container probes hit these every few seconds. Logging them buries real
# traffic and fills RequestLog with rows nobody audits.
PROBE_PATHS = frozenset({"/healthz", "/readyz"})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "user_id",
            "org_id",
            "route",
            "method",
            "status_code",
            "latency_ms",
            "error_class",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _drop_probe_access_logs(record: logging.LogRecord) -> bool:
    # uvicorn.access formats as: '%s - "%s %s HTTP/%s" %d' with args
    # (client, method, path, version, status). Match on the path arg so we
    # don't string-search the whole line.
    args = record.args
    return not (isinstance(args, tuple) and len(args) >= 3 and args[2] in PROBE_PATHS)


def configure_logging() -> None:
    level = logging.DEBUG if settings.verbose_debug_logging else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").addFilter(_drop_probe_access_logs)
