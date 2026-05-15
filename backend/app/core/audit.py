from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.models import AuditLog, ResourceTimelineEvent


def write_audit_log(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    org_id: str | None = None,
    actor_user_id: str | None = None,
    request_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        org_id=org_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        before=before,
        after=after,
        metadata_json=metadata,
    )
    durable_db = SessionLocal()
    try:
        durable_db.add(row)
        durable_db.commit()
        durable_db.refresh(row)
        durable_db.expunge(row)
    except Exception:
        durable_db.rollback()
        raise
    finally:
        durable_db.close()
    return row


def write_timeline_event(
    db: Session,
    *,
    org_id: str,
    resource_type: str,
    resource_id: str,
    event_type: str,
    title: str,
    actor_user_id: str | None = None,
    request_id: str | None = None,
    job_id: str | None = None,
    skill_run_id: str | None = None,
    assistant_run_id: str | None = None,
    ai_call_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> ResourceTimelineEvent:
    row = ResourceTimelineEvent(
        org_id=org_id,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        title=title,
        details=details,
        request_id=request_id,
        job_id=job_id,
        skill_run_id=skill_run_id,
        assistant_run_id=assistant_run_id,
        ai_call_id=ai_call_id,
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    db.add(row)
    return row
