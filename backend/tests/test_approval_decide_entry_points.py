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
        approver_role=None, approver_team_id=None,
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
    db = FakeDB(approval)
    return asyncio.run(routes.decide_approval(
        "appr-1", payload, request, db=db,
        approvals_service=service.ApprovalsService(db), current_user=user,
    ))


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


# --- Spec 009: assigned-only deciding and the open-risk acknowledgement ------------------------

ADMIN = {"approval:decide", "approval:admin"}


def _admin(user_id="admin"):
    return SimpleNamespace(id=user_id, org_id="org-1", roles=[], permission_values=ADMIN)


@pytest.fixture
def risks(monkeypatch):
    """Open risk counts and the audit rows the route writes, without a database."""
    from app.intake import approval_summary

    state = {"counts": {"critical": 1, "high": 2}, "audit": []}
    contract = SimpleNamespace(id="c-1", metadata_json={})
    monkeypatch.setattr(approval_summary, "subject_contract", lambda db, approval: contract)
    monkeypatch.setattr(approval_summary, "open_risk_counts", lambda db, c: state["counts"] if c else {"critical": 0, "high": 0})
    monkeypatch.setattr(routes, "write_audit_log", lambda db, **kw: state["audit"].append(kw))
    return state


def _route_with(approval, user, decision="approve", acknowledged=False, comment="ok"):
    payload = SimpleNamespace(decision=decision, comment=comment, acknowledged_open_risks=acknowledged)
    request = SimpleNamespace(state=SimpleNamespace(request_id="r-1"))
    db = FakeDB(approval)
    return asyncio.run(routes.decide_approval(
        "appr-1", payload, request, db=db,
        approvals_service=service.ApprovalsService(db), current_user=user,
    ))


@pytest.mark.parametrize("decide", [_via_tool, _via_route], ids=["tool", "route"])
def test_an_admin_who_is_not_assigned_cannot_decide(applied, decide):
    """The approval:admin shortcut once let any admin approve every rung; deciding needs an assignment."""
    with pytest.raises(HTTPException) as exc:
        decide(_approval(approver_user_id="someone-else"), _admin())
    assert exc.value.status_code == 403
    assert applied == []


def test_an_assigned_admin_can_decide(applied):
    """Assignment, not the admin permission, is what counts; an assigned admin is not locked out."""
    _via_route(_approval(approver_user_id="admin"), _admin())
    assert len(applied) == 1


def test_admin_keeps_the_view_and_reassign_shortcut():
    """assigned_only must not leak into viewing or reassigning, which admins still do."""
    db = FakeDB(_approval(approver_user_id="someone-else"))
    approval = _approval(approver_user_id="someone-else")
    assert routes._can_decide_approval(db, approval=approval, user=_admin()) is True
    assert routes._can_decide_approval(db, approval=approval, user=_admin(), assigned_only=True) is False


def test_an_already_decided_approval_is_409_before_the_assignment_check(applied):
    """A stale page must get 'already decided', not a misleading 403 for someone no longer able to act."""
    with pytest.raises(HTTPException) as exc:
        _via_route(_approval(status="approved", approver_user_id="someone-else"), _user())
    assert exc.value.status_code == 409
    assert exc.value.detail == "Approval request is already decided"


def test_approving_with_open_risks_needs_acknowledgement(applied, risks):
    """Without the gate a critical/high risk could be approved by one stray click."""
    with pytest.raises(HTTPException) as exc:
        _route_with(_approval(), _user())
    assert exc.value.status_code == 422
    assert exc.value.detail == "Open critical or high risks must be acknowledged before approving"
    assert applied == [] and risks["audit"] == []


def test_acknowledged_approval_writes_the_audit_row(applied, risks):
    """The acknowledgement must be on the record, with the counts the approver saw."""
    _route_with(_approval(), _user(), acknowledged=True)
    assert len(applied) == 1
    (row,) = risks["audit"]
    assert row["action"] == "approval.risk_acknowledged"
    assert row["resource_type"] == "approval_request" and row["resource_id"] == "appr-1"
    assert row["actor_user_id"] == "approver" and row["request_id"] == "r-1"
    assert row["after"] == {
        "critical": 1, "high": 2, "contract_id": "c-1", "intake_request_id": "req-1",
    }


def test_a_rejection_never_needs_acknowledgement(applied, risks):
    """Rejecting is the safe direction; gating it would trap people with open risks."""
    _route_with(_approval(), _user(), decision="reject")
    assert len(applied) == 1 and risks["audit"] == []


def test_approving_with_no_open_risks_writes_no_acknowledgement(applied, risks):
    """An acknowledgement row for a clean contract would claim a risk that was never shown."""
    risks["counts"] = {"critical": 0, "high": 0}
    _route_with(_approval(), _user())
    assert len(applied) == 1 and risks["audit"] == []


def test_a_refused_approval_leaves_no_acknowledgement_row(applied, risks, monkeypatch):
    """The audit row follows a successful decision only; a refused one (authority) must write none."""
    monkeypatch.setattr(authority, "evaluate_authority", _authority(False))
    with pytest.raises(HTTPException) as exc:
        _route_with(_approval(), _user(), acknowledged=True)
    assert exc.value.status_code == 403
    assert risks["audit"] == []


def test_a_blank_rejection_comment_is_refused():
    """'   ' used to count as a comment, so a reject could be saved with no reason."""
    db = FakeDB(_approval())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.ApprovalsService(db)._apply_decision(
            approval=_approval(), subject=SimpleNamespace(), decision="reject", comment="   ",
            actor_user_id="approver", actor_label="A",
        ))
    assert exc.value.status_code == 422
    assert exc.value.detail == "Rejection requires a comment"
