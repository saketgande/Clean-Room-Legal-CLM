"""Template checks as a recorded background job: single flight, the worker, check all, publish.

Failures this guards:
* two saves in a row paying for two reviews at once, or a waiting check never replaced by the newer
  wording (a stale review then deciding the status);
* a check that calls the AI for a template nothing reviews, or that falls back to a better-named
  playbook than the one the template is pinned to;
* the AI failing, being rate limited or being cut off leaving a check "running" forever, leaking a key
  into a message anyone can read, or being run again on its own (every review is paid for);
* a worker that died mid-review being handed the job again and paying for the review a second time;
* a check that creates a run, a finding or a contract on a contract that does not exist;
* a playbook publish checking templates another playbook reviews, or failing the publish itself;
* Check all starting a second review for a template already being checked.

The service is driven directly; the 403/404 endpoint cases belong to the routes' tests.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.ai.models import AISkillRun
from app.ai.schemas import PlaybookDeviationOutput, PlaybookReviewOutput
from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import JobStatus, PlaybookStatus
from app.core.models import AuditLog
from app.drafting_templates import checks, schemas
from app.drafting_templates.models import (
    DraftingTemplateCheck,
    DraftingTemplateType,
    DraftingTemplateVersion,
)
from app.drafting_templates.service import (
    SAMPLE_VALUES,
    TEMPLATES,
    DraftingTemplateService,
    body_sha256,
    default_body,
    get_type,
)
from app.intake import drafting
from app.jobs import tasks
from app.jobs.models import JobRun
from app.jobs.service import create_job
from app.organizations.models import Organization
from app.playbooks.models import (
    Playbook,
    PlaybookDeviation,
    PlaybookRule,
    PlaybookRun,
    PlaybookVersion,
)

SERVICES = "Services Agreement Playbook"  # the name match of the MSA template
NDA_PB = "NDA Playbook"
SAAS_PB = "Software / SaaS Agreement Playbook"

_RULES = [
    {"clause_type": "limitation_of_liability", "preferred_position": "Cap at twelve months of fees.",
     "rationale": "Bound our exposure."},
    {"clause_type": "payment_terms", "preferred_position": "Pay in arrears.", "rationale": "Cash flow.",
     "approval_required": True},
    {"clause_type": "termination", "preferred_position": "Either side on 30 days.", "rationale": "Exit."},
]


@pytest.fixture
def db():
    """A session whose work is rolled back at the end, so nothing the test writes outlives it."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


class _FakeReview:
    """Stands in for ai_controller.run_structured_skill and records how it was called."""

    def __init__(self, output: PlaybookReviewOutput | Exception | list):
        self.output = output
        self.calls: list[dict] = []

    async def __call__(self, db, **kwargs):
        self.calls.append(kwargs)
        out = self.output.pop(0) if isinstance(self.output, list) else self.output
        if isinstance(out, Exception):
            raise out
        return out


class _DuringReview(_FakeReview):
    """A review during which something else happens (a colleague saves): ``during`` runs first."""

    def __init__(self, output, during):
        super().__init__(output)
        self.during = during

    async def __call__(self, db, **kwargs):
        self.during()
        return await super().__call__(db, **kwargs)


def _ai(monkeypatch, fake: _FakeReview) -> _FakeReview:
    from app.ai.controller import ai_controller

    monkeypatch.setattr(ai_controller, "run_structured_skill", fake)
    return fake


def _world(db: Session, name: str = "Checker") -> User:
    org = Organization(id=new_uuid(), name="checks", slug=f"checks-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"u-{uuid.uuid4().hex[:8]}@example.com", full_name=name,
                hashed_password="hash", status="active")
    db.add(user)
    db.flush()
    return user


def _playbook(db: Session, user: User, name: str, rules: list[dict] | None = None, *,
              published: bool = True, deleted: bool = False) -> tuple[Playbook, PlaybookVersion]:
    state = PlaybookStatus.PUBLISHED if published else PlaybookStatus.DRAFT
    playbook = Playbook(org_id=user.org_id, name=name, status=state, created_by_user_id=user.id,
                        updated_by_user_id=user.id, deleted_at=utcnow() if deleted else None)
    db.add(playbook)
    db.flush()
    return playbook, _version(db, user, playbook, 1, _RULES if rules is None else rules, published=published)


