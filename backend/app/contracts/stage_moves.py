"""Moving a contract between stages when someone asks for it in words (the chat).

The stage rules live in ``lifecycle.py``. This module works out, BEFORE anyone is
asked to confirm, whether a requested move can happen and how:

* Intake / Drafting / Review moves, pulling a contract back, and closing it are
  direct stage changes.
* "Send for approval" (and Review straight to Signature) is NOT a direct change. An
  approval chain is only ever created by the workflow's Approval step, so the
  request means "complete the workflow's current review step", and the workflow
  makes the move. A contract moved to Approval by hand would sit there with no
  chain and could never get to Signature.
* Approval to Signature and Signature to Active are made by the approval chain
  finishing and by signing. They are never made by hand from here.

Nothing here overrides a rule. If suggested changes are still open, leaving Review
is refused with the count; overriding stays in the Lifecycle tab, where a reason
is asked for and recorded.

``plan_stage_move`` only reads. ``execute_stage_move`` plans again (the contract may
have moved since the person was asked) and then acts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.contracts.lifecycle import ALLOWED_TRANSITIONS, open_redline_count
from app.core.enums import ContractLifecycleStage

STAGES: tuple[str, ...] = tuple(s.value for s in ContractLifecycleStage)
STAGE_LABEL = {s: s.capitalize() for s in STAGES}
_PRE_APPROVAL = {"intake", "drafting", "review"}
# Stages whose move back to Review is a pull-back, recorded with a reason.
_PULL_BACK_FROM = {"approval", "signature"}


def _label(stage: str) -> str:
    return STAGE_LABEL.get(stage, stage)


def _refuse(code: int, message: str) -> HTTPException:
    return HTTPException(code, message)


@dataclass(frozen=True)
class StagePlan:
    contract_id: str
    title: str
    from_stage: str
    to_stage: str
    # "transition": change the stage directly. "workflow_step": complete the
    # workflow's current step; the workflow makes the move.
    via: str
    reason: str | None = None
    run_id: str | None = None
    step_idx: int | None = None
    step_name: str | None = None
    effects: tuple[str, ...] = ()

    def card(self) -> dict[str, str]:
        """What the confirmation shows, in words (the raw arguments are ids)."""
        card: dict[str, str] = {
            "Contract": self.title,
            "Move": f"{_label(self.from_stage)} to {_label(self.to_stage)}",
        }
        for i, line in enumerate(self.effects, start=1):
            card[f"Effect {i}" if len(self.effects) > 1 else "Effect"] = line
        if self.reason:
            card["Reason"] = self.reason
        return card


def normalise_stage(raw: str) -> str:
    stage = (raw or "").strip().lower()
    if stage not in STAGES:
        raise _refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"“{raw}” isn't a stage. The stages are: {', '.join(_label(s) for s in STAGES)}.",
        )
    return stage


def _step(run: dict[str, Any], idx: int) -> dict[str, Any] | None:
    return next((s for s in run.get("steps") or [] if s.get("idx") == idx), None)


_FINISHED_STEP = ("done", "skipped", "failed")


def _step_waiting_for_a_person(
    run: dict[str, Any], *, actor_id: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The step a person is to complete now, and the step that comes after it.

    ``current_index`` is the first step of the current group, and steps flagged ``parallel`` run side by side
    with the one above. Once the first of them is done it is still the "current" index, so look through the
    whole group for the one that is waiting (the actor's own first). The step after it is the next open step
    of the group while others are still open, and the first step after the group once this is the last."""
    steps = sorted(run.get("steps") or [], key=lambda s: s.get("idx", 0))
    start = run.get("current_index", 0)
    group = [s for s in steps if s.get("idx") == start]
    for s in steps:
        if s.get("idx", 0) > start and s.get("parallel") and group and group[-1].get("idx") == s.get("idx", 0) - 1:
            group.append(s)
    waiting = [s for s in group if s.get("status") == "waiting_human"]
    mine = [s for s in waiting if actor_id and s.get("assignee_user_id") == actor_id]
    cur = (mine or waiting or group or [None])[0]
    if cur is None:
        return None, None
    siblings_open = [s for s in group if s is not cur and s.get("status") not in _FINISHED_STEP]
    if siblings_open:
        return cur, siblings_open[0]
    return cur, _step(run, group[-1]["idx"] + 1)


