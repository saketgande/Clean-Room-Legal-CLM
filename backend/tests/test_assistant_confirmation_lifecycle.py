"""An Ask Aegis turn waiting on a confirmation must always end visibly.

Before: rejecting left the run "waiting_confirmation" forever with no reply; an
expired confirmation was marked EXPIRED and then rolled back (get_db rolls back
on the 409 that followed); nothing ever expired confirmations nobody clicked;
and a page reload lost the confirmation card, so the action could never be
approved. Now: every dead confirmation closes its turn with a stored reply, a
periodic sweep catches the rest, and the session API returns the live card.
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.ai import confirmations as conf_mod
from app.ai.models import AIConfirmation
from app.assistant.models import AssistantMessage, AssistantRun, AssistantSession, AssistantToolCall
from app.auth.models import User
from app.core.database import SessionLocal, utcnow
from app.core.enums import AIConfirmationStatus, AssistantRunStatus, AssistantToolCallStatus

_TAG = "test-assistant-confirmation-lifecycle"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for sess in s.scalars(select(AssistantSession).where(AssistantSession.title == _TAG)):
            s.query(AIConfirmation).filter(AIConfirmation.session_id == sess.id).delete()
            run_ids = [r.id for r in s.scalars(select(AssistantRun).where(AssistantRun.session_id == sess.id))]
            s.query(AssistantToolCall).filter(AssistantToolCall.session_id == sess.id).delete()
            for r in s.scalars(select(AssistantRun).where(AssistantRun.id.in_(run_ids))):
                r.assistant_message_id = None
            s.flush()
            s.query(AssistantRun).filter(AssistantRun.session_id == sess.id).delete()
            s.query(AssistantMessage).filter(AssistantMessage.session_id == sess.id).delete()
            s.delete(sess)
        s.commit()
        s.close()


@pytest.fixture
def user(db):
    u = db.scalar(select(User).order_by(User.created_at))
    if u is None:
        pytest.skip("needs a seeded user")
    return u


def _waiting_turn(db, user, *, expires_in_minutes=30, status=AIConfirmationStatus.PENDING):
    sess = AssistantSession(org_id=user.org_id, title=_TAG, created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(sess)
    db.flush()
    run = AssistantRun(org_id=user.org_id, session_id=sess.id, status=AssistantRunStatus.WAITING_CONFIRMATION,
                       created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(run)
    db.flush()
    call = AssistantToolCall(org_id=user.org_id, session_id=sess.id, assistant_run_id=run.id,
                             tool_name="reassign_request", status=AssistantToolCallStatus.CONFIRMATION_REQUIRED,
                             confirmation_required=True, created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(call)
    db.flush()
    conf = AIConfirmation(org_id=user.org_id, session_id=sess.id, assistant_run_id=run.id, tool_call_id=call.id,
                          tool_name="reassign_request", status=status,
                          tool_input={"request_id": "REQ-1", "assignee": "Erin Legal"},
                          expires_at=utcnow() + timedelta(minutes=expires_in_minutes),
                          created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(conf)
    db.commit()
    return sess, run, conf


def _reply(db, run):
    db.refresh(run)
    return db.get(AssistantMessage, run.assistant_message_id) if run.assistant_message_id else None


def test_a_live_confirmation_is_returned_for_the_reloaded_chat(db, user):
    sess, run, conf = _waiting_turn(db, user)
    pc = conf_mod.pending_confirmation_for_session(db, session_id=sess.id, org_id=user.org_id)
    assert pc["confirmation_id"] == conf.id and pc["assistant_run_id"] == run.id
    assert pc["details"] == {"request_id": "REQ-1", "assignee": "Erin Legal"}


def test_an_expired_confirmation_is_not_offered_again(db, user):
    sess, _, _ = _waiting_turn(db, user, expires_in_minutes=-1)
    assert conf_mod.pending_confirmation_for_session(db, session_id=sess.id, org_id=user.org_id) is None


def test_clicking_an_expired_confirmation_persists_the_expiry_and_closes_the_turn(db, user):
    _, run, conf = _waiting_turn(db, user, expires_in_minutes=-1)
    with pytest.raises(HTTPException) as exc:
        conf_mod.confirm_confirmation(db, confirmation_id=conf.id, user=user)
    assert exc.value.status_code == 409
    db.rollback()  # what get_db does on the exception — the expiry must survive it
    db.refresh(conf)
    assert conf.status == AIConfirmationStatus.EXPIRED
    db.refresh(run)
    assert run.status == AssistantRunStatus.CANCELLED
    assert "expired" in _reply(db, run).content


def test_rejecting_closes_the_turn_with_a_visible_reply(db, user):
    _, run, conf = _waiting_turn(db, user)
    conf_mod.reject_confirmation(db, confirmation_id=conf.id, user=user, reason="no")
    assert conf_mod.close_turn_for_confirmation(db, conf, outcome="rejected", actor_user_id=user.id)
    db.commit()
    assert run.status == AssistantRunStatus.CANCELLED
    assert "nothing was changed" in _reply(db, run).content


def test_the_sweep_expires_overdue_confirmations_and_closes_already_rejected_turns(db, user):
    _, run_overdue, conf_overdue = _waiting_turn(db, user, expires_in_minutes=-5)
    _, run_rejected, _ = _waiting_turn(db, user, status=AIConfirmationStatus.REJECTED)
    _, run_live, _ = _waiting_turn(db, user)
    out = conf_mod.sweep_confirmations(db)
    assert out["expired"] >= 1 and out["closed_stuck_runs"] >= 1
    for r in (run_overdue, run_rejected, run_live):
        db.refresh(r)
    db.refresh(conf_overdue)
    assert conf_overdue.status == AIConfirmationStatus.EXPIRED
    assert run_overdue.status == AssistantRunStatus.CANCELLED
    assert run_rejected.status == AssistantRunStatus.CANCELLED
    assert run_live.status == AssistantRunStatus.WAITING_CONFIRMATION  # still decidable: untouched
