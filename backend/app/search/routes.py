from datetime import date

from fastapi import APIRouter, Depends

from app.core.deps import require_permission
from app.search.dependencies import get_search_service
from app.search.service import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/contracts")
def search_contracts(
    q: str | None = None,
    stage: str | None = None,
    risk_level: str | None = None,
    contract_type: str | None = None,
    counterparty: str | None = None,
    jurisdiction: str | None = None,
    matter_id: str | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expiration_from: date | None = None,
    expiration_to: date | None = None,
    include_text: bool = False,
    limit: int = 50,
    current_user=Depends(require_permission("contract:read")),
    service: SearchService = Depends(get_search_service),
):
    return service.search_contracts(
        current_user=current_user,
        q=q,
        stage=stage,
        risk_level=risk_level,
        contract_type=contract_type,
        counterparty=counterparty,
        jurisdiction=jurisdiction,
        matter_id=matter_id,
        effective_from=effective_from,
        effective_to=effective_to,
        expiration_from=expiration_from,
        expiration_to=expiration_to,
        include_text=include_text,
        limit=limit,
    )


@router.get("/contract-text")
def search_contract_text(
    q: str,
    contract_id: str | None = None,
    matter_id: str | None = None,
    limit: int = 25,
    current_user=Depends(require_permission("contract:read")),
    service: SearchService = Depends(get_search_service),
):
    return service.search_contract_text(
        current_user=current_user, q=q, contract_id=contract_id, matter_id=matter_id, limit=limit,
    )


@router.get("/clauses")
def search_clauses(
    q: str | None = None,
    clause_type: str | None = None,
    contract_id: str | None = None,
    matter_id: str | None = None,
    limit: int = 50,
    current_user=Depends(require_permission("contract:read")),
    service: SearchService = Depends(get_search_service),
):
    return service.search_clauses(
        current_user=current_user, q=q, clause_type=clause_type,
        contract_id=contract_id, matter_id=matter_id, limit=limit,
    )


@router.get("/projects")
def search_projects(
    q: str | None = None,
    matter_type: str | None = None,
    limit: int = 50,
    current_user=Depends(require_permission("project:read")),
    service: SearchService = Depends(get_search_service),
):
    return service.search_projects(current_user=current_user, q=q, matter_type=matter_type, limit=limit)


@router.get("/versions")
def search_contract_versions(
    q: str | None = None,
    source: str | None = None,
    contract_id: str | None = None,
    limit: int = 50,
    current_user=Depends(require_permission("contract_file:read")),
    service: SearchService = Depends(get_search_service),
):
    return service.search_contract_versions(
        current_user=current_user, q=q, source=source, contract_id=contract_id, limit=limit,
    )
