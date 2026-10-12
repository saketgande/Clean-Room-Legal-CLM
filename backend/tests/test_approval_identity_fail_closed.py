"""AUTH-06: an approver who can't be identified must be refused, never treated as
exempt from the duplicate-decision and delegated-authority checks."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.approvals import service
from app.core.enums import ApprovalStatus


class QueueDB:
    """Returns prepared results for successive db.scalar() calls."""

    def __init__(self, *results):
        self.results = list(results)

    def scalar(self, _stmt):
        return self.results.pop(0)


def test_token_for_an_unknown_approver_is_rejected(monkeypatch):
    applied = []

    async def fake_apply(*args, **kwargs):
        applied.append(kwargs)

    monkeypatch.setattr(service, "_apply_decision", fake_apply)
    token_row = SimpleNamespace(
        used_at=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        approval_request_id="apr-1",
        org_id="org-1",
        intended_approver_email="former.employee@example.com",
    )
    approval = SimpleNamespace(id="apr-1", org_id="org-1", requested_by_user_id="u-requester")
    db = QueueDB(token_row, approval, None)  # token, approval, no matching user

    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.redeem_token_decision(db, token="apvl_x", decision="approve", comment=None))
    assert exc.value.status_code == 401
    assert applied == []


def test_decision_without_an_identified_approver_is_refused():
    approval = SimpleNamespace(status=ApprovalStatus.PENDING)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            service._apply_decision(
                None, approval=approval, subject=None, decision="approve",
                comment=None, actor_user_id=None, actor_label="token:unknown",
            )
        )
    assert exc.value.status_code == 401
