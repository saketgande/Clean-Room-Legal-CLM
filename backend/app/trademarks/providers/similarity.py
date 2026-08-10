"""Trademark similarity search: fans out to 4 sources in parallel (internal
portfolio via pgvector, Signa, TMSearch.ai, Serper web search), risk-bands
each result, and degrades a single slow/broken source to an error/timeout/
not_configured status rather than failing the whole request.

Synchronous throughout (CLM's module family doesn't use async SQLAlchemy) -
the 3 external HTTP calls run in a small thread pool so they still overlap.
"""

import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.embeddings import embed_texts
from app.core.config import Settings
from app.trademarks.models import Trademark
from app.trademarks.providers import signa as signa_provider
from app.trademarks.providers import tmsearch as tmsearch_provider
from app.trademarks.providers import web_search as web_search_provider
from app.trademarks.schemas import (
    InternalPortfolioResult,
    SearchSimilarRequest,
    SearchSimilarResponse,
    SearchSummary,
    SignaResult,
    SourceResult,
    TmSearchResult,
    WebSearchResult,
)
from app.core.enums import SourceStatus, TrademarkRiskLevel

# Order is deliberate - Signa, TM Search, Serper web, then internal Postgres -
# the frontend's source cards and the PDF export mirror this insertion order.
_SOURCE_ORDER = ("signa", "tmsearch", "web_search", "internal_portfolio")


def _risk_level(score: float, settings: Settings) -> TrademarkRiskLevel:
    if score >= settings.trademark_risk_threshold_high:
        return TrademarkRiskLevel.HIGH
    if score >= settings.trademark_risk_threshold_medium:
        return TrademarkRiskLevel.MEDIUM
    return TrademarkRiskLevel.LOW


def search_similar(db: Session, *, org_id: str, request: SearchSimilarRequest, settings: Settings) -> SearchSimilarResponse:
    with ThreadPoolExecutor(max_workers=3) as pool:
        signa_future = pool.submit(_search_signa, request, settings)
        tmsearch_future = pool.submit(_search_tmsearch, request, settings)
        web_future = pool.submit(_search_web, request, settings)

        signa_result = _safe_result(signa_future, settings.signa_timeout_seconds)
        tmsearch_result = _safe_result(tmsearch_future, settings.tmsearch_timeout_seconds)
        web_result = _safe_result(web_future, settings.search_provider_timeout_seconds)

    internal_result = _safe_call(
        lambda: _search_internal_portfolio(db, org_id=org_id, request=request, settings=settings)
    )

    sources = {
        "signa": signa_result,
        "tmsearch": tmsearch_result,
        "web_search": web_result,
        "internal_portfolio": internal_result,
    }

    return SearchSimilarResponse(
        query_id=f"srch_{uuid.uuid4().hex[:8]}",
        sources=sources,
        summary=_build_summary(sources),
    )


def _safe_result(future: Future, timeout: float) -> SourceResult:
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        return SourceResult(status=SourceStatus.TIMEOUT, results=[])
    except Exception as exc:  # noqa: BLE001 - isolates one source's failure from the rest
        return SourceResult(status=SourceStatus.ERROR, error_message=str(exc), results=[])


def _safe_call(fn) -> SourceResult:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return SourceResult(status=SourceStatus.ERROR, error_message=str(exc), results=[])


def _search_internal_portfolio(
    db: Session, *, org_id: str, request: SearchSimilarRequest, settings: Settings
) -> SourceResult:
    text = f"{request.trademark_name} {request.description}".strip()
    embedding = embed_texts([text])[0]
    distance = Trademark.embedding.cosine_distance(embedding)
    rows = db.execute(
        select(Trademark, distance.label("distance"))
        .where(Trademark.org_id == org_id, Trademark.deleted_at.is_(None), Trademark.embedding.is_not(None))
        .order_by(distance)
        .limit(settings.trademark_internal_search_top_k)
    ).all()

    results = []
    for trademark, distance_value in rows:
        score = round(max(0.0, 1 - float(distance_value)), 4)
        results.append(
            InternalPortfolioResult(
                trademark_id=trademark.id,
                name=trademark.name,
                similarity_score=score,
                status=trademark.status,
                jurisdiction=trademark.jurisdiction,
                nice_class=trademark.nice_class,
                filed_on=trademark.filed_on,
                risk_level=_risk_level(score, settings),
            )
        )
    return SourceResult(status=SourceStatus.COMPLETE, results=[r.model_dump() for r in results])


