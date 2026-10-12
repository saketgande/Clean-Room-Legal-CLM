"""Stop must end an answer even when the worker that was producing it is gone.

Pressing Stop only sets a flag that the worker running the answer reads. When that worker had died
(for instance a restart of the backend while it was answering) nothing read the flag: the chat said
"Working…" until a background sweep gave up on it about 20 minutes later, and Stop did nothing.

Workers now send a heartbeat. Stop ends the run at once when no worker is beating for it, and the
sweep notices a lost worker within a couple of minutes.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.assistant import run_events, runner
from app.assistant.models import AssistantMessage, AssistantRun, AssistantSession
from app.assistant.routes import cancel_run
from app.assistant.service import AssistantService
from app.auth.models import User
from app.core.database import SessionLocal, utcnow
from app.core.enums import AssistantRunStatus, UserStatus

_TAG = "test-stop-ends-a-run-with-no-worker"


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


class FakeRedis:
    """Just enough of Redis for the heartbeat, claim and cancel keys."""

    def __init__(self):
        self.keys: dict[str, str] = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    def exists(self, key):
        return int(key in self.keys)

    def delete(self, *keys):
        for k in keys:
            self.keys.pop(k, None)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(run_events, "_redis", lambda: fake)
    return fake


@pytest.fixture
def published(monkeypatch):
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(run_events, "publish", lambda run_id, e, p: events.append((run_id, e, p)) or "1-0")
    return events


def _run(db, user, *, age_minutes=0):
    sess = AssistantSession(org_id=user.org_id, title=_TAG, created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(sess)
    db.flush()
    msg = AssistantMessage(org_id=user.org_id, session_id=sess.id, role="user", content="hello",
                           created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(msg)
    db.flush()
    run = AssistantRun(org_id=user.org_id, session_id=sess.id, status=AssistantRunStatus.RUNNING,
                       user_message_id=msg.id, context_manifest={"contract_ids": []},
                       created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(run)
    db.commit()
    if age_minutes:
        run.updated_at = utcnow() - timedelta(minutes=age_minutes)
        db.commit()
    return run


def _stop(db, user, run):
    return cancel_run(assistant_run_id=run.id, db=db, current_user=user, service=AssistantService(db))


# --- what a worker's key says -------------------------------------------------

def test_a_beating_worker_is_alive(redis):
    redis.set(run_events.claim_key("r1"), "1")
    run_events.mark_alive("r1")
    assert run_events.worker_state("r1") == "alive"


def test_a_worker_that_claimed_the_run_and_stopped_beating_is_lost(redis):
    redis.set(run_events.claim_key("r1"), "1")
    assert run_events.worker_state("r1") == "lost"


def test_a_run_nobody_has_claimed_is_unstarted(redis):
    assert run_events.worker_state("r1") == "unstarted"


def test_when_redis_cannot_say_the_worker_is_treated_as_alive(monkeypatch):
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(run_events, "_redis", broken)
    assert run_events.worker_state("r1") == "unknown"


def test_the_heartbeat_marks_the_run_alive_while_the_block_runs_and_clears_it_after(redis):
    with run_events.heartbeat("r1", every=0.01):
        assert run_events.worker_state("r1") == "alive"
    assert run_events.alive_key("r1") not in redis.keys


def test_the_heartbeat_keeps_beating_from_its_own_thread(redis):
    import time

    with run_events.heartbeat("r1", every=0.01):
        redis.delete(run_events.alive_key("r1"))
        time.sleep(0.1)
        assert run_events.alive_key("r1") in redis.keys


# --- Stop ---------------------------------------------------------------------

def test_stop_ends_a_run_whose_worker_died(db, user, redis, published):
    run = _run(db, user)
    redis.set(run_events.claim_key(run.id), "1")  # claimed, but no heartbeat any more

    result = _stop(db, user, run)

    db.refresh(run)
    assert result["run_status"] == AssistantRunStatus.CANCELLED
    assert run.status == AssistantRunStatus.CANCELLED
    assert run.error_message == runner.CANCELLED_MESSAGE
    assert run.completed_at is not None
    assert [e for _, e, _ in published] == ["done"]
    assert published[-1][2]["run_status"] == AssistantRunStatus.CANCELLED
    assert run_events.claim_key(run.id) not in redis.keys


def test_stop_ends_an_answer_that_never_started(db, user, redis, published):
    run = _run(db, user)

    assert _stop(db, user, run)["run_status"] == AssistantRunStatus.CANCELLED


def test_stop_leaves_a_live_worker_to_stop_itself(db, user, redis, published):
    run = _run(db, user)
    redis.set(run_events.claim_key(run.id), "1")
    run_events.mark_alive(run.id)

    result = _stop(db, user, run)

    db.refresh(run)
    assert result["cancel_requested"] is True
    assert run.status == AssistantRunStatus.RUNNING  # the worker saves what it has and ends it
    assert run_events.cancel_key(run.id) in redis.keys
    assert published == []


def test_stop_never_ends_a_run_when_redis_cannot_say_whether_a_worker_is_alive(db, user, monkeypatch, published):
    run = _run(db, user)
    monkeypatch.setattr(run_events, "request_cancel", lambda run_id: True)
    monkeypatch.setattr(run_events, "worker_state", lambda run_id: "unknown")

    _stop(db, user, run)

    db.refresh(run)
    assert run.status == AssistantRunStatus.RUNNING


def test_stop_on_a_finished_run_changes_nothing(db, user, redis, published):
    run = _run(db, user)
    run.status = AssistantRunStatus.SUCCEEDED
    db.commit()

    result = _stop(db, user, run)

    assert result == {"assistant_run_id": run.id, "run_status": AssistantRunStatus.SUCCEEDED, "cancel_requested": False}


def test_stopping_twice_does_not_finish_the_run_twice(db, user, redis, published):
    run = _run(db, user)

    assert runner.stop_run_without_worker(db, run.id) is True
    assert runner.stop_run_without_worker(db, run.id) is False
    assert [e for _, e, _ in published] == ["done"]


def test_a_late_worker_does_not_answer_a_run_that_was_stopped(db, user, redis, published):
    """The worker that finally picks up a queued, already-stopped answer must not run it."""
    run = _run(db, user)
    runner.stop_run_without_worker(db, run.id)

    class NeverCalled:
        def stream_assistant_run(self, *a, **k):  # pragma: no cover - failing is the point
            raise AssertionError("the stopped answer was produced")

    status = asyncio.run(runner.drive_run(db, run_id=run.id, mode="start", controller=NeverCalled()))

    assert status == AssistantRunStatus.CANCELLED


# --- the sweep ----------------------------------------------------------------

def test_the_sweep_ends_a_lost_worker_within_minutes_not_twenty(db, user, redis, published):
    lost = _run(db, user, age_minutes=3)
    redis.set(run_events.claim_key(lost.id), "1")
    alive = _run(db, user, age_minutes=3)
    redis.set(run_events.claim_key(alive.id), "1")
    run_events.mark_alive(alive.id)
    queued = _run(db, user, age_minutes=3)  # never claimed: still waiting for a worker

    runner.sweep_stale_runs(db)

    for r in (lost, alive, queued):
        db.refresh(r)
    assert lost.status == AssistantRunStatus.INTERRUPTED
    assert lost.error_message == runner.INTERRUPTED_MESSAGE
    assert alive.status == AssistantRunStatus.RUNNING
    assert queued.status == AssistantRunStatus.RUNNING


def test_the_sweep_gives_a_just_started_run_a_grace_period(db, user, redis, published):
    fresh = _run(db, user, age_minutes=0)
    redis.set(run_events.claim_key(fresh.id), "1")  # claimed a moment ago, first beat not yet written

    runner.sweep_stale_runs(db)

    db.refresh(fresh)
    assert fresh.status == AssistantRunStatus.RUNNING


def test_the_sweep_never_ends_a_run_on_a_redis_outage(db, user, monkeypatch, published):
    run = _run(db, user, age_minutes=5)
    monkeypatch.setattr(run_events, "worker_state", lambda run_id: "unknown")

    runner.sweep_stale_runs(db)

    db.refresh(run)
    assert run.status == AssistantRunStatus.RUNNING
