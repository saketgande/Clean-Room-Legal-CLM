"""F-16 rewrite — boot-time integration key validation.

Replaces ``backend/app/core/config.py:142-182`` (``validate_runtime_settings``).
The original validator caught insecure defaults and stranded mock flags
but did NOT verify that real integration keys were set when the
matching ``MOCK_X`` flag was off. Flipping ``MOCK_CLAUDE=false`` without
``CLAUDE_API_KEY`` set let the app boot; the first Claude request then
crashed with 401 from Anthropic (Agent 2 F-16 — High).

The new validator:
- Requires every integration's real config when its mock flag is off.
- Warns when a mock flag is on AND the corresponding real key is also
  set (likely-misconfiguration signal).
- Verifies the DocuSign PEM file exists and is readable when in real mode.
- Adds the new ``approval_token_rate_limit`` setting referenced by F-02.

This file is a drop-in for the body of ``app/core/config.py``. The
``Settings`` class itself is unchanged except for the new field; the
file shows only the changed surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Subset shown — only the fields touched by F-02 / F-16 are listed."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "local"

    # F-02 — new rate-limit setting for /approvals/token-decision.
    approval_token_rate_limit: str = Field(default="30/minute;200/hour")

    # Integration toggles + real-mode keys (unchanged shape; included for context).
    claude_api_key: str | None = None
    mock_claude: bool = True
    reducto_api_key: str | None = None
    mock_reducto: bool = True
    resend_api_key: str | None = None
    mock_resend: bool = True

    docusign_integration_key: str | None = None
    docusign_user_id: str | None = None
    docusign_account_id: str | None = None
    docusign_private_key_path: str | None = None
    docusign_connect_hmac_key: str | None = None
    mock_docusign: bool = True


_LOCAL_ENVIRONMENTS = frozenset({"local", "development", "dev", "test"})


def validate_runtime_settings(settings: Settings) -> None:  # noqa: C901 - simple linear checks
    """Refuse to boot when production-class config is missing or unsafe."""
    is_local = settings.environment.lower() in _LOCAL_ENVIRONMENTS
    problems: list[str] = []
    warnings: list[str] = []

    # --- Real-key requirements (NEW for F-16) ---
    if not settings.mock_claude and not settings.claude_api_key:
        problems.append("CLAUDE_API_KEY must be set when MOCK_CLAUDE=false")
    if not settings.mock_reducto and not settings.reducto_api_key:
        problems.append("REDUCTO_API_KEY must be set when MOCK_REDUCTO=false")
    if not settings.mock_resend and not settings.resend_api_key:
        problems.append("RESEND_API_KEY must be set when MOCK_RESEND=false")
    if not settings.mock_docusign:
        missing = [
            name
            for name, value in (
                ("DOCUSIGN_INTEGRATION_KEY", settings.docusign_integration_key),
                ("DOCUSIGN_USER_ID", settings.docusign_user_id),
                ("DOCUSIGN_ACCOUNT_ID", settings.docusign_account_id),
                ("DOCUSIGN_PRIVATE_KEY_PATH", settings.docusign_private_key_path),
            )
            if not value
        ]
        if missing:
            problems.append(
                "DocuSign real-mode requires: " + ", ".join(missing)
            )
        elif settings.docusign_private_key_path is not None:
            pem_path = Path(settings.docusign_private_key_path)
            if not pem_path.is_file():
                problems.append(
                    f"DOCUSIGN_PRIVATE_KEY_PATH points to missing file: {pem_path}"
                )
            else:
                try:
                    # Read a small slice to verify the file is actually readable.
                    pem_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    problems.append(
                        f"DOCUSIGN_PRIVATE_KEY_PATH is not readable as UTF-8: {exc}"
                    )

    # --- Likely-misconfiguration warnings (NEW for F-16) ---
    if settings.mock_claude and settings.claude_api_key:
        warnings.append(
            "MOCK_CLAUDE=true but CLAUDE_API_KEY is also set — likely misconfiguration"
        )
    if settings.mock_docusign and settings.docusign_integration_key:
        warnings.append(
            "MOCK_DOCUSIGN=true but DOCUSIGN_INTEGRATION_KEY is set — likely misconfiguration"
        )
    if settings.mock_resend and settings.resend_api_key:
        warnings.append(
            "MOCK_RESEND=true but RESEND_API_KEY is set — likely misconfiguration"
        )
    if settings.mock_reducto and settings.reducto_api_key:
        warnings.append(
            "MOCK_REDUCTO=true but REDUCTO_API_KEY is set — likely misconfiguration"
        )

    if is_local:
        # Local/dev only emits warnings — don't block bare-metal startup loops.
        if warnings:
            import logging

            logging.getLogger(__name__).warning(
                "config.likely_misconfiguration",
                extra={"warnings": warnings},
            )
        return

    # --- Production-class checks (preserved from the original validator) ---
    insecure_values: list[str] = []
    if (
        getattr(settings, "secret_key", "") == "change-me-before-production"
        or len(getattr(settings, "secret_key", "")) < 32
    ):
        insecure_values.append("SECRET_KEY")
    if (
        getattr(settings, "setup_token", "") == "local-setup-token"
        or len(getattr(settings, "setup_token", "")) < 24
    ):
        insecure_values.append("SETUP_TOKEN")
    enabled_mocks = [
        name
        for name, on in (
            ("MOCK_CLAUDE", settings.mock_claude),
            ("MOCK_DOCUSIGN", settings.mock_docusign),
            ("MOCK_REDUCTO", settings.mock_reducto),
            ("MOCK_RESEND", settings.mock_resend),
        )
        if on
    ]
    if insecure_values:
        problems.append(f"replace {', '.join(insecure_values)}")
    if enabled_mocks:
        problems.append(f"disable mock integrations {', '.join(enabled_mocks)}")
    if getattr(settings, "expose_password_reset_token_in_response", False):
        problems.append("disable EXPOSE_PASSWORD_RESET_TOKEN_IN_RESPONSE")
    if getattr(settings, "expose_refresh_token_in_body", False):
        problems.append("disable EXPOSE_REFRESH_TOKEN_IN_BODY (cookies only)")
    if not getattr(settings, "refresh_cookie_secure", False):
        problems.append("enable REFRESH_COOKIE_SECURE")
    samesite = str(getattr(settings, "refresh_cookie_samesite", "")).lower()
    if samesite not in {"strict", "lax"}:
        problems.append("set REFRESH_COOKIE_SAMESITE to 'strict' or 'lax'")
    if "*" in {
        h.strip() for h in str(getattr(settings, "allowed_hosts", "")).split(",")
    }:
        problems.append("set ALLOWED_HOSTS to a non-wildcard list")
    if "*" in str(getattr(settings, "cors_origins", "")).split(","):
        problems.append("set CORS_ORIGINS to an explicit list")

    if warnings:
        import logging

        logging.getLogger(__name__).warning(
            "config.likely_misconfiguration",
            extra={"warnings": warnings},
        )

    if problems:
        raise RuntimeError(
            "Insecure production configuration: " + "; ".join(problems)
        )
