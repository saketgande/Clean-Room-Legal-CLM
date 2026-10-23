"""APP-02: completing an obligation that repeats on a fixed schedule opens its next
occurrence; free-text recurrences ("Ongoing during term") never recur."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.contracts.models import Contract
from app.core.enums import ObligationStatus
from app.obligations import routes
from app.obligations import service as obligations_service
from app.obligations.models import Obligation, ObligationReminder
from app.obligations.service import next_due_date


@pytest.mark.parametrize("recurrence, due, expected", [
    ("Monthly", date(2026, 1, 31), date(2026, 2, 28)),
    ("Quarterly", date(2026, 11, 15), date(2027, 2, 15)),
    ("Annually", date(2024, 2, 29), date(2025, 2, 28)),
    ("semi_annually", date(2026, 8, 31), date(2027, 2, 28)),  # the skill's typed value
    ("Monthly on overdue amounts", date(2026, 3, 10), date(2026, 4, 10)),
    ("Ongoing during term and survival period", date(2026, 3, 10), None),
    ("Monthly", None, None),
    (None, date(2026, 3, 10), None),
])
def test_next_due_date_follows_a_fixed_cadence_only(recurrence, due, expected):
    assert next_due_date(recurrence, due) == expected


class FakeDB:
    def __init__(self, ob):
        self.ob, self.added = ob, []

    def get(self, model, key):
        if model is Obligation and key == self.ob.id:
            return self.ob
        if model is Contract:
            return SimpleNamespace(title="MSA", counterparty_name="Acme")
        return None

    def refresh(self, obj, **kwargs):
        pass

    def add(self, obj):
        self.added.append(obj)

    def scalars(self, stmt):  # no earlier reminders to replace
        return SimpleNamespace(all=list)

    def delete(self, obj):
        pass

    def flush(self):
        for obj in self.added:
            obj.id = obj.id or "ob-next"

    def commit(self):
        pass


def test_completing_a_monthly_obligation_opens_the_next_one(monkeypatch):
    audits = []
    monkeypatch.setattr(routes, "get_contract_for_user", lambda db, **kw: None)
    # The audit write moved into ObligationsService with the DI refactor.
    monkeypatch.setattr(obligations_service, "write_audit_log", lambda db, **kw: audits.append(kw["after"]))
    ob = Obligation(id="ob-1", org_id="org-1", contract_id="c-1", description="Send the usage report",
                    status="open", recurrence="Monthly", due_date=date(2026, 9, 30), metadata_json={})
    db = FakeDB(ob)
    user = SimpleNamespace(id="u-1", org_id="org-1")
    service = obligations_service.ObligationsService(db)

    routes.complete_obligation("ob-1", db=db, current_user=user, service=service)
    [successor] = [o for o in db.added if isinstance(o, Obligation)]
    assert ob.status == ObligationStatus.COMPLETED
    assert successor.due_date == date(2026, 10, 30)
    assert (successor.status, successor.metadata_json) == (
        ObligationStatus.OPEN,
        {"parent_obligation_id": "ob-1", "series_start": "2026-09-30", "occurrence": 1},
    )
    assert audits[0]["next_obligation_id"] == successor.id

    routes.complete_obligation("ob-1", db=db, current_user=user, service=service)  # completing it again adds nothing
    assert len([o for o in db.added if isinstance(o, Obligation)]) == 1


def test_the_next_occurrence_gets_a_reminder(monkeypatch):
    """Guards the successor's date never reminding anyone: reminders were only
    written at extraction, so every later occurrence of a monthly duty was silent."""
    monkeypatch.setattr(obligations_service, "utcnow", lambda: datetime(2026, 9, 1, tzinfo=UTC))
    ob = Obligation(id="ob-1", org_id="org-1", contract_id="c-1", description="Send the usage report",
                    status="open", recurrence="monthly", due_date=date(2026, 9, 30), metadata_json={})
    db = FakeDB(ob)
    successor = obligations_service.complete_and_schedule_next(db, ob=ob, actor_user_id="u-1")
    [reminder] = [o for o in db.added if isinstance(o, ObligationReminder)]
    assert (reminder.obligation_id, reminder.remind_at) == (successor.id, date(2026, 10, 23))


def test_each_occurrence_is_counted_from_the_first_due_date(monkeypatch):
    """Guards month-end drift: completing a monthly 31 Jan series must open 28 Feb,
    then 31 Mar and 30 Apr. Chaining from the previous date stuck on the 28th."""
    monkeypatch.setattr(obligations_service, "write_audit_log", lambda db, **kw: None)
    ob = Obligation(id="ob-1", org_id="org-1", contract_id="c-1", description="Pay the fee",
                    status="open", recurrence="monthly", due_date=date(2027, 1, 31), metadata_json={})
    dues = []
    for n in range(3):
        db = FakeDB(ob)
        successor = obligations_service.complete_and_schedule_next(db, ob=ob, actor_user_id="u-1")
        successor.id = f"ob-{n + 2}"
        dues.append(successor.due_date)
        ob = successor
    assert dues == [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)]
