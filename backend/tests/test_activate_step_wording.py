"""The last workflow step no longer claims that obligations are tracked.

It used to be "Activate & track" ("File the contract and start obligation tracking."): a step that checks nothing, shown
as a green "Done" above a red "Obligation extraction failed". Now it is "Activate", says what it does, and (in the Active
stage) says on the step that nothing is checked there and where the result shows. Existing workflows, their pinned run
snapshots and their step runs get the same wording from migration 0078, which leaves an administrator's own text alone.
"""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.core.database import engine, utcnow
from app.core.enums import UserStatus
from app.intake.models import IntakeRequest
from app.organizations.models import Organization
from app.workflows import service, stages
from app.workflows.builtin import BUILTIN_FLOWS
from app.workflows.models import Workflow, WorkflowRun, WorkflowStepRun

MIGRATION = Path(__file__).parents[1] / "alembic" / "versions" / "0078_activate_step_wording.py"
OLD_NAME = "Activate & track"
OLD_TEXT = "File the contract and start obligation tracking."


def _migration():
    spec = importlib.util.spec_from_file_location("m0078_activate_step_wording", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The flows that end with the Activate step: Master Services Agreement (built in) and the contract library's. Pinned by
# name so a flow that loses the step, or swaps it for one with an agent, fails here instead of passing a count.
ACTIVATE_FLOWS = {
    "Master Services Agreement", "NDA — standard", "NDA — one-way", "NDA — their paper", "MSA — high value",
    "Vendor — small purchase", "Vendor — standard purchase", "Software / SaaS purchase", "Consultancy",
    "Customer — standard", "Customer — strategic deal", "Other agreement", "Statement of Work",
    "Data Processing Agreement", "Signed outside the system",
}


# --- the shipped workflows ---------------------------------------------------------------------------------------------


def test_every_shipped_workflow_ends_with_the_honest_activate_step():
    """Guards the old claim coming back from one more place that builds the step: the contract library (every agreement
    type) and the built-in MSA flow each define it, and both read the one wording in workflows/stages.py. (BUILTIN_FLOWS
    already includes the library's flows: they are walked once.)"""
    carrying = set()
    for spec in BUILTIN_FLOWS:
        last = spec["steps"][-1]
        if last.get("name") == stages.ACTIVATE_STEP_NAME:
            carrying.add(spec["name"])
            cfg = last["config"]
            assert last["type"] == "ai_task" and last.get("stage") == "active", spec["name"]
            assert not cfg.get("agent") and not cfg.get("skill") and cfg.get("escalate_below_confidence") is None
            assert cfg["instructions"] == stages.ACTIVATE_STEP_TEXT, spec["name"]
    assert carrying == ACTIVATE_FLOWS
    texts = " ".join(f"{s['name']} {(s.get('config') or {}).get('instructions', '')}"
                     for spec in BUILTIN_FLOWS for s in spec["steps"]).lower()
    assert "obligation tracking" not in texts and "activate & track" not in texts


def test_the_step_text_promises_nothing_it_does_not_check():
    for text in (stages.ACTIVATE_STEP_NAME, stages.ACTIVATE_STEP_TEXT, stages.ACTIVATE_STEP_NOTE,
                 stages.ACTIVATE_STEP_NOTE_NOT_ACTIVE, stages.ACTIVATE_STEP_NOTE_NO_CONTRACT):
        assert "track" not in text.lower()
    assert "Active stage" in stages.ACTIVATE_STEP_TEXT and "Active stage" in stages.ACTIVATE_STEP_NOTE
    # The step text holds for a flow that never reaches Active too ("Signed outside the system"): it promises nothing
    # about what the step did, only about what happens once the contract is Active.
    assert "once the contract is Active" in stages.ACTIVATE_STEP_TEXT and "file the contract" not in stages.ACTIVATE_STEP_TEXT.lower()


# --- the engine --------------------------------------------------------------------------------------------------------


def _run_step(step, *, contract_stage=None):
    request = SimpleNamespace(id="req-1", type_label="Contract", description="d", screening=None, ai_triage={},
                              field_values={}, priority="Medium")
    contract = SimpleNamespace(id="c-1", lifecycle_stage=contract_stage) if contract_stage else None
    sr = SimpleNamespace(status="running", note=None, result=None, updated_at=None)
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id="c-1" if contract else None,
                          flow_name="Flow")
    db = SimpleNamespace(get=lambda model, _id: contract if model.__name__ == "Contract" else request,
                         commit=lambda: None)
    outcome = asyncio.run(service._execute_step(db, run=run, step=step, sr=sr, actor=SimpleNamespace(id="u-1")))
    return outcome, sr


ACTIVATE = {"type": "ai_task", "name": stages.ACTIVATE_STEP_NAME, "stage": "active", "config": {}}


@pytest.mark.parametrize("stage", ["active", "closed"])
def test_the_activate_step_still_advances_and_says_nothing_was_checked_for_an_active_contract(stage):
    """Guards both the old behaviour (a system step must not be wedged by the fail-closed rule) and the new note: the step
    row reads "Done", so it says where the real answer is."""
    outcome, sr = _run_step(ACTIVATE, contract_stage=stage)

    assert outcome == "advance" and sr.note == stages.ACTIVATE_STEP_NOTE


@pytest.mark.parametrize("stage", ["intake", "review", "signature"])
def test_the_note_does_not_promise_a_reading_for_a_contract_that_is_not_active(stage):
    """Guards the flow with no signature step ("Signed outside the system"): its contract is still in Review or
    Signature when this step runs and nothing reads its obligations until it becomes Active, so the step must not say
    they are being read."""
    outcome, sr = _run_step(ACTIVATE, contract_stage=stage)

    assert outcome == "advance" and sr.note == stages.ACTIVATE_STEP_NOTE_NOT_ACTIVE


