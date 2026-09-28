"""The OFAC list has to be refreshed by something.

`refresh_ofac` existed and worked, but its only caller was a manual admin
button — so the list aged past `screening.STALE_AFTER` and every screen
returned "unavailable". Safe (an unscreened name is never reported "clear")
and completely invisible: sanctions screening was off while looking like it
was on.
"""

from datetime import timedelta

import pytest

from app.core.database import utcnow
from app.jobs import tasks
from app.jobs.celery_app import celery_app

TASK_PATH = "app.jobs.tasks.refresh_sanctions_lists"


# --- it is actually scheduled ----------------------------------------------


def test_the_task_is_registered_with_celery():
    """Guards a beat entry pointing at a task name that does not exist — beat
    logs the miss and moves on, so the list would age exactly as before."""
    assert TASK_PATH in celery_app.tasks


def test_beat_runs_it_daily():
    entry = celery_app.conf.beat_schedule["refresh-sanctions-lists"]
    assert entry["task"] == TASK_PATH
    schedule = entry["schedule"]
    # Daily, and comfortably inside STALE_AFTER so the list never ages out.
    assert schedule.hour == {6}
    assert schedule.minute == {0}
    assert schedule.day_of_week == set(range(7))


def test_it_runs_far_more_often_than_the_list_goes_stale():
    """A refresh cadence longer than STALE_AFTER would let the list expire
    between runs, which is the state this task exists to prevent."""
    from app.intake.screening import STALE_AFTER

    assert timedelta(days=1) < STALE_AFTER


# --- what it does -----------------------------------------------------------


class _FakeDB:
    def __init__(self, org_id, newest):
        self._returns = [org_id, newest]
        self.rolled_back = False

    def scalar(self, _stmt):
        return self._returns.pop(0) if self._returns else None

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    holder = {}

    def factory():
        return holder["db"]

    monkeypatch.setattr(tasks, "SessionLocal", factory)
    return holder


def test_a_stale_list_is_refreshed(monkeypatch, fake_db):
    fake_db["db"] = _FakeDB("org-1", utcnow() - timedelta(days=20))
    monkeypatch.setattr(
        "app.intake.screening.refresh_ofac",
        lambda db, org_id: {"source": "OFAC_SDN", "added": 5, "updated": 19000},
    )

    result = tasks.refresh_sanctions_lists()
    assert result["status"] == "refreshed"
    assert result["updated"] == 19000


def test_a_fresh_list_is_not_re_downloaded(monkeypatch, fake_db):
    """Beat keeps its last-run state in a schedule file a container restart
    loses, so a crash-looping beat would otherwise re-fetch the ~5 MB Treasury
    file on every boot."""
    fake_db["db"] = _FakeDB("org-1", utcnow() - timedelta(hours=2))

    def must_not_run(db, org_id):
        pytest.fail("a fresh list must not be re-downloaded")

    monkeypatch.setattr("app.intake.screening.refresh_ofac", must_not_run)

    assert tasks.refresh_sanctions_lists()["status"] == "fresh"


def test_a_failed_download_is_reported_not_raised(monkeypatch, fake_db):
    """A beat task that throws disappears into the worker log. The operator
    needs the list's age, because a stale list means screening is off."""
    db = _FakeDB("org-1", utcnow() - timedelta(days=45))
    fake_db["db"] = db

    def treasury_is_down(db, org_id):
        raise ConnectionError("treasury.gov unreachable")

    monkeypatch.setattr("app.intake.screening.refresh_ofac", treasury_is_down)

    result = tasks.refresh_sanctions_lists()
    assert result["status"] == "failed"
    assert "45d old" in result["list_age"]
    assert db.rolled_back


def test_it_skips_cleanly_before_setup(monkeypatch, fake_db):
    """A fresh install has no completed organisation; the task must no-op
    rather than error on every beat tick."""
    fake_db["db"] = _FakeDB(None, None)
    monkeypatch.setattr(
        "app.intake.screening.refresh_ofac",
        lambda db, org_id: pytest.fail("nothing to refresh before setup"),
    )

    assert tasks.refresh_sanctions_lists()["status"] == "skipped"


def test_it_refreshes_once_not_once_per_org():
    """Found by running it: iterating organisations trips
    `ux_sanctions_source_ref`, which is unique on (source, source_ref) with NO
    org_id — the list is public reference data, so a second copy cannot exist.
    `auth.service` picks "the org" the same way."""
    import inspect

    source = inspect.getsource(tasks.refresh_sanctions_lists)
    assert "setup_complete" in source
    assert "for org_id in" not in source


# --- end to end -------------------------------------------------------------


def test_screening_works_against_the_refreshed_list():
    """The point of the whole thing: a real designated entity is found with no
    freshness gate relaxed. Skips when the deployment's list has not been
    refreshed yet."""
    from sqlalchemy import select

    import app.models  # noqa: F401  (register every mapper)
    from app.core.database import SessionLocal
    from app.intake.models import SanctionsListEntry
    from app.intake.screening import screen_sanctions
    from app.organizations.models import Organization

    db = SessionLocal()
    try:
        org = db.scalar(select(Organization.id).where(Organization.setup_complete.is_(True)))
        designated = "JOINT STOCK COMPANY PLASMA"
        if org is None or db.scalar(
            select(SanctionsListEntry.id).where(SanctionsListEntry.name == designated)
        ) is None:
            pytest.skip("no refreshed sanctions list in this database")

        result = screen_sanctions(db, org, designated)
        if result["status"] == "unavailable":
            pytest.skip("list present but stale — run refresh_sanctions_lists first")
        assert result["status"] == "hit"
        assert result["matches"][0]["name"] == designated
    finally:
        db.close()


def test_a_word_boundary_embargo_hit_is_not_resurrected_by_a_live_list():
    """"Miranda Holdings" legitimately surfaces as a *list* candidate (there is
    a real SDNTK entry "LA FIRMA MIRANDA, S.A. DE C.V."). It must never come
    back as an *embargo* match on `iran`, which is a hard block rather than
    something for a reviewer to weigh."""
    from sqlalchemy import select

    import app.models  # noqa: F401
    from app.core.database import SessionLocal
    from app.intake.screening import screen_sanctions
    from app.organizations.models import Organization

    db = SessionLocal()
    try:
        org = db.scalar(select(Organization.id).where(Organization.setup_complete.is_(True)))
        if org is None:
            pytest.skip("no organization set up")
        result = screen_sanctions(db, org, "Miranda Holdings Ltd")
        assert not any(m["kind"] == "embargo" for m in result.get("matches") or [])
    finally:
        db.close()


def test_the_manual_admin_route_still_exists():
    """The scheduled task supplements the button; an operator needs to be able
    to refresh on demand after an OFAC action."""
    import inspect

    from app.intake import routes

    assert "refresh_ofac" in inspect.getsource(routes)
