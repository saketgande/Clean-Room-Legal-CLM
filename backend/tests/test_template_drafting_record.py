"""A draft records the template it came from, and says so when that template does not pass.

Drafting a contract from a template is never held back by the template's status (FR-47, D-6), so the only
safeguards are honesty and a record. Failures this guards:
* a draft blocked, or silently different, because its template is not Passes;
* a notice that names the wrong state or reason, shown for a template that passes, or missing for one that
  does not (a person starting the draft is the one who needs it);
* a draft whose history does not say which template, which version and which status it was drafted at
  (FR-46), including a draft the workflow engine made with no person present (A-15);
* a draft that later changes because the template was edited, reset, or its playbook published (FR-45);
* a check that creates a contract, run or finding, or a draft that is not reviewed by the template's pin (FR-21,
  FR-51), or whose pin is only written after the AI jobs have been dispatched;
* a binding map for the intake forms that drifts from the catalog the Templates page shows.

Runs against Postgres inside one rolled-back transaction, through the real upload pipeline with an
in-memory store; the AI answer is a fake, so nothing is spent.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.ai.schemas import PlaybookReviewOutput
from app.auth.models import User
from app.contract_files import service as contract_files_service
from app.contract_files.models import ContractTextSnapshot
from app.contract_files.service import ContractFilesService
from app.contracts.models import Contract
from app.core.database import engine, utcnow
from app.core.models import ResourceTimelineEvent
from app.drafting_templates import checks
from app.drafting_templates.models import DraftingTemplateType
from app.drafting_templates.service import (
    TEMPLATES,
    DraftingTemplateService,
    default_body,
)
from app.intake import drafting
from app.intake import routes as intake_routes
from app.intake.dependencies import get_drafting_service
from app.intake.drafting import DraftingService, template_notice_of
from app.intake.models import IntakeRequest
from app.intake.schemas import DraftContractResponse, RequestResponse
from app.integrations.storage import StoredBytes
from app.organizations.models import Organization
from app.playbooks.models import PlaybookRun
from app.workflows import service as workflow_service
from tests.test_template_check_jobs import (
    SERVICES,
    _ai,
    _audit,
    _contract_side,
    _deviation,
    _FakeReview,
    _pin,
    _playbook,
    _raw_check,
    _ready,
    _request,
    _run,
    _save_version,
    _world,
)
from tests.test_template_status import _done, _failed, _finding

MSA_FORM = {"request_form": "new_agreement", "agreement_type": "Services (MSA)", "counterparty": "Globex Corporation"}
SOW_FORM = {"request_form": "sow", "counterparty": "Globex Corporation"}


@pytest.fixture
def db():
    """Commits inside the drafting pipeline are savepoints of one outer transaction, so they behave as in
    production and still leave nothing behind."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


class _Disk:
    """An in-memory object store, so a draft writes no file."""

    def __init__(self):
        self.files: dict[str, bytes] = {}

    def save_bytes(self, *, org_id, filename, mime_type, content):
        key = f"{org_id}/{uuid.uuid4().hex}_{filename}"
        self.files[key] = content
        return StoredBytes(key, filename, mime_type, len(content), hashlib.sha256(content).hexdigest())

    def read_bytes(self, storage_key):
        return self.files[storage_key]

    def path_for_read(self, storage_key):  # pragma: no cover - not read back here
        raise NotImplementedError

    def cleanup_read_path(self, path):  # pragma: no cover
        pass

    def delete_bytes_permanently(self, storage_key):
        self.files.pop(storage_key, None)


@pytest.fixture
def dispatched(monkeypatch):
    """The AI jobs a draft queues, with the pin the contract row held at the moment each was dispatched."""
    seen: list[tuple[str, str | None]] = []

    def dispatch(db, *, job):
        pin = db.scalar(select(Contract.review_playbook_id).where(Contract.id == job.resource_id))
        seen.append((job.job_type, pin))
        return job

    monkeypatch.setattr(contract_files_service, "dispatch_job", dispatch)
    return seen


def _service(db: Session, disk: _Disk | None = None) -> DraftingService:
    return DraftingService(db, files=ContractFilesService(db, storage=disk or _Disk()))


