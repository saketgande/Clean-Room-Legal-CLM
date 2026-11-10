"""The daily check that every Active contract's obligations were really read (app/obligations/extraction_sweep.py).

The decision is pure and checked directly. The run is checked against real rows in one transaction that is rolled back
at the end: the sweep commits per contract, so the session is bound to a SAVEPOINT. Each test makes an organisation of its
own, nothing is dispatched to a worker, and the AI budget, the skill switch and the access check are stubbed where a test
needs them to say no.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai import cost_guard
from app.ai.registry import skill_registry
from app.auth.models import Role, User, UserRoleGrant
from app.contracts.models import Contract
from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import engine, utcnow
from app.core.enums import JobStatus, UserStatus
from app.core.models import AdminSetting, ResourceTimelineEvent
from app.jobs import service as job_service
from app.jobs.models import JobRun
from app.notifications.models import Notification
from app.notifications.once import notify_once
from app.obligations import extraction_sweep as sweep
from app.obligations.extraction_audit import (
    LOST_AFTER,
    ExtractionState,
    JobFact,
    decide,
    extraction_state,
    extraction_states,
)
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization
from tests.test_extraction_state import _db_job, _obligation, _with_text

CUT_OFF = {"failure": {"reason": "The AI's answer was too long to fit in one pass.", "retryable": False}}
BUSY = {"failure": {"reason": "The AI service was busy or unreachable.", "retryable": True}}


# --- the decision, pure ------------------------------------------------------------------------------------------

NOW = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)


def _state(state, **fields):
    return ExtractionState(state, **fields)


def _act(state, *, known=False, clock=None):
    return sweep.audit_action(state, known=known, clock=clock, now=NOW)


def test_a_contract_nobody_read_is_started_and_the_settled_problems_are_told():
    assert _act(_state("never")) == "start"
    long_ago = NOW - timedelta(hours=3)
    for state in ("failed", "no_text", "stale"):
        assert _act(_state(state), clock=long_ago) == "escalate"
    assert _act(_state("none_found", poor_text=True)) == "escalate"


def test_a_failure_is_not_told_until_it_has_settled():
    """Guards a notice for a job another layer is still working on: a transport error is retried by Celery, a dead worker's
    job is re-queued by reclaim, and the Jobs page's reaper fails a queued job that is still waiting."""
    assert _act(_state("failed"), clock=NOW - timedelta(minutes=59)) == "wait"
    assert _act(_state("failed"), clock=NOW - timedelta(hours=1)) == "escalate"
    assert _act(_state("failed"), clock=None) == "escalate"  # nothing to wait on is not a reason to stay silent


def test_a_problem_that_existed_when_the_check_began_is_never_pushed():
    for state in (_state("failed"), _state("no_text"), _state("stale"), _state("none_found", poor_text=True)):
        assert _act(state, known=True, clock=NOW - timedelta(days=9)) == "known"


def test_a_cancelled_job_is_not_a_failure_to_report():
    """Guards telling an owner "could not be read" about a read an administrator cancelled on purpose."""
    assert _act(_state("failed", cancelled=True), clock=NOW - timedelta(days=2)) == "skip"


@pytest.mark.parametrize("state", ["done", "running", "none_found"])
def test_a_contract_that_is_fine_or_in_progress_is_left_alone(state):
    assert _act(_state(state)) == "skip"


# --- a world to run it in --------------------------------------------------------------------------------------------


