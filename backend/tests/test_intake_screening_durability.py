"""The intake relationship-check job must survive the things that kill it.

It used to run inline after the request had already been committed, inside a
try/except that wrote `{"status": "error"}` and stopped. Two ways a request
ended up permanently unscreened: the process dying between the commit and the
enrichment, and the screen simply failing — with no retry, no queue, and no way
to find the requests it had happened to. An unscreened request looked exactly
like one that had passed, which is the failure mode a legal system can least
afford.
"""

import inspect
import uuid
from types import SimpleNamespace

import pytest

import app.jobs.service as job_service
import app.models  # noqa: F401  (register every mapper)
from app.intake import service as intake
from app.intake.schemas import RequestCreate
from app.jobs.models import JobRun
from app.jobs.tasks import _run_ai_job

USER = SimpleNamespace(id="u-1", org_id="org-1")


class FakeDB:
    """Records what was written and whether it was committed, so a test can
    assert the screening job shares the request's transaction."""

    def __init__(self, scalar=None):
        self._scalar = scalar
        self.added = []
        self.commits = 0
        self.added_at_commit = []

    def scalar(self, _stmt):
        return self._scalar

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = str(uuid.uuid4())

    def get(self, model, key):
        return next((o for o in self.added if getattr(o, "id", None) == key), None)

    def commit(self):
        self.commits += 1
        self.added_at_commit.append(list(self.added))

    def refresh(self, _obj):
        pass

    def rollback(self):
        pass


@pytest.fixture
def quiet_intake(monkeypatch):
    for name in ("write_audit_log", "write_timeline_event"):
        monkeypatch.setattr(intake, name, lambda db, **kw: None)
    monkeypatch.setattr(intake, "_next_ref", lambda db: "REQ-1")
    monkeypatch.setattr(
        intake, "serialize_request",
        lambda db, r: {"id": r.id, "screening": r.screening},
    )


def _jobs(db) -> list[JobRun]:
    return [o for o in db.added if isinstance(o, JobRun)]


def test_the_screening_job_is_written_in_the_requests_own_transaction(monkeypatch, quiet_intake):
    """The durability property. If the request exists, its screening job
    exists — so a crash after the commit can no longer leave a request that
    nothing will ever screen."""
    monkeypatch.setattr(job_service, "dispatch_job", lambda db, *, job: job)
    db = FakeDB(scalar=None)

    intake.create_request(db, actor=USER, payload=RequestCreate(
        type_label="NDA", description="Mutual NDA with Acme"), defer_triage=True)

    screening = [j for j in _jobs(db) if j.job_type == "intake_screening"]
    assert len(screening) == 1
    # Present in the very first commit, alongside the request itself.
    first_commit = db.added_at_commit[0]
    assert screening[0] in first_commit
    assert any(getattr(o, "ref", None) == "REQ-1" for o in first_commit)


def test_a_broker_that_is_down_does_not_lose_the_screen(monkeypatch, quiet_intake):
    """Dispatch is best-effort because the row is already durable. A failing
    broker must cost minutes — the reclaim sweep re-dispatches anything left
    QUEUED — not the screen."""
    def broker_is_down(db, *, job):
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(job_service, "dispatch_job", broker_is_down)
    db = FakeDB(scalar=None)

    result = intake.create_request(db, actor=USER, payload=RequestCreate(
        type_label="NDA", description="Mutual NDA with Acme"), defer_triage=True)

    assert result["screening"]["status"] == "pending"
    assert [j for j in _jobs(db) if j.job_type == "intake_screening"]


def test_a_request_says_it_is_unscreened_until_the_job_lands(monkeypatch, quiet_intake):
    """Guards the silent failure directly: an unscreened request must not look
    like one that passed."""
    monkeypatch.setattr(job_service, "dispatch_job", lambda db, *, job: job)
    db = FakeDB(scalar=None)

    result = intake.create_request(db, actor=USER, payload=RequestCreate(
        type_label="NDA", description="Mutual NDA with Acme"), defer_triage=True)

    assert result["screening"]["status"] == "pending"


def test_a_failing_screen_fails_its_job_rather_than_being_swallowed(monkeypatch):
    """The retry property. The old code caught every exception and wrote a
    status field nothing queried; the job body must let it propagate so the
    job is retried and then visible as FAILED."""
    request = SimpleNamespace(id="req-1", org_id="org-1", screening=None)

    def boom(db, r, *, actor_user_id=None):
        raise RuntimeError("sanctions list unreachable")

    monkeypatch.setattr("app.intake.screening.run_screening", boom)

    class DB:
        def get(self, model, key):
            return request

    with pytest.raises(RuntimeError):
        intake.run_intake_screening(DB(), request_id="req-1", actor_id="u-1")


def test_a_request_deleted_before_the_job_runs_is_not_an_error(monkeypatch):
    """A job whose request has gone must succeed, not retry forever against
    something that no longer exists."""
    class DB:
        def get(self, model, key):
            return None

    assert intake.run_intake_screening(DB(), request_id="gone", actor_id="u-1") is None


def test_changing_the_parties_queues_a_fresh_screen_not_a_fold_in(monkeypatch):
    """Guards the idempotency key working against us: folding a re-screen into
    the original job would return the verdict for the parties the reviewer
    just replaced."""
    request = SimpleNamespace(id="req-1", org_id="org-1", screening=None)
    keys = []
    monkeypatch.setattr(
        job_service, "create_job",
        lambda db, **kw: keys.append(kw.get("idempotency_key")) or SimpleNamespace(id="j"),
    )

    intake.queue_screening(None, request, "u-1")
    intake.queue_screening(None, request, "u-1", idempotent=False)

    assert keys == ["intake_screening:req-1", None]


def test_the_worker_knows_how_to_run_the_job():
    """Guards the job type being queued but unhandled — which the dispatch
    chain answers with 'Unsupported job type', failing every screen."""
    assert "intake_screening" in inspect.getsource(_run_ai_job)
