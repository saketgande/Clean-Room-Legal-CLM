"""Drafting a suggested change for every playbook issue that has none (app/contract_files/issue_fixes.py).

The bug this exists to fix: the Review tab's "Draft fixes for all" sent ONE request for all the
issues, so its changes named no issue and landed as loose cards while every issue still read "no
change yet". The job makes one request per issue and ties each change to its issue.

What must hold:
* the request sentence is the one the Review tab's own "Draft fix with AI" button sends;
* an issue counts as "has a change" exactly as the page decides it (so the job never drafts a second
  change for an issue the page already shows one for, and never skips one the page shows as empty);
* one issue failing never stops the others; a model that finds nothing to change is an answer, not a
  failure; a provider that is down stops the job instead of failing every issue in turn;
* a job that is run again, or races a person clicking the button, does not draft twice;
* only a person who may redline can start it, and only an automatic start is switched off by the setting;
* a broker problem leaves the job queued (to be re-dispatched), not lost and not a 500;
* it starts after a review (the Playbooks run, the assistant's redline tool, the auto-review after
  drafting) and only when that review made redlines.

Runs the real routes and database (the seeded admin / legal / viewer users). Only the AI edit engine is
replaced: a fake that writes a ContractEdit the way tool_runtime._edit_contract does.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import app.contract_files.service as files_service
import app.models  # noqa: F401  (register every mapper)
from app.ai.tool_registry import PlaybookToolInput
from app.ai.tool_runtime import tool_runtime
from app.auth.models import User
from app.contract_files import issue_fixes, redline_edits
from app.contract_files.models import ContractEdit
from app.contracts.models import Contract
from app.core.config import settings
from app.core.database import SessionLocal, engine, utcnow
from app.core.enums import JobStatus
from app.core.models import ResourceTimelineEvent
from app.jobs.models import JobRun
from app.jobs.service import JobsService
from app.playbooks.models import Playbook, PlaybookDecision, PlaybookDeviation, PlaybookRun

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PASSWORD = "local-dev-password"
_TOKENS: dict[str, str] = {}


def _word(text: str) -> bytes:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---- a real contract with two versions, and the people to act on it ------------------------------


@pytest.fixture(scope="module")
def _contract_world():
    """One contract for the whole file, made through the real upload route (so it has real versions)
    and saved once more so there is an older version as well as the current one. One, because the
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
        data={"title": "Issue fix drafting test"},
        files={"file": ("t.docx", _word("1. Term. One year.\n2. Payment. Net 90."), DOCX)},
    )
    assert up.status_code in (200, 201, 202), up.text
    cid = up.json()["contract"]["id"]
    try:
        yield _setup(client, headers, login, cid)
    finally:
        _cleanup_rows(cid)
        _retire(cid)
        patch.undo()


@pytest.fixture
def world(_contract_world):
    """The shared contract, with whatever a test committed on it removed afterwards."""
    try:
        yield _contract_world
    finally:
        _cleanup_rows(_contract_world.cid)


def _setup(client, headers, login, cid):
    assert client.put(
        f"/api/v1/contracts/{cid}/text", headers=headers, json={"text": "1. Term. One year.\n2. Payment. Net 60."}
    ).status_code == 200

    db = SessionLocal()
    try:
        contract = db.get(Contract, cid)
        versions = db.scalars(
            select(issue_fixes_version_model()).where(issue_fixes_version_model().contract_id == cid)
        ).all()
        old = [v for v in versions if v.id != contract.current_authoritative_version_id]
        assert old, "the contract should have an older version"
        playbook = db.scalars(
            select(Playbook).where(Playbook.org_id == contract.org_id, Playbook.status == "published")
        ).first()
        if playbook is None:
            pytest.skip("no published playbook in this database")
        users = {
            email: db.scalar(select(User).where(User.email == email, User.org_id == contract.org_id))
            for email in ("admin@example.com", "legal1@example.com", "user1@example.com")
        }
        return SimpleNamespace(
            client=client,
            headers=headers,
            login=login,
            cid=cid,
            org_id=contract.org_id,
            current=contract.current_authoritative_version_id,
            old=old[0].id,
            playbook_id=playbook.id,
            playbook_version_id=playbook.current_version_id,
            admin=users["admin@example.com"].id,
            legal=users["legal1@example.com"].id,
            viewer=users["user1@example.com"].id,
        )
    finally:
        db.close()


def issue_fixes_version_model():
    from app.contract_files.models import ContractVersion

    return ContractVersion


def _cleanup_rows(cid: str) -> None:
    """Remove what a test committed through the routes (issues, runs, changes, jobs): the savepoint-rolled
    rows of the other tests never reach the database."""
    db = SessionLocal()
    try:
        fix_jobs = db.scalars(select(JobRun.id).where(JobRun.resource_id == cid, JobRun.job_type == issue_fixes.JOB_TYPE)).all()
        if fix_jobs:
            db.execute(delete(ResourceTimelineEvent).where(ResourceTimelineEvent.job_id.in_(fix_jobs)))
        deviations = db.scalars(select(PlaybookDeviation.id).where(PlaybookDeviation.contract_id == cid)).all()
        if deviations:
            db.execute(delete(PlaybookDecision).where(PlaybookDecision.playbook_deviation_id.in_(deviations)))
        db.execute(delete(ContractEdit).where(ContractEdit.contract_id == cid))
        db.execute(delete(PlaybookDeviation).where(PlaybookDeviation.contract_id == cid))
        db.execute(delete(PlaybookRun).where(PlaybookRun.contract_id == cid))
        if fix_jobs:
            db.execute(delete(JobRun).where(JobRun.id.in_(fix_jobs)))
        db.commit()
    finally:
        db.close()


