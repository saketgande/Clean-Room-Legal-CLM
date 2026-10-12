"""An AI review that stops part-way is told as interrupted, and can be run again (contracts/auto_review.py).

The bug this exists to fix: the automatic AI review runs inside a background job. When that job's worker
stopped (a restart while testing), nothing said so. The contract stayed flagged "review pending"; the
workflow's AI review step waited ten minutes and then asked a person with "never reviewed"; the page said
"no playbook review has run yet"; and the only recovery was the system's own reclaim, twenty minutes later.

What must hold:
* a review is "interrupted" when its job failed, when it has no job, or when it has not finished within
  ten minutes; a young review is "running"; nothing pending is None; the newest job decides;
* a clause scan that finished before the review stopped does not make the contract "AI reviewed";
* a pending flag left behind by a review that did finish reads as nothing pending;
* "Retry review" needs the right to redline and to run playbooks, starts exactly one job however many
  times it is pressed, takes the review over from the older job, and says so when a review already exists
  for this text or no playbook covers it;
* the retry job runs the same review the automatic one does, fails honestly when the review did not run,
  and a job that was handed the review to someone else skips it;
* the workflow's AI review step says "interrupted" (not "never reviewed"), keeps waiting for a retry that
  has started, and "Retry review" on it sets the step going again.

Runs the real routes and database (the seeded admin / viewer users). Only what would reach a worker or an
AI provider is replaced.
"""

from __future__ import annotations

import asyncio
import io
from datetime import timedelta
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import app.contract_files.service as files_service
import app.contracts.risk as risk_module
import app.models  # noqa: F401  (register every mapper)
import app.workflows.service as workflow_service
from app.auth.models import User
from app.contracts import auto_review
from app.contracts.models import Contract
from app.core.database import SessionLocal, engine, utcnow
from app.core.enums import JobStatus
from app.core.models import ResourceTimelineEvent
from app.jobs import tasks
from app.jobs.models import JobRun
from app.jobs.service import JobsService

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PASSWORD = "local-dev-password"
_TOKENS: dict[str, str] = {}
MINUTE = 60


def _word(text: str) -> bytes:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def world():
    """One contract made through the real upload route, and the people to act on it. One, because the
    upload and login routes are rate limited."""
    from app.main import app

    patch = pytest.MonkeyPatch()
    patch.setattr(files_service, "dispatch_job", lambda db, *, job: job)
    client = TestClient(app)

    def login(email: str) -> dict[str, str]:
        if email not in _TOKENS:
            res = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            if res.status_code != 200:
                pytest.skip(f"cannot log in as seeded user {email} ({res.status_code})")
            _TOKENS[email] = res.json()["access_token"]
        return {"authorization": "Bearer " + _TOKENS[email]}

    headers = login("admin@example.com")
    up = client.post(
        "/api/v1/contracts/upload",
        headers=headers,
        data={"title": "Interrupted review test"},
        files={"file": ("t.docx", _word("1. Term. One year.\n2. Payment. Net 60."), DOCX)},
    )
    assert up.status_code in (200, 201, 202), up.text
    cid = up.json()["contract"]["id"]
    saved = client.put(f"/api/v1/contracts/{cid}/text", headers=headers, json={"text": "1. Term. One year.\n2. Payment. Net 60."})
    assert saved.status_code == 200, saved.text
    db = SessionLocal()
    try:
        contract = db.get(Contract, cid)
        users = {
            email: db.scalar(select(User).where(User.email == email, User.org_id == contract.org_id))
            for email in ("admin@example.com", "user1@example.com")
        }
        ns = SimpleNamespace(
            client=client, headers=headers, login=login, cid=cid, org_id=contract.org_id,
            admin_id=users["admin@example.com"].id, viewer_id=users["user1@example.com"].id,
        )
    finally:
        db.close()
    try:
        yield ns
    finally:
        _clean(cid)
        db = SessionLocal()
        try:
            for job in db.scalars(select(JobRun).where(JobRun.resource_id == cid, JobRun.status == "queued")):
                job.status = "cancelled"
            db.get(Contract, cid).deleted_at = utcnow()
            db.commit()
        finally:
            db.close()
        patch.undo()


