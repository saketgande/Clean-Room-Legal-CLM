"""A contract drafted from a template is reviewed and banded by the playbook it was pinned to.

Playbooks are matched to a contract by name (``pick_playbook_for_contract``), so publishing a
better-named playbook later used to change which one reviews, and which reviews count towards the
risk band, on contracts that already existed. The band decides approvals, authority limits and the
NDA fast lane, so that was a silent change to decisions already taken. ``Contract.review_playbook_id``
fixes it (FR-51, D-11): ``PlaybooksService.playbook_for_contract`` is the one place the review
(``auto_review_contract``) and the band (``contracts.risk._review_runs``) ask.

What must hold, at the contract level:
* a pinned contract keeps being reviewed and banded by its pin after a better-named playbook is
  published, and "Retry review" goes to the pin too;
* a pin that is deleted, in another org, or has no published version gives no review, an unknown
  band and no fallback to the name match, and reviews resume if it is published again;
* a contract with no pin (uploads, attachments, drafts made before pins) behaves exactly as the
  name match always did.

The last section runs the same through a contract the real drafting pipeline made from a template
(T012): the pin is written at drafting, before the AI jobs are dispatched, and every later review,
counterparty revision and template change leaves it where it was.

Runs against Postgres inside one rolled-back transaction; the AI answer is a fake, so nothing is spent.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers, UploadFile

import app.models  # noqa: F401  (register every mapper)
from app.ai.controller import ai_controller
from app.ai.schemas import PlaybookDeviationOutput, PlaybookReviewOutput
from app.auth.models import User
from app.contract_files.models import ContractTextSnapshot
from app.contract_files.service import ContractFilesService
from app.contracts import auto_review
from app.contracts.models import Contract
from app.contracts.risk import contract_risk
from app.core.database import engine, utcnow
from app.core.enums import ContractVersionSource, PlaybookStatus
from app.core.models import AuditLog
from app.drafting_templates import checks
from app.drafting_templates.models import DraftingTemplateCheck, DraftingTemplateType
from app.drafting_templates.service import DraftingTemplateService, get_type, reviewing_playbook
from app.intake.drafting import template_notice_of
from app.jobs import tasks
from app.jobs.models import JobRun
from app.jobs.service import JobsService
from app.organizations.models import Organization
from app.playbooks.models import Playbook, PlaybookRule, PlaybookRun, PlaybookVersion
from app.playbooks.service import PlaybooksService, execute_playbook_run
from tests.test_current_findings import TEXT_1, World
from tests.test_template_check_jobs import SERVICES, _pin, _ready, _world
from tests.test_template_check_jobs import _playbook as _template_playbook
from tests.test_template_drafting_record import _Disk, _draft

MSA = "Master Services Agreement"
PINNED = "Services Playbook"  # matches an MSA by one word...
BETTER = "Master Services Agreement Standard Playbook"  # ...and this one by two, so it wins by name
GOVERNING_LAW = ("governing_law", "medium", "Governing law is Mars", "laws of Mars")
LIABILITY = ("liability", "critical", "Liability is unlimited", "Liability of each party is unlimited")


@pytest.fixture
def db():
    """Commits inside the code under test (the review, the retry) are savepoints of one outer
    transaction, so they behave as in production and still leave nothing behind."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _publish(db: Session, org_id: str, name: str) -> tuple[Playbook, PlaybookVersion]:
    """A published playbook with the same three rules World gives its own."""
    playbook = Playbook(org_id=org_id, name=name, status=PlaybookStatus.PUBLISHED)
    db.add(playbook)
    db.flush()
    version = PlaybookVersion(
        org_id=org_id, playbook_id=playbook.id, version_number=1, status=PlaybookStatus.PUBLISHED
    )
    db.add(version)
    db.flush()
    playbook.current_version_id = version.id
    for clause in ("term", "liability", "governing_law"):
        db.add(PlaybookRule(
            org_id=org_id, playbook_version_id=version.id, clause_type=clause,
            rule_type="standard_position", risk_level="medium",
        ))
    db.flush()
    return playbook, version


