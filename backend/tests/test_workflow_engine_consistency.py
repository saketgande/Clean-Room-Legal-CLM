"""WF-05 / WF-06 / WF-07 / WF-08 / AGENT-03: one step executor that never re-enters
waiting steps, approval that leaves signing to the workflow, flows picked by what the
request is, no duplicate or orphaned runs, and escalations that reach a person."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.approvals import service as approvals_service
from app.approvals.models import ApprovalRequest
from app.contracts import lifecycle
from app.contracts.models import Contract
from app.core.database import engine
from app.core.enums import ApprovalStatus
from app.intake import consultancy, dpa, drafting, sow
from app.intake import service as intake_service
from app.intake.drafting import ANSWERS_STEP_NOT_MANUAL, ANSWERS_STEP_SUFFIX
from app.notifications.models import Notification
from app.workflows import service
from app.workflows.builtin import BUILTIN_FLOWS
from tests.test_consultancy_dpa_drafting import CONSULTANCY_PB, _consultancy
from tests.test_consultancy_dpa_hold import _NEW_CONSULTANCY, _older_consultancy
from tests.test_sow_drafting import SOW_PB, _older_answers, _rows
from tests.test_template_check_jobs import _playbook, _world
from tests.test_template_drafting_record import _Disk, _filed, _service


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class DB:
    """Answers each query by the entity it selects; keeps added rows and executed SQL."""

    def __init__(self, by_entity=None, get=None):
        self.by_entity, self.get_value, self.added, self.statements = by_entity or {}, get, [], []

    def scalars(self, stmt):
        return _Rows(self.by_entity.get(stmt.column_descriptions[0]["entity"], []))

    def execute(self, stmt):
        self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))
        return _Rows([])

    def get(self, _model, _key):
        return self.get_value

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _sr(idx, status):
    return SimpleNamespace(idx=idx, status=status, note=None, result=None, assignee_user_id=None, step_name=f"Step {idx}")


def _run(steps, **fields):
    base = dict(id="run-1", org_id="org-1", request_id="req-1", contract_id=None, flow_name="MSA", steps=steps,
                current_index=0, status="waiting", error=None, created_by_user_id="u-1")
    base.update(fields)
    return SimpleNamespace(**base)


ACTOR = SimpleNamespace(id="u-1")


# ---- WF-05 ----------------------------------------------------------------

def test_completing_one_parallel_review_leaves_the_other_untouched(monkeypatch):
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr(service, "_require_step_actor", lambda db, **kw: None)
    quality, privacy = _sr(0, "waiting_human"), _sr(1, "waiting_human")
    privacy.assignee_user_id = "u-privacy"
    run = _run([{"type": "human_task"}, {"type": "human_task", "parallel": True}])
    db = DB({service.WorkflowStepRun: [quality, privacy]})

    service.complete_human_step(db, run=run, actor=ACTOR, step_idx=0)
    asyncio.run(service.advance_run(db, run=run, actor=ACTOR))

    assert (run.status, run.current_index) == ("waiting", 0)
    assert (privacy.status, privacy.assignee_user_id) == ("waiting_human", "u-privacy")


def test_a_stage_change_hands_the_run_to_the_one_executor(monkeypatch):
    assert not hasattr(service, "_run_sync_steps")
    queued = []
    monkeypatch.setattr(service, "_schedule_resume", lambda: queued.append(True))
    monkeypatch.setattr(service, "_resolve_waiting_group", lambda db, run, steps: "advance")
    run = _run([{"type": "approval"}, {"type": "counterparty"}, {"type": "signature"}], contract_id="c-1")
    service.advance_flow_for_contract(DB({service.WorkflowRun: [run]}), contract=SimpleNamespace(id="c-1"), actor_user_id="u-1")
    assert (run.status, run.current_index, queued) == ("running", 1, [True])


def test_counterparty_negotiation_has_an_owner(monkeypatch):
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: setattr(sr, "assignee_user_id", "u-owner"))
    sr = _sr(1, "pending")
    outcome = asyncio.run(service._execute_step(DB(), run=_run([]), step={"type": "counterparty", "config": {}}, sr=sr, actor=ACTOR))
    assert (outcome, sr.assignee_user_id) == ("wait", "u-owner")


# ---- AGENT-03 -------------------------------------------------------------

def test_an_escalated_ai_step_goes_to_the_escalation_team_and_they_are_told(monkeypatch):
    sr = _sr(0, "running")
    roles = []

    async def escalate(db, *, run, step, sr, actor):
        sr.status, sr.note = "waiting_human", "AI confidence 0.3 < 0.8 — escalated to attorney"
        return "wait"

    def assign(db, *, run, sr, cfg):
        roles.append(cfg.get("team_id"))
        sr.assignee_user_id = "u-attorney"

    monkeypatch.setattr(service, "_execute_step", escalate)
    monkeypatch.setattr(service, "_assign_step", assign)
    db = DB({service.WorkflowStepRun: [sr]})
    run = _run([{"type": "ai_task", "config": {"team_id": "team-legal"}}], status="running")
    asyncio.run(service.advance_run(db, run=run, actor=ACTOR))

    assert roles == ["team-legal"] and sr.assignee_user_id == "u-attorney"
    [notice] = [row for row in db.added if isinstance(row, Notification)]
    assert notice.user_id == "u-attorney" and "escalated" in notice.body


# ---- WF-06 ----------------------------------------------------------------

def test_approval_leaves_signing_to_a_workflow_that_still_has_a_signature_step(monkeypatch):
    ahead = _run([{"type": "approval"}, {"type": "counterparty"}, {"type": "signature"}], contract_id="c-1")
    last = _run([{"type": "approval"}], contract_id="c-1")
    assert service.workflow_holds_signature(DB({service.WorkflowRun: [ahead]}), contract_id="c-1") is True
    assert service.workflow_holds_signature(DB({service.WorkflowRun: [last]}), contract_id="c-1") is False

    resumed, moved = [], []
    monkeypatch.setattr(service, "workflow_holds_signature", lambda db, contract_id: True)
    monkeypatch.setattr(service, "advance_flow_for_contract", lambda db, contract, actor_user_id: resumed.append(contract.id))
    monkeypatch.setattr(approvals_service, "transition_contract_stage", lambda *a, **kw: moved.append(kw["to_stage"]))
    contract = SimpleNamespace(id="c-1", current_authoritative_version_id="v-2")
    approvals_service.ContractSubject(contract).on_complete(DB(), actor_user_id="u-1", request_id=None)
    assert (resumed, moved) == (["c-1"], [])


def test_an_approved_version_completes_the_approval_step_while_the_contract_stays_in_approval(monkeypatch):
    monkeypatch.setattr(lifecycle, "_current_version_approved", lambda db, contract: True)
    monkeypatch.setattr(service, "_get_contract", lambda db, cid: SimpleNamespace(id=cid, lifecycle_stage="approval"))
    step = _sr(0, "waiting_job")
    run = _run([{"type": "approval"}, {"type": "counterparty"}], contract_id="c-1")
    assert service._resolve_waiting_group(DB({service.WorkflowStepRun: [step]}), run=run, steps=run.steps) == "advance"
    assert step.status == "done"


# ---- WF-07 ----------------------------------------------------------------

def _request(type_label, description=""):
    return SimpleNamespace(org_id="org-1", type_label=type_label, description=description, priority="Medium",
                           department=None, field_values={})


def _flows(specs):
    # As seed_builtin_flows stores them: a spec's type and conditions live in criteria.
    def criteria(f):
        return {**f["criteria"], **({"used_for": f["used_for"], "conditions": f.get("conditions", [])} if f.get("used_for") else {})}
    return [SimpleNamespace(name=f["name"], criteria=criteria(f), steps=f["steps"])
            for f in sorted(specs, key=lambda f: f["eval_order"])]


@pytest.mark.parametrize("type_label, description, expected", [
    ("dpa Request", "Need a DPA for the analytics platform.", "Contract Approval Ladder"),
    ("Data Privacy Incident (DPA)", "Suspected personal-data breach: our analytics vendor exposed records.",
     "Data Privacy Incident (DPDP)"),
    ("Vendor Due Diligence", "Onboard Acme Logistics as a new logistics partner.", "Vendor / Counterparty Due Diligence"),
    # An emailed NDA (no form) no longer reaches NDA Fast-Track by its words:
    # typed workflows are only for form requests; email gets the general
    # ladder and a person confirms (see select_flow).
    ("NDA Request", "Mutual NDA with Globex.", "Contract Approval Ladder"),
])
def test_each_request_kind_starts_its_intended_flow(type_label, description, expected):
    flow = service.select_flow(DB({service.Workflow: _flows(BUILTIN_FLOWS)}), request=_request(type_label, description))
    assert flow.name == expected


def test_a_dpa_to_draft_skips_the_breach_flow_even_with_its_old_stored_criteria():
    old = [dict(f, criteria={"match_type": "dpa"}) if f["name"] == "Data Privacy Incident (DPDP)" else f for f in BUILTIN_FLOWS]
    db = DB({service.Workflow: _flows(old)})
    assert service.select_flow(db, request=_request("dpa Request", "Need a DPA for the analytics platform.")).name == "Contract Approval Ladder"
    assert service.select_flow(db, request=_request("Data Privacy Incident (DPA)", "Suspected breach.")).name == "Data Privacy Incident (DPDP)"


def test_a_privacy_incident_is_not_a_document_but_a_dpa_is():
    assert drafting.resolve_doc_type(_request("Data Privacy Incident (DPA)", "Suspected personal-data breach at a vendor.")) is None
    assert drafting.resolve_doc_type(_request("dpa Request", "Include 72-hour breach notification terms.")) == "dpa"


def test_a_contract_kind_with_no_template_is_drafted_fresh_instead_of_failing(monkeypatch):
    modes = []

    async def draft(db, *, run, mode, actor):
        modes.append(mode)
        return SimpleNamespace(id="c-new")

    monkeypatch.setattr(service, "_draft_contract", draft)
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: None)
    monkeypatch.setattr(drafting, "resolve_doc_type", lambda request: None)
    step = {"type": "clm_draft", "config": {"mode": "template"}}
    outcome = asyncio.run(service._execute_step(DB(get=_request("Distribution agreement")), run=_run([]), step=step,
                                                sr=_sr(0, "pending"), actor=ACTOR))
    assert (modes, outcome) == (["custom"], "wait")


# ---- WF-08 ----------------------------------------------------------------

def test_starting_a_flow_locks_the_request_before_looking_for_an_open_run(monkeypatch):
    open_run = _run([], status="waiting")
    monkeypatch.setattr(service, "get_run_for_request", lambda db, request_id, org_id: open_run)
    db = DB()
    assert asyncio.run(service.start_flow(db, actor=ACTOR, request=SimpleNamespace(id="req-1", org_id="org-1"))) is open_run
    assert "FROM intake_request" in db.statements[0] and "FOR UPDATE" in db.statements[0]


def test_closing_a_request_cancels_its_run_and_the_approvals_it_raised(monkeypatch):
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: None)
    run = _run([{"type": "approval"}], contract_id="c-1")
    step = _sr(0, "waiting_job")
    rung = SimpleNamespace(status=ApprovalStatus.PENDING, updated_by_user_id=None)
    db = DB({service.WorkflowRun: [run], service.WorkflowStepRun: [step], ApprovalRequest: [rung]})
    assert service.cancel_runs_for_request(db, request_id="req-1", org_id="org-1", actor_user_id="u-1") == 1
    assert (run.status, step.status, rung.status) == ("cancelled", "skipped", ApprovalStatus.CANCELLED)


def test_the_close_transition_is_what_cancels_the_workflow(monkeypatch):
    cancelled = []
    monkeypatch.setattr(service, "cancel_runs_for_request", lambda db, **kw: cancelled.append(kw["request_id"]))
    monkeypatch.setattr(intake_service, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(intake_service, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr(intake_service, "_stamp_stage", lambda request, stage: None)
    request = SimpleNamespace(id="req-1", org_id="org-1", status="open", stage="in_review", closed_at=None,
                              updated_by_user_id=None)
    intake_service._transition(DB(), request=request, actor=ACTOR, to_status="closed", to_stage="complete",
                               audit_action="intake.closed")
    assert cancelled == ["req-1"] and request.status == "closed"


# ---- FR-45: a draft held for the SOW's deliverable rows -----------------------------------------

def _held_step(**fields):
    sr = _sr(0, "waiting_human")
    sr.result, sr.note = {"held": "deliverable_rows"}, sow.HELD_STEP_NOTE
    for k, v in fields.items():
        setattr(sr, k, v)
    return sr


def _draft_flow():
    return [{"type": "clm_draft", "name": "Draft", "config": {}}, {"type": "notify", "name": "Tell"}]


def test_a_draft_held_for_the_rows_stops_the_workflow_at_its_step(monkeypatch):
    """FR-45 / AC-48: a held draft must not be skipped, marked done, or let the run go on to the next step
    with no contract -- it waits for a person and shows why."""
    async def held(db, *, run, mode, actor):
        raise drafting.DraftHeld()

    monkeypatch.setattr(service, "_draft_contract", held)
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: setattr(sr, "assignee_user_id", "u-owner"))
    monkeypatch.setattr(drafting, "resolve_doc_type", lambda request: "sow")
    draft, tell = _sr(0, "pending"), _sr(1, "pending")
    run = _run(_draft_flow(), status="running")

    asyncio.run(service.advance_run(DB({service.WorkflowStepRun: [draft, tell]}, get=_request("New agreement")),
                                    run=run, actor=ACTOR))

    assert (run.status, run.current_index, run.contract_id, run.error) == ("waiting", 0, None, None)
    assert (draft.status, draft.note, draft.result) == ("waiting_human", sow.HELD_STEP_NOTE, {"held": "deliverable_rows"})
    assert (draft.assignee_user_id, tell.status) == ("u-owner", "pending")


def test_a_held_draft_step_cannot_be_marked_done_by_hand(monkeypatch):
    """FR-45: "Approve & advance" on a held step would send the run on with no contract. Refused with the
    frozen message, and nothing is written -- not the step, the run, an audit row or a comment."""
    written = []
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: written.append(kw))
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: written.append(kw))
    monkeypatch.setattr(service, "_require_step_actor", lambda db, **kw: None)
    sr = _held_step()
    run = _run(_draft_flow(), context={"rework": {"reply_required": True, "reason": "too wide"}})

    with pytest.raises(HTTPException) as refused:
        service.complete_human_step(DB({service.WorkflowStepRun: [sr]}), run=run, actor=ACTOR, step_idx=0)

    assert (refused.value.status_code, refused.value.detail) == (409, sow.HELD_STEP_NOT_MANUAL)
    assert (sr.status, run.status, run.current_index, written) == ("waiting_human", "waiting", 0, [])


def test_the_refusal_comes_after_the_permission_check(monkeypatch):
    """A person who may not act on the step is told so (403), not what state the step is in."""
    def nope(db, **kw):
        raise HTTPException(403, "Only this step's assignee or its team can act on it")

    monkeypatch.setattr(service, "_require_step_actor", nope)
    with pytest.raises(HTTPException) as refused:
        service.complete_human_step(DB({service.WorkflowStepRun: [_held_step()]}), run=_run(_draft_flow()),
                                    actor=ACTOR, step_idx=0)
    assert refused.value.status_code == 403


def test_every_other_waiting_draft_step_is_still_completed_by_hand(monkeypatch):
    """The refusal is for the held step only: a custom draft (its contract is made, the attorney writes it)
    is still marked done by its owner."""
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr(service, "_require_step_actor", lambda db, **kw: None)
    sr = _sr(0, "waiting_human")
    sr.result = {"contract_id": "c-1"}
    run = _run(_draft_flow())

    service.complete_human_step(DB({service.WorkflowStepRun: [sr]}), run=run, actor=ACTOR, step_idx=0)

    assert (sr.status, run.current_index) == ("done", 1)


@pytest.mark.parametrize("rows_exist, outcome, status", [(True, "rerun", "pending"), (False, "wait", "waiting_human")])
def test_a_held_draft_step_is_set_going_again_only_once_the_rows_exist(monkeypatch, rows_exist, outcome, status):
    """FR-45: the refresh and the resume beat both settle a waiting step with this one resolver, so it is the
    one place that lets a held draft carry on without the request being filed again -- and not before."""
    monkeypatch.setattr(drafting, "draft_hold", lambda db, *, org_id, request: None if rows_exist else sow.DRAFT_HELD)
    sr = _held_step()
    run = _run(_draft_flow())

    got = service._resolve_waiting_group(DB({service.WorkflowStepRun: [sr]}, get=_request("New agreement")),
                                         run=run, steps=run.steps)

    assert (got, sr.status) == (outcome, status)
    assert (sr.result, sr.note) == ((None, None) if rows_exist else ({"held": "deliverable_rows"}, sow.HELD_STEP_NOTE))


def test_a_step_waiting_for_a_person_for_another_reason_is_not_set_going_by_the_resolver(monkeypatch):
    """Only the held mark re-arms a draft step: a custom draft waiting on its attorney, or an upload, keeps
    waiting whatever the request's rows say."""
    monkeypatch.setattr(drafting, "draft_hold", lambda db, *, org_id, request: None)
    sr = _sr(0, "waiting_human")
    sr.result, sr.note = {"contract_id": "c-1"}, "Draft the custom document in the editor, then mark this step done."
    run = _run(_draft_flow())

    assert service._resolve_waiting_group(DB({service.WorkflowStepRun: [sr]}, get=_request("x")),
                                          run=run, steps=run.steps) == "wait"
    assert sr.status == "waiting_human"


