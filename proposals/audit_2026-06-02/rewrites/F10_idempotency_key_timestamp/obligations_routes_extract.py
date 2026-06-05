"""F-10 rewrite — ``obligations.routes.trigger_obligation_extraction``.

Replaces ``backend/app/obligations/routes.py:132-168``. The previous
body baked ``utcnow().timestamp()`` into the idempotency key, so every
click — including rapid double-clicks — minted a unique key and bypassed
the ``job_run.idempotency_key`` UNIQUE constraint (Agent 2 F-10 — High,
real Claude $ leak).

The new key uses ``build_idempotency_key`` (CC-5) with a 5-minute
``debounce_token`` derived from the user id + minute bucket. Two
clicks inside the same 5-minute window dedupe to one JobRun. A
deliberate re-extract 6 minutes later succeeds.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.service import get_contract_for_user
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.jobs.idempotency import build_debounce_token, build_idempotency_key
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job

router = APIRouter(prefix="/obligations", tags=["obligations"])


@router.post("/extract", status_code=status.HTTP_202_ACCEPTED)
def trigger_obligation_extraction(
    contract_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
) -> dict[str, str]:
    """Queue an obligation-extraction job; debounced by 5-minute bucket per user."""
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
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
        trigger="manual",
        debounce_token=build_debounce_token(user_id=current_user.id, now=utcnow()),
    )
    job = create_job(
        db,
        org_id=current_user.org_id,
        job_type="obligation_extraction",
        resource_type="contract",
        resource_id=contract.id,
        created_by_user_id=current_user.id,
        idempotency_key=idempotency_key,
        metadata={
            "contract_version_id": version.id,
            "text_snapshot_id": snapshot.id,
        },
    )
    db.commit()
    refreshed = db.get(JobRun, job.id)
    if refreshed is None:
        # Theoretically impossible after commit — but never trust the ORM.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Job vanished after commit"
        )
    dispatch_job(db, job=refreshed)
    db.commit()
    return {"job_id": refreshed.id, "status": refreshed.status}
