"""Try again on an answer that never wrote anything must not store the question a second time.

"Try again" sent the same text as a brand-new message, so the chat showed (and stored) two identical
questions. The send now carries the run being retried, and the server reuses that run's question.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

import app.models  # noqa: F401  (registers every mapper)
from app.assistant.models import AssistantMessage, AssistantRun, AssistantSession
from app.assistant.routes import AssistantStreamRequest, create_turn, question_to_retry
from app.assistant.service import AssistantService
from app.auth.models import User
from app.core.database import SessionLocal, utcnow
from app.core.enums import AssistantRunStatus, UserStatus

_TAG = "test-try-again-reuses-the-question"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for sess in s.scalars(select(AssistantSession).where(AssistantSession.title == _TAG)):
            for r in s.scalars(select(AssistantRun).where(AssistantRun.session_id == sess.id)):
                r.assistant_message_id = None
                r.user_message_id = None
            s.flush()
            s.query(AssistantRun).filter(AssistantRun.session_id == sess.id).delete()
            s.query(AssistantMessage).filter(AssistantMessage.session_id == sess.id).delete()
            s.delete(sess)
        s.commit()
        s.close()


@pytest.fixture
def user(db):
    u = db.scalar(select(User).where(User.status == UserStatus.ACTIVE).order_by(User.created_at))
    if u is None:
        pytest.skip("needs a seeded user")
    return u


@pytest.fixture
def session(db, user):
    s = AssistantSession(org_id=user.org_id, title=_TAG, created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(s)
    db.commit()
    return s


QUESTION = "yes go ahead"


def _failed_turn(db, user, session, *, status=AssistantRunStatus.INTERRUPTED, text=QUESTION, answered=False):
    msg = AssistantMessage(org_id=user.org_id, session_id=session.id, role="user", content=text,
                           created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(msg)
    db.flush()
    run = AssistantRun(org_id=user.org_id, session_id=session.id, status=status, user_message_id=msg.id,
                       context_manifest={"contract_ids": []}, completed_at=utcnow(),
                       created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(run)
    db.flush()
    if answered:
        answer = AssistantMessage(org_id=user.org_id, session_id=session.id, role="assistant", content="Half an",
                                  created_by_user_id=user.id, updated_by_user_id=user.id)
        db.add(answer)
        db.flush()
        run.assistant_message_id = answer.id
    db.commit()
    return msg, run


def _send(db, user, session, *, retry_run_id=None, message=QUESTION):
    payload = AssistantStreamRequest(message=message, contract_ids=[], retry_run_id=retry_run_id)
    return create_turn(db, session=session, payload=payload, current_user=user, service=AssistantService(db))


def _questions(db, session):
    return db.scalar(select(func.count()).select_from(AssistantMessage).where(
        AssistantMessage.session_id == session.id, AssistantMessage.role == "user"))


@pytest.mark.parametrize("status", [AssistantRunStatus.FAILED, AssistantRunStatus.INTERRUPTED, AssistantRunStatus.CANCELLED])
def test_try_again_after_an_answer_that_wrote_nothing_reuses_the_question(db, user, session, status):
    msg, failed = _failed_turn(db, user, session, status=status)

    new_run = _send(db, user, session, retry_run_id=failed.id)

    assert new_run.id != failed.id
    assert new_run.user_message_id == msg.id
    assert new_run.status == AssistantRunStatus.RUNNING
    assert _questions(db, session) == 1


def test_a_plain_send_still_stores_a_new_message(db, user, session):
    _failed_turn(db, user, session)

    new_run = _send(db, user, session)

    assert _questions(db, session) == 2
    assert db.get(AssistantMessage, new_run.user_message_id).content == QUESTION


def test_an_answer_that_was_cut_off_keeps_its_partial_text_so_the_question_is_asked_again_below_it(db, user, session):
    _, cut_off = _failed_turn(db, user, session, answered=True)

    _send(db, user, session, retry_run_id=cut_off.id)

    assert _questions(db, session) == 2


def test_the_question_is_not_reused_when_the_text_was_changed(db, user, session):
    _, failed = _failed_turn(db, user, session)

    _send(db, user, session, retry_run_id=failed.id, message="no, do something else")

    assert _questions(db, session) == 2


def test_only_the_latest_answer_can_be_retried(db, user, session):
    _, old = _failed_turn(db, user, session)
    _failed_turn(db, user, session, text="a later question")

    _send(db, user, session, retry_run_id=old.id)

    assert _questions(db, session) == 3


def test_an_answer_that_succeeded_is_not_retried_this_way(db, user, session):
    _, done = _failed_turn(db, user, session, status=AssistantRunStatus.SUCCEEDED)

    assert question_to_retry(db, session=session, payload=AssistantStreamRequest(message=QUESTION, retry_run_id=done.id)) is None


def test_another_chats_run_cannot_be_used(db, user, session):
    other = AssistantSession(org_id=user.org_id, title=_TAG, created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(other)
    db.commit()
    _, foreign = _failed_turn(db, user, other)

    assert question_to_retry(db, session=session, payload=AssistantStreamRequest(message=QUESTION, retry_run_id=foreign.id)) is None


def test_an_unknown_run_id_just_sends_normally(db, user, session):
    _send(db, user, session, retry_run_id="does-not-exist")

    assert _questions(db, session) == 1