def _pinned_world(db: Session, *, contract_type: str = MSA) -> World:
    """A contract with text, pinned (as drafting will) to the "Services Playbook"."""
    w = World(db)
    w.contract.contract_type = contract_type
    w.playbook.name = PINNED
    w.contract.review_playbook_id = w.playbook.id
    w.text(TEXT_1, 1)
    return w


def _finding(w: World, spec: tuple[str, str, str, str]):
    clause, severity, issue, quote = spec
    return w.dev({"term": 1, "liability": 2, "governing_law": 3}[clause], clause, severity, issue, quote)


def _review(w: World, playbook: Playbook, version: PlaybookVersion, *specs):
    """An explicit playbook run, as the Playbooks page or the assistant starts one."""
    return execute_playbook_run(
        w.db, user=w.user, playbook=playbook, version=version, contract=w.contract, create_redline=False,
        ai_output=PlaybookReviewOutput(deviations=[_finding(w, s) for s in specs]),
    )


def _fake_ai(monkeypatch, w: World, *specs) -> list[str]:
    """Stands in for the model; records which playbook each call was about."""
    asked: list[str] = []

    async def fake(*_a, **kw):
        asked.append(kw["input_payload"]["playbook_id"])
        return PlaybookReviewOutput(deviations=[_finding(w, s) for s in specs]).model_dump(mode="json")

    monkeypatch.setattr(ai_controller, "run_structured_skill", fake)
    return asked


def _auto_review(db: Session, w: World) -> dict:
    return asyncio.run(
        PlaybooksService(db).auto_review_contract(contract=w.contract, actor_user_id=w.user.id, create_redline=False)
    )


def _can_retry_and_queue_nothing(monkeypatch) -> list[str]:
    """Retry review without a seeded admin or a worker: the permission gate is not under test here."""
    queued: list[str] = []
    monkeypatch.setattr(auto_review, "can_retry", lambda _db, *, user: True)
    monkeypatch.setattr(JobsService, "dispatch_job", lambda self, *, job: queued.append(job.id) or job)
    return queued


# ---- a pinned contract keeps its playbook ---------------------------------------------------------


def test_a_better_named_playbook_published_later_does_not_review_a_pinned_contract(db, monkeypatch):
    """The takeover this feature closes: a contract drafted against the Services playbook was reviewed
    by whichever playbook matched its type best at that moment, so publishing "Master Services Agreement
    Standard Playbook" moved the review (and the band) to rules the contract was never drafted for."""
    w = _pinned_world(db)
    better, _ = _publish(db, w.contract.org_id, BETTER)
    svc = PlaybooksService(db)
    # The scenario bites: by name alone the newcomer wins; the pin is what holds it back.
    assert svc.pick_playbook_for_contract(org_id=w.contract.org_id, contract_type=MSA).id == better.id
    assert svc.playbook_for_contract(w.contract).id == w.playbook.id
    asked = _fake_ai(monkeypatch, w, GOVERNING_LAW)

    result = _auto_review(db, w)

    assert result["ran"] is True and result["error"] is None
    assert result["playbook_id"] == w.playbook.id
    assert asked == [w.playbook.id]  # the model was asked about the pin, once
    runs = db.scalars(select(PlaybookRun).where(PlaybookRun.contract_id == w.contract.id)).all()
    assert [r.playbook_id for r in runs] == [w.playbook.id]
    risk = contract_risk(db, w.contract)
    assert risk["playbook_id"] == w.playbook.id and risk["band"] == "medium"


def test_the_band_counts_only_the_pinned_playbooks_reviews(db):
    """Same takeover, seen from the band: a run of the better-named playbook (here with a critical
    finding) must not count, or an approval or fast-lane decision flips when someone publishes it."""
    w = _pinned_world(db)
    better, better_pv = _publish(db, w.contract.org_id, BETTER)
    pinned_run = _review(w, w.playbook, w.pv, ("governing_law", "low", "Governing law is Mars", "laws of Mars"))
    _review(w, better, better_pv, LIABILITY)

    risk = contract_risk(db, w.contract)

    assert risk["playbook_id"] == w.playbook.id
    assert risk["playbook_run_ids"] == [pinned_run.run.id]
    assert risk["band"] == "low"
    # The contrast: take the pin away and the name match counts the other playbook's run.
    w.contract.review_playbook_id = None
    db.flush()
    assert contract_risk(db, w.contract)["band"] == "high"


