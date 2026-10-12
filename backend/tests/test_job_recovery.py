"""JOB-01 / JOB-02: a job whose worker died, or whose enqueue failed, is recovered
instead of sitting in Running or Queued forever."""

from datetime import timedelta
from types import SimpleNamespace

import app.jobs.service as job_service
import app.models  # noqa: F401  (register every mapper)
from app.core.database import utcnow
from app.core.enums import JobStatus, TabularCellStatus
from app.jobs import tasks

NOW = utcnow()


def _job(status, *, started_ago=None, attempts=1, task_id=None, job_type="clause_extraction", name="job"):
    return SimpleNamespace(
        id=name, status=status, job_type=job_type, attempt_count=attempts, celery_task_id=task_id,
        started_at=None if started_ago is None else NOW - started_ago,
        error_message=None, finished_at=None, metadata_json={"cell_id": "cell-1"},
        org_id="org-1", created_at=NOW - timedelta(hours=2),
    )


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, *batches, cells=None):
        self.batches = list(batches)
        self.cells = cells or {}

    def scalars(self, _stmt):
        return _Rows(self.batches.pop(0))

    def get(self, _model, key):
        return self.cells.get(key)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_a_running_job_with_a_live_lease_is_not_run_twice():
    assert tasks._should_run(_job(JobStatus.RUNNING, started_ago=timedelta(minutes=1)), NOW) is False


def test_a_running_job_past_its_lease_is_taken_over():
    late = tasks.JOB_LEASE + timedelta(seconds=1)
    assert tasks._should_run(_job(JobStatus.RUNNING, started_ago=late), NOW) is True


def test_finished_jobs_never_rerun():
    for status in (JobStatus.SUCCEEDED, JobStatus.CANCELLED):
        assert tasks._should_run(_job(status, started_ago=timedelta(hours=5)), NOW) is False


def test_reaper_requeues_abandoned_jobs_and_redispatches_failed_enqueues(monkeypatch):
    abandoned = _job(JobStatus.RUNNING, started_ago=timedelta(hours=1), task_id="dead-task", name="abandoned")
    never_sent = _job(JobStatus.QUEUED, name="never-sent")
    dispatched = []

    def fake_dispatch(db, *, job):
        dispatched.append(job.id)
        job.celery_task_id = "new-task"
        return job

    monkeypatch.setattr(job_service, "dispatch_job", fake_dispatch)
    result = tasks._reclaim_stale_jobs(FakeDB([abandoned], [abandoned, never_sent]), now=NOW)
    assert abandoned.status == JobStatus.QUEUED
    assert dispatched == ["abandoned", "never-sent"]
    assert (result["requeued"], result["redispatched"]) == (1, 2)


def test_reaper_dead_letters_a_job_that_keeps_dying_and_frees_its_cell(monkeypatch):
    monkeypatch.setattr(job_service, "dispatch_job", lambda db, *, job: job)
    cell = SimpleNamespace(id="cell-1", status=TabularCellStatus.RUNNING, error_message=None)
    doomed = _job(JobStatus.RUNNING, started_ago=timedelta(hours=1), attempts=tasks.MAX_JOB_ATTEMPTS,
                  job_type="tabular_cell_extraction")
    # batches: stale jobs, the locked cells, newer jobs for those cells (none), undispatched jobs
    tasks._reclaim_stale_jobs(FakeDB([doomed], [cell], [], []), now=NOW)
    assert doomed.status == JobStatus.FAILED
    assert tasks.DEAD_LETTER_META_KEY in doomed.metadata_json
    assert cell.status == TabularCellStatus.FAILED


def test_the_reaper_is_on_the_beat_schedule():
    from app.jobs.celery_app import celery_app

    tasks_scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert "app.jobs.tasks.reclaim_stale_jobs" in tasks_scheduled
