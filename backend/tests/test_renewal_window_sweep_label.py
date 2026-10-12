"""The daily renewal-window sweep (jobs/tasks._run_renewal_window_check) is the
email people actually get; dates code counted and no person confirmed must say so."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from app.jobs import tasks


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _event(event_id, contract_id, dates):
    today = tasks.utcnow().date()
    return SimpleNamespace(id=event_id, contract_id=contract_id, owner_user_id="u-1",
                           renewal_window_starts_at=today - timedelta(days=1), notice_date=today,
                           expiration_date=today + timedelta(days=90), metadata_json={"dates": dates})


def test_the_scheduled_sweep_labels_unconfirmed_dates_in_subject_and_body(monkeypatch):
    """Guards the beat path emailing a code-counted notice date as if a person had
    checked it (only the admin-triggered sweep carried the label before)."""
    calculated = _event("e-1", "c-1", {"expiration_date": "2027-10-15", "confirmed_by": None})
    confirmed = _event("e-2", "c-2", {"expiration_date": "2027-10-15", "confirmed_by": "u-1"})
    contracts = {
        cid: SimpleNamespace(id=cid, title=f"MSA {cid}", lifecycle_stage="active", renewal_due=False,
                             owner_user_id="u-1", updated_by_user_id=None, created_by_user_id=None)
        for cid in ("c-1", "c-2")
    }

    class DB:
        def scalars(self, _stmt):
            return _Rows([calculated, confirmed])

        def get(self, _model, key):
            return contracts.get(key) or SimpleNamespace(email="owner@example.com")

        def commit(self):
            pass

        def close(self):
            pass

    sent = []

    class Mail:
        async def send_email(self, *, to, subject, html):
            sent.append((subject, html))

    monkeypatch.setattr(tasks, "SessionLocal", DB)
    result = asyncio.run(tasks._run_renewal_window_check(resend=Mail()))
    assert result["contracts_moved_to_renewal_due"] == 2
    (subject1, body1), (subject2, body2) = sent
    assert subject1 == "Renewal window open: MSA c-1 (calculated - not yet confirmed)"
    assert "(calculated - not yet confirmed)" in body1
    assert "not yet confirmed" not in subject2 + body2