def _version(db: Session, user: User, playbook: Playbook, number: int, rules: list[dict], *,
             published: bool = True) -> PlaybookVersion:
    version = PlaybookVersion(
        org_id=user.org_id, playbook_id=playbook.id, version_number=number,
        status=PlaybookStatus.PUBLISHED if published else PlaybookStatus.DRAFT,
        created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(version)
    db.flush()
    base = version.created_at
    for i, r in enumerate(rules):  # distinct created_at: the order the model numbers the rules in
        db.add(PlaybookRule(org_id=user.org_id, playbook_version_id=version.id, rule_type="standard_position",
                            risk_level="medium", created_at=base + timedelta(seconds=i),
                            created_by_user_id=user.id, updated_by_user_id=user.id, **r))
    db.flush()
    return version


def _pin(db: Session, user: User, key: str, playbook: Playbook, *, status: str = "active") -> None:
    spec = TEMPLATES[key]
    db.add(DraftingTemplateType(
        org_id=user.org_id, key=key, name=spec["name"], label=spec["label"], form_key=spec["form_key"],
        agreement_kind=spec["agreement_kind"], shipped=True, status=status, playbook_id=playbook.id))
    db.flush()


def _ready(db: Session, key: str = "msa", name: str = SERVICES):
    """An org whose ``key`` template is pinned to a published playbook."""
    user = _world(db)
    playbook, version = _playbook(db, user, name)
    _pin(db, user, key, playbook)
    return user, playbook, version


def _save_version(db: Session, user: User, key: str, body: str) -> DraftingTemplateVersion:
    latest = db.scalar(select(func.max(DraftingTemplateVersion.version)).where(
        DraftingTemplateVersion.org_id == user.org_id, DraftingTemplateVersion.key == key))
    row = DraftingTemplateVersion(org_id=user.org_id, key=key, version=(latest or 0) + 1, body=body,
                                  created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(row)
    db.flush()
    return row


def _raw_check(db: Session, user: User, key: str = "msa", *, state: str = "queued", unsaved: bool = False,
               body: str | None = None, version: int = 0, sha: str | None = None,
               trigger: str = "saved", **fields) -> DraftingTemplateCheck:
    """A check row written directly, for states the service would never produce by itself."""
    row = DraftingTemplateCheck(
        org_id=user.org_id, template_key=key, template_version=version,
        wording_sha256=sha or body_sha256(body if body is not None else default_body(key)),
        unsaved=unsaved, unsaved_body=body if unsaved else None, trigger=trigger, status=state,
        created_by_user_id=user.id, updated_by_user_id=user.id, **fields)
    db.add(row)
    db.flush()
    return row


def _set_running(db: Session, check: DraftingTemplateCheck) -> DraftingTemplateCheck:
    check.status, check.started_at = "running", utcnow()
    db.flush()
    return check


def _request(db: Session, user: User, key: str = "msa", trigger: str = "saved", **kw):
    return checks.request_check(db, org_id=user.org_id, key=key, trigger=trigger, actor_user_id=user.id, **kw)


def _job_of(db: Session, check: DraftingTemplateCheck) -> JobRun | None:
    return db.scalar(select(JobRun).where(JobRun.org_id == check.org_id,
                                          JobRun.idempotency_key == f"template_check:{check.id}"))


def _run(db: Session, check: DraftingTemplateCheck, *, attempt: int = 1) -> JobRun:
    """What the worker does with the check's job: ``_run_ai_job`` marks it running and counts the
    attempt, then hands it to the handler."""
    job = create_job(db, org_id=check.org_id, job_type=checks.JOB_TYPE, resource_type=checks.RESOURCE_TYPE,
                     resource_id=check.id, created_by_user_id=check.created_by_user_id,
                     idempotency_key=f"template_check:{check.id}")
    job.status, job.attempt_count, job.started_at = JobStatus.RUNNING, attempt, utcnow()
    db.flush()
    asyncio.run(checks.run_check_job(db, job))
    return job


def _of(db: Session, user: User, state: str, key: str = "msa") -> list[DraftingTemplateCheck]:
    return list(db.scalars(select(DraftingTemplateCheck).where(
        DraftingTemplateCheck.org_id == user.org_id, DraftingTemplateCheck.template_key == key,
        DraftingTemplateCheck.status == state)))


def _audit(db: Session, org_id: str, action: str) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == action)
                           .order_by(AuditLog.created_at, AuditLog.id)))


def _ai_runs(db: Session, org_id: str) -> int:
    return db.scalar(select(func.count()).select_from(AISkillRun).where(AISkillRun.org_id == org_id))


def _contract_side(db: Session) -> tuple[int, int, int]:
    return tuple(db.scalar(select(func.count()).select_from(m)) for m in (PlaybookRun, PlaybookDeviation, Contract))


def _bare_draft(key: str, body: str) -> str:
    return drafting.render_document(key, company=SAMPLE_VALUES["company"], counterparty=SAMPLE_VALUES["counterparty"],
                                    effective=SAMPLE_VALUES["effective_date"], fields={}, body=body, example=True)


def _deviation(**kw) -> PlaybookDeviationOutput:
    return PlaybookDeviationOutput(**{"rule_index": 1, "clause_type": "limitation_of_liability",
                                      "severity": "high", "issue": "The cap is unlimited.", **kw})


# --- single flight: requesting a check ------------------------------------------------------------


def test_a_request_queues_one_check_and_hands_it_to_the_worker(db):
    """The check must be a recorded row with its own job, or a closed tab loses it (FR-18/19), and
    asking must cost nothing until the worker runs it."""
    user, _, _ = _ready(db)

    check = _request(db, user)

    assert check.status == "queued" and check.trigger == "saved" and check.unsaved is False
    assert check.template_version == 0 and check.wording_sha256 == body_sha256(default_body("msa"))
    assert check.created_by_user_id == user.id
    job = _job_of(db, check)
    assert job is not None and job.job_type == "template_check" and job.celery_task_id
    assert job.resource_type == "drafting_template_check" and job.resource_id == check.id
    assert _ai_runs(db, user.org_id) == 0


def test_a_save_while_one_is_running_waits_and_a_later_one_replaces_it(db):
    """FR-15: at most one running and one waiting. A third request used to be able to queue a second
    waiting review; the newest wording must replace the waiting one, never sit behind it."""
    user, _, _ = _ready(db)
    first = _set_running(db, _request(db, user))
    _save_version(db, user, "msa", "Version one {company} and {counterparty}.")
    waiting = _request(db, user)
    _save_version(db, user, "msa", "Version two {company} and {counterparty}.")
    newest = _request(db, user)

    assert waiting.status == "superseded" and waiting.finished_at is not None
    assert newest.status == "queued" and newest.template_version == 2
    assert [c.id for c in _of(db, user, "queued")] == [newest.id] and _of(db, user, "running") == [first]
    assert _job_of(db, waiting) is None and _job_of(db, newest) is None, "nothing is dispatched behind a running check"
    (note,) = _audit(db, user.org_id, "drafting_template.check_superseded")
    assert note.after == {"check_id": waiting.id, "superseded_by_check_id": newest.id}


