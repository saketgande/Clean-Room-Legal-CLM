from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.notices import service
from app.notices.schemas import (
    NoticeCreate,
    NoticeDetailResponse,
    NoticeDraftResponse,
    NoticeEscalate,
    NoticeExtractionResponse,
    NoticeNoteCreate,
    NoticeResponse,
    NoticeStatusUpdate,
    NoticeSummary,
    NoticeUpdate,
)

router = APIRouter(prefix="/notices", tags=["notices"])

_READ = require_permission("notice:read")
_CREATE = require_permission("notice:create")
_UPDATE = require_permission("notice:update")
_DELETE = require_permission("admin_panel:access")  # deleting destroys the trail


@router.get("", response_model=list[NoticeResponse])
def list_notices(
    status_filter: str | None = None,
    direction: str | None = None,
    notice_type: str | None = None,
    owner_user_id: str | None = None,
    contract_id: str | None = None,
    overdue_only: bool = False,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
):
    return service.list_notices(
        db,
        org_id=current_user.org_id,
        status_filter=status_filter,
        direction=direction,
        notice_type=notice_type,
        owner_user_id=owner_user_id,
        contract_id=contract_id,
        overdue_only=overdue_only,
        q=q,
    )


@router.get("/summary", response_model=NoticeSummary)
def notice_summary(db: Session = Depends(get_db), current_user=Depends(_READ)):
    return service.summary(db, org_id=current_user.org_id)


@router.post("/run-reminders")
def run_reminders(db: Session = Depends(get_db), current_user=Depends(_UPDATE)):
    """Chase this org's notices that are near or past their deadline, now.
    Scoped to the caller's org; the nightly Celery sweep does every org."""
    return service.run_reminders(db, org_id=current_user.org_id)


@router.post("", response_model=NoticeDetailResponse, status_code=status.HTTP_201_CREATED)
def create_notice(
    payload: NoticeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    return service.create_notice(db, actor=current_user, payload=payload)


@router.post("/extract", response_model=NoticeExtractionResponse)
async def extract_from_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(_CREATE),
):
    """Read an uploaded notice and propose register fields. Creates nothing —
    the filer reviews the suggestions in the New Notice form and saves there."""
    content = await file.read()
    return service.extract_from_upload(
        db, actor=current_user,
        filename=file.filename or "notice",
        mime_type=file.content_type or "application/octet-stream",
        content=content,
    )


# Static segments are declared before the dynamic one so '/summary' and
# '/extract' can never be swallowed as a notice id.
@router.get("/{notice_id}", response_model=NoticeDetailResponse)
def get_notice(
    notice_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_READ),
):
    return service.get_notice(db, org_id=current_user.org_id, notice_id=notice_id)


@router.patch("/{notice_id}", response_model=NoticeDetailResponse)
def update_notice(
    notice_id: str,
    payload: NoticeUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.update_notice(db, actor=current_user, notice_id=notice_id, payload=payload)


@router.post("/{notice_id}/status", response_model=NoticeDetailResponse)
def set_status(
    notice_id: str,
    payload: NoticeStatusUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.set_status(db, actor=current_user, notice_id=notice_id, payload=payload)


@router.post("/{notice_id}/draft-response", response_model=NoticeDraftResponse)
def draft_response(
    notice_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    """Draft a reply from the notice and its attachments. Stored on the notice
    for a lawyer to edit — nothing is sent."""
    return service.draft_response(db, actor=current_user, notice_id=notice_id)


@router.post("/{notice_id}/escalate", response_model=NoticeDetailResponse)
def escalate(
    notice_id: str,
    payload: NoticeEscalate,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    """Open a linked intake ticket for this notice, which carries routing, SLA
    and the approval ladder."""
    return service.escalate(db, actor=current_user, notice_id=notice_id, payload=payload)


@router.post("/{notice_id}/notes", response_model=NoticeDetailResponse)
def add_note(
    notice_id: str,
    payload: NoticeNoteCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.add_note(db, actor=current_user, notice_id=notice_id, payload=payload)


@router.post("/{notice_id}/documents", response_model=NoticeDetailResponse, status_code=status.HTTP_201_CREATED)
async def add_document(
    notice_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    content = await file.read()
    return service.add_document(
        db, actor=current_user, notice_id=notice_id,
        filename=file.filename or "attachment",
        mime_type=file.content_type or "application/octet-stream",
        content=content,
    )


@router.delete("/{notice_id}/documents/{document_id}", response_model=NoticeDetailResponse)
def delete_document(
    notice_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_UPDATE),
):
    return service.delete_document(
        db, actor=current_user, notice_id=notice_id, document_id=document_id
    )


@router.delete("/{notice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notice(
    notice_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_DELETE),
):
    service.delete_notice(db, actor=current_user, notice_id=notice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
