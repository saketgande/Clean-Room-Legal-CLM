"""A Consultancy or DPA draft that cannot be made yet is held, and one our template is not written for is
skipped (spec 008).

Failures this guards:
* an older Consultancy or DPA request drafted with its new places empty (or from the Templates page's
  example), instead of being held until the answers are entered, or a hold that pins a template, stores a
  file, queues a job or writes an audit row;
* a hold for answers the wording in effect never uses (an organisation's own paper), for their paper, or for
  a Processor request;
* an emailed "please draft a DPA" drafted from nothing, or held with the wrong message;
* a request that says we are the Processor given a draft from our Controller template, a bespoke AI draft, a
  contract, or a refusal that leaves history behind; or a skipped workflow step with no record of why;
* a held step marked done by hand, or one that never carries on after the answers are saved;
* the draft's record missing what it was filled from (FR-49), and SOW behaviour changing.

Runs against Postgres inside one rolled-back transaction, through the real upload pipeline and the real
workflow engine, with an in-memory store; the AI answer is a fake, so nothing is spent.
"""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.contracts.blanks import find_blanks
from app.contracts.models import Contract
from app.core.database import engine, utcnow
from app.core.models import AuditLog, ResourceTimelineEvent
from app.drafting_templates import checks
from app.drafting_templates.models import DraftingTemplateCheck, DraftingTemplateType
from app.drafting_templates.service import (
    DraftingTemplateService,
    body_sha256,
    default_body,
    get_type,
)
from app.intake import consultancy, dpa, drafting, sow
from app.intake import routes as intake_routes
from app.intake.dependencies import get_drafting_service
from app.intake.drafting import ANSWERS_STEP_NOT_MANUAL, ANSWERS_STEP_SUFFIX, DraftHeld
from app.intake.models import IntakeRequest
from app.jobs.models import JobRun
from app.workflows import service as workflow_service
from tests.test_consultancy_dpa_drafting import (
    CONSULTANCY_PB,
    COUNTERPARTY,
    DPA_PB,
    EXAM_DIR,
    _advice_only,
    _consultancy,
    _dpa,
    _plain_dpa,
)
from tests.test_sow_drafting import SOW_PB, _answers, _older_answers, _rows
from tests.test_template_check_jobs import (
    _ai_runs,
    _audit,
    _playbook,
    _raw_check,
    _ready,
    _save_version,
    _world,
)
from tests.test_template_drafting_record import _Disk, _events, _filed, _service, _text

# What a Consultancy request filed before the schedule's questions existed holds: everything the form asked
# then, none of the five new questions.
_NEW_CONSULTANCY = ("consultancy_deliverables", "advice_only", "fee_basis", "fee_rate", "key_individuals",
                    "company_contact_name", "company_contact_role")
FIVE = ('"Deliverables", "How is the consultant paid?", "Consultant\'s key individuals", '
        '"Company contact: name" and "Company contact: role"')


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


@pytest.fixture
def dispatched(monkeypatch):
    """The AI jobs a draft queues, swallowed: a draft here must not reach a broker."""
    from app.contract_files import service as contract_files_service

    seen: list[str] = []
    monkeypatch.setattr(contract_files_service, "dispatch_job", lambda db, *, job: seen.append(job.job_type) or job)
    return seen


@pytest.fixture
def as_user(client, db):
    """Calls the draft-contract route as ``user`` on the test session, with an in-memory store."""
    from app.core.deps import get_db
    from app.main import app

    def act(user):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[intake_routes._READ] = lambda: user
        app.dependency_overrides[intake_routes._INTAKE_EDIT] = lambda: None
        app.dependency_overrides[get_drafting_service] = lambda: _service(db)
        return client

    return act


def _older_consultancy(**over) -> dict:
    """A Consultancy request filed before the schedule's questions: no deliverable rows, fee basis, key
    individuals or Company contact."""
    return {**{k: v for k, v in _consultancy().items() if k not in _NEW_CONSULTANCY}, **over}


def _older_dpa(**over) -> dict:
    """A DPA request filed with the single-choice questions: Controller, Customers, Contact and identity,
    nothing leaving India, no sub-processors."""
    return {"request_form": "dpa", "paper": "Our template", "counterparty": COUNTERPARTY,
            "our_role": "Controller: we decide how the data is used",
            "processing_purpose": "Hosting and support of the clinical-trial site portal.",
            "data_types": "Contact and identity", "data_subjects": "Customers",
            "transfer": "No", "subprocessors": "No", **over}


def _emailed(db: Session, user, description: str = "Need a DPA for the analytics platform.") -> IntakeRequest:
    """A DPA request that came in by email: no form, so none of the form's answers."""
    req = _filed(db, user)
    req.field_values, req.type_label, req.description, req.source = {}, "dpa Request", description, "email"
    db.flush()
    return req


def _written(db: Session, org_id: str) -> tuple[int, int, int, int]:
    """Contracts, template pins, audit rows and history lines the organisation has: a refused or held draft
    leaves all four as they were."""
    return tuple(db.scalar(select(func.count()).select_from(m).where(m.org_id == org_id))
                 for m in (Contract, DraftingTemplateType, AuditLog, ResourceTimelineEvent))


