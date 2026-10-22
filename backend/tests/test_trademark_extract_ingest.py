"""Document extraction ingests into document_extract only, and the similarity
search reads document_extract. DB-backed (Postgres + pgvector); embeddings are
stubbed so no model is needed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.trademarks import service as service_module
from app.trademarks.models import DocumentExtract, Trademark
from app.trademarks.providers import similarity
from app.trademarks.schemas import ExtractedRecord, IngestRequest, SearchSimilarRequest
from app.trademarks.service import TrademarksService


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """ZOLPHERA text points one way, everything else the other way."""
    vectors = []
    for text in texts:
        match = 1.0 if "zolphera" in text.lower() else 0.0
        vector = [0.0] * 384
        vector[0], vector[1] = match, 1.0 - match
        vectors.append(vector)
    return vectors


@pytest.fixture
def org_ids(db_session, monkeypatch):
    monkeypatch.setattr(service_module, "embed_texts", _fake_embed)
    monkeypatch.setattr(similarity, "embed_texts", _fake_embed)
    orgs = [str(uuid.uuid4()), str(uuid.uuid4())]
    yield orgs
    db_session.rollback()
    db_session.execute(delete(DocumentExtract).where(DocumentExtract.org_id.in_(orgs)))
    db_session.commit()


def _ingest(db, org_id: str, records: list[dict]) -> object:
    user = SimpleNamespace(id=str(uuid.uuid4()), org_id=org_id)
    request = IngestRequest(
        doc_id="doc-key",
        source_filename="journal.pdf",
        page_start=1,
        page_end=1,
        template="ip_india_journal",
        records=[ExtractedRecord(page_number=1, fields=fields) for fields in records],
    )
    return TrademarksService(db).ingest_extraction(user=user, request=request)


def _count(db, model, org_id: str) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.org_id == org_id))


def test_ingest_stores_extractions_only_and_skips_records_with_no_fields(db_session, org_ids):
    org_id = org_ids[0]
    result = _ingest(
        db_session,
        org_id,
        [{"product_name": "ZOLPHERA", "jurisdiction": "MUMBAI"}, {}, {"product_name": None}],
    )

    assert result.ingested_count == 1
    assert _count(db_session, DocumentExtract, org_id) == 1
    assert _count(db_session, Trademark, org_id) == 0


def test_search_finds_the_orgs_own_extractions_and_never_another_orgs(db_session, org_ids):
    org_a, org_b = org_ids
    _ingest(db_session, org_a, [{"product_name": "ZOLPHERA", "jurisdiction": "MUMBAI", "tm_id": "1234"}])
    request = SearchSimilarRequest(trademark_name="ZOLPHERA", description="")

    own = similarity._search_document_extracts(db_session, org_id=org_a, request=request, settings=settings)
    other = similarity._search_document_extracts(db_session, org_id=org_b, request=request, settings=settings)

    assert len(own.results) == 1
    hit = own.results[0]
    assert hit["name"] == "ZOLPHERA"
    assert hit["jurisdiction"] == "MUMBAI"
    assert hit["tm_number"] == "1234"
    assert hit["source_filename"] == "journal.pdf"
    assert hit["similarity_score"] > 0.99
    assert other.results == []