def test_the_database_itself_refuses_a_second_waiting_or_running_check(db):
    """The lock makes supersede-and-queue atomic, but 'never two waiting' has to be a database fact
    too, or one missed lock would let two reviews start for the same template."""
    user, _, _ = _ready(db)
    _raw_check(db, user, state="queued")
    _raw_check(db, user, state="running")
    for state in ("queued", "running"):
        with pytest.raises(IntegrityError), db.begin_nested():
            _raw_check(db, user, state=state)


def test_asking_again_for_the_same_waiting_wording_returns_that_check(db):
    """A waiting check resolves the playbook version when it runs, so a second request for the same
    wording (a publish, a click) must join it instead of queueing a duplicate paid review."""
    user, _, _ = _ready(db)
    _set_running(db, _request(db, user))
    _save_version(db, user, "msa", "Wording {company} and {counterparty}.")
    first = _request(db, user)

    again = _request(db, user, trigger="playbook_published")

    assert again.id == first.id and len(_of(db, user, "queued")) == 1


@pytest.mark.parametrize("trigger", ["manual", "check_all", "retry"])
def test_a_person_asking_for_the_wording_already_running_gets_that_check(db, trigger):
    """Clicking Check now while the same wording is being reviewed would pay for the same answer twice."""
    user, _, _ = _ready(db)
    running = _set_running(db, _request(db, user))

    got = _request(db, user, trigger=trigger)

    assert got.id == running.id and _of(db, user, "queued") == []


def test_an_automatic_request_while_the_same_wording_runs_queues_a_fresh_check(db):
    """A playbook publish during a running review: that review may have read the old version, so the
    publish needs its own check of the same wording."""
    user, _, _ = _ready(db)
    running = _set_running(db, _request(db, user))

    queued = _request(db, user, trigger="playbook_published", trigger_detail={"version_number": 2})

    assert queued.id != running.id and queued.status == "queued"
    assert queued.trigger_detail == {"version_number": 2}


def test_an_unsaved_check_is_refused_while_one_is_waiting(db):
    """FR-16: unsaved checks share the one waiting slot; a second would otherwise replace someone
    else's waiting review, silently."""
    user, _, _ = _ready(db)
    _set_running(db, _request(db, user))
    first = _request(db, user, trigger="manual", unsaved_body="Draft {company} and {counterparty}.")
    assert first.unsaved is True and first.unsaved_body == "Draft {company} and {counterparty}."
    assert first.wording_sha256 == body_sha256("Draft {company} and {counterparty}.")

    with pytest.raises(HTTPException) as exc:
        _request(db, user, trigger="manual", unsaved_body="Another {company} and {counterparty}.")

    assert exc.value.status_code == 409 and exc.value.detail == checks.QUEUE_FULL
    assert [c.id for c in _of(db, user, "queued")] == [first.id]


def test_a_save_replaces_a_waiting_unsaved_check(db):
    """The saved wording is what the status is about; a waiting unsaved check must not keep the slot."""
    user, _, _ = _ready(db)
    _set_running(db, _request(db, user))
    unsaved = _request(db, user, trigger="manual", unsaved_body="Draft {company} and {counterparty}.")
    _save_version(db, user, "msa", "Saved {company} and {counterparty}.")

    saved = _request(db, user)

    assert unsaved.status == "superseded" and saved.status == "queued" and saved.unsaved is False


def test_unsaved_wording_drafting_could_not_fill_is_refused_before_anything_is_written(db):
    """422 with no row, job or lock: a bad paste must cost nothing."""
    user, _, _ = _ready(db)

    with pytest.raises(HTTPException) as exc:
        _request(db, user, trigger="manual", unsaved_body="Dear {client_name},")

    assert exc.value.status_code == 422 and "Unknown placeholder {client_name}" in exc.value.detail
    assert _of(db, user, "queued") == []
    assert db.scalar(select(func.count()).select_from(JobRun).where(JobRun.org_id == user.org_id)) == 0


def test_an_unknown_template_is_a_404(db):
    user, _, _ = _ready(db)
    with pytest.raises(HTTPException) as exc:
        _request(db, user, key="customer")
    assert exc.value.status_code == 404


def test_no_playbook_means_no_check_no_job_and_no_ai_usage(db):
    """FR-9: a template nothing reviews must not start anything, or it would bill for a review that
    cannot be judged."""
    user = _world(db)  # no playbook at all

    assert _request(db, user, key="nda") is None

    assert _of(db, user, "queued", key="nda") == []
    assert db.scalar(select(func.count()).select_from(JobRun).where(JobRun.org_id == user.org_id)) == 0
    assert _ai_runs(db, user.org_id) == 0


@pytest.mark.parametrize("how", ["deleted", "unpublished"])
def test_a_pinned_playbook_that_is_gone_starts_nothing_and_is_not_replaced(db, how):
    """D-11: the pin stands. A tempting better-named playbook must not take over, and a pin that
    cannot review must not start a check that can only fail."""
    user = _world(db)
    pinned, _ = _playbook(db, user, SERVICES, deleted=(how == "deleted"), published=(how == "deleted"))
    _pin(db, user, "msa", pinned)
    _playbook(db, user, "Master Services Agreement Playbook")

    assert _request(db, user) is None

    assert _of(db, user, "queued") == []


