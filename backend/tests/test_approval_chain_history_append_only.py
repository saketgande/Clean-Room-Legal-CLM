"""Tests that ``approval_chain_history`` is genuinely append-only (feature
004-approval-chain-reconciliation, T009, FR-10/FR-11):

1. A source-level scan confirming ``app/approval_chains/service.py`` never
   attempts an ``.update()``/``.delete()`` call chained off an
   ``ApprovalChainHistory`` query -- the service layer should never even
   try, on top of T001's ORM event-listener + Postgres trigger guards.
2. A behavioral test: a decide + recalculate sequence only ever INSERTs new
   history rows -- row count monotonically increases and no existing row's
   content changes.
3. A direct confirmation that T001's ORM guard rejects an accidental mutation
   attempt against an ``ApprovalChainHistory`` instance.
"""

from __future__ import annotations

import ast
import inspect
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.approval_chains.service as service_module

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
    # create_savepoint: the guard test makes a flush fail on purpose. Without a
    # savepoint the session's rollback would also end this outer transaction,
    # and the cleanup below would warn that it is already gone.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
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
        self.org = _make_org(db, name="ChainHistoryAppendOnlyTest")
        self.root = _make_root_unit(db, org_id=self.org.id)
        self.admin = _make_user(db, org_id=self.org.id, label="admin")


@pytest.fixture
def scenario(db: Session) -> Scenario:
    return Scenario(db)


# --- 1. source-level scan -----------------------------------------------


def test_service_source_never_calls_update_or_delete_on_a_history_query():
    source = inspect.getsource(service_module)
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
            "update",
            "delete",
        ):
            try:
                callee_src = ast.unparse(node.func.value)
            except Exception:  # pragma: no cover -- defensive
                callee_src = ""
            if "ApprovalChainHistory" in callee_src:
                violations.append(ast.unparse(node))
    assert violations == [], (
        "service.py must never .update()/.delete() an ApprovalChainHistory "
        f"query -- found: {violations}"
    )


def test_service_source_has_no_direct_attribute_assignment_on_a_history_row_other_than_append():
    """A slightly different angle on the same invariant: the only
    ``ApprovalChainHistory(...)`` construction the service module contains is
    inside ``_append_history`` -- there is no second construction site that
    might later grow a mutating follow-up assignment.
    """
    source = inspect.getsource(service_module)
    tree = ast.parse(source)
    construction_sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ApprovalChainHistory":
            construction_sites.append(ast.unparse(node))
    assert len(construction_sites) == 1


# --- 2. behavioral: decide + recalculate only ever inserts -----------------


def test_decide_and_recalculate_sequence_only_appends_history_rows(db: Session, scenario: Scenario):
    role_a = _make_role(db, org_id=scenario.org.id, name="role-a")
    role_b = _make_role(db, org_id=scenario.org.id, name="role-b")
    user_a = _make_user(db, org_id=scenario.org.id, label="user-a")
    _make_grant(db, user=user_a, role=role_a, org_unit=scenario.root)

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
    # role_b's base rule is added BEFORE the instance is created so both
    # requirements are live at materialization time -- deciding role_a alone
    # then leaves role_b pending, keeping the instance "pending" so
    # recalculate stays reachable afterward.
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
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )

    def _snapshot() -> dict[str, dict]:
        rows = db.scalars(
            select(ApprovalChainHistory).where(ApprovalChainHistory.instance_id == instance.id)
        ).all()
        return {
            row.id: {
                "action": row.action,
                "acted_by_user_id": row.acted_by_user_id,
                "comments": row.comments,
                "before_json": row.before_json,
                "after_json": row.after_json,
            }
            for row in rows
        }

    snap_after_create = _snapshot()
    assert len(snap_after_create) >= 1

    requirement_a = db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id, ApprovalChainRequirement.required_role_id == role_a.id
        )
    )
    service.record_decision(
        db,
        actor=user_a,
        instance_id=instance.id,
        requirement_id=requirement_a.id,
        payload=ChainDecisionPayload(decision="approve"),
    )
    snap_after_decide = _snapshot()

    # every row from the prior snapshot is still present, byte-identical
    for row_id, content in snap_after_create.items():
        assert row_id in snap_after_decide
        assert snap_after_decide[row_id] == content
    assert len(snap_after_decide) > len(snap_after_create)

    service.recalculate(db, actor=scenario.admin, instance_id=instance.id, payload=ChainRecalculatePayload(reason="check"))
    snap_after_recalc = _snapshot()

    for row_id, content in snap_after_decide.items():
        assert row_id in snap_after_recalc
        assert snap_after_recalc[row_id] == content
    assert len(snap_after_recalc) > len(snap_after_decide)


# --- 3. direct ORM guard confirmation --------------------------------------


def test_orm_guard_rejects_a_mutation_attempt_against_an_existing_history_row(db: Session, scenario: Scenario):
    role = _make_role(db, org_id=scenario.org.id, name="role-guard")
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
            is_base_requirement=True, condition_expression=None, required_role_id=role.id, sequence_order=1
        ),
    )
    request = _make_intake_request(db, org_id=scenario.org.id, requester_id=scenario.admin.id)
    instance = service.create_instance(
        db,
        actor=scenario.admin,
        payload=ChainInstanceCreate(definition_id=definition["id"], module="intake_request", module_record_id=request.id),
    )
    row = db.scalar(
        select(ApprovalChainHistory).where(ApprovalChainHistory.instance_id == instance.id)
    )
    assert row is not None

    row.comments = "an accidental edit"
    with pytest.raises(RuntimeError):
        db.flush()