@contextmanager
def savepoint_session():
    connection = engine.connect()
    trans = connection.begin()
    # autoflush off and expire_on_commit off, like the app's own SessionLocal: what the sweep relies on must be flushed
    # or committed by the sweep itself, not by the session being kind.
    session = Session(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


class World:
    """One organisation with an admin and an owner, and the helpers of test_extraction_state acting on `contract`."""

    def __init__(self, db: Session):
        self.db = db
        self.org = Organization(name="Audit org", slug=f"audit-{uuid.uuid4().hex[:8]}")
        db.add(self.org)
        db.flush()
        unit = OrgUnit(org_id=self.org.id, name="Root", parent_id=None)
        db.add(unit)
        db.flush()
        self.admin = self.person("admin")
        role = Role(org_id=self.org.id, name="admin", allows_hierarchy_rollup=True)
        db.add(role)
        db.flush()
        db.add(UserRoleGrant(user_id=self.admin.id, role_id=role.id, org_id=self.org.id, org_unit_id=unit.id))
        db.flush()
        self.owner = self.person("owner")
        self.contract = None

    def person(self, label, status=UserStatus.ACTIVE) -> User:
        user = User(org_id=self.org.id, email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", full_name=label,
                    hashed_password="hash", status=status)
        self.db.add(user)
        self.db.flush()
        return user

    def make(self, title="Services agreement", *, owner=None, stage="active", read=True) -> Contract:
        """An Active contract with a version and text (the helpers of test_extraction_state act on it)."""
        self.contract = Contract(org_id=self.org.id, title=title, lifecycle_stage=stage,
                                 owner_user_id=(owner or self.owner).id)
        self.db.add(self.contract)
        self.db.flush()
        if read:
            _with_text(self)
        return self.contract

    def version(self):
        return _version_of(self)

    def run(self, now) -> dict:
        self.db.commit()  # what a test arranged is committed data by the time a sweep runs (it rolls back per contract)
        return sweep.audit_extraction(self.db, org_id=self.org.id, now=now)

    def notices(self, event=None, user=None) -> list[Notification]:
        self.db.expire_all()
        q = select(Notification).where(Notification.org_id == self.org.id).order_by(Notification.created_at)
        if event:
            q = q.where(Notification.event_type == event)
        if user:
            q = q.where(Notification.user_id == user.id)
        return list(self.db.scalars(q))

    def jobs(self) -> list[JobRun]:
        self.db.expire_all()
        return list(self.db.scalars(select(JobRun).where(JobRun.org_id == self.org.id).order_by(JobRun.created_at)))


def _version_of(w):
    from app.contract_files.models import ContractVersion

    return w.db.get(ContractVersion, w.contract.current_authoritative_version_id)


@pytest.fixture
def w(monkeypatch):
    """A world, with sending stubbed out and every gate open: notices and starting both on, a key, the skill on, a full
    AI budget, and nobody walled off from anything. Tests close the gate they are about."""
    with savepoint_session() as db:
        world = World(db)
        world.dispatched = []
        world.blocked = set()  # users who cannot open any contract

        world.pending_at_dispatch = []

        def fake_dispatch(db_, *, job):
            world.pending_at_dispatch.append(db_.in_transaction())  # True would mean the row is not committed yet
            world.dispatched.append(job.id)
            job.celery_task_id = f"task-{job.id[:8]}"
            db_.add(job)
            return job

        monkeypatch.setattr(job_service, "dispatch_job", fake_dispatch)
        monkeypatch.setattr(sweep, "_contract_visible_silently", lambda db_, user, cid: user.id not in world.blocked)
        monkeypatch.setattr(cost_guard, "tokens_left", lambda org_id: settings.claude_daily_token_cap_per_org)
        monkeypatch.setattr(settings, "obligation_audit_notify", True)
        monkeypatch.setattr(settings, "obligation_audit_start_jobs", True)
        monkeypatch.setattr(settings, "claude_api_key", "k")
        yield world


def _baseline(w, now=None):
    now = now or utcnow()
    result = w.run(now)
    assert result.get("baseline") is True, result
    return now


# --- the baseline ----------------------------------------------------------------------------------------------------


def test_a_poorly_read_empty_answer_that_existed_before_the_check_is_remembered_too(w):
    w.make("Garbled already", read=False)
    _with_text(w, poor=True)
    _db_job(w, w.version(), JobStatus.SUCCEEDED, attempt_count=1)
    w.db.flush()
    start = _baseline(w)

    assert w.run(start + timedelta(days=1))["known"] == 1 and w.notices("extraction.poor_text") == []


def test_the_first_run_remembers_what_is_already_wrong_tells_admins_once_and_starts_nothing(w):
    """Guards the flood: the first night must not tell every owner about every old problem, nor spend on the backlog."""
    failed = w.make("Failed already")
    _db_job(w, w.version(), JobStatus.FAILED, attempt_count=1, finished_at=utcnow() - timedelta(days=3), meta=CUT_OFF)
    w.make("Fine")
    _db_job(w, w.version(), JobStatus.SUCCEEDED, attempt_count=1)
    _obligation(w)
    never = w.make("Never read")

    result = w.run(utcnow())

    assert result["baseline"] is True and result["states"] == {"failed": 1, "never": 1, "stale": 0, "no_text": 0}
    stored = w.db.scalars(select(AdminSetting).where(AdminSetting.org_id == w.org.id)).one()
    assert stored.key == sweep.SETTING_KEY and stored.value["v"] == 1
    assert stored.is_secret is True  # a list of contract ids that GET /admin/settings must not hand to every admin
    assert stored.value["known"] == [sweep.problem_key(failed.id, failed.current_authoritative_version_id)]
    assert w.jobs()[0].status == JobStatus.FAILED and len(w.jobs()) == 2 and w.dispatched == []
    [note] = w.notices()
    assert note.user_id == w.admin.id and note.event_type == "extraction.audit_started"
    assert "1 could not be read, 1 were never read" in note.body and "Failed already" not in note.body
    assert never.id not in note.body


def test_the_baseline_says_when_starting_reads_is_switched_off(w, monkeypatch):
    monkeypatch.setattr(settings, "obligation_audit_start_jobs", False)
    w.make("Never read")
    w.run(utcnow())
    assert "switched off in settings" in w.notices()[0].body


def test_a_run_never_makes_a_second_baseline(w):
    _baseline(w)
    assert w.run(utcnow() + timedelta(days=1)).get("baseline") is None
    assert w.db.scalar(select(func.count(AdminSetting.id)).where(AdminSetting.org_id == w.org.id)) == 1


def test_a_setting_that_is_not_ours_skips_the_run_instead_of_starting_over(w):
    """Guards an admin's PUT of a bad value muting the check: "present but unreadable" must never read as "first run"."""
    w.db.add(AdminSetting(org_id=w.org.id, key=sweep.SETTING_KEY, value="2026-10-01"))
    w.db.flush()
    w.make("Never read")

    assert w.run(utcnow()) == {"skipped": "unreadable baseline setting"}
    assert w.jobs() == [] and w.notices() == []


def test_nothing_runs_when_both_switches_are_off(w, monkeypatch):
    monkeypatch.setattr(settings, "obligation_audit_notify", False)
    monkeypatch.setattr(settings, "obligation_audit_start_jobs", False)
    assert w.run(utcnow()) == {"skipped": "switched off"}


def test_spending_is_off_unless_somebody_turns_it_on():
    """Guards the dev stack (a real key, a real worker) starting paid reads on its own the day this ships."""
    from app.core.config import Settings

    fresh = Settings(_env_file=None)
    assert fresh.obligation_audit_start_jobs is False and fresh.obligation_audit_notify is True


# --- telling people --------------------------------------------------------------------------------------------------


def _new_failure(w, *, finished, meta=CUT_OFF, title="Contract"):
    w.make(title)
    _db_job(w, w.version(), JobStatus.FAILED, attempt_count=1, finished_at=finished, meta=meta)
    return w.contract


def test_a_new_failure_is_told_to_the_owner_once_and_never_again(w):
    """Guards the daily repeat: the same problem must not reach the same person every morning."""
    start = _baseline(w)
    broken = _new_failure(w, finished=start + timedelta(hours=1), title="Acme MSA")

    first = w.run(start + timedelta(days=1))
    second = w.run(start + timedelta(days=2))

    assert first["told"] == 1 and second["told"] == 0
    [note] = w.notices("extraction.failed", w.owner)
    assert note.subject == "Obligations could not be read: Acme MSA"
    assert note.metadata_json == {"contract_id": broken.id, "version_id": broken.current_authoritative_version_id}
    assert "too long to fit in one pass" in note.body and "Ask an administrator" in note.body
    assert "Retry" not in note.body  # this failure offers no Retry button, so the notice must not mention one


def test_a_failure_another_try_can_fix_points_at_the_retry_button(w):
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1), meta=BUSY)
    w.run(start + timedelta(days=1))
    body = w.notices("extraction.failed")[0].body
    assert "Retry button, press it" in body