def test_the_first_request_pins_an_unpinned_template_to_its_name_match(db):
    """D-11: the lazy pin happens inside the single-flight lock, audited as the system acting for the
    person, so two first uses cannot pin two playbooks."""
    user = _world(db)
    playbook, _ = _playbook(db, user, SERVICES)

    check = _request(db, user)

    assert check is not None
    assert get_type(db, user.org_id, "msa").playbook_id == playbook.id
    (pinned,) = _audit(db, user.org_id, "drafting_template.playbook_pinned")
    assert pinned.actor_user_id is None and pinned.metadata_json["on_behalf_of_user_id"] == user.id


def test_a_retired_type_is_not_checked_automatically_but_a_person_may_ask(db):
    """FR-54: a retired type is not checked on its own; asking by hand still works."""
    user = _world(db)
    playbook, _ = _playbook(db, user, SERVICES)
    _pin(db, user, "msa", playbook, status="retired")

    assert _request(db, user, trigger="playbook_published") is None
    assert _request(db, user, trigger="manual") is not None


def test_automatic_triggers_are_the_systems_on_behalf_of_the_person_and_manual_ones_are_theirs(db):
    """FR-70: the audit trail must say whether a person asked or the system acted for them."""
    user, _, _ = _ready(db)
    _request(db, user, trigger="saved")
    saved_row = _audit(db, user.org_id, "drafting_template.check_started")[-1]
    assert saved_row.actor_user_id is None
    assert saved_row.metadata_json == {"actor": "system", "on_behalf_of_user_id": user.id}
    assert saved_row.after["on_behalf"] is True and saved_row.after["trigger"] == "saved"

    other = _ready(db, key="nda", name=NDA_PB)[0]
    _request(db, other, key="nda", trigger="manual")
    manual_row = _audit(db, other.org_id, "drafting_template.check_started")[-1]
    assert manual_row.actor_user_id == other.id and manual_row.after["on_behalf"] is False


def test_asking_again_after_a_failed_check_of_this_wording_is_a_retry(db):
    """FR-8: Try again is a retry of that failure; a first request is a fresh look. The record keeps
    the difference."""
    user, _, version = _ready(db)
    _raw_check(db, user, state="failed", error_code="ai_unavailable", finished_at=utcnow(),
               playbook_version_id=version.id)

    assert _request(db, user, trigger="manual").trigger == "retry"


def test_a_manual_request_after_a_completed_check_is_not_a_retry(db):
    user, _, version = _ready(db)
    _raw_check(db, user, state="completed", findings=[], finished_at=utcnow(), playbook_version_id=version.id)

    assert _request(db, user, trigger="manual").trigger == "manual"


# --- the worker ---------------------------------------------------------------------------------


def test_a_completed_check_reads_the_template_as_a_bare_request_drafts_it(db, monkeypatch):
    """FR-12/22: the review must read what a request with no answers would draft, through the same
    review a contract gets, under the check's own resource, or it judges something else."""
    user, playbook, version = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(summary="One issue.", deviations=[_deviation()])))
    check = _request(db, user)

    job = _run(db, check)

    (call,) = fake.calls
    sent = call["contract_context"].text
    assert sent == _bare_draft("msa", default_body("msa")) and "{" not in sent
    assert call["skill_name"] == "playbook_review" and call["org_id"] == user.org_id
    assert call["created_by_user_id"] == user.id
    assert call["resource_type"] == "drafting_template_check" and call["resource_id"] == check.id
    assert call["input_payload"]["playbook_id"] == playbook.id
    assert check.status == "completed" and check.finished_at is not None and check.started_at is not None
    assert (check.playbook_id, check.playbook_name) == (playbook.id, playbook.name)
    assert (check.playbook_version_id, check.playbook_version_number, check.rule_count) == (version.id, 1, 3)
    assert check.summary == "One issue." and check.text_was_truncated is False
    assert check.text_length == len(sent) == check.read_length
    assert job.status == JobStatus.SUCCEEDED and job.finished_at is not None and job.error_message is None
    (done,) = _audit(db, user.org_id, "drafting_template.check_finished")
    assert done.actor_user_id is None and done.metadata_json["on_behalf_of_user_id"] == user.id
    assert done.after["finding_count"] == 1 and done.after["playbook_version_number"] == 1


def test_a_check_runs_the_unsaved_text_it_was_asked_about(db, monkeypatch):
    """FR-16: Check now on the editor's text must review that text, not the saved version."""
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    body = default_body("msa") + "\n\nSCHEDULE 9 for {counterparty}."
    check = _request(db, user, trigger="manual", unsaved_body=body)

    _run(db, check)

    assert fake.calls[0]["contract_context"].text == _bare_draft("msa", body)
    assert check.status == "completed" and check.unsaved is True


def test_a_saved_check_reads_the_version_it_was_queued_for_not_the_newest(db, monkeypatch):
    """A save after the check was queued makes a newer version; this check is about version 1 and must
    record that, or its result would be filed against the wrong wording."""
    user, _, _ = _ready(db)
    v1 = _save_version(db, user, "msa", "Version one {company} and {counterparty}.")
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _request(db, user)
    _save_version(db, user, "msa", "Version two {company} and {counterparty}.")

    _run(db, check)

    assert check.template_version == v1.version == 1
    assert fake.calls[0]["contract_context"].text == _bare_draft("msa", v1.body)


