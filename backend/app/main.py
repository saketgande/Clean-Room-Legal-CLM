from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.routes import router as admin_router
from app.approvals.routes import router as approvals_router
from app.assistant.routes import router as assistant_router
from app.auth.routes import router as auth_router
from app.auth.routes import users_router
from app.contract_brain.routes import router as contract_brain_router
from app.contract_files.routes import router as contract_files_router
from app.contracts.routes import hub_router, router as contracts_router
from app.core.config import settings
from app.core.middleware import RequestContextMiddleware
from app.debug.routes import router as debug_router
from app.jobs.routes import router as jobs_router
from app.notifications.routes import router as notifications_router
from app.obligations.routes import router as obligations_router
from app.organizations.routes import router as organizations_router
from app.playbooks.routes import router as playbooks_router
from app.projects.routes import router as projects_router
from app.renewals.routes import router as renewals_router
from app.search.routes import router as search_router
from app.signatures.routes import router as signatures_router
from app.tabular_review.routes import router as tabular_review_router
from app.workflows.routes import router as workflows_router


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.debug)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_v1_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(users_router, prefix=prefix)
    app.include_router(organizations_router, prefix=prefix)
    app.include_router(projects_router, prefix=prefix)
    app.include_router(contracts_router, prefix=prefix)
    app.include_router(contract_files_router, prefix=prefix)
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
    app.include_router(notifications_router, prefix=prefix)
    app.include_router(jobs_router, prefix=prefix)
    app.include_router(admin_router, prefix=prefix)
    app.include_router(debug_router, prefix=prefix)

    @app.get("/")
    def root():
        return {"app": settings.app_name, "api": prefix}

    return app


app = create_app()