def test_a_problem_that_existed_before_the_check_is_never_pushed_even_after_a_retry_fails_again(w):
    """Guards the clock that moves: a retry (or the Jobs page's reaper) rewrites a job's finish time, so a time-based
    "new" would push a known failure to the person who just watched it fail."""
    old = _new_failure(w, finished=utcnow() - timedelta(days=5), title="Old failure")
    start = _baseline(w)
    job = w.jobs()[0]
    job.finished_at = start + timedelta(hours=2)  # a person pressed Retry and it failed again, after the baseline
    w.db.flush()

    result = w.run(start + timedelta(days=1))

    assert result["known"] == 1 and result["told"] == 0 and w.notices("extraction.failed") == []
    assert old.id


def test_a_failure_still_inside_the_settle_window_waits_for_tomorrow(w):
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1), meta=BUSY)

    soon = w.run(start + timedelta(hours=1, minutes=30))
    later = w.run(start + timedelta(hours=3))

    assert (soon["wait"], soon["told"]) == (1, 0) and later["told"] == 1


def test_a_read_that_was_lost_is_told_only_once_it_is_settled(w):
    """Guards a lost message (queued for hours with no finish time) being compared on a missing time: it has been given
    up on since `created + 2 h`, and is settled an hour after that."""
    start = _baseline(w)
    w.make("Lost")
    queued = start + timedelta(minutes=1)
    _db_job(w, w.version(), JobStatus.QUEUED, created_at=queued, updated_at=queued, celery_task_id="gone")

    assert w.run(start + timedelta(hours=2, minutes=30))["told"] == 0  # given up on at +2h, settled at +3h
    assert w.run(start + timedelta(hours=3, minutes=5))["told"] == 1
    assert "queued but never started" in w.notices("extraction.failed")[0].body


def test_a_cancelled_read_is_not_told(w):
    start = _baseline(w)
    w.make("Cancelled")
    _db_job(w, w.version(), JobStatus.CANCELLED, updated_at=start + timedelta(hours=1))
    assert w.run(start + timedelta(days=1))["told"] == 0


def test_a_contract_with_no_text_that_appears_after_the_check_is_told(w):
    start = _baseline(w)
    w.make("Scan", read=False)  # an Active contract with no version at all

    result = w.run(start + timedelta(days=1))

    assert result["told"] == 1
    note = w.notices("extraction.no_text")[0]
    assert "no readable text" in note.body and "Upload a readable copy" in note.body
    assert note.metadata_json["version_id"] == "none"  # a contract with no version still has a stable key