def _retire(cid: str) -> None:
    """Retire the contract the way test_word_editor does: its queued jobs cancelled, the contract deleted."""
    db = SessionLocal()
    try:
        for job in db.scalars(select(JobRun).where(JobRun.resource_id == cid, JobRun.status == "queued")):
            job.status = "cancelled"
        db.get(Contract, cid).deleted_at = utcnow()
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


# ---- builders ----------------------------------------------------------------------------------------


def make_run(db: Session, w) -> PlaybookRun:
    run = PlaybookRun(
        org_id=w.org_id,
        playbook_id=w.playbook_id,
        playbook_version_id=w.playbook_version_id,
        contract_id=w.cid,
        contract_version_id=w.current,
        status="completed",
    )
    db.add(run)
    db.flush()
    return run


def make_issue(
    db: Session,
    w,
    run: PlaybookRun,
    *,
    issue="Payment terms are too long.",
    severity="medium",
    clause_type: str | None = "payment_terms",
    suggested_fix: str | None = None,
    quote: str | None = None,
    status: str = "open",
) -> PlaybookDeviation:
    row = PlaybookDeviation(
        org_id=w.org_id,
        playbook_run_id=run.id,
        contract_id=w.cid,
        severity=severity,
        clause_type=clause_type,
        issue=issue,
        suggested_fix=suggested_fix,
        citation={"quote": quote} if quote else None,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def make_edit(
    db: Session,
    w,
    *,
    issue: PlaybookDeviation | None = None,
    status: str = "proposed",
    base: str | None = None,
    edit_type: str = "replace",
    rationale: str | None = None,
) -> ContractEdit:
    citation: list = [{"type": "assistant_edit_version", "contract_version_id": f"proposal-{uuid.uuid4().hex[:8]}"}]
    if issue is not None:
        citation.append({"type": "playbook_deviation", "playbook_deviation_id": issue.id})
    edit = ContractEdit(
        org_id=w.org_id,
        contract_id=w.cid,
        contract_version_id=base or w.current,
        edit_type=edit_type,
        status=status,
        original_text="Net 60",
        replacement_text="Net 30",
        rationale=rationale,
        citation=citation,
    )
    db.add(edit)
    db.flush()
    return edit


def new_job(db: Session, w, *, actor: str | None = None, run_id: str | None = None) -> JobRun:
    """A job as the worker finds it: created, then flipped to RUNNING by jobs.tasks._run_ai_job."""
    job = JobsService(db).create_job(
        org_id=w.org_id,
        job_type=issue_fixes.JOB_TYPE,
        resource_type="contract",
        resource_id=w.cid,
        created_by_user_id=actor or w.legal,
        metadata={"trigger": "manual", "playbook_run_id": run_id},
    )
    job.status = JobStatus.RUNNING
    job.started_at = utcnow()
    job.attempt_count = 1
    db.commit()
    return job


class Engine:
    """Stands in for tool_runtime._edit_contract: what it returns and writes when it works, and
    whatever the test wants it to do on the Nth call (raise, or change the world)."""

    def __init__(self, w, monkeypatch, plan: dict | None = None):
        self.w = w
        self.calls: list[str] = []
        self.plan = plan or {}
        monkeypatch.setattr(tool_runtime, "_edit_contract", self)

    async def __call__(self, db, *, payload, user, session_id):
        index = len(self.calls)
        self.calls.append(payload.instructions)
        action = self.plan.get(index)
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            action()
        proposal = f"proposal-{uuid.uuid4().hex[:8]}"
        edit = ContractEdit(
            org_id=user.org_id,
            contract_id=payload.contract_id,
            contract_version_id=self.w.current,
            edit_type="replace",
            status="proposed",
            original_text="Net 60",
            replacement_text="Net 30",
            citation=[{"type": "assistant_edit_version", "contract_version_id": proposal}],
        )
        db.add(edit)
        db.flush()
        return {
            "status": "created",
            "base_version_id": self.w.current,
            "contract_version_id": proposal,
            "contract_edit_id": edit.id,
            "edits": 1,
        }


def run_job(db: Session, job: JobRun) -> JobRun:
    asyncio.run(issue_fixes.run_issue_fix_job(db, job))
    db.refresh(job)
    return job


def drafted_for(db: Session, w, issue: PlaybookDeviation) -> list[ContractEdit]:
    return [
        e
        for e in db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.cid)).all()
        if redline_edits.paired_deviation_id(e.citation) == issue.id
    ]


def contract_of(db: Session, w) -> Contract:
    return db.get(Contract, w.cid)


# ---- what is asked of the model ---------------------------------------------------------------------------


def test_the_request_is_the_sentence_the_review_tab_button_sends():
    # Mirrors devInstruction in the contract page (_clm-workspace.tsx): change both together.
    full = SimpleNamespace(
        clause_type="limitation_of_liability",
        issue="Liability is uncapped.",
        suggested_fix="Cap it at 12 months of fees.",
        citation={"quote": "Supplier's liability is unlimited."},
    )
    assert issue_fixes.fix_instruction(full) == (
        "Fix the limitation of liability issue as a tracked change. Issue: Liability is uncapped."
        " Required change: Cap it at 12 months of fees."
        ' The existing clause reads: "Supplier\'s liability is unlimited."'
    )
    bare = SimpleNamespace(clause_type=None, issue="No governing law.", suggested_fix=None, citation=None)
    assert issue_fixes.fix_instruction(bare) == "Fix the clause issue as a tracked change. Issue: No governing law."
    odd_citation = SimpleNamespace(clause_type="term", issue="Too long.", suggested_fix="", citation={"type": "text_match"})
    assert issue_fixes.fix_instruction(odd_citation) == "Fix the term issue as a tracked change. Issue: Too long."


