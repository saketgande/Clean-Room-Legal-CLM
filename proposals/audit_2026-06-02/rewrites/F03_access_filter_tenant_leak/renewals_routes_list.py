"""F-03 caller update — ``renewals.routes.list_renewals``.

Updates the WHERE clause to match the same pattern as
``obligations.routes.list_obligations``. CC-1 now guarantees the org
filter is folded into ``accessible_contract_filter`` so the admin
branch can no longer leak cross-org renewals.

Original site: ``backend/app/renewals/routes.py`` ``list_renewals``.
"""

from __future__ import annotations

from datetime import date as _date, timedelta as _timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.core.deps import get_db, require_permission
from app.renewals.models import RenewalEvent

router = APIRouter(prefix="/renewals", tags=["renewals"])


@router.get("")
def list_renewals(
    decision: str | None = None,
    window_days: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("renewal:read")),
) -> list[RenewalEvent]:
    """List renewals user can see; org-scoped via CC-1 access policy."""
    query = (
        select(RenewalEvent)
        .join(Contract, Contract.id == RenewalEvent.contract_id)
        .where(
            RenewalEvent.org_id == current_user.org_id,
            accessible_contract_filter(current_user),
        )
    )
    if decision:
        query = query.where(RenewalEvent.decision == decision)
    if window_days is not None:
        today = _date.today()
        horizon = today + _timedelta(days=window_days)
        query = query.where(
            RenewalEvent.expiration_date.isnot(None),
            RenewalEvent.expiration_date >= today,
            RenewalEvent.expiration_date <= horizon,
        )
    return list(db.scalars(query.order_by(RenewalEvent.expiration_date.asc())).all())