def _timeline(db: Session, org_id: str, event_type: str) -> list[ResourceTimelineEvent]:
    return list(db.scalars(select(ResourceTimelineEvent).where(
        ResourceTimelineEvent.org_id == org_id, ResourceTimelineEvent.event_type == event_type)))


def _hold(db: Session, user, req: IntakeRequest) -> str | None:
    return drafting.draft_hold(db, org_id=user.org_id, request=req)


# --- an older Consultancy request is held (AC-46) ------------------------------------------------


async def test_an_older_consultancy_request_is_held_with_the_missing_answers_and_nothing_is_written(db, dispatched):
    """AC-46: no contract, no pin, no file, no audit row, no history line and no job; the 409 names the five
    answers in the form's order; the stored answers are untouched. Once valid answers are entered the draft
    holds exactly them and none of the Templates page's example."""
    user = _world(db)
    _playbook(db, user, CONSULTANCY_PB)  # published and matching by name, so a draft would pin it
    req = _filed(db, user, _older_consultancy())
    older, disk, before = copy.deepcopy(req.field_values), _Disk(), _written(db, user.org_id)

    with pytest.raises(DraftHeld) as held:
        await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    assert held.value.status_code == 409 and held.value.detail == consultancy.held_message(
        ["Deliverables", "How is the consultant paid?", "Consultant's key individuals", "Company contact: name",
         "Company contact: role"])
    assert held.value.detail == (f"Enter these answers on the request first: {FIVE}. The Engagement Schedule of "
                                 "the Consultancy draft is made from them, and this request does not have them yet.")
    assert req.contract_id is None and disk.files == {} and dispatched == []
    assert _written(db, user.org_id) == before and req.field_values == older

    req.field_values = {**older, **{k: v for k, v in _consultancy("Day rate").items() if k in _NEW_CONSULTANCY}}
    contract = await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    assert req.contract_id == contract.id
    (stored,) = disk.files.values()
    text = stored.decode("utf-8")
    assert "Anita Rao and Dev Menon." in text and "Ravi Shah, Head of Quality." in text
    assert "D2. Process design report." in text and "INR 25,000.00 per day" in text
    assert [m for m in consultancy.EXAMPLE_MARKERS if m in text] == [] and find_blanks(text) == []


@pytest.mark.parametrize(("fv", "labels"), [
    pytest.param(_older_consultancy(), ["Deliverables", "How is the consultant paid?", "Consultant's key individuals",
                                        "Company contact: name", "Company contact: role"], id="none of the five"),
    pytest.param(_older_consultancy(advice_only=[consultancy.ADVICE_TICK]),
                 ["How is the consultant paid?", "Consultant's key individuals", "Company contact: name",
                  "Company contact: role"], id="advice only needs no deliverable rows"),
    pytest.param(_consultancy("Day rate", fee_rate=None), ["Rate per day or hour"], id="a rate basis without its rate"),
    pytest.param(_consultancy("Fixed fee", key_individuals=[]), ["Consultant's key individuals"], id="no individuals"),
    pytest.param(_consultancy("Fixed fee", company_contact_role="  "), ["Company contact: role"], id="blank role"),
    pytest.param(_consultancy("Fixed fee", consultancy_deliverables=[]), ["Deliverables"], id="an empty table"),
])
def test_a_consultancy_request_is_held_only_for_the_answers_it_lacks(db, fv, labels):
    """FR-43: the message names exactly the questions that apply and are unanswered, in form order -- not the
    rate of a fixed fee, not the deliverables of an advice-only engagement."""
    user = _world(db)
    assert _hold(db, user, _filed(db, user, fv)) == consultancy.held_message(labels)
    assert _written(db, user.org_id)[1] == 0  # asking never pins


def test_a_complete_consultancy_request_is_not_held_whatever_the_fee_basis(db):
    user = _world(db)
    for fv in (_consultancy("Fixed fee"), _consultancy("Day rate"), _consultancy("Hourly rate"), _advice_only()):
        assert _hold(db, user, _filed(db, user, fv)) is None


# --- an older or partial DPA request (AC-47) ----------------------------------------------------


async def test_an_older_dpa_request_with_single_choices_drafts_at_once(db, dispatched):
    """AC-47: Controller, Customers, Contact and identity, nothing leaving India and no sub-processors need
    nothing more: the answers it has fill the annexes, and the places for the follow-ups say so."""
    user, _, _ = _ready(db, "dpa", DPA_PB)
    req, disk = _filed(db, user, _older_dpa()), _Disk()
    assert _hold(db, user, req) is None

    await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    (stored,) = disk.files.values()
    text = stored.decode("utf-8")
    assert "Customers." in text and "names, contact details and identification data." in text
    assert dpa.SPECIAL_NO in text and "Processing locations: India. " in text and "\n\nNone.\n\n" in text
    assert find_blanks(text) == [] and "{" not in text