def test_a_pin_that_the_name_would_never_pick_still_reviews_the_contract(db, monkeypatch):
    """A template can be bound to a playbook whose name says nothing about its type; the draft must be
    reviewed by it, where the name match finds nothing and used to leave the contract unreviewed."""
    w = _pinned_world(db, contract_type="Lease")
    svc = PlaybooksService(db)
    assert svc.pick_playbook_for_contract(org_id=w.contract.org_id, contract_type="Lease") is None
    asked = _fake_ai(monkeypatch, w, GOVERNING_LAW)

    result = _auto_review(db, w)

    assert result["ran"] is True and asked == [w.playbook.id]
    risk = contract_risk(db, w.contract)
    assert risk["playbook_id"] == w.playbook.id and risk["band"] == "medium"


def test_retry_review_goes_to_the_pin_not_to_a_better_named_playbooks_review(db, monkeypatch):
    """"Retry review" asks the band whether a counted review exists. By name, the better playbook's clean
    review of this text would count, so retry would say "not needed" and the pin would never be run."""
    w = _pinned_world(db)
    better, better_pv = _publish(db, w.contract.org_id, BETTER)
    _review(w, better, better_pv)  # a clean, qualifying review by the better-named playbook
    queued = _can_retry_and_queue_nothing(monkeypatch)

    first = auto_review.retry_review(db, contract=w.contract, actor=w.user)

    assert first["status"] == "queued" and queued == [first["job_id"]]
    asked = _fake_ai(monkeypatch, w, GOVERNING_LAW)
    job = db.get(JobRun, first["job_id"])
    assert asyncio.run(tasks._maybe_auto_review(db, job=job)) == "ok"
    assert asked == [w.playbook.id]  # the retry job reviewed against the pin
    risk = contract_risk(db, w.contract)
    assert risk["playbook_id"] == w.playbook.id and risk["band"] == "medium"
    # Now a counted review of the pin exists, so a second retry has nothing to pay for.
    again = auto_review.retry_review(db, contract=w.contract, actor=w.user)
    assert again == {"status": "not_needed", "job_id": None}