@pytest.mark.parametrize("blank", ["", "  ", "\n\n\n", "\t", "\f", " \r\n \t "])
def test_a_snapshot_with_no_text_in_it_is_no_text_everywhere_and_is_never_paid_for(w, blank):
    """Guards starting a paid read of an empty scan (the model would return an empty list, the contract would end as
    "none found", and the audit would report success), and the two loaders disagreeing about what "empty" is: a blank
    scan is stored as newlines (one per blank page), which a space-only trim counts as text."""
    start = _baseline(w)
    w.make("Empty scan")
    from app.contract_files.models import ContractTextSnapshot

    w.db.get(ContractTextSnapshot, w.version().text_snapshot_id).text = blank
    w.db.flush()
    assert extraction_state(w.db, w.contract).state == "no_text"
    assert extraction_states(w.db, [w.contract])[w.contract.id].state == "no_text"

    result = w.run(start + timedelta(days=1))

    assert result["started"] == 0 and w.jobs() == [] and result["told"] == 1
    assert w.notices("extraction.no_text")


def test_the_retry_endpoint_and_the_assistant_will_not_pay_to_read_a_snapshot_with_no_text(w):
    """Guards a paid read that cannot change anything: the page shows "no readable text" with no Retry button, so the
    endpoint and the assistant's tool (which share extraction_job) must refuse it the same way."""
    from fastapi import HTTPException

    from app.obligations.service import ObligationsService

    w.make("Blank pages")
    from app.contract_files.models import ContractTextSnapshot

    w.db.get(ContractTextSnapshot, w.version().text_snapshot_id).text = "\n\n"
    w.db.flush()

    with pytest.raises(HTTPException) as err:
        ObligationsService(w.db).extraction_job(contract=w.contract, user=w.owner)

    assert err.value.status_code == 409 and "no readable text" in err.value.detail and w.jobs() == []


def test_a_contract_edited_after_its_obligations_were_read_is_told_as_stale(w):
    start = _baseline(w)
    w.make("Edited")
    old = w.version()
    _obligation(w, contract_version_id=old.id)
    _with_text(w, number=2)

    assert w.run(start + timedelta(days=1))["told"] == 1
    note = w.notices("extraction.stale")[0]
    assert "earlier version" in note.subject and "Re-read" in note.body
    assert Obligation_count(w) == 1  # nothing was replaced


def Obligation_count(w):
    from app.obligations.models import Obligation

    return w.db.scalar(select(func.count(Obligation.id)).where(Obligation.contract_id == w.contract.id,
                                                                Obligation.deleted_at.is_(None)))


def test_an_empty_answer_from_poorly_read_text_is_told(w):
    """Guards the blind spot a scan falls into: the read succeeded, found nothing, and the text was garbled."""
    start = _baseline(w)
    w.make("Garbled", read=False)
    _with_text(w, poor=True)
    _db_job(w, w.version(), JobStatus.SUCCEEDED, attempt_count=1)

    assert w.run(start + timedelta(days=1))["told"] == 1
    assert "read poorly" in w.notices("extraction.poor_text")[0].subject


def test_a_failed_job_is_never_run_again_by_the_sweep(w):
    """Guards the rule Step 8 settled: a machine re-runs only transport errors; the sweep tells a person instead."""
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1), meta=BUSY)

    w.run(start + timedelta(days=1))
    w.run(start + timedelta(days=2))

    assert w.dispatched == [] and [j.status for j in w.jobs()] == [JobStatus.FAILED]


# --- who is told -----------------------------------------------------------------------------------------------------


def test_an_owner_who_has_left_or_cannot_open_the_contract_is_not_told_the_admins_who_can_are(w):
    start = _baseline(w)
    gone = w.person("gone", status=UserStatus.DEACTIVATED)
    walled_admin = w.person("walled admin")
    w.blocked.add(walled_admin.id)
    _new_failure(w, finished=start + timedelta(hours=1), title="Orphaned")
    w.contract.owner_user_id = gone.id
    w.db.flush()

    w.run(start + timedelta(days=1))

    assert w.notices("extraction.failed", gone) == []
    assert [n.user_id for n in w.notices("extraction.failed")] == [w.admin.id]


def test_the_digest_names_no_contract_to_an_admin_who_cannot_open_it(w):
    """Guards a title reaching someone the wall is there to keep out, admins included: the owner is told, the walled admin
    gets the count and nothing else."""
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1), title="Secret deal")
    w.blocked.add(w.admin.id)

    w.run(start + timedelta(days=1))

    assert len(w.notices("extraction.failed", w.owner)) == 1
    [digest] = w.notices("extraction.digest")
    assert digest.user_id == w.admin.id
    assert "Secret deal" not in digest.body and "1 contract needs attention (none of them ones you can open)" in digest.body


def test_a_problem_nobody_may_be_told_about_writes_nothing_and_stays_quiet_every_day(w):
    """Guards a loop of empty notices (and access.denied rows) for a contract everyone is walled from."""
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1), title="Walled off")
    w.blocked.update({w.owner.id, w.admin.id})

    results = [w.run(start + timedelta(days=d)) for d in (1, 2)]

    assert [r["told"] for r in results] == [0, 0] and [r["digests"] for r in results] == [0, 0]
    assert w.notices("extraction.failed") == []