def test_findings_get_an_index_a_wording_flag_and_template_forms_of_their_quotes(db, monkeypatch):
    """FR-23/24/25: Apply must be able to find a quote in the template and write {codes}, never
    'Globex Corporation', into our paper; advice with a blank to fill must never be applied."""
    user, _, _ = _ready(db)
    quote = "Globex Corporation shall pay within 45 days, see {{note}}"
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(deviations=[
        _deviation(rule_index=2, clause_type="payment_terms", severity="low", original_text=quote,
                   suggested_fix="Globex Corporation shall pay within 30 days."),
        _deviation(rule_index=3, clause_type="termination", severity="critical",
                   suggested_fix="Add a termination right of [number] days."),
        _deviation(rule_index=None, clause_type="other", severity="medium", issue="Odd clause."),
    ])))
    check = _request(db, user)

    _run(db, check)

    pay, term, odd = check.findings
    assert [f["index"] for f in check.findings] == [0, 1, 2]
    assert (pay["rule_index"], pay["clause_type"]) == (2, "payment_terms")
    assert pay["approval_required"] is True and pay["severity"] == "high", "rule 2 needs approval, which raises it"
    assert pay["quote"] == quote
    assert pay["quote_template"] == "{counterparty} shall pay within 45 days, see {{{{note}}}}"
    assert pay["suggested_fix_template"] == "{counterparty} shall pay within 30 days."
    assert pay["suggestion_is_wording"] is True
    assert term["suggestion_is_wording"] is False, "a fill-in blank is advice, not wording"
    assert term["severity"] == "critical" and term["quote"] is None and term["quote_template"] is None
    assert odd["rule_index"] is None and odd["suggested_fix"] is None
    assert odd["suggested_fix_template"] is None and odd["suggestion_is_wording"] is False


def test_a_long_template_is_recorded_as_partly_read(db, monkeypatch):
    """FR-7: the review reads only the first MAX_FULL_TEXT_CHARS; the check must record how much, or
    a template whose tail was never read would look fully checked."""
    monkeypatch.setattr("app.ai.context.MAX_FULL_TEXT_CHARS", 500)
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _request(db, user)

    _run(db, check)

    full = len(_bare_draft("msa", default_body("msa")))
    assert full > 500 and len(fake.calls[0]["contract_context"].text) == 500
    assert check.status == "completed" and check.text_was_truncated is True
    assert (check.text_length, check.read_length) == (full, 500)


def test_a_check_makes_no_run_finding_or_contract(db, monkeypatch):
    """FR-21: there is no contract behind a template; a run, deviation or contract here would show up
    on real contracts' risk."""
    user, _, _ = _ready(db)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(deviations=[_deviation()])))
    check = _request(db, user)
    before = _contract_side(db)

    _run(db, check)

    assert _contract_side(db) == before


def test_the_real_controller_records_the_review_under_the_check_with_no_contract(db):
    """Runs run_structured_skill itself (mock model): the AI ledger row, citations and timeline all
    have to cope with a review that has no contract behind it."""
    user, _, _ = _ready(db)
    check = _request(db, user)

    _run(db, check)

    assert check.status == "completed" and check.findings == []
    run = db.scalar(select(AISkillRun).where(AISkillRun.org_id == user.org_id,
                                             AISkillRun.skill_name == "playbook_review"))
    assert run is not None and run.resource_type == "drafting_template_check" and run.resource_id == check.id
    assert run.contract_id is None and run.contract_version_id is None and run.text_snapshot_id is None


def _failed(db, check, job, code):
    assert check.status == "failed" and check.error_code == code and check.finished_at is not None
    assert job.status == JobStatus.FAILED and job.error_message == check.error_message
    (row,) = _audit(db, check.org_id, "drafting_template.check_failed")
    assert row.after["error_code"] == code and row.actor_user_id is None


def test_a_playbook_version_without_rules_fails_as_no_rules_without_calling_the_ai(db, monkeypatch):
    user = _world(db)
    playbook, _ = _playbook(db, user, SERVICES, rules=[])
    _pin(db, user, "msa", playbook)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _request(db, user)

    job = _run(db, check)

    _failed(db, check, job, "no_rules")
    assert "has no rules" in check.error_message and fake.calls == []


def test_a_pinned_playbook_deleted_after_queueing_fails_as_no_playbook_with_no_fallback(db, monkeypatch):
    """D-11 at the worker: the pin was valid when queued and gone when run. The worker must not reach
    for the better-named playbook sitting next to it."""
    user, pinned, _ = _ready(db)
    _playbook(db, user, "Master Services Agreement Playbook")
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _request(db, user)
    pinned.deleted_at = utcnow()
    db.flush()

    job = _run(db, check)

    _failed(db, check, job, "no_playbook")
    assert "which was deleted" in check.error_message and fake.calls == []
    assert check.playbook_id is None


def test_the_worker_never_pins_or_falls_back_to_a_name_match(db, monkeypatch):
    """A queued check whose template is unpinned (the pin was removed, or the row never existed) must
    fail, not pin itself: pinning is the request's job, under the lock, with an audit."""
    user = _world(db)
    _playbook(db, user, SERVICES)  # a perfect name match that must not be used
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _raw_check(db, user)

    job = _run(db, check)

    _failed(db, check, job, "no_playbook")
    assert "Choose a playbook for this template" in check.error_message and fake.calls == []
    assert db.scalar(select(func.count()).select_from(DraftingTemplateType)
                     .where(DraftingTemplateType.org_id == user.org_id)) == 0


