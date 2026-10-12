"""WF-05 / WF-06 / WF-07 / WF-08 / AGENT-03: one step executor that never re-enters
waiting steps, approval that leaves signing to the workflow, flows picked by what the
request is, no duplicate or orphaned runs, and escalations that reach a person."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.approvals import service as approvals_service
from app.approvals.models import ApprovalRequest
from app.contracts import lifecycle
from app.core.enums import ApprovalStatus
from app.intake import drafting
from app.intake import service as intake_service
from app.notifications.models import Notification
from app.workflows import service
from app.workflows.builtin import BUILTIN_FLOWS


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
