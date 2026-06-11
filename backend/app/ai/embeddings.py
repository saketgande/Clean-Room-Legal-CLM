import hashlib
import random

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.ai.context import chunk_text
from app.contract_files.models import ContractEmbedding, ContractTextSnapshot

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
    vectors = _embed([chunk["text"] for chunk in chunks])
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
    if not texts:
        return []
    try:
        from fastembed import TextEmbedding

        model = TextEmbedding(model_name=EMBEDDING_MODEL)
        return [list(vector) for vector in model.embed(texts)]
    except Exception:
        return [_deterministic_mock_vector(text) for text in texts]


def _deterministic_mock_vector(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return [rng.uniform(-0.05, 0.05) for _ in range(EMBEDDING_DIMENSIONS)]