def test_an_admin_who_can_open_the_contract_sees_its_title_in_the_digest(w):
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1), title="Acme MSA")
    w.run(start + timedelta(days=1))
    [digest] = w.notices("extraction.digest")
    assert "1 contract needs attention: Acme MSA" in digest.body


def test_the_digest_is_one_a_day_and_only_when_something_happened(w):
    start = _baseline(w)
    quiet = w.run(start + timedelta(days=1))
    _new_failure(w, finished=start + timedelta(days=1, hours=2), title="Late failure")
    a = w.run(start + timedelta(days=2))
    b = w.run(start + timedelta(days=2, minutes=5))  # the same day again

    assert quiet["digests"] == 0 and a["digests"] == 1 and b["digests"] == 0
    assert len(w.notices("extraction.digest")) == 1


def test_one_owner_is_told_about_at_most_ten_contracts_a_run_and_the_rest_wait_for_tomorrow(w, monkeypatch):
    """Guards an outage after a long gap turning one owner's bell into a wall, and a contract past the cap never being
    told: deferred rows count nothing against the cap, so the next run reaches them."""
    monkeypatch.setattr(sweep, "MAX_NOTICES_PER_OWNER", 2)
    start = _baseline(w)
    for i in range(3):
        _new_failure(w, finished=start + timedelta(hours=1), title=f"Contract {i}")

    first = w.run(start + timedelta(days=1))
    second = w.run(start + timedelta(days=2))

    assert (first["told"], first["deferred"]) == (2, 1) and (second["told"], second["deferred"]) == (1, 0)
    assert len(w.notices("extraction.failed", w.owner)) == 3
    assert "1 more will be told tomorrow" in w.notices("extraction.digest")[0].body


def test_the_cap_applies_to_the_admins_who_stand_in_for_an_owner_who_has_left(w, monkeypatch):
    """Guards the bell becoming a wall for an admin: a person who left may own a hundred contracts, and every one of them
    that fails in the same outage would be told to each admin at once."""
    monkeypatch.setattr(sweep, "MAX_NOTICES_PER_OWNER", 2)
    start = _baseline(w)
    gone = w.person("gone", status=UserStatus.DEACTIVATED)
    for i in range(3):
        _new_failure(w, finished=start + timedelta(hours=1), title=f"Orphan {i}")
        w.contract.owner_user_id = gone.id
    w.db.flush()

    first = w.run(start + timedelta(days=1))
    second = w.run(start + timedelta(days=2))

    assert (first["told"], first["deferred"]) == (2, 1) and (second["told"], second["deferred"]) == (1, 0)
    assert len(w.notices("extraction.failed", w.admin)) == 3


def test_a_problem_already_told_is_not_counted_as_waiting_when_the_cap_is_reached(w, monkeypatch):
    """Guards "N more will be told tomorrow" for notices that were sent long ago: the cap is checked after the
    already-told test, so a contract told on an earlier day, met late in the order, is neither deferred nor re-sent."""
    monkeypatch.setattr(sweep, "MAX_NOTICES_PER_OWNER", 2)
    start = _baseline(w)
    new = [w.make("New 1"), w.make("New 2")]
    for c in new:
        w.contract = c
        _db_job(w, w.version(), JobStatus.SUCCEEDED, attempt_count=1)
        _obligation(w)
    _new_failure(w, finished=start + timedelta(hours=1), title="Early")
    assert w.run(start + timedelta(days=1))["told"] == 1  # only Early is broken so far
    for c in new:  # the next day the two older contracts break too
        w.contract = c
        for job in w.db.scalars(select(JobRun).where(JobRun.resource_id == c.id)):
            job.status, job.finished_at = JobStatus.FAILED, start + timedelta(days=1, hours=1)
            job.metadata_json = {**job.metadata_json, **CUT_OFF}
    w.db.flush()

    result = w.run(start + timedelta(days=2, hours=5))

    assert (result["told"], result["deferred"]) == (2, 0)
    assert len(w.notices("extraction.failed", w.owner)) == 3


def test_a_problem_on_a_contract_of_another_organisation_is_not_this_organisations_business(w):
    start = _baseline(w)
    mine = _new_failure(w, finished=start + timedelta(hours=1), title="Mine")
    other_world = World(w.db)
    other_world.make("Theirs")
    _db_job(other_world, other_world.version(), JobStatus.FAILED, attempt_count=1,
            finished_at=start + timedelta(hours=1), meta=CUT_OFF)

    w.run(start + timedelta(days=1))

    assert [n.metadata_json["contract_id"] for n in w.notices("extraction.failed")] == [mine.id]
    assert other_world.notices() == []


# --- starting reads --------------------------------------------------------------------------------------------------