def _filed(db: Session, user: User, form: dict | None = None) -> IntakeRequest:
    """A request filed on a form, as the intake wizard files it."""
    req = IntakeRequest(
        org_id=user.org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=user.id,
        type_label="New agreement", description="Draft it", field_values=form or MSA_FORM, priority="Medium",
        status="open", stage="new", submitted_at=utcnow(), created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    db.add(req)
    db.flush()
    return req


async def _draft(db: Session, user: User, form: dict | None = None, **kw) -> Contract:
    return await _service(db).draft_contract_for_request(actor=user, request=_filed(db, user, form), **kw)


def _events(db: Session, contract: Contract, event_type: str) -> list[ResourceTimelineEvent]:
    return list(db.scalars(select(ResourceTimelineEvent).where(
        ResourceTimelineEvent.resource_type == "contract", ResourceTimelineEvent.resource_id == contract.id,
        ResourceTimelineEvent.event_type == event_type)))


def _notice(label: str, reason: str) -> str:
    return f"This template does not currently pass its playbook: {label} — {reason} The draft was created anyway."


def _text(db: Session, contract: Contract) -> str:
    return db.scalar(select(ContractTextSnapshot.text).where(ContractTextSnapshot.contract_id == contract.id))


# --- the catalog is the one table of bindings ---------------------------------------------------


def test_the_forms_bind_to_the_same_templates_the_catalog_lists():
    """The Templates page shows each template's form and kind from the catalog. If drafting kept its own
    copy of the table, a binding could change on the page and not in the draft that is actually made."""
    assert drafting._FORM_DOC == {"sow": "sow", "dpa": "dpa"}
    assert drafting._KIND_DOC == {
        "NDA": "nda", "Services (MSA)": "msa", "Consultancy": "consultancy",
        "Buying from a vendor": "vendor", "Software or SaaS": "saas",
    }
    for key, spec in TEMPLATES.items():
        found = drafting._KIND_DOC.get(spec["agreement_kind"]) if spec["agreement_kind"] else drafting._FORM_DOC.get(
            spec["form_key"])
        assert found == key


# --- a draft is made in every state, with the right notice --------------------------------------


def _passes(db, user, playbook, version):
    _done(db, user, playbook, version)


def _has_findings(db, user, playbook, version):
    _done(db, user, playbook, version, findings=[_finding("high"), _finding("medium", 1)])


def _never_checked(db, user, playbook, version):
    pass


def _out_of_date(db, user, playbook, version):
    _done(db, user, playbook, version)
    _save_version(db, user, "msa", "New wording for {company} and {counterparty}, on {effective_date}.")


def _partly_checked(db, user, playbook, version):
    _done(db, user, playbook, version, truncated=True)


def _couldnt_check(db, user, playbook, version):
    _failed(db, user, playbook, version)


STATES = [
    (_passes, "passes", None, 0),
    (_has_findings, "has_findings",
     _notice("Has findings", "2 findings, highest High."), 0),
    (_never_checked, "never_checked", _notice("Never checked", "Not checked yet."), 0),
    (_out_of_date, "out_of_date", _notice("Out of date", "The wording changed after the last check."), 1),
    (_partly_checked, "partly_checked",
     _notice("Partly checked", "The review read the first 400,000 of 500,000 characters; the last 20% was not checked."),
     0),
    (_couldnt_check, "couldnt_check",
     _notice("Couldn't check", "The AI review could not run, so the template was not checked. Try again in a moment."),
     0),
]


@pytest.mark.parametrize(("setup", "state", "notice", "version"), STATES, ids=[s[1] for s in STATES])
async def test_a_draft_is_made_in_every_state_and_the_notice_names_that_state(
    db, dispatched, setup, state, notice, version
):
    """FR-47 / AC-69: a template that does not pass still drafts, at once, and the person is told which state
    and why. Passes gives no notice. A draft held back, or a notice naming another state, is the failure."""
    user, playbook, pv = _ready(db)
    setup(db, user, playbook, pv)

    contract = await _draft(db, user)

    assert contract.id and contract.title == "Master Services Agreement — Globex Corporation"
    record = contract.metadata_json["drafted_from_template"]
    assert record["status"] == state and record["notice"] == notice and record["version"] == version
    assert template_notice_of(contract) == notice
    assert [t for t, _ in dispatched] == ["metadata_extraction", "clause_extraction", "embeddings"]


async def test_a_template_nothing_reviews_still_drafts_and_the_notice_says_why(db, dispatched):
    """No playbook is a state like any other (D-6): the draft is made, with no pin, and says nothing covers it."""
    user = _world(db)

    contract = await _draft(db, user)

    reason = "No published playbook covers MSA contracts. Choose a playbook for this template."
    assert template_notice_of(contract) == _notice("No playbook", reason)
    assert contract.review_playbook_id is None  # nothing to pin: today's name match, as for any other contract
    assert contract.metadata_json["drafted_from_template"]["review_playbook_name"] is None
    assert db.scalar(select(DraftingTemplateType).where(DraftingTemplateType.org_id == user.org_id)) is None


@pytest.mark.parametrize(("deleted", "published", "why"), [
    (True, True, "was deleted"), (False, False, "has no published version")])
async def test_a_template_whose_pinned_playbook_is_gone_still_drafts_and_keeps_the_pin(
    db, dispatched, deleted, published, why
):
    """FR-51: the draft records the pin even when it cannot review right now, so the reviews resume if it is
    published again; and it says why, not that "no playbook covers MSA"."""
    user = _world(db)
    playbook, _ = _playbook(db, user, SERVICES, deleted=deleted, published=published)
    _pin(db, user, "msa", playbook)

    contract = await _draft(db, user)

    assert template_notice_of(contract) == _notice(
        "No playbook", f"Master Services Agreement is reviewed by {SERVICES}, which {why}. "
        "Publish it, or choose another playbook for this template.")
    assert contract.review_playbook_id == playbook.id
    assert contract.metadata_json["drafted_from_template"]["review_playbook_name"] == SERVICES


async def test_a_check_in_flight_does_not_hold_a_draft_back(db, dispatched):
    """A template being checked right now still drafts: the notice says the check is running."""
    user, _, _ = _ready(db)
    _request(db, user)  # queued, never run

    contract = await _draft(db, user)

    assert template_notice_of(contract) == _notice("Checking", "A check is waiting to start.")


# --- the record, the audit trail and the contract's own history ---------------------------------


async def test_the_draft_names_its_template_version_and_status_everywhere_it_is_recorded(db, dispatched):
    """AC-42 / FR-46: the metadata, the audit entry, the request's history and the contract's history all name
    the template, the version it was drafted from and its status at that moment. Version 2 is used so a version
    that was only read back later (or left at 0) does not pass."""
    user, playbook, pv = _ready(db)
    _save_version(db, user, "msa", "Version one {company} and {counterparty}.")
    _save_version(db, user, "msa", "Version two of the agreement between {company} and {counterparty}.")
    wording = "Version two of the agreement between {company} and {counterparty}."
    _raw_check(db, user, state="completed", body=wording, version=2, findings=[_finding("high"), _finding("medium", 1)],
               finished_at=utcnow(), playbook_id=playbook.id, playbook_name=playbook.name,
               playbook_version_id=pv.id, playbook_version_number=pv.version_number, text_was_truncated=False)
    req = _filed(db, user)

    contract = await _service(db).draft_contract_for_request(actor=user, request=req, http_request_id="r-1")

    record = contract.metadata_json["drafted_from_template"]
    assert record == {
        "key": "msa", "name": "Master Services Agreement", "version": 2, "source": "text",
        "status": "has_findings", "reason": "2 findings, highest High.",
        "notice": _notice("Has findings", "2 findings, highest High."),
        "review_playbook_id": playbook.id, "review_playbook_name": SERVICES,
    }
    (audit,) = [a for a in _audit(db, user.org_id, "intake.contract_drafted") if a.resource_id == req.id]
    assert audit.after == {
        "contract_id": contract.id, "doc_type": "msa", "template_key": "msa", "template_version": 2,
        "template_source": "text", "template_status": "has_findings",
        "template_status_reason": "2 findings, highest High.", "review_playbook_id": playbook.id,
    }
    (request_event,) = [e for e in db.scalars(select(ResourceTimelineEvent).where(
        ResourceTimelineEvent.resource_id == req.id, ResourceTimelineEvent.event_type == "intake.contract_drafted"))]
    assert request_event.details["template_version"] == 2 and request_event.details["template_status"] == "has_findings"
    (event,) = _events(db, contract, "contract.drafted_from_template")
    assert event.title == "Drafted from the Master Services Agreement template, version 2 — Has findings"
    assert event.created_by_user_id == user.id and event.request_id == "r-1"
    assert event.details == {
        "template_key": "msa", "template_version": 2, "template_source": "text", "status": "has_findings",
        "reason": "2 findings, highest High.", "notice": _notice("Has findings", "2 findings, highest High."),
        "review_playbook_id": playbook.id, "review_playbook_name": SERVICES,
    }


async def test_a_template_that_passes_is_recorded_without_a_notice(db, dispatched):
    """AC-69: the history still names the version and Passes; only the notice is absent."""
    user, playbook, pv = _ready(db)
    _done(db, user, playbook, pv)

    contract = await _draft(db, user)

    assert template_notice_of(contract) is None
    (event,) = _events(db, contract, "contract.drafted_from_template")
    assert event.title == "Drafted from the Master Services Agreement template, the shipped wording — Passes"
    assert event.details["status"] == "passes" and event.details["notice"] is None
    assert event.details["template_version"] == 0 and event.details["template_source"] == "default"


async def test_the_draft_text_is_what_the_template_always_rendered(db, dispatched):
    """The record is added around the draft, never into it: the contract text is the shipped wording filled in,
    byte for byte as before this feature, and the title and type are unchanged."""
    user, _, _ = _ready(db)
    req = _filed(db, user)
    disk = _Disk()

    contract = await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    expected = drafting.render_document(
        "msa", company=db.get(Organization, user.org_id).name, counterparty="Globex Corporation",
        effective=utcnow().date().isoformat(), fields=MSA_FORM, body=default_body("msa"))
    (stored,) = disk.files.values()  # the bytes handed to the upload pipeline, before the reader sees them
    assert stored.decode("utf-8") == expected
    assert contract.contract_type == "MSA" and contract.counterparty_name == "Globex Corporation"
    assert req.contract_id == contract.id


# --- a workflow's draft: no person present ------------------------------------------------------


async def test_a_draft_the_workflow_makes_carries_the_notice_in_its_history(db, dispatched):
    """A-15: the engine's clm_draft step drafts with no person to show a notice to, so the notice has to be on
    the contract's history. Called through the engine's own step executor, not drafting directly."""
    user, playbook, _ = _ready(db)
    req = _filed(db, user)
    run = SimpleNamespace(id="run-1", org_id=user.org_id, request_id=req.id, contract_id=None, flow_name="MSA")

    outcome = await workflow_service._execute_step(
        db, run=run, step={"type": "clm_draft", "config": {"mode": "template"}}, sr=None, actor=user)

    assert outcome == "advance" and run.contract_id
    contract = db.get(Contract, run.contract_id)
    notice = _notice("Never checked", "Not checked yet.")
    (event,) = _events(db, contract, "contract.drafted_from_template")
    assert event.details["notice"] == notice and event.details["status"] == "never_checked"
    assert contract.review_playbook_id == playbook.id
    assert [t for t, _ in dispatched] == ["metadata_extraction", "clause_extraction", "embeddings"]


# --- the pin is set before anything reads it ----------------------------------------------------


async def test_the_draft_holds_the_templates_pin_before_any_ai_job_is_dispatched(db, dispatched):
    """FR-51: the automatic review and the risk band read review_playbook_id. If it were written after the
    jobs were dispatched, a fast worker would review the contract by the name match first."""
    user, playbook, _ = _ready(db)

    contract = await _draft(db, user)

    assert contract.review_playbook_id == playbook.id
    assert dispatched and {pin for _, pin in dispatched} == {playbook.id}


async def test_the_first_pin_is_committed_before_the_text_is_rendered(db, dispatched, monkeypatch):
    """Pinning writes an audit row, which holds the app-wide audit lock until it commits. Rendering and reading
    the file come after it, and a draft that held the lock across them would freeze every other writer in the
    app for as long as the extraction takes (CLAUDE.md, audit-lock freeze 2026-09-28)."""
    user = _world(db)
    _playbook(db, user, SERVICES)  # published and matching by name, but not pinned yet
    order: list[str] = []
    commit, render = db.commit, drafting.render_document
    monkeypatch.setattr(db, "commit", lambda: (order.append("commit"), commit())[1])
    monkeypatch.setattr(drafting, "render_document", lambda *a, **k: (order.append("render"), render(*a, **k))[1])

    await _draft(db, user)

    assert "commit" in order and order.index("commit") < order.index("render")
    assert db.scalar(select(DraftingTemplateType.playbook_id).where(DraftingTemplateType.org_id == user.org_id))


async def test_a_custom_draft_and_a_request_with_no_template_are_not_pinned_or_recorded(db, dispatched):
    """A custom draft is the AI's own paper and a request with no template drafts nothing: neither came from a
    template, so neither is pinned or carries a record (and the unpinned name match is what reviews them)."""
    user, _, _ = _ready(db)
    service = _service(db)
    pinned_before = db.scalar(select(DraftingTemplateType.playbook_id).where(DraftingTemplateType.org_id == user.org_id))

    async def no_ai(**_kw):
        return None, []

    service._ai_draft_text = no_ai
    custom = await service.draft_contract_for_request(actor=user, request=_filed(db, user), custom=True)

    assert "drafted_from_template" not in (custom.metadata_json or {})
    assert custom.review_playbook_id is None and template_notice_of(custom) is None
    assert _events(db, custom, "contract.drafted_from_template") == []
    (audit,) = [a for a in _audit(db, user.org_id, "intake.contract_drafted") if a.after["contract_id"] == custom.id]
    assert "template_key" not in audit.after
    assert pinned_before == db.scalar(
        select(DraftingTemplateType.playbook_id).where(DraftingTemplateType.org_id == user.org_id))


async def test_a_request_with_no_template_is_still_refused_and_pins_nothing(db, dispatched):
    """Unchanged: a request that maps to no template is a 422, not a draft, and no pin is written for it."""
    from fastapi import HTTPException

    user = _world(db)
    form = {"request_form": "new_agreement", "agreement_type": "Selling to a customer"}

    with pytest.raises(HTTPException) as refused:
        await _draft(db, user, form)

    assert refused.value.status_code == 422
    assert db.scalar(select(DraftingTemplateType).where(DraftingTemplateType.org_id == user.org_id)) is None


async def test_drafting_again_returns_the_same_contract_and_writes_nothing_new(db, dispatched):
    """Idempotent as before: a second call finds the contract the request already has, so it cannot write a
    second record, history event or audit entry."""
    user, _, _ = _ready(db)
    service = _service(db)
    req = _filed(db, user)
    first = await service.draft_contract_for_request(actor=user, request=req)
    jobs = len(dispatched)

    again = await service.draft_contract_for_request(actor=user, request=req)

    assert again.id == first.id and len(dispatched) == jobs
    assert len(_events(db, first, "contract.drafted_from_template")) == 1
    assert template_notice_of(again) == template_notice_of(first)


# --- drafts do not change afterwards (FR-45) ----------------------------------------------------


async def test_a_draft_is_unchanged_when_its_template_is_edited_reset_or_its_playbook_published(db, dispatched):
    """FR-45 / AC-41: editing, resetting or restoring the template, and publishing a playbook, are all changes
    to something other than the contract, so its text, record, pin and history stay as they were drafted."""
    user, playbook, _ = _ready(db)
    contract = await _draft(db, user)

    def snapshot():
        db.expire_all()
        return (_text(db, contract), dict(contract.metadata_json), contract.review_playbook_id,
                contract.contract_type, contract.title,
                len(_events(db, contract, "contract.drafted_from_template")))

    before = snapshot()
    service = DraftingTemplateService(db)
    service.save(actor=user, key="msa", body="A new wording for {company} and {counterparty}.", note=None)
    service.reset(actor=user, key="msa", note=None)
    service.restore(actor=user, key="msa", version=1)
    better, _ = _playbook(db, user, "Master Services Agreement Standard Playbook")
    for published in (playbook, better):
        checks.on_playbooks_published(db, org_id=user.org_id, playbook_ids=[published.id],
                                      actor_user_id=user.id, detail_for=lambda pid: {"playbook_id": pid})

    assert snapshot() == before
    assert before[2] == playbook.id


# --- checks make no contracts, runs or findings (FR-21) ------------------------------------------


def test_a_check_of_the_template_changes_no_contract_run_or_finding(db, dispatched, monkeypatch):
    """FR-21: a template's check is a review of wording, not of a contract. After a draft exists, running one
    must leave the contract, playbook-run and deviation counts exactly as they were."""
    user, _, _ = _ready(db)
    asyncio.run(_draft(db, user))
    counts = _contract_side(db)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(deviations=[_deviation()])))

    check = _request(db, user, trigger="manual")
    _run(db, check)

    db.refresh(check)
    assert check.status == "completed" and len(check.findings) == 1
    assert _contract_side(db) == counts
    assert db.scalar(select(PlaybookRun.id).where(PlaybookRun.org_id == user.org_id)) is None