@pytest.mark.parametrize(("fv", "labels"), [
    pytest.param(_older_dpa(data_subjects="Several"), ["Whose data?"], id="Several for whose data"),
    pytest.param(_older_dpa(data_types="Several of these"), ["What kind of personal data?"], id="Several of these"),
    pytest.param(_older_dpa(data_subjects="Several", data_types="Several of these"),
                 ["What kind of personal data?", "Whose data?"], id="both, in form order"),
    pytest.param(_older_dpa(subprocessors="Yes"), ["Sub-processors"], id="Yes to sub-processors, no rows"),
    pytest.param(_older_dpa(transfer="Yes"), ["Countries outside India"], id="Yes to leaving India, no countries"),
    pytest.param(_dpa(data_subjects=[]), ["Whose data?"], id="nothing ticked"),
    pytest.param(_older_dpa(processing_purpose=" "), ["Why is the data processed?"], id="no purpose"),
])
def test_a_dpa_request_is_held_only_for_the_answers_it_lacks(db, fv, labels):
    user = _world(db)
    assert _hold(db, user, _filed(db, user, fv)) == dpa.held_message(labels)


def test_a_complete_dpa_request_is_not_held(db):
    user = _world(db)
    for fv in (_dpa(), _plain_dpa(), _dpa(transfer="No", subprocessors="No"), _older_dpa(data_types="Financial")):
        assert _hold(db, user, _filed(db, user, fv)) is None


def test_an_organisations_own_wording_is_held_only_for_the_answers_it_uses(db):
    """AC-41 / A-10: a wording saved before this feature, with none of the new fields, never holds and keeps
    drafting as before; one that uses a single new field asks for that answer alone."""
    user = _world(db)
    older = _filed(db, user, _older_consultancy())
    older_dpa = _filed(db, user, _older_dpa(data_subjects="Several", subprocessors="Yes"))
    assert _hold(db, user, older) is not None and _hold(db, user, older_dpa) is not None

    _save_version(db, user, "consultancy", "Agreement between {company} and {counterparty}. [Insert deliverables]")
    _save_version(db, user, "dpa", "DPA between {company} and {counterparty}. [Insert annexes]")
    assert _hold(db, user, older) is None and _hold(db, user, older_dpa) is None

    _save_version(db, user, "consultancy", "Agreement between {company} and {counterparty}: {key_individuals}")
    _save_version(db, user, "dpa", "DPA between {company} and {counterparty}: {data_subject_categories}")
    assert _hold(db, user, older) == consultancy.held_message(["Consultant's key individuals"])
    assert _hold(db, user, older_dpa) == dpa.held_message(["Whose data?"])


def test_their_paper_is_never_held_and_a_processor_request_is_skipped_not_held(db):
    """A-3 and FR-56: their paper does not use our template; a Processor request is skipped (the draft call
    refuses it), so it is not also asked for answers the draft would never use."""
    user = _world(db)
    their = {"paper": "Their paper"}
    assert _hold(db, user, _filed(db, user, {"request_form": "dpa", **their})) is None
    assert _hold(db, user, _filed(db, user, {"request_form": "new_agreement", "agreement_type": "Consultancy",
                                             **their})) is None
    processor = _filed(db, user, _older_dpa(our_role=dpa.PROCESSOR_ROLE, data_subjects="Several"))
    assert _hold(db, user, processor) is None and drafting.template_skip(processor) is not None


# --- an emailed or chat DPA request (AC-48) -----------------------------------------------------


async def test_an_emailed_dpa_request_is_held_for_the_form_and_nothing_is_written(db, dispatched):
    """AC-48: "please draft a DPA" has none of the form's answers, so the draft would be Annex 1 and Annex 3
    made from nothing. 409 with the message that says to file it on the form; nothing is created. With no
    role answered it is held, not skipped."""
    user = _world(db)
    _playbook(db, user, DPA_PB)
    req, disk = _emailed(db, user), _Disk()
    before = _written(db, user.org_id)
    assert drafting.resolve_doc_type(req) == "dpa" and drafting.template_skip(req) is None

    with pytest.raises(DraftHeld) as held:
        await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    assert held.value.status_code == 409 and held.value.detail == dpa.FREE_TEXT_HELD
    assert req.contract_id is None and disk.files == {} and dispatched == []
    assert _written(db, user.org_id) == before


def test_the_route_answers_each_refusal_with_its_status_and_exact_message_and_writes_nothing(db, dispatched, as_user):
    """FR-43 / FR-53: the request page shows these, so the status and the words are the ones the contract
    froze: 409 for an answer to enter, 422 for a Processor request."""
    user = _world(db)
    _playbook(db, user, CONSULTANCY_PB)
    _playbook(db, user, DPA_PB)
    cases = [(_filed(db, user, _older_consultancy()), 409, consultancy.held_message(
        ["Deliverables", "How is the consultant paid?", "Consultant's key individuals", "Company contact: name",
         "Company contact: role"])),
             (_emailed(db, user), 409, dpa.FREE_TEXT_HELD),
             (_filed(db, user, _dpa(our_role=dpa.PROCESSOR_ROLE)), 422, dpa.PROCESSOR_NOTE)]
    before = _written(db, user.org_id)

    for req, code, detail in cases:
        res = as_user(user).post(f"/api/v1/intake/requests/{req.id}/draft-contract")
        assert (res.status_code, res.json()["detail"]) == (code, detail)
        assert req.contract_id is None

    assert dispatched == [] and _written(db, user.org_id) == before


# --- a Processor request gets no draft from our template (AC-58, AC-61) --------------------------