def test_wording_drafting_could_not_fill_fails_as_unfillable_without_calling_the_ai(db, monkeypatch):
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _raw_check(db, user, unsaved=True, body="Dear {client_name},", sha=body_sha256("Dear {client_name},"))

    job = _run(db, check)

    _failed(db, check, job, "unfillable")
    assert "Unknown placeholder {client_name}" in check.error_message and fake.calls == []


def test_a_check_whose_wording_is_no_longer_what_it_was_queued_for_fails_as_wording_changed(db, monkeypatch):
    """A release that edits a shipped default between queueing and running: reviewing the new text
    under the old text's version would file the result against the wrong wording."""
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _raw_check(db, user, sha="0" * 64)

    job = _run(db, check)

    _failed(db, check, job, "wording_changed")
    assert fake.calls == []


def test_the_organisations_spending_limit_fails_the_check_as_cost_cap(db, monkeypatch):
    """FR-20: a 429 is an answer, not an outage; it must say so and not be retried."""
    user, _, version = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(HTTPException(429, "Daily AI token limit reached")))
    check = _request(db, user)

    job = _run(db, check)

    _failed(db, check, job, "cost_cap")
    assert "spending limit" in check.error_message and len(fake.calls) == 1
    assert check.playbook_version_id == version.id, "the failure still records what it was reviewing against"


@pytest.mark.parametrize("failure", [RuntimeError("anthropic key sk-secret-123 rejected"),
                                     HTTPException(502, "upstream said sk-secret-123")])
def test_an_ai_failure_fails_the_check_as_unavailable_and_leaks_nothing(db, monkeypatch, failure):
    """FR-8: any other failure reads the same to the person; the provider's own words (here a key) must
    never reach a message anyone can read, nor the job's error."""
    user, _, _ = _ready(db)
    _ai(monkeypatch, _FakeReview(failure))
    check = _request(db, user)

    job = _run(db, check)

    _failed(db, check, job, "ai_unavailable")
    shown = [check.error_message, job.error_message, job.error_stack,
             *(str(r.after) for r in _audit(db, user.org_id, "drafting_template.check_failed"))]
    assert not any("sk-secret" in (s or "") for s in shown)
    assert "could not run" in check.error_message


def test_a_job_handed_to_a_second_worker_is_interrupted_not_run_again(db, monkeypatch):
    """FR-20: the first worker may already have paid for the review. A reclaimed job must never pay
    twice: it fails as interrupted with no AI call."""
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _request(db, user)

    job = _run(db, check, attempt=2)

    _failed(db, check, job, "interrupted")
    assert fake.calls == []


def test_a_reclaimed_job_whose_first_worker_had_set_the_check_running_is_still_interrupted(db, monkeypatch):
    """The dead worker commits 'running' before the paid call, so the check is no longer queued when
    reclaim hands the job on. It must not be left running forever."""
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _set_running(db, _request(db, user))

    job = _run(db, check, attempt=2)

    _failed(db, check, job, "interrupted")
    assert fake.calls == []


def test_a_superseded_check_is_a_no_op_for_its_job(db, monkeypatch):
    """The waiting check was replaced before its worker started; running it would pay for a review of
    wording nobody wants."""
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _request(db, user)
    check.status = "superseded"
    db.flush()

    job = _run(db, check)

    assert job.status == JobStatus.SUCCEEDED and check.status == "superseded" and fake.calls == []


def test_a_failed_check_is_never_run_again_by_a_redelivered_job(db, monkeypatch):
    """FR-20: nothing retries a failure on its own. A broker redelivering the failed job (its status
    lets it proceed) must hit the already-handled guard instead of paying for a second review."""
    user, _, _ = _ready(db)
    fake = _ai(monkeypatch, _FakeReview(RuntimeError("provider down")))
    check = _request(db, user)
    first = _run(db, check)
    assert first.status == JobStatus.FAILED and len(fake.calls) == 1

    _run(db, check, attempt=2)

    assert len(fake.calls) == 1 and check.status == "failed" and check.error_code == "ai_unavailable"
    assert db.scalar(select(func.count()).select_from(JobRun).where(
        JobRun.org_id == user.org_id, JobRun.job_type == "template_check")) == 1


def test_a_failed_check_starts_no_job_of_its_own_again(db, monkeypatch):
    """Failure ends the check's story: no second JobRun appears for it and nothing is left waiting."""
    user, _, _ = _ready(db)
    _ai(monkeypatch, _FakeReview(RuntimeError("provider down")))
    check = _request(db, user)

    _run(db, check)

    assert _of(db, user, "queued") == [] and _of(db, user, "running") == []
    assert [j.resource_id for j in db.scalars(select(JobRun).where(JobRun.org_id == user.org_id))] == [check.id]


@pytest.mark.parametrize("outcome", [PlaybookReviewOutput(), RuntimeError("provider down")],
                         ids=["after a completed check", "after a failed check"])
