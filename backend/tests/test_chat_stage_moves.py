"""Moving a contract's stage from the chat.

What must hold:
* a move that can't happen is refused BEFORE anyone is asked to confirm it, with a
  message that says what to do instead;
* "send for approval" completes the workflow's review step (so the approval chain is
  created by the workflow). It never changes the stage directly, which would leave
  the contract in Approval with no chain;
* open suggested changes stop a contract leaving Review, and the chat never overrides;
* the same request after the contract has moved again is a new request, not a replay of
  the old answer.

DB-free: the workflow service, lifecycle service and database are small fakes.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.ai import controller as controller_module
from app.ai import tool_runtime as tr
from app.ai.confirmations import pending_confirmation_for_session
from app.ai.tool_registry import AdvanceContractStageInput, tool_registry
from app.contracts import stage_moves as sm
from app.core.enums import AssistantToolCategory

ACTOR = SimpleNamespace(id="u-1", org_id="org-1", permission_values={"*"})


def contract(stage="review", title="Acme MSA", cid="c-1"):
    return SimpleNamespace(id=cid, org_id="org-1", title=title, lifecycle_stage=stage)


def step(idx, type_, status, name=None, **extra):
    return {"idx": idx, "type": type_, "name": name or f"Step {idx}", "status": status, **extra}


REVIEW_THEN_APPROVAL = [
    step(0, "human_task", "waiting_human", "Legal review"),
    step(1, "approval", "pending", "Finance sign-off", authority_note="Needs a Finance approver."),
]


class FakeWorkflows:
    """Just enough of WorkflowService for planning and executing a move."""

    def __init__(self, steps=None, *, status="waiting", current=0, can_act=True, on_advance=None):
        self.run = None if steps is None else SimpleNamespace(id="run-1", status=status, flow_name="MSA flow")
        self.steps, self.current, self.can_act, self.on_advance = steps, current, can_act, on_advance
        self.calls: list[tuple[str, dict]] = []

    def get_run_for_contract(self, *, contract_id, org_id):
        return self.run

    def serialize_run(self, run):
        return {"id": run.id, "status": run.status, "flow_name": run.flow_name, "current_index": self.current, "steps": self.steps}

    def can_act_on_step(self, *, run, step_idx, actor):
        return self.can_act

    def complete_human_step(self, **kw):
        self.calls.append(("complete", kw))

    async def advance_run(self, **kw):
        self.calls.append(("advance", kw))
        if self.on_advance:
            self.on_advance()


class FakeLifecycle:
    def __init__(self):
        self.calls: list[dict] = []

    def transition_contract_stage(self, **kw):
        self.calls.append(kw)
        kw["contract"].lifecycle_stage = kw["to_stage"]
        return kw["contract"]


def plan(monkeypatch, *, stage="review", to="approval", reason=None, open_=0, workflows=None, actor=ACTOR):
    monkeypatch.setattr(sm, "open_redline_count", lambda db, cid: open_)
    return sm.plan_stage_move(
        None, contract=contract(stage), to_stage=to, reason=reason, actor=actor,
        workflow_service=workflows if workflows is not None else FakeWorkflows(REVIEW_THEN_APPROVAL),
    )


def refused(monkeypatch, **kw) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        plan(monkeypatch, **kw)
    return exc.value


# ---- what can't happen is refused, in words ---------------------------------------

def test_an_unknown_stage_lists_the_real_ones(monkeypatch):
    err = refused(monkeypatch, to="executed")
    assert err.status_code == 422
    assert "Intake, Drafting, Review, Approval, Signature, Active, Closed" in err.detail


def test_asking_for_the_stage_it_is_already_in_says_so(monkeypatch):
    err = refused(monkeypatch, stage="review", to="review")
    assert err.status_code == 409 and "already in Review" in err.detail


def test_a_move_the_lifecycle_does_not_allow_names_where_it_can_go(monkeypatch):
    err = refused(monkeypatch, stage="drafting", to="closed")
    assert err.status_code == 409
    assert "can't move from Drafting to Closed" in err.detail and "Review" in err.detail


@pytest.mark.parametrize("stage,to,word", [("approval", "signature", "approval chain"), ("signature", "active", "signing")])
def test_moves_that_approval_and_signing_make_are_never_made_by_hand(monkeypatch, stage, to, word):
    err = refused(monkeypatch, stage=stage, to=to)
    assert err.status_code == 409 and word in err.detail and "by hand" in err.detail


@pytest.mark.parametrize("count,wording", [(1, "1 suggested change is still open"), (3, "3 suggested changes are still open")])
def test_open_suggestions_stop_a_contract_leaving_review(monkeypatch, count, wording):
    err = refused(monkeypatch, open_=count)
    assert err.status_code == 409
    assert wording in err.detail and "Review tab" in err.detail
    assert "Lifecycle tab" in err.detail  # where an allowed override is done


def test_open_suggestions_are_checked_before_the_workflow(monkeypatch):
    # No workflow at all, and open suggestions: the person is told about the suggestions first.
    err = refused(monkeypatch, open_=2, workflows=FakeWorkflows(None))
    assert "2 suggested changes" in err.detail


# ---- sending for approval goes through the workflow ---------------------------------

def test_send_for_approval_completes_the_workflow_step_and_never_changes_the_stage_directly(monkeypatch):
    p = plan(monkeypatch)
    assert p.via == "workflow_step"
    assert (p.run_id, p.step_idx, p.step_name) == ("run-1", 0, "Legal review")
    assert (p.from_stage, p.to_stage) == ("review", "approval")
    assert any("Creates the approval chain" in e for e in p.effects)
    assert "Needs a Finance approver." in p.effects


def test_the_stage_name_is_read_loosely(monkeypatch):
    assert plan(monkeypatch, to="  APPROVAL ").to_stage == "approval"


def test_no_workflow_means_nothing_can_send_it_for_approval(monkeypatch):
    err = refused(monkeypatch, workflows=FakeWorkflows(None))
    assert err.status_code == 409
    assert "No workflow is running" in err.detail and "Lifecycle tab" in err.detail


@pytest.mark.parametrize("status", ["complete", "failed", "cancelled"])
def test_a_finished_workflow_cannot_send_it_either(monkeypatch, status):
    err = refused(monkeypatch, workflows=FakeWorkflows(REVIEW_THEN_APPROVAL, status=status))
    assert "No workflow is running" in err.detail


def test_a_workflow_busy_with_another_step_is_not_waiting_for_the_review(monkeypatch):
    busy = [step(0, "ai_task", "running", "AI review"), step(1, "approval", "pending")]
    err = refused(monkeypatch, workflows=FakeWorkflows(busy))
    assert err.status_code == 409 and "busy with “AI review”" in err.detail


def test_a_step_that_is_not_followed_by_an_approval_will_not_start_one(monkeypatch):
    flow = [step(0, "human_task", "waiting_human", "Legal review"), step(1, "human_task", "pending", "Privacy review")]
    err = refused(monkeypatch, workflows=FakeWorkflows(flow))
    assert "goes to “Privacy review” (human_task), not to Approval" in err.detail


def test_the_last_step_has_nothing_after_it(monkeypatch):
    err = refused(monkeypatch, workflows=FakeWorkflows([step(0, "human_task", "waiting_human", "Legal review")]))
    assert "goes to nothing" in err.detail


def test_someone_who_may_not_complete_the_step_is_told_before_being_asked_to_confirm(monkeypatch):
    err = refused(monkeypatch, workflows=FakeWorkflows(REVIEW_THEN_APPROVAL, can_act=False))
    assert err.status_code == 403 and "assigned to someone else" in err.detail


def test_review_straight_to_signature_needs_a_signature_step_next(monkeypatch):
    err = refused(monkeypatch, to="signature")  # the next step is an approval
    assert "not to Signature" in err.detail
    flow = [step(0, "human_task", "waiting_human", "Legal review"), step(1, "signature", "pending", "Sign")]
    p = plan(monkeypatch, to="signature", workflows=FakeWorkflows(flow))
    assert p.via == "workflow_step" and p.to_stage == "signature"


# ---- direct moves ------------------------------------------------------------------

@pytest.mark.parametrize("stage,to", [("intake", "drafting"), ("intake", "review"), ("drafting", "review"), ("review", "drafting")])
def test_early_moves_are_direct_and_ignore_the_workflow(monkeypatch, stage, to):
    p = plan(monkeypatch, stage=stage, to=to, open_=5, workflows=FakeWorkflows(None))
    assert p.via == "transition" and p.run_id is None


@pytest.mark.parametrize("stage", ["approval", "signature"])
def test_pulling_a_contract_back_needs_a_reason(monkeypatch, stage):
    err = refused(monkeypatch, stage=stage, to="review")
    assert err.status_code == 422 and "Say why" in err.detail
    p = plan(monkeypatch, stage=stage, to="review", reason="  Terms changed  ")
    assert p.via == "transition" and p.reason == "Terms changed"


def test_closing_an_active_contract_is_direct(monkeypatch):
    p = plan(monkeypatch, stage="active", to="closed")
    assert p.via == "transition"
    assert "stays searchable" in p.effects[0]


def test_nothing_leaves_a_closed_contract(monkeypatch):
    err = refused(monkeypatch, stage="closed", to="active")
    assert "nowhere (it is closed)" in err.detail


# ---- the confirmation card ---------------------------------------------------------

def test_the_card_says_which_contract_and_what_will_happen_without_ids(monkeypatch):
    card = plan(monkeypatch).card()
    assert card["Contract"] == "Acme MSA"
    assert card["Move"] == "Review to Approval"
    assert "Completes the workflow step “Legal review”." in card.values()
    assert not any("c-1" in v or "run-1" in v for v in card.values())


def test_a_pull_back_card_carries_the_reason(monkeypatch):
    assert plan(monkeypatch, stage="approval", to="review", reason="Terms changed").card()["Reason"] == "Terms changed"


# ---- doing it ------------------------------------------------------------------------

def _run_execute(p, c, workflows, lifecycle=None):
    db = SimpleNamespace(get=lambda model, id: SimpleNamespace(id=id), refresh=lambda obj: None)
    lifecycle = lifecycle or FakeLifecycle()
    result = asyncio.run(sm.execute_stage_move(db, contract=c, plan=p, actor=ACTOR, workflow_service=workflows, lifecycle_service=lifecycle))
    return result, lifecycle


def test_sending_for_approval_completes_the_step_then_lets_the_workflow_advance(monkeypatch):
    c = contract("review")
    workflows = FakeWorkflows(REVIEW_THEN_APPROVAL, on_advance=lambda: setattr(c, "lifecycle_stage", "approval"))
    result, lifecycle = _run_execute(plan(monkeypatch), c, workflows)
    assert [name for name, _ in workflows.calls] == ["complete", "advance"]
    assert workflows.calls[0][1]["step_idx"] == 0
    assert lifecycle.calls == []  # the stage was NOT changed by hand
    assert result == {
        "moved": True, "contract_id": "c-1", "from_stage": "review", "stage": "approval",
        "via": "workflow_step", "completed_step": "Legal review",
    }


def test_the_result_is_honest_when_the_workflow_put_it_somewhere_else(monkeypatch):
    c = contract("review")
    result, _ = _run_execute(plan(monkeypatch), c, FakeWorkflows(REVIEW_THEN_APPROVAL))  # advance changes nothing
    assert result["moved"] is False and result["stage"] == "review"
    assert "now in Review, not Approval" in result["note"]


def test_a_direct_move_records_who_why_and_that_it_came_from_the_chat(monkeypatch):
    c = contract("approval")
    p = plan(monkeypatch, stage="approval", to="review", reason="Terms changed")
    result, lifecycle = _run_execute(p, c, FakeWorkflows(None))
    assert len(lifecycle.calls) == 1
    call = lifecycle.calls[0]
    assert call["to_stage"] == "review" and call["actor_user_id"] == "u-1"
    assert call["reason"] == "Moved from the chat: Terms changed"
    assert "override" not in call  # the chat never overrides
    assert result["moved"] is True and result["stage"] == "review"


def test_a_direct_move_without_a_reason_still_says_it_came_from_the_chat(monkeypatch):
    c = contract("drafting")
    _, lifecycle = _run_execute(plan(monkeypatch, stage="drafting", to="review"), c, FakeWorkflows(None))
    assert lifecycle.calls[0]["reason"] == "Moved from the chat"


# ---- the tool: refuse early, key on state, show the card ---------------------------------

class QueueDB:
    def __init__(self, *results):
        self.results, self.added = list(results), []

    def scalar(self, _stmt):
        return self.results.pop(0) if self.results else None

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def _stage_plan(frm="review", to="approval", via="workflow_step"):
    return sm.StagePlan(
        contract_id="c-1", title="Acme MSA", from_stage=frm, to_stage=to, via=via,
        run_id="run-1", step_idx=0, step_name="Legal review", effects=("Completes the workflow step “Legal review”.",),
    )


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(tr, "is_tool_enabled", lambda db, **kw: True)
    seen: dict = {"keys": [], "policy": None}

    def fake_replayed(db, *, spec, org_id, session_id, idempotency_key):
        seen["keys"].append(idempotency_key)

    def fake_create_confirmation(db, **kw):
        seen["policy"] = kw["policy"]
        return SimpleNamespace(id="conf-1")

    monkeypatch.setattr(tr.tool_runtime, "_replayed_call", fake_replayed)
    monkeypatch.setattr(tr, "create_confirmation", fake_create_confirmation)
    return seen


def _execute(db, tool="advance_contract_stage", args=None):
    return asyncio.run(tr.tool_runtime.execute(
        db, tool_name=tool, tool_input=args or {"to_stage": "approval"}, user=ACTOR, session_id="s-1",
    ))


def test_a_move_that_cannot_happen_is_refused_before_a_confirmation_or_call_record_exists(monkeypatch, runtime):
    def refuse(*a, **kw):
        raise HTTPException(409, "2 suggested changes are still open")

    monkeypatch.setattr(tr.tool_runtime, "_plan_stage_move", refuse)
    db = QueueDB()
    with pytest.raises(HTTPException) as exc:
        _execute(db)
    assert exc.value.status_code == 409
    assert db.added == [] and runtime["policy"] is None


def test_the_confirmation_carries_the_card_in_words(monkeypatch, runtime):
    p = _stage_plan()
    monkeypatch.setattr(tr.tool_runtime, "_plan_stage_move", lambda *a, **kw: p)
    out = _execute(QueueDB())
    assert out["confirmation_required"] is True and out["confirmation_id"] == "conf-1"
    assert out["details"] == p.card()
    assert runtime["policy"]["card"] == p.card()


def test_the_same_words_are_the_same_call_only_while_the_contract_is_in_the_same_state(monkeypatch, runtime):
    plans = iter([_stage_plan("review", "approval"), _stage_plan("review", "approval"), _stage_plan("drafting", "review", "transition")])
    monkeypatch.setattr(tr.tool_runtime, "_plan_stage_move", lambda *a, **kw: next(plans))
    for _ in range(3):
        _execute(QueueDB())
    first, again, other_state = runtime["keys"]
    assert first == again  # a retry while nothing changed is still a replay
    assert first != other_state  # after the contract moved, the same words are a new request


def test_a_repeat_after_the_move_happened_is_refused_not_answered_with_the_old_success(monkeypatch, runtime):
    # The first call moved the contract to Review. Asking again plans from Review to Review.
    monkeypatch.setattr(tr, "ContractService", lambda db: SimpleNamespace(get_contract_for_user=lambda **kw: contract("review")))
    monkeypatch.setattr(sm, "open_redline_count", lambda db, cid: 0)
    monkeypatch.setattr(tr.tool_runtime, "_resolve_stage_contract", lambda db, **kw: contract("review"))
    monkeypatch.setattr("app.workflows.service.WorkflowService", lambda db: FakeWorkflows(None))
    with pytest.raises(HTTPException) as exc:
        _execute(QueueDB(), args={"to_stage": "review"})
    assert exc.value.status_code == 409 and "already in Review" in exc.value.detail
    assert runtime["keys"] == []  # never reached the replay lookup


def test_other_tools_are_not_planned(monkeypatch, runtime):
    def boom(*a, **kw):
        raise AssertionError("only stage moves are planned")

    monkeypatch.setattr(tr.tool_runtime, "_plan_stage_move", boom)
    out = _execute(QueueDB(), tool="complete_task", args={"task_id": "t-1"})
    assert out["confirmation_required"] is True and "details" not in out


# ---- which contract is meant -----------------------------------------------------------------

class ScalarsDB:
    def __init__(self, handles=(), scalar_result=None):
        self.handles, self.scalar_result = list(handles), scalar_result

    def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: self.handles)

    def scalar(self, _stmt):
        return self.scalar_result


class FakeContractService:
    def __init__(self, db):
        pass

    def get_contract_for_user(self, *, contract_id, user):
        return contract(cid=contract_id)


def _resolve(db, **payload):
    return tr.tool_runtime._resolve_stage_contract(
        db, payload=AdvanceContractStageInput(to_stage="review", **payload), user=ACTOR, session_id="s-1"
    )


def test_a_handle_names_the_contract(monkeypatch):
    monkeypatch.setattr(tr, "ContractService", FakeContractService)
    assert _resolve(ScalarsDB(scalar_result=SimpleNamespace(contract_id="c-9")), contract_handle="contract-0").id == "c-9"


def test_a_contract_id_names_the_contract(monkeypatch):
    monkeypatch.setattr(tr, "ContractService", FakeContractService)
    assert _resolve(ScalarsDB(), contract_id="c-7").id == "c-7"


def test_with_nothing_named_a_chat_about_one_contract_means_that_contract(monkeypatch):
    monkeypatch.setattr(tr, "ContractService", FakeContractService)
    assert _resolve(ScalarsDB(handles=[SimpleNamespace(contract_id="c-5")])).id == "c-5"


@pytest.mark.parametrize("handles", [[], [SimpleNamespace(contract_id="a"), SimpleNamespace(contract_id="b")]])
def test_it_does_not_guess_between_several_contracts_or_none(monkeypatch, handles):
    monkeypatch.setattr(tr, "ContractService", FakeContractService)
    with pytest.raises(HTTPException) as exc:
        _resolve(ScalarsDB(handles=handles))
    assert exc.value.status_code == 422 and "Say which contract" in exc.value.detail


# ---- reading what is open -------------------------------------------------------------------------

def _edit(i, citation, original="Net 30.", replacement="Net 60.", version="v-1"):
    return SimpleNamespace(
        id=f"e-{i}", edit_type="replace", original_text=original, replacement_text=replacement,
        rationale=f"Because {i}", citation=citation, contract_version_id=version,
    )


def test_open_suggestions_can_be_listed_with_where_each_came_from(monkeypatch):
    monkeypatch.setattr(tr, "ContractService", FakeContractService)
    monkeypatch.setattr(tr, "open_redline_count", lambda db, cid: 4)
    c = contract()
    c.current_authoritative_version_id = "v-1"
    monkeypatch.setattr(tr.tool_runtime, "_resolve_contract", lambda db, **kw: c)
    edits = [
        _edit(1, [{"type": "playbook_deviation", "playbook_deviation_id": "d-1"}]),
        _edit(2, [{"type": "assistant_edit_version", "version_id": "v-9"}], original="x" * 500),
        _edit(3, [], version="v-0"),
    ]
    db = SimpleNamespace(scalars=lambda stmt: SimpleNamespace(all=lambda: edits))
    out = tr.tool_runtime._list_contract_suggestions(db, payload=SimpleNamespace(), user=ACTOR, session_id="s-1")
    assert out["open_suggestions"] == 4 and out["shown"] == 3
    assert [s["made_by"] for s in out["suggestions"]] == ["playbook", "chat", "manual"]
    assert [s["for_current_version"] for s in out["suggestions"]] == [True, True, False]
    assert len(out["suggestions"][1]["original"]) == 300 and out["suggestions"][1]["original"].endswith("…")


# ---- the registry and the card --------------------------------------------------------------------------

def test_the_stage_tool_still_asks_first_and_the_list_tool_never_does():
    stage = tool_registry.get("advance_contract_stage")
    assert stage.requires_confirmation and stage.category != AssistantToolCategory.READ_ONLY
    listing = tool_registry.get("list_contract_suggestions")
    assert listing.category == AssistantToolCategory.READ_ONLY and not listing.requires_confirmation


def test_the_model_can_name_the_contract_by_handle_and_give_a_reason():
    fields = AdvanceContractStageInput.model_fields
    assert {"contract_handle", "contract_id", "request_id", "to_stage", "reason"} <= set(fields)


def test_approvals_still_cannot_be_started_by_a_tool_other_than_through_a_workflow():
    assert "submit_for_approval" not in {t.name for t in tool_registry.all()}


def test_a_live_confirmation_event_uses_the_tools_card_else_the_raw_arguments():
    card = {"Contract": "Acme MSA", "Move": "Review to Approval"}
    assert controller_module._card_details({"details": card}, {"to_stage": "approval"}) == card
    assert controller_module._card_details({}, {"to_stage": "approval"}) == {"to_stage": "approval"}
    assert controller_module._card_details({"details": {}}, {"to_stage": "approval"}) == {"to_stage": "approval"}


def test_both_places_that_pause_for_confirmation_use_it():
    src = inspect.getsource(controller_module)
    assert '_card_details(result, tool_use.get("input"))' in src
    assert '_card_details(loop_result, tool_use.get("input"))' in src


def test_a_reloaded_chat_shows_the_same_card():
    run = SimpleNamespace(id="run-1")
    conf = SimpleNamespace(
        id="conf-1", tool_name="advance_contract_stage", tool_input={"to_stage": "approval", "contract_handle": "contract-0"},
        policy={"card": {"Contract": "Acme MSA", "Move": "Review to Approval"}}, expires_at=None,
    )
    out = pending_confirmation_for_session(QueueDB(run, conf), session_id="s-1", org_id="org-1")
    assert out["details"] == {"Contract": "Acme MSA", "Move": "Review to Approval"}


def test_a_reloaded_chat_falls_back_to_the_arguments_for_older_confirmations():
    run = SimpleNamespace(id="run-1")
    conf = SimpleNamespace(id="conf-1", tool_name="complete_task", tool_input={"task_id": "t-1"}, policy={}, expires_at=None)
    out = pending_confirmation_for_session(QueueDB(run, conf), session_id="s-1", org_id="org-1")
    assert out["details"] == {"task_id": "t-1"}
