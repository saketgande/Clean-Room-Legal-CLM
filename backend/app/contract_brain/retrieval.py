import logging
import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai.embeddings import _embed
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction, KnowledgeEdge, KnowledgeNode
from app.contract_files.models import ContractEmbedding, ContractTextSnapshot
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.projects.access import get_project_for_user
from app.projects.models import ProjectContract
from app.search.fts import (
    clause_vector,
    fts_usable,
    like_contains,
    snapshot_headline,
    snapshot_vector,
    text_matches,
)

logger = logging.getLogger(__name__)


def hybrid_sources(
    db: Session,
    *,
    org_id: str,
    contract_ids: list[str],
    question: str,
    limit: int = 8,
) -> dict:
    """The ONE hybrid retrieval used by both /search ("Find sources only") and
    /ask. Because the answer is grounded in exactly what this returns, the two
    can never diverge — the sources under an answer are the same sources Find
    shows. Three complementary lenses, no LLM cost:
      - semantic: pgvector cosine over embedded chunks (meaning, not words)
      - clauses:  full-text over extracted, classified clauses
      - text:     full-text over raw contract text, with highlighted excerpts
    """
    limit = min(max(limit, 1), 25)
    if not contract_ids:
        return {"semantic": [], "clauses": [], "text": []}
    titles = dict(
        db.execute(
            select(Contract.id, Contract.title).where(Contract.id.in_(contract_ids))
        ).all()
    )
    ids = list(titles)

    from app.contract_brain.rerank import rerank_enabled, rerank_order

    semantic: list[dict] = []
    try:
        # When a reranker is configured, over-fetch a wider candidate pool and
        # let the cross-encoder pick the true top-N. Otherwise take the top-N
        # by cosine directly.
        do_rerank = rerank_enabled()
        candidate_limit = limit * 4 if do_rerank else limit
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
                ContractEmbedding.contract_id.in_(ids),
                ContractEmbedding.contract_version_id
                == Contract.current_authoritative_version_id,
            )
            .order_by(distance)
            .limit(candidate_limit)
        ).all()
        semantic = [
            {
                "contract_id": cid,
                "contract_title": titles.get(cid, ""),
                "text": chunk[:600],
                "score": round(1.0 - float(dist), 4),
            }
            for cid, chunk, dist in rows
        ]
        if do_rerank and semantic:
            order = rerank_order(question, [s["text"] for s in semantic], limit)
            semantic = [semantic[i] for i in order]
    except Exception:
        logger.warning("hybrid retrieval: vector search failed", exc_info=True)

    use_fts = fts_usable(db, question)
    tsq = func.websearch_to_tsquery("english", question)
    q_like = like_contains(question)

    clause_query = (
        select(ClauseExtraction, Contract.title)
        .join(Contract, Contract.id == ClauseExtraction.contract_id)
        .where(
            ClauseExtraction.org_id == org_id,
            ClauseExtraction.is_stale.is_(False),
            ClauseExtraction.contract_id.in_(ids),
        )
    )
    if use_fts:
        cvec = clause_vector()
        clause_query = clause_query.where(
            or_(cvec.op("@@")(tsq), ClauseExtraction.clause_type.ilike(q_like, escape="\\"))
        ).order_by(func.ts_rank(cvec, tsq).desc())
    else:
        clause_query = clause_query.where(
            or_(
                ClauseExtraction.text.ilike(q_like, escape="\\"),
                ClauseExtraction.heading.ilike(q_like, escape="\\"),
                ClauseExtraction.clause_type.ilike(q_like, escape="\\"),
            )
        )
    clauses = [
        {
            "clause_id": clause.id,
            "contract_id": clause.contract_id,
            "contract_title": title,
            "clause_type": clause.clause_type,
            "heading": clause.heading,
            "excerpt": clause.text[:600],
        }
        for clause, title in db.execute(clause_query.limit(limit)).all()
    ]

    headline = (
        snapshot_headline(tsq)
        if use_fts
        else func.substr(ContractTextSnapshot.text, 1, 0)
    )
    text_query = (
        select(ContractTextSnapshot, Contract.title, headline.label("headline"))
        .join(Contract, Contract.id == ContractTextSnapshot.contract_id)
        .where(
            ContractTextSnapshot.org_id == org_id,
            ContractTextSnapshot.deleted_at.is_(None),
            ContractTextSnapshot.contract_id.in_(ids),
        )
    )
    if use_fts:
        svec = snapshot_vector()
        text_query = text_query.where(svec.op("@@")(tsq)).order_by(
            func.ts_rank(svec, tsq).desc()
        )
    else:
        text_query = text_query.where(
            ContractTextSnapshot.text.ilike(q_like, escape="\\")
        )
    text_hits = []
    for snapshot, title, headline_text in db.execute(text_query.limit(limit)).all():
        matches = text_matches(snapshot.text, question)
        if not matches and headline_text:
            matches = [
                {"start_char": -1, "end_char": -1, "excerpt": frag.strip()}
                for frag in str(headline_text).split(" ... ")
                if frag.strip()
            ]
        text_hits.append(
            {
                "contract_id": snapshot.contract_id,
                "contract_title": title,
                "matches": matches[:3],
            }
        )

    return {"semantic": semantic, "clauses": clauses, "text": text_hits}


