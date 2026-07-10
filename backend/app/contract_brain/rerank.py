"""Pluggable cross-encoder reranker for retrieval.

Hybrid dense+FTS retrieval casts a wide net; a cross-encoder reranker then
re-scores each candidate against the query with full cross-attention, which is
the standard "second stage" that lifts precision. Off by default (needs an API
key). Enable with rerank_provider = "cohere" | "voyage" + the matching key.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def rerank_enabled() -> bool:
    provider = settings.rerank_provider
    if provider == "cohere":
        return bool(settings.cohere_api_key)
    if provider == "voyage":
        return bool(settings.voyage_api_key)
    return False


def rerank_order(query: str, documents: list[str], top_n: int) -> list[int]:
    """Return document indices ordered best-first (length <= top_n). On any
    failure, degrade gracefully to the input order (retrieval still works)."""
    if not documents:
        return []
    top_n = min(top_n, len(documents))
    try:
        if settings.rerank_provider == "cohere":
            resp = httpx.post(
                "https://api.cohere.com/v2/rerank",
                headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
                json={
                    "model": settings.cohere_rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return [item["index"] for item in resp.json()["results"]]
        if settings.rerank_provider == "voyage":
            resp = httpx.post(
                "https://api.voyageai.com/v1/rerank",
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={
                    "model": settings.voyage_rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_k": top_n,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return [item["index"] for item in resp.json()["data"]]
    except Exception:
        logger.warning("rerank failed; keeping retrieval order", exc_info=True)
    return list(range(top_n))
