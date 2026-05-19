import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, new_uuid, utcnow
from app.core.models import AuditLog, ResourceTimelineEvent


# Postgres advisory lock key for the audit-log hash chain. Any writer that
# wants to append a row takes this lock first, so two concurrent writers
# cannot both read the same previous_hash and insert siblings — which would
# silently break the chain's tamper-evident guarantee.
#
# Chosen as a fixed bigint so the key never collides with other advisory
# locks in this codebase. Keep this in sync if you ever add another chain.
_AUDIT_CHAIN_LOCK_KEY = 0x6175_6469_745f_6c6f  # ascii bytes "audit_lo"


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
        # Serialize append with a Postgres session-scoped advisory lock so
        # two concurrent audit writers cannot both observe the same tail row
        # and insert siblings that share a prev_hash. Without this the chain
        # is not actually tamper-evident under concurrency. SQLite (used in
        # tests) ignores the function — the test harness is single-writer.
        try:
            durable_db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_CHAIN_LOCK_KEY})
        except Exception:
            # Backends without pg_advisory_xact_lock (e.g. SQLite in tests)
            # fall through; concurrency guarantees come from the DB engine
            # only when running on Postgres.
            durable_db.rollback()
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


def verify_audit_hash_chain(db: Session, *, chunk_size: int = 500) -> bool:
    """Stream-verify the chain so the whole table doesn't have to live in RAM.

    The previous implementation called ``.all()`` and loaded every audit row
    at once — fine for thousands of rows, hostile to memory at millions.
    ``yield_per`` lets SQLAlchemy stream results in fixed-size batches.
    """
    previous_hash = None
    result = db.scalars(
        select(AuditLog)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .execution_options(yield_per=chunk_size)
    )
    for row in result:
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
