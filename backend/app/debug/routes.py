from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db, get_current_user
from app.core.models import AICallLog, RequestLog, ResourceTimelineEvent
from app.jobs.models import JobRun

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@router.get("/readiness")
def readiness(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"status": "ready", "database": "ok"}


@router.get("/config-status")
def config_status():
    return {
        "claude": {"configured": bool(settings.claude_api_key), "mock": settings.mock_claude},
        "reducto": {"configured": bool(settings.reducto_api_key), "mock": settings.mock_reducto},
        "resend": {"configured": bool(settings.resend_api_key), "mock": settings.mock_resend},
        "docusign": {
            "configured": bool(settings.docusign_integration_key),
            "mock": settings.mock_docusign,
        },
        "storage_root": str(settings.storage_root),
        "debug": settings.debug,
    }


@router.get("/requests/{request_id}")
def request_trace(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.scalar(select(RequestLog).where(RequestLog.request_id == request_id))
    if row is None or (row.org_id and row.org_id != current_user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request trace not found")
    return row


@router.get("/jobs/{job_id}")
def job_trace(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(JobRun, job_id)
    if row is None or row.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return row


@router.get("/ai-calls/{ai_call_id}")
def ai_call_trace(
    ai_call_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(AICallLog, ai_call_id)
    if row is None or row.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI call not found")
    return row


@router.get("/resources/{resource_type}/{resource_id}/timeline")
def resource_timeline(
    resource_type: str,
    resource_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return db.scalars(
        select(ResourceTimelineEvent)
        .where(
            ResourceTimelineEvent.org_id == current_user.org_id,
            ResourceTimelineEvent.resource_type == resource_type,
            ResourceTimelineEvent.resource_id == resource_id,
        )
        .order_by(ResourceTimelineEvent.created_at.asc())
    ).all()