async def test_a_processor_request_is_refused_with_the_note_and_nothing_is_written(db, dispatched):
    """AC-58 / FR-53: our DPA template is written for the Controller. A person asking for the draft gets 422
    with the note (the same status as "no draft template"), and no contract, pin, file, audit row or history
    line is made. A request that also holds "Several" gets the Processor message, not a hold."""
    user = _world(db)
    _playbook(db, user, DPA_PB)
    disk = _Disk()
    before = _written(db, user.org_id)

    for fv in (_dpa(our_role=dpa.PROCESSOR_ROLE), _older_dpa(our_role=dpa.PROCESSOR_ROLE, data_subjects="Several")):
        req = _filed(db, user, fv)
        with pytest.raises(HTTPException) as refused:
            await _service(db, disk).draft_contract_for_request(actor=user, request=req)
        assert not isinstance(refused.value, DraftHeld)
        assert (refused.value.status_code, refused.value.detail) == (422, dpa.PROCESSOR_NOTE)
        assert req.contract_id is None

    assert disk.files == {} and dispatched == [] and _written(db, user.org_id) == before


async def test_a_bespoke_draft_and_an_existing_contract_still_work_for_a_processor_request(db, dispatched):
    """FR-56: the skip is for the template draft only. A custom draft (a person chose it) still makes its
    bespoke paper, and a request that already has a contract returns that contract, not a refusal."""
    user, _, _ = _ready(db, "dpa", DPA_PB)
    service = _service(db)

    async def no_ai(**_kw):
        return None, []

    service._ai_draft_text = no_ai
    req = _filed(db, user, _dpa(our_role=dpa.PROCESSOR_ROLE))
    custom = await service.draft_contract_for_request(actor=user, request=req, custom=True)
    assert custom.id and req.contract_id == custom.id and "drafted_from_template" not in (custom.metadata_json or {})

    own = await _service(db).draft_contract_for_request(actor=user, request=_filed(db, user, _dpa()))
    flipped = _filed(db, user, _dpa(our_role=dpa.PROCESSOR_ROLE))
    flipped.contract_id = own.id  # a contract was attached before the role was set to Processor
    assert drafting.template_skip(flipped) is None
    audits = _written(db, user.org_id)[2]

    again = await _service(db).draft_contract_for_request(actor=user, request=flipped)

    assert again.id == own.id and _written(db, user.org_id)[2] == audits


@pytest.mark.parametrize("fv", [
    pytest.param({"request_form": "new_agreement", "agreement_type": "Consultancy", "our_role": dpa.PROCESSOR_ROLE},
                 id="another kind of agreement with the same answer"),
    pytest.param({"request_form": "dpa", "our_role": "Controller: we decide how the data is used"}, id="Controller"),
    pytest.param({"request_form": "dpa"}, id="no role answered"),
])
def test_only_a_dpa_request_that_says_processor_is_skipped(db, fv):
    """A-18: recognised by "Our role" on a DPA request and nothing else -- not by words in an email."""
    user = _world(db)
    assert drafting.template_skip(_filed(db, user, fv)) is None
    emailed = _emailed(db, user, "We are the processor for this DPA, please draft it.")
    assert drafting.template_skip(emailed) is None


def test_the_skip_names_the_reason_the_title_and_the_note():
    req = SimpleNamespace(field_values={"request_form": "dpa", "our_role": dpa.PROCESSOR_ROLE}, contract_id=None,
                          type_label="dpa", description="")
    assert drafting.template_skip(req) == ("processor_role", dpa.PROCESSOR_TITLE, dpa.PROCESSOR_NOTE)


# --- what the draft records (AC-54) -------------------------------------------------------------


@pytest.mark.parametrize(("key", "playbook", "fv", "facts", "absent"), [
    ("consultancy", CONSULTANCY_PB, _consultancy("Fixed fee"),
     {"deliverable_rows": 2, "fee_basis": "Fixed fee", "key_individuals": 2},
     ("countries_outside_india", "subprocessors", "special_category_data", "pricing")),
    ("consultancy", CONSULTANCY_PB, _advice_only(),
     {"deliverable_rows": 0, "fee_basis": "Day rate", "key_individuals": 2},
     ("countries_outside_india", "subprocessors", "special_category_data", "pricing")),
    ("dpa", DPA_PB, _dpa(), {"countries_outside_india": 2, "subprocessors": 2, "special_category_data": True},
     ("deliverable_rows", "fee_basis", "key_individuals", "pricing")),
    ("dpa", DPA_PB, _plain_dpa(), {"countries_outside_india": 0, "subprocessors": 0, "special_category_data": False},
     ("deliverable_rows", "fee_basis", "key_individuals", "pricing")),
])
async def test_a_complete_request_drafts_at_once_and_the_record_says_what_it_was_filled_from(
        db, dispatched, key, playbook, fv, facts, absent):
    """AC-54 / FR-49: the contract's metadata, its history and the audit trail all carry the same counts,
    beside the template record 006 writes; nothing is held back."""
    user, _, _ = _ready(db, key, playbook)
    req = _filed(db, user, fv)
    assert _hold(db, user, req) is None

    contract = await _service(db).draft_contract_for_request(actor=user, request=req)

    (event,) = _events(db, contract, "contract.drafted_from_template")
    (audit,) = [a for a in _audit(db, user.org_id, "intake.contract_drafted") if a.after["contract_id"] == contract.id]
    for where in (contract.metadata_json["drafted_from_template"], event.details, audit.after):
        assert {k: where[k] for k in facts} == facts
        assert not set(absent) & set(where)
        assert (where.get("template_key") or where.get("key")) == key


