from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.embeddings import _embed
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction, KnowledgeEdge, KnowledgeNode
from app.contract_files.models import ContractEmbedding
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.projects.access import get_project_for_user
from app.projects.models import ProjectContract

MAX_VECTOR_CHUNKS = 8
MAX_GRAPH_FACTS = 40
MAX_CLAUSES = 12
# Candidate clauses pulled before relevance ranking (portfolio scope spans
# many contracts, so the ranked top-N must be chosen from a wide pool).
CLAUSE_CANDIDATE_POOL = 600


def resolve_scope_contract_ids(
    db: Session,
    *,
    user: User,
    scope: str,
    contract_id: str | None,
    project_id: str | None,
) -> list[str]:
    """Permission-aware contract id set for the requested scope."""
    if scope == "contract":
        if not contract_id:
            return []
        get_contract_for_user(db, contract_id=contract_id, user=user)  # access check
        return [contract_id]
    base = select(Contract.id).where(
        Contract.org_id == user.org_id,
        Contract.deleted_at.is_(None),
        accessible_contract_filter(user),
    )
    if scope == "project":
        if not project_id:
            return []
        get_project_for_user(db, project_id=project_id, user=user)
        in_project = select(ProjectContract.contract_id).where(
            ProjectContract.org_id == user.org_id,
            ProjectContract.project_id == project_id,
        )
        base = base.where(Contract.id.in_(in_project))
    return list(db.scalars(base).all())


def _vector_chunks(db: Session, *, question: str, contract_ids: list[str]) -> list[dict]:
    if not contract_ids:
        return []
    try:
        query_vec = _embed([question])[0]
        distance = ContractEmbedding.embedding.cosine_distance(query_vec)
        rows = db.execute(
            select(
                ContractEmbedding.contract_id,
                ContractEmbedding.chunk_text,
                distance.label("distance"),
            )
            .join(Contract, Contract.id == ContractEmbedding.contract_id)
            .where(
                ContractEmbedding.contract_id.in_(contract_ids),
                ContractEmbedding.contract_version_id == Contract.current_authoritative_version_id,
            )
            .order_by(distance)
            .limit(MAX_VECTOR_CHUNKS)
        ).all()
        return [
            {"contract_id": cid, "text": text, "score": round(1.0 - float(dist), 4)}
            for cid, text, dist in rows
        ]
    except Exception:
        return []


def _fulltext_clauses(db: Session, *, question: str, contract_ids: list[str]) -> list[dict]:
    if not contract_ids:
        return []
    terms = [t for t in question.lower().split() if len(t) > 3][:8]
    # Score across a broad candidate pool, THEN take the best — applying
    # the small cap at the DB level returned ~MAX_CLAUSES arbitrary clauses
    # (so portfolio-wide questions matched almost nothing and the answer
    # hedged). Pull a wide pool scoped to the contracts and rank in Python.
    query = (
        select(ClauseExtraction)
        .where(
            ClauseExtraction.contract_id.in_(contract_ids),
            ClauseExtraction.is_stale.is_(False),
        )
        .limit(CLAUSE_CANDIDATE_POOL)
    )
    clauses = db.scalars(query).all()
    scored = []
    for cl in clauses:
        text_l = (cl.text or "").lower()
        type_l = (cl.clause_type or "").lower().replace("_", " ")
        hits = sum(1 for t in terms if t in text_l)
        hits += sum(2 for t in terms if t in type_l)  # clause-type match weighs more
        scored.append((hits, cl))
    scored.sort(key=lambda x: x[0], reverse=True)

    def _fmt(cl) -> dict:
        return {
            "contract_id": cl.contract_id,
            "clause_type": cl.clause_type,
            "clause_id": cl.id,
            "text": (cl.text or "")[:1200],
        }

    matched = [_fmt(cl) for hits, cl in scored if hits > 0][:MAX_CLAUSES]
    if matched:
        return matched
    # No keyword hits: still return a representative slice so the answer
    # is grounded rather than a hedge.
    return [_fmt(cl) for _, cl in scored[:MAX_CLAUSES]]