def sources_to_context(sources: dict) -> str:
    """Flatten hybrid sources into the exact text block the answer LLM sees.
    Every line the model reads is a source the user can also open."""
    parts: list[str] = []
    for s in sources.get("semantic", []):
        parts.append(f"[snippet · {s['contract_title']}] {s['text']}")
    for c in sources.get("clauses", []):
        head = c.get("heading") or c.get("clause_type") or "clause"
        parts.append(f"[clause · {c['contract_title']} · {head}] {c['excerpt']}")
    for t in sources.get("text", []):
        for m in t.get("matches", []):
            parts.append(f"[text · {t['contract_title']}] {m['excerpt']}")
    return "\n\n".join(parts)

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


# --- Deterministic answers for count / list questions ---------------------
# RAG retrieves clause *snippets*, not totals, so "how many / list contracts"
# can't be answered by the LLM (it guesses). These are answered straight from
# the database. Anchored to the whole question so content questions like
# "how many contracts have an indemnity cap" fall through to retrieval.
_FILLER = (
    r"(are there|do (i|we) have|are in (my |the )?portfolio|in (my |the )?portfolio"
    r"|exist|in total|total|right now|now|currently|in this project|in the project"
    r"|altogether|here)"
)
_COUNT_RE = re.compile(
    rf"^\s*(how many|the number of|number of|count(\s+of)?|total(\s+number\s+of)?)\s+"
    rf"(contracts?|agreements?)\b\s*(?:{_FILLER})?\s*[?.]?\s*$",
    re.IGNORECASE,
)
_LIST_RE = re.compile(
    rf"^\s*(list|show(\s+me)?|what|which)\s+(all\s+|my\s+|the\s+|our\s+)?"
    rf"(contracts?|agreements?)\b\s*(?:{_FILLER}|do (i|we) have)?\s*[?.]?\s*$",
    re.IGNORECASE,
)


def aggregate_answer(
    db: Session,
    *,
    user: User,
    question: str,
    scope: str,
    contract_id: str | None,
    project_id: str | None,
) -> dict | None:
    """Answer a plain count/list-of-contracts question from the database. Returns
    None for anything else, so content questions still go through retrieval."""
    q = question.strip()
    is_count = bool(_COUNT_RE.match(q))
    is_list = bool(_LIST_RE.match(q)) and not is_count
    if not (is_count or is_list):
        return None

    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope=scope, contract_id=contract_id, project_id=project_id
    )
    n = len(contract_ids)
    label = {
        "portfolio": "portfolio",
        "project": "project",
        "contract": "selected contract",
    }.get(scope, "portfolio")

    if is_count:
        verb = "is" if n == 1 else "are"
        noun = "contract" if n == 1 else "contracts"
        return {"answer": f"There {verb} {n} {noun} in your {label}.", "contract_ids": contract_ids}

    if n == 0:
        return {"answer": f"There are no contracts in your {label}.", "contract_ids": []}
    titles = list(
        db.scalars(
            select(Contract.title)
            .where(Contract.id.in_(contract_ids))
            .order_by(Contract.created_at.desc())
            .limit(50)
        ).all()
    )
    lines = "\n".join(f"- {t}" for t in titles)
    more = f"\n…and {n - 50} more." if n > 50 else ""
    plural = "contract" if n == 1 else "contracts"
    return {
        "answer": f"Your {label} has {n} {plural}:\n{lines}{more}",
        "contract_ids": contract_ids,
    }


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
        # Vector store failures are recoverable — the chat path can still
        # return a hedged answer — but we want a signal in the logs so we
        # can detect a broken vector index rather than silently degrading.
        logger.warning("vector retrieval failed", exc_info=True)
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
    if not edges:
        return []

    # Bulk-fetch all referenced nodes in a single IN query rather than two
    # per edge. With MAX_GRAPH_FACTS=40 this trims 80 round-trips off every
    # chat question — the hottest AI retrieval path.
    node_ids = {edge.from_node_id for edge in edges} | {edge.to_node_id for edge in edges}
    nodes_by_id: dict[str, KnowledgeNode] = {
        node.id: node
        for node in db.scalars(
            select(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids))
        ).all()
    }

    facts = []
    for edge in edges:
        src = nodes_by_id.get(edge.from_node_id)
        dst = nodes_by_id.get(edge.to_node_id)
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
        logger.warning("precedent vector retrieval failed", exc_info=True)
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
