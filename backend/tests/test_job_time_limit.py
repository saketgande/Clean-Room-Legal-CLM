"""A job that runs out of time says so, and an AI run a dead worker left "running" is closed (app/jobs/tasks.py).

Celery's soft time limit raises SoftTimeLimitExceeded inside the running task a few minutes before the hard kill. The
handler writes a plain failure that no machine re-runs (a 12-minute job would be paid again) and closes the job's AI
runs; the reclaim sweep closes any AI run left "running" past the lease, whichever job it belonged to. Real rows in one
rolled-back transaction; the worker's own session is pointed at it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.models import AISkillRun
from app.ai.outcomes import failure_from_exception
from app.assistant.models import AssistantRun, AssistantSession
from app.core import retry_policy
from app.core.database import engine
from app.core.enums import AISkillRunStatus, AIValidationStatus, JobStatus
from app.jobs import tasks
from app.jobs.models import JobRun
from tests.test_extraction_state import new_world, rolled_back_session

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def world(monkeypatch):
    with rolled_back_session() as db:
        # `_mark_job_soft_limit` opens (and commits and closes) a session of its own: give it one on this transaction.
        monkeypatch.setattr(tasks, "SessionLocal", lambda: Session(bind=db.get_bind(), expire_on_commit=False))
        yield new_world(db)


def _job(w, job_type="clause_extraction", status=JobStatus.RUNNING):
    job = JobRun(org_id=w.org.id, job_type=job_type, resource_type="contract", resource_id=w.contract.id,
                 idempotency_key=uuid.uuid4().hex, status=status, started_at=NOW, attempt_count=1, metadata_json={})
    w.db.add(job)
    w.db.flush()
    return job


def _run(w, *, job=None, status=AISkillRunStatus.RUNNING, started=None, assistant_run=None):
    run = AISkillRun(
        org_id=w.org.id, skill_name="s", skill_version="1", execution_mode="job", status=status,
        job_id=job.id if job else None, assistant_run_id=assistant_run, prompt_key="p", prompt_version="1",
        prompt_hash="0" * 64, model="m", input_payload={}, started_at=started)
    w.db.add(run)
    w.db.flush()
    return run


def _reload(w, *rows):
    w.db.expire_all()
    return rows


# --- what the failure says -----------------------------------------------------------------------------------------


def test_running_out_of_time_is_a_plain_failure_a_person_may_retry_and_no_machine_will():
    """Guards "Something went wrong inside Aegis" (what an unknown exception reads as) for the one failure whose cause
    is known, and a machine re-running a job that already used its 12 minutes."""
    failure = failure_from_exception(SoftTimeLimitExceeded())

    assert (failure["outcome"], failure["retryable"], failure["auto_retry"]) == ("transient", True, False)
    assert "took longer than the time allowed" in failure["reason"]
    assert "busy" not in failure["reason"] and "service" not in failure["reason"]


# --- the handler -----------------------------------------------------------------------------------------------------


def test_a_job_that_ran_out_of_time_is_failed_with_the_plain_reason_and_its_runs_are_closed(world):
    job = _job(world)
    mine = _run(world, job=job)
    finished = _run(world, job=job, status=AISkillRunStatus.SUCCEEDED)
    someone_elses = _run(world, job=_job(world))

    tasks._mark_job_soft_limit(job.id)

    _reload(world)
    assert job.status == JobStatus.FAILED and job.finished_at is not None
    assert job.metadata_json["failure"]["auto_retry"] is False
    assert job.error_message == job.metadata_json["failure"]["reason"] and "time allowed" in job.error_message
    assert (mine.status, mine.validation_status) == (AISkillRunStatus.FAILED, AIValidationStatus.INVALID)
    assert mine.error_message == job.error_message and mine.finished_at is not None
    assert finished.status == AISkillRunStatus.SUCCEEDED  # an answer that finished is left alone
    assert someone_elses.status == AISkillRunStatus.RUNNING  # another job's run is not this handler's business
    assert tasks.DEAD_LETTER_META_KEY not in job.metadata_json


def test_a_template_check_is_left_for_its_rerun_but_its_runs_are_closed(world):
    """Guards taking away the only recovery a template check has: after the lease reclaim re-runs it, the re-run ends
    the check as "interrupted" (no new paid review) and starts the next. Marking the job failed here would leave the
    check "running" for ever, and that template could never be checked again."""
    job = _job(world, "template_check")
    run = _run(world, job=job)

    tasks._mark_job_soft_limit(job.id)

    _reload(world)
    assert job.status == JobStatus.RUNNING and "failure" not in job.metadata_json
    assert run.status == AISkillRunStatus.FAILED


@pytest.mark.parametrize("job_type", ["tabular_cell_extraction", "tabular_row_extraction"])
def test_a_tabular_job_that_ran_out_of_time_is_failed_with_its_cells_not_left_to_be_paid_for_again(
        world, monkeypatch, job_type):
    """Guards the contradiction the review found: left RUNNING, reclaim re-queues it after the lease and the whole
    extraction is paid again (up to three runs), for a job that already used its time."""
    freed = []
    monkeypatch.setattr(tasks, "_fail_stuck_cells", lambda db, job: freed.append(job.id))
    job = _job(world, job_type)

    tasks._mark_job_soft_limit(job.id)

    _reload(world)
    assert job.status == JobStatus.FAILED and job.metadata_json["failure"]["auto_retry"] is False
    assert freed == [job.id]


def test_a_job_already_failed_keeps_its_own_failure_when_the_limit_lands_later(world):
    """Guards the handler overwriting a more specific failure the run itself wrote just before."""
    job = _job(world, status=JobStatus.FAILED)
    job.metadata_json = {"failure": {"outcome": "refused", "reason": "The AI declined to answer for this document."}}
    world.db.flush()
    run = _run(world, job=job)

    tasks._mark_job_soft_limit(job.id)

    _reload(world)
    assert job.metadata_json["failure"]["outcome"] == "refused" and run.status == AISkillRunStatus.FAILED


def test_a_job_that_is_gone_is_not_an_error(world):
    tasks._mark_job_soft_limit(str(uuid.uuid4()))  # must not raise: it runs while the worker is being stopped


def test_the_task_records_the_time_limit_and_still_raises_it(monkeypatch):
    """Guards the handler swallowing the exception: Celery must still see the task as stopped."""
    seen = []

    async def runs_out_of_time(job_id):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks, "_run_ai_job", runs_out_of_time)
    monkeypatch.setattr(tasks, "_mark_job_soft_limit", seen.append)

    with pytest.raises(SoftTimeLimitExceeded):
        tasks.run_ai_job.run("job-1")

    assert seen == ["job-1"]


def test_a_job_runs_patiently_whoever_starts_it_and_puts_the_flag_back(monkeypatch):
    """Guards a job's model calls giving up after two tries: the flag is set where the job runs, not in the Celery
    task, because the exam and the fixture seeder call `_run_ai_job` directly and must run the policy production runs."""
    seen = []

    class Gone:
        def scalar(self, _stmt):
            seen.append(retry_policy.patient.get())  # the first thing the job does is look itself up; finds nothing

        def rollback(self):
            pass

        def get(self, *_a):
            return None

        def close(self):
            pass

    monkeypatch.setattr(tasks, "SessionLocal", Gone)

    async def as_the_exam_calls_it():
        # Awaited from the caller's own task, as the exam does: a flag left set would leak into everything it runs next.
        with pytest.raises(RuntimeError, match="Job not found"):
            await tasks._run_ai_job("missing")
        return retry_policy.patient.get()

    assert asyncio.run(as_the_exam_calls_it()) is False
    assert seen == [True]


def test_a_transport_error_is_still_left_to_the_old_celery_retry_and_dead_lettered_at_the_end(monkeypatch):
    """Guards the old retry (OCR, e-signature) surviving the new handler: the order of the except clauses decides it."""
    import httpx

    async def down(job_id):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(tasks, "_run_ai_job", down)
    dead = []
    monkeypatch.setattr(tasks, "_mark_job_dead_letter", lambda job_id, *, reason: dead.append((job_id, reason)))
    monkeypatch.setattr(tasks, "_mark_job_soft_limit", lambda job_id: pytest.fail("not a time limit"))
    task = tasks.run_ai_job
    monkeypatch.setattr(type(task.request), "retries", 3, raising=False)

    with pytest.raises(httpx.ConnectError):
        task.run("job-9")

    assert dead == [("job-9", "max_retries_exhausted")]


# --- the reclaim sweep ---------------------------------------------------------------------------------------------


def test_an_ai_run_left_running_past_the_lease_is_closed_whatever_job_it_belonged_to(world):
    """Guards the 7 orphan runs found on dev: the longest call in a clause job (the playbook review) is tied to no job,
    so a sweep that matched on the job missed it, and the contract page waited on a run no worker would ever finish."""
    old = NOW - tasks.JOB_LEASE - timedelta(minutes=1)
    unlinked = _run(world, started=old)
    linked = _run(world, job=_job(world), started=old)
    never_started = _run(world, started=None)
    never_started.created_at = old
    recent = _run(world, started=NOW - timedelta(minutes=5))
    done = _run(world, status=AISkillRunStatus.SUCCEEDED, started=old)
    queued = _run(world, status=AISkillRunStatus.QUEUED, started=old)

    closed = tasks._close_orphan_skill_runs(world.db, now=NOW)

    _reload(world)
    assert closed == 3
    for run in (unlinked, linked, never_started):
        assert (run.status, run.error_message) == (AISkillRunStatus.FAILED, "The worker stopped before finishing.")
    assert recent.status == AISkillRunStatus.RUNNING  # a worker may still be on it
    assert done.status == AISkillRunStatus.SUCCEEDED and queued.status == AISkillRunStatus.QUEUED


@pytest.mark.parametrize("turn_status, closed", [
    ("running", False), ("waiting_confirmation", False),  # a resumed turn reuses the run row and its old start time
    ("interrupted", True), ("failed", True), ("cancelled", True), ("succeeded", True),
])
def test_a_chat_answers_ai_run_is_closed_only_once_that_answer_has_ended(world, turn_status, closed):
    """Guards two mistakes: closing the run of an answer that is still going (or waiting on a confirmation), and never
    closing the run of one a dead worker left behind. The assistant's own sweep marks the dead answer INTERRUPTED but
    never touches its AI run (dev held four such runs from October)."""
    session = AssistantSession(org_id=world.org.id, created_by_user_id=world.user.id)
    world.db.add(session)
    world.db.flush()
    turn = AssistantRun(org_id=world.org.id, session_id=session.id, status=turn_status)
    world.db.add(turn)
    world.db.flush()
    run = _run(world, started=NOW - tasks.JOB_LEASE - timedelta(hours=1), assistant_run=turn.id)

    assert tasks._close_orphan_skill_runs(world.db, now=NOW) == (1 if closed else 0)

    _reload(world)
    assert run.status == (AISkillRunStatus.FAILED if closed else AISkillRunStatus.RUNNING)


def test_the_reclaim_task_runs_the_sweep_and_reports_it(monkeypatch):
    """Guards the sweep existing but never being called: it is one step of what the beat task does."""
    calls = []
    monkeypatch.setattr(tasks, "_close_orphan_skill_runs", lambda db, *, now: calls.append(now) or 4)

    class Empty:
        def scalars(self, _stmt):
            class Rows:
                def all(self):
                    return []

            return Rows()

        def commit(self):
            pass

    result = tasks._reclaim_stale_jobs(Empty(), now=NOW)

    assert calls == [NOW] and result["skill_runs_closed"] == 4


def test_the_test_database_is_the_one_in_use():
    """Guards a mis-pointed run: these tests write rows (rolled back) and must never see the dev database."""
    # On CI the service database is its own throwaway and keeps its name (tests/_testdb.py).
    assert (engine.url.database or "").endswith("_test") or os.environ.get("CI") == "true"
