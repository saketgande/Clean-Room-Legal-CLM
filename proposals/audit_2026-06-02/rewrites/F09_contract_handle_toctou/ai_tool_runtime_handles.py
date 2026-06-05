"""F-09 rewrite — ``ToolRuntime._find_contracts`` handle allocation.

Replaces the inline allocator at
``backend/app/ai/tool_runtime.py:399-431``. The old body computed
``count = len(existing)`` and inserted ``contract-N`` with no lock.
Now it delegates to ``allocate_contract_handle`` (CC-2) for each
matched contract in the search result, ensuring uniqueness even
under concurrent ``find_contracts`` calls on the same session.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.session_state import allocate_contract_handle
from app.ai.tool_registry import FindContractsInput
from app.auth.models import User
from app.contracts.models import Contract


def find_contracts(
    self,
    db: Session,
    *,
    payload: FindContractsInput,
    user: User,
    session_id: str,
) -> dict[str, Any]:
    """Search org's contracts by title/counterparty; ensure handle for each match."""
    like = f"%{payload.query.strip()}%"
    rows = db.scalars(
        select(Contract)
        .where(
            Contract.org_id == user.org_id,
            or_(
                Contract.title.ilike(like),
                Contract.counterparty_name.ilike(like),
            ),
        )
        .limit(8)
    ).all()
    matches: list[dict[str, Any]] = []
    for contract in rows:
        record = allocate_contract_handle(
            db,
            org_id=user.org_id,
            session_id=session_id,
            contract_id=contract.id,
            user_id=user.id,
        )
        matches.append(
            {
                "contract_id": contract.id,
                "title": contract.title,
                "counterparty_name": contract.counterparty_name,
                "lifecycle_stage": contract.lifecycle_stage,
                "handle": record.handle,
            }
        )
    return {"query": payload.query, "found": len(matches), "matches": matches}