async def test_another_templates_record_has_none_of_the_fill_keys_and_the_sow_keeps_its_two(db, dispatched):
    """FR-49: an MSA's record is what it always was; the SOW's two keys (007) are unchanged."""
    user, _, _ = _ready(db)
    msa = await _service(db).draft_contract_for_request(actor=user, request=_filed(db, user))
    keys = {"deliverable_rows", "pricing", "fee_basis", "key_individuals", "countries_outside_india",
            "subprocessors", "special_category_data"}
    assert not keys & set(msa.metadata_json["drafted_from_template"])

    other, _, _ = _ready(db, "sow", SOW_PB)
    contract = await _service(db).draft_contract_for_request(actor=other, request=_filed(db, other, _answers()))
    record = contract.metadata_json["drafted_from_template"]
    assert {k: record[k] for k in keys & set(record)} == {"deliverable_rows": 3, "pricing": "Fixed fee"}


# --- the SOW is held exactly as before (007) ----------------------------------------------------


async def test_an_older_sow_request_is_held_with_the_same_status_message_and_effects(db, dispatched):
    """007's behaviour through the generalised hold: the SOW message is unchanged, the draft is held with
    nothing written, and rows entered afterwards draft it. A Consultancy hold does not use the SOW's text."""
    user = _world(db)
    _playbook(db, user, SOW_PB)
    req, disk = _filed(db, user, _older_answers()), _Disk()
    before = _written(db, user.org_id)

    with pytest.raises(DraftHeld) as held:
        await _service(db, disk).draft_contract_for_request(actor=user, request=req)

    assert (held.value.status_code, held.value.detail) == (409, sow.DRAFT_HELD)
    assert drafting.DraftHeld().detail == sow.DRAFT_HELD
    assert disk.files == {} and _written(db, user.org_id) == before

    req.field_values = {**req.field_values, "deliverable_rows": _rows()}
    assert _hold(db, user, req) is None
    await _service(db, disk).draft_contract_for_request(actor=user, request=req)
    assert "D1. Migration plan." in next(iter(disk.files.values())).decode("utf-8")


@pytest.mark.parametrize(("message", "note", "result"), [
    (sow.DRAFT_HELD, sow.HELD_STEP_NOTE, {"held": "deliverable_rows"}),
    (dpa.FREE_TEXT_HELD, dpa.FREE_TEXT_HELD, {"held": "form"}),
    (dpa.held_message(["Whose data?"]), dpa.held_message(["Whose data?"]) + ANSWERS_STEP_SUFFIX, {"held": "answers"}),
    (consultancy.held_message(["Deliverables"]), consultancy.held_message(["Deliverables"]) + ANSWERS_STEP_SUFFIX,
     {"held": "answers"}),
])
def test_a_held_step_shows_its_reason_and_what_it_waits_for(message, note, result):
    """The note and result a held draft step shows (and the request page reads): the SOW's stay as frozen in
    007, an emailed request needs the form, the rest wait for answers."""
    assert drafting.held_step(message) == (note, result)
    assert ANSWERS_STEP_SUFFIX == " This step carries on by itself once the answers are saved on the request."


# --- the workflow: held, refused by hand, carried on; skipped -----------------------------------


def _flow(user) -> workflow_service.Workflow:
    return workflow_service.Workflow(
        id=str(uuid.uuid4()), org_id=user.org_id, name="Draft test flow", enabled=True, version=1, criteria={},
        steps=[{"type": "clm_draft", "name": "Draft", "stage": "drafting", "config": {}},
               {"type": "human_task", "name": "Review", "stage": "review", "config": {}}])


@pytest.fixture
def engine_draft(db, dispatched, monkeypatch):
    """The workflow's draft step made real on an in-memory store; ``modes`` records how it was asked to draft
    and ``ai`` any model call the engine made."""
    from app.ai.controller import ai_controller

    disk, modes, ai = _Disk(), [], []

    async def draft(db, *, run, mode, actor):
        modes.append(mode)
        request = db.get(IntakeRequest, run.request_id)
        return await _service(db, disk).draft_contract_for_request(actor=actor, request=request, custom=mode == "custom")

    async def no_model(*_a, **_kw):
        ai.append(True)
        raise AssertionError("the engine must not call a model for this step")

    monkeypatch.setattr(workflow_service, "_draft_contract", draft)
    monkeypatch.setattr(ai_controller, "run_structured_skill", no_model)
    return SimpleNamespace(disk=disk, modes=modes, ai=ai)


async def _start(db: Session, user, req: IntakeRequest):
    req.assigned_to_user_id = user.id  # a step with no team falls back to the request's owner
    flow = _flow(user)
    db.add(flow)
    run = await workflow_service.start_flow(db, actor=user, request=req, flow=flow)
    return run, workflow_service.serialize_run(db, run)["steps"]


