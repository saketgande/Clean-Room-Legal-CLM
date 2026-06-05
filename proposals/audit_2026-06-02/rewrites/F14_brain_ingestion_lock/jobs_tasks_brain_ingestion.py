"""F-14 rewrite — auto-enqueued ``contract_brain_ingestion`` race.

Replaces ``backend/app/jobs/tasks.py:216-251`` (``_queue_contract_brain_ingestion``)
and the per-call site behavior at ``backend/app/contract_brain/ingestion.py:14``
(``ingest_contract_brain``).

Original issue (Agent 2 F-14 — High): clause/obligation/renewal jobs
each enqueued a brain-ingestion JobRun keyed by ``...:{reason}``. Three
reasons → three potential JobRuns per contract upload. They ran
concurrently. ``ingest_contract_brain`` marked prior nodes/edges stale
then inserted new ones without a lock; two concurrent runs corrupted
the graph.

The new shape:
- Idempotency key uses ``build_auto_brain_ingestion_key`` (CC-5):
  single canonical key per ``(version_id, snapshot_id)`` regardless of
  which upstream job triggered the enqueue. The unique constraint on
  ``job_run.idempotency_key`` collapses the three siblings to one.
- ``ingest_contract_brain`` now takes a per-contract advisory lock via
  ``pg_advisory_xact_lock(hash(f"brain_ingest:{contract_id}"))`` so
  even if two jobs are somehow enqueued, they serialize at execution
  time and the graph state remains consistent.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs.idempotency import build_auto_brain_ingestion_key
from app.jobs.models import JobRun

_logger = logging.getLogger(__name__)

_BRAIN_INGEST_LOCK_NAMESPACE: int = 0x6272_6169_6e5f_696e  # ascii "brain_in"


def _brain_lock_key(contract_id: str) -> int:
    """63-bit advisory-lock key derived from contract_id."""
    digest = hashlib.sha256(contract_id.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return (raw ^ _BRAIN_INGEST_LOCK_NAMESPACE) & 0x7FFF_FFFF_FFFF_FFFF


def queue_contract_brain_ingestion(
    db: Session,
    *,
    job: JobRun,
    reason: str,
) -> None:
    """Auto-enqueue a brain ingestion; single key regardless of triggering reason."""
    from app.core.enums import JobStatus

    if job.status != JobStatus.SUCCEEDED:
        return
    version_id = (job.metadata_json or {}).get("contract_version_id")
    snapshot_id = (job.metadata_json or {}).get("text_snapshot_id")
    if not version_id:
        return
    idempotency_key = build_auto_brain_ingestion_key(
        version_id=version_id,
        snapshot_id=snapshot_id,
    )
    existing = db.scalar(
        select(JobRun).where(JobRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        _logger.info(
            "brain_ingestion.dedup",
            extra={
                "contract_id": job.resource_id,
                "idempotency_key": idempotency_key,
                "reason": reason,
                "existing_job_id": existing.id,
            },
        )
        return
    from app.jobs.service import create_job, dispatch_job

    brain_job = create_job(
        db,
        org_id=job.org_id,
        job_type="contract_brain_ingestion",
        resource_type="contract",
        resource_id=job.resource_id,
        created_by_user_id=job.created_by_user_id,
        idempotency_key=idempotency_key,
        metadata={
            "contract_version_id": version_id,
            "text_snapshot_id": snapshot_id,
            "triggered_by_job_id": job.id,
            "trigger_reason": reason,
        },
    )
    db.flush()
    brain_job_id = brain_job.id
    db.commit()
    refreshed = db.get(JobRun, brain_job_id)
    if refreshed is None:
        return
    dispatch_job(db, job=refreshed)
    db.commit()


def acquire_brain_ingest_lock(db: Session, *, contract_id: str) -> None:
    """Acquire the per-contract advisory lock around graph mutation."""
    if not settings.database_url.startswith("postgres"):
        return
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _brain_lock_key(contract_id)},
        )
    except Exception:  # pragma: no cover - lock surface differs by driver
        _logger.warning(
            "brain_ingestion.advisory_lock_unavailable",
            extra={"contract_id": contract_id},
        )


def ingest_contract_brain_locked(
    db: Session,
    *,
    org_id: str,
    created_by_user_id: str | None,
    contract: Any,
    version: Any,
    snapshot: Any | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Wrap ``ingest_contract_brain`` with a per-contract advisory lock."""
    from app.contract_brain.ingestion import ingest_contract_brain

    acquire_brain_ingest_lock(db, contract_id=contract.id)
    return ingest_contract_brain(
        db,
        org_id=org_id,
        created_by_user_id=created_by_user_id,
        contract=contract,
        version=version,
        snapshot=snapshot,
        request_id=request_id,
    )
