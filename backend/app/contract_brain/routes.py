from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.citations import validate_citation
from app.ai.controller import ai_controller
from app.ai.schemas import BrainAnswerOutput, BrainQueryParseOutput, CitationInput
from app.contract_brain.models import BrainQuery
from app.contract_brain.retrieval import assemble_context, precedent_contracts
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.service import get_contract_for_user
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.projects.access import get_project_for_user

router = APIRouter(prefix="/contract-brain", tags=["contract-brain"])


class BrainAskRequest(BaseModel):
    question: str = Field(min_length=3)
    query_scope: str = Field(default="portfolio", pattern="^(contract|project|portfolio)$")
    contract_id: str | None = None
    project_id: str | None = None


@router.post("/ask")
async def ask_contract_brain(
    payload: BrainAskRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    if payload.query_scope == "contract":
        if not payload.contract_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "contract_id required for contract scope")
        get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    if payload.query_scope == "project":
        if not payload.project_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "project_id required for project scope")
        get_project_for_user(db, project_id=payload.project_id, user=current_user)

    request_id = getattr(request.state, "request_id", None)
    parsed = await ai_controller.run_structured_skill(
        db,
        skill_name="contract_brain_query_parse",
        org_id=current_user.org_id,
        created_by_user_id=current_user.id,
        input_payload={"question": payload.question, "requested_scope": payload.query_scope},
        request_id=request_id,
    )
    parsed = parsed if isinstance(parsed, BrainQueryParseOutput) else BrainQueryParseOutput.model_validate(parsed)

    context = assemble_context(
        db,
        user=current_user,
        question=payload.question,
        scope=payload.query_scope,
        contract_id=payload.contract_id,
        project_id=payload.project_id,
        parsed=parsed,
    )

    answer = await ai_controller.run_structured_skill(
        db,
        skill_name="contract_brain_answer",
        org_id=current_user.org_id,
        created_by_user_id=current_user.id,
        input_payload={
            "question": payload.question,
            "retrieved_context": context["context_text"],
            "scope": payload.query_scope,
        },
        request_id=request_id,
        resource_type="contract" if payload.query_scope == "contract" else None,
        resource_id=payload.contract_id if payload.query_scope == "contract" else None,
    )
    answer = answer if isinstance(answer, BrainAnswerOutput) else BrainAnswerOutput.model_validate(answer)

    # Fuzzy-validate every answer citation against the retrieved context.
    source_text = context["context_text"] or ""
    validated_citations = []
    review = "valid"
    for c in answer.citations:
        result = validate_citation(CitationInput(quote=c.quote), source_text)
        if result.validation_status != "valid":
            review = "needs_review"
        validated_citations.append(
            {
                "quote": c.quote,
                "label": c.label,
                "validation_status": result.validation_status,
                "similarity_score": result.similarity_score,
            }
        )
    if not source_text:
        review = "no_context"

    query = BrainQuery(
        org_id=current_user.org_id,
        query_scope=payload.query_scope,
        question=payload.question,
        contract_id=payload.contract_id,
        project_id=payload.project_id,
        answer=answer.answer,
        citations=validated_citations,
        retrieval_metadata={
            "scope": payload.query_scope,
            "source_count": context["source_count"],
            "graph_facts": len(context["graph_facts"]),
            "vector_chunks": len(context["vector_chunks"]),
            "fulltext_clauses": len(context["fulltext_clauses"]),
            "contract_ids": context["contract_ids"][:50],
            "confidence": answer.confidence,
            "citation_review": review,
            "limitations": answer.limitations,
        },
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(query)
    db.commit()
    db.refresh(query)
    return query


@router.get("/queries")
def list_brain_queries(
    contract_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    from sqlalchemy import select

    q = select(BrainQuery).where(BrainQuery.org_id == current_user.org_id)
    if contract_id:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
        q = q.where(BrainQuery.contract_id == contract_id)
    rows = db.scalars(q.order_by(BrainQuery.created_at.desc()).limit(min(limit, 200))).all()
    return [row for row in rows if _can_view_brain_query(db, query=row, current_user=current_user)]


@router.get("/precedents")
def get_precedents(
    query: str,
    contract_id: str | None = None,
    limit: int = 5,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    if contract_id:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return precedent_contracts(
        db,
        user=current_user,
        query_text=query,
        exclude_contract_id=contract_id,
        limit=min(limit, 20),
    )


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
def trigger_brain_ingestion(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    version = (
        db.get(ContractVersion, contract.current_authoritative_version_id)
        if contract.current_authoritative_version_id
        else None
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Contract has no authoritative version")
    snapshot = (
        db.get(ContractTextSnapshot, version.text_snapshot_id)
        if version.text_snapshot_id
        else None
    )
    job = create_job(
        db,
        org_id=current_user.org_id,
        job_type="contract_brain_ingestion",
        resource_type="contract",
        resource_id=contract.id,
        created_by_user_id=current_user.id,
        idempotency_key=f"contract_brain_ingestion:{version.id}:manual:{utcnow().timestamp()}",
        metadata={
            "contract_version_id": version.id,
            "text_snapshot_id": snapshot.id if snapshot else None,
        },
    )
    db.commit()
    job = db.get(JobRun, job.id)
    dispatch_job(db, job=job)
    db.commit()
    return {"job_id": job.id, "status": job.status}


def _can_view_brain_query(db: Session, *, query: BrainQuery, current_user) -> bool:
    if is_org_admin(current_user) or query.created_by_user_id == current_user.id:
        return True
    if query.contract_id:
        try:
            get_contract_for_user(db, contract_id=query.contract_id, user=current_user)
        except HTTPException:
            return False
        return True
    if query.project_id:
        try:
            get_project_for_user(db, project_id=query.project_id, user=current_user)
        except HTTPException:
            return False
        return True
    return False