def test_a_very_long_issue_is_cut_to_what_the_route_accepts():
    long_issue = SimpleNamespace(clause_type="x", issue="a" * 9000, suggested_fix=None, citation=None)
    assert len(issue_fixes.fix_instruction(long_issue)) <= 3900  # the ai-redline route takes 4000


# ---- which issues still need a change ----------------------------------------------------------------------


def test_an_issue_with_no_change_is_listed_and_the_ones_the_page_shows_a_change_for_are_not(db, world):
    w = world
    run = make_run(db, w)
    bare = make_issue(db, w, run, issue="Bare issue")
    tagged = make_issue(db, w, run, issue="Tagged issue")
    legacy = make_issue(db, w, run, issue="Legacy issue")
    legacy_other_text = make_issue(db, w, run, issue="Legacy but different text")
    closed = make_issue(db, w, run, issue="Closed issue", status="accepted")
    needs_review = make_issue(db, w, run, issue="Needs review", status="needs_review")
    make_edit(db, w, issue=tagged)
    make_edit(db, w, edit_type="playbook_redline", rationale="Legacy issue")  # saved before edits named their issue
    make_edit(db, w, edit_type="playbook_redline", rationale="something else")
    db.commit()

    listed = {d.id for d in issue_fixes.issues_needing_change(db, contract=contract_of(db, w))}

    assert listed == {bare.id, legacy_other_text.id, needs_review.id}
    assert tagged.id not in listed and legacy.id not in listed and closed.id not in listed


def test_a_rejected_change_counts_only_while_it_is_for_the_current_version(db, world):
    # The page offers a new fix when the only change was rejected for an OLDER version, and shows
    # "rejected" when it was rejected for the version in front of the person (frontend buildSuggestions).
    w = world
    run = make_run(db, w)
    rejected_now = make_issue(db, w, run, issue="Rejected on the current version")
    rejected_before = make_issue(db, w, run, issue="Rejected on an older version")
    proposed_before = make_issue(db, w, run, issue="Proposed on an older version")
    make_edit(db, w, issue=rejected_now, status="rejected", base=w.current)
    make_edit(db, w, issue=rejected_before, status="rejected", base=w.old)
    make_edit(db, w, issue=proposed_before, status="proposed", base=w.old)
    db.commit()

    listed = {d.id for d in issue_fixes.issues_needing_change(db, contract=contract_of(db, w))}

    assert listed == {rejected_before.id}


def test_a_contract_with_no_current_version_does_not_treat_every_rejection_as_old(db, world):
    w = world
    run = make_run(db, w)
    issue = make_issue(db, w, run)
    make_edit(db, w, issue=issue, status="rejected", base=w.old)
    db.commit()
    edits = db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.cid)).all()

    assert issue_fixes._has_change(issue, list(edits), None) is True


def test_the_most_severe_issues_come_first_and_one_review_can_be_asked_for_alone(db, world):
    w = world
    run = make_run(db, w)
    other_run = make_run(db, w)
    low = make_issue(db, w, run, issue="low", severity="low")
    critical = make_issue(db, w, run, issue="critical", severity="critical")
    medium = make_issue(db, w, run, issue="medium", severity="medium")
    high = make_issue(db, w, run, issue="high", severity="high")
    elsewhere = make_issue(db, w, other_run, issue="from another review", severity="critical")
    db.commit()
    contract = contract_of(db, w)

    ordered = [d.id for d in issue_fixes.issues_needing_change(db, contract=contract)]
    assert ordered[:2] == sorted([critical.id, high.id, elsewhere.id], key=ordered.index)[:2]
    assert ordered.index(medium.id) > max(ordered.index(critical.id), ordered.index(high.id))
    assert ordered.index(low.id) == len(ordered) - 1

    only_one_review = [d.id for d in issue_fixes.issues_needing_change(db, contract=contract, playbook_run_id=run.id)]
    assert set(only_one_review) == {low.id, critical.id, medium.id, high.id}
    assert only_one_review == [critical.id, high.id, medium.id, low.id]  # ties keep the order they were found in


# ---- starting the job ------------------------------------------------------------------------------------------


def test_asking_by_hand_queues_one_job_and_asking_again_returns_it(db, world):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    make_issue(db, w, run, issue="Second")
    db.commit()
    contract = contract_of(db, w)
    legal = db.get(User, w.legal)

    first = issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, automatic=False)
    again = issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, automatic=False)

    assert first is not None and first.status == JobStatus.QUEUED
    assert first.celery_task_id, "it was handed to the worker"
    assert first.metadata_json["trigger"] == "manual"
    assert first.metadata_json["total"] == 2, "the tab can say '0 of 2' before the worker starts"
    assert again.id == first.id
    assert db.scalars(select(JobRun).where(JobRun.resource_id == w.cid, JobRun.job_type == issue_fixes.JOB_TYPE)).all() == [first]


def test_nothing_is_queued_when_no_issue_needs_a_change(db, world):
    w = world
    run = make_run(db, w)
    issue = make_issue(db, w, run)
    make_edit(db, w, issue=issue)
    db.commit()

    job = issue_fixes.enqueue_issue_fix_drafting(
        db, contract=contract_of(db, w), actor=db.get(User, w.legal), automatic=False
    )

    assert job is None
    assert db.scalars(select(JobRun).where(JobRun.resource_id == w.cid, JobRun.job_type == issue_fixes.JOB_TYPE)).all() == []