# ---- a pin that is gone ---------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["deleted", "unpublished", "other_org"])
def test_a_pinned_playbook_that_is_gone_gives_no_review_and_never_falls_back(db, monkeypatch, how):
    """Falling back to the name match when the pin is deleted or unpublished would put the contract
    under rules it was not drafted for, and a clean review by them would make it "low". It must read
    as "no applicable playbook": nothing runs, and the band stays unknown until a person acts."""
    w = _pinned_world(db)
    better, better_pv = _publish(db, w.contract.org_id, BETTER)
    _review(w, w.playbook, w.pv)  # it was reviewed while the pin was fine
    _review(w, better, better_pv)  # and the name match has a clean, qualifying review: a fallback would show
    assert contract_risk(db, w.contract)["band"] == "low"
    if how == "deleted":
        w.playbook.deleted_at = utcnow()
    elif how == "unpublished":
        w.pv.status = PlaybookStatus.DRAFT
    else:
        other = Organization(name="Other org", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        db.flush()
        w.contract.review_playbook_id = _publish(db, other.id, PINNED)[0].id
    db.flush()
    svc = PlaybooksService(db)
    runs_before = len(db.scalars(select(PlaybookRun).where(PlaybookRun.contract_id == w.contract.id)).all())
    asked = _fake_ai(monkeypatch, w, GOVERNING_LAW)
    queued = _can_retry_and_queue_nothing(monkeypatch)

    assert svc.playbook_for_contract(w.contract) is None
    result = _auto_review(db, w)
    assert result["ran"] is False and result["error"] == "no published playbook"
    assert asked == []  # no AI call
    assert len(db.scalars(select(PlaybookRun).where(PlaybookRun.contract_id == w.contract.id)).all()) == runs_before
    risk = contract_risk(db, w.contract)
    assert risk["band"] == "unknown" and risk["playbook_id"] is None and risk["playbook_run_ids"] == []
    assert auto_review.retry_review(db, contract=w.contract, actor=w.user) == {"status": "no_playbook", "job_id": None}
    assert queued == []


def test_the_note_for_a_deleted_pin_names_the_playbook_not_the_contract_type(db):
    """"No playbook covers this contract type" is false for a pinned contract: a playbook does cover it,
    and it was deleted. The note must say which one and what happened, so a person knows what to fix."""
    w = _pinned_world(db)
    w.playbook.deleted_at = utcnow()
    db.flush()

    risk = contract_risk(db, w.contract)

    assert risk["band"] == "unknown" and risk["playbook_id"] is None
    assert risk["note"] == (
        f"{PINNED} reviews this contract, but it was deleted, so it can't be reviewed. "
        "Publish it again to resume reviews."
    )


def test_the_note_for_an_unpublished_pin_says_it_has_no_published_version(db):
    """Same false message, for a pin whose rules were unpublished rather than deleted."""
    w = _pinned_world(db)
    w.pv.status = PlaybookStatus.DRAFT
    db.flush()

    risk = contract_risk(db, w.contract)

    assert risk["band"] == "unknown" and risk["playbook_id"] is None
    assert risk["note"] == (
        f"{PINNED} reviews this contract, but it has no published version, so it can't be reviewed. "
        "Publish it again to resume reviews."
    )


def test_the_note_for_a_pin_that_cannot_be_found_does_not_name_another_orgs_playbook(db):
    """A pin whose row is gone, or belongs to another org, reads "no longer exists". Naming the row of
    another org would leak its playbook's name through this contract's risk note."""
    w = _pinned_world(db)
    other = Organization(name="Other org", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    foreign, _ = _publish(db, other.id, "Other Orgs Secret Playbook")
    gone = "The playbook this contract was drafted with no longer exists, so it can't be reviewed."

    w.contract.review_playbook_id = foreign.id
    db.flush()
    foreign_note = contract_risk(db, w.contract)["note"]

    with db.no_autoflush:  # the foreign key would refuse an id with no row: this is the row deleted for good
        w.contract.review_playbook_id = "no-such-playbook"
        missing = contract_risk(db, w.contract)
    w.contract.review_playbook_id = w.playbook.id

    assert foreign_note == gone
    assert missing["note"] == gone and missing["band"] == "unknown"


def test_publishing_the_pinned_playbook_again_resumes_its_reviews(db, monkeypatch):
    """The remedy for an unpublished pin is to publish it again: the contract must come back to it,
    not stay unreviewed and not have moved to another playbook meanwhile."""
    w = _pinned_world(db)
    better, _ = _publish(db, w.contract.org_id, BETTER)
    w.pv.status = PlaybookStatus.DRAFT
    db.flush()
    svc = PlaybooksService(db)
    assert svc.playbook_for_contract(w.contract) is None

    w.pv.status = PlaybookStatus.PUBLISHED
    db.flush()

    assert svc.playbook_for_contract(w.contract).id == w.playbook.id != better.id
    asked = _fake_ai(monkeypatch, w, GOVERNING_LAW)
    assert _auto_review(db, w)["ran"] is True and asked == [w.playbook.id]


# ---- a contract with no pin -----------------------------------------------------------------------

_LIBRARY = (
    "NDA Playbook",
    "Master Services Agreement Playbook",
    "Statement of Work Playbook",
    "Data Processing Agreement Playbook",
)


@pytest.mark.parametrize(
    ("contract_type", "expected"),
    [
        ("nda", "NDA Playbook"),
        (MSA, "Master Services Agreement Playbook"),
        ("SOW", "Statement of Work Playbook"),
        ("Data Processing Agreement", "Data Processing Agreement Playbook"),
        ("Lease", None),
        (None, None),
    ],
)
def test_a_contract_with_no_pin_is_reviewed_and_banded_by_the_name_match(db, contract_type, expected):
    """Uploads, attachments, custom drafts and every contract drafted before pins existed keep the pick
    they always had. If this drifted, the existing contracts' bands would move on the day it shipped."""
    org = Organization(name="Unpinned org", slug=f"unpinned-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(
        org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Unpinned",
        hashed_password="hash", status="active",
    )
    db.add(user)
    db.flush()
    for name in _LIBRARY:
        _publish(db, org.id, name)
    contract = Contract(
        org_id=org.id, title="Upload", owner_user_id=user.id, contract_type=contract_type,
        review_playbook_id=None,
    )
    db.add(contract)
    db.flush()
    svc = PlaybooksService(db)

    picked = svc.pick_playbook_for_contract(org_id=org.id, contract_type=contract_type)
    chosen = svc.playbook_for_contract(contract)

    assert (chosen.id if chosen else None) == (picked.id if picked else None)
    assert (chosen.name if chosen else None) == expected
    risk = contract_risk(db, contract)
    assert risk["playbook_id"] == (chosen.id if chosen else None)
    if chosen is None:  # the note an unpinned contract with no match has always had
        assert risk["note"] == "No playbook covers this contract type, so it can't be reviewed yet."


# ---- a contract the real drafting pipeline made from a template ----------------------------------

# A sentence of the shipped MSA, so a fake review can cite text that is really in the draft.
MSA_QUOTE = "The Provider shall perform the Services described in each Statement of Work executed by both Parties."


def _drafted(db: Session):
    """(user, the template's pinned playbook and its version, the MSA drafted from that template): the
    template is pinned to a playbook the name match would never prefer once BETTER exists."""
    user, playbook, version = _ready(db)
    return user, playbook, version, asyncio.run(_draft(db, user))


def _draft_text(db: Session, contract: Contract) -> str:
    return db.scalar(select(ContractTextSnapshot.text).where(
        ContractTextSnapshot.contract_version_id == contract.current_authoritative_version_id))


def _msa_finding(db: Session, contract: Contract, severity: str = "medium", rule_index: int = 1):
    text = _draft_text(db, contract)
    start = text.index(MSA_QUOTE)
    return PlaybookDeviationOutput.model_validate({
        "rule_index": rule_index, "clause_type": "limitation_of_liability", "severity": severity,
        "issue": "Services are only described in each Statement of Work", "original_text": MSA_QUOTE,
        "suggested_fix": "State the services here.",
        "citations": [{"quote": MSA_QUOTE, "start_char": start, "end_char": start + len(MSA_QUOTE)}],
    })


def _fake_review(monkeypatch, db: Session, contract: Contract, severity: str = "medium") -> list[str]:
    """Stands in for the model; records which playbook each call was about."""
    asked: list[str] = []

    async def fake(*_a, **kw):
        asked.append(kw["input_payload"]["playbook_id"])
        out = PlaybookReviewOutput(deviations=[_msa_finding(db, contract, severity)])
        return out.model_dump(mode="json")

    monkeypatch.setattr(ai_controller, "run_structured_skill", fake)
    return asked


def _auto_review_contract(db: Session, user: User, contract: Contract) -> dict:
    return asyncio.run(
        PlaybooksService(db).auto_review_contract(contract=contract, actor_user_id=user.id, create_redline=False)
    )


def _template_checks(db: Session, org_id: str) -> int:
    return db.scalar(select(func.count()).select_from(DraftingTemplateCheck).where(
        DraftingTemplateCheck.org_id == org_id))


def test_a_drafted_msa_is_reviewed_and_banded_by_its_pin_after_a_better_named_playbook_is_published(
    db, monkeypatch
):
    """The takeover this feature closes, end to end: draft an MSA from a template pinned to the Services
    playbook, then publish "Master Services Agreement Standard Playbook". The draft's automatic review, its
    band and the template's own checks must all stay with the pin. If any of them followed the name, the
    contract's approval path would change after the decision was taken."""
    user, playbook, _, contract = _drafted(db)
    assert contract.review_playbook_id == playbook.id
    better, better_pv = _publish(db, user.org_id, BETTER)
    checks_before = _template_checks(db, user.org_id)
    svc = PlaybooksService(db)
    # The scenario bites: by name alone the newcomer wins; only the pin holds the contract back.
    assert svc.pick_playbook_for_contract(org_id=user.org_id, contract_type=contract.contract_type).id == better.id

    # Publishing the better-named playbook asks every template it reviews to be re-checked: not this one.
    checks.on_playbooks_published(db, org_id=user.org_id, playbook_ids=[better.id], actor_user_id=user.id,
                                  detail_for=lambda pid: {"playbook_id": pid})
    asked = _fake_review(monkeypatch, db, contract)
    result = _auto_review_contract(db, user, contract)

    assert result["ran"] is True and result["playbook_id"] == playbook.id and asked == [playbook.id]
    pinned_run = db.get(PlaybookRun, result["playbook_run_id"])
    # An explicit run of the better playbook, with a critical finding, must not count towards the band.
    execute_playbook_run(
        db, user=user, playbook=better, version=better_pv, contract=contract, create_redline=False,
        ai_output=PlaybookReviewOutput(deviations=[_msa_finding(db, contract, "critical", rule_index=2)]),
    )
    risk = contract_risk(db, contract)
    assert risk["playbook_id"] == playbook.id and risk["playbook_run_ids"] == [pinned_run.id]
    assert risk["band"] == "medium"
    assert _template_checks(db, user.org_id) == checks_before  # the template was not re-checked...
    assert get_type(db, user.org_id, "msa").playbook_id == playbook.id  # ...and keeps its pin


def test_a_counterparty_revision_of_a_drafted_msa_is_reviewed_by_the_pin(db, monkeypatch):
    """Same takeover on the second road into auto_review_contract: their returned file is a new version
    with new text, which has no review yet. The pin lives on the contract, not on a version, so the review
    that follows (and the band that reads it) must still be the template's playbook."""
    user, playbook, _, contract = _drafted(db)
    better, _ = _publish(db, user.org_id, BETTER)
    asked = _fake_review(monkeypatch, db, contract)
    first = _auto_review_contract(db, user, contract)
    assert first["ran"] is True and asked == [playbook.id]
    theirs = (_draft_text(db, contract) + "\n\n99. Added by the counterparty. Fees are non-refundable.").encode()
    upload = UploadFile(file=io.BytesIO(theirs), size=len(theirs), filename="theirs.txt",
                        headers=Headers({"content-type": "text/plain"}))

    version = asyncio.run(ContractFilesService(db, storage=_Disk()).add_version_from_upload(
        contract=contract, upload=upload, user=user, source=ContractVersionSource.COUNTERPARTY_REVISION))
    auto_review.request_review(contract)
    db.refresh(contract)
    assert contract.current_authoritative_version_id == version.id
    assert risk_is_unknown(db, contract)  # their text has not been reviewed yet
    job = SimpleNamespace(id="job-1", resource_id=contract.id, created_by_user_id=user.id, metadata_json={})
    assert asyncio.run(tasks._maybe_auto_review(db, job=job)) == "ok"

    assert asked == [playbook.id, playbook.id]  # both reviews were of the pin, never of the better name
    assert contract.review_playbook_id == playbook.id != better.id
    risk = contract_risk(db, contract)
    assert risk["playbook_id"] == playbook.id and risk["band"] == "medium"
    runs = db.scalars(select(PlaybookRun).where(PlaybookRun.contract_id == contract.id)).all()
    assert {r.playbook_id for r in runs} == {playbook.id}


def risk_is_unknown(db: Session, contract: Contract) -> bool:
    return contract_risk(db, contract)["band"] == "unknown"


def test_the_first_draft_of_an_unpinned_template_pins_it_by_name_and_audits_it(db):
    """Lazy pin (D-11): a shipped template has no row until it is first used. Its first draft records today's
    name match, once, as the system on behalf of the person who drafted; a playbook published later, however
    well named, neither re-pins it nor changes where the next draft goes."""
    user = _world(db)
    playbook, _ = _template_playbook(db, user, SERVICES)
    assert db.scalar(select(DraftingTemplateType).where(DraftingTemplateType.org_id == user.org_id)) is None

    first = asyncio.run(_draft(db, user))

    row = db.scalar(select(DraftingTemplateType).where(DraftingTemplateType.org_id == user.org_id))
    assert row.key == "msa" and row.playbook_id == playbook.id
    assert first.review_playbook_id == playbook.id
    (pinned,) = db.scalars(select(AuditLog).where(
        AuditLog.org_id == user.org_id, AuditLog.action == "drafting_template.playbook_pinned")).all()
    assert pinned.actor_user_id is None and pinned.metadata_json == {"actor": "system", "on_behalf_of_user_id": user.id}
    assert pinned.after == {"key": "msa", "playbook_id": playbook.id, "playbook_name": SERVICES,
                            "how": "matched by name at first use"}
    better, _ = _publish(db, user.org_id, BETTER)
    second = asyncio.run(_draft(db, user))
    assert second.review_playbook_id == playbook.id != better.id
    assert db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.org_id == user.org_id, AuditLog.action == "drafting_template.playbook_pinned")) == 1


