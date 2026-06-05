"""F-09 rewrite — ``AIController._handle_for_contract`` delegates to CC-2.

Replaces ``backend/app/ai/controller.py:736-774`` (``_handle_for_contract``).
The old body did a SELECT-count followed by an INSERT without any
lock; two concurrent requests on the same session both read the same
count and both inserted ``contract-N`` (Agent 2 F-09 — High).

The new body is a one-line delegation to ``allocate_contract_handle``
in ``app/ai/session_state.py`` (CC-2). All concurrency control,
existence-check, and unique-constraint fallback live in that one
module.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.session_state import allocate_contract_handle


def handle_for_contract(
    self,
    db: Session,
    *,
    org_id: str,
    session_id: str,
    contract_id: str,
    user_id: str | None,
) -> str:
    """Return the per-session handle for this contract; allocate if missing."""
    record = allocate_contract_handle(
        db,
        org_id=org_id,
        session_id=session_id,
        contract_id=contract_id,
        user_id=user_id,
    )
    return record.handle
