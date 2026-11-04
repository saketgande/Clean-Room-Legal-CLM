"""The Retry button's endpoint (POST /obligations/extract, ObligationsService.trigger_extraction): it says why when
it cannot help (409), never stacks a second job on one in flight, restarts the job that ended badly instead of
piling up rows, and is on the audit trail and the contract's timeline. Real rows in one rolled-back transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.auth.models import User
from app.core.config import settings
from app.core.enums import JobStatus
from app.core.models import AuditLog, ResourceTimelineEvent
from app.jobs import tasks
from app.jobs.models import JobRun
from app.obligations.extraction_audit import LOST_AFTER, extraction_state
from app.obligations.service import ObligationsService
from tests.test_extraction_state import _db_job, _with_text, new_world, rolled_back_session


@pytest.fixture
def world():
    with rolled_back_session() as db:
        yield new_world(db)


@pytest.fixture
def sent(monkeypatch):
    """The Celery dispatch, recorded instead of sent."""
    calls: list[str] = []
    monkeypatch.setattr(tasks.run_ai_job, "delay", lambda job_id: calls.append(job_id) or type("T", (), {"id": f"task-{len(calls)}"})())
    return calls


def _retry(w, user=None):
    return ObligationsService(w.db).trigger_extraction(contract_id=w.contract.id, current_user=user or w.user)


def _jobs(w):
    return list(w.db.scalars(select(JobRun).where(JobRun.resource_id == w.contract.id, JobRun.job_type == "obligation_extraction")))


def _events(w, action):
    return list(w.db.scalars(select(AuditLog).where(AuditLog.resource_id == w.contract.id, AuditLog.action == action)))


def test_a_contract_already_read_for_this_version_answers_409_and_changes_nothing(world, sent):
    """Guards the silent no-op: the old endpoint returned the succeeded job as if it had restarted, so the button
    seemed to work and nothing happened."""
    version = _with_text(world)
    _db_job(world, version, JobStatus.SUCCEEDED, attempt_count=1, key="x:manual")

    with pytest.raises(HTTPException) as refused:
        _retry(world)

    assert refused.value.status_code == 409 and "already read" in refused.value.detail
    assert sent == [] and len(_jobs(world)) == 1 and _events(world, "obligation.extraction_requested") == []


def test_a_contract_with_no_text_answers_409(world, sent):
    with pytest.raises(HTTPException) as refused:
        _retry(world)
    assert refused.value.status_code == 409 and sent == []


def test_a_failed_job_is_restarted_not_duplicated(world, sent):
    """Guards a row per click: pressing Retry on a failed read restarts that job (so its attempts accumulate and the
    state stays one story), clears the old failure, and dispatches it once."""
    version = _with_text(world)
    failed = _db_job(world, version, JobStatus.FAILED, attempt_count=3, finished_at=datetime.now(UTC),
                     error_message="old", meta={"failure": {"reason": "The AI's answer was cut off."}})

    out = _retry(world)

    assert out == {"job_id": failed.id, "status": JobStatus.QUEUED, "started": True}
    assert sent == [failed.id] and len(_jobs(world)) == 1
    job = world.db.get(JobRun, failed.id)
    assert job.error_message is None and "failure" not in job.metadata_json
    assert extraction_state(world.db, world.contract).state == "running"


def test_no_job_yet_creates_one_and_dispatches_it(world, sent):
    version = _with_text(world)
    out = _retry(world)
    [job] = _jobs(world)
    assert out["job_id"] == job.id and out["started"] is True and sent == [job.id]
    assert job.idempotency_key.endswith(":manual") and job.metadata_json["contract_version_id"] == version.id


def test_a_second_click_does_not_stack_another_job_on_one_in_flight(world, sent):
    """Guards double-paying for the same reading: a job already queued or running is returned as it is."""
    version = _with_text(world)
    queued = _db_job(world, version, JobStatus.QUEUED, created_at=datetime.now(UTC), celery_task_id="t1")

    out = _retry(world)

    assert out == {"job_id": queued.id, "status": JobStatus.QUEUED, "started": False}
    assert sent == [] and len(_jobs(world)) == 1 and _events(world, "obligation.extraction_requested") == []


@pytest.mark.parametrize("make", [
    lambda w, v: _db_job(w, v, JobStatus.QUEUED, created_at=datetime.now(UTC) - LOST_AFTER - timedelta(minutes=5),
                         updated_at=datetime.now(UTC) - LOST_AFTER - timedelta(minutes=5), celery_task_id="lost"),
    lambda w, v: _db_job(w, v, JobStatus.RUNNING, created_at=datetime.now(UTC) - timedelta(days=1),
                         started_at=datetime.now(UTC) - timedelta(days=1)),
    lambda w, v: _db_job(w, v, JobStatus.CANCELLED, finished_at=datetime.now(UTC),
                         started_at=datetime.now(UTC) - timedelta(days=1)),
], ids=["lost in the queue", "worker died", "cancelled"])
def test_a_job_that_will_never_finish_by_itself_is_sent_again(world, sent, make):
    """Guards a Retry that silently did nothing because dispatch_job leaves a queued job with a Celery id, a running
    job and a cancelled job alone: each is made restartable first."""
    version = _with_text(world)
    stuck = make(world, version)

    out = _retry(world)

    assert out["job_id"] == stuck.id and out["started"] is True and sent == [stuck.id]
    assert world.db.get(JobRun, stuck.id).status == JobStatus.QUEUED


def test_a_job_made_hours_ago_and_restarted_now_reads_as_running_not_lost(world, sent):
    """Guards the page showing 'failed' again the moment Retry is pressed: a restart re-queues the SAME row, whose
    created_at may be days old, so its queue age must count from when it was queued now. Without that the red row and
    its Retry button stayed, a second click sent it again, and a second audit row was written."""
    version = _with_text(world)
    long_ago = datetime.now(UTC) - timedelta(days=2)
    failed = _db_job(world, version, JobStatus.FAILED, attempt_count=3, created_at=long_ago, updated_at=long_ago,
                     started_at=long_ago, finished_at=long_ago)

    first = _retry(world)

    assert first["started"] is True and extraction_state(world.db, world.contract).state == "running"
    second = _retry(world)  # a second click while it waits for a worker
    assert second == {"job_id": failed.id, "status": JobStatus.QUEUED, "started": False}
    assert sent == [failed.id] and len(_events(world, "obligation.extraction_requested")) == 1


def test_the_first_press_names_the_job_it_started_in_the_audit_row(world, sent):
    """Guards the immutable audit row recording job_id null: the id is assigned at flush, so the job must be flushed
    before the row is written."""
    _with_text(world)
    out = _retry(world)
    [audit] = _events(world, "obligation.extraction_requested")
    assert audit.after["job_id"] == out["job_id"] and audit.after["restarted_existing"] is False


def test_a_job_cancelled_a_moment_ago_may_still_be_running_so_it_is_not_restarted(world, sent):
    """Guards two workers on one reading: cancelling only flips the status, and a worker that had started may still
    be finishing. Nothing is sent, and the refusal says why."""
    version = _with_text(world)
    _db_job(world, version, JobStatus.CANCELLED, started_at=datetime.now(UTC) - timedelta(minutes=1))

    with pytest.raises(HTTPException) as refused:
        _retry(world)

    assert refused.value.status_code == 409 and "may still be finishing" in refused.value.detail and sent == []


def test_a_press_is_on_the_audit_trail_and_the_contracts_timeline(world, sent):
    """Guards the constitution's rule for mutations: who restarted the reading, for which version."""
    version = _with_text(world)
    _db_job(world, version, JobStatus.FAILED, attempt_count=3, finished_at=datetime.now(UTC))

    _retry(world)

    [audit] = _events(world, "obligation.extraction_requested")
    assert audit.actor_user_id == world.user.id and audit.after["contract_version_id"] == version.id
    assert audit.after["restarted_existing"] is True
    [event] = world.db.scalars(select(ResourceTimelineEvent).where(
        ResourceTimelineEvent.resource_id == world.contract.id,
        ResourceTimelineEvent.event_type == "contract.obligations_extraction_requested"))
    assert event.title == "Obligation extraction restarted"


def test_someone_who_cannot_edit_the_contract_cannot_start_it(world, sent, monkeypatch):
    """Guards the button being a way around the contract's own access rule: a colleague with no access to this
    contract is refused, and nothing is queued."""
    monkeypatch.setattr(settings, "disable_rbac", False)
    _with_text(world)
    stranger = User(org_id=world.org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Stranger",
                    hashed_password="hash", status="active")
    world.db.add(stranger)
    world.db.flush()

    with pytest.raises(HTTPException) as refused:
        _retry(world, stranger)

    assert refused.value.status_code in (403, 404) and sent == [] and _jobs(world) == []