def test_a_contract_nobody_read_gets_one_read_started_audited_and_dispatched_after_the_commit(w):
    start = _baseline(w)
    never = w.make("Never read")

    result = w.run(start + timedelta(days=1))

    [job] = w.jobs()
    version = w.version()
    assert result["started"] == 1 and w.dispatched == [job.id]
    assert w.pending_at_dispatch == [False]  # the row was committed before a worker could look for it
    assert job.idempotency_key == f"obligation_extraction:{version.id}:{version.text_snapshot_id}"  # the activation's key
    assert job.created_by_user_id is None and job.metadata_json["source"] == "daily_audit"
    assert job.celery_task_id == f"task-{job.id[:8]}"  # persisted by the second commit, not left in memory
    audit = w.db.scalars(select(AuditLog).where(AuditLog.resource_id == never.id,
                                                AuditLog.action == "obligation.extraction_requested")).one()
    assert audit.actor_user_id is None and audit.after["source"] == "daily_audit" and audit.after["job_id"] == job.id
    event = w.db.scalars(select(ResourceTimelineEvent).where(
        ResourceTimelineEvent.resource_id == never.id, ResourceTimelineEvent.event_type ==
        "contract.obligations_extraction_requested")).one()
    assert "daily check" in event.title and event.job_id == job.id
    assert [n.event_type for n in w.notices()] == ["extraction.audit_started", "extraction.digest"]
    assert "1 unread contract was sent to be read" in w.notices("extraction.digest")[0].body


def test_a_started_reads_task_id_survives_a_later_contract_that_rolls_back(w, monkeypatch):
    """Guards the dispatch's in-memory task id being lost: dispatch only sets it on the object, and the next contract's
    rollback (here, one that changed under the sweep) would otherwise undo it, so reclaim would send the job again."""
    start = _baseline(w)
    first = w.make("First")
    second = w.make("Second")
    real = sweep.extraction_state

    def second_is_busy(db, contract, *, now=None):
        state = real(db, contract, now=now)
        return ExtractionState("running") if contract.id == second.id else state

    monkeypatch.setattr(sweep, "extraction_state", second_is_busy)
    result = w.run(start + timedelta(days=1))

    [job] = w.jobs()
    assert result["started"] == 1 and job.resource_id == first.id and job.celery_task_id is not None


def test_a_second_run_does_not_start_a_second_read(w):
    start = _baseline(w)
    w.make("Never read")
    w.run(start + timedelta(days=1))
    again = w.run(start + timedelta(days=1, minutes=5))
    assert again["started"] == 0 and len(w.jobs()) == 1


def test_starting_reads_off_means_no_job_and_no_digest_about_it(w, monkeypatch):
    """Guards a daily "no new reads were started: switched off" note: ordinary operation is not news."""
    start = _baseline(w)
    monkeypatch.setattr(settings, "obligation_audit_start_jobs", False)
    w.make("Never read")

    result = w.run(start + timedelta(days=1))

    assert result["started"] == 0 and w.jobs() == [] and result["digests"] == 0
    assert result["held_back"] == "starting reads is switched off in settings"


def test_at_most_six_reads_a_day_however_often_it_runs(w, monkeypatch):
    monkeypatch.setattr(sweep, "MAX_JOBS_PER_DAY", 2)
    _baseline(w)
    for i in range(5):
        w.make(f"Never {i}")

    today = utcnow()  # the day's count is of jobs created today, so "today" is the real clock here
    first = w.run(today)
    again = w.run(today)  # a redelivered task, or a manual run
    for job in w.jobs():  # a day passes: yesterday's reads no longer count against today's limit
        job.created_at -= timedelta(days=1)
    tomorrow = w.run(utcnow())

    assert (first["started"], again["started"], tomorrow["started"]) == (2, 0, 2)
    assert again["held_back"] == "today's limit of reads is used" and again["digests"] == 0


def test_reads_are_not_started_when_the_ones_it_started_keep_failing(w):
    """Guards a revoked key or a wrong workspace turning the whole backlog into red rows, 25 a day: three of its own reads
    failed in two days and none finished, so it stops and tells admins."""
    start = _baseline(w)
    for i in range(3):
        w.make(f"Tried {i}")
        job = _db_job(w, w.version(), JobStatus.FAILED, attempt_count=1, finished_at=start, meta=BUSY)
        job.metadata_json = {**job.metadata_json, "source": "daily_audit"}
        job.created_at = start
    w.db.flush()
    w.make("Waiting")

    result = w.run(start + timedelta(hours=26))

    assert result["started"] == 0 and result["held_back"] == "the last reads it started all failed"
    assert "No new reads were started: the last reads it started all failed" in w.notices("extraction.digest")[0].body


