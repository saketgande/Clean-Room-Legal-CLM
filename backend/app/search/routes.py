from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.core.deps import get_db, require_permission
from app.projects.access import get_project_for_user, project_scope_query
from app.projects.models import Project, ProjectContract
from app.search.fts import (
    clause_vector,
    fts_usable,
    like_contains,
    snapshot_headline,
    snapshot_vector,
    text_matches,
)

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
        q_like = like_contains(q)
        metadata_filter = or_(
            Contract.title.ilike(q_like, escape="\\"),
            Contract.counterparty_name.ilike(q_like, escape="\\"),
            Contract.contract_type.ilike(q_like, escape="\\"),
            Contract.jurisdiction.ilike(q_like, escape="\\"),
        )
        if include_text:
            text_match = (
                snapshot_vector().op("@@")(func.websearch_to_tsquery("english", q))
                if fts_usable(db, q)
                else ContractTextSnapshot.text.ilike(q_like, escape="\\")
            )
            text_contract_ids = select(ContractTextSnapshot.contract_id).where(
                ContractTextSnapshot.org_id == current_user.org_id,
                ContractTextSnapshot.deleted_at.is_(None),
                text_match,
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
        query = query.where(Contract.counterparty_name.ilike(like_contains(counterparty), escape="\\"))
    if jurisdiction:
        query = query.where(Contract.jurisdiction.ilike(like_contains(jurisdiction), escape="\\"))
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
    use_fts = fts_usable(db, q)
    tsq = func.websearch_to_tsquery("english", q)
    headline = (
        snapshot_headline(tsq)
        if use_fts
        else func.substr(ContractTextSnapshot.text, 1, 0)
    )
    query = (
        select(ContractTextSnapshot, Contract, headline.label("headline"))
        .join(Contract, Contract.id == ContractTextSnapshot.contract_id)
        .where(
            ContractTextSnapshot.org_id == current_user.org_id,
            ContractTextSnapshot.deleted_at.is_(None),
            Contract.deleted_at.is_(None),
            accessible_contract_filter(current_user),
        )
    )
    if use_fts:
        vec = snapshot_vector()
        query = query.where(vec.op("@@")(tsq)).order_by(func.ts_rank(vec, tsq).desc())
    else:
        query = query.where(
            ContractTextSnapshot.text.ilike(like_contains(q), escape="\\")
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
    results = []
    for snapshot, contract, headline_text in rows:
        # Exact-substring excerpts when the literal query appears; otherwise
        # (stemmed FTS matches like "terminations" → "termination") fall back
        # to ts_headline fragments so the user still sees why it matched.
        matches = text_matches(snapshot.text, q)
        if not matches and headline_text:
            matches = [
                {"start_char": -1, "end_char": -1, "excerpt": frag.strip()}
                for frag in str(headline_text).split(" ... ")
                if frag.strip()
            ]
        results.append(
            {
                "contract_id": contract.id,
                "contract_title": contract.title,
                "text_snapshot_id": snapshot.id,
                "contract_version_id": snapshot.contract_version_id,
                "matches": matches,
            }
        )
    return results


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
        q_like = like_contains(q)
        if fts_usable(db, q):
            tsq = func.websearch_to_tsquery("english", q)
            vec = clause_vector()
            # clause_type stays ILIKE — it's a short slug ("limitation_of_
            # liability") that FTS tokenizes poorly.
            query = query.where(
                or_(
                    vec.op("@@")(tsq),
                    ClauseExtraction.clause_type.ilike(q_like, escape="\\"),
                )
            ).order_by(func.ts_rank(vec, tsq).desc())
        else:
            query = query.where(
                or_(
                    ClauseExtraction.text.ilike(q_like, escape="\\"),
                    ClauseExtraction.heading.ilike(q_like, escape="\\"),
                    ClauseExtraction.clause_type.ilike(q_like, escape="\\"),
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
        q_like = like_contains(q)
        query = query.where(
            or_(
                Project.name.ilike(q_like, escape="\\"),
                Project.description.ilike(q_like, escape="\\"),
            )
        )
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
        query = query.where(ContractVersion.change_summary.ilike(like_contains(q), escape="\\"))
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
