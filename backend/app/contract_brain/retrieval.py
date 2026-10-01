import logging
import re
from datetime import timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.ai.embeddings import _embed
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction, KnowledgeEdge, KnowledgeNode
from app.contract_files.models import ContractEmbedding, ContractTextSnapshot, ContractVersion
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.database import utcnow
from app.core.enums import ContractLifecycleStage, ObligationStatus
from app.obligations.models import Obligation
from app.search.fts import (
    clause_vector,
    fts_usable,
    like_contains,
    or_terms,
    snapshot_headline,
    snapshot_vector,
    text_matches,
)

logger = logging.getLogger(__name__)


def _current_clauses():
    """Clauses from the contract's current authoritative version only (callers
    join Contract). One definition of "current text" for every clause lens."""
    return and_(
        ClauseExtraction.is_stale.is_(False),
        ClauseExtraction.contract_version_id == Contract.current_authoritative_version_id,
    )


def accessible_contract_ids(db: Session, *, user: User) -> set[str]:
    """Every contract this user may open (org, ethical walls, clearance). Lenses
    that reach OTHER contracts than the ones in scope must stay inside this set."""
    return set(
        db.scalars(
            select(Contract.id).where(
                Contract.org_id == user.org_id,
                Contract.deleted_at.is_(None),
                accessible_contract_filter(user),
            )
        ).all()
    )


def _reaches_inaccessible(nodes: dict, node_ids, accessible_ids: set[str]) -> bool:
    """True if any of these graph nodes belongs to a contract the user can't open."""
    for node_id in node_ids:
        node = nodes.get(node_id)
        if node is not None and node.contract_id and node.contract_id not in accessible_ids:
            return True
    return False


