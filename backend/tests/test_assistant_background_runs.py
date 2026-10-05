"""Ask Aegis answers are produced by a worker, not by the browser's connection.

Before: the answer was generated inside the HTTP request, so pressing Back or
closing the tab cancelled it — the run was marked interrupted and the chat
showed the question with no reply. Now ``run_assistant_turn`` (a Celery task)
drives the answer to a final state with nobody watching; the browser only
reads its events from Redis; only the Stop button cancels.
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.assistant import run_events, runner
from app.assistant.models import AssistantMessage, AssistantRun, AssistantSession
from app.assistant.service import AssistantService
from app.auth.models import User
from app.core.database import SessionLocal, utcnow
from app.core.enums import AssistantRunStatus, UserStatus

_TAG = "test-assistant-background-runs"


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
def published(monkeypatch):
    """Capture what the worker publishes instead of writing to Redis."""
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(run_events, "publish", lambda run_id, e, p: events.append((run_id, e, p)) or "1-0")
    monkeypatch.setattr(run_events, "cancel_requested", lambda run_id: False)
    monkeypatch.setattr(run_events, "release_claim", lambda run_id: None)
    return events


def _turn(db, user, *, question="What is the notice period?", status=AssistantRunStatus.RUNNING):
    sess = AssistantSession(org_id=user.org_id, title=_TAG, created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(sess)
    db.flush()
    msg = AssistantMessage(org_id=user.org_id, session_id=sess.id, role="user", content=question,
                           created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(msg)
    db.flush()
    run = AssistantRun(org_id=user.org_id, session_id=sess.id, status=status, user_message_id=msg.id,
                       context_manifest={"contract_ids": []},
                       created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(run)
    db.commit()
    return sess, run


class FakeController:
    """Stands in for the AI controller: yields the events it is given."""

    def __init__(self, events=(), *, raise_after=None, on_start=None):
        self.events = list(events)
        self.raise_after = raise_after
        self.on_start = on_start
        self.calls: list[dict] = []

    async def stream_assistant_run(self, db, **kwargs):
        self.calls.append(kwargs)
        if self.on_start:
            self.on_start(db, kwargs)
        for event in self.events:
            yield event
        if self.raise_after is not None:
            raise self.raise_after

    async def resume_assistant_run(self, db, **kwargs):
        self.calls.append(kwargs)
        for event in self.events:
            yield event


def _delta(text):
    return {"event": "message_delta", "payload": {"text": text}}


def _drive(db, run, controller, mode="start"):
    return asyncio.run(runner.drive_run(db, run_id=run.id, mode=mode, controller=controller))


def test_the_answer_is_saved_with_nobody_watching(db, user, published):
    sess, run = _turn(db, user)
    ctrl = FakeController([_delta("Thirty "), _delta("days.")])
    assert _drive(db, run, ctrl) == AssistantRunStatus.SUCCEEDED
    db.refresh(run)
    answer = db.get(AssistantMessage, run.assistant_message_id)
    assert answer.content == "Thirty days." and answer.role == "assistant"
    assert run.completed_at is not None
    # The controller got the stored question and the chat's type.
    assert ctrl.calls[0]["message"] == "What is the notice period?"
    assert ctrl.calls[0]["session_type"] == sess.session_type
    names = [e for _, e, _ in published]
    assert names == ["message_delta", "message_delta", "done"]
    assert published[-1][2]["run_status"] == AssistantRunStatus.SUCCEEDED


def test_stop_keeps_what_was_written_and_ends_cancelled(db, user, published, monkeypatch):
    _, run = _turn(db, user)
    monkeypatch.setattr(runner, "CANCEL_CHECK_SECONDS", 0)
    seen = {"n": 0}

    def cancel_after_two(run_id):
        seen["n"] += 1
        return seen["n"] >= 2

    monkeypatch.setattr(run_events, "cancel_requested", cancel_after_two)
    ctrl = FakeController([_delta("One. "), _delta("Two. "), _delta("Three.")])
    assert _drive(db, run, ctrl) == AssistantRunStatus.CANCELLED
    db.refresh(run)
    answer = db.get(AssistantMessage, run.assistant_message_id)
    assert answer.content == "One. "  # checked before each event: nothing after the Stop
    assert answer.metadata_json.get("cancelled") is True
    assert run.error_message == runner.CANCELLED_MESSAGE
    assert published[-1][1] == "done" and published[-1][2]["run_status"] == AssistantRunStatus.CANCELLED


def test_a_provider_error_fails_the_run_with_a_safe_message(db, user, published):
    _, run = _turn(db, user)
    ctrl = FakeController([_delta("Partial")], raise_after=RuntimeError("401 invalid x-api-key sk-ant-123"))
    assert _drive(db, run, ctrl) == AssistantRunStatus.FAILED
    db.refresh(run)
    assert "sk-ant" not in run.error_message
    # The partial answer is kept, marked as interrupted.
    answer = db.get(AssistantMessage, run.assistant_message_id)
    assert answer.content == "Partial" and answer.metadata_json.get("interrupted") is True
    names = [e for _, e, _ in published]
    assert names[-2:] == ["error", "done"]
    assert "sk-ant" not in published[-2][2]["message"]


def test_a_stop_before_any_text_saves_no_answer(db, user, published, monkeypatch):
    _, run = _turn(db, user)
    monkeypatch.setattr(run_events, "cancel_requested", lambda run_id: True)
    ctrl = FakeController([_delta("Too late.")])
    assert _drive(db, run, ctrl) == AssistantRunStatus.CANCELLED
    db.refresh(run)
    assert run.assistant_message_id is None
    assert [e for _, e, _ in published] == ["done"]


def test_a_stop_never_cancels_a_confirmation_request(db, user, published, monkeypatch):
    _, run = _turn(db, user)
    monkeypatch.setattr(run_events, "cancel_requested", lambda run_id: True)

    def pause(db_, kwargs):
        r = db_.get(AssistantRun, kwargs["assistant_run_id"])
        r.status = AssistantRunStatus.WAITING_CONFIRMATION
        db_.commit()

    ctrl = FakeController(
        [{"event": "confirmation_required", "payload": {"confirmation_id": "c1"}}], on_start=pause
    )
    assert _drive(db, run, ctrl) == AssistantRunStatus.WAITING_CONFIRMATION


def test_a_finished_run_is_never_answered_twice(db, user, published):
    _, run = _turn(db, user, status=AssistantRunStatus.SUCCEEDED)
    ctrl = FakeController([_delta("again")])
    assert _drive(db, run, ctrl) == AssistantRunStatus.SUCCEEDED
    assert ctrl.calls == [] and published == []


def test_a_confirmation_pauses_the_run_and_ends_the_stream(db, user, published):
    _, run = _turn(db, user)

    def pause(db_, kwargs):
        r = db_.get(AssistantRun, kwargs["assistant_run_id"])
        r.status = AssistantRunStatus.WAITING_CONFIRMATION
        db_.commit()

    ctrl = FakeController(
        [{"event": "confirmation_required", "payload": {"confirmation_id": "c1", "tool_name": "reassign_request"}}],
        on_start=pause,
    )
    assert _drive(db, run, ctrl) == AssistantRunStatus.WAITING_CONFIRMATION
    assert [e for _, e, _ in published] == ["confirmation_required", "done"]
    assert published[-1][2]["run_status"] == AssistantRunStatus.WAITING_CONFIRMATION


def test_runs_whose_worker_died_are_marked_interrupted(db, user, published):
    _, stale = _turn(db, user)
    _, fresh = _turn(db, user)
    stale.updated_at = utcnow() - runner.run_lease() - timedelta(minutes=1)
    db.commit()
    assert runner.sweep_stale_runs(db) >= 1
    db.refresh(stale)
    db.refresh(fresh)
    assert stale.status == AssistantRunStatus.INTERRUPTED
    assert stale.error_message == runner.INTERRUPTED_MESSAGE
    assert fresh.status == AssistantRunStatus.RUNNING
    ends = [(rid, e) for rid, e, _ in published if rid == stale.id]
    assert ends == [(stale.id, "error"), (stale.id, "done")]


def test_a_redelivered_task_does_not_rerun_the_answer(db, user, published, monkeypatch):
    """acks_late redelivers a task whose worker died; its claim is still held, so
    the redelivery marks the run interrupted instead of running tools again."""
    from app.jobs.tasks import run_assistant_turn

    _, run = _turn(db, user)
    monkeypatch.setattr(run_events, "claim", lambda run_id, ttl_seconds: False)
    assert run_assistant_turn.run(run_id=run.id) == "duplicate"
    db.refresh(run)
    assert run.status == AssistantRunStatus.INTERRUPTED
    assert [e for _, e, _ in published] == ["error", "done"]


def test_the_session_api_reports_the_latest_answer(db, user, published):
    sess, run = _turn(db, user)
    summary = AssistantService(db).latest_run_summary(session_id=sess.id, org_id=user.org_id)
    assert summary == {
        "id": run.id,
        "status": AssistantRunStatus.RUNNING,
        "error_message": None,
        "user_message_id": run.user_message_id,
        "has_answer": False,
    }
