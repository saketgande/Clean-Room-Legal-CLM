from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db, require_permission
from app.trademarks import service
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

router = APIRouter(prefix="/trademarks", tags=["trademarks"])


@router.get("", response_model=list[TrademarkResponse])
def list_trademarks(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:read")),
):
    return service.list_trademarks(db, org_id=current_user.org_id)


@router.post("", response_model=TrademarkResponse, status_code=status.HTTP_201_CREATED)
def create_trademark(
    payload: TrademarkCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:create")),
):
    return service.create_trademark(db, user=current_user, payload=payload)


@router.post("/intake", response_model=TrademarkResponse, status_code=status.HTTP_201_CREATED)
def submit_intake(
    payload: IntakeSubmitRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:create")),
):
    return service.create_trademark_from_intake(db, user=current_user, payload=payload)


@router.get("/dashboard", response_model=DashboardMetrics)
def dashboard(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:read")),
):
    return service.dashboard_metrics(db, org_id=current_user.org_id)


@router.get("/calendar", response_model=list[RenewalCalendarEntry])
def calendar(
    within_days: int = 90,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:read")),
):
    return service.list_upcoming_renewals(db, org_id=current_user.org_id, within_days=within_days)


@router.get("/{trademark_id}", response_model=TrademarkResponse)
def get_trademark(
    trademark_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:read")),
):
    return service.get_trademark_for_user(db, trademark_id=trademark_id, user=current_user)


@router.patch("/{trademark_id}", response_model=TrademarkResponse)
def update_trademark(
    trademark_id: str,
    payload: TrademarkUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:update")),
):
    trademark = service.get_trademark_for_user(db, trademark_id=trademark_id, user=current_user)
    return service.update_trademark(db, trademark=trademark, user=current_user, payload=payload)


@router.post("/search-similar", response_model=SearchSimilarResponse)
def search_similar(
    payload: SearchSimilarRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:search")),
):
    return service.search_similar(db, user=current_user, request=payload)


@router.post("/documents/upload", response_model=UploadDocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:extract")),
):
    return await service.save_uploaded_document(db, user=current_user, file=file)


@router.post("/documents/extract", response_model=ExtractResponse)
async def extract_fields(
    payload: ExtractRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:extract")),
):
    try:
        return await service.extract_fields(db, user=current_user, request=payload)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Uploaded document not found") from exc


@router.post("/documents/ingest", response_model=IngestResponse)
def ingest_document(
    payload: IngestRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:extract")),
):
    return service.ingest_extraction(db, user=current_user, request=payload)


@router.get("/integrations/status", response_model=IntegrationStatusResponse)
def integrations_status(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:integrations_manage")),
):
    return service.get_integration_status(db, settings=settings)


@router.post("/integrations/test/{service_name}", response_model=IntegrationTestResponse)
def test_integration(
    service_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("trademark:integrations_manage")),
):
    return service.test_integration(db, service_name=service_name, settings=settings)
