"""Deciding an approval in the app — through the web route or the assistant tool
— must refuse the same people: someone not assigned to the step, the person who
raised the request, and an approver above their delegated authority.

The assistant tool used to call decide_in_app directly and skipped all three.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai import tool_runtime as tr
from app.ai.tool_registry import tool_registry
from app.approvals import routes, service
from app.authority import service as authority
from app.core import authz
from app.intake import approval_bridge


class FakeDB:
    def __init__(self, approval):
        self.approval = approval

    def scalar(self, *_args, **_kwargs):
        return self.approval

    def get(self, *_args, **_kwargs):
        return None

    def commit(self):
        pass

    def refresh(self, *_args, **_kwargs):
        pass


def _approval(**overrides):
    fields = dict(
        id="appr-1", org_id="org-1", status="pending", contract_id=None, intake_request_id="req-1",
        requested_by_user_id="requester", approver_user_id="approver",
        approver_role=None, approver_group_id=None,
    )
    return SimpleNamespace(**{**fields, **overrides})


def _user(user_id="approver"):
    return SimpleNamespace(id=user_id, org_id="org-1", roles=[], permission_values={"approval:decide"})


def _authority(allowed):
    decision = authority.AuthorityDecision(allowed=allowed, gated=True, reason="value exceeds the limit")
    return lambda db, **kwargs: decision


@pytest.fixture
def applied(monkeypatch):
    """Record decisions that got past the checks instead of applying them."""
    calls = []

    async def fake_apply(db, **kwargs):
        calls.append(kwargs)
        return kwargs["approval"]

    monkeypatch.setattr(service, "_apply_decision", fake_apply)
    monkeypatch.setattr(service, "_subject_for", lambda db, approval: SimpleNamespace())
    monkeypatch.setattr(approval_bridge, "build_intake_subject", lambda db, request_id, org_id: SimpleNamespace())
    monkeypatch.setattr(authority, "evaluate_authority", _authority(True))
    monkeypatch.setattr(authz, "record_decision", lambda **kwargs: None)
    monkeypatch.setattr(
        tr.tool_runtime, "_resolve_request", lambda db, ref, user: SimpleNamespace(id="req-1", ref="REQ-1")
    )
    return calls


def _via_tool(approval, user, decision="approve"):
    payload = SimpleNamespace(request_id="REQ-1", decision=decision, comment="looks fine")
    return asyncio.run(tr.tool_runtime._decide_approval(FakeDB(approval), payload=payload, user=user))


def _via_route(approval, user, decision="approve"):
    payload = SimpleNamespace(decision=decision, comment="looks fine")
    request = SimpleNamespace(state=SimpleNamespace(request_id=None))
    return asyncio.run(routes.decide_approval("appr-1", payload, request, db=FakeDB(approval), current_user=user))


@pytest.mark.parametrize("decide", [_via_tool, _via_route], ids=["tool", "route"])
def test_refuses_an_approver_who_is_not_assigned(applied, decide):
    with pytest.raises(HTTPException) as exc:
        decide(_approval(approver_user_id="someone-else"), _user())
    assert exc.value.status_code == 403
    assert applied == []


@pytest.mark.parametrize("decide", [_via_tool, _via_route], ids=["tool", "route"])
def test_refuses_the_requester_approving_their_own_request(applied, decide):
    with pytest.raises(HTTPException) as exc:
        decide(_approval(requested_by_user_id="approver"), _user())
    assert exc.value.status_code == 403
    assert applied == []


@pytest.mark.parametrize("decide", [_via_tool, _via_route], ids=["tool", "route"])
def test_refuses_an_approver_above_their_authority(applied, decide, monkeypatch):
    monkeypatch.setattr(authority, "evaluate_authority", _authority(False))
    with pytest.raises(HTTPException) as exc:
        decide(_approval(), _user())
    assert exc.value.status_code == 403
    assert "delegated authority" in exc.value.detail
    assert applied == []


@pytest.mark.parametrize("decide", [_via_tool, _via_route], ids=["tool", "route"])
def test_the_assigned_approver_can_still_decide(applied, decide):
    decide(_approval(), _user())
    assert len(applied) == 1


def test_a_rejection_is_not_held_to_the_authority_limit(applied, monkeypatch):
    monkeypatch.setattr(authority, "evaluate_authority", _authority(False))
    _via_tool(_approval(), _user(), decision="reject")
    assert len(applied) == 1


def test_the_tool_needs_the_same_permission_as_the_route():
    assert tool_registry.get("decide_approval").required_permission == "approval:decide"
