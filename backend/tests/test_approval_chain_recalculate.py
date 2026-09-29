"""Tests for ``service.recalculate`` (feature 004-approval-chain-
reconciliation, T009, FR-9/FR-10): the four reconciliation cases (added /
unchanged / removed-no-decision / removed-with-decision), the single
``recalculated`` history row per call, the 409 when the instance is not
pending, and step completion triggered by a recalculation that removes the
last outstanding requirement.

Follows the fixture/tree conventions of ``test_approval_chain_config_api.py``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
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
    ChainRecalculatePayload,
    ChainStepCreate,
    ChainStepRuleCreate,
    ChainStepRuleUpdate,
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


def _make_role(db: Session, *, org_id: str, name: str) -> Role:
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
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


def _make_intake_request(db: Session, *, org_id: str, requester_id: str, department: str | None = None) -> IntakeRequest:
    request = IntakeRequest(
        org_id=org_id,
        ref=f"REQ-{uuid.uuid4().hex[:6]}",
        source="form",
        requester_user_id=requester_id,
        type_label="General request",
        description="test request",
        field_values={},
        department=department,
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
        self.org = _make_org(db, name="ChainRecalculateTest")
        self.root = _make_root_unit(db, org_id=self.org.id)
        self.admin = _make_user(db, org_id=self.org.id, label="admin")


@pytest.fixture
def scenario(db: Session) -> Scenario:
    return Scenario(db)


def _condition(field: str, operator: str, value) -> ConditionExpressionIn:
    return ConditionExpressionIn(field=field, operator=operator, value=value)


def _build_definition_and_step(db: Session, scenario: Scenario, *, approval_mode: str = "parallel"):
    definition = service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="intake_request")
    )
    step = service.create_step(
        db,
        actor=scenario.admin,
        definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1, approval_mode=approval_mode),
    )
    return definition, step


def _requirement_for_role(db: Session, instance_id: str, role_id: str) -> ApprovalChainRequirement | None:
    return db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance_id,
            ApprovalChainRequirement.required_role_id == role_id,
            ApprovalChainRequirement.deleted_at.is_(None),
        )
    )


# --- added role ----------------------------------------------------------


def test_recalculate_adds_a_new_pending_requirement_for_a_newly_added_rule(db: Session, scenario: Scenario):
    role_a = _make_role(db, org_id=scenario.org.id, name="role-a")
    role_b = _make_role(db, org_id=scenario.org.id, name="role-b")
    definition, step = _build_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_a.id, sequence_order=1
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    assert _requirement_for_role(db, instance.id, role_b.id) is None

    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_b.id, sequence_order=2
        ),
    )
    service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload(reason="added role b"))

    new_req = _requirement_for_role(db, instance.id, role_b.id)
    assert new_req is not None
    assert new_req.status == "pending"


# --- unchanged role --------------------------------------------------------


def test_recalculate_refreshes_unchanged_role_and_preserves_existing_decision(db: Session, scenario: Scenario):
    role_decided = _make_role(db, org_id=scenario.org.id, name="role-decided")
    role_pending = _make_role(db, org_id=scenario.org.id, name="role-pending")
    user_decided = _make_user(db, org_id=scenario.org.id, label="user-decided")
    _make_grant(db, user=user_decided, role=role_decided, org_unit=scenario.root)

    definition, step = _build_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_decided.id, sequence_order=1
        ),
    )
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_pending.id, sequence_order=2
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    req_decided = _requirement_for_role(db, instance.id, role_decided.id)
    service.record_decision(
        db,
        actor=user_decided,
        instance_id=instance.id,
        requirement_id=req_decided.id,
        payload=ChainDecisionPayload(decision="approve"),
    )

    # Add another holder for the already-decided role so recalculate's
    # refresh of eligible_user_count is observable.
    another_user = _make_user(db, org_id=scenario.org.id, label="another")
    _make_grant(db, user=another_user, role=role_decided, org_unit=scenario.root)

    service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload())

    db.refresh(req_decided)
    assert req_decided.status == "approved"  # decision preserved
    assert req_decided.eligible_user_count == 2  # refreshed
    assert req_decided.superseded_at is None
    assert req_decided.counts_toward_completion is True


# --- removed role, no decision -> soft-deleted -------------------------------


def test_recalculate_soft_deletes_a_removed_role_with_no_decision(db: Session, scenario: Scenario):
    role_gone = _make_role(db, org_id=scenario.org.id, name="role-gone")
    definition, step = _build_definition_and_step(db, scenario)
    rule = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("department", "eq", "Finance"),
            required_role_id=role_gone.id,
            sequence_order=1,
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id, department="Finance")
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    req_gone = _requirement_for_role(db, instance.id, role_gone.id)
    assert req_gone is not None
    assert req_gone.status == "pending"

    # Change the condition so it no longer matches current facts.
    service.update_rule(
        db,
        actor=scenario.admin,
        rule_id=rule["id"],
        payload=ChainStepRuleUpdate(condition_expression=_condition("department", "eq", "Sales")),
    )
    service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload())

    db.refresh(req_gone)
    assert req_gone.deleted_at is not None
    assert req_gone.deleted_by_user_id == scenario.admin.id


# --- removed role, WITH decision -> superseded, no longer counted -----------


def test_recalculate_supersedes_a_removed_role_that_already_has_a_decision(db: Session, scenario: Scenario):
    role_decided_then_removed = _make_role(db, org_id=scenario.org.id, name="role-superseded")
    role_kept_pending = _make_role(db, org_id=scenario.org.id, name="role-kept")
    user_decided = _make_user(db, org_id=scenario.org.id, label="user-decided2")
    _make_grant(db, user=user_decided, role=role_decided_then_removed, org_unit=scenario.root)

    definition, step = _build_definition_and_step(db, scenario)
    rule = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("department", "eq", "Finance"),
            required_role_id=role_decided_then_removed.id,
            sequence_order=1,
        ),
    )
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_kept_pending.id, sequence_order=2
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id, department="Finance")
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    req_target = _requirement_for_role(db, instance.id, role_decided_then_removed.id)
    service.record_decision(
        db,
        actor=user_decided,
        instance_id=instance.id,
        requirement_id=req_target.id,
        payload=ChainDecisionPayload(decision="approve"),
    )

    service.update_rule(
        db,
        actor=scenario.admin,
        rule_id=rule["id"],
        payload=ChainStepRuleUpdate(condition_expression=_condition("department", "eq", "Sales")),
    )
    service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload())

    db.refresh(req_target)
    assert req_target.status == "approved"  # decision preserved
    assert req_target.deleted_at is None
    assert req_target.superseded_at is not None
    assert req_target.counts_toward_completion is False


# --- exactly one history row per call, before/after lists -------------------


def test_recalculate_writes_exactly_one_recalculated_history_row_with_full_before_after(
    db: Session, scenario: Scenario
):
    role_a = _make_role(db, org_id=scenario.org.id, name="role-hist-a")
    role_b = _make_role(db, org_id=scenario.org.id, name="role-hist-b")
    definition, step = _build_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_a.id, sequence_order=1
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_b.id, sequence_order=2
        ),
    )
    service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload(reason="add b"))

    rows = db.scalars(
        select(ApprovalChainHistory).where(
            ApprovalChainHistory.instance_id == instance.id, ApprovalChainHistory.action == "recalculated"
        )
    ).all()
    assert len(rows) == 1
    assert isinstance(rows[0].before_json, list) and len(rows[0].before_json) >= 1
    assert isinstance(rows[0].after_json, list) and len(rows[0].after_json) >= 2


# --- 409 when not pending ----------------------------------------------------


def test_recalculate_is_409_when_instance_is_not_pending(db: Session, scenario: Scenario):
    role = _make_role(db, org_id=scenario.org.id, name="role-notpending")
    user = _make_user(db, org_id=scenario.org.id, label="user-notpending")
    _make_grant(db, user=user, role=role, org_unit=scenario.root)

    definition, step = _build_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role.id, sequence_order=1
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    requirement = _requirement_for_role(db, instance.id, role.id)
    service.record_decision(
        db,
        actor=user,
        instance_id=instance.id,
        requirement_id=requirement.id,
        payload=ChainDecisionPayload(decision="reject", comment="no"),
    )

    with pytest.raises(HTTPException) as exc_info:
        service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload())
    assert exc_info.value.status_code == 409


# --- recalculation removing the last outstanding requirement completes -----


def test_recalculate_removing_last_outstanding_requirement_completes_the_step(db: Session, scenario: Scenario):
    role_done = _make_role(db, org_id=scenario.org.id, name="role-done")
    role_outstanding = _make_role(db, org_id=scenario.org.id, name="role-outstanding")
    user_done = _make_user(db, org_id=scenario.org.id, label="user-done")
    _make_grant(db, user=user_done, role=role_done, org_unit=scenario.root)

    definition, step = _build_definition_and_step(db, scenario)
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_done.id, sequence_order=1
        ),
    )
    rule_outstanding = service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=False,
            condition_expression=_condition("department", "eq", "Finance"),
            required_role_id=role_outstanding.id,
            sequence_order=2,
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id, department="Finance")
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    req_done = _requirement_for_role(db, instance.id, role_done.id)
    service.record_decision(
        db,
        actor=user_done,
        instance_id=instance.id,
        requirement_id=req_done.id,
        payload=ChainDecisionPayload(decision="approve"),
    )
    updated_instance = service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload())
    assert updated_instance.status == "pending"  # role_outstanding still fires -> still pending

    # Now change the rule so role_outstanding no longer matches -> its
    # (still-pending, undecided) requirement is soft-deleted by recalculate,
    # leaving only the already-approved role_done requirement live, which
    # completes the step/instance.
    service.update_rule(
        db,
        actor=scenario.admin,
        rule_id=rule_outstanding["id"],
        payload=ChainStepRuleUpdate(condition_expression=_condition("department", "eq", "Sales")),
    )
    final_instance = service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload())
    assert final_instance.status == "approved"
    assert final_instance.current_step_id is None
