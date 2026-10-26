from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.deps import require_permission, require_screen_level
from app.trademarks.clearance_service import TrademarkClearanceService
from app.trademarks.dependencies import get_trademark_clearance_service
from app.trademarks.schemas import (
    ActivityEntry,
    DecisionRequest,
    FilingRequest,
    SearchRunUpdate,
    TrademarkResponse,
    TrademarkSearchRunDetail,
    TrademarkSearchRunSummary,
)

router = APIRouter(prefix="/trademarks", tags=["trademarks"])

_TRADEMARKS_VIEW = require_screen_level("trademarks", "VIEW")
_TRADEMARKS_EDIT = require_screen_level("trademarks", "EDIT")

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{trademark_id}/search-runs", response_model=list[TrademarkSearchRunSummary])
def list_search_runs(
    trademark_id: str,
    current_user=Depends(require_permission("trademark:read")),
    _screen=Depends(_TRADEMARKS_VIEW),
    service: TrademarkClearanceService = Depends(get_trademark_clearance_service),
):
    return service.list_runs(org_id=current_user.org_id, trademark_id=trademark_id)


@router.get("/search-runs/{run_id}", response_model=TrademarkSearchRunDetail)
def get_search_run(
    run_id: str,
    current_user=Depends(require_permission("trademark:read")),
    _screen=Depends(_TRADEMARKS_VIEW),
    service: TrademarkClearanceService = Depends(get_trademark_clearance_service),
):
    return service.get_run(org_id=current_user.org_id, run_id=run_id)


@router.patch("/search-runs/{run_id}", response_model=TrademarkSearchRunDetail)
def update_search_run(
    run_id: str,
    payload: SearchRunUpdate,
    current_user=Depends(require_permission("trademark:update")),
    _screen=Depends(_TRADEMARKS_EDIT),
    service: TrademarkClearanceService = Depends(get_trademark_clearance_service),
):
    return service.update_run(user=current_user, run_id=run_id, payload=payload)


@router.get("/search-runs/{run_id}/export")
def export_search_run(
    run_id: str,
    current_user=Depends(require_permission("trademark:read")),
    _screen=Depends(_TRADEMARKS_VIEW),
    service: TrademarkClearanceService = Depends(get_trademark_clearance_service),
):
    filename, content = service.export_run_xlsx(user=current_user, run_id=run_id)
    return Response(
        content=content,
        media_type=_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{trademark_id}/decision", response_model=TrademarkResponse)
def record_decision(
    trademark_id: str,
    payload: DecisionRequest,
    current_user=Depends(require_permission("trademark:update")),
    _screen=Depends(_TRADEMARKS_EDIT),
    service: TrademarkClearanceService = Depends(get_trademark_clearance_service),
):
    return service.record_decision(user=current_user, trademark_id=trademark_id, payload=payload)


@router.post("/{trademark_id}/filing", response_model=TrademarkResponse)
def record_filing(
    trademark_id: str,
    payload: FilingRequest,
    current_user=Depends(require_permission("trademark:update")),
    _screen=Depends(_TRADEMARKS_EDIT),
    service: TrademarkClearanceService = Depends(get_trademark_clearance_service),
):
    return service.record_filing(user=current_user, trademark_id=trademark_id, payload=payload)


@router.get("/{trademark_id}/activity", response_model=list[ActivityEntry])
def trademark_activity(
    trademark_id: str,
    current_user=Depends(require_permission("trademark:read")),
    _screen=Depends(_TRADEMARKS_VIEW),
    service: TrademarkClearanceService = Depends(get_trademark_clearance_service),
):
    return service.activity(org_id=current_user.org_id, trademark_id=trademark_id)
