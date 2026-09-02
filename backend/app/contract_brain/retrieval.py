import logging
import re
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai.embeddings import _embed
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction, KnowledgeEdge, KnowledgeNode
from app.contract_files.models import ContractEmbedding, ContractTextSnapshot
from app.obligations.models import Obligation
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.matters.access import get_project_for_user
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
        return {"semantic": [], "clauses": [], "text": [], "graph": []}
    titles = dict(
        db.execute(
            select(Contract.id, Contract.title).where(
                Contract.id.in_(contract_ids),
                Contract.org_id == org_id,
            )
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
                ContractEmbedding.org_id == org_id,
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

    # Knowledge-graph relationships (party ↔ contract, contract ↔ clause,
    # obligations, approvals…) — the lens the /ask page was missing. Same
    # source as the assistant's tool, so both now reason over the graph.
    # Ordered high-signal first: lineage, temporal, clause-reuse and cohort are
    # few and portfolio-defining, so the global cap trims the long shared-rule
    # tail rather than dropping them. One cap here means the answer context and
    # the source cards see the identical bounded set — no count drift.
    graph = (
        lineage_facts(db, org_id=org_id, contract_ids=ids)
        + temporal_facts(db, org_id=org_id, contract_ids=ids)
        + clause_language_matches(db, org_id=org_id, contract_ids=ids)
        + cohort_facts(db, org_id=org_id, contract_ids=ids)
        + shared_entity_links(db, org_id=org_id, contract_ids=ids)
        + _graph_facts(db, contract_ids=ids, clause_types=[])
    )[:MAX_GRAPH_FACTS]

    return {"semantic": semantic, "clauses": clauses, "text": text_hits, "graph": graph}


def sources_to_context(sources: dict) -> str:
    """Flatten hybrid sources into the exact text block the answer LLM sees.
    Every line the model reads is a source the user can also open."""
    parts: list[str] = []
    for g in sources.get("graph", []):
        # Tag the portfolio-level facts distinctly. A plain [graph] line reads
        # like one more snippet; [portfolio] / [lineage] tell the model this
        # fact spans contracts and answers "across the book" questions.
        if g.get("shared_with_count"):
            parts.append(f"[portfolio] {g['fact']}")
        elif g.get("direction") in ("parent", "child"):
            parts.append(f"[lineage] {g['fact']}")
        elif g.get("kind") in ("expired", "expiring", "obligation_due", "cascade"):
            parts.append(f"[temporal] {g['fact']}")
        elif g.get("similarity"):
            parts.append(f"[clause-reuse] {g['fact']}")
        else:
            parts.append(f"[graph] {g['fact']}")
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
    matter_id: str | None,
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
        if not matter_id:
            return []
        get_project_for_user(db, matter_id=matter_id, user=user)
        # One matter -> many contracts: filter by the canonical contract.matter_id.
        base = base.where(Contract.matter_id == matter_id)
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
    matter_id: str | None,
) -> dict | None:
    """Answer a plain count/list-of-contracts question from the database. Returns
    None for anything else, so content questions still go through retrieval."""
    q = question.strip()
    is_count = bool(_COUNT_RE.match(q))
    is_list = bool(_LIST_RE.match(q)) and not is_count
    if not (is_count or is_list):
        return None

    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope=scope, contract_id=contract_id, matter_id=matter_id
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
        from app.contract_brain.rerank import rerank_enabled, rerank_order

        # Same second-stage reranking the /ask page path uses — over-fetch a
        # wider pool, then let the cross-encoder pick the true top-N. Previously
        # the assistant path took raw cosine order with no rerank.
        do_rerank = rerank_enabled()
        candidate_limit = MAX_VECTOR_CHUNKS * 4 if do_rerank else MAX_VECTOR_CHUNKS
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
            .limit(candidate_limit)
        ).all()
        chunks = [
            {"contract_id": cid, "text": text, "score": round(1.0 - float(dist), 4)}
            for cid, text, dist in rows
        ]
        if do_rerank and chunks:
            order = rerank_order(question, [c["text"] for c in chunks], MAX_VECTOR_CHUNKS)
            chunks = [chunks[i] for i in order]
        return chunks[:MAX_VECTOR_CHUNKS]
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