def _clean(cid: str) -> None:
    """Remove what a test committed through the routes, and put the contract's flags back."""
    db = SessionLocal()
    try:
        job_ids = db.scalars(select(JobRun.id).where(JobRun.resource_id == cid)).all()
        if job_ids:
            db.execute(delete(ResourceTimelineEvent).where(ResourceTimelineEvent.job_id.in_(job_ids)))
            db.execute(delete(JobRun).where(JobRun.id.in_(job_ids)))
        contract = db.get(Contract, cid)
        contract.metadata_json = {
            k: v for k, v in (contract.metadata_json or {}).items() if k not in ("auto_review_pending", "auto_review_attempt", "auto_review_reason")
        }
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db():
    """A real session whose commits are savepoints inside one outer transaction, so a test can commit
    and roll back like production code does and still leave nothing behind."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _no_review_yet(monkeypatch):
    """Unless a test says otherwise: a playbook covers the contract, and no review of this text exists."""
    monkeypatch.setattr(risk_module, "contract_risk", lambda _db, _c: {"playbook_id": "pb-1", "playbook_run_ids": []})


# ---- builders ----------------------------------------------------------------------------------------


def flagged(db: Session, w, *, updated_ago: int = 0, **extra) -> Contract:
    """The shared contract, flagged for an automatic review, with no jobs of its own."""
    db.execute(delete(JobRun).where(JobRun.resource_id == w.cid))
    contract = db.get(Contract, w.cid)
    contract.metadata_json = {**(contract.metadata_json or {}), "auto_review_pending": True, **extra}
    contract.updated_at = utcnow() - timedelta(seconds=updated_ago)
    db.flush()
    return contract


def add_job(db: Session, w, *, status="running", job_type="clause_extraction", age=0, started=None, finished=None) -> JobRun:
    now = utcnow()
    job = JobRun(
        org_id=w.org_id, job_type=job_type, resource_type="contract", resource_id=w.cid, status=status,
        created_at=now - timedelta(seconds=age),
        started_at=None if started is None else now - timedelta(seconds=started),
        finished_at=None if finished is None else now - timedelta(seconds=finished),
        metadata_json={},
    )
    db.add(job)
    db.flush()
    return job


# ---- is the review still going, or did it stop? --------------------------------------------------------


def test_nothing_pending_reads_as_nothing(db, world):
    contract = flagged(db, world)
    contract.metadata_json = {k: v for k, v in contract.metadata_json.items() if k != "auto_review_pending"}
    db.flush()
    assert auto_review.review_state(db, contract) is None


def test_a_review_that_started_a_minute_ago_is_running(db, world):
    contract = flagged(db, world)
    job = add_job(db, world, status="running", age=MINUTE, started=MINUTE)
    state = auto_review.review_state(db, contract)
    assert state["state"] == "running" and state["job_id"] == job.id and state["reason"] is None


def test_a_queued_job_counts_from_when_it_was_queued(db, world):
    contract = flagged(db, world)
    add_job(db, world, status="queued", age=11 * MINUTE)
    assert auto_review.review_state(db, contract)["state"] == "interrupted"


def test_a_job_still_running_after_ten_minutes_is_taken_as_stopped(db, world):
    contract = flagged(db, world)
    job = add_job(db, world, status="running", age=11 * MINUTE, started=11 * MINUTE)
    state = auto_review.review_state(db, contract)
    assert (state["state"], state["reason"], state["job_id"]) == ("interrupted", "stalled", job.id)


def test_the_boundary_is_ten_minutes(db, world):
    contract = flagged(db, world)
    add_job(db, world, status="running", age=9 * MINUTE + 50, started=9 * MINUTE + 50)
    assert auto_review.review_state(db, contract)["state"] == "running"


def test_a_failed_job_is_interrupted_at_once(db, world):
    contract = flagged(db, world)
    job = add_job(db, world, status="failed", age=30, finished=10)
    state = auto_review.review_state(db, contract)
    assert (state["state"], state["reason"], state["job_id"]) == ("interrupted", "failed", job.id)


def test_a_cancelled_job_is_interrupted_at_once(db, world):
    contract = flagged(db, world)
    add_job(db, world, status="cancelled", age=30, finished=10)
    assert auto_review.review_state(db, contract)["state"] == "interrupted"


def test_a_clause_job_that_finished_and_then_stopped_inside_the_review_is_caught(db, world):
    """Clause extraction marks itself done BEFORE it runs the review, so a worker lost during the review
    leaves a succeeded job and a flag nothing will ever clear: no reclaim touches a finished job."""
    contract = flagged(db, world)
    add_job(db, world, status="succeeded", age=12 * MINUTE, started=12 * MINUTE, finished=11 * MINUTE)
    state = auto_review.review_state(db, contract)
    assert (state["state"], state["reason"]) == ("interrupted", "stalled")


def test_a_succeeded_job_a_minute_ago_is_the_review_in_progress(db, world):
    contract = flagged(db, world)
    add_job(db, world, status="succeeded", age=2 * MINUTE, started=2 * MINUTE, finished=MINUTE)
    assert auto_review.review_state(db, contract)["state"] == "running"


def test_the_newest_job_decides(db, world):
    contract = flagged(db, world)
    add_job(db, world, status="failed", age=20 * MINUTE, finished=19 * MINUTE)
    newer = add_job(db, world, job_type="contract_review", status="running", age=MINUTE, started=MINUTE)
    state = auto_review.review_state(db, contract)
    assert state["state"] == "running" and state["job_id"] == newer.id


def test_other_kinds_of_job_are_not_the_review(db, world):
    contract = flagged(db, world, updated_ago=11 * MINUTE)
    add_job(db, world, job_type="metadata_extraction", status="running", age=MINUTE, started=MINUTE)
    assert auto_review.review_state(db, contract)["reason"] == "no_job"


def test_a_flag_with_no_job_is_given_the_same_ten_minutes_to_get_one(db, world):
    young = flagged(db, world, updated_ago=MINUTE)
    assert auto_review.review_state(db, young)["state"] == "running"
    old = flagged(db, world, updated_ago=11 * MINUTE)
    state = auto_review.review_state(db, old)
    assert (state["state"], state["reason"], state["job_id"]) == ("interrupted", "no_job", None)


def test_a_flag_left_behind_by_a_review_that_finished_is_not_a_pending_review(db, world, monkeypatch):
    """The worker saved the review and stopped before it cleared the flag: there is nothing to retry."""
    contract = flagged(db, world)
    add_job(db, world, status="failed", age=30, finished=10)
    monkeypatch.setattr(risk_module, "contract_risk", lambda _db, _c: {"playbook_id": "pb-1", "playbook_run_ids": ["run-1"]})
    assert auto_review.review_state(db, contract) is None


def test_the_review_status_page_does_not_call_a_clause_scan_a_review(db, world):
    """The clause job's scan is saved before the review runs. With the review stopped, "AI review: done" and
    "send it for review" were wrong: no playbook review ran."""
    from app.contract_brain.models import ClauseExtraction
    from app.contract_files.models import ContractVersion
    from app.contracts.service import ContractService

    contract = flagged(db, world)
    add_job(db, world, status="failed", age=30, finished=10)
    version = db.get(ContractVersion, contract.current_authoritative_version_id)
    db.add(ClauseExtraction(
        org_id=world.org_id, contract_id=world.cid, contract_version_id=version.id,
        text_snapshot_id=version.text_snapshot_id, clause_type="term", text="One year.",
    ))
    db.flush()
    status = ContractService(db).compute_review_status(contract=contract)
    assert status["auto_review"]["state"] == "interrupted"
    assert status["ai_reviewed"] is False
    assert status["next_action"] == "run_ai"
    item = next(i for i in status["checklist"] if i["key"] == "ai_review")
    assert item["status"] == "todo" and "interrupted" in item["detail"]


def test_the_review_status_reports_a_review_in_progress(db, world):
    from app.contracts.service import ContractService

    contract = flagged(db, world)
    job = add_job(db, world, status="running", age=MINUTE, started=MINUTE)
    status = ContractService(db).compute_review_status(contract=contract)
    assert status["auto_review"]["state"] == "running" and status["auto_review"]["job_id"] == job.id


def test_the_review_status_has_no_auto_review_when_nothing_is_pending(db, world):
    from app.contracts.service import ContractService

    contract = flagged(db, world)
    contract.metadata_json = {k: v for k, v in contract.metadata_json.items() if k != "auto_review_pending"}
    db.flush()
    assert ContractService(db).compute_review_status(contract=contract)["auto_review"] is None


def test_the_workflow_waits_as_long_as_the_review_does():
    assert workflow_service._REVIEW_WAIT_SECONDS == auto_review.STALL_SECONDS


# ---- running it again ---------------------------------------------------------------------------------


@pytest.fixture
def dispatched(monkeypatch):
    """Where the jobs would have gone to a worker."""
    seen: list[str] = []
    monkeypatch.setattr(JobsService, "dispatch_job", lambda self, *, job: seen.append(job.id) or job)
    return seen


def person(db: Session, user_id: str) -> User:
    return db.get(User, user_id)


def test_a_person_who_cannot_redline_cannot_restart_the_review(db, world, dispatched):
    contract = flagged(db, world)
    add_job(db, world, status="failed", age=30, finished=10)
    with pytest.raises(HTTPException) as caught:
        auto_review.retry_review(db, contract=contract, actor=person(db, world.viewer_id))
    assert caught.value.status_code == 403
    assert dispatched == []


def test_retrying_an_interrupted_review_starts_one_job_that_owns_the_review(db, world, dispatched):
    contract = flagged(db, world)
    old = add_job(db, world, status="running", age=12 * MINUTE, started=12 * MINUTE)
    admin = person(db, world.admin_id)

    result = auto_review.retry_review(db, contract=contract, actor=admin)

    assert result["status"] == "queued"
    job = db.get(JobRun, result["job_id"])
    assert (job.job_type, job.resource_id, job.created_by_user_id, job.status) == ("contract_review", world.cid, admin.id, "queued")
    assert dispatched == [job.id]
    meta = db.get(Contract, world.cid).metadata_json
    assert meta["auto_review_pending"] is True and meta["auto_review_attempt"] == job.id
    assert db.get(JobRun, old.id).status == "running"  # the old job is left alone; the reclaim settles it
    event = db.scalar(select(ResourceTimelineEvent).where(ResourceTimelineEvent.job_id == job.id))
    assert event.event_type == "contract.auto_review_retried" and event.created_by_user_id == admin.id


def test_pressing_retry_twice_does_not_start_a_second_review(db, world, dispatched):
    contract = flagged(db, world)
    add_job(db, world, status="failed", age=30, finished=10)
    admin = person(db, world.admin_id)
    first = auto_review.retry_review(db, contract=contract, actor=admin)
    second = auto_review.retry_review(db, contract=contract, actor=admin)
    assert first["status"] == "queued"
    assert second == {"status": "already_running", "job_id": first["job_id"]}
    assert len(dispatched) == 1
    assert db.scalar(select(JobRun.id).where(JobRun.resource_id == world.cid, JobRun.job_type == "contract_review", JobRun.id != first["job_id"])) is None


def test_retry_can_follow_a_retry_that_also_stopped(db, world, dispatched):
    contract = flagged(db, world, updated_ago=11 * MINUTE)
    admin = person(db, world.admin_id)
    first = auto_review.retry_review(db, contract=contract, actor=admin)
    db.get(JobRun, first["job_id"]).status = JobStatus.FAILED
    db.flush()
    second = auto_review.retry_review(db, contract=contract, actor=admin)
    assert second["status"] == "queued" and second["job_id"] != first["job_id"]
    assert db.get(Contract, world.cid).metadata_json["auto_review_attempt"] == second["job_id"]


def test_retry_after_a_review_that_ended_badly_runs_a_new_one(db, world, dispatched):
    """No flag any more (the job cleared it when its review failed): the page still offers Retry."""
    contract = flagged(db, world)
    contract.metadata_json = {k: v for k, v in contract.metadata_json.items() if k != "auto_review_pending"}
    db.flush()
    result = auto_review.retry_review(db, contract=contract, actor=person(db, world.admin_id))
    assert result["status"] == "queued"
    assert db.get(Contract, world.cid).metadata_json["auto_review_pending"] is True


def test_retry_does_not_pay_for_a_second_review_of_the_same_text(db, world, dispatched, monkeypatch):
    contract = flagged(db, world, auto_review_attempt="old-job")
    add_job(db, world, status="failed", age=30, finished=10)
    monkeypatch.setattr(risk_module, "contract_risk", lambda _db, _c: {"playbook_id": "pb-1", "playbook_run_ids": ["run-1"]})
    result = auto_review.retry_review(db, contract=contract, actor=person(db, world.admin_id))
    assert result == {"status": "not_needed", "job_id": None}
    assert dispatched == []
    assert "auto_review_pending" not in db.get(Contract, world.cid).metadata_json  # the stale flag is cleared
    assert "auto_review_attempt" not in db.get(Contract, world.cid).metadata_json


def test_retry_says_so_when_no_playbook_covers_the_contract(db, world, dispatched, monkeypatch):
    contract = flagged(db, world, updated_ago=11 * MINUTE)
    monkeypatch.setattr(risk_module, "contract_risk", lambda _db, _c: {"playbook_id": None, "playbook_run_ids": []})
    result = auto_review.retry_review(db, contract=contract, actor=person(db, world.admin_id))
    assert result == {"status": "no_playbook", "job_id": None}
    assert dispatched == []


def test_a_broker_problem_leaves_the_job_queued_not_lost(db, world, monkeypatch):
    contract = flagged(db, world, updated_ago=11 * MINUTE)

    def down(self, *, job):
        raise ConnectionError("broker is down")

    monkeypatch.setattr(JobsService, "dispatch_job", down)
    result = auto_review.retry_review(db, contract=contract, actor=person(db, world.admin_id))
    assert result["status"] == "queued"
    job = db.get(JobRun, result["job_id"])
    assert job.status == "queued" and job.celery_task_id is None  # the periodic reclaim hands it to a worker


# ---- the routes ---------------------------------------------------------------------------------------


def _set_flag(cid: str, *, job: dict | None = None, **extra) -> None:
    db = SessionLocal()
    try:
        db.execute(delete(JobRun).where(JobRun.resource_id == cid))
        contract = db.get(Contract, cid)
        contract.metadata_json = {**(contract.metadata_json or {}), "auto_review_pending": True, **extra}
        if job:
            now = utcnow()
            db.add(JobRun(
                org_id=contract.org_id, job_type="clause_extraction", resource_type="contract", resource_id=cid,
                status=job["status"], created_at=now - timedelta(seconds=job["age"]),
                started_at=now - timedelta(seconds=job["age"]), metadata_json={},
            ))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def clean_after(world):
    yield
    _clean(world.cid)


def test_the_review_status_route_tells_a_stopped_review(world, clean_after):
    _set_flag(world.cid, job={"status": "running", "age": 15 * MINUTE})
    res = world.client.get(f"/api/v1/contracts/{world.cid}/review-status", headers=world.headers)
    assert res.status_code == 200, res.text
    body = res.json()["auto_review"]
    assert body["state"] == "interrupted" and body["reason"] == "stalled" and body["job_id"]


def test_the_retry_route_needs_the_redline_permission(world, clean_after):
    _set_flag(world.cid, job={"status": "failed", "age": 60})
    res = world.client.post(f"/api/v1/contracts/{world.cid}/review/retry", headers=world.login("user1@example.com"))
    assert res.status_code == 403


def test_the_retry_route_starts_the_job_and_a_second_press_returns_it(world, clean_after, dispatched):
    _set_flag(world.cid, job={"status": "running", "age": 15 * MINUTE})
    url = f"/api/v1/contracts/{world.cid}/review/retry"
    first = world.client.post(url, headers=world.headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "queued" and body["job_id"] and body["workflow_resumed"] is False
    again = world.client.post(url, headers=world.headers)
    assert again.json()["status"] == "already_running" and again.json()["job_id"] == body["job_id"]
    assert dispatched == [body["job_id"]]
    after = world.client.get(f"/api/v1/contracts/{world.cid}/review-status", headers=world.headers).json()["auto_review"]
    assert after["state"] == "running" and after["job_id"] == body["job_id"]


# ---- the retry job ---------------------------------------------------------------------------------------


def _review_job(world, **meta):
    db = SessionLocal()
    try:
        job = JobRun(
            org_id=world.org_id, job_type="contract_review", resource_type="contract", resource_id=world.cid,
            created_by_user_id=world.admin_id, status="queued", metadata_json={}, **meta,
        )
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def _run_job(job_id: str, monkeypatch, outcome: str) -> JobRun:
    async def fake(_db, *, job):
        return outcome

    monkeypatch.setattr(tasks, "_maybe_auto_review", fake)
    asyncio.run(tasks._run_ai_job(job_id))
    db = SessionLocal()
    try:
        return db.get(JobRun, job_id)
    finally:
        db.close()


def test_the_retry_job_succeeds_when_the_review_ran(world, clean_after, monkeypatch):
    job = _run_job(_review_job(world), monkeypatch, "ok")
    assert job.status == "succeeded" and job.finished_at is not None and job.progress == 100


def test_the_retry_job_fails_honestly_when_the_review_did_not_run(world, clean_after, monkeypatch):
    job = _run_job(_review_job(world), monkeypatch, "incomplete")
    assert job.status == "failed" and "did not run" in job.error_message


def test_the_retry_job_is_not_treated_as_a_clause_job(world, clean_after, monkeypatch):
    """It must go to the review branch only: no clause skill, no brain ingestion."""
    from app.ai.controller import ai_controller

    async def boom(*_a, **_k):
        raise AssertionError("a retry job must not run a skill")

    monkeypatch.setattr(ai_controller, "run_job_skill", boom)
    job = _run_job(_review_job(world), monkeypatch, "ok")
    assert job.status == "succeeded"


def _auto_review_run(world, monkeypatch, *, job_id: str, attempt: str | None, result: dict):
    """_maybe_auto_review for a job, with the review itself replaced. Returns (outcome, reviews run, metadata)."""
    from app.playbooks.service import PlaybooksService

    ran: list[str] = []

    async def fake_review(self, *, contract, actor_user_id, create_redline):
        ran.append(contract.id)
        return result

    monkeypatch.setattr(PlaybooksService, "auto_review_contract", fake_review)
    _set_flag(world.cid, **({"auto_review_attempt": attempt} if attempt else {}))
    db = SessionLocal()
    try:
        job = SimpleNamespace(
            id=job_id, resource_id=world.cid, created_by_user_id=world.admin_id, metadata_json={}, org_id=world.org_id
        )
        outcome = asyncio.run(tasks._maybe_auto_review(db, job=job))
        meta = dict(db.get(Contract, world.cid).metadata_json or {})
    finally:
        db.close()
    return outcome, ran, meta


def test_a_job_that_was_handed_the_review_to_another_skips_it(world, clean_after, monkeypatch):
    outcome, ran, meta = _auto_review_run(world, monkeypatch, job_id="old-job", attempt="retry-job", result={"ran": True})
    assert outcome == "skipped" and ran == []
    assert meta["auto_review_pending"] is True and meta["auto_review_attempt"] == "retry-job"


def test_the_job_that_owns_the_review_runs_it_and_clears_both_flags(world, clean_after, monkeypatch):
    outcome, ran, meta = _auto_review_run(world, monkeypatch, job_id="retry-job", attempt="retry-job", result={"ran": True})
    assert outcome == "ok" and ran == [world.cid]
    assert "auto_review_pending" not in meta and "auto_review_attempt" not in meta


def test_the_clause_job_runs_the_review_when_nobody_retried(world, clean_after, monkeypatch):
    outcome, ran, _ = _auto_review_run(world, monkeypatch, job_id="clause-job", attempt=None, result={"ran": True})
    assert outcome == "ok" and ran == [world.cid]


def test_a_review_that_did_not_run_is_reported_incomplete_and_the_flags_are_cleared(world, clean_after, monkeypatch):
    outcome, _, meta = _auto_review_run(
        world, monkeypatch, job_id="retry-job", attempt="retry-job", result={"ran": False, "error": "no published playbook"}
    )
    assert outcome == "incomplete"
    assert "auto_review_pending" not in meta and "auto_review_attempt" not in meta


# ---- the re-check after accepted changes ---------------------------------------------------------------------


def _recheck_run(world, monkeypatch, *, reason: str | None, attempt: str | None = None):
    """_maybe_auto_review with the review replaced. Returns (what create_redline was asked for, metadata, newest timeline title)."""
    from app.playbooks.service import PlaybooksService

    asked: list[bool] = []

    async def fake_review(self, *, contract, actor_user_id, create_redline):
        asked.append(create_redline)
        return {"ran": True}

    monkeypatch.setattr(PlaybooksService, "auto_review_contract", fake_review)
    extra = {"auto_review_reason": reason} if reason else {}
    if attempt:
        extra["auto_review_attempt"] = attempt
    _set_flag(world.cid, **extra)
    db = SessionLocal()
    try:
        job = SimpleNamespace(
            id=attempt or "clause-job", resource_id=world.cid, created_by_user_id=world.admin_id, metadata_json={}, org_id=world.org_id
        )
        asyncio.run(tasks._maybe_auto_review(db, job=job))
        meta = dict(db.get(Contract, world.cid).metadata_json or {})
        title = db.scalar(
            select(ResourceTimelineEvent.title)
            .where(ResourceTimelineEvent.resource_id == world.cid, ResourceTimelineEvent.event_type == "contract.auto_review")
            .order_by(ResourceTimelineEvent.created_at.desc())
            .limit(1)
        )
        db.execute(delete(ResourceTimelineEvent).where(
            ResourceTimelineEvent.resource_id == world.cid, ResourceTimelineEvent.event_type == "contract.auto_review"))
        db.commit()
    finally:
        db.close()
    return asked, meta, title


def test_the_re_check_after_accepted_changes_proposes_no_changes_of_its_own(world, clean_after, monkeypatch):
    """Accepting changes makes new text, so it is checked again (or the risk band would sit at unknown
    and block approvals). But the check must not also write a new round of redlines and AI fixes for the
    person to decide: that kept the Review tab from ever emptying."""
    asked, meta, title = _recheck_run(world, monkeypatch, reason="accepted_edits")
    assert asked == [False]
    assert "auto_review_pending" not in meta and "auto_review_reason" not in meta
    assert "your changes" in title and "no changes were drafted" in title


def test_a_draft_or_a_counterparty_revision_still_gets_its_redlines(world, clean_after, monkeypatch):
    """Their first review is what fills the Review tab with suggested changes; that stays."""
    asked, _, title = _recheck_run(world, monkeypatch, reason=None)
    assert asked == [True]
    assert "your changes" not in title


def test_a_retry_of_the_re_check_is_still_a_re_check(world, clean_after, monkeypatch):
    asked, meta, _ = _recheck_run(world, monkeypatch, reason="accepted_edits", attempt="retry-job")
    assert asked == [False]
    assert "auto_review_reason" not in meta and "auto_review_attempt" not in meta


def test_the_review_state_says_when_it_follows_accepted_changes(db, world):
    contract = flagged(db, world, auto_review_reason="accepted_edits")
    add_job(db, world, status="running", started=30)
    assert auto_review.review_state(db, contract)["after"] == "accepted_edits"


def test_the_review_state_of_an_ordinary_review_has_no_reason(db, world):
    contract = flagged(db, world)
    add_job(db, world, status="running", started=30)
    assert auto_review.review_state(db, contract)["after"] is None


def test_asking_for_a_review_replaces_a_stale_reason(db, world):
    """A revision arriving while an accepted-changes re-check is still flagged must not inherit its reason."""
    contract = flagged(db, world, auto_review_reason="accepted_edits")
    auto_review.request_review(contract)
    assert contract.metadata_json["auto_review_pending"] is True
    assert "auto_review_reason" not in contract.metadata_json
    auto_review.request_review(contract, after=auto_review.AFTER_ACCEPT)
    assert contract.metadata_json["auto_review_reason"] == "accepted_edits"


def test_the_review_status_route_carries_the_reason(world, clean_after):
    _set_flag(world.cid, job={"status": "running", "age": 30}, auto_review_reason="accepted_edits")
    body = world.client.get(f"/api/v1/contracts/{world.cid}/review-status", headers=world.headers).json()["auto_review"]
    assert body["state"] == "running" and body["after"] == "accepted_edits"


# ---- the workflow's AI review step -------------------------------------------------------------------------


def _step(monkeypatch, review, *, waited=11 * MINUTE, flagged_=True, band="unknown", note="Never reviewed against the NDA playbook."):
    contract = SimpleNamespace(id="c-1", metadata_json={"auto_review_pending": True} if flagged_ else {})
    monkeypatch.setattr(workflow_service, "_get_contract", lambda _db, _cid: contract)
    monkeypatch.setattr(
        risk_module, "contract_risk",
        lambda _db, _c: {"band": band, "counts": {"critical": 0, "high": 0, "medium": 0, "low": 0}, "drivers": [], "note": note},
    )
    monkeypatch.setattr(workflow_service, "_review_state_or_none", lambda _db, _c: review)
    step = {"type": "ai_task", "name": "AI review",
            "config": {"agent": "contract_review_agent", "escalate_below_confidence": 0.6}}
    sr = SimpleNamespace(status="running", note=None, result=None, updated_at=utcnow() - timedelta(seconds=waited), assignee_user_id=None)
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id="c-1", flow_name="Review")
    fake_db = SimpleNamespace(get=lambda *_: None, commit=lambda: None)
    outcome = asyncio.run(workflow_service._execute_step(fake_db, run=run, step=step, sr=sr, actor=SimpleNamespace(id="u-1")))
    return outcome, sr


def test_the_step_says_interrupted_not_never_reviewed(monkeypatch):
    outcome, sr = _step(monkeypatch, {"state": "interrupted", "reason": "stalled", "since": None, "job_id": "j-1"})
    assert outcome == "wait" and sr.status == "waiting_human"
    assert sr.result["review_state"] == "interrupted"
    assert "didn't finish" in sr.note and "Never reviewed" not in sr.note
    assert sr.result["confidence"] is None  # a person still decides


def test_the_step_keeps_waiting_for_a_retry_that_has_started(monkeypatch):
    outcome, sr = _step(monkeypatch, {"state": "running", "reason": None, "since": None, "job_id": "j-2"})
    assert outcome == "yield" and sr.status == "running" and sr.result is None


def test_a_leftover_flag_does_not_change_what_the_step_says(monkeypatch):
    """The review did finish (the flag is residue): the step judges the band as it always did."""
    outcome, sr = _step(monkeypatch, None, band="low", note=None)
    assert outcome == "advance" and "review_state" not in sr.result


def test_an_unknown_band_with_nothing_pending_still_says_never_reviewed(monkeypatch):
    outcome, sr = _step(monkeypatch, None, flagged_=False)
    assert outcome == "wait" and "Never reviewed" in sr.note and "review_state" not in sr.result


def test_a_failure_to_tell_leaves_the_step_asking_a_person_as_before(monkeypatch):
    def broken(_db, _contract):
        raise RuntimeError("database went away")

    monkeypatch.setattr(auto_review, "review_state", broken)
    contract = SimpleNamespace(id="c-1", metadata_json={"auto_review_pending": True})
    assert workflow_service._review_state_or_none(None, contract) is None


# ---- "Retry review" on the step ---------------------------------------------------------------------------


def _waiting_run(result, *, status="waiting_human", step_type="ai_task", agent="contract_review_agent"):
    sr = SimpleNamespace(
        idx=0, status=status, result=result, note="x", assignee_user_id="u-9", step_name="AI review", step_type=step_type
    )
    run = SimpleNamespace(
        id="run-1", org_id="org-1", request_id="req-1", contract_id="c-1", status="waiting", current_index=0, error=None,
        steps=[{"type": step_type, "name": "AI review", "config": {"agent": agent}}],
    )
    return run, sr


def test_only_an_interrupted_review_step_can_be_retried(monkeypatch):
    for run, sr in (
        _waiting_run({"review_state": "interrupted"}),
        _waiting_run({"risk_band": "unknown"}),
        _waiting_run({"review_state": "interrupted"}, status="done"),
        _waiting_run({"review_state": "interrupted"}, step_type="human_task", agent=""),
        _waiting_run({"review_state": "interrupted"}, agent="litigation_agent"),
    ):
        monkeypatch.setattr(workflow_service, "_sr_at", lambda _db, _run, _idx, sr=sr: sr)
        found = workflow_service._interrupted_review_step(None, run)
        expected = sr.result.get("review_state") == "interrupted" and sr.status == "waiting_human" \
            and run.steps[0]["type"] == "ai_task" and run.steps[0]["config"]["agent"] == "contract_review_agent"
        assert (found is sr) is expected
    run, sr = _waiting_run({"review_state": "interrupted"})
    run.status = "running"
    monkeypatch.setattr(workflow_service, "_sr_at", lambda _db, _run, _idx: sr)
    assert workflow_service._interrupted_review_step(None, run) is None


def test_retrying_the_step_runs_the_review_and_sets_the_step_going(monkeypatch):
    run, sr = _waiting_run({"review_state": "interrupted", "risk_band": "unknown"})
    calls: list = []

    async def advance(_db, *, run, actor):
        calls.append(("advance", run.status, sr.status))
        return run

    monkeypatch.setattr(workflow_service, "_sr_at", lambda _db, _run, _idx: sr)
    monkeypatch.setattr(workflow_service, "_require_step_actor", lambda _db, *, sr, actor: None)
    monkeypatch.setattr(workflow_service, "_get_contract", lambda _db, _cid: SimpleNamespace(id="c-1"))
    monkeypatch.setattr(workflow_service, "advance_run", advance)
    monkeypatch.setattr(workflow_service, "write_timeline_event", lambda *_a, **k: calls.append(("timeline", k["event_type"])))

    def retry(_db, *, contract, actor):
        calls.append(("retry", contract.id))
        sr.status = "waiting_human"  # unchanged by the retry itself
        return {"status": "queued", "job_id": "j-1"}

    monkeypatch.setattr(auto_review, "retry_review", retry)
    asyncio.run(workflow_service.retry_ai_review(None, run=run, actor=SimpleNamespace(id="u-1")))

    assert calls == [("retry", "c-1"), ("timeline", "flow.review_retried"), ("advance", "running", "pending")]
    assert sr.result is None and sr.note is None and run.error is None
    assert sr.assignee_user_id == "u-9"  # whoever was asked stays on the step


def test_retrying_a_step_that_is_not_interrupted_is_refused(monkeypatch):
    run, sr = _waiting_run({"risk_band": "unknown"})
    monkeypatch.setattr(workflow_service, "_sr_at", lambda _db, _run, _idx: sr)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(workflow_service.retry_ai_review(None, run=run, actor=SimpleNamespace(id="u-1")))
    assert caught.value.status_code == 409


def test_retrying_the_step_with_no_playbook_says_why_and_leaves_the_step(monkeypatch):
    run, sr = _waiting_run({"review_state": "interrupted"})
    monkeypatch.setattr(workflow_service, "_sr_at", lambda _db, _run, _idx: sr)
    monkeypatch.setattr(workflow_service, "_require_step_actor", lambda _db, *, sr, actor: None)
    monkeypatch.setattr(workflow_service, "_get_contract", lambda _db, _cid: SimpleNamespace(id="c-1"))
    monkeypatch.setattr(auto_review, "retry_review", lambda _db, *, contract, actor: {"status": "no_playbook", "job_id": None})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(workflow_service.retry_ai_review(None, run=run, actor=SimpleNamespace(id="u-1")))
    assert caught.value.status_code == 409 and "playbook" in caught.value.detail
    assert sr.status == "waiting_human" and sr.result == {"review_state": "interrupted"}


def test_someone_who_may_not_act_on_the_step_cannot_retry_it(monkeypatch):
    run, sr = _waiting_run({"review_state": "interrupted"})
    monkeypatch.setattr(workflow_service, "_sr_at", lambda _db, _run, _idx: sr)

    def refuse(_db, *, sr, actor):
        raise HTTPException(403, "Only this step's assignee or its team can act on it")

    monkeypatch.setattr(workflow_service, "_require_step_actor", refuse)
    monkeypatch.setattr(auto_review, "retry_review", lambda *_a, **_k: pytest.fail("must not retry"))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(workflow_service.retry_ai_review(None, run=run, actor=SimpleNamespace(id="u-1")))
    assert caught.value.status_code == 403

