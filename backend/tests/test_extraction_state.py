"""Does this contract have a real obligation extraction for the version it is on? One function (app/obligations/
extraction_audit.py) answers it, so the lifecycle row, the Retry button and anything later cannot disagree. The
decision is pure; the loader is checked against real rows in one rolled-back transaction."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.core.database import engine
from app.core.enums import JobStatus
from app.jobs.models import JobRun
from app.obligations.extraction_audit import (
    LOST_AFTER,
    JobFact,
    _fact,
    dead,
    decide,
    extraction_state,
    version_jobs,
)
from app.obligations.models import Obligation
from app.organizations.models import Organization

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=20)
SECRET = "Supplier shall pay INR 5,000,000 to Acme within 3 days"  # a contract's own words


def _job(status, *, made=NOW - timedelta(minutes=5), started=None, finished=None, attempts=0, reason=None, ident=None):
    return JobFact(ident or str(uuid.uuid4()), status, attempts, made, started, finished, reason)


def _state(jobs, *, live=0, has_text=True):
    return decide(has_text=has_text, jobs=jobs, live_obligations=live, now=NOW, lease=LEASE)


# --- the decision -----------------------------------------------------------------------------------------------


def test_a_contract_nobody_ever_read_is_never_not_done():
    """Guards 'Obligations tracked: 0' or an empty Active row passing as fine when extraction never ran."""
    assert _state([]).state == "never"


def test_a_succeeded_job_with_zero_obligations_is_none_found_not_a_failure():
    """Guards a contract that legitimately has no duties being retried for ever or alarmed on: the signal is a
    succeeded job, not a row count."""
    state = _state([_job(JobStatus.SUCCEEDED, attempts=1)], live=0)
    assert state.state == "none_found" and state.reason is None


def test_a_succeeded_job_with_obligations_is_done():
    state = _state([_job(JobStatus.SUCCEEDED, attempts=1)], live=7)
    assert (state.state, state.live_obligations) == ("done", 7)


def test_a_manual_retry_that_worked_makes_the_contract_done_even_though_the_first_job_failed():
    """Guards the real dev case (aee17d16): the plain job failed, the ':manual' job for the same version succeeded.
    Judging by the first job alone left a good contract red for ever."""
    failed = _job(JobStatus.FAILED, attempts=3, finished=NOW - timedelta(days=1))
    worked = _job(JobStatus.SUCCEEDED, attempts=1)
    assert _state([failed, worked], live=4).state == "done"


def test_only_failed_jobs_is_failed_and_counts_every_real_attempt():
    first = _job(JobStatus.FAILED, attempts=3, finished=NOW - timedelta(days=2))
    second = _job(JobStatus.FAILED, attempts=1, finished=NOW - timedelta(days=1), reason="The AI's answer was cut off.")
    state = _state([first, second])
    assert (state.state, state.attempts, state.last_failed_at) == ("failed", 4, NOW - timedelta(days=1))
    assert state.reason == "The AI's answer was cut off."  # the latest failure's own plain reason


def test_obligations_from_an_older_version_with_no_job_for_this_one_is_stale():
    """Guards the Globex NDA case: edited after signing, its obligations still come from the signed copy. Reported,
    never silently replaced."""
    state = _state([], live=5)
    assert (state.state, state.live_obligations) == ("stale", 5)


def test_a_recent_queue_or_a_job_inside_its_lease_is_running():
    assert _state([_job(JobStatus.QUEUED)]).state == "running"
    assert _state([_job(JobStatus.RUNNING, started=NOW - timedelta(minutes=5))]).state == "running"


def test_a_job_queued_for_hours_was_lost_and_a_job_past_its_lease_died_so_both_are_failed():
    """Guards 'Extracting…' showing for ever on a message that never reached a worker, or a worker that died."""
    lost = _state([_job(JobStatus.QUEUED, made=NOW - LOST_AFTER - timedelta(minutes=1))])
    assert lost.state == "failed" and lost.reason == "The reading was queued but never started."
    died = _state([_job(JobStatus.RUNNING, started=NOW - LEASE - timedelta(minutes=1))])
    assert died.state == "failed" and died.reason == "The worker stopped before finishing the reading."


def test_a_queue_age_counts_from_when_the_job_was_last_queued_not_from_creation():
    """Guards a restarted job being judged lost at once: Retry re-queues the same row, whose created_at may be days
    old. Only a job that has sat in the queue for hours since it was last queued is lost."""
    days_old = NOW - timedelta(days=3)
    requeued = _job(JobStatus.QUEUED, made=days_old)
    requeued = JobFact(requeued.id, requeued.status, 3, days_old, None, None, None, updated_at=NOW - timedelta(minutes=1))
    assert _state([requeued]).state == "running"
    untouched = JobFact("j", JobStatus.QUEUED, 0, days_old, None, None, None, updated_at=days_old)
    assert _state([untouched]).state == "failed"


@pytest.mark.parametrize("retryable, offered", [(False, False), (True, True), (None, True)])
def test_a_failure_that_says_another_try_fails_the_same_way_is_not_retryable(retryable, offered):
    """Guards a Retry that pays for the same failure again: a cut-off or refused answer says retryable=false. A job
    with no payload (an older one) cannot say, so it stays retryable."""
    job = JobFact("j", JobStatus.FAILED, 1, NOW, None, NOW, "The AI's answer was cut off.", retryable=retryable)
    assert _state([job]).retryable is offered


def test_a_cancelled_job_is_not_a_reading():
    state = _state([_job(JobStatus.CANCELLED, finished=NOW - timedelta(hours=1))])
    assert state.state == "failed" and state.reason == "The reading was cancelled."


def test_no_text_is_its_own_state_whatever_jobs_exist():
    """Guards a retry being offered for a contract with nothing to read."""
    state = _state([_job(JobStatus.FAILED)], has_text=False)
    assert state.state == "no_text" and "no readable text" in state.reason


def test_a_running_job_beside_a_failed_one_is_running():
    """Guards the page showing a red failure while a retry is already in flight."""
    assert _state([_job(JobStatus.FAILED, attempts=3), _job(JobStatus.QUEUED)]).state == "running"


def test_dead_works_on_a_job_row_as_well_as_a_fact():
    row = SimpleNamespace(status=JobStatus.QUEUED, created_at=NOW - LOST_AFTER - timedelta(seconds=1), started_at=None)
    assert dead(row, NOW, LEASE) == "lost" and dead(SimpleNamespace(**{**row.__dict__, "status": JobStatus.FAILED}),
                                                    NOW, LEASE) is None


def test_the_reason_shown_is_the_jobs_plain_reason_or_a_fixed_sentence_never_its_error_text():
    """Guards the leak: a job written before failures were made plain can hold the contract's own words in
    error_message (a failed insert prints its parameters). The state never reads it."""
    legacy = SimpleNamespace(id="j1", status=JobStatus.FAILED, attempt_count=1, created_at=NOW, started_at=None,
                             finished_at=NOW, error_message=f"INSERT ... {SECRET}", metadata_json={})
    assert SECRET not in (_state([_fact(legacy)]).reason or "")
    assert _state([_fact(legacy)]).reason == "The last attempt did not finish."
    modern = SimpleNamespace(**{**legacy.__dict__, "metadata_json": {"failure": {"reason": "The AI service was busy."}}})
    assert _state([_fact(modern)]).reason == "The AI service was busy."
    odd = SimpleNamespace(**{**legacy.__dict__, "metadata_json": {"failure": {"reason": 5}}})
    assert _state([_fact(odd)]).reason == "The last attempt did not finish."


# --- the loader, against real rows ------------------------------------------------------------------------------


@contextmanager
def rolled_back_session():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def db():
    with rolled_back_session() as session:
        yield session


@pytest.fixture
def world(db):
    return new_world(db)


def new_world(db):
    """An organisation, a user and an Active contract (no version yet)."""
    org = Organization(name="Extraction org", slug=f"ex-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Ex Tract",
                hashed_password="hash", status="active")
    db.add(user)
    db.flush()
    contract = Contract(org_id=org.id, title="Services agreement", owner_user_id=user.id, lifecycle_stage="active")
    db.add(contract)
    db.flush()
    return SimpleNamespace(db=db, org=org, user=user, contract=contract)


def _with_text(w, *, number=1, poor=False, make_current=True):
    """A version of the contract with a text snapshot (and optionally the current authoritative one)."""
    db = w.db
    file = db.scalars(select(ContractFile).where(ContractFile.contract_id == w.contract.id)).first()
    if file is None:
        file = ContractFile(org_id=w.org.id, contract_id=w.contract.id, file_label="main")
        db.add(file)
        db.flush()
    storage = StorageObject(org_id=w.org.id, storage_key=f"k-{uuid.uuid4().hex}", filename="a.docx",
                            mime_type="application/octet-stream", size_bytes=1, sha256_hash="0" * 64)
    db.add(storage)
    db.flush()
    version = ContractVersion(org_id=w.org.id, contract_id=w.contract.id, contract_file_id=file.id,
                              version_number=number, storage_object_id=storage.id)
    db.add(version)
    db.flush()
    snapshot = ContractTextSnapshot(org_id=w.org.id, contract_id=w.contract.id, contract_version_id=version.id,
                                    extraction_method="native", text="Supplier shall deliver.",
                                    validation_status="needs_review" if poor else "valid")
    db.add(snapshot)
    db.flush()
    version.text_snapshot_id = snapshot.id
    if make_current:
        w.contract.current_authoritative_version_id = version.id
    db.flush()
    return version


def _db_job(w, version, status, *, key="", **fields):
    job = JobRun(org_id=w.org.id, job_type="obligation_extraction", resource_type="contract",
                 resource_id=w.contract.id, idempotency_key=f"obligation_extraction:{version.id}:{key or uuid.uuid4().hex}",
                 status=status, metadata_json={"contract_version_id": version.id, **fields.pop("meta", {})}, **fields)
    w.db.add(job)
    w.db.flush()
    return job


def _obligation(w, **fields):
    w.db.add(Obligation(**{"org_id": w.org.id, "contract_id": w.contract.id, "description": "Deliver the report", **fields}))
    w.db.flush()


def test_a_contract_with_no_version_has_no_text(world):
    assert extraction_state(world.db, world.contract).state == "no_text"


def test_the_state_follows_the_jobs_of_the_current_version_only(world):
    """Guards a job for an OLD version counting as this version's extraction: the version changed, so the
    obligations are from an earlier copy (stale), not done."""
    old = _with_text(world, number=1)
    _db_job(world, old, JobStatus.SUCCEEDED, attempt_count=1)
    _obligation(world, contract_version_id=old.id)
    assert extraction_state(world.db, world.contract).state == "done"

    new = _with_text(world, number=2)  # the contract moves to a new authoritative version
    assert extraction_state(world.db, world.contract).state == "stale"
    _db_job(world, new, JobStatus.FAILED, attempt_count=3, finished_at=datetime.now(UTC),
            meta={"failure": {"reason": "The AI's answer was cut off."}})
    state = extraction_state(world.db, world.contract)
    assert (state.state, state.attempts, state.reason) == ("failed", 3, "The AI's answer was cut off.")


def test_deleted_obligations_are_not_counted(world):
    """Guards the old row counting rows a person deleted: 'Obligations tracked: 5' with nothing live."""
    version = _with_text(world)
    _db_job(world, version, JobStatus.SUCCEEDED, attempt_count=1)
    _obligation(world, deleted_at=datetime.now(UTC))
    assert extraction_state(world.db, world.contract).state == "none_found"
    _obligation(world)
    assert extraction_state(world.db, world.contract).live_obligations == 1


def test_a_manual_job_that_succeeded_beside_a_failed_plain_one_is_done(world):
    version = _with_text(world)
    _db_job(world, version, JobStatus.FAILED, key="", attempt_count=3, finished_at=datetime.now(UTC))
    _db_job(world, version, JobStatus.SUCCEEDED, key="x:manual", attempt_count=1)
    _obligation(world)
    assert extraction_state(world.db, world.contract).state == "done"
    assert len(version_jobs(world.db, contract=world.contract, version_id=version.id)) == 2


def test_none_found_carries_the_models_note_and_a_poor_text_warning(world):
    """Guards a wrong empty answer being invisible: the row shows what the model said, and says when the text was
    read poorly."""
    from app.ai.models import AISkillRun

    version = _with_text(world, poor=True)
    job = _db_job(world, version, JobStatus.SUCCEEDED, attempt_count=1)
    world.db.add(AISkillRun(
        org_id=world.org.id, skill_name="obligation_extraction", skill_version="2.0.0", execution_mode="job",
        status="succeeded", job_id=job.id, prompt_key="obligation_extraction", prompt_version="2.0.0",
        prompt_hash="0" * 64, model="m", input_payload={},
        output_payload={"obligations": [], "extraction_notes": "No operative obligations: a short NDA header only."}))
    world.db.flush()

    state = extraction_state(world.db, world.contract)
    assert state.state == "none_found" and state.poor_text is True
    assert state.note == "No operative obligations: a short NDA header only."


def test_a_contract_with_text_and_no_job_is_never(world):
    _with_text(world)
    assert extraction_state(world.db, world.contract).state == "never"