def lineage_facts(db: Session, *, org_id: str, contract_ids: list[str]) -> list[dict]:
    """Lineage in both directions for the contracts in scope:

      - upward:   "this SoW is likely governed by <MSA>"
      - downward: "these SoWs/DPAs hang off this MSA and are affected if it ends"

    Downward is the one that answers "what dies if this master terminates" —
    the question the graph existed to make answerable. Inferred edges are
    labelled as such so an answer never states an inferred parent as fact.
    """
    if not contract_ids:
        return []
    hubs = {
        n.contract_id: n
        for n in db.scalars(
            select(KnowledgeNode).where(
                KnowledgeNode.node_type == "contract",
                KnowledgeNode.contract_id.in_(contract_ids),
                KnowledgeNode.is_stale.is_(False),
            )
        )
    }
    if not hubs:
        return []
    hub_ids = {n.id for n in hubs.values()}

    edges = db.scalars(
        select(KnowledgeEdge).where(
            KnowledgeEdge.org_id == org_id,
            KnowledgeEdge.edge_type.in_(("governed_by", "supersedes")),
            KnowledgeEdge.is_stale.is_(False),
            or_(
                KnowledgeEdge.from_node_id.in_(hub_ids),   # we are the child
                KnowledgeEdge.to_node_id.in_(hub_ids),     # we are the parent
            ),
        )
    ).all()
    if not edges:
        return []

    node_ids = {e.from_node_id for e in edges} | {e.to_node_id for e in edges}
    labels = {
        n.id: n.label
        for n in db.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids)))
    }

    facts = []
    for e in edges:
        conf = (e.properties or {}).get("confidence")
        hedge = f" (inferred, {conf})" if (e.properties or {}).get("inferred") else ""
        child, parent = labels.get(e.from_node_id), labels.get(e.to_node_id)
        if e.from_node_id in hub_ids:      # upward: we are the child
            facts.append({"fact": f"{child} is {e.edge_type.replace('_',' ')} {parent}{hedge}",
                          "direction": "parent"})
        if e.to_node_id in hub_ids:        # downward: we are the parent
            facts.append({"fact": f"{child} depends on {parent} — affected if it is amended or terminated{hedge}",
                          "direction": "child"})
    return facts



