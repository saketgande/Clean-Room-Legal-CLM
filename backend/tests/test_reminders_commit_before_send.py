"""JOB-03: a reminder is recorded as sent, and committed, before its email goes out,
so a crash or time limit mid-batch never re-sends the whole batch."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.core.enums import ObligationStatus
from app.integrations import resend
from app.jobs import tasks
from app.obligations.models import Obligation


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ReminderDB:
    def __init__(self, obligations, reminders, owners):
        self.batches = [reminders]  # obligation statuses are recomputed with set-based UPDATEs
        self.obligations = {o.id: o for o in obligations}
        self.reminders = reminders
        self.owners = owners
        self.committed = {r.id: None for r in reminders}

    def scalars(self, _stmt):
        return _Rows(self.batches.pop(0))

    def get(self, model, key):
        return self.obligations.get(key) if model is Obligation else self.owners.get(key)

    def execute(self, _stmt):
        return type("Result", (), {"rowcount": 0})()

    def commit(self):
        self.committed = {r.id: r.sent_at for r in self.reminders}

    def close(self):
        pass


def test_each_reminder_is_committed_before_its_email(monkeypatch):
    today = tasks.utcnow().date()
    obligations = [
        SimpleNamespace(id=f"ob-{i}", due_date=today + timedelta(days=3), status=ObligationStatus.OPEN,
                        deleted_at=None, owner_user_id=f"u-{i}", obligation_type="Payment")
        for i in (1, 2)
    ]
    reminders = [SimpleNamespace(id=f"r-{i}", obligation_id=f"ob-{i}", sent_at=None) for i in (1, 2)]
    owners = {"u-1": SimpleNamespace(email="ok@example.com"), "u-2": SimpleNamespace(email="bounce@example.com")}
    db = ReminderDB(obligations, reminders, owners)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    reminder_for = {"ok@example.com": "r-1", "bounce@example.com": "r-2"}
    delivered = []

    async def fake_send(*, to, subject, html):
        assert db.committed[reminder_for[to]] is not None, "email sent before its reminder was committed"
        if to.startswith("bounce"):
            raise RuntimeError("provider down")
        delivered.append(to)

    monkeypatch.setattr(resend.resend_client, "send_email", fake_send)
    result = asyncio.run(tasks._send_obligation_reminders())
    assert delivered == ["ok@example.com"]
    assert reminders[0].sent_at == today
    assert reminders[1].sent_at is None  # the failed one is retried next run
    assert (result["reminders_sent"], result["reminders_failed"]) == (1, 1)


def test_renewal_flag_is_committed_before_its_email(monkeypatch):
    today = tasks.utcnow().date()
    contract = SimpleNamespace(id="c-1", title="MSA", lifecycle_stage="active", renewal_due=False,
                               owner_user_id="u-1", updated_by_user_id=None, created_by_user_id=None)
    event = SimpleNamespace(id="e-1", contract_id="c-1", renewal_window_starts_at=today - timedelta(days=1),
                            notice_date=today, expiration_date=today + timedelta(days=60), owner_user_id="u-1",
                            metadata_json={})
    committed = {"renewal_due": False}

    class RenewalDB:
        def scalars(self, _stmt):
            return _Rows([event])

        def get(self, _model, key):
            return contract if key == "c-1" else SimpleNamespace(email="owner@example.com")

        def commit(self):
            committed["renewal_due"] = contract.renewal_due

        def close(self):
            pass

    monkeypatch.setattr(tasks, "SessionLocal", lambda: RenewalDB())
    flag_at_send = []

    async def fake_send(*, to, subject, html):
        flag_at_send.append(committed["renewal_due"])

    monkeypatch.setattr(resend.resend_client, "send_email", fake_send)
    asyncio.run(tasks._run_renewal_window_check())
    assert flag_at_send == [True]
