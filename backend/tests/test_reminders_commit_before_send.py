"""JOB-03 (renewal window): the flag is committed before its email goes out, so a crash or time limit
mid-batch never re-sends the whole batch. Obligation reminders went the other way round (send, then mark,
one per transaction - tests/test_obligation_deadlines.py): losing a deadline reminder is worse than
repeating one."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.integrations import resend
from app.jobs import tasks


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


def test_renewal_flag_is_committed_before_its_email(monkeypatch):
    today = tasks.utcnow().date()
    contract = SimpleNamespace(id="c-1", title="MSA", lifecycle_stage="active", renewal_due=False,
                               owner_user_id="u-1", updated_by_user_id=None, created_by_user_id=None)
    event = SimpleNamespace(id="e-1", contract_id="c-1", renewal_window_starts_at=today - timedelta(days=1),
                            notice_date=today, expiration_date=today + timedelta(days=60), owner_user_id="u-1",
                            # Code-counted dates: the sweep skips events read before Step 3d.
                            metadata_json={"dates": {"notice_date": today.isoformat()}})
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
