import hashlib
import logging
import math
import random

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.context import chunk_text
from app.contract_files.models import (
    ContractDocumentElement,
    ContractEmbedding,
    ContractTextSnapshot,
)
from app.contracts.models import Contract
from app.core.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384


def backfill_embeddings(db: Session, *, limit: int | None = None) -> dict:
    """Re-embed already-indexed contracts that are now structured, so their
    vectors use clause-aligned chunks (better retrieval, clause/page metadata)
    instead of the old fixed windows. Idempotent: generate_embeddings_for_snapshot
    deletes and rebuilds a snapshot's embeddings. Run as a background job for a
    large portfolio — each snapshot loads the embedding model, so it's not free.
    """
    embedded = select(ContractEmbedding.text_snapshot_id).distinct()
    query = select(ContractTextSnapshot).where(
        ContractTextSnapshot.structure_status == "structured",
        ContractTextSnapshot.id.in_(embedded),
    )
    if limit is not None:
        query = query.limit(limit)
    snapshots = db.scalars(query).all()

    reembedded = 0
    for snapshot in snapshots:
        try:
            generate_embeddings_for_snapshot(db, snapshot=snapshot, created_by_user_id=None)
            db.commit()
            reembedded += 1
        except Exception:
            db.rollback()
            logger.warning("re-embed failed for snapshot %s", snapshot.id, exc_info=True)
    return {"eligible": len(snapshots), "reembedded": reembedded}


def element_chunks(db: Session, snapshot: ContractTextSnapshot, *, target_chars: int = 2400) -> list[dict]:
    """Chunk a structured snapshot by clause instead of by fixed-size window.
    Each chunk is a heading and the clauses under it (packed to ~target_chars),
    so a retrieved passage is a complete clause, not a mid-sentence slice —
    and page_artifact junk (headers/footers/page numbers) is dropped. Carries
    clause labels + pages as metadata for grounded citations."""
    els = db.scalars(
        select(ContractDocumentElement)
        .where(ContractDocumentElement.text_snapshot_id == snapshot.id)
        .order_by(ContractDocumentElement.seq)
    ).all()

    chunks: list[dict] = []
    group: list[ContractDocumentElement] = []

    def flush() -> None:
        if not group:
            return
        chunks.append({
            "chunk_index": len(chunks),
            "start_char": group[0].char_start if group[0].char_start is not None else 0,
            "end_char": group[-1].char_end if group[-1].char_end is not None else 0,
            "text": "\n\n".join(e.text for e in group),
            "meta": {
                "chunk_source": "element",
                "pages": sorted({e.page_number for e in group if e.page_number is not None}),
                "clause_labels": [e.number_label for e in group if e.number_label],
                "block_ids": [e.block_id for e in group],
            },
        })
        group.clear()

    size = 0
    for e in els:
        if e.element_type == "page_artifact":
            continue
        if e.element_type in ("heading", "title") and group:
            flush()  # a heading starts a fresh section
            size = 0
        group.append(e)
        size += len(e.text or "")
        if size >= target_chars:
            flush()
            size = 0
    flush()
    return chunks