def hybrid_sources(
    db: Session,
    *,
    org_id: str,
    contract_ids: list[str],
    question: str,
    user: User,
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
    accessible = accessible_contract_ids(db, user=user)
    ids = [cid for cid in titles if cid in accessible]

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
    # /ask always sends a natural-language question, and websearch_to_tsquery
    # ANDs every term — one word absent from the corpus ("MSAs", "our") silently
    # zeroes both keyword lenses and leaves the answer on the vector lens alone.
    # Tried only when the strict AND form matched nothing, so exact keyword
    # queries keep their precision.
    tsq_or = func.websearch_to_tsquery("english", or_terms(question))
    q_like = like_contains(question)

    clause_base = (
        select(ClauseExtraction, Contract.title)
        .join(Contract, Contract.id == ClauseExtraction.contract_id)
        .where(
            ClauseExtraction.org_id == org_id,
            _current_clauses(),
            ClauseExtraction.contract_id.in_(ids),
        )
    )
    if use_fts:
        cvec = clause_vector()

        def _clause_rows(query):
            return db.execute(
                clause_base.where(
                    or_(cvec.op("@@")(query), ClauseExtraction.clause_type.ilike(q_like, escape="\\"))
                )
                .order_by(func.ts_rank(cvec, query).desc())
                .limit(limit)
            ).all()

        clause_rows = _clause_rows(tsq) or _clause_rows(tsq_or)
    else:
        clause_rows = db.execute(
            clause_base.where(
                or_(
                    ClauseExtraction.text.ilike(q_like, escape="\\"),
                    ClauseExtraction.heading.ilike(q_like, escape="\\"),
                    ClauseExtraction.clause_type.ilike(q_like, escape="\\"),
                )
            ).limit(limit)
        ).all()
    clauses = [
        {
            "clause_id": clause.id,
            "contract_id": clause.contract_id,
            "contract_title": title,
            "clause_type": clause.clause_type,
            "heading": clause.heading,
            "excerpt": clause.text[:600],
        }
        for clause, title in clause_rows
    ]

    def _text_base(query):
        return (
            select(
                ContractTextSnapshot,
                Contract.title,
                (
                    snapshot_headline(query)
                    if use_fts
                    else func.substr(ContractTextSnapshot.text, 1, 0)
                ).label("headline"),
            )
            .join(Contract, Contract.id == ContractTextSnapshot.contract_id)
            .join(ContractVersion, ContractVersion.id == Contract.current_authoritative_version_id)
            .where(
                ContractTextSnapshot.org_id == org_id,
                ContractTextSnapshot.deleted_at.is_(None),
                ContractTextSnapshot.contract_id.in_(ids),
                # The contract's current text only: the authoritative version's own
                # snapshot, never a rejected proposal or a superseded version.
                ContractTextSnapshot.id == ContractVersion.text_snapshot_id,
            )
        )

    def _text_rows(query):
        text_query = _text_base(query)
        if use_fts:
            svec = snapshot_vector()
            text_query = text_query.where(svec.op("@@")(query)).order_by(
                func.ts_rank(svec, query).desc()
            )
        else:
            text_query = text_query.where(
                ContractTextSnapshot.text.ilike(q_like, escape="\\")
            )
        return db.execute(text_query.limit(limit)).all()

    # Same AND-then-OR fallback as the clause lens above.
    text_rows = _text_rows(tsq) or (_text_rows(tsq_or) if use_fts else [])
    text_hits = []
    for snapshot, title, headline_text in text_rows:
        matches = text_matches(snapshot.text, question)
        if not matches and headline_text:
            # ts_headline runs with empty StartSel/StopSel, so each fragment is a
            # verbatim substring of the snapshot: find it to recover real offsets.
            # Without this the text lens cites excerpts that anchor nowhere, and
            # a multi-word question almost never hits the exact-substring path.
            matches = []
            for frag in str(headline_text).split(" ... "):
                frag = frag.strip()
                if not frag:
                    continue
                start = snapshot.text.find(frag)
                matches.append(
                    {
                        "start_char": start if start >= 0 else None,
                        "end_char": start + len(frag) if start >= 0 else None,
                        "excerpt": frag,
                    }
                )
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
        lineage_facts(db, org_id=org_id, contract_ids=ids, accessible_ids=accessible)
        + temporal_facts(db, org_id=org_id, contract_ids=ids, accessible_ids=accessible)
        + clause_language_matches(db, org_id=org_id, contract_ids=ids, accessible_ids=accessible)
        + cohort_facts(db, org_id=org_id, contract_ids=ids, accessible_ids=accessible)
        + shared_entity_links(db, org_id=org_id, contract_ids=ids, accessible_ids=accessible)
        + _graph_facts(db, contract_ids=ids, clause_types=[], accessible_ids=accessible)
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

MAX_GRAPH_FACTS = 40
# ponytail: capped pairwise scan; precompute similarity signatures at ingestion if books outgrow it.
_REUSE_CLAUSE_CAP = 2000


def resolve_scope_contract_ids(
    db: Session,
    *,
    user: User,
    scope: str,
    contract_id: str | None,
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
) -> dict | None:
    """Answer a plain count/list-of-contracts question from the database. Returns
    None for anything else, so content questions still go through retrieval."""
    q = question.strip()
    is_count = bool(_COUNT_RE.match(q))
    is_list = bool(_LIST_RE.match(q)) and not is_count
    if not (is_count or is_list):
        return None

    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope=scope, contract_id=contract_id
    )
    n = len(contract_ids)
    label = {
        "portfolio": "portfolio",
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


def lineage_facts(
    db: Session, *, org_id: str, contract_ids: list[str], accessible_ids: set[str]
) -> list[dict]:
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
    nodes = {
        n.id: n
        for n in db.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids)))
    }
    labels = {node_id: n.label for node_id, n in nodes.items()}

    facts = []
    for e in edges:
        if _reaches_inaccessible(nodes, (e.from_node_id, e.to_node_id), accessible_ids):
            continue
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
    db: Session, *, org_id: str, contract_ids: list[str], accessible_ids: set[str],
    horizon_days: int = 90,
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
    today = utcnow().date()
    soon = today + timedelta(days=horizon_days)
    facts: list[dict] = []

    # Deadlines only matter for contracts in force: no expiry or overdue warnings
    # for drafts or contracts that are already closed.
    contracts = {
        c.id: c
        for c in db.scalars(
            select(Contract).where(
                Contract.id.in_(contract_ids),
                Contract.lifecycle_stage == ContractLifecycleStage.ACTIVE,
            )
        )
    }
    if not contracts:
        return []

    for c in contracts.values():
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
            Obligation.contract_id.in_(list(contracts)),
            Obligation.status.notin_((ObligationStatus.COMPLETED, ObligationStatus.CANCELLED)),
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
                KnowledgeNode.contract_id.in_(list(contracts)),
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
            if child_cid not in accessible_ids:
                continue  # never name, or count, a dependent the user can't open
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
    db: Session, *, org_id: str, contract_ids: list[str], accessible_ids: set[str],
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
    if not contract_ids or threshold > 100:
        return []
    try:
        from rapidfuzz import fuzz, process
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
        .limit(_REUSE_CLAUSE_CAP)
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
            ClauseExtraction.contract_id.in_(accessible_ids),
            ClauseExtraction.is_stale.is_(False),
            ClauseExtraction.text.isnot(None),
        )
        .order_by(ClauseExtraction.created_at.desc())
        .limit(_REUSE_CLAUSE_CAP)
    ).all()
    if not candidates:
        return []
    by_type: dict[str, tuple[list[str], list[str]]] = {}
    for ct, txt, title in candidates:
        texts, titles = by_type.setdefault(ct, ([], []))
        texts.append(txt)
        titles.append(title)

    facts: list[dict] = []
    seen: set = set()
    for ct, mytext, heading in mine:
        texts, titles = by_type.get(ct, ([], []))
        # extractOne compares in C and skips anything below the threshold.
        best = process.extractOne(mytext, texts, scorer=fuzz.token_sort_ratio, score_cutoff=threshold) if texts else None
        if best is None:
            continue
        best_score, best_title = int(best[1]), titles[best[2]]
        if (ct, best_title) not in seen:
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
    db: Session, *, org_id: str, contract_ids: list[str], accessible_ids: set[str], limit: int = 8
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
            KnowledgeEdge.contract_id.in_(accessible_ids),
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
        high_labels = list(dict.fromkeys(labels[n][1] for n in high if n in labels))
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
    db: Session, *, org_id: str, contract_ids: list[str], accessible_ids: set[str], limit: int = 12
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
            KnowledgeEdge.contract_id.in_(accessible_ids),
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
        # One contract can reach the entity through several edges: count contracts, not edges.
        other_ids = list(dict.fromkeys(cid for cid, _ in others))
        sample = list(dict.fromkeys(titles.get(cid) or cid for cid in other_ids))[:5]
        severities = sorted({(p.get("severity") or "").lower() for _, p in others} - {""})
        facts.append(
            {
                "entity": node.label,
                "entity_type": node.node_type,
                "shared_with_count": len(other_ids),
                "fact": (
                    f"{node.label} — also affects {len(other_ids)} other contract(s): "
                    + ", ".join(sample)
                    + (f" [severity: {', '.join(severities)}]" if severities else "")
                ),
                "contract_ids": other_ids,
            }
        )
    facts.sort(key=lambda f: f["shared_with_count"], reverse=True)
    return facts[:limit]


