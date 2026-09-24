"""FastAPI-native dependency providers for the contracts module.

Part of the DI migration (see backend/DI_MIGRATION.md). Each provider is a
plain function taking `db` via `Depends(get_db)` and returning a service
instance — mirroring the `require_permission` factory-closure shape already
used in `app.core.deps`. Providers are usable both as `Depends(...)` in
routes and by direct call from non-request contexts (Celery tasks, deferred
imports) since they take no `Request`.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.contracts.access import ContractAccessService
from app.contracts.comments_service import ContractCommentService
from app.contracts.lifecycle import ContractLifecycleService
from app.contracts.risk import ContractRiskService
from app.contracts.service import ContractService
from app.core.deps import get_db


def get_contract_access_service(db: Session = Depends(get_db)) -> ContractAccessService:
    return ContractAccessService(db)


def get_contract_service(db: Session = Depends(get_db)) -> ContractService:
    return ContractService(db)


def get_contract_lifecycle_service(db: Session = Depends(get_db)) -> ContractLifecycleService:
    return ContractLifecycleService(db)


def get_contract_risk_service(db: Session = Depends(get_db)) -> ContractRiskService:
    return ContractRiskService(db)


def get_contract_comment_service(db: Session = Depends(get_db)) -> ContractCommentService:
    return ContractCommentService(db)