async def test_an_older_consultancy_request_waits_in_its_workflow_and_is_drafted_once_the_answers_are_saved(
        db, engine_draft):
    """FR-44 / AC-49: the draft step stops for a person with the reason, makes no contract and cannot be marked
    done by hand; the request is not filed again -- saving the answers and one refresh drafts it and the run
    goes on."""
    user = _world(db)
    _playbook(db, user, CONSULTANCY_PB)
    req = _filed(db, user, _older_consultancy())

    run, (held, _) = await _start(db, user, req)

    note = consultancy.held_message(["Deliverables", "How is the consultant paid?", "Consultant's key individuals",
                                     "Company contact: name", "Company contact: role"]) + ANSWERS_STEP_SUFFIX
    assert (run.status, run.current_index, run.contract_id) == ("waiting", 0, None)
    assert (held["type"], held["status"], held["note"], held["result"], held["assignee_user_id"]) == (
        "clm_draft", "waiting_human", note, {"held": "answers"}, user.id)
    assert engine_draft.disk.files == {} and _written(db, user.org_id)[0] == 0
    with pytest.raises(HTTPException) as refused:
        workflow_service.complete_human_step(db, run=run, actor=user, step_idx=0)
    assert (refused.value.status_code, refused.value.detail) == (409, ANSWERS_STEP_NOT_MANUAL)
    assert refused.value.detail != sow.HELD_STEP_NOT_MANUAL

    await workflow_service.refresh_run(db, run=run, actor=user)  # still no answers: nothing moves
    assert (run.status, run.current_index, run.contract_id) == ("waiting", 0, None)

    req.field_values = {**req.field_values, **{k: v for k, v in _consultancy().items() if k in _NEW_CONSULTANCY}}
    await workflow_service.refresh_run(db, run=run, actor=user)

    first, second = workflow_service.serialize_run(db, run)["steps"]
    assert run.contract_id is not None and req.contract_id == run.contract_id and run.current_index == 1
    assert (first["status"], first["result"]["contract_id"]) == ("done", run.contract_id)
    assert second["status"] == "waiting_human" and engine_draft.modes == ["template", "template"]
    (stored,) = engine_draft.disk.files.values()
    assert "Ravi Shah, Head of Quality." in stored.decode("utf-8")


async def test_an_older_dpa_request_waits_for_the_answers_and_an_emailed_one_for_the_form(db, engine_draft):
    """FR-44: both stop the workflow with their own reason and result; neither can be marked done by hand;
    the emailed one does not carry on by itself (it needs the form, not an answer on this request)."""
    user = _world(db)
    older = _filed(db, user, _older_dpa(data_subjects="Several"))
    emailed = _emailed(db, user)

    run, (step, _) = await _start(db, user, older)
    assert (step["status"], step["note"], step["result"]) == (
        "waiting_human", dpa.held_message(["Whose data?"]) + ANSWERS_STEP_SUFFIX, {"held": "answers"})
    with pytest.raises(HTTPException) as refused:
        workflow_service.complete_human_step(db, run=run, actor=user, step_idx=0)
    assert (refused.value.status_code, refused.value.detail) == (409, ANSWERS_STEP_NOT_MANUAL)

    older.field_values = {**older.field_values, "data_subjects": ["Customers", "Patients or trial subjects"]}
    await workflow_service.refresh_run(db, run=run, actor=user)
    assert run.contract_id is not None and run.current_index == 1
    (stored,) = engine_draft.disk.files.values()
    assert "Customers; Patients or trial subjects." in stored.decode("utf-8")

    run2, (form_step, _) = await _start(db, user, emailed)
    assert (form_step["status"], form_step["note"], form_step["result"]) == (
        "waiting_human", dpa.FREE_TEXT_HELD, {"held": "form"})
    with pytest.raises(HTTPException) as refused:
        workflow_service.complete_human_step(db, run=run2, actor=user, step_idx=0)
    assert (refused.value.status_code, refused.value.detail) == (409, ANSWERS_STEP_NOT_MANUAL)
    await workflow_service.refresh_run(db, run=run2, actor=user)
    assert (run2.status, run2.current_index, run2.contract_id) == ("waiting", 0, None)


async def test_a_processor_request_is_skipped_by_its_workflow_and_the_run_goes_on(db, engine_draft):
    """FR-54 / FR-55 / AC-59: the draft step is skipped with the note, the run moves to its next step, and
    there is no contract, no bespoke draft, no stored file and no model call. The audit row and the history
    line are written by the system (no actor) and say which run, which step and why."""
    user = _world(db)
    _playbook(db, user, DPA_PB)
    req = _filed(db, user, _dpa(our_role=dpa.PROCESSOR_ROLE))

    run, (skipped, review) = await _start(db, user, req)

    assert (skipped["type"], skipped["status"], skipped["note"], skipped["result"]) == (
        "clm_draft", "skipped", dpa.PROCESSOR_NOTE, {"skipped": "processor_role"})
    assert (review["type"], review["status"]) == ("human_task", "waiting_human")
    assert (run.status, run.current_index, run.contract_id, req.contract_id) == ("waiting", 1, None, None)
    assert engine_draft.modes == [] and engine_draft.ai == [] and engine_draft.disk.files == {}
    assert _written(db, user.org_id)[:2] == (0, 0)

    facts = {"flow_run_id": run.id, "step_idx": 0, "reason": "processor_role", "note": dpa.PROCESSOR_NOTE}
    (audit,) = _audit(db, user.org_id, "workflow.draft_skipped")
    assert (audit.actor_user_id, audit.resource_type, audit.resource_id, audit.after) == (
        None, "intake_request", req.id, facts)
    (line,) = _timeline(db, user.org_id, "flow.draft_skipped")
    assert (line.created_by_user_id, line.resource_type, line.resource_id, line.title, line.details) == (
        None, "intake_request", req.id, dpa.PROCESSOR_TITLE, facts)

    await workflow_service.refresh_run(db, run=run, actor=user)  # polling a finished skip writes nothing more
    assert len(_audit(db, user.org_id, "workflow.draft_skipped")) == 1
    assert len(_timeline(db, user.org_id, "flow.draft_skipped")) == 1


