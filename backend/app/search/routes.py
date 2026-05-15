from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.core.deps import get_db, require_permission
from app.projects.access import get_project_for_user, project_scope_query
from app.projects.models import Project, ProjectContract

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/contracts")
def search_contracts(
    q: str | None = None,
    stage: str | None = None,
    risk_level: str | None = None,
    contract_type: str | None = None,
    counterparty: str | None = None,
    jurisdiction: str | None = None,
    project_id: str | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expiration_from: date | None = None,
    expiration_to: date | None = None,
    include_text: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    query = select(Contract).where(
        Contract.org_id == current_user.org_id,
        Contract.deleted_at.is_(None),
        accessible_contract_filter(current_user),
    )
    if project_id:
        get_project_for_user(db, project_id=project_id, user=current_user)
        query = query.join(ProjectContract, ProjectContract.contract_id == Contract.id).where(
            ProjectContract.org_id == current_user.org_id,
            ProjectContract.project_id == project_id,
        )
    if q:
        metadata_filter = or_(
            Contract.title.ilike(f"%{q}%"),
            Contract.counterparty_name.ilike(f"%{q}%"),
            Contract.contract_type.ilike(f"%{q}%"),
            Contract.jurisdiction.ilike(f"%{q}%"),
        )
        if include_text:
            text_contract_ids = select(ContractTextSnapshot.contract_id).where(
                ContractTextSnapshot.org_id == current_user.org_id,
                ContractTextSnapshot.deleted_at.is_(None),
                ContractTextSnapshot.text.ilike(f"%{q}%"),
            )
            query = query.where(or_(metadata_filter, Contract.id.in_(text_contract_ids)))
        else:
            query = query.where(metadata_filter)
    if stage:
        query = query.where(Contract.lifecycle_stage == stage)
    if risk_level:
        query = query.where(Contract.risk_level == risk_level)
    if contract_type:
        query = query.where(Contract.contract_type == contract_type)
    if counterparty:
        query = query.where(Contract.counterparty_name.ilike(f"%{counterparty}%"))
    if jurisdiction:
        query = query.where(Contract.jurisdiction.ilike(f"%{jurisdiction}%"))
    if effective_from:
        query = query.where(Contract.effective_date >= effective_from)
    if effective_to:
        query = query.where(Contract.effective_date <= effective_to)
    if expiration_from:
        query = query.where(Contract.expiration_date >= expiration_from)
    if expiration_to:
        query = query.where(Contract.expiration_date <= expiration_to)
    return db.scalars(query.order_by(Contract.updated_at.desc()).limit(min(limit, 100))).all()


@router.get("/contract-text")
def search_contract_text(
    q: str,
    contract_id: str | None = None,
    project_id: str | None = None,
    limit: int = 25,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    query = (
        select(ContractTextSnapshot, Contract)
        .join(Contract, Contract.id == ContractTextSnapshot.contract_id)
        .where(
            ContractTextSnapshot.org_id == current_user.org_id,
            ContractTextSnapshot.deleted_at.is_(None),
            Contract.deleted_at.is_(None),
            accessible_contract_filter(current_user),
            ContractTextSnapshot.text.ilike(f"%{q}%"),
        )
    )
    if contract_id:
        query = query.where(ContractTextSnapshot.contract_id == contract_id)
    if project_id:
        get_project_for_user(db, project_id=project_id, user=current_user)
        query = query.join(ProjectContract, ProjectContract.contract_id == Contract.id).where(
            ProjectContract.org_id == current_user.org_id,
            ProjectContract.project_id == project_id,
        )
    rows = db.execute(query.limit(min(limit, 100))).all()
    return [
        {
            "contract_id": contract.id,
            "contract_title": contract.title,
            "text_snapshot_id": snapshot.id,
            "contract_version_id": snapshot.contract_version_id,
            "matches": _text_matches(snapshot.text, q),
        }
        for snapshot, contract in rows
    ]


@router.get("/clauses")
def search_clauses(
    q: str | None = None,
    clause_type: str | None = None,
    contract_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    query = (
        select(ClauseExtraction, Contract)
        .join(Contract, Contract.id == ClauseExtraction.contract_id)
        .where(
            ClauseExtraction.org_id == current_user.org_id,
            ClauseExtraction.is_stale.is_(False),
            Contract.deleted_at.is_(None),
            accessible_contract_filter(current_user),
        )
    )
    if q:
        query = query.where(
            or_(
                ClauseExtraction.text.ilike(f"%{q}%"),
                ClauseExtraction.heading.ilike(f"%{q}%"),
                ClauseExtraction.clause_type.ilike(f"%{q}%"),
            )
        )
    if clause_type:
        query = query.where(ClauseExtraction.clause_type == clause_type)
    if contract_id:
        query = query.where(ClauseExtraction.contract_id == contract_id)
    if project_id:
        get_project_for_user(db, project_id=project_id, user=current_user)
        query = query.join(ProjectContract, ProjectContract.contract_id == Contract.id).where(
            ProjectContract.org_id == current_user.org_id,
            ProjectContract.project_id == project_id,
        )
    rows = db.execute(query.limit(min(limit, 100))).all()
    return [
        {
            "clause_id": clause.id,
            "contract_id": contract.id,
            "contract_title": contract.title,
            "contract_version_id": clause.contract_version_id,
            "text_snapshot_id": clause.text_snapshot_id,
            "clause_type": clause.clause_type,
            "heading": clause.heading,
            "confidence": clause.confidence,
            "excerpt": clause.text[:1000],
        }
        for clause, contract in rows
    ]


@router.get("/projects")
def search_projects(
    q: str | None = None,
    project_type: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    query = project_scope_query(db, user=current_user)
    if q:
        query = query.where(or_(Project.name.ilike(f"%{q}%"), Project.description.ilike(f"%{q}%")))
    if project_type:
        query = query.where(Project.project_type == project_type)
    return db.scalars(query.order_by(Project.updated_at.desc()).limit(min(limit, 100))).all()


@router.get("/versions")
def search_contract_versions(
    q: str | None = None,
    source: str | None = None,
    contract_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
):
    query = (
        select(ContractVersion, Contract)
        .join(Contract, Contract.id == ContractVersion.contract_id)
        .where(
            ContractVersion.org_id == current_user.org_id,
            ContractVersion.deleted_at.is_(None),
            Contract.deleted_at.is_(None),
            accessible_contract_filter(current_user),
        )
    )
    if q:
        query = query.where(ContractVersion.change_summary.ilike(f"%{q}%"))
    if source:
        query = query.where(ContractVersion.source == source)
    if contract_id:
        query = query.where(ContractVersion.contract_id == contract_id)
    rows = db.execute(query.order_by(ContractVersion.created_at.desc()).limit(min(limit, 100))).all()
    return [
        {
            "contract_version_id": version.id,
            "contract_id": contract.id,
            "contract_title": contract.title,
            "version_number": version.version_number,
            "source": version.source,
            "change_summary": version.change_summary,
            "is_authoritative": version.is_authoritative,
        }
        for version, contract in rows
    ]


def _text_matches(text: str, q: str) -> list[dict]:
    matches = []
    lowered = text.lower()
    needle = q.lower()
    start = lowered.find(needle)
    while start >= 0 and len(matches) < 5:
        end = start + len(q)
        excerpt_start = max(0, start - 160)
        excerpt_end = min(len(text), end + 160)
        matches.append(
            {
                "start_char": start,
                "end_char": end,
                "excerpt": text[excerpt_start:excerpt_end],
            }
        )
        start = lowered.find(needle, end)
    return matches
