"""Obligation actions shared by the API and the assistant."""

import calendar
import re
from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import ObligationStatus
from app.obligations.models import Obligation

# Leading cadence word -> months between occurrences. Free-text recurrences such
# as "Ongoing during term" describe how long a duty lasts, not a schedule, so
# they never create a next occurrence.
_CADENCE_MONTHS = {
    "monthly": 1, "quarterly": 3, "semi-annually": 6, "semiannually": 6, "half-yearly": 6,
    "annually": 12, "annual": 12, "yearly": 12,
}


def next_due_date(recurrence: str | None, due: date | None) -> date | None:
    """The next occurrence's due date for a fixed cadence ("Monthly", "Quarterly",
    "Annually"...), or None when the obligation doesn't repeat on a schedule."""
    words = re.findall(r"[a-z-]+", (recurrence or "").lower())
    months = _CADENCE_MONTHS.get(words[0]) if words else None
    if not months or due is None:
        return None
    index = due.month - 1 + months
    year, month = due.year + index // 12, index % 12 + 1
    return date(year, month, min(due.day, calendar.monthrange(year, month)[1]))


def complete_and_schedule_next(db: Session, *, ob: Obligation, actor_user_id: str) -> Obligation | None:
    """Mark an obligation completed and, when it repeats on a fixed schedule, open
    its next occurrence (linked back to this one) with the due date moved forward.
    Returns that next occurrence, or None."""
    db.refresh(ob, with_for_update=True)  # two completions at once can't both add a successor
    if ob.status == ObligationStatus.COMPLETED:
        return None
    ob.status = ObligationStatus.COMPLETED
    ob.updated_by_user_id = actor_user_id
    due = next_due_date(ob.recurrence, ob.due_date)
    if due is None:
        return None
    successor = Obligation(
        org_id=ob.org_id,
        contract_id=ob.contract_id,
        contract_version_id=ob.contract_version_id,
        owner_user_id=ob.owner_user_id,
        responsible_party=ob.responsible_party,
        obligation_type=ob.obligation_type,
        description=ob.description,
        due_date=due,
        recurrence=ob.recurrence,
        status=ObligationStatus.OPEN,
        source_citation=ob.source_citation,
        metadata_json={"parent_obligation_id": ob.id},
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    db.add(successor)
    db.flush()
    return successor
