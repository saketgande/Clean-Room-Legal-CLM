from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Clean Room Legal CLM"
    environment: str = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm"
    redis_url: str = "redis://localhost:6379/0"
    storage_root: Path = Path("./.local-contract-storage")

    secret_key: str = "change-me-before-production"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    setup_token: str = "local-setup-token"

    allowed_mime_types: list[str] = Field(
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

    reducto_api_key: str | None = None
    mock_reducto: bool = True

    resend_api_key: str | None = None
    resend_from_email: str = "legal-clm@example.com"
    mock_resend: bool = True

    docusign_integration_key: str | None = None
    docusign_user_id: str | None = None
    docusign_account_id: str | None = None
    docusign_private_key_path: str | None = None
    mock_docusign: bool = True

    verbose_debug_logging: bool = False
    allow_dev_reset: bool = False
    dev_seed_admin_email: str = "admin@example.com"
    dev_seed_admin_password: str = "local-dev-password"

    @field_validator("allowed_mime_types", mode="before")
    @classmethod
    def parse_mime_types(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