def generate_embeddings_for_snapshot(
    db: Session,
    *,
    snapshot: ContractTextSnapshot,
    created_by_user_id: str | None,
) -> list[ContractEmbedding]:
    db.execute(
        delete(ContractEmbedding).where(
            ContractEmbedding.contract_id == snapshot.contract_id,
            ContractEmbedding.contract_version_id == snapshot.contract_version_id,
            ContractEmbedding.text_snapshot_id == snapshot.id,
        )
    )
    # Clause-aligned chunks when the document is structured (better retrieval,
    # clause/page provenance, no header/footer noise); fixed windows otherwise.
    chunks = element_chunks(db, snapshot) if snapshot.structure_status == "structured" else []
    if not chunks:
        chunks = chunk_text(snapshot.text, chunk_chars=3600, overlap_chars=500)
    embed_inputs = [chunk["text"] for chunk in chunks]
    if settings.contextual_chunking:
        # Prepend a light context marker so a chunk embeds with its parent
        # contract's identity — helps passages disambiguate across contracts at
        # retrieval time. Only the EMBEDDED text is prefixed; chunk_text (shown
        # to the user) stays clean.
        title = db.scalar(
            select(Contract.title).where(Contract.id == snapshot.contract_id)
        )
        if title:
            embed_inputs = [f"[Contract: {title}]\n{t}" for t in embed_inputs]
    vectors = _embed(embed_inputs)
    rows: list[ContractEmbedding] = []
    for chunk, vector in zip(chunks, vectors, strict=False):
        row = ContractEmbedding(
            org_id=snapshot.org_id,
            contract_id=snapshot.contract_id,
            contract_version_id=snapshot.contract_version_id,
            text_snapshot_id=snapshot.id,
            chunk_index=chunk["chunk_index"],
            chunk_text=chunk["text"],
            embedding=vector,
            metadata_json={
                "embedding_model": EMBEDDING_MODEL,
                "embedding_dimensions": EMBEDDING_DIMENSIONS,
                "start_char": chunk["start_char"],
                "end_char": chunk["end_char"],
                **chunk.get("meta", {}),
            },
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(row)
        rows.append(row)
    return rows


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Public entry point for other modules (e.g. app.trademarks) that need
    embeddings but aren't part of the contract-embedding pipeline above."""
    return _embed(texts)


def similarities(query: str, texts: list[str]) -> list[float]:
    """Cosine similarity of ``query`` to each of ``texts`` in one embedding pass.
    Used for clause-scoped redline retrieval: a contract has only tens of
    clauses, so embedding them on the fly is cheap and needs no stored vectors.
    Returns [] if embeddings are unavailable, so the caller falls back to lexical.
    """
    if not texts:
        return []
    try:
        vecs = _embed([query, *texts])
    except Exception:
        logger.warning("similarity embedding failed; caller should fall back", exc_info=True)
        return []
    if len(vecs) != len(texts) + 1:
        return []
    q = vecs[0]

    def _cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    return [_cos(q, v) for v in vecs[1:]]


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed texts with the configured provider. Default is the local
    bge-small model (384-dim, no key). Set embedding_provider="voyage" +
    voyage_api_key to use voyage-law-2 (1024-dim legal-domain embeddings);
    that also requires migrating the pgvector column to 1024 and re-embedding,
    since column dimension is fixed."""
    if not texts:
        return []
    if settings.embedding_provider == "voyage" and settings.voyage_api_key:
        try:
            return _embed_voyage(texts)
        except Exception:
            logger.warning("voyage embeddings failed; falling back to local", exc_info=True)
    return _embed_local(texts)


def _embed_local(texts: list[str]) -> list[list[float]]:
    try:
        from fastembed import TextEmbedding

        model = TextEmbedding(model_name=EMBEDDING_MODEL)
        return [list(vector) for vector in model.embed(texts)]
    except Exception:
        # Random (seeded) vectors have no semantic meaning — every retrieval/RAG
        # result built on them is noise. Never write that silently: unless an
        # operator has explicitly opted in (CI / bare shells), fail loudly so the
        # embedding job errors instead of poisoning the index with garbage that
        # *looks* embedded.
        if not settings.allow_mock_embeddings:
            logger.error(
                "local embeddings (fastembed) unavailable and allow_mock_embeddings "
                "is off — refusing to write meaningless vectors",
                exc_info=True,
            )
            raise
        logger.warning(
            "local embeddings (fastembed) unavailable — using DETERMINISTIC MOCK "
            "vectors (allow_mock_embeddings=on); semantic search will be meaningless",
            exc_info=True,
        )
        return [_deterministic_mock_vector(text) for text in texts]


def _embed_voyage(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
        json={
            "input": texts,
            "model": settings.voyage_embedding_model,
            "input_type": "document",
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]


def _deterministic_mock_vector(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return [rng.uniform(-0.05, 0.05) for _ in range(EMBEDDING_DIMENSIONS)]