def test_reads_are_not_started_when_less_than_half_the_days_budget_is_left_or_it_cannot_be_read(w, monkeypatch):
    start = _baseline(w)
    w.make("Never read")
    day = start + timedelta(days=1)
    cap = settings.claude_daily_token_cap_per_org

    monkeypatch.setattr(cost_guard, "tokens_left", lambda org_id: cap // 2 - 1)
    assert w.run(day)["held_back"] == "less than half of the day's AI budget is left"
    monkeypatch.setattr(cost_guard, "tokens_left", lambda org_id: None)
    assert w.run(day)["held_back"] == "the day's AI budget could not be read"
    assert w.jobs() == []
    monkeypatch.setattr(cost_guard, "tokens_left", lambda org_id: cap // 2)
    assert w.run(day)["started"] == 1


def test_reads_already_queued_in_the_run_count_against_the_budget_floor(w, monkeypatch):
    """Guards the floor being read once per read while none of the queued ones has spent anything yet: with a budget of
    300,000 the floor is 150,000, and each queued read is counted at 120,000, so a third is refused."""
    monkeypatch.setattr(settings, "claude_daily_token_cap_per_org", 300_000)
    start = _baseline(w)
    for i in range(4):
        w.make(f"Never {i}")

    result = w.run(start + timedelta(days=1))

    assert result["started"] == 2 and result["held_back"] == "less than half of the day's AI budget is left"


def test_reads_are_not_started_with_the_skill_off_or_no_key(w, monkeypatch):
    start = _baseline(w)
    w.make("Never read")
    day = start + timedelta(days=1)
    flag = skill_registry.get("obligation_extraction").feature_flag
    w.db.add(AdminSetting(org_id=w.org.id, key=flag, value=False))
    w.db.flush()
    assert w.run(day)["held_back"] == "the obligation reading skill is switched off"
    w.db.execute(AdminSetting.__table__.delete().where(AdminSetting.org_id == w.org.id, AdminSetting.key == flag))
    monkeypatch.setattr(settings, "claude_api_key", "")
    monkeypatch.setattr(settings, "mock_claude", False)
    assert w.run(day)["held_back"] == "the AI service is not set up"
    assert w.jobs() == []


def test_a_queue_that_cannot_be_reached_stops_the_run_and_the_read_is_left_for_reclaim(w, monkeypatch):
    """Guards waiting out a publish timeout for every contract: the first failed send stops the starting. The job is
    committed QUEUED with no task id, which is exactly what reclaim's undispatched sweep picks up."""
    start = _baseline(w)
    w.make("First")
    w.make("Second")

    def broken(db, *, job):
        raise ConnectionError("broker down")

    monkeypatch.setattr(job_service, "dispatch_job", broken)
    result = w.run(start + timedelta(days=1))

    jobs = w.jobs()
    assert result["started"] == 1 and len(jobs) == 1 and jobs[0].celery_task_id is None
    assert jobs[0].status == JobStatus.QUEUED
    assert result["held_back"] == "the queue could not be reached"
    assert "the queue could not be reached" in w.notices("extraction.digest")[0].body


def test_a_contract_read_by_an_activation_a_moment_ago_is_not_started_again(w, monkeypatch):
    """Guards the race with activation: the state is read again after the lock, and the job key is the activation's own."""
    start = _baseline(w)
    contract = w.make("Raced")
    real = sweep.extraction_state

    def meanwhile_activation(db, c, *, now=None):
        _db_job(w, w.version(), JobStatus.QUEUED, key="")  # the activation's job appears after the batch looked
        db.commit()  # committed by its own request, not part of the sweep's transaction
        return real(db, c, now=now)

    monkeypatch.setattr(sweep, "extraction_state", meanwhile_activation)
    result = w.run(start + timedelta(days=1))

    assert result["started"] == 0 and len(w.jobs()) == 1 and w.dispatched == []
    assert contract.id


def test_a_contract_that_left_active_before_its_turn_is_skipped(w, monkeypatch):
    start = _baseline(w)
    contract = w.make("Closing")
    real = sweep._start_job

    def closed_first(db, row, now):
        contract.lifecycle_stage = "closed"
        db.flush()
        return real(db, row, now)

    monkeypatch.setattr(sweep, "_start_job", closed_first)
    assert w.run(start + timedelta(days=1))["started"] == 0


# --- keeping a run safe ----------------------------------------------------------------------------------------------


def test_one_contract_that_fails_does_not_stop_the_rest(w, monkeypatch):
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1), title="Bad")
    _new_failure(w, finished=start + timedelta(hours=1), title="Good")
    real = sweep.notify_once

    def sometimes(db, **kw):
        if "Bad" in kw["subject"]:
            raise RuntimeError("boom")
        return real(db, **kw)

    monkeypatch.setattr(sweep, "notify_once", sometimes)
    result = w.run(start + timedelta(days=1))

    assert result["errors"] == 1 and result["told"] == 1
    assert [n.subject for n in w.notices("extraction.failed")] == ["Obligations could not be read: Good"]


def test_the_workers_time_limit_is_not_swallowed_by_the_per_contract_catch(w, monkeypatch):
    """Guards the sweep running on to the hard kill: SoftTimeLimitExceeded is an ordinary Exception."""
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1))
    monkeypatch.setattr(sweep, "notify_once", lambda *a, **k: (_ for _ in ()).throw(SoftTimeLimitExceeded()))
    with pytest.raises(SoftTimeLimitExceeded):
        w.run(start + timedelta(days=1))