def _search_web(request: SearchSimilarRequest, settings: Settings) -> SourceResult:
    if settings.mock_serper or not settings.search_provider_api_key:
        return SourceResult(status=SourceStatus.NOT_CONFIGURED, provider=settings.search_provider, results=[])

    query = f'"{request.trademark_name}" trademark {" ".join(request.jurisdictions)}'
    raw_results = web_search_provider.search(
        query,
        api_key=settings.search_provider_api_key,
        timeout_seconds=settings.search_provider_timeout_seconds,
        num=5,
    )
    results = [
        WebSearchResult(title=r.title, url=r.url, snippet=r.snippet).model_dump() for r in raw_results
    ]
    return SourceResult(status=SourceStatus.COMPLETE, provider=settings.search_provider, results=results)


def _search_signa(request: SearchSimilarRequest, settings: Settings) -> SourceResult:
    if settings.mock_signa or not settings.signa_api_key:
        return SourceResult(status=SourceStatus.NOT_CONFIGURED, results=[])

    raw_matches = signa_provider.search(
        request.trademark_name,
        api_key=settings.signa_api_key,
        base_url=settings.signa_base_url,
        offices=settings.signa_offices,
        timeout_seconds=settings.signa_timeout_seconds,
    )
    results = []
    for m in raw_matches:
        score = round(m.relevance_score / 100, 4)  # normalize Signa's 0-100 to 0-1
        results.append(
            SignaResult(
                signa_id=m.id,
                mark_text=m.mark_text,
                similarity_score=score,
                status_primary=m.status_primary,
                office_code=m.office_code,
                filing_date=m.filing_date,
                owner_name=m.owner_name,
                nice_classes=m.nice_classes,
                risk_level=_risk_level(score, settings),
            ).model_dump()
        )
    return SourceResult(status=SourceStatus.COMPLETE, provider="signa", results=results)


def _search_tmsearch(request: SearchSimilarRequest, settings: Settings) -> SourceResult:
    if settings.mock_tmsearch or not settings.tmsearch_api_key:
        return SourceResult(status=SourceStatus.NOT_CONFIGURED, results=[])

    raw_matches = tmsearch_provider.search(
        request.trademark_name,
        api_key=settings.tmsearch_api_key,
        base_url=settings.tmsearch_base_url,
        timeout_seconds=settings.tmsearch_timeout_seconds,
    )
    results = []
    for m in raw_matches:
        score = round(m.accuracy / 99, 4)  # normalize TMSearch's 0-99 "accuracy" to 0-1
        results.append(
            TmSearchResult(
                tmsearch_id=m.mid,
                mark_text=m.verbal,
                similarity_score=score,
                status=m.status,
                office_code=m.submition,
                application_number=m.app,
                registration_number=m.reg,
                filed_date=m.applied_date,
                protection_countries=m.protection,
                image_url=m.image_url,
                risk_level=_risk_level(score, settings),
            ).model_dump()
        )
    return SourceResult(status=SourceStatus.COMPLETE, provider="tmsearch", results=results)


def _build_summary(sources: dict[str, SourceResult]) -> SearchSummary:
    high = medium = low = 0
    for source in sources.values():
        for item in source.results:
            risk = item.get("risk_level")
            if risk == TrademarkRiskLevel.HIGH:
                high += 1
            elif risk == TrademarkRiskLevel.MEDIUM:
                medium += 1
            elif risk == TrademarkRiskLevel.LOW:
                low += 1

    if high > 0:
        recommendation = "proceed_with_caution"
    elif medium > 0:
        recommendation = "review_recommended"
    else:
        recommendation = "clear_to_proceed"

    return SearchSummary(
        high_risk_count=high,
        medium_risk_count=medium,
        low_risk_count=low,
        recommendation=recommendation,
    )
