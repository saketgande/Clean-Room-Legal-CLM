from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session

from app.admin.routes import router as admin_router
from app.ai.routes import router as ai_router
from app.approvals.routes import router as approvals_router
from app.assistant.routes import router as assistant_router
from app.auth.routes import router as auth_router
from app.auth.routes import users_router
from app.contract_brain.routes import router as contract_brain_router
from app.contract_files.routes import external_share_router, router as contract_files_router
from app.contracts.routes import hub_router, router as contracts_router
from app.contracts.comments_routes import router as contract_comments_router
from app.core.config import settings, validate_runtime_settings
from app.core.deps import get_db
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.core.request_log_queue import start_writer as start_request_log_writer
from app.core.request_log_queue import stop_writer as stop_request_log_writer
from app.debug.routes import check_readiness
from app.debug.routes import router as debug_router
from app.integrations.claude import aclose_claude_client
from app.integrations.docusign import aclose_docusign_client
from app.integrations.resend import aclose_resend_client
from app.jobs.routes import router as jobs_router
from app.notifications.routes import router as notifications_router
from app.obligations.routes import router as obligations_router
from app.organizations.routes import router as organizations_router
from app.playbooks.routes import router as playbooks_router
from app.projects.routes import router as projects_router
from app.renewals.routes import router as renewals_router
from app.observability.routes import router as observability_router
from app.search.routes import router as search_router
from app.signatures.routes import router as signatures_router
from app.tabular_review.routes import router as tabular_review_router
from app.workflows.routes import router as workflows_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background worker for batched RequestLog flushing — runs for the
    # lifetime of the app; ``stop_writer`` drains the queue on shutdown so we
    # don't lose buffered rows on a clean uvicorn exit.
    start_request_log_writer()
    try:
        yield
    finally:
        stop_request_log_writer()
        # Close the long-lived HTTP clients so any connection-pool resources
        # are released cleanly. Skipping these isn't catastrophic (Python
        # would tear them down at process exit) but it produces noisy warnings.
        await aclose_claude_client()
        await aclose_docusign_client()
        await aclose_resend_client()


def create_app() -> FastAPI:
    configure_logging()
    validate_runtime_settings(settings)

    # Error reporting. Initialised before anything else so startup failures are
    # captured. No-op when the DSN is unset or sentry-sdk isn't installed.
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.environment,
                traces_sample_rate=settings.sentry_traces_sample_rate,
                send_default_pii=False,
            )
        except ImportError:
            pass

    app = FastAPI(
        title=settings.app_name, version="0.1.0", debug=settings.debug, lifespan=lifespan
    )
    register_exception_handlers(app)

    # Rate limit: shared limiter instance bound onto the app so decorators in
    # auth/routes.py can resolve it.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    if settings.rate_limit_enabled:
        app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    cors_origins = [
        o.strip() for o in settings.cors_origins.split(",") if o.strip()
    ]
    cors_methods = [
        m.strip() for m in settings.cors_allow_methods.split(",") if m.strip()
    ] or ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
    cors_headers = [
        h.strip() for h in settings.cors_allow_headers.split(",") if h.strip()
    ] or ["Authorization", "Content-Type", "X-Request-ID"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=cors_methods,
        allow_headers=cors_headers,
        max_age=settings.cors_max_age_seconds,
    )

    allowed_hosts = [
        h.strip() for h in settings.allowed_hosts.split(",") if h.strip()
    ] or ["*"]
    if allowed_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    if settings.force_https:
        app.add_middleware(HTTPSRedirectMiddleware)

    prefix = settings.api_v1_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(users_router, prefix=prefix)
    app.include_router(organizations_router, prefix=prefix)
    app.include_router(projects_router, prefix=prefix)
    app.include_router(contracts_router, prefix=prefix)
    app.include_router(contract_comments_router, prefix=prefix)
    app.include_router(contract_files_router, prefix=prefix)
    app.include_router(external_share_router, prefix=prefix)
    app.include_router(ai_router, prefix=prefix)
    app.include_router(assistant_router, prefix=prefix)
    app.include_router(workflows_router, prefix=prefix)
    app.include_router(playbooks_router, prefix=prefix)
    app.include_router(hub_router, prefix=prefix)
    app.include_router(contract_brain_router, prefix=prefix)
    app.include_router(approvals_router, prefix=prefix)
    app.include_router(signatures_router, prefix=prefix)
    app.include_router(obligations_router, prefix=prefix)
    app.include_router(renewals_router, prefix=prefix)
    app.include_router(tabular_review_router, prefix=prefix)
    app.include_router(search_router, prefix=prefix)
    app.include_router(observability_router, prefix=prefix)
    app.include_router(notifications_router, prefix=prefix)
    app.include_router(jobs_router, prefix=prefix)
    app.include_router(admin_router, prefix=prefix)
    # Internal traces / config-disclosure router is only mounted outside
    # production. The LB-facing /healthz and /readyz below are always present.
    if settings.environment.lower() in {"local", "development", "test"}:
        app.include_router(debug_router, prefix=prefix)

    if settings.enable_metrics:
        # Prometheus /metrics. Guarded so a missing instrumentator package is a
        # no-op rather than a hard startup failure.
        try:
            from prometheus_fastapi_instrumentator import Instrumentator

            Instrumentator().instrument(app).expose(app, endpoint="/metrics")
        except ImportError:
            pass

    @app.get("/")
    def root():
        return {"app": settings.app_name, "api": prefix}

    # Load-balancer / process probes. Unauthenticated and registered in ALL
    # environments. /healthz is pure liveness; /readyz verifies dependencies.
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(db: Session = Depends(get_db)):
        return check_readiness(db)

    return app


app = create_app()
