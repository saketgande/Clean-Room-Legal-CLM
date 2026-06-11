"""Helpers for redacting sensitive values out of audit/request logs.

We pass query strings, headers and other ambient request data into durable
logs. URLs occasionally carry tokens, passcodes, share secrets and similar.
This module isolates that scrubbing so it has one place to live.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode


REDACTED = "***redacted***"


def redact_query_string(
    raw: str | None,
    *,
    sensitive_keys: set[str],
) -> str | None:
    """Return a query string with sensitive parameter values replaced.

    A non-key=value query (e.g. "abc&def") is treated as opaque and replaced
    in full by ``REDACTED`` to be safe — we never persist arbitrary unparsed
    blobs that might happen to contain secrets.
    """
    if not raw:
        return raw
    if "=" not in raw:
        return REDACTED
    pairs = parse_qsl(raw, keep_blank_values=True)
    # urlencode percent-encodes the asterisks in the marker, which makes the
    # audit log harder to skim. ``safe="*"`` keeps the marker readable while
    # still percent-encoding any actual sensitive values that contain reserved
    # characters.
    redacted: list[tuple[str, str]] = []
    for key, value in pairs:
        if key.lower() in sensitive_keys:
            redacted.append((key, REDACTED))
        else:
            redacted.append((key, value))
    return urlencode(redacted, doseq=True, safe="*")


def parse_sensitive_keys(csv: str) -> set[str]:
    return {item.strip().lower() for item in csv.split(",") if item.strip()}
