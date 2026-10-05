"""Workflow "Send to counterparty" step: emailed 7-day share link, counterparty
comments + Submit (expires the link), reviewer sees state and comments."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contract_files.models import ContractShare
from app.contract_files.routes import _get_active_share
from app.contracts.comments_service import add_counterparty_comment
from app.contracts.models import Contract, ContractParty
from app.core.database import engine, new_uuid, utcnow
from app.intake.models import IntakeRequest
from app.integrations.sendgrid import EmailResult, sendgrid_client
from app.notifications.models import Notification
from app.organizations.models import Organization
from app.workflows import counterparty as cp
from app.workflows.models import WorkflowRun, WorkflowStepRun


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


@pytest.fixture
def sent_mail(monkeypatch):
    """Capture outbound mail instead of sending; also silence Celery dispatch."""
    captured: list[dict] = []

    async def _send(*, to, subject, html, include_cc=True):
        captured.append({"to": to, "subject": subject, "html": html, "include_cc": include_cc})
        return EmailResult("mid-1", "sent", {})

    monkeypatch.setattr(sendgrid_client, "send_email", _send)
    dispatched: list = []
    monkeypatch.setattr(
        "app.jobs.tasks.notify_counterparty_submitted.apply_async",
        lambda args=None, **kw: dispatched.append((args, kw)),
    )
    captured_dispatch = dispatched
    return captured, captured_dispatch


def _org(db):
    org = Organization(id=new_uuid(), name="CP Co", slug=f"cp-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db, org_id, label):
    u = User(org_id=org_id, email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", full_name=label,
             hashed_password="h", status="active")
    db.add(u)
    db.flush()
    return u


def _scenario(db, *, step_type="counterparty", with_contract=True):
    org = _org(db)
    owner = _user(db, org.id, "owner")
    contract = None
    if with_contract:
        contract = Contract(
            org_id=org.id, title="Mutual NDA", owner_user_id=owner.id, contract_type="nda",
            risk_band="low", risk_level="low", jurisdiction="US", currency="USD",
            counterparty_name="Acme Ltd", created_by_user_id=owner.id, updated_by_user_id=owner.id,
        )
        db.add(contract)
        db.flush()
    req = IntakeRequest(
        org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=owner.id,
        type_label="NDA", description="t", field_values={}, priority="Medium", status="open",
        stage="new", submitted_at=utcnow(), created_by_user_id=owner.id, updated_by_user_id=owner.id,
    )
    db.add(req)
    db.flush()
    run = WorkflowRun(
        org_id=org.id, request_id=req.id, flow_name="F", flow_version=1,
        steps=[{"id": "s1", "type": step_type, "name": "Send to counterparty", "config": {}}],
        status="waiting", current_index=0, contract_id=contract.id if contract else None,
    )
    db.add(run)
    db.flush()
    sr = WorkflowStepRun(org_id=org.id, flow_run_id=run.id, idx=0, step_type=step_type,
                         step_name="Send to counterparty", status="waiting_human",
                         assignee_user_id=owner.id)
    db.add(sr)
    db.flush()
    return org, owner, contract, run, sr


def _token_from(mail: dict) -> str:
    return re.search(r"/s/([A-Za-z0-9_\-]+)", mail["html"]).group(1)


async def test_send_creates_a_seven_day_link_and_emails_it(db, sent_mail):
    mail, _ = sent_mail
    _, owner, contract, run, sr = _scenario(db)

    state = await cp.send_to_counterparty(
        db, run=run, actor=owner, recipient_email="cp@acme.com", recipient_name="Casey", message="Please review.",
    )

    assert state["state"] == "sent"
    assert mail[0]["to"] == "cp@acme.com"
    assert mail[0]["include_cc"] is False  # internal CC must not leak to the counterparty
    share = db.query(ContractShare).filter(ContractShare.workflow_step_run_id == sr.id).one()
    delta = share.expires_at - utcnow()
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)
    # the emailed token is what authenticates the public routes
    live = _get_active_share(db, token=_token_from(mail[0]), passcode=None)
    assert live.id == share.id
    party = db.query(ContractParty).filter(ContractParty.contract_id == contract.id).one()
    assert party.contact_email == "cp@acme.com"
    assert (sr.result or {})["recipient_email"] == "cp@acme.com"
    assert "token" not in (sr.result or {})
    assert db.query(Notification).filter(Notification.event_type == "workflow.counterparty_sent").count() == 1


async def test_resend_revokes_the_previous_link(db, sent_mail):
    mail, _ = sent_mail
    _o, owner, _c, run, _sr = _scenario(db)

    await cp.send_to_counterparty(db, run=run, actor=owner, recipient_email="cp@acme.com",
                                  recipient_name=None, message=None)
    await cp.send_to_counterparty(db, run=run, actor=owner, recipient_email="cp@acme.com",
                                  recipient_name=None, message=None)

    with pytest.raises(HTTPException) as exc:
        _get_active_share(db, token=_token_from(mail[0]), passcode=None)
    assert exc.value.status_code == 410
    assert _get_active_share(db, token=_token_from(mail[1]), passcode=None) is not None


async def test_submit_expires_the_link_and_notifies_the_reviewer(db, sent_mail):
    mail, dispatched = sent_mail
    _o, owner, _c, run, sr = _scenario(db)
    await cp.send_to_counterparty(db, run=run, actor=owner, recipient_email="cp@acme.com",
                                  recipient_name=None, message=None)
    token = _token_from(mail[0])

    cp.submit_share(db, share=_get_active_share(db, token=token, passcode=None))

    with pytest.raises(HTTPException) as exc:
        _get_active_share(db, token=token, passcode=None)
    assert exc.value.status_code == 410
    assert len(dispatched) == 1
    assert dispatched[0][0] == [sr.id]
    assert cp.get_state(db, run=run, actor=owner)["state"] == "submitted"
    assert (sr.result or {}).get("submitted_at")


async def test_reviewer_sees_counterpartys_comments(db, sent_mail):
    _mail, _ = sent_mail
    _o, owner, contract, run, _sr = _scenario(db)
    await cp.send_to_counterparty(db, run=run, actor=owner, recipient_email="cp@acme.com",
                                  recipient_name=None, message=None)

    add_counterparty_comment(db, contract=contract, author_name="Casey", body="Clause 4 is too broad")

    comments = cp.get_state(db, run=run, actor=owner)["comments"]
    assert [c["body"] for c in comments] == ["Clause 4 is too broad"]
    assert comments[0]["author_kind"] == "counterparty"


async def test_rejects_bad_state_and_bad_input(db, sent_mail):
    _o, owner, _c, run, _sr = _scenario(db, step_type="human_task")
    with pytest.raises(HTTPException) as exc:
        await cp.send_to_counterparty(db, run=run, actor=owner, recipient_email="cp@acme.com",
                                      recipient_name=None, message=None)
    assert exc.value.status_code == 409

    _o2, owner2, _c2, run2, _sr2 = _scenario(db, with_contract=False)
    with pytest.raises(HTTPException) as exc:
        await cp.send_to_counterparty(db, run=run2, actor=owner2, recipient_email="cp@acme.com",
                                      recipient_name=None, message=None)
    assert exc.value.status_code == 409

    _o3, owner3, _c3, run3, _sr3 = _scenario(db)
    with pytest.raises(HTTPException) as exc:
        await cp.send_to_counterparty(db, run=run3, actor=owner3, recipient_email="not-an-email",
                                      recipient_name=None, message=None)
    assert exc.value.status_code == 422


async def test_stranger_without_contract_access_cannot_send(db, sent_mail):
    org, _owner, _c, run, sr = _scenario(db)
    sr.assignee_user_id = None
    stranger = _user(db, org.id, "stranger")
    db.flush()

    with pytest.raises(HTTPException) as exc:
        await cp.send_to_counterparty(db, run=run, actor=stranger, recipient_email="cp@acme.com",
                                      recipient_name=None, message=None)
    assert exc.value.status_code == 404