def _graph_facts(db: Session, *, contract_ids: list[str], clause_types: list[str]) -> list[dict]:
    if not contract_ids:
        return []
    edges = db.scalars(
        select(KnowledgeEdge)
        .where(
            KnowledgeEdge.contract_id.in_(contract_ids),
            KnowledgeEdge.is_stale.is_(False),
        )
        .limit(MAX_GRAPH_FACTS)
    ).all()
    facts = []
    for edge in edges:
        src = db.get(KnowledgeNode, edge.from_node_id)
        dst = db.get(KnowledgeNode, edge.to_node_id)
        if src is None or dst is None:
            continue
        if clause_types and dst.node_type == "clause" and dst.properties.get("clause_type") not in clause_types:
            continue
        facts.append(
            {
                "contract_id": edge.contract_id,
                "fact": f"{src.label} --{edge.edge_type}--> {dst.label}",
                "edge_type": edge.edge_type,
            }
        )
    return facts


def assemble_context(
    db: Session,
    *,
    user: User,
    question: str,
    scope: str,
    contract_id: str | None,
    project_id: str | None,
    parsed,
) -> dict:
    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope=scope, contract_id=contract_id, project_id=project_id
    )
    graph = _graph_facts(db, contract_ids=contract_ids, clause_types=getattr(parsed, "target_clause_types", []) or [])
    vectors = (
        _vector_chunks(db, question=question, contract_ids=contract_ids)
        if getattr(parsed, "needs_vector_search", True)
        else []
    )
    fulltext = (
        _fulltext_clauses(db, question=question, contract_ids=contract_ids)
        if (not vectors or getattr(parsed, "needs_full_text_search", True))
        else []
    )
    parts: list[str] = []
    for g in graph:
        parts.append(f"[graph] {g['fact']}")
    for v in vectors:
        parts.append(f"[snippet] {v['text']}")
    for f in fulltext:
        parts.append(f"[clause:{f['clause_type']}] {f['text']}")
    context_text = "\n\n".join(parts)
    return {
        "contract_ids": contract_ids,
        "graph_facts": graph,
        "vector_chunks": vectors,
        "fulltext_clauses": fulltext,
        "context_text": context_text,
        "source_count": len(graph) + len(vectors) + len(fulltext),
    }


def precedent_contracts(
    db: Session,
    *,
    user: User,
    query_text: str,
    exclude_contract_id: str | None,
    limit: int = 5,
) -> list[dict]:
    accessible = list(
        db.scalars(
            select(Contract.id).where(
                Contract.org_id == user.org_id,
                Contract.deleted_at.is_(None),
                accessible_contract_filter(user),
            )
        ).all()
    )
    if exclude_contract_id and exclude_contract_id in accessible:
        accessible.remove(exclude_contract_id)
    if not accessible:
        return []
    try:
        query_vec = _embed([query_text])[0]
        distance = ContractEmbedding.embedding.cosine_distance(query_vec)
        rows = db.execute(
            select(
                ContractEmbedding.contract_id,
                ContractEmbedding.chunk_text,
                distance.label("distance"),
            )
            .join(Contract, Contract.id == ContractEmbedding.contract_id)
            .where(
                ContractEmbedding.contract_id.in_(accessible),
                ContractEmbedding.contract_version_id == Contract.current_authoritative_version_id,
            )
            .order_by(distance)
            .limit(limit * 3)
        ).all()
    except Exception:
        return []
    seen: dict[str, dict] = {}
    for cid, text, dist in rows:
        if cid in seen:
            continue
        contract = db.get(Contract, cid)
        seen[cid] = {
            "contract_id": cid,
            "title": contract.title if contract else None,
            "similarity": round(1.0 - float(dist), 4),
            "excerpt": text[:400],
        }
        if len(seen) >= limit:
            break
    return list(seen.values())