async def test_a_held_request_changed_to_processor_is_skipped_by_the_resume_beat_with_no_person(db, engine_draft):
    """FR-56: skip before hold. A draft held for answers whose role is then set to Processor is no longer
    held; the beat's refresh (its actor is the run's creator) skips the step. The record still has no actor:
    nobody chose the skip."""
    user = _world(db)
    req = _filed(db, user, _older_dpa(data_subjects="Several"))
    run, (held, _) = await _start(db, user, req)
    assert held["result"] == {"held": "answers"}

    req.field_values = {**req.field_values, "our_role": dpa.PROCESSOR_ROLE}
    await workflow_service.refresh_run(db, run=run, actor=user)

    first, second = workflow_service.serialize_run(db, run)["steps"]
    assert (first["status"], first["note"], first["result"]) == ("skipped", dpa.PROCESSOR_NOTE,
                                                                  {"skipped": "processor_role"})
    assert (run.current_index, run.contract_id, second["status"]) == (1, None, "waiting_human")
    (audit,) = _audit(db, user.org_id, "workflow.draft_skipped")
    assert audit.actor_user_id is None and engine_draft.disk.files == {} and engine_draft.ai == []


# --- a request with no "Whose paper?" answer is on our paper (P2-1) ---------------------------------

_NO_PAPER = [pytest.param({"drop": True}, id="no paper answer"), pytest.param({"paper": ""}, id="blank paper answer")]


def _without_paper(fv: dict, how: dict) -> dict:
    """``fv`` as an older request holds it: the paper answer missing, or blank."""
    return {**{k: v for k, v in fv.items() if k != "paper"}, **{k: v for k, v in how.items() if k != "drop"}}


@pytest.mark.parametrize("how", _NO_PAPER)
async def test_an_older_request_with_no_paper_answer_is_held_like_one_on_our_paper(db, dispatched, how):
    """P2-1 / A-3: our own paper is "Our template" or no such answer. Every new question is shown only for
    "Our template", so reading a missing answer as no paper would find nothing missing and draft the schedule
    and the annexes with empty places (no bracket, so nothing else would stop it). A person's draft is
    refused with the missing answers, and nothing is written."""
    user = _world(db)
    _playbook(db, user, CONSULTANCY_PB)
    _playbook(db, user, DPA_PB)
    consultancy_req = _filed(db, user, _without_paper(_older_consultancy(), how))
    dpa_req = _filed(db, user, _without_paper(_older_dpa(data_subjects="Several", transfer="Yes", subprocessors="Yes"), how))
    expected = [
        (consultancy_req, consultancy.held_message(
            ["Deliverables", "How is the consultant paid?", "Consultant's key individuals", "Company contact: name",
             "Company contact: role"])),
        (dpa_req, dpa.held_message(["Whose data?", "Countries outside India", "Sub-processors"])),
    ]
    disk, before = _Disk(), _written(db, user.org_id)

    for req, message in expected:
        assert _hold(db, user, req) == message
        with pytest.raises(DraftHeld) as held:
            await _service(db, disk).draft_contract_for_request(actor=user, request=req)
        assert (held.value.status_code, held.value.detail) == (409, message)
        assert req.contract_id is None

    assert disk.files == {} and dispatched == [] and _written(db, user.org_id) == before


@pytest.mark.parametrize("how", _NO_PAPER)
def test_a_paperless_request_that_needs_nothing_more_is_not_held_and_their_paper_still_is_not(db, how):
    """The missing paper answer is read as ours for the hold only: a complete request, or an older DPA whose
    follow-ups do not apply, is not held, and "Their paper" is still never held."""
    user = _world(db)
    for fv in (_consultancy(), _advice_only(), _dpa(), _older_dpa()):
        assert _hold(db, user, _filed(db, user, _without_paper(fv, how))) is None
    for fv in (_older_consultancy(), _older_dpa(data_subjects="Several")):
        assert _hold(db, user, _filed(db, user, {**fv, "paper": "Their paper"})) is None


