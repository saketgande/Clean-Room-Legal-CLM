import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, new_uuid, utcnow
from app.core.models import AuditLog, ResourceTimelineEvent


def _json_safe(value: Any) -> Any:
    """Coerce a payload into JSON-serializable form (dates, Decimals, etc.).

    Audit/timeline payloads land in JSON columns; a raw ``date`` from a model
    snapshot would otherwise raise ``TypeError`` at write time and 500 the
    request. Audit logging must never break the operation it records.
    """
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


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
    durable_db = SessionLocal()
    try:
        previous_hash = durable_db.scalar(
            select(AuditLog.row_hash).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1)
        )
        created_at = utcnow()
        row = AuditLog(
            id=new_uuid(),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            org_id=org_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            before=_json_safe(before),
            after=_json_safe(after),
            metadata_json=_json_safe(metadata),
            prev_hash=previous_hash,
            created_at=created_at,
            updated_at=created_at,
        )
        row.row_hash = compute_audit_row_hash(row)
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


def compute_audit_row_hash(row: AuditLog) -> str:
    payload = {
        "id": row.id,
        "org_id": row.org_id,
        "actor_user_id": row.actor_user_id,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "request_id": row.request_id,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "before": row.before,
        "after": row.after,
        "metadata_json": row.metadata_json,
        "prev_hash": row.prev_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_audit_hash_chain(db: Session) -> bool:
    previous_hash = None
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc())).all()
    for row in rows:
        if row.prev_hash != previous_hash:
            return False
        if row.row_hash != compute_audit_row_hash(row):
            return False
        previous_hash = row.row_hash
    return True

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
        details=_json_safe(details),
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