def test_the_waiting_check_is_dispatched_when_the_running_one_finishes(db, monkeypatch, outcome):
    """FR-15: a check that waited behind a running one must start when it ends, whichever way it ends,
    or the newest wording is never reviewed."""
    user, _, _ = _ready(db)
    seen: dict = {}

    def colleague_saves():
        _save_version(db, user, "msa", "Version one {company} and {counterparty}.")
        seen["waiting"] = _request(db, user)
        seen["job_while_running"] = _job_of(db, seen["waiting"])

    _ai(monkeypatch, _DuringReview(outcome, colleague_saves))
    check = _request(db, user)

    _run(db, check)

    waiting = seen["waiting"]
    assert seen["job_while_running"] is None, "nothing is dispatched while a check is running"
    job = _job_of(db, waiting)
    assert job is not None and job.celery_task_id and waiting.status == "queued"


def test_nothing_is_dispatched_when_nothing_is_waiting(db, monkeypatch):
    user, _, _ = _ready(db)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    check = _request(db, user)

    _run(db, check)

    assert db.scalar(select(func.count()).select_from(JobRun).where(JobRun.org_id == user.org_id)) == 1


def test_the_worker_runs_template_check_jobs():
    """The single dispatch chain answered 'Unsupported job type' for anything it did not list, which
    would fail every check without a trace of why."""
    source = inspect.getsource(tasks._run_ai_job)
    assert '"template_check"' in source and "run_check_job" in source


# --- turning a filled quote back into template wording -----------------------------------------


def test_long_values_become_their_codes_and_short_ones_stay_text():
    """The page locates a quote in the template by its codes; a 6-letter value like 'Mutual' is as
    likely to be ordinary wording as a field, so only values of 12+ characters are turned back."""
    values = {"company": "Aegis Pharma Ltd", "counterparty": "Globex Corporation", "effective_date": "2026-10-01",
              "nda_kind_word": "Mutual", "term_clause": "continues for three (3) years"}
    out = checks.template_form_of("Mutual deal: Aegis Pharma Ltd and Globex Corporation on 2026-10-01, "
                                  "which continues for three (3) years.", values)
    assert out == "Mutual deal: {company} and {counterparty} on 2026-10-01, which {term_clause}."


def test_literal_braces_are_doubled_so_the_quote_is_a_valid_template_fragment():
    assert checks.template_form_of("Schedule {A} of {B}", {}) == "Schedule {{A}} of {{B}}"


def test_the_longest_value_wins_when_one_contains_another():
    values = {"company": "Aegis Pharma Ltd", "counterparty": "Aegis Pharma Ltd Europe"}
    assert checks.template_form_of("Aegis Pharma Ltd Europe", values) == "{counterparty}"


# --- check all ----------------------------------------------------------------------------------


def _check_all_world(db):
    """msa running, nda and saas checkable (saas pinned lazily), consultancy's playbook deleted, the
    rest uncovered."""
    user = _world(db)
    msa, _ = _playbook(db, user, SERVICES)
    nda, _ = _playbook(db, user, NDA_PB)
    _playbook(db, user, SAAS_PB)
    gone, _ = _playbook(db, user, "Consultancy Agreement Playbook", deleted=True)
    _pin(db, user, "msa", msa)
    _pin(db, user, "nda", nda)
    _pin(db, user, "consultancy", gone)
    _set_running(db, _raw_check(db, user, "msa", state="queued"))
    return user


def test_check_all_starts_exactly_the_checkable_templates_and_says_why_it_skipped_the_rest(db):
    """FR-17: Check all must not double-pay for a template already being checked, nor start a check
    nothing can review; what it starts is exactly what it promised."""
    user = _check_all_world(db)

    result = checks.check_all(db, user=user)

    assert sorted(s["key"] for s in result["started"]) == ["nda", "saas"]
    assert {s["key"]: s["reason"] for s in result["skipped"]} == {
        "msa": "checking", "consultancy": "no_playbook", "sow": "no_playbook",
        "vendor": "no_playbook", "dpa": "no_playbook"}
    for item in result["started"]:
        check = db.get(DraftingTemplateCheck, item["check_id"])
        assert check.template_key == item["key"] and check.status == "queued" and check.trigger == "check_all"
        assert check.created_by_user_id == user.id and _job_of(db, check) is not None
    assert get_type(db, user.org_id, "saas").playbook_id is not None, "the unpinned one was pinned on the way"
    (note,) = _audit(db, user.org_id, "drafting_template.check_all_started")
    assert note.actor_user_id == user.id and note.after["count"] == 2
    assert sorted(note.after["keys"]) == ["nda", "saas"]
    assert _ai_runs(db, user.org_id) == 0, "queueing costs nothing"


def test_check_all_carries_on_past_a_template_that_fails_to_queue(db, monkeypatch):
    """One broken template must not stop the others from being checked."""
    user = _check_all_world(db)
    real = checks.request_check

    def flaky(db, *, key, **kw):
        if key == "nda":
            raise RuntimeError("boom")
        return real(db, key=key, **kw)

    monkeypatch.setattr(checks, "request_check", flaky)

    result = checks.check_all(db, user=user)

    assert [s["key"] for s in result["started"]] == ["saas"]
    assert "nda" not in {s["key"] for s in result["skipped"]}


def test_check_all_for_an_org_with_nothing_to_check_starts_nothing(db):
    user = _world(db)

    result = checks.check_all(db, user=user)

    assert result["started"] == [] and len(result["skipped"]) == len(TEMPLATES)
    assert {s["reason"] for s in result["skipped"]} == {"no_playbook"}


# --- publishing a playbook ----------------------------------------------------------------------


def _detail(playbook_id: str) -> dict:
    return {"playbook_id": playbook_id, "playbook_version_id": "v", "version_number": 2}


