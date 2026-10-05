"""Org-scoped fetch/guard helpers for the approval_chains domain.

Every helper here filters on the actor's ``org_id`` (AC-18) — 404, not 403,
on a cross-org reference, matching ``app.org_structure.access``'s framing
(not leaking existence via a 403). Org-unit lookups are NOT reimplemented
here — ``get_org_unit_or_404``/``get_org_root`` are imported directly from
``app.org_structure.access`` per plan.md.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approval_chains.models import (
    ApprovalChainDefinition,
    ApprovalChainInstance,
    ApprovalChainRequirement,
    ApprovalChainStep,
    ApprovalChainStepRule,
)
from app.auth.models import User
from app.core import org_access
from app.core.rbac import has_permission
from app.org_structure.access import get_org_root, get_org_unit_or_404  # noqa: F401 (re-exported)


def get_definition_or_404(
    db: Session, org_id: str, definition_id: str, *, include_deleted: bool = False
) -> ApprovalChainDefinition:
    stmt = select(ApprovalChainDefinition).where(
        ApprovalChainDefinition.id == definition_id, ApprovalChainDefinition.org_id == org_id
    )
    if not include_deleted:
        stmt = stmt.where(ApprovalChainDefinition.deleted_at.is_(None))
    definition = db.execute(stmt).scalar_one_or_none()
    if definition is None:
        raise HTTPException(404, "Chain definition not found")
    return definition


def get_step_or_404(
    db: Session, org_id: str, step_id: str, *, include_deleted: bool = False
) -> ApprovalChainStep:
    stmt = select(ApprovalChainStep).where(
        ApprovalChainStep.id == step_id, ApprovalChainStep.org_id == org_id
    )
    if not include_deleted:
        stmt = stmt.where(ApprovalChainStep.deleted_at.is_(None))
    step = db.execute(stmt).scalar_one_or_none()
    if step is None:
        raise HTTPException(404, "Chain step not found")
    return step


def get_rule_or_404(
    db: Session, org_id: str, rule_id: str, *, include_deleted: bool = False
) -> ApprovalChainStepRule:
    stmt = select(ApprovalChainStepRule).where(
        ApprovalChainStepRule.id == rule_id, ApprovalChainStepRule.org_id == org_id
    )
    if not include_deleted:
        stmt = stmt.where(ApprovalChainStepRule.deleted_at.is_(None))
    rule = db.execute(stmt).scalar_one_or_none()
    if rule is None:
        raise HTTPException(404, "Chain step rule not found")
    return rule


def get_instance_or_404(
    db: Session, org_id: str, instance_id: str, *, include_deleted: bool = False
) -> ApprovalChainInstance:
    stmt = select(ApprovalChainInstance).where(
        ApprovalChainInstance.id == instance_id, ApprovalChainInstance.org_id == org_id
    )
    if not include_deleted:
        stmt = stmt.where(ApprovalChainInstance.deleted_at.is_(None))
    instance = db.execute(stmt).scalar_one_or_none()
    if instance is None:
        raise HTTPException(404, "Chain instance not found")
    return instance


def get_requirement_or_404(
    db: Session, org_id: str, requirement_id: str, *, include_deleted: bool = False
) -> ApprovalChainRequirement:
    stmt = select(ApprovalChainRequirement).where(
        ApprovalChainRequirement.id == requirement_id, ApprovalChainRequirement.org_id == org_id
    )
    if not include_deleted:
        stmt = stmt.where(ApprovalChainRequirement.deleted_at.is_(None))
    requirement = db.execute(stmt).scalar_one_or_none()
    if requirement is None:
        raise HTTPException(404, "Chain requirement not found")
    return requirement


def get_org_role_or_404(db: Session, org_id: str, role_id: str):
    """Fetch a ``Role`` scoped to ``org_id``. 404s when missing or in another
    org — a rule can never reference a different organization's role.
    """
    # local import: keep this module's module-level dependency on app.auth
    # limited to the User import already above.
    from app.auth.models import Role

    stmt = select(Role).where(Role.id == role_id, Role.org_id == org_id)
    role = db.execute(stmt).scalar_one_or_none()
    if role is None:
        raise HTTPException(404, "Role not found")
    return role


def get_module_record_or_404(
    db: Session, *, actor: User, module: str, record_id: str, enforce_actor_access: bool = True,
):
    """Fetch the underlying business record for ``module``/``record_id``,
    org-scoped to ``actor.org_id``, applying the SAME access control the
    record's own domain already enforces for reading it:

    - ``"contract"``: org-scoped fetch PLUS
      ``app.contracts.access.user_can_access_contract`` — ethical walls and
      insufficient clearance keep overriding, unchanged (plan.md's explicit
      requirement).
    - ``"intake_request"``: org-scoped fetch. Reading an intake request today
      is gated at the route level by the ``intake:read`` permission with no
      additional per-record check beyond ``org_id`` (verified in
      ``app.intake.service``/``routes.py`` — no ethical-wall equivalent
      exists for intake requests), so the equivalent here is the org-scoped
      fetch alone.

    ``enforce_actor_access=False`` skips the ``user_can_access_contract``
    layer (org+existence is still checked) — used only when ``actor`` isn't
    the person actually acting on the record, but the workflow engine
    auto-advancing a run on their behalf (see
    ``app.approval_chains.dispatch.start_chain_for_subject``): completing an
    earlier, unrelated step (e.g. a human_task assigned via a team) must not
    fail because the record-level check — ethical walls, MAC clearance,
    ownership, matter membership — was written for a human directly
    creating a chain instance via this module's own POST /instances route,
    not for "whoever happened to trigger the engine's next hop." The named
    approvers on the chain still get their own access check when THEY act.

    Raises HTTPException(422) for an unrecognized module (fails closed,
    matching ``facts.build_facts``), HTTPException(404) when the record is
    missing, belongs to another org, or (only when ``enforce_actor_access``)
    is blocked by the record's own access control.
    """
    if module == "contract":
        from app.contracts.models import Contract

        contract = db.get(Contract, record_id)
        if contract is None or contract.org_id != actor.org_id:
            raise HTTPException(404, "Contract not found")
        if enforce_actor_access:
            from app.contracts.access import user_can_access_contract

            if not user_can_access_contract(db, contract=contract, user=actor):
                raise HTTPException(404, "Contract not found")
        return contract

    if module == "intake_request":
        from app.intake.models import IntakeRequest

        request = db.get(IntakeRequest, record_id)
        if request is None or request.org_id != actor.org_id:
            raise HTTPException(404, "Request not found")
        return request

    raise HTTPException(422, f"Unknown module '{module}'")


def assert_instance_visible(db: Session, *, actor: User, instance: ApprovalChainInstance) -> None:
    """The spec's "Permissions, scoping & audit" visibility rule for a chain
    instance (FR-5, FR-7, FR-10): visible to eligible approvers on that chain
    instance, the requester, and organization administrators, scoped
    strictly to the viewer's own organization; ethical walls on the
    underlying item keep overriding, unaffected by this feature.

    Concretely: ``instance.org_id == actor.org_id`` AND any of —
    the actor is ``instance.started_by_user_id``; the actor is an eligible
    holder of any of the instance's materialized required roles; the actor
    holds ``approval_chain:manage`` — AND, for ``module == "contract"``, the
    existing ``app.contracts.access.user_can_access_contract`` check must
    also pass (ethical walls / clearance keep overriding).

    Raises HTTPException(404) — "not found", not 403, matching this
    codebase's convention of never leaking existence of an out-of-scope row.
    """
    if instance.org_id != actor.org_id:
        raise HTTPException(404, "Chain instance not found")

    permissions = org_access.effective_permission_values(db, user=actor)
    is_manager = has_permission(permissions, "approval_chain:manage")
    is_requester = instance.started_by_user_id == actor.id

    is_eligible_holder = False
    if not (is_manager or is_requester):
        role_ids = {
            row[0]
            for row in db.execute(
                select(ApprovalChainRequirement.required_role_id).where(
                    ApprovalChainRequirement.instance_id == instance.id,
                    ApprovalChainRequirement.deleted_at.is_(None),
                )
            ).all()
        }
        for role_id in role_ids:
            holders = org_access.users_holding_role(
                db, org_id=instance.org_id, role_id=role_id, org_unit_id=instance.org_unit_id
            )
            if any(holder.user_id == actor.id for holder in holders):
                is_eligible_holder = True
                break

    if not (is_manager or is_requester or is_eligible_holder):
        raise HTTPException(404, "Chain instance not found")

    if instance.module == "contract":
        from app.contracts.access import user_can_access_contract
        from app.contracts.models import Contract

        contract = db.get(Contract, instance.module_record_id)
        if contract is None or not user_can_access_contract(db, contract=contract, user=actor):
            raise HTTPException(404, "Chain instance not found")
