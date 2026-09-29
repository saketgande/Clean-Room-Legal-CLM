"""Tests for ``service.record_decision`` (feature 004-approval-chain-
reconciliation, T009): the sequential gate, fresh eligibility re-check,
authority parity, approve/reject branching, delegation attribution, and
``_advance_step`` firing.

Follows the fixture/tree conventions of ``test_approval_chain_config_api.py``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

# registers every domain's models (e.g. Contract) so SQLAlchemy's mapper
# configuration can resolve intake_request.contract_id's FK when this file
# is run standalone rather than as part of the full suite.
import app.models  # noqa: F401
from app.approval_chains import service
from app.approval_chains.models import ApprovalChainRequirement
from app.approval_chains.schemas import (
    ChainDecisionPayload,
    ChainDefinitionCreate,
    ChainDefinitionUpdate,
    ChainInstanceCreate,
    ChainStepCreate,
    ChainStepRuleCreate,
)
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import UserStatus
from app.core.models import AuditLog
from app.intake.models import IntakeRequest
from app.org_structure.models import Delegation, OrgUnit
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


def _make_intake_request(db: Session, *, org_id: str, requester_id: str) -> IntakeRequest:
    request = IntakeRequest(
        org_id=org_id,
        ref=f"REQ-{uuid.uuid4().hex[:6]}",
        source="form",
        requester_user_id=requester_id,
        type_label="General request",
        description="test request",
        field_values={},
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
        self.org = _make_org(db, name="ChainDecisionsTest")
        self.root = _make_root_unit(db, org_id=self.org.id)
        self.admin = _make_user(db, org_id=self.org.id, label="admin")


@pytest.fixture
def scenario(db: Session) -> Scenario:
    return Scenario(db)


def _build_single_role_instance(db: Session, scenario: Scenario, *, role: Role, approval_mode: str = "sequential"):
    definition = service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="intake_request")
    )
    step = service.create_step(
        db,
        actor=scenario.admin,
        definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1, approval_mode=approval_mode),
    )
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
        payload=ChainInstanceCreate(
            definition_id=definition["id"], module="intake_request", module_record_id=request.id
        ),
    )
    requirement = db.scalar(
        select(ApprovalChainRequirement).where(ApprovalChainRequirement.instance_id == instance.id)
    )
    return definition, step, instance, requirement


# --- sequential gate (FR-13) --------------------------------------------------


def test_sequential_gate_rejects_a_later_decision_while_an_earlier_one_is_pending(
    db: Session, scenario: Scenario
):
    role_a = _make_role(db, org_id=scenario.org.id, name="role-a")
    role_b = _make_role(db, org_id=scenario.org.id, name="role-b")
    user_b = _make_user(db, org_id=scenario.org.id, label="user-b")
    _make_grant(db, user=user_b, role=role_b, org_unit=scenario.root)

    definition = service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="intake_request")
    )
    step = service.create_step(
        db,
        actor=scenario.admin,
        definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1, approval_mode="sequential"),
    )
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_a.id, sequence_order=1
        ),
    )
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_b.id, sequence_order=2
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
    requirement_b = db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id, ApprovalChainRequirement.required_role_id == role_b.id
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        service.record_decision(
            db,
            actor=user_b,
            instance_id=instance.id,
            requirement_id=requirement_b.id,
            payload=ChainDecisionPayload(decision="approve"),
        )
    assert exc_info.value.status_code == 409


# --- fresh eligibility re-check (FR-15) --------------------------------------


def test_lost_eligibility_before_deciding_is_rejected_fresh_not_from_a_stale_snapshot(
    db: Session, scenario: Scenario
):
    role = _make_role(db, org_id=scenario.org.id, name="role-x")
    user = _make_user(db, org_id=scenario.org.id, label="user-x")
    grant = _make_grant(db, user=user, role=role, org_unit=scenario.root)

    _definition, _step, instance, requirement = _build_single_role_instance(db, scenario, role=role)
    assert requirement.eligible_user_count == 1  # true at materialization time

    # The user loses the grant before acting -- eligibility must be re-checked
    # fresh, never trusting the materialization-time snapshot's holder count.
    grant.deleted_at = utcnow()
    grant.deleted_by_user_id = scenario.admin.id
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        service.record_decision(
            db,
            actor=user,
            instance_id=instance.id,
            requirement_id=requirement.id,
            payload=ChainDecisionPayload(decision="approve"),
        )
    assert exc_info.value.status_code == 403


# --- authority parity ---------------------------------------------------------


def test_enforce_authority_called_on_approve_but_not_on_reject(db: Session, scenario: Scenario, monkeypatch):
    import app.authority.service as authority_service

    calls: list[str] = []
    original = authority_service.enforce_authority

    def _spy(db_arg, **kwargs):
        calls.append(kwargs.get("action"))
        return original(db_arg, **kwargs)

    monkeypatch.setattr(authority_service, "enforce_authority", _spy)

    role_approve = _make_role(db, org_id=scenario.org.id, name="role-approve")
    user_approve = _make_user(db, org_id=scenario.org.id, label="user-approve")
    _make_grant(db, user=user_approve, role=role_approve, org_unit=scenario.root)
    _definition, _step, instance, requirement = _build_single_role_instance(db, scenario, role=role_approve)

    service.record_decision(
        db,
        actor=user_approve,
        instance_id=instance.id,
        requirement_id=requirement.id,
        payload=ChainDecisionPayload(decision="approve"),
    )
    assert calls == ["contract:approve"]

    # only one active definition per (org, module) is allowed -- deactivate
    # the first before building a second scenario.
    service.update_definition(
        db, actor=scenario.admin, definition_id=_definition["id"], payload=ChainDefinitionUpdate(is_active=False)
    )

    calls.clear()
    role_reject = _make_role(db, org_id=scenario.org.id, name="role-reject")
    user_reject = _make_user(db, org_id=scenario.org.id, label="user-reject")
    _make_grant(db, user=user_reject, role=role_reject, org_unit=scenario.root)
    _definition2, _step2, instance2, requirement2 = _build_single_role_instance(db, scenario, role=role_reject)

    service.record_decision(
        db,
        actor=user_reject,
        instance_id=instance2.id,
        requirement_id=requirement2.id,
        payload=ChainDecisionPayload(decision="reject", comment="not acceptable"),
    )
    assert calls == []


# --- approve/reject branching (FR-17) ----------------------------------------


def test_reject_cancels_every_other_live_pending_requirement_and_calls_on_reject(
    db: Session, scenario: Scenario
):
    role_a = _make_role(db, org_id=scenario.org.id, name="role-a")
    role_b = _make_role(db, org_id=scenario.org.id, name="role-b")
    user_a = _make_user(db, org_id=scenario.org.id, label="user-a")
    user_b = _make_user(db, org_id=scenario.org.id, label="user-b")
    _make_grant(db, user=user_a, role=role_a, org_unit=scenario.root)
    _make_grant(db, user=user_b, role=role_b, org_unit=scenario.root)

    definition = service.create_definition(
        db, actor=scenario.admin, payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="intake_request")
    )
    step = service.create_step(
        db,
        actor=scenario.admin,
        definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1, approval_mode="parallel"),
    )
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_a.id, sequence_order=1
        ),
    )
    service.create_rule(
        db,
        actor=scenario.admin,
        step_id=step["id"],
        payload=ChainStepRuleCreate(
            is_base_requirement=True, condition_expression=None, required_role_id=role_b.id, sequence_order=1
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
    requirement_a = db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id, ApprovalChainRequirement.required_role_id == role_a.id
        )
    )

    updated_instance = service.record_decision(
        db,
        actor=user_a,
        instance_id=instance.id,
        requirement_id=requirement_a.id,
        payload=ChainDecisionPayload(decision="reject", comment="rejecting"),
    )
    assert updated_instance.status == "rejected"

    requirements = db.scalars(
        select(ApprovalChainRequirement).where(ApprovalChainRequirement.instance_id == instance.id)
    ).all()
    req_a = next(r for r in requirements if r.required_role_id == role_a.id)
    req_b = next(r for r in requirements if r.required_role_id == role_b.id)
    assert req_a.status == "rejected"
    assert req_b.status == "cancelled"

    # subject.on_reject was called -> reopened the request (status stays
    # "open" since that's the reject target) and wrote the reject audit row.
    db.refresh(request)
    assert request.status == "open"
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "intake.approval_rejected", AuditLog.resource_id == request.id
        )
    )
    assert audit is not None


def test_reject_requires_a_comment(db: Session, scenario: Scenario):
    role = _make_role(db, org_id=scenario.org.id, name="role-nc")
    user = _make_user(db, org_id=scenario.org.id, label="user-nc")
    _make_grant(db, user=user, role=role, org_unit=scenario.root)
    _definition, _step, instance, requirement = _build_single_role_instance(db, scenario, role=role)

    with pytest.raises(HTTPException) as exc_info:
        service.record_decision(
            db,
            actor=user,
            instance_id=instance.id,
            requirement_id=requirement.id,
            payload=ChainDecisionPayload(decision="reject", comment=None),
        )
    assert exc_info.value.status_code == 422


# --- delegation attribution (AC-19) ------------------------------------------


def test_delegate_decision_attributes_acted_by_and_delegated_from_as_distinct_fields(
    db: Session, scenario: Scenario
):
    role = _make_role(db, org_id=scenario.org.id, name="role-deleg")
    delegator = _make_user(db, org_id=scenario.org.id, label="delegator")
    delegate = _make_user(db, org_id=scenario.org.id, label="delegate")
    _make_grant(db, user=delegator, role=role, org_unit=scenario.root)

    now = utcnow()
    delegation = Delegation(
        org_id=scenario.org.id,
        delegator_user_id=delegator.id,
        delegate_user_id=delegate.id,
        role_id=role.id,
        org_unit_id=scenario.root.id,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
        status="active",
    )
    db.add(delegation)
    db.flush()

    _definition, _step, instance, requirement = _build_single_role_instance(db, scenario, role=role)

    service.record_decision(
        db,
        actor=delegate,
        instance_id=instance.id,
        requirement_id=requirement.id,
        payload=ChainDecisionPayload(decision="approve"),
    )
    db.refresh(requirement)
    assert requirement.acted_by_user_id == delegate.id
    assert requirement.delegated_from_user_id == delegator.id
    assert requirement.acted_by_user_id != requirement.delegated_from_user_id


# --- _advance_step ------------------------------------------------------------


def test_advance_step_fires_when_every_live_counting_requirement_is_approved(
    db: Session, scenario: Scenario
):
    role = _make_role(db, org_id=scenario.org.id, name="role-adv")
    user = _make_user(db, org_id=scenario.org.id, label="user-adv")
    _make_grant(db, user=user, role=role, org_unit=scenario.root)
    _definition, _step, instance, requirement = _build_single_role_instance(db, scenario, role=role)

    updated_instance = service.record_decision(
        db,
        actor=user,
        instance_id=instance.id,
        requirement_id=requirement.id,
        payload=ChainDecisionPayload(decision="approve"),
    )
    # single-step definition -> no next step -> instance completes.
    assert updated_instance.status == "approved"
    assert updated_instance.current_step_id is None
