"""Business logic for the approval_chains domain (feature 004).

Every mutating function here is org-scoped and writes an audit row (and,
where the spec requires it, an append-only history row) in the same
transaction as the change. ``_materialize_step`` — the FR-5 evaluation — is
called from EXACTLY TWO places in this entire codebase: ``create_instance``
and ``_advance_step``. Never from a read path (verified by a source-scan
regression test in ``tests/test_approval_chain_recalculate.py``).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approval_chains import access, conditions, facts, subjects
from app.approval_chains.models import (
    ApprovalChainDefinition,
    ApprovalChainHistory,
    ApprovalChainInstance,
    ApprovalChainRequirement,
    ApprovalChainStep,
    ApprovalChainStepRule,
)
from app.approval_chains.schemas import (
    ChainDecisionPayload,
    ChainDefinitionCreate,
    ChainDefinitionUpdate,
    ChainInstanceCreate,
    ChainRecalculatePayload,
    ChainStepCreate,
    ChainStepRuleCreate,
    ChainStepRuleUpdate,
    ChainStepUpdate,
)
from app.auth.models import User
from app.core import org_access
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.rbac import has_permission
from app.org_structure.models import OrgUnit

# Display labels for the fields exposed by GET /approval-chains/fields.
# ``facts.py`` (T003) owns the fact-name -> attribute / fact-type mapping;
# this domain-private label map is presentation only.
_CONTRACT_FIELD_LABELS: dict[str, str] = {
    "contract_value": "Contract value",
    "currency": "Currency",
    "contract_type": "Contract type",
    "jurisdiction": "Jurisdiction",
    "risk_band": "Risk band",
    "risk_score": "Risk score",
    "confidentiality": "Confidentiality",
    "counterparty_name": "Counterparty",
    "lifecycle_stage": "Lifecycle stage",
    "title": "Title",
    "matter_id": "Matter",
    "owner_user_id": "Owner",
    "effective_date": "Effective date (ISO)",
    "expiration_date": "Expiration date (ISO)",
    "renewal_due": "Renewal due",
    "archived": "Archived",
}
_INTAKE_FIELD_LABELS: dict[str, str] = {
    "request_value": "Requested value",
    "currency": "Currency",
    "jurisdiction": "Jurisdiction",
    "request_type": "Request type",
    "request_type_key": "Request type key",
    "department": "Department",
    "priority": "Priority",
    "risk_band": "Risk band",
    "source": "Source",
    "status": "Status",
    "stage": "Stage",
    "work_status": "Work status",
    "sla_hours": "SLA hours",
    "sla_status": "SLA status",
    "request_ref": "Reference",
    "request_subject": "Subject",
    "requester_user_id": "Requester",
    "assigned_to_user_id": "Assignee",
    "matter_id": "Matter",
    "has_contract": "Has a contract",
}
_FIELD_LABELS: dict[str, dict[str, str]] = {
    "contract": _CONTRACT_FIELD_LABELS,
    "intake_request": _INTAKE_FIELD_LABELS,
}
_OPERATOR_ORDER = ["gt", "lt", "eq", "in", "contains"]


# --------------------------------------------------------------------------
# Field catalog
# --------------------------------------------------------------------------


def get_field_catalog(module: str) -> dict[str, Any]:
    if module not in facts.MODULE_FACTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown module '{module}'")
    types = facts.FIELD_TYPES[module]
    labels = _FIELD_LABELS[module]
    fields = [
        {"name": name, "type": types[name], "label": labels.get(name, name)}
        for name in facts.MODULE_FACTS[module]
    ]
    return {"module": module, "operators": list(_OPERATOR_ORDER), "fields": fields}


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------


def _serialize_rule(rule: ApprovalChainStepRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "org_id": rule.org_id,
        "step_id": rule.step_id,
        "is_base_requirement": rule.is_base_requirement,
        "condition_expression": rule.condition_expression,
        "condition_text": (
            None if rule.is_base_requirement else conditions.render_condition_text(rule.condition_expression)
        ),
        "required_role_id": rule.required_role_id,
        "required_role_name": rule.required_role.name if rule.required_role else "",
        "sequence_order": rule.sequence_order,
        "description": rule.description,
        "is_active": rule.is_active,
        "created_at": rule.created_at,
        "created_by_user_id": rule.created_by_user_id,
        "updated_at": rule.updated_at,
        "updated_by_user_id": rule.updated_by_user_id,
    }


def _serialize_step(db: Session, step: ApprovalChainStep) -> dict[str, Any]:
    rules = db.scalars(
        select(ApprovalChainStepRule)
        .where(ApprovalChainStepRule.step_id == step.id, ApprovalChainStepRule.deleted_at.is_(None))
        .order_by(ApprovalChainStepRule.sequence_order, ApprovalChainStepRule.created_at)
    ).all()
    return {
        "id": step.id,
        "org_id": step.org_id,
        "definition_id": step.definition_id,
        "step_key": step.step_key,
        "name": step.name,
        "sequence_order": step.sequence_order,
        "step_type": step.step_type,
        "approval_mode": step.approval_mode,
        "rules": [_serialize_rule(r) for r in rules],
        "created_at": step.created_at,
        "created_by_user_id": step.created_by_user_id,
        "updated_at": step.updated_at,
        "updated_by_user_id": step.updated_by_user_id,
    }


def _serialize_definition(db: Session, definition: ApprovalChainDefinition) -> dict[str, Any]:
    steps = db.scalars(
        select(ApprovalChainStep)
        .where(ApprovalChainStep.definition_id == definition.id, ApprovalChainStep.deleted_at.is_(None))
        .order_by(ApprovalChainStep.sequence_order)
    ).all()
    return {
        "id": definition.id,
        "org_id": definition.org_id,
        "name": definition.name,
        "module": definition.module,
        "version": definition.version,
        "is_active": definition.is_active,
        "is_default_seeded": definition.is_default_seeded,
        "steps": [_serialize_step(db, s) for s in steps],
        "created_at": definition.created_at,
        "created_by_user_id": definition.created_by_user_id,
        "updated_at": definition.updated_at,
        "updated_by_user_id": definition.updated_by_user_id,
    }


# --------------------------------------------------------------------------
# Chain definitions
# --------------------------------------------------------------------------


def list_definitions(
    db: Session, *, actor: User, module: str | None = None, include_inactive: bool = False
) -> list[dict[str, Any]]:
    stmt = select(ApprovalChainDefinition).where(
        ApprovalChainDefinition.org_id == actor.org_id, ApprovalChainDefinition.deleted_at.is_(None)
    )
    if module is not None:
        stmt = stmt.where(ApprovalChainDefinition.module == module)
    if not include_inactive:
        stmt = stmt.where(ApprovalChainDefinition.is_active.is_(True))
    definitions = db.scalars(stmt.order_by(ApprovalChainDefinition.created_at.desc())).all()
    return [_serialize_definition(db, d) for d in definitions]


def _assert_no_duplicate_definition_name(
    db: Session, *, org_id: str, name: str, version: int, exclude_id: str | None = None
) -> None:
    stmt = select(ApprovalChainDefinition.id).where(
        ApprovalChainDefinition.org_id == org_id,
        ApprovalChainDefinition.name == name,
        ApprovalChainDefinition.version == version,
        ApprovalChainDefinition.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(ApprovalChainDefinition.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A definition with this name and version already exists")


def _assert_no_other_active_definition(
    db: Session, *, org_id: str, module: str, exclude_id: str | None = None
) -> None:
    stmt = select(ApprovalChainDefinition.id).where(
        ApprovalChainDefinition.org_id == org_id,
        ApprovalChainDefinition.module == module,
        ApprovalChainDefinition.is_active.is_(True),
        ApprovalChainDefinition.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(ApprovalChainDefinition.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"An active definition already exists for module '{module}'"
        )


def create_definition(db: Session, *, actor: User, payload: ChainDefinitionCreate) -> dict[str, Any]:
    _assert_no_duplicate_definition_name(
        db, org_id=actor.org_id, name=payload.name, version=payload.version
    )
    if payload.is_active:
        _assert_no_other_active_definition(db, org_id=actor.org_id, module=payload.module)

    definition = ApprovalChainDefinition(
        org_id=actor.org_id,
        name=payload.name,
        module=payload.module,
        version=payload.version,
        is_active=payload.is_active,
        is_default_seeded=False,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(definition)
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.definition_created",
        resource_type="approval_chain_definition",
        resource_id=definition.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={
            "name": definition.name,
            "module": definition.module,
            "version": definition.version,
            "is_active": definition.is_active,
        },
    )
    db.commit()
    db.refresh(definition)
    return _serialize_definition(db, definition)


def update_definition(
    db: Session, *, actor: User, definition_id: str, payload: ChainDefinitionUpdate
) -> dict[str, Any]:
    definition = access.get_definition_or_404(db, actor.org_id, definition_id)
    before = {"name": definition.name, "is_active": definition.is_active}

    new_name = definition.name if payload.name is None else payload.name
    if payload.name is not None and payload.name != definition.name:
        _assert_no_duplicate_definition_name(
            db,
            org_id=actor.org_id,
            name=new_name,
            version=definition.version,
            exclude_id=definition.id,
        )
        definition.name = new_name

    if payload.is_active is not None and payload.is_active and not definition.is_active:
        _assert_no_other_active_definition(
            db, org_id=actor.org_id, module=definition.module, exclude_id=definition.id
        )
    if payload.is_active is not None:
        definition.is_active = payload.is_active

    definition.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.definition_updated",
        resource_type="approval_chain_definition",
        resource_id=definition.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
        after={"name": definition.name, "is_active": definition.is_active},
    )
    db.commit()
    db.refresh(definition)
    return _serialize_definition(db, definition)


def delete_definition(db: Session, *, actor: User, definition_id: str) -> None:
    definition = access.get_definition_or_404(db, actor.org_id, definition_id, include_deleted=True)
    if definition.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Chain definition already deleted")

    before = {"name": definition.name, "is_active": definition.is_active}
    definition.deleted_at = utcnow()
    definition.deleted_by_user_id = actor.id
    definition.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.definition_deactivated",
        resource_type="approval_chain_definition",
        resource_id=definition.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
        metadata={"soft_delete": True},
    )
    db.commit()


# --------------------------------------------------------------------------
# Chain steps
# --------------------------------------------------------------------------


def _assert_no_duplicate_step(
    db: Session,
    *,
    definition_id: str,
    step_key: str | None = None,
    sequence_order: int | None = None,
    exclude_id: str | None = None,
) -> None:
    if step_key is not None:
        stmt = select(ApprovalChainStep.id).where(
            ApprovalChainStep.definition_id == definition_id,
            ApprovalChainStep.step_key == step_key,
            ApprovalChainStep.deleted_at.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(ApprovalChainStep.id != exclude_id)
        if db.scalar(stmt) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "A step with this key already exists")
    if sequence_order is not None:
        stmt = select(ApprovalChainStep.id).where(
            ApprovalChainStep.definition_id == definition_id,
            ApprovalChainStep.sequence_order == sequence_order,
            ApprovalChainStep.deleted_at.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(ApprovalChainStep.id != exclude_id)
        if db.scalar(stmt) is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A step with this sequence order already exists"
            )


def create_step(
    db: Session, *, actor: User, definition_id: str, payload: ChainStepCreate
) -> dict[str, Any]:
    definition = access.get_definition_or_404(db, actor.org_id, definition_id)
    _assert_no_duplicate_step(
        db, definition_id=definition.id, step_key=payload.step_key, sequence_order=payload.sequence_order
    )

    step = ApprovalChainStep(
        org_id=actor.org_id,
        definition_id=definition.id,
        step_key=payload.step_key,
        name=payload.name,
        sequence_order=payload.sequence_order,
        step_type=payload.step_type,
        approval_mode=payload.approval_mode,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(step)
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.step_created",
        resource_type="approval_chain_step",
        resource_id=step.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={
            "step_key": step.step_key,
            "sequence_order": step.sequence_order,
            "approval_mode": step.approval_mode,
        },
    )
    db.commit()
    db.refresh(step)
    return _serialize_step(db, step)


def update_step(db: Session, *, actor: User, step_id: str, payload: ChainStepUpdate) -> dict[str, Any]:
    step = access.get_step_or_404(db, actor.org_id, step_id)
    before = {
        "name": step.name,
        "sequence_order": step.sequence_order,
        "approval_mode": step.approval_mode,
    }

    if payload.sequence_order is not None and payload.sequence_order != step.sequence_order:
        _assert_no_duplicate_step(
            db,
            definition_id=step.definition_id,
            sequence_order=payload.sequence_order,
            exclude_id=step.id,
        )
        step.sequence_order = payload.sequence_order
    if payload.name is not None:
        step.name = payload.name
    if payload.approval_mode is not None:
        step.approval_mode = payload.approval_mode

    step.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.step_updated",
        resource_type="approval_chain_step",
        resource_id=step.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
        after={
            "name": step.name,
            "sequence_order": step.sequence_order,
            "approval_mode": step.approval_mode,
        },
    )
    db.commit()
    db.refresh(step)
    return _serialize_step(db, step)


def delete_step(db: Session, *, actor: User, step_id: str) -> None:
    step = access.get_step_or_404(db, actor.org_id, step_id, include_deleted=True)
    if step.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Chain step already deleted")

    before = {"step_key": step.step_key, "sequence_order": step.sequence_order}
    step.deleted_at = utcnow()
    step.deleted_by_user_id = actor.id
    step.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.step_deleted",
        resource_type="approval_chain_step",
        resource_id=step.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
    )
    db.commit()


# --------------------------------------------------------------------------
# Chain step rules (base requirements + condition rules)
# --------------------------------------------------------------------------


def _rule_audit_fields(rule: ApprovalChainStepRule) -> dict[str, Any]:
    return {
        "is_base_requirement": rule.is_base_requirement,
        "condition_expression": rule.condition_expression,
        "required_role_id": rule.required_role_id,
        "required_role_name": rule.required_role.name if rule.required_role else "",
        "sequence_order": rule.sequence_order,
    }


def create_rule(
    db: Session, *, actor: User, step_id: str, payload: ChainStepRuleCreate
) -> dict[str, Any]:
    step = access.get_step_or_404(db, actor.org_id, step_id)
    definition = access.get_definition_or_404(db, actor.org_id, step.definition_id)
    role = access.get_org_role_or_404(db, actor.org_id, payload.required_role_id)

    if payload.is_base_requirement:
        if payload.condition_expression is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A base requirement cannot carry a condition expression",
            )
        stmt = select(ApprovalChainStepRule.id).where(
            ApprovalChainStepRule.step_id == step.id,
            ApprovalChainStepRule.required_role_id == role.id,
            ApprovalChainStepRule.is_base_requirement.is_(True),
            ApprovalChainStepRule.deleted_at.is_(None),
        )
        if db.scalar(stmt) is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A base requirement for this role already exists on this step"
            )
        condition_expression = None
    else:
        if payload.condition_expression is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A condition rule must carry a condition expression",
            )
        condition_expression = payload.condition_expression.model_dump()
        conditions.validate_expression(
            condition_expression, allowed_fields=facts.allowed_fields(definition.module)
        )

    rule = ApprovalChainStepRule(
        org_id=actor.org_id,
        step_id=step.id,
        is_base_requirement=payload.is_base_requirement,
        condition_expression=condition_expression,
        required_role_id=role.id,
        sequence_order=payload.sequence_order,
        description=payload.description,
        is_active=payload.is_active,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(rule)
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.rule_created",
        resource_type="approval_chain_step_rule",
        resource_id=rule.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after=_rule_audit_fields(rule),
    )
    db.commit()
    db.refresh(rule)
    return _serialize_rule(rule)


def update_rule(db: Session, *, actor: User, rule_id: str, payload: ChainStepRuleUpdate) -> dict[str, Any]:
    rule = access.get_rule_or_404(db, actor.org_id, rule_id)
    step = access.get_step_or_404(db, actor.org_id, rule.step_id)
    definition = access.get_definition_or_404(db, actor.org_id, step.definition_id)
    before = _rule_audit_fields(rule)

    if payload.condition_expression is not None:
        if rule.is_base_requirement:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A base requirement cannot carry a condition expression",
            )
        expr = payload.condition_expression.model_dump()
        conditions.validate_expression(expr, allowed_fields=facts.allowed_fields(definition.module))
        rule.condition_expression = expr
    if payload.sequence_order is not None:
        rule.sequence_order = payload.sequence_order
    if payload.description is not None or "description" in payload.model_fields_set:
        rule.description = payload.description
    if payload.is_active is not None:
        rule.is_active = payload.is_active

    rule.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.rule_updated",
        resource_type="approval_chain_step_rule",
        resource_id=rule.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
        after=_rule_audit_fields(rule),
    )
    db.commit()
    db.refresh(rule)
    return _serialize_rule(rule)


def delete_rule(db: Session, *, actor: User, rule_id: str) -> None:
    rule = access.get_rule_or_404(db, actor.org_id, rule_id, include_deleted=True)
    if rule.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Chain step rule already deleted")

    before = _rule_audit_fields(rule)
    rule.deleted_at = utcnow()
    rule.deleted_by_user_id = actor.id
    rule.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="approval_chain.rule_deleted",
        resource_type="approval_chain_step_rule",
        resource_id=rule.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
    )
    db.commit()


# --------------------------------------------------------------------------
# Materialization (FR-5) — the core evaluation
# --------------------------------------------------------------------------


def _group_rule_sources(
    db: Session, *, module: str, module_record_id: str, org_id: str, step_id: str
) -> dict[str, dict[str, Any]]:
    """Evaluate every active rule on ``step_id`` against the item's CURRENT
    facts and group the surviving sources by ``required_role_id`` (FR-3/AC-5).
    Shared by ``_materialize_step`` (materialization time) and
    ``recalculate`` (explicit re-evaluation) — the grouping algorithm itself,
    not the ``_materialize_step`` entry point, which must stay called from
    exactly two places (FR-5).
    """
    facts_dict = facts.build_facts(db, module=module, record_id=module_record_id, org_id=org_id)
    allowed = facts.allowed_fields(module)

    rules = db.scalars(
        select(ApprovalChainStepRule)
        .where(
            ApprovalChainStepRule.step_id == step_id,
            ApprovalChainStepRule.is_active.is_(True),
            ApprovalChainStepRule.deleted_at.is_(None),
        )
        .order_by(ApprovalChainStepRule.sequence_order, ApprovalChainStepRule.created_at)
    ).all()

    sources: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if rule.is_base_requirement:
            entry = sources.setdefault(
                rule.required_role_id,
                {"is_base": False, "sequence_order": rule.sequence_order, "rule_ids": [], "explanations": []},
            )
            entry["is_base"] = True
            entry["sequence_order"] = min(entry["sequence_order"], rule.sequence_order)
            continue

        result = conditions.evaluate_condition(rule.condition_expression, facts_dict, allowed_fields=allowed)
        if not result.satisfied:
            continue
        entry = sources.setdefault(
            rule.required_role_id,
            {"is_base": False, "sequence_order": rule.sequence_order, "rule_ids": [], "explanations": []},
        )
        entry["sequence_order"] = min(entry["sequence_order"], rule.sequence_order)
        entry["rule_ids"].append(rule.id)
        entry["explanations"].append(
            {
                "rule_id": rule.id,
                "field": result.field,
                "operator": result.operator,
                "value": result.value,
                "actual": result.actual,
                "text": conditions.render_explanation(result),
            }
        )
    return sources


def _materialize_step(
    db: Session, *, actor: User, instance: ApprovalChainInstance, step: ApprovalChainStep
) -> list[ApprovalChainRequirement]:
    """The FR-5 evaluation — the ONLY place conditions are ever evaluated.
    Called from EXACTLY TWO places: ``create_instance`` and ``_advance_step``.
    Never from a read path.
    """
    sources = _group_rule_sources(
        db,
        module=instance.module,
        module_record_id=instance.module_record_id,
        org_id=instance.org_id,
        step_id=step.id,
    )

    created: list[ApprovalChainRequirement] = []
    for role_id, entry in sources.items():
        holders = org_access.users_holding_role(
            db, org_id=instance.org_id, role_id=role_id, org_unit_id=instance.org_unit_id
        )
        requirement = ApprovalChainRequirement(
            org_id=instance.org_id,
            instance_id=instance.id,
            step_id=step.id,
            required_role_id=role_id,
            sequence_order=entry["sequence_order"],
            is_base_requirement=entry["is_base"],
            triggered_by_rule_ids=list(entry["rule_ids"]),
            condition_explanations=list(entry["explanations"]),
            status="pending",
            counts_toward_completion=True,
            is_unfulfillable=not holders,
            eligible_user_count=len(holders),
            materialized_at=utcnow(),
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(requirement)
        db.flush()
        created.append(requirement)
        if requirement.is_unfulfillable:
            _append_history(
                db,
                instance=instance,
                step_id=step.id,
                requirement_id=requirement.id,
                action="blocked_no_eligible_approver",
                actor=actor,
                after_json={"required_role_id": role_id, "eligible_user_count": 0},
            )

    if created:
        # Email every role holder now eligible to act on this step. This
        # function is called from exactly two places (chain creation and
        # _advance_step) and always creates fresh requirement rows, so it's
        # inherently a one-shot-per-step-transition event — no idempotency
        # guard needed, unlike workflows/service.py::_assign_step. Dispatch
        # note: same race as that function's own fix — the caller hasn't
        # committed yet, so a short countdown lets it land before the worker
        # reads these rows.
        from app.jobs.tasks import notify_approval_chain_step_holders

        notify_approval_chain_step_holders.apply_async(args=[instance.id, step.id], countdown=4)

    return created


# --------------------------------------------------------------------------
# History helper
# --------------------------------------------------------------------------


def _append_history(
    db: Session,
    *,
    instance: ApprovalChainInstance,
    action: str,
    step_id: str | None = None,
    requirement_id: str | None = None,
    actor: User | None = None,
    acted_as_role_id: str | None = None,
    delegated_from_user_id: str | None = None,
    comments: str | None = None,
    before_json: Any = None,
    after_json: Any = None,
) -> ApprovalChainHistory:
    row = ApprovalChainHistory(
        org_id=instance.org_id,
        instance_id=instance.id,
        step_id=step_id,
        requirement_id=requirement_id,
        action=action,
        acted_by_user_id=actor.id if actor else None,
        acted_as_role_id=acted_as_role_id,
        delegated_from_user_id=delegated_from_user_id,
        comments=comments,
        before_json=before_json,
        after_json=after_json,
        acted_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _requirement_summary(req: ApprovalChainRequirement) -> dict[str, Any]:
    return {
        "requirement_id": req.id,
        "required_role_id": req.required_role_id,
        "required_role_name": req.required_role.name if req.required_role else "",
        "sequence_order": req.sequence_order,
        "is_base_requirement": req.is_base_requirement,
        "triggered_by_rule_ids": list(req.triggered_by_rule_ids or []),
        "status": req.status,
    }


# --------------------------------------------------------------------------
# Chain instances
# --------------------------------------------------------------------------


def create_instance(
    db: Session,
    *,
    actor: User,
    payload: ChainInstanceCreate,
    subject: Any = None,
    raise_on_existing: bool = True,
    request_id: str | None = None,
    enforce_actor_access: bool = True,
) -> ApprovalChainInstance:
    definition = access.get_definition_or_404(db, actor.org_id, payload.definition_id)
    if not definition.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chain definition is not active")
    if payload.module != definition.module:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Definition is for module '{definition.module}', not '{payload.module}'",
        )

    steps = db.scalars(
        select(ApprovalChainStep)
        .where(ApprovalChainStep.definition_id == definition.id, ApprovalChainStep.deleted_at.is_(None))
        .order_by(ApprovalChainStep.sequence_order)
    ).all()
    if not steps:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chain definition has no steps")

    access.get_module_record_or_404(
        db, actor=actor, module=payload.module, record_id=payload.module_record_id,
        enforce_actor_access=enforce_actor_access,
    )

    if payload.org_unit_id is not None:
        org_unit = access.get_org_unit_or_404(db, actor.org_id, payload.org_unit_id)
    else:
        org_unit = access.get_org_root(db, actor.org_id)

    existing = db.scalar(
        select(ApprovalChainInstance).where(
            ApprovalChainInstance.definition_id == definition.id,
            ApprovalChainInstance.module == payload.module,
            ApprovalChainInstance.module_record_id == payload.module_record_id,
            ApprovalChainInstance.status == "pending",
            ApprovalChainInstance.deleted_at.is_(None),
        )
    )
    if existing is not None:
        if raise_on_existing:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A live instance already exists for this record and definition"
            )
        return existing

    first_step = steps[0]
    instance = ApprovalChainInstance(
        org_id=actor.org_id,
        definition_id=definition.id,
        module=payload.module,
        module_record_id=payload.module_record_id,
        org_unit_id=org_unit.id,
        current_step_id=first_step.id,
        status="pending",
        started_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(instance)
    db.flush()

    requirements = _materialize_step(db, actor=actor, instance=instance, step=first_step)

    subj = subject
    if subj is None:
        subj = subjects.resolve_subject(
            db, module=instance.module, record_id=instance.module_record_id, org_id=instance.org_id
        )
    subj.on_submit(db, actor_user_id=actor.id, request_id=request_id)

    write_audit_log(
        db,
        action="approval_chain.instance_created",
        resource_type="approval_chain_instance",
        resource_id=instance.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
        after={
            "definition_id": definition.id,
            "module": instance.module,
            "module_record_id": instance.module_record_id,
            "org_unit_id": instance.org_unit_id,
            "current_step_id": instance.current_step_id,
        },
        metadata={
            "materialized_requirements": [_requirement_summary(r) for r in requirements],
            "unfulfillable_role_ids": [r.required_role_id for r in requirements if r.is_unfulfillable],
        },
    )
    _append_history(db, instance=instance, action="instance_created", actor=actor)
    _append_history(
        db,
        instance=instance,
        step_id=first_step.id,
        action="materialized",
        actor=actor,
        after_json={"requirements": [_requirement_summary(r) for r in requirements]},
    )

    db.commit()
    db.refresh(instance)
    return instance


def _advance_step(db: Session, *, actor: User, instance: ApprovalChainInstance) -> None:
    current_step = db.get(ApprovalChainStep, instance.current_step_id)
    next_step = db.scalar(
        select(ApprovalChainStep)
        .where(
            ApprovalChainStep.definition_id == current_step.definition_id,
            ApprovalChainStep.deleted_at.is_(None),
            ApprovalChainStep.sequence_order > current_step.sequence_order,
        )
        .order_by(ApprovalChainStep.sequence_order)
    )
    if next_step is not None:
        instance.current_step_id = next_step.id
        instance.updated_by_user_id = actor.id
        db.flush()
        requirements = _materialize_step(db, actor=actor, instance=instance, step=next_step)
        _append_history(
            db,
            instance=instance,
            step_id=next_step.id,
            action="materialized",
            actor=actor,
            after_json={"requirements": [_requirement_summary(r) for r in requirements]},
        )
        write_audit_log(
            db,
            action="approval_chain.step_materialized",
            resource_type="approval_chain_instance",
            resource_id=instance.id,
            org_id=instance.org_id,
            actor_user_id=actor.id,
            after={"current_step_id": next_step.id},
        )
        return

    instance.status = "approved"
    instance.current_step_id = None
    instance.updated_by_user_id = actor.id
    db.flush()

    subj = subjects.resolve_subject(
        db, module=instance.module, record_id=instance.module_record_id, org_id=instance.org_id
    )
    subj.on_complete(db, actor_user_id=actor.id, request_id=None)

    _append_history(
        db, instance=instance, action="instance_completed", actor=actor, after_json={"status": "approved"}
    )
    write_audit_log(
        db,
        action="approval_chain.completed",
        resource_type="approval_chain_instance",
        resource_id=instance.id,
        org_id=instance.org_id,
        actor_user_id=actor.id,
        after={"status": "approved"},
    )


def record_decision(
    db: Session, *, actor: User, instance_id: str, requirement_id: str, payload: ChainDecisionPayload
) -> ApprovalChainInstance:
    instance = db.execute(
        select(ApprovalChainInstance)
        .where(
            ApprovalChainInstance.id == instance_id,
            ApprovalChainInstance.org_id == actor.org_id,
            ApprovalChainInstance.deleted_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chain instance not found")
    if instance.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This chain instance is not pending")

    requirement = db.execute(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.id == requirement_id,
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.org_id == actor.org_id,
            ApprovalChainRequirement.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if requirement is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chain requirement not found")
    if requirement.step_id != instance.current_step_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This requirement is not on the current step")
    if requirement.status != "pending" or requirement.superseded_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This requirement has already been decided")
    if payload.decision == "reject" and not payload.comment:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A comment is required to reject")

    step = db.get(ApprovalChainStep, requirement.step_id)
    subject = subjects.resolve_subject(
        db, module=instance.module, record_id=instance.module_record_id, org_id=instance.org_id
    )
    subject.guard_can_decide(db)

    # FR-15/AC-fresh-eligibility: re-checked FRESH at decision time, never
    # trusting the materialization-time snapshot's holder count.
    holders = org_access.users_holding_role(
        db, org_id=instance.org_id, role_id=requirement.required_role_id, org_unit_id=instance.org_unit_id
    )
    holder = next((h for h in holders if h.user_id == actor.id), None)
    if holder is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not an eligible holder of the required role for this approval"
        )

    # FR-13: the sequential gate is a hard 409, not a flag.
    if step.approval_mode == "sequential":
        earlier_pending = db.scalar(
            select(ApprovalChainRequirement.id).where(
                ApprovalChainRequirement.instance_id == instance.id,
                ApprovalChainRequirement.step_id == step.id,
                ApprovalChainRequirement.deleted_at.is_(None),
                ApprovalChainRequirement.superseded_at.is_(None),
                ApprovalChainRequirement.counts_toward_completion.is_(True),
                ApprovalChainRequirement.status == "pending",
                ApprovalChainRequirement.sequence_order < requirement.sequence_order,
                ApprovalChainRequirement.id != requirement.id,
            )
        )
        if earlier_pending is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "An earlier required approval on this step is still pending"
            )

    metadata: dict[str, Any] = {"acting_user_id": actor.id}
    if holder.via_delegation_id:
        metadata["on_behalf_of_user_id"] = holder.on_behalf_of_user_id
        metadata["delegation_id"] = holder.via_delegation_id

    if payload.decision == "approve":
        from app.authority.service import enforce_authority

        enforce_authority(
            db,
            user=actor,
            action="contract:approve",
            contract=subject,
            resource_type="approval_chain_requirement",
            resource_id=requirement.id,
        )
        requirement.status = "approved"
        requirement.acted_by_user_id = actor.id
        requirement.acted_as_role_id = holder.role_id
        requirement.delegated_from_user_id = holder.on_behalf_of_user_id
        requirement.acted_at = utcnow()
        requirement.comment = payload.comment
        requirement.updated_by_user_id = actor.id
        db.flush()

        write_audit_log(
            db,
            action="approval_chain.decided",
            resource_type="approval_chain_requirement",
            resource_id=requirement.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            after={
                "decision": "approve",
                "requirement_id": requirement.id,
                "required_role_id": requirement.required_role_id,
                "step_id": step.id,
                "sequence_order": requirement.sequence_order,
            },
            metadata=metadata,
        )
        _append_history(
            db,
            instance=instance,
            step_id=step.id,
            requirement_id=requirement.id,
            action="approved",
            actor=actor,
            acted_as_role_id=holder.role_id,
            delegated_from_user_id=holder.on_behalf_of_user_id,
            comments=payload.comment,
            after_json={"status": "approved"},
        )

        live_counting = db.scalars(
            select(ApprovalChainRequirement).where(
                ApprovalChainRequirement.instance_id == instance.id,
                ApprovalChainRequirement.step_id == step.id,
                ApprovalChainRequirement.deleted_at.is_(None),
                ApprovalChainRequirement.superseded_at.is_(None),
                ApprovalChainRequirement.counts_toward_completion.is_(True),
            )
        ).all()
        if live_counting and all(r.status == "approved" for r in live_counting):
            _advance_step(db, actor=actor, instance=instance)
    else:
        requirement.status = "rejected"
        requirement.acted_by_user_id = actor.id
        requirement.acted_as_role_id = holder.role_id
        requirement.delegated_from_user_id = holder.on_behalf_of_user_id
        requirement.acted_at = utcnow()
        requirement.comment = payload.comment
        requirement.updated_by_user_id = actor.id

        others = db.scalars(
            select(ApprovalChainRequirement).where(
                ApprovalChainRequirement.instance_id == instance.id,
                ApprovalChainRequirement.deleted_at.is_(None),
                ApprovalChainRequirement.superseded_at.is_(None),
                ApprovalChainRequirement.status == "pending",
                ApprovalChainRequirement.id != requirement.id,
            )
        ).all()
        for other in others:
            other.status = "cancelled"
            other.updated_by_user_id = actor.id

        instance.status = "rejected"
        instance.updated_by_user_id = actor.id
        db.flush()

        write_audit_log(
            db,
            action="approval_chain.decided",
            resource_type="approval_chain_requirement",
            resource_id=requirement.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            after={
                "decision": "reject",
                "requirement_id": requirement.id,
                "required_role_id": requirement.required_role_id,
                "step_id": step.id,
                "sequence_order": requirement.sequence_order,
            },
            metadata=metadata,
        )
        _append_history(
            db,
            instance=instance,
            step_id=step.id,
            requirement_id=requirement.id,
            action="rejected",
            actor=actor,
            acted_as_role_id=holder.role_id,
            delegated_from_user_id=holder.on_behalf_of_user_id,
            comments=payload.comment,
            after_json={"status": "rejected"},
        )
        _append_history(
            db, instance=instance, action="instance_rejected", actor=actor, after_json={"status": "rejected"}
        )
        write_audit_log(
            db,
            action="approval_chain.rejected",
            resource_type="approval_chain_instance",
            resource_id=instance.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            after={"status": "rejected"},
        )

        subject.on_reject(db, actor_user_id=actor.id, comment=payload.comment, request_id=None)

    db.commit()
    db.refresh(instance)
    return instance


def recalculate(
    db: Session, *, actor: User, instance_id: str, payload: ChainRecalculatePayload
) -> ApprovalChainInstance:
    instance = db.execute(
        select(ApprovalChainInstance)
        .where(
            ApprovalChainInstance.id == instance_id,
            ApprovalChainInstance.org_id == actor.org_id,
            ApprovalChainInstance.deleted_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chain instance not found")
    if instance.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This chain instance is not pending")

    step = db.get(ApprovalChainStep, instance.current_step_id)

    existing_requirements = db.scalars(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.step_id == step.id,
            ApprovalChainRequirement.deleted_at.is_(None),
            ApprovalChainRequirement.superseded_at.is_(None),
        )
    ).all()
    existing_by_role = {r.required_role_id: r for r in existing_requirements}
    before_list = [_requirement_summary(r) for r in existing_requirements]

    sources = _group_rule_sources(
        db,
        module=instance.module,
        module_record_id=instance.module_record_id,
        org_id=instance.org_id,
        step_id=step.id,
    )

    new_role_ids = set(sources.keys())
    existing_role_ids = set(existing_by_role.keys())
    added_role_ids = new_role_ids - existing_role_ids
    removed_role_ids = existing_role_ids - new_role_ids
    unchanged_role_ids = new_role_ids & existing_role_ids

    created: list[ApprovalChainRequirement] = []
    for role_id in added_role_ids:
        entry = sources[role_id]
        holders = org_access.users_holding_role(
            db, org_id=instance.org_id, role_id=role_id, org_unit_id=instance.org_unit_id
        )
        requirement = ApprovalChainRequirement(
            org_id=instance.org_id,
            instance_id=instance.id,
            step_id=step.id,
            required_role_id=role_id,
            sequence_order=entry["sequence_order"],
            is_base_requirement=entry["is_base"],
            triggered_by_rule_ids=list(entry["rule_ids"]),
            condition_explanations=list(entry["explanations"]),
            status="pending",
            counts_toward_completion=True,
            is_unfulfillable=not holders,
            eligible_user_count=len(holders),
            materialized_at=utcnow(),
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(requirement)
        db.flush()
        created.append(requirement)
        if requirement.is_unfulfillable:
            _append_history(
                db,
                instance=instance,
                step_id=step.id,
                requirement_id=requirement.id,
                action="blocked_no_eligible_approver",
                actor=actor,
                after_json={"required_role_id": role_id, "eligible_user_count": 0},
            )

    for role_id in unchanged_role_ids:
        requirement = existing_by_role[role_id]
        entry = sources[role_id]
        holders = org_access.users_holding_role(
            db, org_id=instance.org_id, role_id=role_id, org_unit_id=instance.org_unit_id
        )
        requirement.triggered_by_rule_ids = list(entry["rule_ids"])
        requirement.condition_explanations = list(entry["explanations"])
        requirement.sequence_order = entry["sequence_order"]
        requirement.is_base_requirement = entry["is_base"]
        requirement.eligible_user_count = len(holders)
        requirement.is_unfulfillable = not holders
        requirement.updated_by_user_id = actor.id

    for role_id in removed_role_ids:
        requirement = existing_by_role[role_id]
        if requirement.status == "pending":
            requirement.deleted_at = utcnow()
            requirement.deleted_by_user_id = actor.id
        else:
            requirement.superseded_at = utcnow()
            requirement.counts_toward_completion = False
        requirement.updated_by_user_id = actor.id

    db.flush()

    after_list = [_requirement_summary(r) for r in existing_requirements if r.deleted_at is None]
    after_list += [_requirement_summary(r) for r in created]

    instance.updated_by_user_id = actor.id
    db.flush()

    _append_history(
        db,
        instance=instance,
        step_id=step.id,
        action="recalculated",
        actor=actor,
        comments=payload.reason,
        before_json=before_list,
        after_json=after_list,
    )
    write_audit_log(
        db,
        action="approval_chain.recalculated",
        resource_type="approval_chain_instance",
        resource_id=instance.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before={"requirements": before_list},
        after={"requirements": after_list},
        metadata={"reason": payload.reason},
    )

    live_counting = db.scalars(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.step_id == step.id,
            ApprovalChainRequirement.deleted_at.is_(None),
            ApprovalChainRequirement.superseded_at.is_(None),
            ApprovalChainRequirement.counts_toward_completion.is_(True),
        )
    ).all()
    if live_counting and all(r.status == "approved" for r in live_counting):
        _advance_step(db, actor=actor, instance=instance)

    db.commit()
    db.refresh(instance)
    return instance


# --------------------------------------------------------------------------
# Read paths — NO evaluation, NO writes (FR-5, FR-8)
# --------------------------------------------------------------------------


def _org_unit_name(db: Session, org_unit_id: str | None) -> str:
    if org_unit_id is None:
        return ""
    unit = db.get(OrgUnit, org_unit_id)
    return unit.name if unit else ""


def _module_record_label(db: Session, *, module: str, module_record_id: str) -> str:
    try:
        if module == "contract":
            from app.contracts.models import Contract

            record = db.get(Contract, module_record_id)
            return record.title if record else ""
        if module == "intake_request":
            from app.intake.models import IntakeRequest

            record = db.get(IntakeRequest, module_record_id)
            return record.ref if record else ""
    except Exception:  # defensive — a label failure must never break a list view
        return ""
    return ""


def _summarize_instance(db: Session, instance: ApprovalChainInstance) -> dict[str, Any]:
    definition = db.get(ApprovalChainDefinition, instance.definition_id)
    current_step = db.get(ApprovalChainStep, instance.current_step_id) if instance.current_step_id else None

    pending_requirements: list[ApprovalChainRequirement] = []
    if current_step is not None:
        pending_requirements = db.scalars(
            select(ApprovalChainRequirement).where(
                ApprovalChainRequirement.instance_id == instance.id,
                ApprovalChainRequirement.step_id == current_step.id,
                ApprovalChainRequirement.deleted_at.is_(None),
                ApprovalChainRequirement.superseded_at.is_(None),
                ApprovalChainRequirement.counts_toward_completion.is_(True),
                ApprovalChainRequirement.status == "pending",
            )
        ).all()
    is_blocked = any(r.is_unfulfillable for r in pending_requirements)

    return {
        "id": instance.id,
        "org_id": instance.org_id,
        "definition_id": instance.definition_id,
        "definition_name": definition.name if definition else "",
        "module": instance.module,
        "module_record_id": instance.module_record_id,
        "module_record_label": _module_record_label(
            db, module=instance.module, module_record_id=instance.module_record_id
        ),
        "org_unit_id": instance.org_unit_id,
        "org_unit_name": _org_unit_name(db, instance.org_unit_id),
        "current_step_id": instance.current_step_id,
        "current_step_key": current_step.step_key if current_step else None,
        "status": instance.status,
        "is_blocked": is_blocked,
        "pending_requirement_count": len(pending_requirements),
        "started_by_user_id": instance.started_by_user_id,
        "created_at": instance.created_at,
        "updated_at": instance.updated_at,
    }


def list_instances(
    db: Session,
    *,
    actor: User,
    module: str | None = None,
    module_record_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    stmt = select(ApprovalChainInstance).where(
        ApprovalChainInstance.org_id == actor.org_id, ApprovalChainInstance.deleted_at.is_(None)
    )
    if module is not None:
        stmt = stmt.where(ApprovalChainInstance.module == module)
    if module_record_id is not None:
        stmt = stmt.where(ApprovalChainInstance.module_record_id == module_record_id)
    if status_filter is not None:
        stmt = stmt.where(ApprovalChainInstance.status == status_filter)
    stmt = stmt.order_by(ApprovalChainInstance.updated_at.desc()).offset(offset).limit(limit)

    rows = db.scalars(stmt).all()
    visible: list[dict[str, Any]] = []
    for instance in rows:
        try:
            access.assert_instance_visible(db, actor=actor, instance=instance)
        except HTTPException:
            continue
        visible.append(_summarize_instance(db, instance))
    return visible


def _requirement_response(
    db: Session, req: ApprovalChainRequirement, *, actor: User, step: ApprovalChainStep, instance: ApprovalChainInstance
) -> dict[str, Any]:
    blocked_by_sequence = False
    if step.approval_mode == "sequential" and req.status == "pending" and req.superseded_at is None:
        earlier_pending = db.scalar(
            select(ApprovalChainRequirement.id).where(
                ApprovalChainRequirement.instance_id == instance.id,
                ApprovalChainRequirement.step_id == step.id,
                ApprovalChainRequirement.deleted_at.is_(None),
                ApprovalChainRequirement.superseded_at.is_(None),
                ApprovalChainRequirement.counts_toward_completion.is_(True),
                ApprovalChainRequirement.status == "pending",
                ApprovalChainRequirement.sequence_order < req.sequence_order,
                ApprovalChainRequirement.id != req.id,
            )
        )
        blocked_by_sequence = earlier_pending is not None

    can_decide = False
    if (
        instance.status == "pending"
        and step.id == instance.current_step_id
        and req.status == "pending"
        and req.superseded_at is None
        and not blocked_by_sequence
    ):
        holders = org_access.users_holding_role(
            db, org_id=instance.org_id, role_id=req.required_role_id, org_unit_id=instance.org_unit_id
        )
        can_decide = any(h.user_id == actor.id for h in holders)

    acted_by_label = None
    if req.acted_by_user_id:
        acted_user = db.get(User, req.acted_by_user_id)
        acted_by_label = acted_user.full_name if acted_user else None

    explanation = None
    if req.condition_explanations:
        explanation = "; and ".join(
            f"required because {e['text']}" for e in req.condition_explanations
        )

    return {
        "id": req.id,
        "instance_id": req.instance_id,
        "step_id": req.step_id,
        "required_role_id": req.required_role_id,
        "required_role_name": req.required_role.name if req.required_role else "",
        "sequence_order": req.sequence_order,
        "is_base_requirement": req.is_base_requirement,
        "triggered_by_rule_ids": list(req.triggered_by_rule_ids or []),
        "condition_explanations": list(req.condition_explanations or []),
        "explanation": explanation,
        "status": req.status,
        "counts_toward_completion": req.counts_toward_completion,
        "superseded_at": req.superseded_at,
        "acted_by_user_id": req.acted_by_user_id,
        "acted_by_label": acted_by_label,
        "acted_as_role_id": req.acted_as_role_id,
        "delegated_from_user_id": req.delegated_from_user_id,
        "acted_at": req.acted_at,
        "comment": req.comment,
        "is_unfulfillable": req.is_unfulfillable,
        "eligible_user_count": req.eligible_user_count,
        "can_decide": can_decide,
        "blocked_by_sequence": blocked_by_sequence,
        "materialized_at": req.materialized_at,
    }


def _history_entry(db: Session, row: ApprovalChainHistory) -> dict[str, Any]:
    acted_by_label = None
    if row.acted_by_user_id:
        user = db.get(User, row.acted_by_user_id)
        acted_by_label = user.full_name if user else None
    acted_as_role_name = None
    if row.acted_as_role_id:
        from app.auth.models import Role

        role = db.get(Role, row.acted_as_role_id)
        acted_as_role_name = role.name if role else None
    delegated_from_label = None
    if row.delegated_from_user_id:
        user = db.get(User, row.delegated_from_user_id)
        delegated_from_label = user.full_name if user else None

    return {
        "id": row.id,
        "instance_id": row.instance_id,
        "step_id": row.step_id,
        "requirement_id": row.requirement_id,
        "action": row.action,
        "acted_by_user_id": row.acted_by_user_id,
        "acted_by_label": acted_by_label,
        "acted_as_role_id": row.acted_as_role_id,
        "acted_as_role_name": acted_as_role_name,
        "delegated_from_user_id": row.delegated_from_user_id,
        "delegated_from_label": delegated_from_label,
        "comments": row.comments,
        "before_json": row.before_json,
        "after_json": row.after_json,
        "acted_at": row.acted_at,
    }


def get_history(db: Session, *, actor: User, instance_id: str) -> list[dict[str, Any]]:
    instance = access.get_instance_or_404(db, actor.org_id, instance_id)
    access.assert_instance_visible(db, actor=actor, instance=instance)
    rows = db.scalars(
        select(ApprovalChainHistory)
        .where(ApprovalChainHistory.instance_id == instance.id)
        .order_by(ApprovalChainHistory.acted_at.desc())
    ).all()
    return [_history_entry(db, r) for r in rows]


def _blocking_entries(db: Session, *, instance: ApprovalChainInstance) -> list[dict[str, Any]]:
    if instance.current_step_id is None:
        return []
    step = db.get(ApprovalChainStep, instance.current_step_id)
    org_unit_name = _org_unit_name(db, instance.org_unit_id)
    pending_unfulfillable = db.scalars(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.step_id == instance.current_step_id,
            ApprovalChainRequirement.deleted_at.is_(None),
            ApprovalChainRequirement.superseded_at.is_(None),
            ApprovalChainRequirement.counts_toward_completion.is_(True),
            ApprovalChainRequirement.status == "pending",
            ApprovalChainRequirement.is_unfulfillable.is_(True),
        )
    ).all()
    return [
        {
            "requirement_id": r.id,
            "step_id": step.id,
            "step_key": step.step_key,
            "required_role_id": r.required_role_id,
            "required_role_name": r.required_role.name if r.required_role else "",
            "sequence_order": r.sequence_order,
            "org_unit_id": instance.org_unit_id,
            "org_unit_name": org_unit_name,
        }
        for r in pending_unfulfillable
    ]


def get_instance_detail(db: Session, *, actor: User, instance_id: str) -> dict[str, Any]:
    instance = access.get_instance_or_404(db, actor.org_id, instance_id)
    access.assert_instance_visible(db, actor=actor, instance=instance)

    permissions = org_access.effective_permission_values(db, user=actor)
    is_manager = has_permission(permissions, "approval_chain:manage")
    can_recalculate = has_permission(permissions, "approval_chain:recalculate") and instance.status == "pending"

    steps = db.scalars(
        select(ApprovalChainStep)
        .where(ApprovalChainStep.definition_id == instance.definition_id, ApprovalChainStep.deleted_at.is_(None))
        .order_by(ApprovalChainStep.sequence_order)
    ).all()

    step_responses = []
    for step in steps:
        requirements = db.scalars(
            select(ApprovalChainRequirement)
            .where(
                ApprovalChainRequirement.instance_id == instance.id,
                ApprovalChainRequirement.step_id == step.id,
                ApprovalChainRequirement.deleted_at.is_(None),
            )
            .order_by(ApprovalChainRequirement.sequence_order)
        ).all()
        counting = [r for r in requirements if r.counts_toward_completion and r.superseded_at is None]
        is_complete = bool(counting) and all(r.status == "approved" for r in counting)
        is_current = step.id == instance.current_step_id
        is_blocked = is_current and any(
            r.is_unfulfillable and r.status == "pending" for r in counting
        )
        step_responses.append(
            {
                "step_id": step.id,
                "step_key": step.step_key,
                "name": step.name,
                "sequence_order": step.sequence_order,
                "approval_mode": step.approval_mode,
                "is_current": is_current,
                "is_complete": is_complete,
                "is_blocked": is_blocked,
                "requirements": [
                    _requirement_response(db, r, actor=actor, step=step, instance=instance)
                    for r in requirements
                ],
            }
        )

    blocking = _blocking_entries(db, instance=instance) if is_manager else []

    return {
        "instance": _summarize_instance(db, instance),
        "steps": step_responses,
        "blocking": blocking,
        "blocking_visible": is_manager,
        "can_recalculate": can_recalculate,
        "history": get_history(db, actor=actor, instance_id=instance.id),
    }


def get_blocked(db: Session, *, actor: User, instance_id: str) -> dict[str, Any]:
    instance = access.get_instance_or_404(db, actor.org_id, instance_id)
    access.assert_instance_visible(db, actor=actor, instance=instance)
    return {"instance_id": instance.id, "blocking": _blocking_entries(db, instance=instance)}