def temporal_facts(
    db: Session, *, org_id: str, contract_ids: list[str], horizon_days: int = 90
) -> list[dict]:
    """Time as a first-class dimension: expiry proximity, upcoming obligations,
    and — the graph-native one — renewal cascades.

    A renewal cascade is where temporal meets lineage: a Master expiring soon
    drags every SoW that depends on it. That is a fact neither a date column nor
    a vector search can produce on its own; it needs the ``governed_by`` edges
    the graph now holds.
    """
    if not contract_ids:
        return []
    today = date.today()
    soon = today + timedelta(days=horizon_days)
    facts: list[dict] = []

    contracts = {
        c.id: c
        for c in db.scalars(
            select(Contract).where(Contract.id.in_(contract_ids))
        )
    }

    for cid, c in contracts.items():
        exp = c.expiration_date
        if exp is None:
            continue
        if exp < today:
            facts.append({"fact": f"{c.title} EXPIRED on {exp}", "kind": "expired"})
        elif exp <= soon:
            facts.append({"fact": f"{c.title} expires on {exp} ({(exp - today).days} days) — "
                                  f"renewal or termination decision is due now", "kind": "expiring"})

    # obligations coming due on the scoped contracts
    due = db.scalars(
        select(Obligation).where(
            Obligation.org_id == org_id,
            Obligation.contract_id.in_(contract_ids),
            Obligation.due_date.isnot(None),
            Obligation.due_date <= soon,
            Obligation.deleted_at.is_(None),
        ).order_by(Obligation.due_date)
    ).all()
    for ob in due[:8]:
        overdue = " (OVERDUE)" if ob.due_date < today else ""
        facts.append({"fact": f"Obligation '{ob.obligation_type or ob.description[:40]}' "
                              f"due {ob.due_date}{overdue}", "kind": "obligation_due"})

    # renewal cascade: for a scoped MASTER expiring soon, name the dependents
    hubs = {
        n.contract_id: n.id
        for n in db.scalars(
            select(KnowledgeNode).where(
                KnowledgeNode.node_type == "contract",
                KnowledgeNode.contract_id.in_(contract_ids),
                KnowledgeNode.is_stale.is_(False),
            )
        )
    }
    if hubs:
        # children that point at a scoped contract via governed_by
        children = db.execute(
            select(KnowledgeEdge.to_node_id, KnowledgeEdge.contract_id)
            .where(
                KnowledgeEdge.org_id == org_id,
                KnowledgeEdge.edge_type == "governed_by",
                KnowledgeEdge.to_node_id.in_(hubs.values()),
                KnowledgeEdge.is_stale.is_(False),
            )
        ).all()
        # group children by the parent (scoped) contract
        by_parent: dict[str, list[str]] = {}
        parent_of_hub = {hub_id: cid for cid, hub_id in hubs.items()}
        for parent_hub_id, child_cid in children:
            by_parent.setdefault(parent_of_hub[parent_hub_id], []).append(child_cid)
        for parent_cid, child_cids in by_parent.items():
            parent = contracts.get(parent_cid)
            if parent and parent.expiration_date and parent.expiration_date <= soon:
                child_titles = [
                    t for (t,) in db.execute(
                        select(Contract.title).where(Contract.id.in_(child_cids))
                    ).all()
                ]
                facts.append({
                    "fact": f"RENEWAL CASCADE: {parent.title} expires {parent.expiration_date} and "
                            f"{len(child_cids)} dependent agreement(s) rely on it: "
                            + ", ".join(child_titles[:4]),
                    "kind": "cascade",
                })
    return facts



def clause_language_matches(
    db: Session, *, org_id: str, contract_ids: list[str],
    threshold: int = 80, limit: int = 8
) -> list[dict]:
    """Where else the SAME wording appears. For each clause on the scoped
    contracts, finds clauses of the same type in OTHER contracts whose text is
    near-identical — reused templates, propagated concessions, boilerplate.

    Lexical, not semantic: "the same indemnity wording" means the words match,
    so rapidfuzz over same-type clause text is the right tool — and comparing
    only within a clause_type keeps it cheap. This is the fact the GraphRAG
    literature warns vector search gets WRONG: similar wording across different
    contracts is exactly what naive similarity conflates, so we surface it as an
    explicit, contract-named relationship instead.
    """
    if not contract_ids:
        return []
    try:
        from rapidfuzz import fuzz
    except ImportError:                                   # pragma: no cover
        return []

    mine = db.execute(
        select(ClauseExtraction.clause_type, ClauseExtraction.text, ClauseExtraction.heading)
        .where(
            ClauseExtraction.org_id == org_id,
            ClauseExtraction.contract_id.in_(contract_ids),
            ClauseExtraction.is_stale.is_(False),
            ClauseExtraction.text.isnot(None),
        )
    ).all()
    if not mine:
        return []
    my_types = {ct for ct, _, _ in mine if ct}

    # candidate clauses of the same types, in OTHER contracts, with their titles
    candidates = db.execute(
        select(ClauseExtraction.clause_type, ClauseExtraction.text, Contract.title)
        .join(Contract, Contract.id == ClauseExtraction.contract_id)
        .where(
            ClauseExtraction.org_id == org_id,
            ClauseExtraction.clause_type.in_(my_types),
            ClauseExtraction.contract_id.notin_(contract_ids),
            ClauseExtraction.is_stale.is_(False),
            ClauseExtraction.text.isnot(None),
        )
    ).all()
    if not candidates:
        return []
    by_type: dict[str, list[tuple[str, str]]] = {}
    for ct, txt, title in candidates:
        by_type.setdefault(ct, []).append((txt, title))

    facts: list[dict] = []
    seen: set = set()
    for ct, mytext, heading in mine:
        best_score, best_title = 0, None
        for cand_text, cand_title in by_type.get(ct, []):
            score = int(fuzz.token_sort_ratio(mytext, cand_text))
            if score > best_score:
                best_score, best_title = score, cand_title
        if best_score >= threshold and (ct, best_title) not in seen:
            seen.add((ct, best_title))
            label = heading or ct.replace("_", " ")
            facts.append({
                "fact": f"The {label} clause is ~{best_score}% identical to the one in "
                        f"{best_title} — reused or templated language",
                "clause_type": ct,
                "similarity": best_score,
            })
    facts.sort(key=lambda f: f["similarity"], reverse=True)
    return facts[:limit]


