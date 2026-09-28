from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.jobs import tasks


class FakeDB:
    def __init__(self, overdue, intake_request):
        self._overdue = overdue
        self._intake_request = intake_request
        self._scalar_calls = 0
        self.added = []

    def scalars(self, stmt):
        # First query is the overdue sweep; any later one (org admins) is empty.
        self._scalar_calls += 1
        rows = self._overdue if self._scalar_calls == 1 else []
        return SimpleNamespace(all=lambda: rows)

    def get(self, model, pk):
        return self._intake_request

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def close(self):
        pass


def test_overdue_intake_approval_is_titled_from_the_request(monkeypatch):
    """The beat task read `IntakeRequest.title`, which doesn't exist, so every
    run crashed on the first intake-linked approval and nothing was flagged."""
    req = SimpleNamespace(
        org_id="org-1", contract_id=None, intake_request_id="ir-1",
        approver_user_id="u-approver", approver_group_id=None,
        requested_by_user_id="u-submitter", step_order=1, metadata_json={},
        due_at=datetime.now(UTC) - timedelta(days=2),
    )
    intake_request = SimpleNamespace(subject=None, ref="REQ-4123")  # no .title
    db = FakeDB([req], intake_request)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)

    result = tasks.mark_overdue_approvals()

    assert result["marked_overdue"] == 1
    assert db.added and all("REQ-4123" in n.subject for n in db.added)
    assert "overdue" in req.metadata_json
