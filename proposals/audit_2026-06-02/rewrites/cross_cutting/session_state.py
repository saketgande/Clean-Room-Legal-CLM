"""Cross-cutting decision CC-2 — centralized assistant session state.

Collapses the three duplicate contract-handle allocators at
``backend/app/ai/controller.py:736-774`` (``_handle_for_contract``),
``backend/app/ai/tool_runtime.py:399-431`` (``_find_contracts``), and
``backend/app/assistant/routes.py:539-587`` (``_ensure_contract_handle``)
into a single per-session-locked allocator. Resolves Agent 2 findings
F-09 (TOCTOU race), F-18 (three copies), F-30 (divergent validation).

This module is intended to live at ``backend/app/ai/session_state.py``
once merged. The Postgres advisory lock is keyed off the
session_id hash so two concurrent workers serving the same session
serialize, and the UNIQUE(session_id, handle) index created by the
companion Alembic migration is the last line of defense against
duplicate handles.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.models import AssistantContractHandle
from app.core.config import settings

_logger = logging.getLogger(__name__)

# Advisory-lock key namespace for session-handle allocation. Choosing a
# stable prefix so this lock never collides with the audit-chain lock
# (0x6175_6469_745f_6c6f / "audit_lo").
_HANDLE_LOCK_NAMESPACE = 0x6173_7374_5f68_646c  # ascii "asst_hdl"


def _session_lock_key(session_id: str) -> int:
    """Return a 63-bit Postgres advisory lock key derived from session_id."""
    digest = hashlib.sha256(session_id.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], byteorder="big", signed=False)
    # Postgres bigint is 63-bit signed when used positively here.
    return (raw ^ _HANDLE_LOCK_NAMESPACE) & 0x7FFF_FFFF_FFFF_FFFF


def _acquire_session_lock(db: Session, session_id: str) -> None:
    """Acquire a Postgres advisory lock; no-op on backends without it (SQLite tests)."""
    if not settings.database_url.startswith("postgres"):
        return
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _session_lock_key(session_id)},
        )
    except Exception:  # pragma: no cover - lock acquisition surface differs by driver
        _logger.warning(
            "session_state.advisory_lock_unavailable",
            extra={"session_id": session_id},
        )


def _existing_handle_for_contract(
    db: Session,
    *,
    org_id: str,
    session_id: str,
    contract_id: str,
) -> AssistantContractHandle | None:
    return db.scalar(
        select(AssistantContractHandle).where(
            AssistantContractHandle.org_id == org_id,
            AssistantContractHandle.session_id == session_id,
            AssistantContractHandle.contract_id == contract_id,
        )
    )


def _next_handle_value(
    db: Session,
    *,
    org_id: str,
    session_id: str,
) -> str:
    """Return the next unused handle value for the session."""
    count = db.scalar(
        select(func.count(AssistantContractHandle.id)).where(
            AssistantContractHandle.org_id == org_id,
            AssistantContractHandle.session_id == session_id,
        )
    ) or 0
    return f"contract-{int(count)}"


def allocate_contract_handle(
    db: Session,
    *,
    org_id: str,
    session_id: str,
    contract_id: str,
    user_id: str | None,
    requested_handle: str | None = None,
) -> AssistantContractHandle:
    """Atomically allocate (or fetch) a per-session contract handle."""
    existing = _existing_handle_for_contract(
        db, org_id=org_id, session_id=session_id, contract_id=contract_id
    )
    if existing is not None:
        return existing
    _acquire_session_lock(db, session_id)
    # Re-check after acquiring the lock: another worker may have just inserted.
    existing = _existing_handle_for_contract(
        db, org_id=org_id, session_id=session_id, contract_id=contract_id
    )
    if existing is not None:
        return existing
    if requested_handle:
        duplicate = db.scalar(
            select(AssistantContractHandle).where(
                AssistantContractHandle.org_id == org_id,
                AssistantContractHandle.session_id == session_id,
                AssistantContractHandle.handle == requested_handle,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Contract handle already exists"
            )
        handle_value = requested_handle
    else:
        handle_value = _next_handle_value(
            db, org_id=org_id, session_id=session_id
        )
    record = AssistantContractHandle(
        org_id=org_id,
        session_id=session_id,
        contract_id=contract_id,
        handle=handle_value,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError as exc:
        # The UNIQUE(session_id, handle) index caught a race we lost. Roll
        # back the failing INSERT and re-fetch — the winning side already
        # populated the row.
        db.rollback()
        retry = _existing_handle_for_contract(
            db, org_id=org_id, session_id=session_id, contract_id=contract_id
        )
        if retry is not None:
            return retry
        # If even after rollback the row is missing, the violation is on the
        # handle column (requested_handle collision under race). Surface 409.
        _logger.warning(
            "session_state.handle_unique_violation",
            extra={
                "session_id": session_id,
                "contract_id": contract_id,
                "requested_handle": requested_handle,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Contract handle already exists"
        ) from exc
    return record


def allocate_handles_for_search(
    db: Session,
    *,
    org_id: str,
    session_id: str,
    contract_ids: list[str],
    user_id: str | None,
) -> dict[str, str]:
    """Allocate handles for a batch of search-matched contracts in one pass."""
    if not contract_ids:
        return {}
    out: dict[str, str] = {}
    for contract_id in contract_ids:
        handle = allocate_contract_handle(
            db,
            org_id=org_id,
            session_id=session_id,
            contract_id=contract_id,
            user_id=user_id,
        )
        out[contract_id] = handle.handle
    return out


def list_session_handles(
    db: Session,
    *,
    org_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return the session's contract-handle map sorted by creation time."""
    rows = db.scalars(
        select(AssistantContractHandle)
        .where(
            AssistantContractHandle.org_id == org_id,
            AssistantContractHandle.session_id == session_id,
        )
        .order_by(AssistantContractHandle.created_at.asc())
    ).all()
    return [
        {"handle": row.handle, "contract_id": row.contract_id}
        for row in rows
    ]
