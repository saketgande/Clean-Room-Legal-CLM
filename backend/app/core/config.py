from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Clean Room Legal CLM"
    environment: str
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Comma-separated. Override per-environment; no wildcard in production.
    # https://localhost:3001 is the local Word add-in dev server (word-addin/).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,https://localhost:3001"
    # Pinned CORS — '*' is rejected when allow_credentials is true anyway, but
    # we also keep the verb/header lists explicit so we can audit the surface.
    cors_allow_methods: str = "GET,POST,PATCH,PUT,DELETE,OPTIONS"
    # CSRF is already handled by the refresh cookie's SameSite attribute (see
    # refresh_cookie_samesite below) — no X-CSRF-Token header is generated or
    # checked anywhere, so advertising one here would be decorative and
    # misleading about what actually protects this app.
    cors_allow_headers: str = "Authorization,Content-Type,X-Request-ID"
    allowed_hosts: str = "*"
    force_https: bool = False

    # No hardcoded dev default: a missing DATABASE_URL must fail fast at
    # construction time rather than silently pointing at a throwaway DB.
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    storage_root: Path = Path("./.local-contract-storage")

    # SQLAlchemy connection pool. Conservative per-worker defaults; size up via
    # env for a multi-worker production deployment with a generous DB max_conn.
    db_pool_size: int = 5
    db_max_overflow: int = 10
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
    # F-02: unauthenticated, token-bearing approval endpoint. Kept tight.
    rate_limit_token_decision: str = "5/minute"
    # Streaming assistant / contract upload / generic AI-skill endpoints. These
    # are heavier than auth calls so they get their own buckets.
    rate_limit_assistant_stream: str = "10/minute"
    rate_limit_contract_upload: str = "20/minute"
    rate_limit_ai_skill: str = "20/minute"
    rate_limit_enabled: bool = True

    # Default SLA for approval requests. Used to populate ApprovalRequest.due_at
    # so overdue-approval sweeps are meaningful.
    approval_default_due_days: int = 7

    # When True, password reset tokens are echoed back in the API response —
    # convenient for local dev when MOCK_RESEND is on, dangerous anywhere else.
    # Default false; legacy local environments can override via env.
    expose_password_reset_token_in_response: bool = False

    # Per-request audit logging (RequestLog). Query strings are redacted to
    # avoid persisting tokens / passcodes / share secrets that occasionally
    # show up in URLs.
    request_log_redact_query: bool = True
    request_log_sensitive_query_keys: str = "token,passcode,refresh_token,access_token,api_key,setup_token"

    # Async batched writer for the RequestLog table. When True, the middleware
    # enqueues each log row and a background daemon thread flushes them in
    # batches via bulk_insert_mappings — collapsing one INSERT-per-request into
    # one per N requests or per interval. Set False to restore the synchronous
    # per-request write (one extra DB round-trip per API call).
    request_log_async_enabled: bool = True
    request_log_batch_size: int = 50
    request_log_flush_interval_seconds: float = 2.0
    # Bounded queue so a slow writer can't grow memory unboundedly. Overflow
    # falls back to inline write rather than dropping the row.
    request_log_queue_max_items: int = 10_000

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
    claude_model: str = "claude-sonnet-4-6"
    # Mock integrations default OFF — production-safe. Local dev opts back in
    # explicitly via MOCK_* env (see .env.example / tests/conftest.py).
    mock_claude: bool = False
    ai_store_raw_outputs: bool = False
    ai_max_tool_iterations: int = 8
    ai_default_temperature: float = 0.0
    # Anthropic prompt caching: cache the (large, static) system prompt + tool
    # schemas so they aren't re-billed at full price on every turn and every
    # tool-loop iteration. Cheap cache reads instead of full input tokens.
    ai_prompt_caching: bool = True

    # Retrieval quality (Contract Brain / Search). All default to the current
    # local, no-key behavior; set the provider + key to activate.
    #   embedding_provider: "local" (bge-small, 384-dim) | "voyage" (voyage-law-2,
    #     1024-dim — legal-domain embeddings). Switching to voyage requires the
    #     1024-dim migration + a full re-embed (see docs) since pgvector columns
    #     are fixed-dimension.
    embedding_provider: str = "local"
    voyage_api_key: str | None = None
    voyage_embedding_model: str = "voyage-law-2"
    # If local embeddings (fastembed) are unavailable, refuse to write meaningless
    # random vectors — raise so the job fails loudly instead of poisoning the
    # index. Set True only in CI/bare shells that knowingly want mock vectors.
    allow_mock_embeddings: bool = False
    #   rerank_provider: "local" (fastembed cross-encoder, no key — the default so
    #     the second-stage reranker is actually on) | "cohere" | "voyage" | "none".
    #     Over-fetches candidates then reranks with a cross-encoder to the top N.
    rerank_provider: str = "local"
    rerank_model_local: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    cohere_api_key: str | None = None
    cohere_rerank_model: str = "rerank-english-v3.0"
    voyage_rerank_model: str = "rerank-2"
    #   contextual_chunking: prepend a short "[Contract: title]" context marker to
    #     each embedded chunk so passages disambiguate across contracts (free).
    contextual_chunking: bool = False

    # Per-stage lifecycle SLAs (days). The daily sweep notifies the contract
    # owner when a stage SLA is breached and escalates to org admins after
    # sla_escalation_after_days more days. Format: "stage:days,stage:days".
    # NDA fast-lane: a low-risk NDA under the value cap with no open
    # high-severity deviations skips Approval entirely (straight to Signature),
    # with a full audit trail. The Ironclad-style "routine contracts route
    # themselves" behavior.
    nda_fast_lane_enabled: bool = True
    nda_fast_lane_max_value: float = 50_000.0

    stage_sla_days: str = "intake:3,drafting:7,review:7,approval:3,signature:5"
    sla_escalation_after_days: int = 3

    reducto_api_key: str | None = None
    mock_reducto: bool = False

    # --- Databricks document extraction ------------------------------------
    # OCR (ai_parse_document) plus structured field extraction (ai_extract),
    # reached over the SQL Statement Execution API. Inert until host, token and
    # warehouse are all set, so the app runs unchanged without them.
    databricks_host: str | None = None            # https://<workspace>.cloud.databricks.com
    databricks_token: str | None = None
    databricks_warehouse_id: str | None = None    # must be a SERVERLESS warehouse
    databricks_volume: str = "/Volumes/main/legal/contracts"
    databricks_precision_mode: bool = True        # off for short paper, on for long agreements
    mock_databricks: bool = False

    resend_api_key: str | None = None
    resend_from_email: str = "legal-clm@example.com"
    mock_resend: bool = False

    # Public base URL of the web app, used to build clickable links in emails
    # (e.g. one-click Approve/Reject). Override via APP_BASE_URL in production.
    app_base_url: str = "http://localhost:4173"

    docusign_integration_key: str | None = None
    docusign_user_id: str | None = None
    docusign_account_id: str | None = None
    docusign_private_key_path: str | None = None
    docusign_oauth_base_url: str = "https://account-d.docusign.com"
    docusign_rest_base_url: str = "https://demo.docusign.net/restapi"
    docusign_connect_hmac_key: str | None = None
    mock_docusign: bool = False

    # Legal Intake channel ingestion + screening (all optional; features are
    # inert until configured). Webhook auth fails CLOSED in production.
    intake_webhook_secret: str | None = None
    intake_teams_secret: str | None = None
    intake_graph_tenant_id: str | None = None
    intake_graph_client_id: str | None = None
    intake_graph_client_secret: str | None = None
    intake_graph_mailbox: str | None = None  # e.g. legal@company.com
    intake_mailbox_auto_ack: bool = False
    intake_gmail_address: str | None = None  # Gmail account to sync, e.g. legal@gmail.com
    intake_gmail_app_password: str | None = None  # 16-char Google App Password (not the account password)
    intake_gmail_folder: str = "INBOX"
    intake_gmail_max_messages: int = 20  # cap per sync click
    # Non-production-ready agents stay hidden unless demo agents are enabled,
    # so users never see fabricated analysis in production.
    intake_demo_agents: bool = True

    verbose_debug_logging: bool = False
    allow_dev_reset: bool = False
    dev_seed_admin_email: str = "admin@example.com"
    dev_seed_admin_password: str = "local-dev-password"

    # Anthropic / Claude resiliency knobs (tenacity retries).
    claude_max_retries: int = 3
    claude_retry_initial_backoff_seconds: float = 1.0
    claude_retry_max_backoff_seconds: float = 30.0

    # Claude spend guardrails. Per-org daily token budget (input+output) and a
    # hard ceiling on max_tokens for any single request so a runaway prompt
    # can't blow the budget in one call.
    claude_daily_token_cap_per_org: int = 5_000_000
    claude_max_tokens_ceiling: int = 8000

    # Upload pipeline safety nets.
    upload_stream_chunk_bytes: int = 1024 * 1024  # 1 MB chunks
    pdf_max_extracted_text_bytes: int = 8 * 1024 * 1024  # 8 MB cap per contract

    # Object storage backend. "local" writes under storage_root (default,
    # dev-friendly); "s3" targets the bucket/endpoint/region below.
    storage_backend: str = "local"  # "local" | "s3"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None

    # Optional ClamAV antivirus scan on upload. Off by default so local dev
    # and CI don't need a clamd sidecar; the host/port target a clamd daemon.
    enable_clamav: bool = False
    clamav_host: str = "clamav"
    clamav_port: int = 3310

    # Sentry error reporting. Unset DSN disables it entirely; PII is never sent.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.05

    # Prometheus metrics endpoint (/metrics). Off by default.
    enable_metrics: bool = False

    # CORS preflight cache lifetime (seconds) sent as Access-Control-Max-Age.
    cors_max_age_seconds: int = 600

    # Trademarks module — external similarity-search providers. All optional;
    # search-similar degrades a source to "not_configured" rather than failing
    # the whole request when a key is absent (see app/trademarks/providers/).
    signa_api_key: str | None = None
    signa_base_url: str = "https://api.signa.so/v1/trademarks"
    signa_offices: str = ""
    signa_timeout_seconds: float = 5.0
    mock_signa: bool = True

    tmsearch_api_key: str | None = None
    tmsearch_base_url: str = "https://tmsearch.ai/api/search/"
    tmsearch_timeout_seconds: float = 10.0
    mock_tmsearch: bool = True

    search_provider: str = "serper_dev"
    search_provider_api_key: str | None = None
    search_provider_timeout_seconds: float = 5.0
    mock_serper: bool = True

    trademark_internal_search_top_k: int = 10
    trademark_internal_search_timeout_seconds: float = 5.0
    trademark_risk_threshold_high: float = 0.85
    trademark_risk_threshold_medium: float = 0.60

    # ip_india_journal vision extraction template — renders a PDF page to a
    # PNG and asks Claude (via app.integrations.claude.ClaudeClient) to read
    # it. Degrades gracefully (empty entries) when mock_claude is on / no key.
    trademark_vision_render_dpi: int = 200

    @field_validator("allowed_mime_types", mode="before")
    @classmethod
    def parse_mime_types(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("environment")
    @classmethod
    def environment_must_be_explicit(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise ValueError("ENVIRONMENT must be set explicitly")
        return normalized


def validate_runtime_settings(settings: Settings) -> None:
    env = settings.environment.lower()
    if env in {"local", "development", "dev", "test"}:
        return
    if env not in {"production", "prod", "staging", "stage"}:
        raise RuntimeError(
            "Unknown ENVIRONMENT value; use local/development/test/staging/production"
        )
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
            ("MOCK_SIGNA", settings.mock_signa),
            ("MOCK_TMSEARCH", settings.mock_tmsearch),
            ("MOCK_SERPER", settings.mock_serper),
        )
        if on
    ]
    problems = []
    if insecure_values:
        problems.append(f"replace {', '.join(insecure_values)}")
    if enabled_mocks:
        problems.append(f"disable mock integrations {', '.join(enabled_mocks)}")

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

    # When DocuSign is live (not mocked) the Connect webhook MUST verify its
    # HMAC signature, otherwise anyone can forge envelope status callbacks.
    if not settings.mock_docusign and not settings.docusign_connect_hmac_key:
        problems.append("set DOCUSIGN_CONNECT_HMAC_KEY (required when MOCK_DOCUSIGN is off)")
    # Public links must not point at a developer's loopback address in prod.
    if settings.app_base_url.lower().startswith("http://localhost"):
        problems.append("set APP_BASE_URL to a public https URL (not http://localhost)")

    if problems:
        raise RuntimeError("Insecure production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