def _workflow_step_for(
    *, contract, to_stage: str, workflow_service, actor
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """The run and current step that will make this move, or a refusal that says
    what to do instead."""
    where = "the Lifecycle tab"
    label = _label(to_stage)
    run = workflow_service.get_run_for_contract(contract_id=contract.id, org_id=contract.org_id)
    if run is None or run.status in ("complete", "failed", "cancelled"):
        raise _refuse(
            status.HTTP_409_CONFLICT,
            f"No workflow is running for this contract, and only a workflow's step can send a contract to {label}. "
            f"Start a workflow from {where}, then ask again.",
        )
    view = workflow_service.serialize_run(run)
    cur, nxt = _step_waiting_for_a_person(view, actor_id=getattr(actor, "id", None))
    if cur is None or cur.get("status") != "waiting_human":
        name = f"“{cur['name']}”" if cur else "its current step"
        raise _refuse(
            status.HTTP_409_CONFLICT,
            f"The workflow is busy with {name}. I can send the contract to {label} once the review step is waiting for you.",
        )
    wanted = "approval" if to_stage == "approval" else "signature"
    if nxt is None or nxt.get("type") != wanted:
        after = f"“{nxt['name']}” ({nxt['type']})" if nxt else "nothing"
        raise _refuse(
            status.HTTP_409_CONFLICT,
            f"After “{cur['name']}” the workflow goes to {after}, not to {label}, so completing it won't send the contract there. "
            f"Complete the step from {where} if that is what you want.",
        )
    if not workflow_service.can_act_on_step(run=run, step_idx=cur["idx"], actor=actor):
        raise _refuse(
            status.HTTP_403_FORBIDDEN,
            f"“{cur['name']}” is assigned to someone else. Only its assignee or its team can complete it.",
        )
    return run, cur, nxt


def plan_stage_move(
    db: Session, *, contract, to_stage: str, reason: str | None, actor, workflow_service
) -> StagePlan:
    """Decide whether ``contract`` can be moved to ``to_stage`` now, and how.
    Raises HTTPException with a message written for the person asking."""
    to_stage = normalise_stage(to_stage)
    from_stage = str(contract.lifecycle_stage)
    reason = (reason or "").strip() or None
    title = contract.title or "this contract"

    if from_stage == to_stage:
        raise _refuse(status.HTTP_409_CONFLICT, f"“{title}” is already in {_label(to_stage)}.")

    allowed = sorted(ALLOWED_TRANSITIONS.get(from_stage, set()), key=STAGES.index)
    if to_stage not in allowed:
        options = ", ".join(_label(s) for s in allowed) or "nowhere (it is closed)"
        raise _refuse(
            status.HTTP_409_CONFLICT,
            f"A contract can't move from {_label(from_stage)} to {_label(to_stage)}. From {_label(from_stage)} it can go to: {options}.",
        )

    # Moves another process makes. Never made by hand from here.
    if (from_stage, to_stage) == ("approval", "signature"):
        raise _refuse(
            status.HTTP_409_CONFLICT,
            "A contract moves from Approval to Signature when its approval chain is fully approved. It can't be moved by hand.",
        )
    if (from_stage, to_stage) == ("signature", "active"):
        raise _refuse(
            status.HTTP_409_CONFLICT,
            "A contract becomes Active when signing is complete. It can't be moved by hand.",
        )

    leaving_review_for_later = from_stage in _PRE_APPROVAL and to_stage not in _PRE_APPROVAL
    if leaving_review_for_later:
        open_redlines = open_redline_count(db, contract.id)
        if open_redlines > 0:
            plural = "s" if open_redlines != 1 else ""
            raise _refuse(
                status.HTTP_409_CONFLICT,
                f"{open_redlines} suggested change{plural} {'are' if open_redlines != 1 else 'is'} still open on “{title}”. "
                "Accept or reject them in the Review tab first. If you are allowed to send it anyway, "
                "that is done from the Lifecycle tab, with a reason.",
            )
        run, cur, nxt = _workflow_step_for(
            contract=contract, to_stage=to_stage, workflow_service=workflow_service, actor=actor
        )
        effects = [f"Completes the workflow step “{cur['name']}”."]
        if to_stage == "approval":
            effects.append("Creates the approval chain and moves the contract to Approval.")
            if nxt.get("authority_note"):
                effects.append(str(nxt["authority_note"]))
        else:
            effects.append("Moves the contract to Signature.")
        return StagePlan(
            contract_id=contract.id, title=title, from_stage=from_stage, to_stage=to_stage, via="workflow_step",
            reason=reason, run_id=run.id, step_idx=cur["idx"], step_name=cur["name"], effects=tuple(effects),
        )

    if from_stage in _PULL_BACK_FROM and to_stage == "review" and not reason:
        raise _refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Say why it is going back to Review from {_label(from_stage)}. The reason is recorded.",
        )

    effects: list[str] = []
    if to_stage == "closed":
        effects.append("Closes the contract. It stays searchable and held for retention.")
    elif from_stage in _PULL_BACK_FROM:
        effects.append("Pulls the contract back to Review for changes.")
    else:
        effects.append(f"Moves the contract to {_label(to_stage)}.")
    return StagePlan(
        contract_id=contract.id, title=title, from_stage=from_stage, to_stage=to_stage, via="transition",
        reason=reason, effects=tuple(effects),
    )


async def execute_stage_move(
    db: Session, *, contract, plan: StagePlan, actor, workflow_service, lifecycle_service, request_id: str | None = None
) -> dict[str, Any]:
    """Do what ``plan`` says. The caller commits.

    The result says what really happened: ``moved`` is true only when the contract
    is now in the stage that was asked for."""
    if plan.via == "workflow_step":
        run = db.get(_workflow_run_model(), plan.run_id)
        if run is None:
            raise _refuse(status.HTTP_404_NOT_FOUND, "The workflow for this contract is gone.")
        workflow_service.complete_human_step(
            run=run, actor=actor, note="Completed from the chat", step_idx=plan.step_idx, request_id=request_id,
        )
        await workflow_service.advance_run(run=run, actor=actor)
        db.refresh(contract)
    else:
        why = f"Moved from the chat: {plan.reason}" if plan.reason else "Moved from the chat"
        lifecycle_service.transition_contract_stage(
            contract=contract, to_stage=plan.to_stage, actor_user_id=actor.id, reason=why, request_id=request_id,
        )
    now = str(contract.lifecycle_stage)
    result: dict[str, Any] = {
        "moved": now == plan.to_stage,
        "contract_id": plan.contract_id,
        "from_stage": plan.from_stage,
        "stage": now,
        "via": plan.via,
    }
    if plan.via == "workflow_step":
        result["completed_step"] = plan.step_name
        if now != plan.to_stage:
            result["note"] = (
                f"The step “{plan.step_name}” was completed, but the contract is now in {_label(now)}, "
                f"not {_label(plan.to_stage)}. The workflow decides where it goes next."
            )
    return result


def _workflow_run_model():
    from app.workflows.models import WorkflowRun

    return WorkflowRun
