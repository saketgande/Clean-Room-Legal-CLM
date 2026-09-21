from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import settings
from app.core.deps import require_permission
from app.trademarks.dependencies import get_trademarks_service
from app.trademarks.schemas import (
    DashboardMetrics,
    ExtractRequest,
    ExtractResponse,
    IngestRequest,
    IngestResponse,
    IntakeSubmitRequest,
    IntegrationStatusResponse,
    IntegrationTestResponse,
    RenewalCalendarEntry,
    SearchSimilarRequest,
    SearchSimilarResponse,
    TrademarkCreate,
    TrademarkResponse,
    TrademarkUpdate,
    UploadDocumentResponse,
)
from app.trademarks.service import TrademarksService

router = APIRouter(prefix="/trademarks", tags=["trademarks"])


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
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.create_trademark(user=current_user, payload=payload)


@router.post("/intake", response_model=TrademarkResponse, status_code=status.HTTP_201_CREATED)
def submit_intake(
    payload: IntakeSubmitRequest,
    current_user=Depends(require_permission("trademark:create")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.create_trademark_from_intake(user=current_user, payload=payload)


@router.get("/dashboard", response_model=DashboardMetrics)
def dashboard(
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.dashboard_metrics(org_id=current_user.org_id)


@router.get("/calendar", response_model=list[RenewalCalendarEntry])
def calendar(
    within_days: int = 90,
    current_user=Depends(require_permission("trademark:read")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.list_upcoming_renewals(org_id=current_user.org_id, within_days=within_days)


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
    service: TrademarksService = Depends(get_trademarks_service),
):
    trademark = service.get_trademark_for_user(trademark_id=trademark_id, user=current_user)
    return service.update_trademark(trademark=trademark, user=current_user, payload=payload)


@router.post("/search-similar", response_model=SearchSimilarResponse)
def search_similar(
    payload: SearchSimilarRequest,
    current_user=Depends(require_permission("trademark:search")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.search_similar(user=current_user, request=payload)


@router.post("/documents/upload", response_model=UploadDocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("trademark:extract")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return await service.save_uploaded_document(user=current_user, file=file)


@router.post("/documents/extract", response_model=ExtractResponse)
async def extract_fields(
    payload: ExtractRequest,
    current_user=Depends(require_permission("trademark:extract")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    try:
        return await service.extract_fields(user=current_user, request=payload)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Uploaded document not found") from exc


@router.post("/documents/ingest", response_model=IngestResponse)
def ingest_document(
    payload: IngestRequest,
    current_user=Depends(require_permission("trademark:extract")),
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.ingest_extraction(user=current_user, request=payload)


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
    service: TrademarksService = Depends(get_trademarks_service),
):
    return service.test_integration(service_name=service_name, settings=settings)