def cohort_facts(
    db: Session, *, org_id: str, contract_ids: list[str], limit: int = 8
) -> list[dict]:
    """Two hops: contracts that share MORE THAN ONE shared entity with the ones
    in scope. This is the multi-hop step — a contract sharing the counterparty
    AND the governing law AND the signer is a far stronger 'related to this'
    signal than one sharing a single common jurisdiction.

    Where shared_entity_links answers "who else touches X", this answers "which
    contracts are most connected to mine, and on how many fronts" — the
    portfolio cohort the current contracts sit in.
    """
    if not contract_ids:
        return []

    # hop 1: the shared entities the scoped contracts touch
    my_entities = {
        row[0]
        for row in db.execute(
            select(KnowledgeEdge.to_node_id)
            .join(KnowledgeNode, KnowledgeNode.id == KnowledgeEdge.to_node_id)
            .where(
                KnowledgeEdge.org_id == org_id,
                KnowledgeEdge.contract_id.in_(contract_ids),
                KnowledgeEdge.is_stale.is_(False),
                KnowledgeNode.contract_id.is_(None),
                KnowledgeNode.is_stale.is_(False),
            )
        ).all()
    }
    if not my_entities:
        return []

    # hop 2: every OTHER contract on those entities, and which entities it shares
    rows = db.execute(
        select(KnowledgeEdge.contract_id, KnowledgeEdge.to_node_id)
        .where(
            KnowledgeEdge.org_id == org_id,
            KnowledgeEdge.to_node_id.in_(my_entities),
            KnowledgeEdge.contract_id.notin_(contract_ids),
            KnowledgeEdge.is_stale.is_(False),
        )
    ).all()
    if not rows:
        return []

    labels = {
        n.id: (n.node_type, n.label)
        for n in db.scalars(
            select(KnowledgeNode).where(KnowledgeNode.id.in_(my_entities))
        )
    }

    # Not all shared entities mean the same thing. A shared counterparty, signer
    # or governing law is a real "these are the same relationship" signal; a
    # shared playbook-rule deviation is common across the whole book (nearly
    # every contract breaks the confidentiality rule) and says little. So a
    # cohort must share at least one HIGH-SIGNAL entity, and we rank by those.
    HIGH_SIGNAL = {"party", "person", "jurisdiction"}
    by_contract: dict[str, set] = {}
    for cid, node_id in rows:
        by_contract.setdefault(cid, set()).add(node_id)

    strong = {}
    for cid, ents in by_contract.items():
        high = [n for n in ents if labels.get(n, ("", ""))[0] in HIGH_SIGNAL]
        if high:                                   # at least one meaningful match
            strong[cid] = (ents, high)
    if not strong:
        return []
    titles = dict(
        db.execute(
            select(Contract.id, Contract.title).where(Contract.id.in_(strong))
        ).all()
    )

    facts = []
    for cid, (ents, high) in sorted(
        strong.items(), key=lambda kv: (len(kv[1][1]), len(kv[1][0])), reverse=True
    ):
        # lead with the high-signal overlap (the counterparty / signer / law)
        high_labels = [labels[n][1] for n in high if n in labels]
        facts.append(
            {
                "fact": f"{titles.get(cid) or cid} is closely related — shares "
                        f"{', '.join(high_labels[:3])}"
                        + (f" and {len(ents) - len(high)} other terms" if len(ents) > len(high) else ""),
                "contract_id": cid,
                "shared_count": len(ents),
                "high_signal_count": len(high),
            }
        )
    return facts[:limit]


