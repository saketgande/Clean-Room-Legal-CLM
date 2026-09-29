"""The FR-22 seam — the ONLY module ``app/approvals/service.py`` and
``app/workflows/service.py`` import from this domain (both lazily, inside the
function, per this codebase's cross-domain lazy-import convention). Kept
deliberately small and read-only-friendly so the edit inside those two
existing domains stays a handful of lines while this domain owns all the
logic (plan.md "The FR-22 interception point").
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approval_chains.models import ApprovalChainDefinition, ApprovalChainInstance
from app.approval_chains.schemas import ChainInstanceCreate
from app.auth.models import User


def active_definition_for(
    db: Session, *, org_id: str, subject_kind: str
) -> ApprovalChainDefinition | None:
    """The single active, non-deleted definition for ``(org_id, module=subject_kind)``.

    ``subject_kind`` is the existing subject protocol's ``kind`` attribute,
    whose values are already exactly ``"contract"`` and ``"intake_request"``
    — so the discriminator needs no translation table. Returns ``None`` when
    the org has none (the fail-safe legacy-fallback branch).
    """
    return db.scalar(
        select(ApprovalChainDefinition).where(
            ApprovalChainDefinition.org_id == org_id,
            ApprovalChainDefinition.module == subject_kind,
            ApprovalChainDefinition.is_active.is_(True),
            ApprovalChainDefinition.deleted_at.is_(None),
        )
    )


def start_chain_for_subject(
    db: Session,
    *,
    actor: User,
    subject,
    definition: ApprovalChainDefinition,
    request_id: str | None = None,
) -> ApprovalChainInstance:
    """Idempotent: returns the existing live instance for
    ``(definition, subject.kind, subject.id)`` unchanged if one exists, else
    materializes step 1 and fires ``subject.on_submit(...)`` via
    ``service.create_instance``. Never raises 409 on re-submission, because
    ``submit_subject_for_approval`` is contractually idempotent.

    ``actor`` here is whoever's action (completing a workflow step, filing a
    request, etc.) caused the engine to reach this point — not necessarily
    someone with any standing relationship to the record itself (e.g. a
    human_task assignee completing their own step, which auto-advances the
    run into a contract-approval step for a contract they were never given
    direct access to). ``enforce_actor_access=False`` on the underlying
    ``create_instance`` call reflects that: the workflow engine itself
    already authorized reaching this step, so the record-level access check
    written for a human directly hitting POST /instances doesn't apply here.
    The chain's named approvers still get their own access check when they
    act on it.
    """
    from app.approval_chains import service

    payload = ChainInstanceCreate(
        definition_id=definition.id,
        module=subject.kind,
        module_record_id=subject.id,
        org_unit_id=None,
    )
    return service.create_instance(
        db,
        actor=actor,
        payload=payload,
        subject=subject,
        raise_on_existing=False,
        request_id=request_id,
        enforce_actor_access=False,
    )


def live_instance_for(
    db: Session, *, org_id: str, module: str, module_record_id: str
) -> ApprovalChainInstance | None:
    """Read-only lookup: the newest non-deleted instance for a record
    (pending first, then most recently updated) or ``None``. Used by
    ``app/workflows/service.py`` to tell "a chain was started" apart from
    "nothing to approve", and by ``app/intake/service.py`` to find the chain
    backing a rerouted request.
    """
    stmt = (
        select(ApprovalChainInstance)
        .where(
            ApprovalChainInstance.org_id == org_id,
            ApprovalChainInstance.module == module,
            ApprovalChainInstance.module_record_id == module_record_id,
            ApprovalChainInstance.deleted_at.is_(None),
        )
        .order_by(
            (ApprovalChainInstance.status == "pending").desc(),
            ApprovalChainInstance.updated_at.desc(),
        )
    )
    return db.scalars(stmt).first()


def intake_strip_rungs(db: Session, *, instance: ApprovalChainInstance) -> list[dict]:
    """The intake ladder strip's rung dicts, built from a chain instance's
    materialized requirements. Pure read — evaluates NO conditions (FR-5).
    """
    from app.approval_chains.models import ApprovalChainRequirement, ApprovalChainStep

    steps = db.scalars(
        select(ApprovalChainStep)
        .where(
            ApprovalChainStep.definition_id == instance.definition_id,
            ApprovalChainStep.deleted_at.is_(None),
        )
        .order_by(ApprovalChainStep.sequence_order)
    ).all()
    step_order_by_id = {step.id: idx + 1 for idx, step in enumerate(steps)}

    requirements = db.scalars(
        select(ApprovalChainRequirement)
        .where(
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.deleted_at.is_(None),
            ApprovalChainRequirement.superseded_at.is_(None),
        )
        .order_by(ApprovalChainRequirement.step_id, ApprovalChainRequirement.sequence_order)
    ).all()

    rungs: list[dict] = []
    for requirement in requirements:
        explanation = (
            requirement.condition_explanations[0]["text"]
            if requirement.condition_explanations
            else None
        )
        if requirement.condition_explanations and len(requirement.condition_explanations) > 1:
            explanation = "; and ".join(
                f"required because {e['text']}" for e in requirement.condition_explanations
            )
        elif explanation is not None:
            explanation = f"required because {explanation}"
        rungs.append({
            "approval_request_id": None,
            "requirement_id": requirement.id,
            "chain_instance_id": instance.id,
            "step_order": step_order_by_id.get(requirement.step_id, requirement.sequence_order),
            "status": requirement.status,
            "approver_label": requirement.required_role.name if requirement.required_role else "",
            "due_at": None,
            "explanation": explanation,
            "blocked": requirement.is_unfulfillable,
        })
    return rungs


def intake_planned_rungs(
    db: Session, *, org_id: str, definition: ApprovalChainDefinition
) -> list[dict]:
    """The rung shape for a request that has NOT been submitted yet: the
    first step's BASE requirements only, each with status "planned".
    Condition rules are deliberately NOT evaluated here (FR-5 confines
    evaluation to chain entry).
    """
    from app.approval_chains.models import ApprovalChainStep, ApprovalChainStepRule

    first_step = db.scalar(
        select(ApprovalChainStep)
        .where(
            ApprovalChainStep.definition_id == definition.id,
            ApprovalChainStep.deleted_at.is_(None),
        )
        .order_by(ApprovalChainStep.sequence_order)
    )
    if first_step is None:
        return []

    base_rules = db.scalars(
        select(ApprovalChainStepRule)
        .where(
            ApprovalChainStepRule.step_id == first_step.id,
            ApprovalChainStepRule.is_base_requirement.is_(True),
            ApprovalChainStepRule.is_active.is_(True),
            ApprovalChainStepRule.deleted_at.is_(None),
        )
        .order_by(ApprovalChainStepRule.sequence_order)
    ).all()

    return [
        {
            "approval_request_id": None,
            "requirement_id": None,
            "chain_instance_id": None,
            "step_order": 1,
            "status": "planned",
            "approver_label": rule.required_role.name if rule.required_role else "",
            "due_at": None,
            "explanation": None,
            "blocked": False,
        }
        for rule in base_rules
    ]
