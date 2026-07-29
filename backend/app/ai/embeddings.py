import hashlib
import logging
import random

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.context import chunk_text
from app.contract_files.models import ContractEmbedding, ContractTextSnapshot
from app.contracts.models import Contract
from app.core.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384


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
            },
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(row)
        rows.append(row)
    return rows


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
        # M9: this fallback returns random (seeded) vectors with no semantic
        # meaning — every retrieval/RAG result becomes noise. It was silent;
        # log loudly so an operator can see semantic search is degraded.
        logger.warning(
            "local embeddings (fastembed) unavailable — falling back to "
            "DETERMINISTIC MOCK vectors; semantic search / RAG results will be "
            "meaningless until this is fixed",
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