def test_only_a_person_who_may_redline_can_start_it(db, world):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    db.commit()
    viewer = db.get(User, w.viewer)
    assert issue_fixes.can_draft(db, user=viewer) is False

    assert issue_fixes.enqueue_issue_fix_drafting(db, contract=contract_of(db, w), actor=viewer, automatic=False) is None
    assert issue_fixes.enqueue_issue_fix_drafting(db, contract=contract_of(db, w), actor=viewer, automatic=True) is None


def test_the_setting_switches_off_the_automatic_start_only(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    db.commit()
    contract, legal = contract_of(db, w), db.get(User, w.legal)
    monkeypatch.setattr(settings, "auto_draft_issue_fixes", False)

    assert issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, playbook_run_id=run.id, automatic=True) is None
    by_hand = issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, automatic=False)
    assert by_hand is not None, "a person clicking the button is not blocked by the setting"


def test_an_automatic_start_takes_only_that_reviews_issues_and_one_review_gets_one_job(db, world):
    w = world
    run, other = make_run(db, w), make_run(db, w)
    mine = make_issue(db, w, run, issue="from this review")
    make_issue(db, w, other, issue="from an earlier review")
    db.commit()
    contract, legal = contract_of(db, w), db.get(User, w.legal)

    job = issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, playbook_run_id=run.id, automatic=True)
    assert job.idempotency_key == f"{issue_fixes.JOB_TYPE}:{run.id}"
    assert job.metadata_json["playbook_run_id"] == run.id and job.metadata_json["total"] == 1

    # The job finishes; the same review being reported again must not queue it (or run it) again.
    job.status = JobStatus.SUCCEEDED
    db.commit()
    task_id = job.celery_task_id
    again = issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, playbook_run_id=run.id, automatic=True)
    assert again.id == job.id and again.status == JobStatus.SUCCEEDED and again.celery_task_id == task_id

    assert [d.id for d in issue_fixes.issues_needing_change(db, contract=contract, playbook_run_id=run.id)] == [mine.id]


def test_a_broker_problem_leaves_the_job_queued_for_the_periodic_re_dispatch(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    db.commit()

    def broker_down(self, *, job):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(JobsService, "dispatch_job", broker_down)
    job = issue_fixes.enqueue_issue_fix_drafting(
        db, contract=contract_of(db, w), actor=db.get(User, w.legal), automatic=False
    )

    assert job is not None, "the person still gets a job to watch, not an error"
    saved = db.get(JobRun, job.id)
    assert saved.status == JobStatus.QUEUED and saved.celery_task_id is None


def test_a_job_whose_worker_is_long_gone_does_not_block_a_new_one(db, world):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    db.commit()
    contract, legal = contract_of(db, w), db.get(User, w.legal)
    first = issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, automatic=False)
    first.created_at = utcnow() - issue_fixes.STALE_AFTER - timedelta(minutes=1)
    db.commit()

    assert issue_fixes.job_status(first)["status"] == "failed", "the tab must not show it as running forever"
    second = issue_fixes.enqueue_issue_fix_drafting(db, contract=contract, actor=legal, automatic=False)
    assert second.id != first.id


# ---- the job -------------------------------------------------------------------------------------------------------


