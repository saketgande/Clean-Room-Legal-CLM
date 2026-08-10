import re
from typing import Any

# Keys whose string values are document/contract bodies — never persist these
# verbatim in an audit/log row; replace with a length-only placeholder.
SENSITIVE_KEYS = {"contract_text", "text", "raw_text", "document_text", "content"}
SECRET_KEYS = {
    "access_token",
    "api_key",
    "external_share_token",
    "passcode",
    "password",
    "refresh_token",
    "secret",
    "setup_token",
    "token",
}

# Inline PII patterns scrubbed from any string we persist. These are deliberately
# conservative (low false-positive) so audit payloads stay useful:
#   * email addresses
#   * 16+ consecutive digits (PANs / card numbers), allowing space/hyphen groups
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_LONG_DIGITS_RE = re.compile(r"\b(?:\d[ -]?){16,}\b")

_EMAIL_PLACEHOLDER = "<redacted-email>"
_DIGITS_PLACEHOLDER = "<redacted-number>"


def _scrub_text(value: str) -> str:
    """Replace inline PII (emails, long digit runs) inside a single string."""
    value = _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, value)
    value = _LONG_DIGITS_RE.sub(_DIGITS_PLACEHOLDER, value)
    return value


def redact_ai_payload(value: Any, *, max_text_chars: int = 500) -> Any:
    """Recursively scrub a value before it is persisted to an AI audit/log row.

    Rules (applied depth-first so nested tool arguments/results are covered):
      * a dict key in ``SENSITIVE_KEYS`` whose value is a string → length-only
        placeholder (the body is never stored verbatim);
      * any string longer than ``max_text_chars`` → truncated with a length tag;
      * every surviving string → inline PII (email / 16+ digit number) scrubbed.

    Non-container scalars (int/float/bool/None) pass through unchanged. The input
    is never mutated in place — a new structure is returned.
    """
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_name = key.lower() if isinstance(key, str) else ""
            if key_name in SECRET_KEYS:
                redacted[key] = "<redacted-secret>"
            elif key_name in SENSITIVE_KEYS and isinstance(item, str):
                redacted[key] = f"<redacted text length={len(item)}>"
            else:
                redacted[key] = redact_ai_payload(item, max_text_chars=max_text_chars)
        return redacted
    if isinstance(value, list):
        return [redact_ai_payload(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, tuple):
        return [redact_ai_payload(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, str):
        if len(value) > max_text_chars:
            value = f"{value[:max_text_chars]}<truncated length={len(value)}>"
        return _scrub_text(value)
    return value