@pytest.mark.parametrize("how", ["deleted", "unpublished"])
def test_a_draft_from_a_template_whose_pin_is_gone_is_made_and_reports_no_playbook(db, monkeypatch, how):
    """FR-47 with FR-51: the draft is never blocked and it still records the pin, so its reviews resume when the
    pin is published again. Until then the review says no playbook applies. It must not fall back to the
    better-named playbook that is published right beside it."""
    user = _world(db)
    playbook, version = _template_playbook(db, user, SERVICES)
    _pin(db, user, "msa", playbook)
    if how == "deleted":
        playbook.deleted_at = utcnow()
    else:
        version.status = PlaybookStatus.DRAFT
    _publish(db, user.org_id, BETTER)
    db.flush()

    contract = asyncio.run(_draft(db, user))

    why = "was deleted" if how == "deleted" else "has no published version"
    assert f"{SERVICES}, which {why}." in template_notice_of(contract)
    assert contract.review_playbook_id == playbook.id
    asked = _fake_review(monkeypatch, db, contract)
    result = _auto_review_contract(db, user, contract)
    assert result["ran"] is False and result["error"] == "no published playbook" and asked == []
    risk = contract_risk(db, contract)
    assert risk["band"] == "unknown" and risk["playbook_id"] is None
    assert risk["note"].startswith(f"{SERVICES} reviews this contract, but it {why}")
    assert reviewing_playbook(db, user.org_id, get_type(db, user.org_id, "msa")).available is False


def test_changing_the_templates_playbook_leaves_contracts_already_drafted_on_their_old_pin(db, monkeypatch):
    """FR-45: Change playbook sends later drafts to the new playbook. A contract drafted before it keeps the one
    it was drafted with: moving it would change its band, and with it approvals and authority limits, after
    the fact."""
    user, old, _, drafted = _drafted(db)
    new, _ = _publish(db, user.org_id, BETTER)
    asked = _fake_review(monkeypatch, db, drafted)
    _auto_review_contract(db, user, drafted)

    DraftingTemplateService(db).set_playbook(actor=user, key="msa", playbook_id=new.id)
    later = asyncio.run(_draft(db, user))

    assert get_type(db, user.org_id, "msa").playbook_id == new.id
    assert later.review_playbook_id == new.id
    assert drafted.review_playbook_id == old.id
    svc = PlaybooksService(db)
    assert svc.playbook_for_contract(drafted).id == old.id and svc.playbook_for_contract(later).id == new.id
    assert _auto_review_contract(db, user, drafted)["playbook_id"] == old.id
    assert asked == [old.id, old.id]
    risk = contract_risk(db, drafted)
    assert risk["playbook_id"] == old.id and risk["band"] == "medium"