def shared_entity_links(
    db: Session, *, org_id: str, contract_ids: list[str], limit: int = 12
) -> list[dict]:
    """One hop out: from the contracts in scope, to the shared entities they
    touch, to the OTHER contracts touching the same entity.

    This is the first genuinely portfolio-level fact the graph can produce.
    Before shared entities existed, every node belonged to exactly one contract
    and there was nothing to traverse — a question like "who else deviates from
    this rule" was unanswerable regardless of how the graph was queried.
    """
    if not contract_ids:
        return []

    # contracts in scope -> shared nodes they point at
    inbound = db.execute(
        select(KnowledgeEdge.to_node_id, KnowledgeEdge.contract_id, KnowledgeEdge.edge_type)
        .join(KnowledgeNode, KnowledgeNode.id == KnowledgeEdge.to_node_id)
        .where(
            KnowledgeEdge.org_id == org_id,
            KnowledgeEdge.contract_id.in_(contract_ids),
            KnowledgeEdge.is_stale.is_(False),
            KnowledgeNode.contract_id.is_(None),      # shared entities only
            KnowledgeNode.is_stale.is_(False),
        )
    ).all()
    if not inbound:
        return []

    shared_ids = {row[0] for row in inbound}
    nodes = {
        n.id: n
        for n in db.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(shared_ids)))
    }

    # the same shared nodes, seen from every OTHER contract
    outbound = db.execute(
        select(KnowledgeEdge.to_node_id, KnowledgeEdge.contract_id, KnowledgeEdge.properties)
        .where(
            KnowledgeEdge.org_id == org_id,
            KnowledgeEdge.to_node_id.in_(shared_ids),
            KnowledgeEdge.contract_id.notin_(contract_ids),
            KnowledgeEdge.is_stale.is_(False),
        )
    ).all()
    if not outbound:
        return []

    peers: dict[str, list[tuple[str, dict]]] = {}
    for node_id, cid, props in outbound:
        peers.setdefault(node_id, []).append((cid, props or {}))

    titles = dict(
        db.execute(
            select(Contract.id, Contract.title).where(
                Contract.id.in_({cid for lst in peers.values() for cid, _ in lst})
            )
        ).all()
    )

    facts: list[dict] = []
    for node_id, others in peers.items():
        node = nodes.get(node_id)
        if node is None:
            continue
        # Rank by how widely shared the entity is — a rule 20 contracts deviate
        # from is a portfolio problem; one contract deviating is a one-off.
        sample = [titles.get(cid) or cid for cid, _ in others][:5]
        severities = sorted({(p.get("severity") or "").lower() for _, p in others} - {""})
        facts.append(
            {
                "entity": node.label,
                "entity_type": node.node_type,
                "shared_with_count": len(others),
                "fact": (
                    f"{node.label} — also affects {len(others)} other contract(s): "
                    + ", ".join(sample)
                    + (f" [severity: {', '.join(severities)}]" if severities else "")
                ),
                "contract_ids": [cid for cid, _ in others],
            }
        )
    facts.sort(key=lambda f: f["shared_with_count"], reverse=True)
    return facts[:limit]


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
    matter_id: str | None,
    parsed,
) -> dict:
    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope=scope, contract_id=contract_id, matter_id=matter_id
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
