import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.controller import ai_controller
from app.ai.schemas import BrainAnswerOutput
from app.contract_brain.grounding import ground_answer
from app.contract_brain.models import BrainQuery
from app.contract_brain.retrieval import (
    accessible_contract_ids,
    aggregate_answer,
    hybrid_sources,
    precedent_contracts,
    resolve_scope_contract_ids,
    sources_to_context,
)
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.access import is_org_admin
from app.core.config import settings
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import JobStatus
from app.core.rate_limit import limiter
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contract-brain", tags=["contract-brain"])


class BrainAskRequest(BaseModel):
    # Uncapped free text lands straight in a Claude prompt, so one request
    # could burn an org's whole daily token budget. 8k chars is far longer
    # than any real question and still cheap to embed.
    question: str = Field(min_length=3, max_length=8000)
    query_scope: str = Field(default="portfolio", pattern="^(contract|portfolio)$")
    contract_id: str | None = None


def _rank_sources_by_citation(sources: dict, citations: list[dict]) -> None:
    """Mark and float the source passages the answer actually cited to the top.

    The answer's validated citations are quotes lifted from the retrieved
    context, so a cited passage contains (or is contained by) a valid quote.
    Marking those `cited` and stable-sorting them first makes the top "built from
    these" cards the real basis of the answer, not just the closest embedding
    match. Mutates ``sources`` in place; leaves the graph bucket untouched.
    """
    quotes = [
        c.get("quote", "").strip()
        for c in citations
        if c.get("validation_status") == "valid" and c.get("quote")
    ]
    quotes = [q for q in quotes if len(q) >= 12]  # ignore trivially-short spans

    def is_cited(text: str) -> bool:
        t = (text or "").strip()
        return bool(t) and any(q in t or t in q for q in quotes)

    for bucket in ("semantic", "clauses", "text"):
        items = sources.get(bucket)
        if not isinstance(items, list):
            continue
        for s in items:
            if isinstance(s, dict):
                s["cited"] = is_cited(s.get("text") or s.get("quote") or "")
        # Stable: cited first, original (score) order preserved within each group.
        items.sort(key=lambda s: not (isinstance(s, dict) and s.get("cited")))


