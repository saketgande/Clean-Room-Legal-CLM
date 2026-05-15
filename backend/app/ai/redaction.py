from typing import Any


SENSITIVE_KEYS = {"contract_text", "text", "raw_text", "document_text", "content"}


def redact_ai_payload(payload: dict[str, Any], *, max_text_chars: int = 500) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in SENSITIVE_KEYS and isinstance(value, str):
            redacted[key] = f"<redacted text length={len(value)}>"
        elif isinstance(value, str) and len(value) > max_text_chars:
            redacted[key] = f"{value[:max_text_chars]}<truncated length={len(value)}>"
        else:
            redacted[key] = value
    return redacted
