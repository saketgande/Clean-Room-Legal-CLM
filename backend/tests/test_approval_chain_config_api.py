"""Tests for the approval_chains config CRUD service functions (feature
004-approval-chain-reconciliation, T009 — definitions/steps/rules).

Follows the fixture/tree conventions of ``test_org_access_role_holders.py`` /
``test_screen_access_grants_api.py``: a real (migrated) Postgres session, one
transaction per test, rolled back at teardown. Permission-gate 403s are
exercised by directly invoking the ``require_permission(...)`` dependency,
matching ``test_screen_access_grants_api.py``'s pattern — routers are thin and
carry no logic of their own to test independently.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approval_chains import service
from app.approval_chains.models import ApprovalChainStepRule
from app.approval_chains.schemas import (
    ChainDefinitionCreate,
    ChainDefinitionUpdate,
    ChainStepCreate,
    ChainStepRuleCreate,
    ChainStepRuleUpdate,
    ConditionExpressionIn,
)
from app.auth.models import Permission, Role, User
from app.core.database import engine, new_uuid
from app.core.deps import require_permission
from app.core.enums import UserStatus
from app.core.models import AuditLog
from app.organizations.models import Organization


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _get_or_create_permission(db: Session, value: str) -> Permission:
    existing = db.query(Permission).filter(Permission.value == value).one_or_none()
    if existing is not None:
        return existing
    perm = Permission(value=value)
    db.add(perm)
    db.flush()
    return perm


def _make_org(db: Session, *, name: str) -> Organization:
    org = Organization(id=new_uuid(), name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _make_role(db: Session, *, org_id: str, name: str, permission_values: list[str]) -> Role:
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    role.permissions = [_get_or_create_permission(db, v) for v in permission_values]
    db.add(role)
    db.flush()
    return role


def _make_user(db: Session, *, org_id: str, label: str) -> User:
    user = User(
        org_id=org_id,
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=label,
        hashed_password="hash",
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    return user


class Scenario:
    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="ChainConfigApiTest")
        self.admin = _make_user(db, org_id=self.org.id, label="admin")
        self.plain = _make_user(db, org_id=self.org.id, label="plain")
        self.approver_role = _make_role(
            db, org_id=self.org.id, name="approver", permission_values=["approval_chain:decide"]
        )
        self.finance_role = _make_role(
            db, org_id=self.org.id, name="finance", permission_values=["approval_chain:decide"]
        )


@pytest.fixture
def scenario(db: Session) -> Scenario:
    return Scenario(db)


def _condition(field: str, operator: str, value) -> ConditionExpressionIn:
    return ConditionExpressionIn(field=field, operator=operator, value=value)


# --- definitions --------------------------------------------------------


def test_create_and_list_definitions_happy_path(db: Session, scenario: Scenario):
    result = service.create_definition(
        db,
        actor=scenario.admin,
        payload=ChainDefinitionCreate(name="Standard contract approval", module="contract"),
    )
    assert result["module"] == "contract"
    assert result["is_active"] is True
    assert result["steps"] == []

    listing = service.list_definitions(db, actor=scenario.admin)
    assert any(d["id"] == result["id"] for d in listing)

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "approval_chain.definition_created", AuditLog.resource_id == result["id"]
        )
    )
    assert audit is not None
    assert audit.actor_user_id == scenario.admin.id


def test_second_active_definition_for_same_module_is_409(db: Session, scenario: Scenario):
    service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name="First", module="contract")
    )
    with pytest.raises(HTTPException) as exc_info:
        service.create_definition(
            db, actor=scenario.admin, payload=ChainDefinitionCreate(name="Second", module="contract")
        )
    assert exc_info.value.status_code == 409


def test_activating_a_second_definition_via_update_is_409(db: Session, scenario: Scenario):
    service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name="First", module="contract")
    )
    inactive = service.create_definition(
        db,
        actor=scenario.admin,
        payload=ChainDefinitionCreate(name="Second", module="contract", is_active=False),
    )
    with pytest.raises(HTTPException) as exc_info:
        service.update_definition(
            db, actor=scenario.admin, definition_id=inactive["id"], payload=ChainDefinitionUpdate(is_active=True)
        )
    assert exc_info.value.status_code == 409


def test_delete_definition_then_delete_again_is_409(db: Session, scenario: Scenario):
    definition = service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name="Once", module="contract")
    )
    service.delete_definition(db, actor=scenario.admin, definition_id=definition["id"])
    with pytest.raises(HTTPException) as exc_info:
        service.delete_definition(db, actor=scenario.admin, definition_id=definition["id"])
    assert exc_info.value.status_code == 409


# --- steps ---------------------------------------------------------------


def test_create_step_happy_path_and_duplicate_sequence_conflict(db: Session, scenario: Scenario):
    definition = service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name="D1", module="contract")
    )
    step = service.create_step(
        db,
        actor=scenario.admin,
        definition_id=definition["id"],
        payload=ChainStepCreate(step_key="review", name="Review", sequence_order=1),
    )
    assert step["approval_mode"] == "sequential"

    with pytest.raises(HTTPException) as exc_info:
        service.create_step(
            db,
            actor=scenario.admin,
            definition_id=definition["id"],
            payload=ChainStepCreate(step_key="other", name="Other", sequence_order=1),
        )
    assert exc_info.value.status_code == 409


# --- rules -----------------------------------------------------------------


def _make_definition_and_step(db: Session, scenario: Scenario, module: str = "contract"):
    definition = service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name=f"D-{uuid.uuid4().hex[:8]}", module=module)
    )
    step = service.create_step(
        db,
        actor=scenario.admin,
        definition_id=definition["id"],
        payload=ChainStepCreate(step_key="review", name="Review", sequence_order=1),
    )
    return definition, step


def test_base_rule_with_condition_is_422(db: Session, scenario: Scenario):
    _definition, step = _make_definition_and_step(db, scenario)
    with pytest.raises(HTTPException) as exc_info:
        service.create_rule(
            db,
            actor=scenario.admin,
            step_id=step["id"],
            payload=ChainStepRuleCreate(
                is_base_requirement=True,
                condition_expression=_condition("contract_value", "gt", 1_000_000),
                required_role_id=scenario.approver_role.id,
                sequence_order=1,
            ),
        )
    assert exc_info.value.status_code == 422


def test_condition_rule_without_condition_is_422(db: Session, scenario: Scenario):
    _definition, step = _make_definition_and_step(db, scenario)
    with pytest.raises(HTTPException) as exc_info:
        service.create_rule(
            db,
            actor=scenario.admin,
            step_id=step["id"],
            payload=ChainStepRuleCreate(
                is_base_requirement=False,
                condition_expression=None,
                required_role_id=scenario.approver_role.id,
                sequence_order=1,
            ),
        )
    assert exc_info.value.status_code == 422


def test_unknown_field_condition_is_422_on_create_and_patch(db: Session, scenario: Scenario):
    """The ``operator`` key is a Pydantic ``Literal`` (FR-2/FR-4) so a
    genuinely unknown operator is already rejected at the schema layer,
    before ``ConditionExpressionIn`` construction even succeeds — there is no
    way to reach ``service.create_rule`` with one. What IS reachable through
    the API contract, and must 422 at the service layer via
    ``conditions.validate_expression``, is a well-formed expression
    referencing a field outside the module's whitelist (FR-4).
    """
    _definition, step = _make_definition_and_step(db, scenario)
    with pytest.raises(HTTPException) as exc_info:
        service.create_rule(
            db,
            actor=scenario.admin,
            step_id=step["id"],
            payload=ChainStepRuleCreate(
                is_base_requirement=False,
                condition_expression=_condition("not_a_field", "gt", 1000),
                required_role_id=scenario.finance_role.id,
                sequence_order=2,
            ),
        )
    assert exc_info.value.status_code == 422

    # a well-formed rule, then patched to reference an unknown field -> 422
    rule = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("contract_value", "gt", 1000),
            required_role_id=scenario.finance_role.id,
            sequence_order=2,
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        service.update_rule(
            db,
            actor=scenario.admin,
            rule_id=rule["id"],
            payload=ChainStepRuleUpdate(condition_expression=_condition("not_a_field", "gt", 1)),
        )
    assert exc_info.value.status_code == 422


def test_role_from_another_org_is_404(db: Session, scenario: Scenario):
    other_org = _make_org(db, name="OtherOrgChainConfig")
    other_role = _make_role(db, org_id=other_org.id, name="other", permission_values=[])
    _definition, step = _make_definition_and_step(db, scenario)
    with pytest.raises(HTTPException) as exc_info:
        service.create_rule(
            db,
            actor=scenario.admin,
            step_id=step["id"],
            payload=ChainStepRuleCreate(
                is_base_requirement=True,
                condition_expression=None,
                required_role_id=other_role.id,
                sequence_order=1,
            ),
        )
    assert exc_info.value.status_code == 404


def test_duplicate_base_requirement_for_same_role_is_409(db: Session, scenario: Scenario):
    _definition, step = _make_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True,
            condition_expression=None,
            required_role_id=scenario.approver_role.id,
            sequence_order=1,
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        service.create_rule(
            db,
            actor=scenario.admin,
            step_id=step["id"],
            payload=ChainStepRuleCreate(
                is_base_requirement=True,
                condition_expression=None,
                required_role_id=scenario.approver_role.id,
                sequence_order=1,
            ),
        )
    assert exc_info.value.status_code == 409


def test_two_condition_rules_for_same_role_are_both_allowed(db: Session, scenario: Scenario):
    _definition, step = _make_definition_and_step(db, scenario)
    r1 = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("contract_value", "gt", 1_000_000),
            required_role_id=scenario.finance_role.id,
            sequence_order=2,
        ),
    )
    r2 = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("jurisdiction", "eq", "EU"),
            required_role_id=scenario.finance_role.id,
            sequence_order=2,
        ),
    )
    assert r1["id"] != r2["id"]
    live = db.scalars(
        select(ApprovalChainStepRule).where(ApprovalChainStepRule.step_id == step["id"])
    ).all()
    assert len(live) == 2


def test_rule_updated_audit_carries_prior_and_new_fields(db: Session, scenario: Scenario):
    _definition, step = _make_definition_and_step(db, scenario)
    rule = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("contract_value", "gt", 1_000_000),
            required_role_id=scenario.finance_role.id,
            sequence_order=2,
        ),
    )
    service.update_rule(
        db,
        actor=scenario.admin,
        rule_id=rule["id"],
        payload=ChainStepRuleUpdate(condition_expression=_condition("contract_value", "gt", 2_000_000)),
    )
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "approval_chain.rule_updated", AuditLog.resource_id == rule["id"]
        )
    )
    assert audit is not None
    assert audit.before["condition_expression"]["value"] == 1_000_000
    assert audit.after["condition_expression"]["value"] == 2_000_000
    assert audit.after["required_role_id"] == scenario.finance_role.id
    assert audit.after["sequence_order"] == 2
    assert audit.after["is_base_requirement"] is False


# --- permission gate (403) -------------------------------------------------


def test_manage_permission_gate_denies_a_plain_user(db: Session, scenario: Scenario):
    dependency = require_permission("approval_chain:manage")
    with pytest.raises(HTTPException) as exc_info:
        dependency(current_user=scenario.plain, db=db)
    assert exc_info.value.status_code == 403
