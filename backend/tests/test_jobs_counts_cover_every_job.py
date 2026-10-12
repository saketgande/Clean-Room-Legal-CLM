"""The Background Jobs cards must count every job, not just the newest page of them.

The page counted the 100 rows the list endpoint returned (newest first, no paging). When 100 jobs
were cancelled in half a minute the cards read "Succeeded 0, Failed 0" even though older jobs had
succeeded and failed. The server now counts by status itself and the list can be paged and filtered.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import JobStatus
from app.jobs.models import JobRun
from app.jobs.routes import jobs_summary, list_jobs
from app.organizations.models import Organization


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


def _org_with_jobs(db, plan):
    """plan: list of (status, how_many), oldest group first; later groups are newer."""
    org = Organization(id=new_uuid(), name="Jobs Co", slug=f"jobs-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"j-{uuid.uuid4().hex[:6]}@example.com", full_name="Admin",
                hashed_password="h", status="active")
    db.add(user)
    db.flush()
    total = sum(n for _, n in plan)
    clock = utcnow() - timedelta(minutes=total + 5)
    for status, n in plan:
        for _ in range(n):
            clock += timedelta(minutes=1)
            job = JobRun(org_id=org.id, job_type="clause_extraction", resource_type="contract",
                         resource_id=new_uuid(), status=status, created_at=clock,
                         started_at=clock, finished_at=clock)
            db.add(job)
    db.flush()
    return user


def _list(db, user, *, status=None, limit=100, offset=0):
    return list_jobs(job_status=status, limit=limit, offset=offset, db=db, current_user=user)


PLAN = [(JobStatus.SUCCEEDED, 6), (JobStatus.FAILED, 4), (JobStatus.CANCELLED, 120)]


def test_the_summary_counts_every_job_even_when_the_newest_hundred_are_all_cancelled(db):
    user = _org_with_jobs(db, PLAN)

    summary = jobs_summary(db=db, current_user=user)

    assert summary == {"queued": 0, "running": 0, "succeeded": 6, "failed": 4, "cancelled": 120, "total": 130}


def test_the_first_page_alone_would_have_hidden_them(db):
    """This is the page the old cards were counting from."""
    user = _org_with_jobs(db, PLAN)

    first_page = _list(db, user)

    assert len(first_page) == 100
    assert {j.status for j in first_page} == {JobStatus.CANCELLED}


def test_paging_reaches_the_older_jobs_without_repeats_or_gaps(db):
    user = _org_with_jobs(db, PLAN)

    ids = [j.id for j in _list(db, user)] + [j.id for j in _list(db, user, offset=100)]

    assert len(ids) == 130
    assert len(set(ids)) == 130


def test_a_status_filter_lists_only_that_status(db):
    user = _org_with_jobs(db, PLAN)

    failed = _list(db, user, status=JobStatus.FAILED)

    assert len(failed) == 4
    assert {j.status for j in failed} == {JobStatus.FAILED}


def test_the_summary_never_counts_another_organisations_jobs(db):
    user = _org_with_jobs(db, [(JobStatus.SUCCEEDED, 2)])
    _org_with_jobs(db, [(JobStatus.SUCCEEDED, 5), (JobStatus.FAILED, 1)])

    summary = jobs_summary(db=db, current_user=user)

    assert (summary["succeeded"], summary["failed"], summary["total"]) == (2, 0, 2)


def test_an_organisation_with_no_jobs_gets_zeroes(db):
    user = _org_with_jobs(db, [])

    assert jobs_summary(db=db, current_user=user) == {
        "queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0, "total": 0}