def _graph_facts(
    db: Session, *, contract_ids: list[str], clause_types: list[str], accessible_ids: set[str]
) -> list[dict]:
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
        if _reaches_inaccessible(nodes_by_id, (edge.from_node_id, edge.to_node_id), accessible_ids):
            continue
        if clause_types and dst.node_type == "clause" and dst.properties.get("clause_type") not in clause_types:
            continue
        facts.append(
            {
                "contract_id": edge.contract_id,
                "fact": f"{src.label} --{edge.edge_type}--> {dst.label}"
                + (f" (status: {edge.properties['status']})" if (edge.properties or {}).get("status") else ""),
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
) -> dict:
    """The assistant's Contract Brain context: the same hybrid retrieval and the
    same text block the Brain page grounds its answers in, so the two surfaces
    can't disagree. A question nothing matches gets an empty context (and a
    "not found" answer), never a filler slice of unrelated clauses."""
    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope=scope, contract_id=contract_id
    )
    sources = hybrid_sources(
        db, org_id=user.org_id, contract_ids=contract_ids, question=question, user=user
    )
    return {
        "contract_ids": contract_ids,
        "graph_facts": sources["graph"],
        "context_text": sources_to_context(sources),
        "source_count": sum(len(sources[lens]) for lens in ("semantic", "clauses", "text", "graph")),
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
