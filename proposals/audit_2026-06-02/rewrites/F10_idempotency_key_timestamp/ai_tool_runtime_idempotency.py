"""F-10 rewrite — ``ToolRuntime._extract_obligations`` idempotency key.

Replaces ``backend/app/ai/tool_runtime.py:1222-1248``. Same fix as the
obligations route — switch from ``f"...:{utcnow().timestamp()}"`` to
``build_idempotency_key(...)`` with a 5-minute debounce token derived
from the calling user id + minute bucket (CC-5).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.ai.tool_registry import ExtractObligationsInput
from app.auth.models import User
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.core.database import utcnow
from app.jobs.idempotency import build_debounce_token, build_idempotency_key
from app.jobs.service import create_job, dispatch_job


def extract_obligations(
    self,
    db: Session,
    *,
    payload: ExtractObligationsInput,
    user: User,
    session_id: str,
) -> dict[str, Any]:
    """Queue obligation_extraction from the assistant; debounced via CC-5."""
    contract = self._resolve_contract(  # type: ignore[attr-defined]
        db, payload=payload, user=user, session_id=session_id
    )
    version = (
        db.get(ContractVersion, contract.current_authoritative_version_id)
        if contract.current_authoritative_version_id
        else None
    )
    snapshot = (
        db.get(ContractTextSnapshot, version.text_snapshot_id)
        if version and version.text_snapshot_id
        else None
    )
    if version is None or snapshot is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Contract has no extractable authoritative version",
        )
    idempotency_key = build_idempotency_key(
        "obligation_extraction",
        version_id=version.id,
        snapshot_id=snapshot.id,
        trigger="assistant",
        debounce_token=build_debounce_token(user_id=user.id, now=utcnow()),
    )
    job = create_job(
        db,
        org_id=user.org_id,
        job_type="obligation_extraction",
        resource_type="contract",
        resource_id=contract.id,
        created_by_user_id=user.id,
        idempotency_key=idempotency_key,
        metadata={
            "contract_version_id": version.id,
            "text_snapshot_id": snapshot.id,
        },
    )
    db.flush()
    dispatch_job(db, job=job)
    db.flush()
    return {
        "status": "queued",
        "contract_id": contract.id,
        "job_id": job.id,
        "job_type": job.job_type,
        "idempotency_key": idempotency_key,
    }
