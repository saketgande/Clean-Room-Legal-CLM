"""Delegate and Escalate may only hand an approval to someone who can act on it.

The pick lists used to offer every active user (including real mailboxes — each
hand-over sends an email), and the server accepted anyone in the organisation who
was not the requester. A plain member could end up holding an approval they were
not allowed to decide; "escalate" went to anyone, with no idea of seniority.

The rule, in one place (``ApprovalsService.reassign_target_problem``), used both to
build the list and to check the choice:

* delegate  -> the person must hold ``approval:decide``
* escalate  -> the person must hold ``approval:admin`` (a senior approver)
* never the requester, never the person who already holds it, never an inactive account
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approvals import routes, service
from app.approvals.models import ApprovalRequest
from app.approvals.service import ApprovalsService
from app.auth.models import User
from app.core import org_access
from app.core.database import engine, new_uuid
from app.organizations.models import Organization


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="Reassign Co", slug=f"reassign-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db: Session, org_id: str, label: str, *, status: str = "active") -> User:
    u = User(org_id=org_id, email=f"{label}-{uuid.uuid4().hex[:6]}@example.com", full_name=label,
             hashed_password="h", status=status)
    db.add(u)
    db.flush()
    return u


def _approval(db: Session, *, org_id: str, requester: User, holder: User | None) -> ApprovalRequest:
    row = ApprovalRequest(
        org_id=org_id, contract_id=None, intake_request_id=None, status="pending",
        requested_by_user_id=requester.id, approver_user_id=holder.id if holder else None,
        step_order=1, mode="any", metadata_json={},
        created_by_user_id=requester.id, updated_by_user_id=requester.id,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def perms(monkeypatch):
    """Control who holds what, by user id, without building roles and grants."""
    table: dict[str, set[str]] = {}

    def fake(db, *, org_id, user_ids, at=None):
        return {uid: set(table.get(uid, set())) for uid in user_ids}

    monkeypatch.setattr(org_access, "effective_permission_values_for_users", fake)
    return table


@pytest.fixture
def people(db, perms):
    org = _org(db)
    cast = SimpleNamespace(
        org=org,
        requester=_user(db, org.id, "requester"),
        holder=_user(db, org.id, "holder"),
        actor=_user(db, org.id, "actor"),
        approver=_user(db, org.id, "approver"),
        senior=_user(db, org.id, "senior"),
        member=_user(db, org.id, "member"),
        gone=_user(db, org.id, "gone", status="disabled"),
    )
    perms[cast.holder.id] = {"approval:decide"}
    perms[cast.actor.id] = {"approval:decide"}
    perms[cast.approver.id] = {"approval:decide"}
    perms[cast.senior.id] = {"approval:decide", "approval:admin"}
    perms[cast.member.id] = {"contract:read"}
    perms[cast.gone.id] = {"approval:decide", "approval:admin"}
    perms[cast.requester.id] = {"approval:decide", "approval:admin"}
    cast.approval = _approval(db, org_id=org.id, requester=cast.requester, holder=cast.holder)
    return cast


def _problem(db, p, to_user, kind):
    return ApprovalsService(db).reassign_target_problem(approval=p.approval, to_user=to_user, kind=kind)


# --- the rule ---------------------------------------------------------------

def test_a_delegate_must_be_able_to_decide(db, people):
    assert _problem(db, people, people.approver, "delegate") is None
    refusal = _problem(db, people, people.member, "delegate")
    assert refusal and "member" in refusal and "approv" in refusal.lower()


def test_an_escalation_must_go_to_a_senior_approver(db, people):
    assert _problem(db, people, people.senior, "escalate") is None
    refusal = _problem(db, people, people.approver, "escalate")
    assert refusal and "approver" in refusal and "senior" in refusal.lower()


def test_nobody_can_hand_it_to_the_requester_the_holder_or_a_closed_account(db, people):
    assert "own request" in _problem(db, people, people.requester, "delegate")
    assert "already" in _problem(db, people, people.holder, "delegate")
    assert "not active" in _problem(db, people, people.gone, "delegate")


def test_a_person_from_another_organisation_is_not_found(db, people, perms):
    other_org = _org(db)
    outsider = _user(db, other_org.id, "outsider")
    perms[outsider.id] = {"approval:decide", "approval:admin"}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ApprovalsService(db).reassign_rung(
            approval=people.approval, to_user=outsider, kind="delegate", actor=people.actor))
    assert exc.value.status_code == 404


# --- the pick list is the same rule -----------------------------------------

def test_the_delegate_list_holds_only_people_who_can_decide(db, people):
    names = {u.full_name for u in ApprovalsService(db).reassign_candidates(
        approval=people.approval, kind="delegate")}
    assert names == {"actor", "approver", "senior"}


def test_the_escalate_list_holds_only_senior_approvers(db, people):
    names = {u.full_name for u in ApprovalsService(db).reassign_candidates(
        approval=people.approval, kind="escalate")}
    assert names == {"senior"}


# --- the server refuses what the list would not offer -----------------------

@pytest.fixture
def quiet(monkeypatch):
    """Skip the email and the audit rows; the rule is what is under test."""
    async def no_email(self, **kwargs):
        return False

    monkeypatch.setattr(ApprovalsService, "_activate_step", no_email)
    monkeypatch.setattr(
        ApprovalsService, "_subject_for",
        lambda self, approval: SimpleNamespace(kind="contract", id="c-1", title="MSA"),
    )
    monkeypatch.setattr(service, "write_audit_log", lambda *a, **k: None)
    monkeypatch.setattr(service, "write_timeline_event", lambda *a, **k: None)


def _reassign(db, p, to_user, kind):
    return asyncio.run(ApprovalsService(db).reassign_rung(
        approval=p.approval, to_user=to_user, kind=kind, actor=p.actor))


def test_delegating_to_a_plain_member_is_refused_and_changes_nothing(db, people, quiet):
    with pytest.raises(HTTPException) as exc:
        _reassign(db, people, people.member, "delegate")
    assert exc.value.status_code == 422
    assert people.approval.approver_user_id == people.holder.id


def test_escalating_to_a_plain_approver_is_refused(db, people, quiet):
    with pytest.raises(HTTPException) as exc:
        _reassign(db, people, people.approver, "escalate")
    assert exc.value.status_code == 422
    assert people.approval.approver_user_id == people.holder.id


def test_a_valid_hand_over_still_works(db, people, quiet):
    _reassign(db, people, people.senior, "escalate")
    assert people.approval.approver_user_id == people.senior.id
    assert people.approval.metadata_json["reassign"]["kind"] == "escalate"


# --- the route that serves the list ----------------------------------------

def test_the_candidates_route_is_limited_to_someone_who_may_move_the_approval(db, people, monkeypatch):
    monkeypatch.setattr(routes, "_can_decide_approval", lambda *a, **k: False)
    with pytest.raises(HTTPException) as exc:
        routes.reassign_candidates(
            people.approval.id, kind="delegate", db=db,
            approvals_service=ApprovalsService(db), current_user=people.actor)
    assert exc.value.status_code == 403


def test_the_candidates_route_returns_brief_people(db, people, monkeypatch):
    monkeypatch.setattr(routes, "_can_decide_approval", lambda *a, **k: True)
    rows = routes.reassign_candidates(
        people.approval.id, kind="escalate", db=db,
        approvals_service=ApprovalsService(db), current_user=people.actor)
    assert [r["full_name"] for r in rows] == ["senior"]
    assert set(rows[0]) >= {"id", "full_name", "email", "roles"}


def test_the_candidates_route_rejects_an_unknown_kind(db, people):
    with pytest.raises(HTTPException) as exc:
        routes.reassign_candidates(
            people.approval.id, kind="sideways", db=db,
            approvals_service=ApprovalsService(db), current_user=people.actor)
    assert exc.value.status_code == 422
