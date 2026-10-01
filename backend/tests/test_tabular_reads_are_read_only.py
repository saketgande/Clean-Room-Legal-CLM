"""API-02: opening tabular reviews never writes. A review's status is settled when its
cells finish and by a periodic sweep, so nothing depends on someone viewing the page."""

import inspect
from datetime import timedelta
from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.core.database import utcnow
from app.core.enums import TabularCellStatus
from app.jobs import tasks
from app.jobs.celery_app import celery_app
from app.tabular_review import routes, service


def _endpoint(path):
    return next(r.endpoint for r in routes.router.routes if r.path == path and "GET" in r.methods)


def test_reading_reviews_does_not_write():
    for path in ("/tabular-reviews", "/tabular-reviews/{review_id}"):
        source = inspect.getsource(_endpoint(path))
        assert "commit(" not in source and "reconcile" not in source, path


class DB:
    def __init__(self, cells):
        self.cells = cells

    def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: self.cells)


def _cell(status):
    return SimpleNamespace(status=status, error_message=None)


def test_a_review_whose_cells_all_finished_is_completed():
    now = utcnow()
    review = SimpleNamespace(id="r-1", org_id="org-1", status="running", updated_at=now, created_at=now)
    cells = [_cell(TabularCellStatus.COMPLETE), _cell(TabularCellStatus.FAILED)]
    assert service.reconcile_review_status(DB(cells), review=review) is True
    assert review.status == "completed"


def test_cells_stuck_past_the_window_are_failed_and_the_review_resolves():
    stale = utcnow() - service.STUCK_REVIEW_TTL - timedelta(minutes=1)
    review = SimpleNamespace(id="r-1", org_id="org-1", status="running", updated_at=stale, created_at=stale)
    stuck = _cell(TabularCellStatus.RUNNING)
    assert service.reconcile_review_status(DB([stuck]), review=review) is True
    assert (stuck.status, review.status) == (TabularCellStatus.FAILED, "failed")


def test_reviews_settle_on_a_schedule_and_when_a_job_finishes():
    assert "app.jobs.tasks.reconcile_tabular_reviews" in {e["task"] for e in celery_app.conf.beat_schedule.values()}
    assert "_settle_review(db, job)" in inspect.getsource(tasks._run_tabular_job)