# --- the route's shape --------------------------------------------------------------------------


def test_the_draft_contract_response_is_the_request_plus_the_template_notice():
    """The page (T008) reads template_notice off the draft-contract response; it is the whole request plus that
    one nullable field, and null by default so a draft with no template needs no special case."""
    assert set(DraftContractResponse.model_fields) - set(RequestResponse.model_fields) == {"template_notice"}
    assert DraftContractResponse.model_fields["template_notice"].default is None


@pytest.fixture
def as_user(client, db, monkeypatch):
    """Calls the draft-contract route as ``user`` on the test session, with an in-memory store. The permission
    and screen gates are swapped for the user (their own tests are the permission suites'): what is under test
    is the response the route builds. The user holds ``contract:read`` (the response only names the contract
    to someone who may open it)."""
    from app.core.deps import get_db
    from app.main import app

    monkeypatch.setattr(User, "permission_values", property(lambda u: {"contract:read"}))

    def act(user: User):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[intake_routes._READ] = lambda: user
        app.dependency_overrides[intake_routes._INTAKE_EDIT] = lambda: None
        app.dependency_overrides[get_drafting_service] = lambda: _service(db)
        return client

    return act


def test_the_route_hands_the_notice_to_the_page_and_still_drafts(db, dispatched, as_user):
    """FR-47: the page shows the notice from this response. If the response model dropped the field, or the
    route read it from the wrong place, the person would start a draft from a failing template unwarned."""
    user, _, _ = _ready(db)
    req = _filed(db, user)

    res = as_user(user).post(f"/api/v1/intake/requests/{req.id}/draft-contract")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["template_notice"] == _notice("Never checked", "Not checked yet.")
    assert body["contract_id"] and body["id"] == req.id
    assert db.get(Contract, body["contract_id"]).review_playbook_id is not None


def test_the_route_says_nothing_for_a_template_that_passes(db, dispatched, as_user):
    """No notice (null, not an empty string) for a template that passes, so the page shows no bar."""
    user, playbook, pv = _ready(db)
    _done(db, user, playbook, pv)
    req = _filed(db, user)

    res = as_user(user).post(f"/api/v1/intake/requests/{req.id}/draft-contract")

    assert res.status_code == 200, res.text
    assert res.json()["template_notice"] is None and res.json()["contract_id"]


def test_the_route_still_refuses_a_request_with_no_template(db, dispatched, as_user):
    """422 as before, with no contract, no pin and no record for a request that maps to no template."""
    user = _world(db)
    req = _filed(db, user, {"request_form": "new_agreement", "agreement_type": "Selling to a customer"})

    res = as_user(user).post(f"/api/v1/intake/requests/{req.id}/draft-contract")

    assert res.status_code == 422
    assert res.json()["detail"] == "This request type has no draft template — handle it manually."
    assert req.contract_id is None and dispatched == []
