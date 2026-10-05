from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import settings
from app.core.deps import require_permission, require_screen_level
from app.trademarks.dependencies import get_trademarks_service
from app.trademarks.schemas import (
    ClassSuggestionRequest,
    ClassSuggestionResponse,
    DashboardMetrics,
    ExplainConflictRequest,
    ExplainConflictResponse,
    ExtractRequest,
    ExtractResponse,
    IngestRequest,
    IngestResponse,
    IntakeSubmitRequest,
    IntegrationStatusResponse,
    IntegrationTestResponse,
    PortfolioDigestResponse,
    PortfolioRenewalsResponse,
    PortfolioStatsResponse,
    SearchSimilarRequest,
    SearchSimilarResponse,
    TrademarkCreate,
    TrademarkFromIntakeRequest,
    TrademarkResponse,
    TrademarkSummary,
    TrademarkUpdate,
    UploadDocumentResponse,
)
from app.trademarks.service import TrademarksService

router = APIRouter(prefix="/trademarks", tags=["trademarks"])

_TRADEMARKS_VIEW = require_screen_level("trademarks", "VIEW")
_TRADEMARKS_ADD = require_screen_level("trademarks", "ADD")
_TRADEMARKS_EDIT = require_screen_level("trademarks", "EDIT")


@router.get("", response_model=list[TrademarkResponse])
def list_trademarks(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.list_trademarks(org_id=current_user.org_id)


@router.post("", response_model=TrademarkResponse, status_code=status.HTTP_201_CREATED)
def create_trademark(
    payload: TrademarkCreate,
    current_user=Depends(require_permission("trademark:create")),
    service: TrademarksService = Depends(_TRADEMARKS_ADD),
):
    return service.create_trademark(user=current_user, payload=payload)


@router.post("/intake", response_model=TrademarkResponse, status_code=status.HTTP_201_CREATED)
def submit_intake(
    payload: IntakeSubmitRequest,
    current_user=Depends(require_permission("trademark:create")),
    service: TrademarksService = Depends(_TRADEMARKS_ADD),
):
    """The standalone wizard path — kept alongside the Legal Intake bridge
    below, same reason `POST /contracts/upload` is kept alongside
    intake-driven contract drafting: sometimes there's no request to
    continue from at all."""
    return service.create_trademark_from_intake(user=current_user, payload=payload)


@router.post(
    "/from-intake/{intake_request_id}", response_model=TrademarkResponse, status_code=status.HTTP_201_CREATED
)
def continue_as_trademark(
    intake_request_id: str,
    payload: TrademarkFromIntakeRequest,
    current_user=Depends(require_permission("trademark:create")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """"Continue as Trademark" — creates a trademark from an existing Legal
    Intake request, the same shape as escalating a Notice from one."""
    return service.create_trademark_from_intake_request(
        intake_request_id=intake_request_id, user=current_user, overrides=payload
    )


@router.get("/dashboard", response_model=DashboardMetrics)
def dashboard(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.dashboard_metrics(org_id=current_user.org_id)


@router.get("/calendar", response_model=PortfolioRenewalsResponse)
def calendar(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """Every trademark with a computable renewal date, plus portfolio
    totals — the Renewal calendar and Dashboard timeline both window/filter
    this client-side (month view vs next-6-months), same as the source
    module's own single unfiltered GET /api/portfolio/renewals."""
    return service.list_portfolio_renewals(org_id=current_user.org_id)


@router.get("/{trademark_id}", response_model=TrademarkResponse)
def get_trademark(
    trademark_id: str,
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.get_trademark_for_user(trademark_id=trademark_id, user=current_user)


@router.patch("/{trademark_id}", response_model=TrademarkResponse)
def update_trademark(
    trademark_id: str,
    payload: TrademarkUpdate,
    current_user=Depends(require_permission("trademark:update")),
    service: TrademarksService = Depends(_TRADEMARKS_EDIT),
):
    trademark = service.get_trademark_for_user(trademark_id=trademark_id, user=current_user)
    return service.update_trademark(trademark=trademark, user=current_user, payload=payload)


@router.post("/search-similar", response_model=SearchSimilarResponse)
def search_similar(
    payload: SearchSimilarRequest,
    current_user=Depends(require_permission("trademark:search")),
    service: TrademarksService = Depends(_TRADEMARKS_VIEW),
):
    return service.search_similar(user=current_user, request=payload)


@router.post("/documents/upload", response_model=UploadDocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("trademark:extract")),
    service: TrademarksService = Depends(_TRADEMARKS_ADD),
):
    return await service.save_uploaded_document(user=current_user, file=file)


@router.post("/documents/extract", response_model=ExtractResponse)
async def extract_fields(
    payload: ExtractRequest,
    current_user=Depends(require_permission("trademark:extract")),
    service: TrademarksService = Depends(_TRADEMARKS_VIEW),
):
    try:
        return await service.extract_fields(user=current_user, request=payload)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Uploaded document not found") from exc


@router.post("/documents/ingest", response_model=IngestResponse)
def ingest_document(
    payload: IngestRequest,
    current_user=Depends(require_permission("trademark:extract")),
    service: TrademarksService = Depends(_TRADEMARKS_ADD),
):
    return service.ingest_extraction(user=current_user, request=payload)


@router.post("/classify-goods", response_model=ClassSuggestionResponse)
async def classify_goods(
    payload: ClassSuggestionRequest,
    current_user=Depends(require_permission("trademark:create")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """NICE-class suggestion, called as the user pauses typing on the
    create/intake form — debounced client-side, cached server-side."""
    try:
        return await service.suggest_nice_class(org_id=current_user.org_id, request=payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Classifier provider error: {exc}") from exc


@router.post("/explain-conflict", response_model=ExplainConflictResponse)
async def explain_conflict(
    payload: ExplainConflictRequest,
    current_user=Depends(require_permission("trademark:search")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """"Explain this conflict" — one on-demand call per result the user
    clicks into on the search-similar page, not the whole result set."""
    try:
        return await service.explain_conflict(org_id=current_user.org_id, request=payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Explainer provider error: {exc}") from exc


@router.get("/reports/digest", response_model=PortfolioDigestResponse)
async def get_portfolio_digest(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """Today's portfolio digest, generated on the first call of the day and
    reused for every reload after that — see PortfolioDigest's docstring."""
    try:
        return await service.get_or_create_portfolio_digest(user=current_user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Digest provider error: {exc}") from exc


@router.post("/reports/digest/regenerate", response_model=PortfolioDigestResponse)
async def regenerate_portfolio_digest(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """Force-regenerates today's digest even if one already exists — only
    reachable via an explicit "Regenerate" click; spends one extra LLM call."""
    try:
        return await service.get_or_create_portfolio_digest(user=current_user, force=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Digest provider error: {exc}") from exc


@router.get("/reports/stats", response_model=PortfolioStatsResponse)
def get_portfolio_stats(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """Pure SQL aggregates for the Reports page — no LLM spend, safe on
    every page load."""
    return service.portfolio_stats(org_id=current_user.org_id)


@router.get("/reports/at-risk", response_model=list[TrademarkSummary])
def get_at_risk(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    """Backs the Reports "Renewal & risk pipeline" panel — trademarks
    currently in a renewal_pending or lapsed status."""
    return service.list_at_risk(org_id=current_user.org_id)


@router.get("/integrations/status", response_model=IntegrationStatusResponse)
def integrations_status(
    current_user=Depends(require_permission("trademark:integrations_manage")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.get_integration_status(settings=settings)


@router.post("/integrations/test/{service_name}", response_model=IntegrationTestResponse)
def test_integration(
    service_name: str,
    current_user=Depends(require_permission("trademark:integrations_manage")),
    service: TrademarksService = Depends(_TRADEMARKS_VIEW),
):
    return service.test_integration(service_name=service_name, settings=settings)