def test_a_run_past_its_time_budget_stops_and_says_so(w, monkeypatch):
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1))
    w.make("Never read")
    monkeypatch.setattr(sweep, "RUN_BUDGET", timedelta(seconds=-1))

    result = w.run(start + timedelta(days=1))

    assert result["partial"] is True and result["started"] == 0 and result["told"] == 0


def test_every_organisation_with_an_active_contract_is_checked(w):
    w.make("Mine")
    other = World(w.db)
    other.make("Theirs")
    ran = sweep.audit_all_orgs(w.db, now=utcnow())
    assert {r["org_id"] for r in ran} >= {w.org.id, other.org.id} and all(r.get("baseline") for r in ran if r["org_id"] in
                                                                         (w.org.id, other.org.id))


def test_the_check_is_on_the_beat_schedule_off_the_reclaim_tick_and_the_task_is_registered():
    from app.jobs import tasks  # noqa: F401  (registers the task)
    from app.jobs.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["audit-obligation-extraction"]
    assert entry["task"] == "app.jobs.tasks.audit_obligation_extraction"
    assert (entry["schedule"].hour, entry["schedule"].minute) == ({7}, {58})  # not a multiple of 5: reclaim runs */5
    assert "app.jobs.tasks.audit_obligation_extraction" in celery_app.tasks


def test_only_active_contracts_are_started(w):
    """Guards a contract still in Review (or closed) being read: the sweep is about contracts being tracked."""
    start = _baseline(w)
    w.make("In review", stage="review")
    w.make("Closed", stage="closed")
    assert w.run(start + timedelta(days=1))["scanned"] == 0 and w.jobs() == []


def test_an_admin_who_has_left_is_told_nothing(w):
    gone = w.person("old admin", status=UserStatus.DEACTIVATED)
    role = w.db.scalars(select(Role).where(Role.org_id == w.org.id, Role.name == "admin")).one()
    unit = w.db.scalars(select(OrgUnit).where(OrgUnit.org_id == w.org.id)).one()
    w.db.add(UserRoleGrant(user_id=gone.id, role_id=role.id, org_id=w.org.id, org_unit_id=unit.id))
    w.db.flush()
    start = _baseline(w)
    _new_failure(w, finished=start + timedelta(hours=1))

    w.run(start + timedelta(days=1))

    assert {n.user_id for n in w.notices()} == {w.admin.id, w.owner.id}


@pytest.mark.parametrize("why, jobs, expected", [
    ("a finished job: when it finished", [dict(status="failed", finished_at=NOW)], NOW),
    ("a lost job has no finish time: given up on two hours after it was queued",
     [dict(status="queued", created_at=NOW - timedelta(hours=5), updated_at=NOW - timedelta(hours=4))],
     NOW - timedelta(hours=4) + LOST_AFTER),
    ("a dead one: when its lease ran out",
     [dict(status="running", created_at=NOW - timedelta(hours=5), started_at=NOW - timedelta(hours=4))],
     NOW - timedelta(hours=4) + timedelta(minutes=20)),
    ("a cancelled one with no finish time: when it was last touched",
     [dict(status="cancelled", created_at=NOW - timedelta(hours=5), updated_at=NOW - timedelta(hours=3))],
     NOW - timedelta(hours=3)),
])
def test_a_failed_state_always_says_since_when(why, jobs, expected):
    """Guards comparing a missing time: a lost or dead job has no finish time, and the sweep must never find out by
    raising (it would count an error every day and never tell anyone)."""
    facts = [JobFact(f"j{i}", j["status"], 1, j.get("created_at", NOW - timedelta(hours=9)), j.get("started_at"),
                     j.get("finished_at"), None, j.get("updated_at")) for i, j in enumerate(jobs)]
    state = decide(has_text=True, jobs=facts, live_obligations=0, now=NOW, lease=timedelta(minutes=20))
    assert state.state == "failed" and state.failed_since == expected, why


# --- notify_once -----------------------------------------------------------------------------------------------------


def test_notify_once_writes_a_notification_once_per_person_kind_and_key(w):
    def send(user, event="x.y", key=None, subject="s"):
        return notify_once(w.db, org_id=w.org.id, user_id=user.id, event_type=event, subject=subject, body="b",
                           key=key or {"a": "1"})

    assert send(w.owner) is True
    assert send(w.owner) is False  # the same person, kind and key, in the same session
    assert send(w.admin) is True  # another person
    assert send(w.owner, event="x.z") is True  # another kind
    assert send(w.owner, key={"a": "2"}) is True  # another key
    assert send(w.owner, key={"a": "1", "b": "2"}) is True  # every pair must match: a different key, not a subset
    assert len(w.notices()) == 5
    assert all(n.channel == "in_app" and n.status == "sent" for n in w.notices())


def test_notify_once_cuts_a_subject_that_would_not_fit_the_column(w):
    """Guards a 500-character contract title failing the insert every day, so that contract is never told."""
    assert notify_once(w.db, org_id=w.org.id, user_id=w.owner.id, event_type="x", subject="T" * 600, body="b",
                       key={"a": "1"})
    assert len(w.notices()[0].subject) == 500
