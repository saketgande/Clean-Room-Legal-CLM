"""F-03 caller update — ``obligations.routes.list_obligations``.

Replaces the WHERE clause at
``backend/app/obligations/routes.py:50-58``. The call to
``accessible_contract_filter`` now folds in
``Contract.org_id == user.org_id`` via CC-1, so this caller is safe
even for org admins. The redundant ``Obligation.org_id`` predicate is
kept for defense-in-depth (and because it's the index the planner
will probably use first).
"""

from __future__ import annotations

from datetime import date as _date, timedelta as _timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.core.enums import ObligationStatus
from app.obligations.models import Obligation

router = APIRouter(prefix="/obligations", tags=["obligations"])


@router.get("")
def list_obligations(
    contract_id: str | None = None,
    status_filter: ObligationStatus | None = None,  # F-21: enum-validated
    due_within_days: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:read")),
) -> list[Obligation]:
    """List obligations user can see; org-scoped via CC-1 access policy."""
    query = (
        select(Obligation)
        .join(Contract, Contract.id == Obligation.contract_id)
        .where(
            Obligation.org_id == current_user.org_id,
            Obligation.deleted_at.is_(None),
            accessible_contract_filter(current_user),
        )
    )
    if contract_id:
        get_contract_for_user(db, contract_id=contract_id, user=current_user)
        query = query.where(Obligation.contract_id == contract_id)
    if status_filter is not None:
        query = query.where(Obligation.status == status_filter.value)
    if due_within_days is not None:
        today = _date.today()
        query = query.where(
            Obligation.due_date.isnot(None),
            Obligation.due_date >= today,
            Obligation.due_date <= today + _timedelta(days=due_within_days),
        )
    return list(db.scalars(query.order_by(Obligation.due_date.asc())).all())
