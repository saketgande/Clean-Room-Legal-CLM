"""Tests for the FR-5 materialization core (feature 004-approval-chain-
reconciliation, T009): grouping condition/base sources by role, unfulfillable
detection + its history row, and the invariant that NO read path ever
triggers materialization.

Follows the fixture/tree conventions of ``test_approval_chain_config_api.py``
/ ``test_org_access_role_holders.py``: a real (migrated) Postgres session,
one transaction per test, rolled back at teardown.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# registers every domain's models (e.g. Contract) so SQLAlchemy's mapper
# configuration can resolve intake_request.contract_id's FK when this file
# is run standalone rather than as part of the full suite.
import app.models  # noqa: F401
from app.approval_chains import service
from app.approval_chains.models import ApprovalChainHistory, ApprovalChainRequirement
from app.approval_chains.schemas import (
    ChainDecisionPayload,
    ChainDefinitionCreate,
    ChainInstanceCreate,
    ChainStepCreate,
    ChainStepRuleCreate,
    ConditionExpressionIn,
)
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import UserStatus
from app.intake.models import IntakeRequest
from app.org_structure.models import OrgUnit
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


def _make_root_unit(db: Session, *, org_id: str) -> OrgUnit:
    unit = OrgUnit(org_id=org_id, name="Root", parent_id=None)
    db.add(unit)
    db.flush()
    return unit


def _make_role(db: Session, *, org_id: str, name: str, permission_values: list[str] | None = None) -> Role:
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    role.permissions = [_get_or_create_permission(db, v) for v in (permission_values or [])]
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


def _make_grant(db: Session, *, user: User, role: Role, org_unit: OrgUnit) -> UserRoleGrant:
    grant = UserRoleGrant(user_id=user.id, role_id=role.id, org_id=user.org_id, org_unit_id=org_unit.id)
    db.add(grant)
    db.flush()
    return grant


def _make_intake_request(
    db: Session, *, org_id: str, requester_id: str, department: str | None = None, amount: float | None = None
) -> IntakeRequest:
    request = IntakeRequest(
        org_id=org_id,
        ref=f"REQ-{uuid.uuid4().hex[:6]}",
        source="form",
        requester_user_id=requester_id,
        type_label="General request",
        description="test request",
        field_values={"amount": amount} if amount is not None else {},
        department=department,
        priority="Medium",
        status="open",
        stage="new",
        submitted_at=utcnow(),
        created_by_user_id=requester_id,
        updated_by_user_id=requester_id,
    )
    db.add(request)
    db.flush()
    return request


class Scenario:
    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="ChainMaterializationTest")
        self.root = _make_root_unit(db, org_id=self.org.id)
        self.admin = _make_user(db, org_id=self.org.id, label="admin")
        self.approver_role = _make_role(db, org_id=self.org.id, name="approver")
        self.finance_role = _make_role(db, org_id=self.org.id, name="finance")
        self.orphan_role = _make_role(db, org_id=self.org.id, name="orphan")

        self.approver_user = _make_user(db, org_id=self.org.id, label="approver-user")
        _make_grant(db, user=self.approver_user, role=self.approver_role, org_unit=self.root)
        self.finance_user = _make_user(db, org_id=self.org.id, label="finance-user")
        _make_grant(db, user=self.finance_user, role=self.finance_role, org_unit=self.root)
        # orphan_role deliberately has no grant anywhere -> unfulfillable.


@pytest.fixture
def scenario(db: Session) -> Scenario:
    return Scenario(db)


def _build_definition_and_step(db: Session, scenario: Scenario, *, approval_mode: str = "sequential"):
    definition = service.create_definition(
        db,
        actor=scenario.admin,
        payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="intake_request"),
    )
    step = service.create_step(
        db,
        actor=scenario.admin,
        definition_id=definition["id"],
        payload=ChainStepCreate(
            step_key="step1", name="Step 1", sequence_order=1, approval_mode=approval_mode
        ),
    )
    return definition, step


def _condition(field: str, operator: str, value) -> ConditionExpressionIn:
    return ConditionExpressionIn(field=field, operator=operator, value=value)


# --- grouping (FR-3/AC-5) ---------------------------------------------------


def test_multiple_firing_rules_for_same_role_collapse_to_one_requirement(db: Session, scenario: Scenario):
    definition, step = _build_definition_and_step(db, scenario)
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
    rule1 = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("department", "eq", "Finance"),
            required_role_id=scenario.finance_role.id,
            sequence_order=2,
        ),
    )
    rule2 = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("request_value", "gt", 1_000_000),
            required_role_id=scenario.finance_role.id,
            sequence_order=3,
        ),
    )

    request = _make_intake_request(
        db, org_id=scenario.org.id, requester_id=scenario.admin.id, department="Finance", amount=2_000_000
    )
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(
            definition_id=definition["id"], module="intake_request", module_record_id=request.id
        ),
    )

    requirements = db.scalars(
        select(ApprovalChainRequirement).where(ApprovalChainRequirement.instance_id == instance.id)
    ).all()
    assert len(requirements) == 2  # one per role, not one per firing rule

    finance_req = next(r for r in requirements if r.required_role_id == scenario.finance_role.id)
    assert finance_req.is_base_requirement is False
    assert set(finance_req.triggered_by_rule_ids) == {rule1["id"], rule2["id"]}
    assert len(finance_req.condition_explanations) == 2
    for explanation in finance_req.condition_explanations:
        assert explanation["rule_id"] in {rule1["id"], rule2["id"]}
        assert explanation.get("text")

    approver_req = next(r for r in requirements if r.required_role_id == scenario.approver_role.id)
    assert approver_req.is_base_requirement is True
    assert approver_req.triggered_by_rule_ids == []


def test_condition_rule_that_does_not_match_is_not_materialized(db: Session, scenario: Scenario):
    definition, step = _build_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("department", "eq", "Finance"),
            required_role_id=scenario.finance_role.id,
            sequence_order=1,
        ),
    )
    request = _make_intake_request(
        db, org_id=scenario.org.id, requester_id=scenario.admin.id, department="Legal"
    )
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(
            definition_id=definition["id"], module="intake_request", module_record_id=request.id
        ),
    )
    requirements = db.scalars(
        select(ApprovalChainRequirement).where(ApprovalChainRequirement.instance_id == instance.id)
    ).all()
    assert requirements == []


# --- unfulfillable (FR-19) ---------------------------------------------------


def test_role_with_no_holders_is_unfulfillable_and_writes_history(db: Session, scenario: Scenario):
    definition, step = _build_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True,
            condition_expression=None,
            required_role_id=scenario.orphan_role.id,
            sequence_order=1,
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(
            definition_id=definition["id"], module="intake_request", module_record_id=request.id
        ),
    )
    requirement = db.scalar(
        select(ApprovalChainRequirement).where(ApprovalChainRequirement.instance_id == instance.id)
    )
    assert requirement.is_unfulfillable is True
    assert requirement.eligible_user_count == 0

    history_rows = db.scalars(
        select(ApprovalChainHistory).where(
            ApprovalChainHistory.instance_id == instance.id,
            ApprovalChainHistory.action == "blocked_no_eligible_approver",
        )
    ).all()
    assert len(history_rows) == 1
    assert history_rows[0].requirement_id == requirement.id


def test_unfulfillable_requirement_blocks_completion_and_is_visible_to_an_admin(
    db: Session, scenario: Scenario
):
    """AC-16 (FR-19) / AC-17 (FR-20): a step with one fulfillable base
    requirement and one requirement for a role with zero eligible holders
    stays blocked (never completes) even after the fulfillable requirement
    is decided, and an admin can see -- on the instance itself, via
    ``get_blocked``, not only via logs -- exactly which required role has no
    eligible holder.

    T017/QA raised this pairing (under the label "AC-18") as lacking a
    dedicated regression test; ``test_role_with_no_holders_is_unfulfillable_
    and_writes_history`` above covers detection (``is_unfulfillable``,
    the history row) but never asserts the step stays BLOCKED FROM COMPLETING
    (FR-19's second half) nor that ``get_blocked`` (FR-20) actually surfaces
    the blocked role's name -- this test closes exactly that gap.
    """
    definition, step = _build_definition_and_step(db, scenario, approval_mode="parallel")
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
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True,
            condition_expression=None,
            required_role_id=scenario.orphan_role.id,
            sequence_order=1,
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(
            definition_id=definition["id"], module="intake_request", module_record_id=request.id
        ),
    )
    approver_requirement = db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.required_role_id == scenario.approver_role.id,
        )
    )
    orphan_requirement = db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.required_role_id == scenario.orphan_role.id,
        )
    )
    assert orphan_requirement.is_unfulfillable is True

    # The one fulfillable requirement is decided -- the step still must not
    # complete while the orphan-role gap persists (FR-16 + FR-19).
    service.record_decision(
        db,
        actor=scenario.approver_user,
        instance_id=instance.id,
        requirement_id=approver_requirement.id,
        payload=ChainDecisionPayload(decision="approve"),
    )
    db.refresh(instance)
    assert instance.status == "pending"

    blocked = service.get_blocked(db, actor=scenario.admin, instance_id=instance.id)
    assert blocked["instance_id"] == instance.id
    assert len(blocked["blocking"]) == 1
    entry = blocked["blocking"][0]
    assert entry["requirement_id"] == orphan_requirement.id
    assert entry["required_role_id"] == scenario.orphan_role.id
    assert entry["required_role_name"] == scenario.orphan_role.name


# --- FR-5: never evaluated on read -------------------------------------------


def test_read_paths_never_materialize_new_requirements(db: Session, scenario: Scenario):
    definition, step = _build_definition_and_step(db, scenario)
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
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(
            definition_id=definition["id"], module="intake_request", module_record_id=request.id
        ),
    )

    def _count() -> int:
        return db.scalar(
            select(func.count()).select_from(ApprovalChainRequirement).where(
                ApprovalChainRequirement.instance_id == instance.id
            )
        )

    before = _count()
    service.list_instances(db, actor=scenario.admin)
    service.get_instance_detail(db, actor=scenario.admin, instance_id=instance.id)
    service.get_history(db, actor=scenario.admin, instance_id=instance.id)
    service.get_blocked(db, actor=scenario.admin, instance_id=instance.id)
    after = _count()

    assert before == after == 1