def test_a_request_with_no_contract_says_there_is_nothing_to_read():
    outcome, sr = _run_step(ACTIVATE, contract_stage=None)

    assert outcome == "advance" and sr.note == stages.ACTIVATE_STEP_NOTE_NO_CONTRACT


def test_a_system_step_in_any_other_stage_gets_no_such_note():
    """Guards the note leaking onto an administrator's own agent-less step in Review, where it would be false."""
    outcome, sr = _run_step({"type": "ai_task", "name": "Log it", "stage": "review", "config": {}},
                            contract_stage="active")

    assert outcome == "advance" and sr.note is None


# --- the migration -----------------------------------------------------------------------------------------------------


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _step(name, *, type_="ai_task", instructions=OLD_TEXT, **cfg):
    return {"id": str(uuid.uuid4()), "type": type_, "name": name, "stage": "active",
            "config": {"instructions": instructions, "sla_hours": 0, **cfg}}


def _world(db):
    org = Organization(name="Wording org", slug=f"wording-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="U", hashed_password="h",
                status=UserStatus.ACTIVE)
    db.add(user)
    db.flush()
    request = IntakeRequest(org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=user.id,
                            type_label="General request", description="d", field_values={}, priority="Medium",
                            status="open", stage="new", submitted_at=utcnow())
    db.add(request)
    db.flush()
    return org, request


def test_the_migration_rewrites_definitions_snapshots_and_step_runs_and_spares_everything_else(db):
    """Guards the three places the old wording lives (the definition, the snapshot every request pins, and each step
    run), and an administrator's own text and steps being flattened by a blanket replace."""
    m = _migration()
    org, request = _world(db)
    old = _step(OLD_NAME)
    customised = _step(OLD_NAME, instructions="Our own words about filing.")
    other_ai = _step("Log it", instructions=OLD_TEXT)
    human = _step(OLD_NAME, type_="human_task")
    flow = Workflow(org_id=org.id, name="Custom flow", steps=[old, customised, other_ai, human])
    db.add(flow)
    db.flush()
    run = WorkflowRun(org_id=org.id, request_id=request.id, flow_id=flow.id, flow_name=flow.name,
                      steps=[dict(old), dict(other_ai)], status="complete")
    db.add(run)
    db.flush()
    for idx, (type_, name) in enumerate([("ai_task", OLD_NAME), ("human_task", OLD_NAME), ("ai_task", "Log it")]):
        db.add(WorkflowStepRun(org_id=org.id, flow_run_id=run.id, idx=idx, step_type=type_, step_name=name,
                               status="done"))
    db.flush()

    counts = m.reword(db.connection())

    db.expire_all()
    assert counts["workflow"] >= 1 and counts["workflow_run"] >= 1 and counts["workflow_step_run"] >= 1
    after = {s["id"]: s for s in db.get(Workflow, flow.id).steps}
    assert after[old["id"]]["name"] == "Activate" and after[old["id"]]["config"]["instructions"] == m.NEW_TEXT
    assert after[old["id"]]["config"]["sla_hours"] == 0 and after[old["id"]]["stage"] == "active"  # nothing else touched
    assert after[customised["id"]]["name"] == "Activate"  # the name claimed tracking whatever the text says
    assert after[customised["id"]]["config"]["instructions"] == "Our own words about filing."
    assert after[other_ai["id"]]["name"] == "Log it" and after[other_ai["id"]]["config"]["instructions"] == OLD_TEXT
    assert after[human["id"]]["name"] == OLD_NAME  # not an ai_task: not ours
    assert [s["name"] for s in db.get(WorkflowRun, run.id).steps] == ["Activate", "Log it"]
    names = {(r.step_type, r.step_name) for r in db.query(WorkflowStepRun).filter_by(flow_run_id=run.id)}
    assert names == {("ai_task", "Activate"), ("human_task", OLD_NAME), ("ai_task", "Log it")}


def test_the_migration_is_idempotent_and_reversible(db):
    m = _migration()
    org, _request = _world(db)
    step = _step(OLD_NAME)
    flow = Workflow(org_id=org.id, name="Custom flow", steps=[step])
    db.add(flow)
    db.flush()
    m.reword(db.connection())

    again = m.reword(db.connection())
    assert again == {"workflow": 0, "workflow_run": 0, "workflow_step_run": 0}

    m.reword(db.connection(), reverse=True)
    db.expire_all()
    [back] = db.get(Workflow, flow.id).steps
    assert back["name"] == OLD_NAME and back["config"]["instructions"] == OLD_TEXT


def test_the_migrations_wording_is_the_one_the_code_ships():
    """Guards the two copies drifting: a migration must not depend on app code that changes later, so it holds its own
    copy of the sentence, and this keeps the copies equal."""
    m = _migration()
    assert (m.NEW_NAME, m.NEW_TEXT) == (stages.ACTIVATE_STEP_NAME, stages.ACTIVATE_STEP_TEXT)
    assert (m.OLD_NAME, m.OLD_TEXT) == (OLD_NAME, OLD_TEXT)
    assert m.revision == "0078_activate_step_wording" and len(m.revision) <= 32 and m.down_revision == "0077_std_paper"


@pytest.mark.parametrize("steps", [None, "text", {"a": 1}, [1, "x", None]])
def test_a_steps_value_that_is_not_a_list_of_steps_is_left_alone(steps):
    out, changed = _migration().rewrite_steps(steps, old_name=OLD_NAME, new_name="Activate", old_text=OLD_TEXT,
                                              new_text="new")
    assert out == steps and changed is False