@pytest.mark.parametrize("how", _NO_PAPER)
async def test_a_workflow_holds_the_draft_step_of_an_older_request_with_no_paper_answer(db, engine_draft, how):
    """P2-1: the workflow's draft step stops for answers like a request on our paper (not drafted with empty
    places), cannot be marked done by hand, and carries on once the answers are saved."""
    user = _world(db)
    _playbook(db, user, CONSULTANCY_PB)
    req = _filed(db, user, _without_paper(_older_consultancy(), how))

    run, (held, _) = await _start(db, user, req)

    assert (held["status"], held["result"]) == ("waiting_human", {"held": "answers"})
    assert held["note"].startswith("Enter these answers on the request first: \"Deliverables\"")
    assert (run.status, run.current_index, run.contract_id) == ("waiting", 0, None)
    assert engine_draft.disk.files == {}
    with pytest.raises(HTTPException) as refused:
        workflow_service.complete_human_step(db, run=run, actor=user, step_idx=0)
    assert (refused.value.status_code, refused.value.detail) == (409, ANSWERS_STEP_NOT_MANUAL)

    req.field_values = {**req.field_values, **{k: v for k, v in _consultancy().items() if k in _NEW_CONSULTANCY}}
    await workflow_service.refresh_run(db, run=run, actor=user)
    assert run.contract_id is not None and run.current_index == 1


@pytest.mark.parametrize("how", _NO_PAPER)
async def test_a_workflow_holds_the_draft_step_of_an_older_dpa_request_with_no_paper_answer(db, engine_draft, how):
    user = _world(db)
    # The two tables are shown only for "Our template": without the fix this request is held for the first
    # question alone.
    req = _filed(db, user, _without_paper(_older_dpa(data_subjects="Several", transfer="Yes", subprocessors="Yes"), how))

    _, (held, _) = await _start(db, user, req)

    assert (held["status"], held["result"]) == ("waiting_human", {"held": "answers"})
    assert held["note"] == dpa.held_message(
        ["Whose data?", "Countries outside India", "Sub-processors"]) + ANSWERS_STEP_SUFFIX
    assert engine_draft.disk.files == {}


# --- the release and the drafts already made (AC-41, AC-44) ---------------------------------------


@pytest.mark.parametrize(("key", "playbook"), [("consultancy", CONSULTANCY_PB), ("dpa", DPA_PB)])
def test_the_release_makes_a_check_of_the_old_wording_out_of_date_and_starts_nothing(db, key, playbook):
    """AC-41 / FR-37: a template last checked on the old shipped wording reads Out of date, saying the wording
    changed. Reading it starts no check, no job and no model call (every review is paid for). One never
    checked stays never checked, and a reset gives the new wording as a new version."""
    old = (EXAM_DIR / f"{key}_2026-10-15.txt").read_text(encoding="utf-8")
    assert body_sha256(old) != body_sha256(default_body(key))
    user, playbook_row, version = _ready(db, key, playbook)
    assert checks.template_status(db, org_id=user.org_id, ttype=get_type(db, user.org_id, key))["state"] == (
        "never_checked")
    _raw_check(db, user, key, state="completed", body=old, findings=[], finished_at=utcnow(),
               playbook_id=playbook_row.id, playbook_name=playbook_row.name, playbook_version_id=version.id,
               playbook_version_number=version.version_number, text_was_truncated=False)
    rows = [db.scalar(select(func.count()).select_from(m)) for m in (DraftingTemplateCheck, JobRun)]
    ai = _ai_runs(db, user.org_id)

    status = checks.template_status(db, org_id=user.org_id, ttype=get_type(db, user.org_id, key))

    assert (status["state"], status["changed"]) == ("out_of_date", "wording")
    assert [db.scalar(select(func.count()).select_from(m)) for m in (DraftingTemplateCheck, JobRun)] == rows
    assert _ai_runs(db, user.org_id) == ai

    _save_version(db, user, key, old)
    back = DraftingTemplateService(db).reset(actor=user, key=key, note=None)
    assert back["changed"] is True and back["template"]["version"] == 2 and back["template"]["source"] == "default"
    assert find_blanks(DraftingTemplateService(db).body_for(org_id=user.org_id, key=key)) == []


@pytest.mark.parametrize(("key", "playbook", "fv"), [
    ("consultancy", CONSULTANCY_PB, _consultancy("Day rate")), ("dpa", DPA_PB, _dpa())])
async def test_a_draft_keeps_its_text_when_the_template_is_edited_and_reset(db, dispatched, key, playbook, fv):
    """AC-44 / FR-40: a contract drafted from the old wording keeps the text it was drafted with, and its
    record, when the template is later edited and reset to the new shipped wording."""
    user, _, _ = _ready(db, key, playbook)
    _save_version(db, user, key, (EXAM_DIR / f"{key}_2026-10-15.txt").read_text(encoding="utf-8"))
    contract = await _service(db).draft_contract_for_request(actor=user, request=_filed(db, user, fv))

    def snapshot():
        db.expire_all()
        return _text(db, contract), dict(contract.metadata_json), len(_events(db, contract, "contract.drafted_from_template"))

    before = snapshot()
    assert find_blanks(before[0]) != []  # drafted from the old wording, brackets and all
    service = DraftingTemplateService(db)
    service.save(actor=user, key=key, body="A new wording for {company} and {counterparty}.", note=None)
    service.reset(actor=user, key=key, note=None)

    assert snapshot() == before