def test_each_issue_gets_its_own_request_and_its_change_is_tied_to_it(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    a = make_issue(db, w, run, issue="Payment terms are too long.", severity="high", suggested_fix="Net 30.")
    b = make_issue(db, w, run, issue="No cap on liability.", severity="medium", clause_type="limitation_of_liability")
    c = make_issue(db, w, run, issue="Term auto-renews.", severity="low", clause_type="term", quote="renews automatically")
    db.commit()
    engine_ = Engine(w, monkeypatch)
    job = new_job(db, w)

    run_job(db, job)

    assert len(engine_.calls) == 3, "one request per issue, not one for all"
    assert engine_.calls == [issue_fixes.fix_instruction(d) for d in (a, b, c)], "most severe first"
    for issue in (a, b, c):
        mine = drafted_for(db, w, issue)
        assert len(mine) == 1, "the change names its issue, so the Review tab shows it under the issue"
    assert issue_fixes.issues_needing_change(db, contract=contract_of(db, w)) == []
    assert job.status == JobStatus.SUCCEEDED and job.progress == 100 and job.finished_at is not None
    status = issue_fixes.job_status(job)
    assert (status["total"], status["done"], status["drafted"], status["failed"], status["no_change"]) == (3, 3, 3, 0, 0)
    assert [i["outcome"] for i in job.metadata_json["issues"]] == ["drafted"] * 3
    event = db.scalar(select(ResourceTimelineEvent).where(ResourceTimelineEvent.job_id == job.id))
    assert event.event_type == "contract.issue_fixes_drafted" and event.details["drafted"] == 3


def test_one_issue_failing_does_not_stop_the_others_and_is_counted(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    a, b, c = (make_issue(db, w, run, issue=f"Issue {n}") for n in "ABC")
    db.commit()
    Engine(w, monkeypatch, plan={1: RuntimeError("provider returned garbage")})
    job = new_job(db, w)

    run_job(db, job)

    assert job.status == JobStatus.SUCCEEDED, "two of three worked"
    assert [len(drafted_for(db, w, d)) for d in (a, b, c)] == [1, 0, 1]
    status = issue_fixes.job_status(job)
    assert (status["drafted"], status["failed"], status["done"]) == (2, 1, 3)
    assert "garbage" not in repr(status), "an internal error text is never sent to the page"
    assert [d.id for d in issue_fixes.issues_needing_change(db, contract=contract_of(db, w))] == [b.id]


def test_a_model_that_finds_nothing_to_change_is_an_answer_not_a_failure(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run, issue="A")
    make_issue(db, w, run, issue="B")
    db.commit()
    nothing = HTTPException(422, "No specific language to revise")
    Engine(w, monkeypatch, plan={0: nothing, 1: nothing})
    job = new_job(db, w)

    run_job(db, job)

    assert job.status == JobStatus.SUCCEEDED, "nothing went wrong"
    status = issue_fixes.job_status(job)
    assert (status["no_change"], status["failed"], status["drafted"]) == (2, 0, 0)


def test_when_every_issue_errors_the_job_fails(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    for n in range(2):
        make_issue(db, w, run, issue=f"Issue {n}")
    db.commit()
    Engine(w, monkeypatch, plan={0: RuntimeError("down"), 1: HTTPException(502, "bad gateway")})
    job = new_job(db, w)

    run_job(db, job)

    assert job.status == JobStatus.FAILED
    assert issue_fixes.job_status(job)["failed"] == 2
    assert job.error_message and "down" not in job.error_message


def test_a_provider_that_is_down_stops_the_job_instead_of_failing_every_issue(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    for n in range(6):
        make_issue(db, w, run, issue=f"Issue {n}")
    db.commit()
    engine_ = Engine(w, monkeypatch, plan={n: RuntimeError("401 invalid key") for n in range(6)})
    job = new_job(db, w)

    run_job(db, job)

    assert len(engine_.calls) == issue_fixes.MAX_CONSECUTIVE_FAILURES, "stops asking once it is clear nothing will work"
    status = issue_fixes.job_status(job)
    assert status["stopped_early"] is True and status["failed"] == 3 and status["total"] == 6
    assert job.status == JobStatus.FAILED


def test_a_success_between_failures_keeps_the_job_going(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    for n in range(6):
        make_issue(db, w, run, issue=f"Issue {n}")
    db.commit()
    boom = RuntimeError("blip")
    engine_ = Engine(w, monkeypatch, plan={0: boom, 1: boom, 3: boom, 4: boom})
    job = new_job(db, w)

    run_job(db, job)

    assert len(engine_.calls) == 6, "never three failures in a row"
    status = issue_fixes.job_status(job)
    assert (status["drafted"], status["failed"], status["stopped_early"]) == (2, 4, False)


def test_only_the_configured_number_of_issues_is_taken_on_the_rest_are_left_for_a_person(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    low = make_issue(db, w, run, issue="low", severity="low")
    critical = make_issue(db, w, run, issue="critical", severity="critical")
    medium = make_issue(db, w, run, issue="medium", severity="medium")
    db.commit()
    monkeypatch.setattr(settings, "auto_draft_max_issues", 2)
    engine_ = Engine(w, monkeypatch)
    job = new_job(db, w)

    run_job(db, job)

    assert len(engine_.calls) == 2
    status = issue_fixes.job_status(job)
    assert (status["total"], status["left_for_later"], status["drafted"]) == (2, 1, 2)
    assert [d.id for d in issue_fixes.issues_needing_change(db, contract=contract_of(db, w))] == [low.id]
    assert len(drafted_for(db, w, critical)) == 1 and len(drafted_for(db, w, medium)) == 1


def test_it_stops_starting_new_issues_when_its_time_is_up_and_keeps_what_it_did(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    db.commit()
    monkeypatch.setattr(issue_fixes, "JOB_BUDGET_SECONDS", -1)
    engine_ = Engine(w, monkeypatch)
    job = new_job(db, w)

    run_job(db, job)

    assert engine_.calls == []
    status = issue_fixes.job_status(job)
    assert status["stopped_early"] is True and status["done"] == 0 and status["total"] == 1


def test_an_issue_closed_or_given_a_change_while_the_job_runs_is_skipped(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    a, b, c = (make_issue(db, w, run, issue=f"Issue {n}") for n in "ABC")
    db.commit()

    def person_decides_b():
        b.status = "rejected"  # someone closed the issue while the first request was running
        db.flush()

    engine_ = Engine(w, monkeypatch, plan={0: person_decides_b})
    job = new_job(db, w)
    run_job(db, job)

    assert len(engine_.calls) == 2, "B was not drafted for"
    outcomes = {i["id"]: i["outcome"] for i in job.metadata_json["issues"]}
    assert outcomes == {a.id: "drafted", b.id: "skipped", c.id: "drafted"}


def test_a_change_a_person_asked_for_meanwhile_is_not_drafted_a_second_time(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run, issue="A")
    b = make_issue(db, w, run, issue="B")
    db.commit()
    engine_ = Engine(w, monkeypatch, plan={0: lambda: make_edit(db, w, issue=b)})  # clicked "Draft fix with AI" on B
    job = new_job(db, w)

    run_job(db, job)

    assert len(engine_.calls) == 1
    assert len(drafted_for(db, w, b)) == 1


def test_running_it_again_drafts_nothing_new(db, world, monkeypatch):
    # A redelivered task, or a reclaimed job whose worker died after saving some issues.
    w = world
    run = make_run(db, w)
    for n in range(3):
        make_issue(db, w, run, issue=f"Issue {n}")
    db.commit()
    engine_ = Engine(w, monkeypatch)
    run_job(db, new_job(db, w))
    assert len(engine_.calls) == 3

    second = run_job(db, new_job(db, w))

    assert len(engine_.calls) == 3, "no second request for an issue that has its change"
    assert second.status == JobStatus.SUCCEEDED and issue_fixes.job_status(second)["total"] == 0


def test_a_cancelled_job_stays_cancelled_and_stops_after_the_issue_in_hand(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    for n in range(3):
        make_issue(db, w, run, issue=f"Issue {n}")
    db.commit()
    job = new_job(db, w)

    def cancel():
        job.status = JobStatus.CANCELLED
        db.commit()

    engine_ = Engine(w, monkeypatch, plan={0: cancel})

    run_job(db, job)

    assert len(engine_.calls) == 1
    assert job.status == JobStatus.CANCELLED


def test_the_job_re_checks_that_its_starter_may_redline(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    db.commit()
    engine_ = Engine(w, monkeypatch)
    job = new_job(db, w, actor=w.viewer)  # permissions can change between the click and the worker

    run_job(db, job)

    assert engine_.calls == []
    assert job.status == JobStatus.FAILED and "may not redline" in job.error_message


def test_a_deleted_contract_is_not_worked_on(db, world, monkeypatch):
    w = world
    run = make_run(db, w)
    make_issue(db, w, run)
    contract_of(db, w).deleted_at = utcnow()
    db.commit()
    Engine(w, monkeypatch)
    job = new_job(db, w)

    with pytest.raises(RuntimeError, match="Contract not found"):
        asyncio.run(issue_fixes.run_issue_fix_job(db, job))


# ---- the routes --------------------------------------------------------------------------------------------------------


def commit_issues(w, count: int, *, with_change: int = 0) -> list[str]:
    db = SessionLocal()
    try:
        run = make_run(db, w)
        ids = []
        for n in range(count):
            issue = make_issue(db, w, run, issue=f"Route issue {n}")
            ids.append(issue.id)
            if n < with_change:
                make_edit(db, w, issue=issue)
        db.commit()
        return ids
    finally:
        db.close()


def test_the_page_can_start_the_drafting_and_watch_it(world):
    w = world
    url = f"/api/v1/contracts/{w.cid}/edits/draft-issue-fixes"
    assert w.client.get(url, headers=w.headers).json() is None, "nothing has run yet"
    nothing = w.client.post(url, headers=w.headers)
    assert nothing.status_code == 202 and nothing.json() == {"job": None, "reason": "nothing_to_draft"}

    commit_issues(w, 3, with_change=1)
    started = w.client.post(url, headers=w.headers)
    assert started.status_code == 202
    job = started.json()["job"]
    assert job["status"] == "queued" and job["total"] == 2 and job["trigger"] == "manual"
    assert set(job) == {
        "job_id", "status", "trigger", "total", "done", "drafted", "no_change", "failed",
        "left_for_later", "stopped_early", "started_at", "finished_at",
    }, "the page's type for this must match"

    again = w.client.post(url, headers=w.headers).json()["job"]
    assert again["job_id"] == job["job_id"], "clicking twice is one job"
    assert w.client.get(url, headers=w.headers).json()["job_id"] == job["job_id"]


def test_drafting_routes_need_the_redline_permission(world):
    w = world
    viewer = w.login("user1@example.com")
    url = f"/api/v1/contracts/{w.cid}/edits/draft-issue-fixes"

    assert w.client.get(url, headers=viewer).status_code == 403
    assert w.client.post(url, headers=viewer).status_code == 403


def test_a_contract_someone_else_cannot_see_is_not_found(world):
    w = world
    url = "/api/v1/contracts/00000000-0000-0000-0000-000000000000/edits/draft-issue-fixes"
    assert w.client.get(url, headers=w.headers).status_code == 404
    assert w.client.post(url, headers=w.headers).status_code == 404


def test_the_single_issue_button_still_ties_its_change_to_the_issue(world, monkeypatch):
    # POST /edits/ai-redline now goes through the same function the job uses.
    w = world
    (issue_id,) = commit_issues(w, 1)
    engine_ = Engine(w, monkeypatch)
    url = f"/api/v1/contracts/{w.cid}/edits/ai-redline"

    res = w.client.post(url, headers=w.headers, json={"instructions": "Fix it.", "playbook_deviation_id": issue_id})

    assert res.status_code == 201 and res.json()["edits"] == 1
    assert engine_.calls == ["Fix it."]
    db = SessionLocal()
    try:
        tagged = [e for e in db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.cid)) if redline_edits.paired_deviation_id(e.citation) == issue_id]
        assert len(tagged) == 1
    finally:
        db.close()


def test_the_single_issue_button_without_an_issue_makes_an_untied_change(world, monkeypatch):
    w = world
    Engine(w, monkeypatch)

    res = w.client.post(f"/api/v1/contracts/{w.cid}/edits/ai-redline", headers=w.headers, json={"instructions": "Tighten the term."})

    assert res.status_code == 201
    db = SessionLocal()
    try:
        edits = db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.cid)).all()
        assert len(edits) == 1 and redline_edits.paired_deviation_id(edits[0].citation) is None
    finally:
        db.close()


def test_the_single_issue_button_refuses_an_unknown_or_closed_issue(world, monkeypatch):
    w = world
    (issue_id,) = commit_issues(w, 1)
    db = SessionLocal()
    db.get(PlaybookDeviation, issue_id).status = "accepted"
    db.commit()
    db.close()
    engine_ = Engine(w, monkeypatch)
    url = f"/api/v1/contracts/{w.cid}/edits/ai-redline"

    unknown = w.client.post(url, headers=w.headers, json={"instructions": "x", "playbook_deviation_id": "nope"})
    closed = w.client.post(url, headers=w.headers, json={"instructions": "x", "playbook_deviation_id": issue_id})

    assert (unknown.status_code, closed.status_code) == (404, 409)
    assert engine_.calls == [], "refused before any AI request"


# ---- it starts after a review ----------------------------------------------------------------------------------------


@pytest.fixture
def started(monkeypatch):
    """Records every start_after_review call and lets it run for real."""
    calls: list[dict] = []
    real = issue_fixes.start_after_review

    def spy(db, *, contract, actor_user_id, playbook_run_id):
        calls.append({"contract": contract.id, "actor": actor_user_id, "run": playbook_run_id})
        return real(db, contract=contract, actor_user_id=actor_user_id, playbook_run_id=playbook_run_id)

    monkeypatch.setattr(issue_fixes, "start_after_review", spy)
    return calls


def _post_run(w, **body):
    return w.client.post(
        f"/api/v1/playbooks/{w.playbook_id}/runs", headers=w.headers, json={"contract_id": w.cid, **body}
    )


def test_a_playbook_run_that_makes_redlines_starts_the_drafting(world, started):
    w = world
    res = _post_run(w, create_redline=True)
    assert res.status_code == 201, res.text
    assert started == [{"contract": w.cid, "actor": w.admin, "run": res.json()["id"]}]


def test_a_playbook_run_without_redlines_or_a_trial_run_does_not(world, started):
    w = world
    assert _post_run(w, create_redline=False).status_code == 201
    assert _post_run(w, create_redline=True, test_mode=True).status_code == 201
    assert started == []


def test_the_assistants_redline_tool_starts_it_and_the_review_only_tool_does_not(world, started):
    w = world
    db = SessionLocal()
    try:
        user = db.get(User, w.admin)
        payload = PlaybookToolInput(contract_id=w.cid, playbook_id=w.playbook_id)
        review = asyncio.run(tool_runtime._run_playbook_review(db, payload=payload, user=user, session_id="t", create_redline=False))
        assert started == [] and "issue_fix_job_id" not in review

        redline = asyncio.run(tool_runtime._run_playbook_review(db, payload=payload, user=user, session_id="t", create_redline=True))
        db.commit()
        assert started == [{"contract": w.cid, "actor": w.admin, "run": redline["playbook_run_id"]}]
    finally:
        db.close()


def test_the_auto_review_after_drafting_starts_it_for_a_real_person_only(world, started, monkeypatch):
    from app.playbooks.service import PlaybooksService

    w = world
    # Which playbook suits a contract is not what this checks (that matches on the contract's type).
    monkeypatch.setattr(PlaybooksService, "pick_playbook_for_contract", lambda self, **k: self.db.get(Playbook, w.playbook_id))
    db = SessionLocal()
    try:
        service = PlaybooksService(db)
        contract = db.get(Contract, w.cid)
        result = asyncio.run(service.auto_review_contract(contract=contract, actor_user_id=w.admin, create_redline=True))
        assert result["ran"] is True and result["playbook_run_id"], result
        assert started == [{"contract": w.cid, "actor": w.admin, "run": result["playbook_run_id"]}]

        started.clear()
        asyncio.run(service.auto_review_contract(contract=db.get(Contract, w.cid), actor_user_id=w.admin, create_redline=False))
        asyncio.run(service.auto_review_contract(contract=db.get(Contract, w.cid), actor_user_id=None, create_redline=True))
        assert started == [], "no redlines asked for, or nobody to act as: nothing starts"
    finally:
        db.close()


def test_a_failure_while_queueing_never_breaks_the_review(db, world, monkeypatch):
    w = world

    def explode(*a, **k):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(issue_fixes, "enqueue_issue_fix_drafting", explode)

    assert issue_fixes.start_after_review(db, contract=contract_of(db, w), actor_user_id=w.legal, playbook_run_id="r") is None
    assert issue_fixes.start_after_review(db, contract=contract_of(db, w), actor_user_id=None, playbook_run_id="r") is None
    assert issue_fixes.start_after_review(db, contract=contract_of(db, w), actor_user_id=w.legal, playbook_run_id=None) is None


def test_the_job_type_is_known_to_the_worker():
    import inspect

    from app.jobs import tasks

    source = inspect.getsource(tasks._run_ai_job)
    assert f'"{issue_fixes.JOB_TYPE}"' in source and "run_issue_fix_job" in source


# ---- the whole chain, on real rows -----------------------------------------------------------------------------------


def test_after_a_review_only_the_issues_the_playbook_had_no_wording_for_are_left_to_the_job(world, monkeypatch):
    """The Playbooks run, then the job it queues, then the Review tab's question: is anything left with no change?

    One issue is backed by a playbook rule with document wording (the run writes its change itself); the other has no rule,
    so nothing could be written for it. The job must take exactly that one, with its own request, and leave nothing behind."""
    from app.ai.controller import ai_controller

    w = world
    review = {
        "deviations": [
            {
                "rule_index": 1,
                "clause_type": "payment_terms",
                "severity": "high",
                "issue": "Payment terms of 60 days are longer than the playbook allows.",
                "original_text": "Net 60",
                "suggested_fix": "Payment is due within thirty (30) days of the invoice date.",
                "citations": [{"quote": "Net 60"}],
            },
            {
                "rule_index": None,
                "clause_type": "limitation_of_liability",
                "severity": "critical",
                "issue": "The contract has no limit on liability.",
                "suggested_fix": "Add a cap on liability.",
                "citations": [{"quote": "One year"}],
            },
        ],
        "summary": "Two issues.",
    }

    async def fake_review(db, **kwargs):
        assert kwargs["skill_name"] == "playbook_review"
        return review

    monkeypatch.setattr(ai_controller, "run_structured_skill", fake_review)

    res = _post_run(w, create_redline=True)
    assert res.status_code == 201, res.text
    run_id = res.json()["id"]

    db = SessionLocal()
    try:
        contract = db.get(Contract, w.cid)
        issues = {d.clause_type: d for d in db.scalars(select(PlaybookDeviation).where(PlaybookDeviation.playbook_run_id == run_id))}
        assert set(issues) == {"payment_terms", "limitation_of_liability"}, "the review recorded both issues"
        waiting = issue_fixes.issues_needing_change(db, contract=contract, playbook_run_id=run_id)
        assert [d.clause_type for d in waiting] == ["limitation_of_liability"], "the run itself wrote the payment change"
        assert len(drafted_for(db, w, issues["payment_terms"])) == 1

        job = db.scalar(select(JobRun).where(JobRun.resource_id == w.cid, JobRun.job_type == issue_fixes.JOB_TYPE))
        assert job is not None, "the review queued the drafting"
        assert job.status == JobStatus.QUEUED and job.created_by_user_id == w.admin
        assert job.metadata_json["playbook_run_id"] == run_id and job.metadata_json["total"] == 1

        engine_ = Engine(w, monkeypatch)
        job.status, job.started_at, job.attempt_count = JobStatus.RUNNING, utcnow(), 1  # what the worker does first
        db.commit()
        run_job(db, job)

        assert engine_.calls == [issue_fixes.fix_instruction(issues["limitation_of_liability"])]
        assert job.status == JobStatus.SUCCEEDED and issue_fixes.job_status(job)["drafted"] == 1
        assert len(drafted_for(db, w, issues["limitation_of_liability"])) == 1
        assert issue_fixes.issues_needing_change(db, contract=db.get(Contract, w.cid)) == [], "nothing left for a person to draft"
    finally:
        db.close()


# ---- a keyword scan is not a review (F3) -------------------------------------------------------------------------------


def _latest_event(db: Session, w, event_type: str) -> ResourceTimelineEvent:
    return db.scalars(
        select(ResourceTimelineEvent)
        .where(ResourceTimelineEvent.resource_id == w.cid, ResourceTimelineEvent.event_type == event_type)
        .order_by(ResourceTimelineEvent.created_at.desc())
    ).first()


def test_a_playbook_run_whose_ai_failed_is_a_keyword_scan_not_a_review(world, monkeypatch):
    """When the AI review raised, the keyword engine's findings were recorded as a 'succeeded' run, and
    the contract's 'AI review' checklist item turned done — the app acted as if a review it never did
    had passed."""
    from app.ai.controller import ai_controller

    w = world

    async def down(db, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(ai_controller, "run_structured_skill", down)
    res = _post_run(w, create_redline=False)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "keyword_only"
    assert body["model_name"] == "deterministic_playbook_engine" and "down" in body["error_message"]

    db = SessionLocal()
    try:
        assert db.get(PlaybookRun, body["id"]).status == "keyword_only"
        assert "did not run" in _latest_event(db, w, "playbook.run_completed").title
    finally:
        db.close()

    status = w.client.get(f"/api/v1/contracts/{w.cid}/review-status", headers=w.headers).json()
    assert status["last_review"] == {
        "status": "keyword_only", "source": "keyword", "contract_version_id": w.current,
        "on_current_text": True,
    }
    item = next(c for c in status["checklist"] if c["key"] == "ai_review")
    assert "keyword scan" in item["detail"]


def test_a_real_ai_review_after_a_fallback_counts_as_reviewed(world, monkeypatch):
    """A keyword_only run must not stop a later genuine review from counting: the newest run is the
    AI one, and it is 'succeeded'."""
    from app.ai.controller import ai_controller

    w = world

    async def down(db, **kwargs):
        raise RuntimeError("down")

    async def clean(db, **kwargs):
        return {"deviations": [], "summary": "Nothing to flag."}

    monkeypatch.setattr(ai_controller, "run_structured_skill", down)
    assert _post_run(w, create_redline=False).json()["status"] == "keyword_only"
    monkeypatch.setattr(ai_controller, "run_structured_skill", clean)
    res = _post_run(w, create_redline=False)
    assert res.json()["status"] == "succeeded", res.text

    status = w.client.get(f"/api/v1/contracts/{w.cid}/review-status", headers=w.headers).json()
    assert status["last_review"]["source"] == "ai" and status["last_review"]["status"] == "succeeded"
    item = next(c for c in status["checklist"] if c["key"] == "ai_review")
    assert item["status"] == "done" and "keyword" not in (item["detail"] or "")


def test_auto_review_timeline_does_not_claim_a_review_that_failed(world, monkeypatch):
    """The job wrote 'AI review complete' even when auto_review_contract reported the playbook review
    failed — or raised outright, which left `res` unbound."""
    from app.jobs.tasks import _maybe_auto_review
    from app.playbooks.service import PlaybooksService

    w = world
    job = SimpleNamespace(resource_id=w.cid, created_by_user_id=None)  # no user: skips the risk score

    def flag_pending(db):
        contract = db.get(Contract, w.cid)
        contract.metadata_json = {**(contract.metadata_json or {}), "auto_review_pending": True}
        db.commit()

    async def failed(self, **kwargs):
        return {"ran": True, "playbook_id": w.playbook_id, "playbook_run_id": "r", "error": "RuntimeError: down"}

    async def raised(self, **kwargs):
        raise RuntimeError("boom")

    async def ok(self, **kwargs):
        return {"ran": True, "playbook_id": w.playbook_id, "playbook_run_id": "r", "error": None}

    db = SessionLocal()
    try:
        titles = []
        for fake in (failed, raised, ok):
            monkeypatch.setattr(PlaybooksService, "auto_review_contract", fake)
            flag_pending(db)
            asyncio.run(_maybe_auto_review(db, job=job))
            titles.append(_latest_event(db, w, "contract.auto_review").title)
        assert titles[0].startswith("AI review incomplete") and titles[1].startswith("AI review incomplete")
        assert titles[2] == "AI review complete — risk + playbook deviations"
    finally:
        db.execute(
            delete(ResourceTimelineEvent).where(
                ResourceTimelineEvent.resource_id == w.cid, ResourceTimelineEvent.event_type == "contract.auto_review"
            )
        )
        db.commit()
        db.close()