def test_a_refresh_drafts_a_held_step_once_the_rows_exist_and_the_run_goes_on(monkeypatch):
    """AC-48: the page's refresh (and the beat, which calls the same function) drafts the held step and the
    run moves on to the next one -- no new filing, one contract."""
    drafted = []

    async def draft(db, *, run, mode, actor):
        drafted.append(mode)
        return SimpleNamespace(id="c-new")

    monkeypatch.setattr(service, "_draft_contract", draft)
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: None)
    monkeypatch.setattr(drafting, "resolve_doc_type", lambda request: "sow")
    monkeypatch.setattr(drafting, "draft_hold", lambda db, *, org_id, request: None)
    held, review = _held_step(), _sr(1, "pending")
    run = _run([{"type": "clm_draft", "name": "Draft", "config": {}}, {"type": "human_task", "name": "Review"}],
               context={})

    asyncio.run(service.refresh_run(DB({service.WorkflowStepRun: [held, review]}, get=_request("New agreement")),
                                    run=run, actor=ACTOR))

    assert drafted == ["template"]
    assert (held.status, held.result, held.note) == ("done", {"contract_id": "c-new"}, None)
    assert (run.contract_id, run.current_index, run.status, review.status) == ("c-new", 1, "waiting", "waiting_human")


@pytest.fixture
def real_db():
    """One rolled-back transaction in which the draft pipeline's commits are savepoints."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def test_an_older_sow_request_waits_in_its_workflow_and_is_drafted_once_its_rows_are_entered(real_db, monkeypatch):
    """FR-45 / AC-48 against the real draft and the real hold: the workflow's draft step stops with the reason
    and no contract, and after the rows are saved a refresh drafts it -- the request is not filed again."""
    from app.contract_files import service as contract_files_service

    monkeypatch.setattr(contract_files_service, "dispatch_job", lambda db, *, job: job)  # no broker
    user = _world(real_db)
    _playbook(real_db, user, SOW_PB)
    request = _filed(real_db, user, _older_answers())
    request.assigned_to_user_id = user.id  # the step falls back to the request's owner
    flow = service.Workflow(
        id=str(uuid.uuid4()), org_id=user.org_id, name="SOW test flow", enabled=True, version=1, criteria={},
        steps=[{"type": "clm_draft", "name": "Draft", "stage": "drafting", "config": {}},
               {"type": "human_task", "name": "Review", "stage": "review", "config": {}}],
    )
    real_db.add(flow)
    disk = _Disk()

    async def draft(db, *, run, mode, actor):  # the real draft, on an in-memory store
        return await _service(db, disk).draft_contract_for_request(actor=actor, request=request)

    monkeypatch.setattr(service, "_draft_contract", draft)

    run = asyncio.run(service.start_flow(real_db, actor=user, request=request, flow=flow))

    held = service.serialize_run(real_db, run)["steps"][0]
    assert (run.status, run.current_index, run.contract_id) == ("waiting", 0, None)
    assert (held["type"], held["status"], held["note"], held["result"], held["assignee_user_id"]) == (
        "clm_draft", "waiting_human", sow.HELD_STEP_NOTE, {"held": "deliverable_rows"}, user.id)
    assert disk.files == {} and real_db.query(Contract).filter_by(org_id=user.org_id).count() == 0
    with pytest.raises(HTTPException) as refused:
        service.complete_human_step(real_db, run=run, actor=user, step_idx=0)
    assert (refused.value.status_code, refused.value.detail) == (409, sow.HELD_STEP_NOT_MANUAL)

    asyncio.run(service.refresh_run(real_db, run=run, actor=user))  # still no rows: nothing moves
    assert (run.status, run.contract_id) == ("waiting", None) and disk.files == {}

    request.field_values = {**request.field_values, "deliverable_rows": _rows()}
    asyncio.run(service.refresh_run(real_db, run=run, actor=user))

    first, second = service.serialize_run(real_db, run)["steps"]
    assert (run.status, run.current_index, request.contract_id) == ("waiting", 1, run.contract_id)
    assert run.contract_id is not None and (first["status"], first["result"]["contract_id"]) == ("done", run.contract_id)
    assert second["status"] == "waiting_human"
    (stored,) = disk.files.values()
    assert "D1. Migration plan." in stored.decode("utf-8")


# ---- spec 008: a draft held for answers or the form, and a draft skipped for a Processor request ------

_FIVE = ["Deliverables", "How is the consultant paid?", "Consultant's key individuals", "Company contact: name",
         "Company contact: role"]


@pytest.mark.parametrize("doc, message, note, result", [
    ("consultancy", consultancy.held_message(_FIVE), consultancy.held_message(_FIVE) + ANSWERS_STEP_SUFFIX,
     {"held": "answers"}),
    ("dpa", dpa.held_message(["Whose data?"]), dpa.held_message(["Whose data?"]) + ANSWERS_STEP_SUFFIX,
     {"held": "answers"}),
    ("dpa", dpa.FREE_TEXT_HELD, dpa.FREE_TEXT_HELD, {"held": "form"}),
])
def test_a_draft_held_for_answers_or_the_form_stops_the_workflow_at_its_step(monkeypatch, doc, message, note, result):
    """spec 008 FR-44 / AC-49: a Consultancy or DPA draft that is held (answers to enter, or a DPA that came in
    by email and needs the form) waits for a person with the reason as its note and the kind of hold in its
    result. It is not skipped or marked done, the run does not go on, and there is no contract."""
    async def held(db, *, run, mode, actor):
        raise drafting.DraftHeld(message)

    monkeypatch.setattr(service, "_draft_contract", held)
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: setattr(sr, "assignee_user_id", "u-owner"))
    monkeypatch.setattr(drafting, "resolve_doc_type", lambda request: doc)
    draft, tell = _sr(0, "pending"), _sr(1, "pending")
    run = _run(_draft_flow(), status="running")

    asyncio.run(service.advance_run(DB({service.WorkflowStepRun: [draft, tell]}, get=_request("New agreement")),
                                    run=run, actor=ACTOR))

    assert (run.status, run.current_index, run.contract_id, run.error) == ("waiting", 0, None, None)
    assert (draft.status, draft.note, draft.result) == ("waiting_human", note, result)
    assert (draft.assignee_user_id, tell.status) == ("u-owner", "pending")


@pytest.mark.parametrize("held", ["answers", "form"])
def test_a_step_held_for_answers_or_the_form_cannot_be_marked_done_by_hand(monkeypatch, held):
    """spec 008 FR-44: "Approve & advance" on a held Consultancy/DPA step would send the run on with no
    contract. Refused with the message that points at the step's note (the SOW keeps its own), after the
    permission check and with nothing written."""
    written = []
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: written.append(kw))
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: written.append(kw))
    monkeypatch.setattr(service, "_require_step_actor", lambda db, **kw: None)
    sr = _held_step(result={"held": held}, note=dpa.FREE_TEXT_HELD)
    run = _run(_draft_flow(), context={"rework": {"reply_required": True, "reason": "too wide"}})

    with pytest.raises(HTTPException) as refused:
        service.complete_human_step(DB({service.WorkflowStepRun: [sr]}), run=run, actor=ACTOR, step_idx=0)

    assert (refused.value.status_code, refused.value.detail) == (409, ANSWERS_STEP_NOT_MANUAL)
    assert ANSWERS_STEP_NOT_MANUAL != sow.HELD_STEP_NOT_MANUAL
    assert (sr.status, run.status, run.current_index, written) == ("waiting_human", "waiting", 0, [])


@pytest.mark.parametrize("held", ["answers", "form"])
@pytest.mark.parametrize("answered, outcome, status", [(True, "rerun", "pending"), (False, "wait", "waiting_human")])
def test_a_step_held_for_answers_is_set_going_again_only_once_the_request_has_them(
        monkeypatch, held, answered, outcome, status):
    """spec 008 FR-44: the refresh and the resume beat settle a waiting step with this one resolver, so it is
    the one place a held Consultancy/DPA draft carries on without the request being filed again."""
    monkeypatch.setattr(drafting, "draft_hold", lambda db, *, org_id, request: None if answered else "still held")
    sr = _held_step(result={"held": held}, note="held")
    run = _run(_draft_flow())

    got = service._resolve_waiting_group(DB({service.WorkflowStepRun: [sr]}, get=_request("New agreement")),
                                         run=run, steps=run.steps)

    assert (got, sr.status) == (outcome, status)
    assert (sr.result, sr.note) == ((None, None) if answered else ({"held": held}, "held"))


def _processor(**fields):
    """A DPA request that says we are the Processor, as the engine reads it."""
    return SimpleNamespace(org_id="org-1", type_label="dpa Request", description="", priority="Medium",
                           department=None, contract_id=None,
                           field_values={"request_form": "dpa", "our_role": dpa.PROCESSOR_ROLE, **fields})


def _skipping_flow():
    return [{"type": "clm_draft", "name": "Draft", "config": {}}, {"type": "human_task", "name": "Review", "config": {}}]


@pytest.fixture
def records(monkeypatch):
    """Every audit row and history line the engine writes, in order."""
    written = []
    monkeypatch.setattr(service, "write_audit_log", lambda db, **kw: written.append(("audit", kw)))
    monkeypatch.setattr(service, "write_timeline_event", lambda db, **kw: written.append(("timeline", kw)))
    return written


def test_a_processor_request_at_a_draft_step_is_skipped_and_the_run_goes_on(monkeypatch, records):
    """spec 008 FR-54 / FR-55 / AC-59: our DPA template is written for the Controller, so the draft step is
    skipped with the note, BEFORE the fallback that would draft a bespoke document for an attorney. The run
    moves on to its next step with no contract, one audit row and one history line, both by the system."""
    modes = []

    async def draft(db, *, run, mode, actor):
        modes.append(mode)
        return SimpleNamespace(id="c-new")

    monkeypatch.setattr(service, "_draft_contract", draft)
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: None)
    skipped, review = _sr(0, "pending"), _sr(1, "pending")
    run = _run(_skipping_flow(), status="running")

    asyncio.run(service.advance_run(DB({service.WorkflowStepRun: [skipped, review]}, get=_processor()),
                                    run=run, actor=ACTOR))

    assert (skipped.status, skipped.note, skipped.result) == ("skipped", dpa.PROCESSOR_NOTE,
                                                              {"skipped": "processor_role"})
    assert (run.status, run.current_index, run.contract_id, review.status) == ("waiting", 1, None, "waiting_human")
    assert modes == []  # neither the template draft nor the bespoke one was asked for
    facts = {"flow_run_id": "run-1", "step_idx": 0, "reason": "processor_role", "note": dpa.PROCESSOR_NOTE}
    (audit,) = [kw for kind, kw in records if kind == "audit"]
    assert audit == {"action": "workflow.draft_skipped", "resource_type": "intake_request", "resource_id": "req-1",
                     "org_id": "org-1", "actor_user_id": None, "after": facts}
    (line,) = [kw for kind, kw in records if kind == "timeline"]
    assert line == {"org_id": "org-1", "resource_type": "intake_request", "resource_id": "req-1",
                    "event_type": "flow.draft_skipped", "title": dpa.PROCESSOR_TITLE, "actor_user_id": None,
                    "details": facts}


@pytest.mark.parametrize("fields, mode, outcome, modes", [
    ({}, "custom", "wait", ["custom"]),
    ({"draft_path": "custom"}, "template", "wait", ["custom"]),
    ({}, "attach", "wait", []),
    ({"draft_path": "attach"}, "template", "wait", []),
])
def test_a_custom_or_attach_draft_step_behaves_as_before_for_a_processor_request(
        monkeypatch, records, fields, mode, outcome, modes):
    """FR-56: the skip is for a step that drafts from the template. A step a person made custom (their choice)
    or one that waits for an upload goes on as it always did, and records no skip."""
    asked = []

    async def draft(db, *, run, mode, actor):
        asked.append(mode)
        return SimpleNamespace(id="c-new")

    monkeypatch.setattr(service, "_draft_contract", draft)
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: None)
    sr = _sr(0, "pending")

    got = asyncio.run(service._execute_step(DB(get=_processor(**fields)), run=_run([]),
                                            step={"type": "clm_draft", "config": {"mode": mode}}, sr=sr, actor=ACTOR))

    assert (got, asked) == (outcome, modes) and sr.status == "waiting_human" and records == []


def test_a_run_that_already_has_a_contract_takes_the_existing_skip_not_the_processor_one(monkeypatch, records):
    """FR-56: a contract that already exists is what "nothing to draft" means, and it is checked first."""
    async def draft(db, *, run, mode, actor):  # pragma: no cover - must not be reached
        raise AssertionError("nothing to draft")

    monkeypatch.setattr(service, "_draft_contract", draft)
    sr = _sr(0, "pending")

    got = asyncio.run(service._execute_step(DB(get=_processor()), run=_run([], contract_id="c-1"),
                                            step={"type": "clm_draft", "config": {}}, sr=sr, actor=ACTOR))

    assert (got, sr.status, sr.note) == ("advance", "skipped", "The contract already exists — nothing to draft.")
    assert records == []


def test_a_processor_request_that_already_has_a_contract_is_not_skipped_by_the_engine(monkeypatch, records):
    """FR-56: once a contract is on the request, the draft call returns it (the step links it and goes on); the
    Processor rule is for a request that would be drafted."""
    modes = []

    async def draft(db, *, run, mode, actor):
        modes.append(mode)
        return SimpleNamespace(id="c-1")

    monkeypatch.setattr(service, "_draft_contract", draft)
    request = _processor()
    request.contract_id = "c-1"
    run, sr = _run([]), _sr(0, "pending")

    got = asyncio.run(service._execute_step(DB(get=request), run=run, step={"type": "clm_draft", "config": {}},
                                            sr=sr, actor=ACTOR))

    assert (got, modes, run.contract_id, sr.result) == ("advance", ["template"], "c-1", {"contract_id": "c-1"})
    assert records == []


def test_a_held_draft_whose_request_becomes_a_processor_one_is_skipped_by_the_next_refresh(monkeypatch, records):
    """FR-56 (skip before hold): once the role is Processor the real hold gives None, the resolver sets the
    held step going again, and the refresh (the beat's path, its actor the run's creator) skips it. The
    record has no actor even though one was passed."""
    monkeypatch.setattr(service, "_assign_step", lambda db, run, sr, cfg: None)
    held, review = _held_step(result={"held": "answers"}, note="held"), _sr(1, "pending")
    run = _run(_skipping_flow(), context={})

    asyncio.run(service.refresh_run(DB({service.WorkflowStepRun: [held, review]}, get=_processor()),
                                    run=run, actor=ACTOR))

    assert (held.status, held.note, held.result) == ("skipped", dpa.PROCESSOR_NOTE, {"skipped": "processor_role"})
    assert (run.current_index, run.contract_id, review.status) == (1, None, "waiting_human")
    assert [kw["actor_user_id"] for _, kw in records] == [None, None]


def test_an_older_consultancy_request_waits_in_its_workflow_and_is_drafted_once_its_answers_are_entered(
        real_db, monkeypatch):
    """spec 008 FR-44 / AC-49 against the real draft and the real hold: the Consultancy draft step stops with
    the reason and no contract, the manual complete is refused, and after the answers are saved a refresh
    drafts it -- the request is not filed again."""
    from app.contract_files import service as contract_files_service

    monkeypatch.setattr(contract_files_service, "dispatch_job", lambda db, *, job: job)  # no broker
    user = _world(real_db)
    _playbook(real_db, user, CONSULTANCY_PB)
    request = _filed(real_db, user, _older_consultancy())
    request.assigned_to_user_id = user.id
    flow = service.Workflow(
        id=str(uuid.uuid4()), org_id=user.org_id, name="Consultancy test flow", enabled=True, version=1, criteria={},
        steps=[{"type": "clm_draft", "name": "Draft", "stage": "drafting", "config": {}},
               {"type": "human_task", "name": "Review", "stage": "review", "config": {}}],
    )
    real_db.add(flow)
    disk = _Disk()

    async def draft(db, *, run, mode, actor):
        return await _service(db, disk).draft_contract_for_request(actor=actor, request=request)

    monkeypatch.setattr(service, "_draft_contract", draft)

    run = asyncio.run(service.start_flow(real_db, actor=user, request=request, flow=flow))

    held = service.serialize_run(real_db, run)["steps"][0]
    note = consultancy.held_message(_FIVE) + ANSWERS_STEP_SUFFIX
    assert (run.status, run.current_index, run.contract_id) == ("waiting", 0, None)
    assert (held["status"], held["note"], held["result"], held["assignee_user_id"]) == (
        "waiting_human", note, {"held": "answers"}, user.id)
    assert disk.files == {} and real_db.query(Contract).filter_by(org_id=user.org_id).count() == 0
    with pytest.raises(HTTPException) as refused:
        service.complete_human_step(real_db, run=run, actor=user, step_idx=0)
    assert (refused.value.status_code, refused.value.detail) == (409, ANSWERS_STEP_NOT_MANUAL)

    asyncio.run(service.refresh_run(real_db, run=run, actor=user))  # still no answers: nothing moves
    assert (run.status, run.contract_id) == ("waiting", None) and disk.files == {}

    request.field_values = {**request.field_values, **{k: v for k, v in _consultancy().items() if k in _NEW_CONSULTANCY}}
    asyncio.run(service.refresh_run(real_db, run=run, actor=user))

    first, second = service.serialize_run(real_db, run)["steps"]
    assert (run.status, run.current_index, request.contract_id) == ("waiting", 1, run.contract_id)
    assert run.contract_id is not None and (first["status"], first["result"]["contract_id"]) == ("done", run.contract_id)
    assert second["status"] == "waiting_human"
    (stored,) = disk.files.values()
    assert "Anita Rao and Dev Menon." in stored.decode("utf-8")
