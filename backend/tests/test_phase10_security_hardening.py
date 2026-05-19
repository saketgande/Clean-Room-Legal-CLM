"""Tests for the post-review security hardening pass.

Each test maps to one or more findings in CODE_REVIEW_2026-05-19.md.
"""

from __future__ import annotations

import pytest

from app.contract_files.service import _sniff_mime_type
from app.core.config import Settings, validate_runtime_settings
from app.core.sanitize import (
    REDACTED,
    parse_sensitive_keys,
    redact_query_string,
)
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# --- bcrypt pre-hashing (#11) ---------------------------------------------


def test_long_passwords_with_different_suffixes_hash_differently():
    """bcrypt silently truncates to 72 bytes. Without SHA-256 pre-hashing,
    two passwords that share the first 72 bytes hash identically.
    """
    base = "a" * 72
    h1 = hash_password(base + "tail-one")
    assert verify_password(base + "tail-one", h1)
    # Same first 72 bytes, different tail — must NOT verify against h1.
    assert not verify_password(base + "tail-TWO", h1)


# --- JWT revocation infra (#4) -------------------------------------------


def test_create_access_token_returns_payload_with_jti():
    token, payload = create_access_token("user-1", {"org_id": "org-1"})
    decoded = decode_access_token(token)
    assert payload["jti"] == decoded["jti"]
    assert payload["sub"] == "user-1"
    assert "iat" in payload


def test_decode_access_token_requires_jti():
    """A token without a jti must be rejected — we use jti for revocation."""
    import jwt
    from datetime import UTC, datetime, timedelta
    from app.core.config import settings as live_settings

    forged = jwt.encode(
        {
            "sub": "user-1",
            "typ": "access",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        live_settings.secret_key,
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        decode_access_token(forged)


# --- MIME spoofing protection (#6) ---------------------------------------


def test_mime_sniff_accepts_real_pdf():
    real_pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>endobj"
    assert _sniff_mime_type(real_pdf, "application/pdf") == "application/pdf"


def test_mime_sniff_rejects_fake_pdf():
    """A client claiming application/pdf with non-PDF bytes must 415."""
    from fastapi import HTTPException

    fake = b"Not actually a PDF, just a string"
    with pytest.raises(HTTPException) as exc_info:
        _sniff_mime_type(fake, "application/pdf")
    assert exc_info.value.status_code == 415


def test_mime_sniff_passes_through_text_plain():
    """text/* has no magic — accept the claim."""
    assert _sniff_mime_type(b"hello world", "text/plain") == "text/plain"


# --- Query-string redaction (#14) ----------------------------------------


def test_redact_passes_through_safe_query():
    out = redact_query_string("page=2&size=50", sensitive_keys=parse_sensitive_keys("token,passcode"))
    assert out == "page=2&size=50"


def test_redact_replaces_sensitive_keys():
    sensitive = parse_sensitive_keys("token,passcode")
    out = redact_query_string("token=abc123&page=2", sensitive_keys=sensitive)
    assert "abc123" not in (out or "")
    assert REDACTED in (out or "")


def test_redact_handles_opaque_query():
    out = redact_query_string("abc&def", sensitive_keys=set())
    assert out == REDACTED


# --- Production config gates (#10, refresh cookie) -----------------------


def test_runtime_settings_reject_password_reset_token_exposure_in_prod():
    settings = Settings(
        environment="production",
        secret_key="x" * 40,
        setup_token="y" * 32,
        mock_claude=False,
        mock_docusign=False,
        mock_reducto=False,
        mock_resend=False,
        cors_origins="https://app.example.com",
        allowed_hosts="app.example.com",
        refresh_cookie_secure=True,
        expose_refresh_token_in_body=False,
        expose_password_reset_token_in_response=True,
    )
    with pytest.raises(RuntimeError) as exc:
        validate_runtime_settings(settings)
    assert "EXPOSE_PASSWORD_RESET_TOKEN_IN_RESPONSE" in str(exc.value)


def test_runtime_settings_accepts_a_correctly_locked_down_production_config():
    settings = Settings(
        environment="production",
        secret_key="x" * 40,
        setup_token="y" * 32,
        mock_claude=False,
        mock_docusign=False,
        mock_reducto=False,
        mock_resend=False,
        cors_origins="https://app.example.com",
        allowed_hosts="app.example.com",
        refresh_cookie_secure=True,
        refresh_cookie_samesite="strict",
        expose_refresh_token_in_body=False,
        expose_password_reset_token_in_response=False,
    )
    # Must not raise.
    validate_runtime_settings(settings)