def _published(db, user, ids):
    checks.on_playbooks_published(db, org_id=user.org_id, playbook_ids=ids, actor_user_id=user.id,
                                  detail_for=_detail)
    return {c.template_key: c for c in db.scalars(select(DraftingTemplateCheck).where(
        DraftingTemplateCheck.org_id == user.org_id))}


def test_a_publish_checks_only_the_templates_that_playbook_reviews(db):
    """FR-14 / D-11: publishing playbook X checks the templates pinned to X (or unpinned ones X is the
    name match for). A template pinned to another playbook must never be taken over by a
    better-named newcomer."""
    user = _world(db)
    services, _ = _playbook(db, user, SERVICES)
    nda, _ = _playbook(db, user, NDA_PB)
    saas, _ = _playbook(db, user, SAAS_PB)
    better, _ = _playbook(db, user, "Master Services Agreement Playbook")
    _pin(db, user, "msa", services)
    _pin(db, user, "nda", nda)  # saas stays unpinned

    assert _published(db, user, [better.id]) == {}, "no template is pinned to it, and msa keeps its pin"
    by_key = _published(db, user, [services.id])
    assert set(by_key) == {"msa"}
    assert by_key["msa"].trigger == "playbook_published" and by_key["msa"].trigger_detail == _detail(services.id)
    assert by_key["msa"].created_by_user_id == user.id
    by_key = _published(db, user, [saas.id])
    assert set(by_key) == {"msa", "saas"} and get_type(db, user.org_id, "saas").playbook_id == saas.id
    assert get_type(db, user.org_id, "nda").playbook_id == nda.id and "nda" not in by_key


def test_a_publish_skips_a_retired_template(db):
    """FR-54: a retired type is not checked on its own, publish included."""
    user = _world(db)
    services, _ = _playbook(db, user, SERVICES)
    _pin(db, user, "msa", services, status="retired")

    assert _published(db, user, [services.id]) == {}


def test_a_failure_checking_one_template_never_fails_the_publish_or_the_others(db, monkeypatch):
    """The publish has already committed (FR-14); an error here must not turn it into a 500 or skip
    the templates after the broken one."""
    user = _world(db)
    services, _ = _playbook(db, user, SERVICES)
    _pin(db, user, "msa", services)
    _pin(db, user, "vendor", services)
    real = checks.request_check

    def flaky(db, *, key, **kw):
        if key == "msa":
            raise RuntimeError("boom")
        return real(db, key=key, **kw)

    monkeypatch.setattr(checks, "request_check", flaky)

    assert set(_published(db, user, [services.id])) == {"vendor"}


def test_the_refusal_for_a_manual_check_names_why_nothing_reviews_the_template(db):
    """The 409 a person gets must say which of the two situations it is: no playbook covers the
    template, or its pinned playbook is gone."""
    user = _world(db)
    ttype = get_type(db, user.org_id, "msa")
    assert checks.no_playbook_detail(db, org_id=user.org_id, ttype=ttype) == (
        "No published playbook covers Master Services Agreement, so there is nothing to check it against.")
    pinned, _ = _playbook(db, user, SERVICES, deleted=True)
    _pin(db, user, "msa", pinned)
    ttype = get_type(db, user.org_id, "msa")
    assert checks.no_playbook_detail(db, org_id=user.org_id, ttype=ttype) == (
        f"Master Services Agreement is reviewed by {SERVICES}, which was deleted. Publish it, or choose "
        "another playbook for this template.")
    ready, _, _ = _ready(db)
    assert checks.no_playbook_detail(db, org_id=ready.org_id, ttype=get_type(db, ready.org_id, "msa")) is None


# --- the service with the real checks module ------------------------------------------------------


def test_the_service_answers_in_the_contracts_shapes_and_a_save_queues_its_check(db):
    """The service's own tests replace this module with a stand-in. With the real one, every answer must
    still fit the contract the page is typed against, and a save must leave a queued check of the new
    wording behind (FR-13)."""
    user, _, _ = _ready(db)
    service = DraftingTemplateService(db)
    for item in service.list(org_id=user.org_id):
        schemas.DraftingTemplateSummaryOut.model_validate(item)
    schemas.DraftingTemplateOut.model_validate(service.get(org_id=user.org_id, key="msa"))

    result = service.save(actor=user, key="msa", body=default_body("msa") + "\n\nExtra clause.", note=None)

    schemas.TemplateMutationResultOut.model_validate(result)
    assert result["changed"] is True and result["check"]["state"] == "queued"
    assert result["check"]["trigger"] == "saved" and result["check"]["template_version"] == 1
    assert result["template"]["status"]["state"] == "checking"
    for version in service.versions(org_id=user.org_id, key="msa"):
        schemas.DraftingTemplateVersionOut.model_validate(version)
    (queued,) = _of(db, user, "queued")
    assert queued.id == result["check"]["id"]


def test_changing_the_playbook_joins_the_check_already_waiting_for_this_wording(db):
    """A waiting check resolves the pinned playbook when it runs, so a pin change must not queue a second
    paid review of the same wording behind it."""
    user, _, _ = _ready(db)
    waiting = _request(db, user)
    other, _ = _playbook(db, user, "Other Playbook")

    result = DraftingTemplateService(db).set_playbook(actor=user, key="msa", playbook_id=other.id)

    assert result["changed"] is True and result["check"]["id"] == waiting.id
    assert get_type(db, user.org_id, "msa").playbook_id == other.id and len(_of(db, user, "queued")) == 1
