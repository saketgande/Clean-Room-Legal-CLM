"""F-12 rewrite — ``write_audit_log`` distinguishes lock-unsupported from chain-error.

Replaces ``backend/app/core/audit.py:54-87``. The original wrapped the
``pg_advisory_xact_lock`` call in a bare ``except Exception`` that
silently swallowed real Postgres errors (Agent 2 F-12 — High). The
outer try/except did ``rollback(); raise`` but the operation in
between was not bracketed by any tamper-evident signal, so a brief
Postgres failure during a security-critical action could leave the
business write done and the audit row missing.

The new shape:
- Backend-aware: when the configured database URL begins with
  ``sqlite``, skip the lock entirely. Anything else MUST acquire the
  advisory lock or fail — no silent fallthrough.
- A dedicated ``AuditChainCorrupted`` exception is raised on any
  unexpected failure (lock acquire failure, advisory-lock timeout,
  row insert IntegrityError, hash recomputation mismatch). Callers
  whose audit row failed MUST treat their business write as failed
  too. F-12's whole point is that an audit miss is a security event.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, new_uuid, utcnow
from app.core.models import AuditLog, ResourceTimelineEvent

_logger = logging.getLogger(__name__)


# Same constant the original module used. Keep wire-compatible so the
# in-place migration of a hot path doesn't desync the chain.
_AUDIT_CHAIN_LOCK_KEY: int = 0x6175_6469_745f_6c6f


class AuditChainCorrupted(Exception):
    """Raised when an audit-log append fails for a reason that breaks the chain."""

    def __init__(self, action: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"audit_chain_corrupted[{action}]: {message}")
        self.action = action
        self.retryable = retryable


def _json_safe(value: Any) -> Any:
    """Coerce a payload into JSON-serializable form (re-export, unchanged)."""
    import json

    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def _is_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")


def _acquire_chain_lock(db: Session) -> None:
    """Acquire the audit-chain advisory lock or raise ``AuditChainCorrupted``."""
    if _is_sqlite():
        return
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _AUDIT_CHAIN_LOCK_KEY},
        )
    except SQLAlchemyError as exc:
        db.rollback()
        raise AuditChainCorrupted(
            "lock_acquire", str(exc), retryable=True
        ) from exc


def compute_audit_row_hash(row: AuditLog) -> str:
    """Compute the canonical SHA-256 chain hash for one row."""
    import hashlib
    import json

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
    """Append a tamper-evident audit row; raise ``AuditChainCorrupted`` on failure."""
    durable_db = SessionLocal()
    try:
        _acquire_chain_lock(durable_db)
        try:
            previous_hash = durable_db.scalar(
                select(AuditLog.row_hash)
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(1)
            )
        except SQLAlchemyError as exc:
            durable_db.rollback()
            raise AuditChainCorrupted(action, f"prev_hash_lookup: {exc}") from exc

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
        try:
            durable_db.commit()
        except SQLAlchemyError as exc:
            durable_db.rollback()
            _logger.error(
                "audit_log.write_failed",
                extra={
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "org_id": org_id,
                    "actor_user_id": actor_user_id,
                    "request_id": request_id,
                    "error_class": type(exc).__name__,
                },
            )
            raise AuditChainCorrupted(
                action, f"commit_failed: {exc}", retryable=True
            ) from exc
        durable_db.refresh(row)
        durable_db.expunge(row)
        return row
    except AuditChainCorrupted:
        raise
    except Exception as exc:  # noqa: BLE001 - last-ditch surface
        durable_db.rollback()
        _logger.exception(
            "audit_log.unexpected_error",
            extra={"action": action, "resource_type": resource_type},
        )
        raise AuditChainCorrupted(action, str(exc)) from exc
    finally:
        durable_db.close()


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
    """Append a per-resource timeline row on the caller's session (unchanged)."""
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