@router.post("/ask")
async def ask_contract_brain(
    payload: BrainAskRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    if payload.query_scope == "contract":
        if not payload.contract_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "contract_id required for contract scope")
        get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)

    request_id = getattr(request.state, "request_id", None)

    # Count/list questions ("how many contracts", "list contracts") are answered
    # deterministically from the database — RAG retrieves snippets, not totals,
    # so the LLM would otherwise guess a number.
    agg = aggregate_answer(
        db,
        user=current_user,
        question=payload.question,
        scope=payload.query_scope,
        contract_id=payload.contract_id,
    )
    if agg is not None:
        query = BrainQuery(
            org_id=current_user.org_id,
            query_scope=payload.query_scope,
            question=payload.question,
            contract_id=payload.contract_id,
            answer=agg["answer"],
            citations=[],
            retrieval_metadata={
                "scope": payload.query_scope,
                "source_count": len(agg["contract_ids"]),
                "graph_facts": 0,
                "vector_chunks": 0,
                "fulltext_clauses": 0,
                "contract_ids": agg["contract_ids"][:50],
                "confidence": "high",
                "citation_review": "deterministic",
                "limitations": None,
                "answer_mode": "database_count",
            },
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(query)
        db.commit()
        db.refresh(query)
        return query

    # Retrieve the SAME hybrid sources that "Find sources only" returns, so the
    # answer is grounded in exactly what the user can see — the two can never
    # diverge. Scope-resolved to portfolio / contract.
    contract_ids = resolve_scope_contract_ids(
        db,
        user=current_user,
        scope=payload.query_scope,
        contract_id=payload.contract_id,
    )
    # hybrid_sources does a sync DB query plus a CPU/HTTP-bound embedding call
    # (_embed) — this is the one Contract Brain route that's `async def`, so
    # calling it directly would block the whole event loop for every question.
    # Sibling routes below (brain_search, get_precedents) are plain `def` and
    # FastAPI already threadpools those; this one needs an explicit hand-off.
    sources = await asyncio.to_thread(
        hybrid_sources,
        db,
        org_id=current_user.org_id,
        contract_ids=contract_ids,
        question=payload.question,
        user=current_user,
    )
    source_text = sources_to_context(sources)

    answer = await ai_controller.run_structured_skill(
        db,
        skill_name="contract_brain_answer",
        org_id=current_user.org_id,
        created_by_user_id=current_user.id,
        input_payload={
            "question": payload.question,
            "retrieved_context": source_text,
            "scope": payload.query_scope,
        },
        request_id=request_id,
        resource_type="contract" if payload.query_scope == "contract" else None,
        resource_id=payload.contract_id if payload.query_scope == "contract" else None,
    )
    answer = answer if isinstance(answer, BrainAnswerOutput) else BrainAnswerOutput.model_validate(answer)

    # Verify → attribute → cap confidence → guard fabrication, via the ONE
    # shared grounding function the assistant tool now uses too, so both paths
    # can never diverge on how trustworthy an answer is.
    grounded = ground_answer(answer, source_text)

    # Float the sources the answer actually cited to the top of the cards — raw
    # semantic similarity can rank a close-but-unused passage first, which is what
    # made the top "built from these" card not match the answer's basis.
    _rank_sources_by_citation(sources, grounded["citations"])

    n_sem, n_cl, n_tx = len(sources["semantic"]), len(sources["clauses"]), len(sources["text"])
    query = BrainQuery(
        org_id=current_user.org_id,
        query_scope=payload.query_scope,
        question=payload.question,
        contract_id=payload.contract_id,
        answer=grounded["display_answer"],
        citations=grounded["citations"],
        retrieval_metadata={
            "scope": payload.query_scope,
            "source_count": n_sem + n_cl + n_tx + len(sources.get("graph", [])),
            "graph_facts": len(sources.get("graph", [])),
            "vector_chunks": n_sem,
            "fulltext_clauses": n_cl + n_tx,
            "contract_ids": contract_ids[:50],
            "confidence": grounded["confidence"],
            "model_confidence": grounded["model_confidence"],
            "citation_review": grounded["citation_review"],
            "grounding": grounded["grounding"],
            "verified_citations": grounded["verified_citations"],
            "total_citations": grounded["total_citations"],
            "limitations": grounded["limitations"],
            # The exact sources this answer was built from — the frontend shows
            # them beneath the answer, identical to "Find sources only".
            "sources": sources,
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
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    q = select(BrainQuery).where(
        BrainQuery.org_id == current_user.org_id,
        _visible_brain_queries(db, current_user),
    )
    if contract_id:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
        q = q.where(BrainQuery.contract_id == contract_id)
    rows = db.scalars(q.order_by(BrainQuery.created_at.desc()).limit(min(limit, 200))).all()
    accessible = accessible_contract_ids(db, user=current_user)
    return [_viewer_copy(row, viewer_id=current_user.id, accessible_ids=accessible) for row in rows]


@router.get("/precedents")
def get_precedents(
    query: str,
    contract_id: str | None = None,
    limit: int = Query(default=5, ge=1, le=50),
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


@router.get("/search")
def brain_search(
    q: str,
    limit: int = Query(default=8, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Hybrid intelligent search across the accessible portfolio.

    Three retrieval modes in one call, no LLM cost:
    - semantic: pgvector cosine over embedded chunks (finds meaning, not words)
    - clauses:  full-text over extracted clauses (headings + text)
    - text:     full-text over raw contract text, with excerpt highlights
    """
    ids = list(
        db.scalars(
            select(Contract.id).where(
                Contract.org_id == current_user.org_id,
                Contract.deleted_at.is_(None),
                accessible_contract_filter(current_user),
            )
        ).all()
    )
    sources = hybrid_sources(
        db, org_id=current_user.org_id, contract_ids=ids, question=q, limit=limit, user=current_user
    )
    return {"query": q, **sources}


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.rate_limit_ai_skill)
def trigger_brain_ingestion(
    request: Request,
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:update")),
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
    active = _active_ingestion_job(db, org_id=current_user.org_id, contract_id=contract.id)
    if active is not None:
        return {"job_id": active.id, "status": active.status}
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


def _active_ingestion_job(db: Session, *, org_id: str, contract_id: str) -> JobRun | None:
    """A rebuild already queued or running answers a repeat click, so clicks can't
    pile jobs onto the worker. Once it finishes, a later rebuild is allowed again."""
    return db.scalar(
        select(JobRun)
        .where(
            JobRun.org_id == org_id,
            JobRun.job_type == "contract_brain_ingestion",
            JobRun.resource_id == contract_id,
            JobRun.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .order_by(JobRun.created_at.desc())
        .limit(1)
    )


def _visible_brain_queries(db: Session, current_user):
    """Which saved answers this user may see, as a SQL condition so the limit counts
    only those — filtering after the limit hid a user's own older questions behind
    the org's newest 200. Their own; contract-scoped answers they can open
    (ethical walls bind admins too); portfolio-wide answers only for admins."""
    clauses = [
        BrainQuery.created_by_user_id == current_user.id,
        BrainQuery.contract_id.in_(
            select(Contract.id).where(
                Contract.deleted_at.is_(None),
                accessible_contract_filter(current_user),
            )
        ),
    ]
    if is_org_admin(current_user):
        # A portfolio-wide answer names no record whose access could be checked.
        clauses.append(BrainQuery.contract_id.is_(None))
    return or_(*clauses)

_WITHHELD_ANSWER = "This answer drew on contracts you don't have access to, so it isn't shown."


def _viewer_copy(query: BrainQuery, *, viewer_id: str, accessible_ids: set[str]) -> dict:
    """A saved answer as this viewer may see it. Access was checked for whoever
    asked; anyone else gets only the sources they can open, and no answer text if
    it drew on a contract they can't. Builds a new dict: the stored row is untouched."""
    data = {column.name: getattr(query, column.name) for column in BrainQuery.__table__.columns}
    if query.created_by_user_id == viewer_id:
        return data
    meta = dict(query.retrieval_metadata or {})
    used = set(meta.get("contract_ids") or [])
    if isinstance(meta.get("sources"), dict):
        sources = dict(meta["sources"])
        for lens in ("semantic", "clauses", "text"):
            items = [s for s in (sources.get(lens) or []) if isinstance(s, dict)]
            used |= {s["contract_id"] for s in items if s.get("contract_id")}
            sources[lens] = [s for s in items if s.get("contract_id") in accessible_ids]
        graph = []
        for fact in sources.get("graph") or []:
            if not isinstance(fact, dict):
                continue
            ids = set(fact.get("contract_ids") or []) | ({fact["contract_id"]} if fact.get("contract_id") else set())
            used |= ids
            if ids and ids <= accessible_ids:
                graph.append(fact)  # facts naming no contract id can't be checked, so they're withheld
        sources["graph"] = graph
        meta["sources"] = sources
    meta["contract_ids"] = [cid for cid in (meta.get("contract_ids") or []) if cid in accessible_ids]
    if used - accessible_ids:
        data["answer"] = _WITHHELD_ANSWER
        data["citations"] = []
        meta["withheld_for_viewer"] = True
    data["retrieval_metadata"] = meta
    return data
