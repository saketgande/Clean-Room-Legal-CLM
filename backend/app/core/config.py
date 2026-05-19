from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Clean Room Legal CLM"
    environment: str = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Comma-separated. Override per-environment; no wildcard in production.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Pinned CORS — '*' is rejected when allow_credentials is true anyway, but
    # we also keep the verb/header lists explicit so we can audit the surface.
    cors_allow_methods: str = "GET,POST,PATCH,PUT,DELETE,OPTIONS"
    cors_allow_headers: str = "Authorization,Content-Type,X-Request-ID,X-CSRF-Token"
    allowed_hosts: str = "*"
    force_https: bool = False

    database_url: str = "postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm"
    redis_url: str = "redis://localhost:6379/0"
    storage_root: Path = Path("./.local-contract-storage")

    # SQLAlchemy connection pool. Defaults are sane for a single dev process but
    # almost certainly too small for a multi-worker production deployment.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_pool_timeout_seconds: int = 30

    secret_key: str = "change-me-before-production"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    setup_token: str = "local-setup-token"

    # Refresh tokens are issued as HttpOnly cookies. The legacy JSON body
    # field is preserved for backwards-compat with existing API clients but
    # disabled in production by default — flip on only for trusted backends.
    refresh_cookie_name: str = "aegis_refresh"
    refresh_cookie_path: str = "/api/v1/auth"
    refresh_cookie_samesite: str = "lax"  # "strict" once frontend is fully cookie-aware
    refresh_cookie_secure: bool = False  # set true in production
    expose_refresh_token_in_body: bool = True  # legacy clients; turn off in prod

    # Rate limiting (slowapi). "5/minute" style strings.
    rate_limit_login: str = "10/minute"
    rate_limit_refresh: str = "30/minute"
    rate_limit_password_reset: str = "5/minute"
    rate_limit_invitation_accept: str = "5/minute"
    rate_limit_enabled: bool = True

    # When True, password reset tokens are echoed back in the API response —
    # convenient for local dev when MOCK_RESEND is on, dangerous anywhere else.
    # Default false; legacy local environments can override via env.
    expose_password_reset_token_in_response: bool = False

    # Per-request audit logging (RequestLog). Query strings are redacted to
    # avoid persisting tokens / passcodes / share secrets that occasionally
    # show up in URLs.
    request_log_redact_query: bool = True
    request_log_sensitive_query_keys: str = "token,passcode,refresh_token,access_token,api_key,setup_token"

    allowed_mime_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
            "text/plain",
            "image/png",
            "image/jpeg",
        ]
    )
    max_upload_size_bytes: int = 50 * 1024 * 1024

    claude_api_key: str | None = None
    claude_model: str = "claude-3-5-sonnet-latest"
    mock_claude: bool = True
    ai_store_raw_outputs: bool = True
    ai_max_tool_iterations: int = 8
    ai_default_temperature: float = 0.0

    reducto_api_key: str | None = None
    mock_reducto: bool = True

    resend_api_key: str | None = None
    resend_from_email: str = "legal-clm@example.com"
    mock_resend: bool = True

    docusign_integration_key: str | None = None
    docusign_user_id: str | None = None
    docusign_account_id: str | None = None
    docusign_private_key_path: str | None = None
    docusign_oauth_base_url: str = "https://account-d.docusign.com"
    docusign_rest_base_url: str = "https://demo.docusign.net/restapi"
    docusign_connect_hmac_key: str | None = None
    mock_docusign: bool = True

    verbose_debug_logging: bool = False
    allow_dev_reset: bool = False
    dev_seed_admin_email: str = "admin@example.com"
    dev_seed_admin_password: str = "local-dev-password"

    # Anthropic / Claude resiliency knobs (tenacity retries).
    claude_max_retries: int = 3
    claude_retry_initial_backoff_seconds: float = 1.0
    claude_retry_max_backoff_seconds: float = 30.0

    # Upload pipeline safety nets.
    upload_stream_chunk_bytes: int = 1024 * 1024  # 1 MB chunks
    pdf_max_extracted_text_bytes: int = 8 * 1024 * 1024  # 8 MB cap per contract

    @field_validator("allowed_mime_types", mode="before")
    @classmethod
    def parse_mime_types(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


def validate_runtime_settings(settings: Settings) -> None:
    if settings.environment.lower() in {"local", "development", "dev", "test"}:
        return
    insecure_values = []
    if settings.secret_key == "change-me-before-production" or len(settings.secret_key) < 32:
        insecure_values.append("SECRET_KEY")
    if settings.setup_token == "local-setup-token" or len(settings.setup_token) < 24:
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
    problems = []
    if insecure_values:
        problems.append(f"replace {', '.join(insecure_values)}")
    if enabled_mocks:
        problems.append(f"disable mock integrations {', '.join(enabled_mocks)}")
    # HMAC key intentionally optional: Connect is plan-gated; the webhook self-rejects when unset.

    # Security flags that must be tightened before going live.
    if settings.expose_password_reset_token_in_response:
        problems.append("disable EXPOSE_PASSWORD_RESET_TOKEN_IN_RESPONSE")
    if settings.expose_refresh_token_in_body:
        problems.append("disable EXPOSE_REFRESH_TOKEN_IN_BODY (cookies only)")
    if not settings.refresh_cookie_secure:
        problems.append("enable REFRESH_COOKIE_SECURE")
    if settings.refresh_cookie_samesite.lower() not in {"strict", "lax"}:
        problems.append("set REFRESH_COOKIE_SAMESITE to 'strict' or 'lax'")
    if "*" in {h.strip() for h in settings.allowed_hosts.split(",")}:
        problems.append("set ALLOWED_HOSTS to a non-wildcard list")
    if "*" in settings.cors_origins.split(","):
        problems.append("set CORS_ORIGINS to an explicit list")

    if problems:
        raise RuntimeError("Insecure production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
