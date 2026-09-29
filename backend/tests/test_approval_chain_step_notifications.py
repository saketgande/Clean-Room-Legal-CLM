"""Every user eligible for an approval-chain step's required role gets
emailed the moment that step is materialized — at chain creation AND on
every subsequent step advance (the exact gap: gande.saket@in.ey.com, role
`approver`, never got notified when the NDA Fast-Track's "Approval" step
rerouted into the org's active contract chain definition).

``_materialize_step`` (approval_chains/service.py) is the single hook point
— called from exactly two places (`create_instance`, `_advance_step`) and
always creates fresh `ApprovalChainRequirement` rows, so it's inherently a
one-shot-per-step-transition event; no idempotency guard is needed the way
`_assign_step`'s `notified_at` marker is.

Fixture conventions follow `test_approval_chain_reroute.py` (rollback-only
`db` for calling service functions directly) and
`test_workflow_team_notifications.py` (`db_real_commit` for calling
`jobs.tasks._notify_approval_chain_step_holders`, which opens its own
`SessionLocal()` and so needs committed rows to see anything).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approval_chains import service as chain_service
from app.approval_chains.models import ApprovalChainDefinition, ApprovalChainRequirement
from app.approval_chains.schemas import (
    ChainDecisionPayload,
    ChainDefinitionCreate,
    ChainStepCreate,
    ChainStepRuleCreate,
)
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.contracts.models import Contract
from app.core.database import engine, new_uuid
from app.jobs import tasks as jobs_tasks
from app.notifications.models import Notification
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization

_CLEANUP_MODELS: tuple[type, ...] = (Notification,)


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


@pytest.fixture
def db_real_commit():
    connection = engine.connect()
    session = Session(bind=connection)
    org_ids: list[str] = []
    try:
        yield session, org_ids
    finally:
        session.rollback()
        if org_ids:
            for model in _CLEANUP_MODELS:
                session.query(model).filter(model.org_id.in_(org_ids)).delete(synchronize_session=False)
            session.query(Organization).filter(Organization.id.in_(org_ids)).delete(synchronize_session=False)
            session.commit()
        session.close()
        connection.close()


def _get_or_create_permission(db: Session, value: str) -> Permission:
    existing = db.query(Permission).filter(Permission.value == value).one_or_none()
    if existing is not None:
        return existing
    perm = Permission(value=value)
    db.add(perm)
    db.flush()
    return perm


def _make_org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="Chain Co", slug=f"chain-co-{uuid.uuid4().hex[:8]}")
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
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def _make_grant(db: Session, *, user: User, role: Role, org_unit: OrgUnit) -> UserRoleGrant:
    grant = UserRoleGrant(user_id=user.id, role_id=role.id, org_id=user.org_id, org_unit_id=org_unit.id)
    db.add(grant)
    db.flush()
    return grant


def _make_contract(db: Session, *, org_id: str, owner: User) -> Contract:
    contract = Contract(
        org_id=org_id, title="Confidential NDA", owner_user_id=owner.id,
        contract_type="nda", risk_band="low", risk_level="low", jurisdiction="US", currency="USD",
        created_by_user_id=owner.id, updated_by_user_id=owner.id,
    )
    db.add(contract)
    db.flush()
    return contract


def _two_step_definition(db: Session, *, admin: User, role1: Role, role2: Role) -> ApprovalChainDefinition:
    definition = chain_service.create_definition(
        db, actor=admin, payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="contract"),
    )
    step1 = chain_service.create_step(
        db, actor=admin, definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1),
    )
    chain_service.create_rule(
        db, actor=admin, step_id=step1["id"],
        payload=ChainStepRuleCreate(is_base_requirement=True, condition_expression=None,
                                    required_role_id=role1.id, sequence_order=1),
    )
    step2 = chain_service.create_step(
        db, actor=admin, definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step2", name="Step 2", sequence_order=2),
    )
    chain_service.create_rule(
        db, actor=admin, step_id=step2["id"],
        payload=ChainStepRuleCreate(is_base_requirement=True, condition_expression=None,
                                    required_role_id=role2.id, sequence_order=1),
    )
    return db.get(ApprovalChainDefinition, definition["id"])


def test_materialize_step_dispatches_once_on_create_and_again_on_advance(db: Session, monkeypatch):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    admin = _make_user(db, org_id=org.id, label="admin")
    role1 = _make_role(db, org_id=org.id, name="approver1")
    role2 = _make_role(db, org_id=org.id, name="approver2")
    holder1 = _make_user(db, org_id=org.id, label="holder1")
    _make_grant(db, user=holder1, role=role1, org_unit=root)
    definition = _two_step_definition(db, admin=admin, role1=role1, role2=role2)
    contract = _make_contract(db, org_id=org.id, owner=admin)

    calls: list[tuple[list, dict]] = []
    monkeypatch.setattr(
        "app.jobs.tasks.notify_approval_chain_step_holders.apply_async",
        lambda args=None, **kwargs: calls.append((args, kwargs)),
    )

    from app.approval_chains.schemas import ChainInstanceCreate

    instance = chain_service.create_instance(
        db, actor=admin,
        payload=ChainInstanceCreate(definition_id=definition.id, module="contract", module_record_id=contract.id),
    )
    assert len(calls) == 1
    step1_id = calls[0][0][1]
    assert calls[0][1].get("countdown", 0) > 0

    _decide(db, actor=holder1, instance_id=instance.id, role_id=role1.id, decision="approve")

    assert len(calls) == 2
    step2_id = calls[1][0][1]
    assert step2_id != step1_id


def _decide(db: Session, *, actor: User, instance_id: str, role_id: str, decision: str):
    from sqlalchemy import select

    requirement = db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance_id,
            ApprovalChainRequirement.required_role_id == role_id,
        )
    )
    return chain_service.record_decision(
        db, actor=actor, instance_id=instance_id, requirement_id=requirement.id,
        payload=ChainDecisionPayload(decision=decision, comment=None),
    )


async def test_notify_holders_emails_every_holder_of_the_steps_role(db_real_commit, monkeypatch):
    # _materialize_step (reached via create_instance below) auto-dispatches a
    # REAL Celery task the moment this test's data is committed — this repo's
    # worker container runs with real SendGrid credentials, not test mocks,
    # so an un-monkeypatched dispatch here would fire a genuine outbound
    # email to a fake @example.com address. Silence it; the test asserts
    # against a direct in-process call to the notify function instead.
    monkeypatch.setattr(
        "app.jobs.tasks.notify_approval_chain_step_holders.apply_async", lambda *a, **k: None,
    )
    db, org_ids = db_real_commit
    org = _make_org(db)
    org_ids.append(org.id)
    root = _make_root_unit(db, org_id=org.id)
    admin = _make_user(db, org_id=org.id, label="admin")
    role1 = _make_role(db, org_id=org.id, name="approver1")
    role2 = _make_role(db, org_id=org.id, name="approver2")
    holder_a = _make_user(db, org_id=org.id, label="holder-a")
    holder_b = _make_user(db, org_id=org.id, label="holder-b")
    _make_grant(db, user=holder_a, role=role1, org_unit=root)
    _make_grant(db, user=holder_b, role=role1, org_unit=root)
    definition = _two_step_definition(db, admin=admin, role1=role1, role2=role2)
    contract = _make_contract(db, org_id=org.id, owner=admin)

    from app.approval_chains.schemas import ChainInstanceCreate

    instance = chain_service.create_instance(
        db, actor=admin,
        payload=ChainInstanceCreate(definition_id=definition.id, module="contract", module_record_id=contract.id),
    )
    step1_id = instance.current_step_id
    db.commit()

    result = await jobs_tasks._notify_approval_chain_step_holders(instance.id, step1_id)

    assert result["sent"] == 2
    assert result["failed"] == 0
    notified = {
        n.user_id for n in db.query(Notification).filter(
            Notification.org_id == org.id, Notification.event_type == "approval_chain.step_assigned",
        ).all()
    }
    assert notified == {holder_a.id, holder_b.id}


async def test_notify_holders_sends_nothing_for_an_unfulfillable_role(db_real_commit, monkeypatch):
    monkeypatch.setattr(
        "app.jobs.tasks.notify_approval_chain_step_holders.apply_async", lambda *a, **k: None,
    )
    db, org_ids = db_real_commit
    org = _make_org(db)
    org_ids.append(org.id)
    _make_root_unit(db, org_id=org.id)
    admin = _make_user(db, org_id=org.id, label="admin")
    role1 = _make_role(db, org_id=org.id, name="approver1")  # nobody holds this
    role2 = _make_role(db, org_id=org.id, name="approver2")
    definition = _two_step_definition(db, admin=admin, role1=role1, role2=role2)
    contract = _make_contract(db, org_id=org.id, owner=admin)

    from app.approval_chains.schemas import ChainInstanceCreate

    instance = chain_service.create_instance(
        db, actor=admin,
        payload=ChainInstanceCreate(definition_id=definition.id, module="contract", module_record_id=contract.id),
    )
    step1_id = instance.current_step_id
    db.commit()

    result = await jobs_tasks._notify_approval_chain_step_holders(instance.id, step1_id)

    assert result["sent"] == 0
    assert result["failed"] == 0


async def test_notify_holders_dedupes_a_user_eligible_via_two_required_roles(db_real_commit, monkeypatch):
    monkeypatch.setattr(
        "app.jobs.tasks.notify_approval_chain_step_holders.apply_async", lambda *a, **k: None,
    )
    db, org_ids = db_real_commit
    org = _make_org(db)
    org_ids.append(org.id)
    root = _make_root_unit(db, org_id=org.id)
    admin = _make_user(db, org_id=org.id, label="admin")
    role1 = _make_role(db, org_id=org.id, name="approver1")
    role2 = _make_role(db, org_id=org.id, name="approver2")
    dual_holder = _make_user(db, org_id=org.id, label="dual-holder")
    _make_grant(db, user=dual_holder, role=role1, org_unit=root)
    _make_grant(db, user=dual_holder, role=role2, org_unit=root)

    definition = chain_service.create_definition(
        db, actor=admin, payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="contract"),
    )
    step = chain_service.create_step(
        db, actor=admin, definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1),
    )
    chain_service.create_rule(
        db, actor=admin, step_id=step["id"],
        payload=ChainStepRuleCreate(is_base_requirement=True, condition_expression=None,
                                    required_role_id=role1.id, sequence_order=1),
    )
    chain_service.create_rule(
        db, actor=admin, step_id=step["id"],
        payload=ChainStepRuleCreate(is_base_requirement=True, condition_expression=None,
                                    required_role_id=role2.id, sequence_order=2),
    )
    contract = _make_contract(db, org_id=org.id, owner=admin)

    from app.approval_chains.schemas import ChainInstanceCreate

    instance = chain_service.create_instance(
        db, actor=admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="contract", module_record_id=contract.id),
    )
    db.commit()

    result = await jobs_tasks._notify_approval_chain_step_holders(instance.id, step["id"])

    assert result["sent"] == 1
    notified = [
        n for n in db.query(Notification).filter(
            Notification.org_id == org.id, Notification.event_type == "approval_chain.step_assigned",
        ).all()
    ]
    assert len(notified) == 1
    assert notified[0].user_id == dual_holder.id
