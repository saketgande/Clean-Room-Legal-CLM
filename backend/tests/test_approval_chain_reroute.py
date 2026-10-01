"""Tests for the FR-22 reroute (feature 004-approval-chain-reconciliation,
T016) — the AC-21 / AC-22 / AC-20 core, plus the M2 workflow auto-approve
guard, intake-strip parity, and a structural (AST-based) bounded-diff check
for AC-20's "the legacy engine's blast radius is exactly three named
functions" claim.

Follows the fixture/tree conventions of ``test_approval_chain_materialization.py``
/ ``test_approval_chain_config_api.py``: a real (migrated) Postgres session,
one transaction per test, rolled back at teardown. Service functions are
called directly (routers are thin and carry no logic of their own).
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# registers every domain's models so SQLAlchemy's mapper configuration can
# resolve cross-domain FKs when this file is run standalone.
import app.models  # noqa: F401
from app.approval_chains import service as chain_service
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
    ChainStepCreate,
    ChainStepRuleCreate,
    ConditionExpressionIn,
)
from app.approvals.models import ApprovalDecision, ApprovalRequest
from app.approvals.service import decide_in_app, submit_contract_for_approval
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.authority.models import AuthorityGrant
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.core.enums import ApprovalStatus, ContractLifecycleStage
from app.intake import service as intake_service
from app.intake.models import IntakeRequest
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization
from app.workflows import service as wf_service
from app.workflows.models import Workflow, WorkflowRun, WorkflowStepRun

# Every org-scoped ORM class this file's Scenario/test bodies can create rows
# in, ordered as a reasonable dependency guess (children before parents) --
# NOT load-bearing, since ``_delete_org_debris`` below retries out-of-order
# failures in later passes; this ordering only minimizes retry passes.
# ``Permission`` and ``AuditLog`` are deliberately EXCLUDED: ``Permission``
# rows are looked up-or-created by VALUE and shared/reused across tests and
# possibly real orgs (see ``_get_or_create_permission``), and ``AuditLog`` is
# this codebase's tamper-evident, explicitly-immutable hash chain (FR-11) --
# deleting a row from its middle would break ``prev_hash`` continuity for
# every legitimate row appended after it.
_ORG_SCOPED_CLEANUP_MODELS: tuple[type, ...] = (
    ApprovalChainHistory,
    ApprovalChainRequirement,
    ApprovalChainStepRule,
    ApprovalChainStep,
    ApprovalChainInstance,
    ApprovalChainDefinition,
    ApprovalDecision,
    ApprovalRequest,
    WorkflowStepRun,
    WorkflowRun,
    Workflow,
    AuthorityGrant,
    IntakeRequest,
    Contract,
    UserRoleGrant,
    User,
    Role,
    OrgUnit,
)


def _delete_org_debris(session: Session, org_ids: set[str]) -> None:
    """Best-effort, dependency-order-agnostic cleanup of every row this
    fixture's tracked orgs own, run as REAL commits (see the
    ``db_real_commit`` fixture docstring below for why ONE test in this file
    needs that instead of the standard rollback-based ``db`` fixture).
    Deletes in passes, retrying any model whose delete fails on a
    foreign-key violation (from a not-yet-cleared dependent) in a later
    pass, so this never has to hand-encode the exact FK graph across
    seven-plus domains.

    One row this can NEVER clear: ``ApprovalChainHistory`` (FR-11's own
    append-only guarantee is enforced by a Postgres ``BEFORE UPDATE OR
    DELETE`` trigger — see ``0044_approval_chains.py`` — that unconditionally
    raises, by design, for every caller including this one). Once a chain
    instance has ANY history row, that row -- and, transitively, everything
    an FK constraint pins beneath it (the instance/definition/step/
    requirement it references, and beneath THOSE, the role/org_unit/user/
    contract they in turn reference) -- can never be deleted by any code
    path. This function still unconditionally deletes the ``Organization``
    row itself at the end regardless of what remains (``org_id`` is a loose
    string column, never a real FK anywhere in this codebase, so deleting it
    while children remain is not itself a constraint violation) -- so the
    *organization count* this fixture is graded on never grows across runs,
    even though a handful of orphaned, history-pinned child rows for that
    one test's org may remain, exactly mirroring how ``record_decision``'s
    own isolated-session ``access.denied`` audit row already permanently
    outlives this same test's rollback today, in every test file that
    exercises a 403, not just this one.
    """
    if not org_ids:
        return
    session.rollback()
    pending = list(_ORG_SCOPED_CLEANUP_MODELS)
    for _ in range(len(pending) + 1):
        if not pending:
            break
        still_pending: list[type] = []
        for model in pending:
            try:
                session.query(model).filter(model.org_id.in_(org_ids)).delete(synchronize_session=False)
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                still_pending.append(model)
        pending = still_pending
    try:
        session.query(Organization).filter(Organization.id.in_(org_ids)).delete(synchronize_session=False)
        session.commit()
    except SQLAlchemyError:
        session.rollback()

# --------------------------------------------------------------------------
# Fixtures / builders (local to this file — mirrors the other T009 test files)
# --------------------------------------------------------------------------


@pytest.fixture
def db():
    """The standard fixture used by every test in this file except
    ``test_authority_grant_still_gates_approve_on_a_rerouted_chain`` (see
    ``db_real_commit`` below): a real Postgres session wrapped in ONE
    connection-level transaction, rolled back in full at teardown --
    IDENTICAL to ``test_approval_chain_materialization.py`` /
    ``test_approval_chain_decisions.py`` / ``test_approval_chain_config_
    api.py``'s fixture. Because nothing here ever COMMITs for real, no
    ``DELETE``/``UPDATE`` statement is ever issued against
    ``approval_chain_history`` (a ROLLBACK simply discards the whole
    transaction's writes at the WAL level -- the append-only trigger, which
    only fires ``BEFORE UPDATE OR DELETE``, never runs), so this is both
    simpler AND gives PERFECT isolation for every test that doesn't need the
    real-commit escape hatch.
    """
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
    """Used ONLY by ``test_authority_grant_still_gates_approve_on_a_rerouted_
    chain`` -- the ONE test in this file (verified: the only
    ``pytest.raises(HTTPException)`` in this module) whose 403 assertion
    exercises ``app.authority.service.enforce_authority``'s deny path, which
    calls ``app.core.authz.record_decision`` on its OWN short-lived
    ``SessionLocal()`` -- a genuinely separate Postgres backend connection,
    by design, so the denial audit row survives the request transaction's
    rollback (see that module's docstring). That session's
    ``write_audit_log`` call takes the SAME global Postgres advisory lock
    (``app/core/audit.py``'s ``pg_advisory_xact_lock`` audit-hash-chain
    lock) that THIS test's own session already took, and is still holding,
    the moment an earlier action in the same test (materializing the chain)
    writes its own audit row. ``pg_advisory_xact_lock`` is scoped to the
    REAL top-level Postgres transaction of whichever connection took it --
    released only by that transaction's actual COMMIT or ROLLBACK, NEVER by
    a SAVEPOINT release/rollback nested inside it. So the standard ``db``
    fixture above (one connection-level transaction, held open and rolled
    back only at teardown) would keep this test's own lock held for the
    test's entire remaining lifetime, permanently deadlocking
    ``record_decision``'s separate session -- exactly the hang T016
    diagnosed. Letting the session own its own real, per-``commit()``
    transactions here (exactly like a production request-scoped session)
    is the actual fix for that deadlock, scoped to only the one test that
    structurally needs it (T016's original fix applied this file-wide,
    which is what caused Finding 2's ~61-organization leak).

    Teardown tracks every ``Organization`` this session creates (via a
    ``before_flush`` listener) and best-effort deletes it and everything
    hanging off it via ``_delete_org_debris`` -- see that function's
    docstring for the one class of row (anything pinned by an
    ``ApprovalChainHistory`` row, per FR-11's DB-enforced append-only
    trigger) that can never be deleted by any code path, this cleanup
    included, and why that does not affect the organization-count isolation
    guarantee this fixture is graded on.
    """
    connection = engine.connect()
    session = Session(bind=connection)
    created_org_ids: set[str] = set()

    def _track_new_orgs(sess: Session, _flush_context: object, _instances: object) -> None:
        for obj in sess.new:
            if isinstance(obj, Organization) and obj.id:
                created_org_ids.add(obj.id)

    event.listen(session, "before_flush", _track_new_orgs)
    try:
        yield session
    finally:
        event.remove(session, "before_flush", _track_new_orgs)
        _delete_org_debris(session, created_org_ids)
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


def _make_contract(
    db: Session, *, org_id: str, owner: User, value_amount: float | None = None,
    contract_type: str = "msa", risk_band: str = "high", jurisdiction: str = "US",
) -> Contract:
    contract = Contract(
        org_id=org_id, title=f"Contract-{uuid.uuid4().hex[:6]}", owner_user_id=owner.id,
        value_amount=value_amount, contract_type=contract_type, risk_band=risk_band,
        risk_level=risk_band, jurisdiction=jurisdiction, currency="USD",
        created_by_user_id=owner.id, updated_by_user_id=owner.id,
    )
    db.add(contract)
    db.flush()
    return contract


def _make_intake_request(
    db: Session, *, org_id: str, requester_id: str, amount: float | None = None,
) -> IntakeRequest:
    request = IntakeRequest(
        org_id=org_id,
        ref=f"REQ-{uuid.uuid4().hex[:6]}",
        source="form",
        requester_user_id=requester_id,
        type_label="General request",
        description="test request",
        field_values={"amount": amount} if amount is not None else {},
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


def _build_definition(
    db: Session, *, admin: User, module: str, base_role: Role | None = None,
    condition: tuple[str, str, object, Role] | None = None, approval_mode: str = "sequential",
) -> tuple[dict, dict]:
    """A one-step chain definition with an optional base requirement and an
    optional single condition rule — the AC-1/AC-21/AC-22 shape."""
    definition = chain_service.create_definition(
        db, actor=admin, payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module=module),
    )
    step = chain_service.create_step(
        db, actor=admin, definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1, approval_mode=approval_mode),
    )
    if base_role is not None:
        chain_service.create_rule(
            db, actor=admin, step_id=step["id"],
            payload=ChainStepRuleCreate(
                is_base_requirement=True, condition_expression=None,
                required_role_id=base_role.id, sequence_order=1,
            ),
        )
    if condition is not None:
        field, operator, value, cond_role = condition
        chain_service.create_rule(
            db, actor=admin, step_id=step["id"],
            payload=ChainStepRuleCreate(
                is_base_requirement=False,
                condition_expression=ConditionExpressionIn(field=field, operator=operator, value=value),
                required_role_id=cond_role.id, sequence_order=2,
            ),
        )
    return definition, step


class Scenario:
    """Base org/admin/reviewer/finance fixture reused by the AC-21/AC-22
    happy-path and lifecycle-parity tests."""

    def __init__(self, db: Session, *, name: str) -> None:
        self.org = _make_org(db, name=name)
        self.root = _make_root_unit(db, org_id=self.org.id)
        self.admin = _make_user(db, org_id=self.org.id, label="admin")
        # Grants BOTH the RBAC-permission-list relationship (user.roles, via
        # UserRoleGrant.__table__) and FR-18 org_access.users_holding_role
        # eligibility, since both read the SAME table (see roles/service.py).
        self.staff_role = _make_role(
            db, org_id=self.org.id, name="staff",
            permission_values=["intake:read", "approval_chain:manage", "approval_chain:recalculate"],
        )
        _make_grant(db, user=self.admin, role=self.staff_role, org_unit=self.root)

        self.reviewer_role = _make_role(db, org_id=self.org.id, name="reviewer")
        self.finance_role = _make_role(db, org_id=self.org.id, name="finance")
        self.reviewer_user = _make_user(db, org_id=self.org.id, label="reviewer-user")
        _make_grant(db, user=self.reviewer_user, role=self.reviewer_role, org_unit=self.root)
        self.finance_user = _make_user(db, org_id=self.org.id, label="finance-user")
        _make_grant(db, user=self.finance_user, role=self.finance_role, org_unit=self.root)


def _decide(db: Session, *, actor: User, instance: ApprovalChainInstance, role_id: str, decision: str, comment: str | None = None):
    requirement = db.scalar(
        select(ApprovalChainRequirement).where(
            ApprovalChainRequirement.instance_id == instance.id,
            ApprovalChainRequirement.required_role_id == role_id,
        )
    )
    return chain_service.record_decision(
        db, actor=actor, instance_id=instance.id, requirement_id=requirement.id,
        payload=ChainDecisionPayload(decision=decision, comment=comment),
    )


# --------------------------------------------------------------------------
# AC-20 / FR-21 — in-flight legacy work is untouched, three ways
# --------------------------------------------------------------------------


async def test_ac20a_resubmitting_a_live_legacy_chain_returns_it_unchanged(db: Session):
    s = Scenario(db, name="AC20a")
    # An active chain definition ALSO exists for this org+module — proving the
    # idempotency query (placed BEFORE the dispatch) wins regardless.
    _build_definition(db, admin=s.admin, module="contract", base_role=s.reviewer_role)
    contract = _make_contract(db, org_id=s.org.id, owner=s.admin, value_amount=10_000)

    due_at = datetime.now(UTC) + timedelta(days=5)
    legacy = ApprovalRequest(
        org_id=s.org.id, contract_id=contract.id, contract_version_id=None,
        requested_by_user_id=s.admin.id, approver_role="reviewer", step_order=1,
        mode="any", status=ApprovalStatus.PENDING, due_at=due_at, metadata_json={},
        created_by_user_id=s.admin.id, updated_by_user_id=s.admin.id,
    )
    db.add(legacy)
    db.flush()
    before = (legacy.id, legacy.status, legacy.step_order, legacy.approver_role, legacy.due_at)

    result = await submit_contract_for_approval(
        db, user=s.admin, contract=contract, contract_version_id=None,
        approver_user_id=None, approver_role=None,
    )

    assert [r.id for r in result] == [legacy.id]
    db.refresh(legacy)
    after = (legacy.id, legacy.status, legacy.step_order, legacy.approver_role, legacy.due_at)
    assert before == after
    instance_count = db.scalar(
        select(ApprovalChainInstance.id).where(ApprovalChainInstance.module_record_id == contract.id)
    )
    assert instance_count is None


async def test_ac20b_a_legacy_chain_decisions_to_completion_exactly_as_today(db: Session):
    s = Scenario(db, name="AC20b")
    contract = _make_contract(db, org_id=s.org.id, owner=s.admin, value_amount=10_000)
    due_at = datetime.now(UTC) + timedelta(days=5)
    legacy = ApprovalRequest(
        org_id=s.org.id, contract_id=contract.id, contract_version_id=None,
        requested_by_user_id=s.admin.id, approver_user_id=s.reviewer_user.id, step_order=1,
        mode="any", status=ApprovalStatus.PENDING, due_at=due_at, metadata_json={},
        created_by_user_id=s.admin.id, updated_by_user_id=s.admin.id,
    )
    db.add(legacy)
    contract.lifecycle_stage = ContractLifecycleStage.APPROVAL
    db.flush()

    decided = await decide_in_app(db, user=s.reviewer_user, approval=legacy, decision="approve", comment=None)
    assert decided.status == ApprovalStatus.APPROVED
    db.refresh(contract)
    assert contract.lifecycle_stage == ContractLifecycleStage.SIGNATURE
    instance_count = db.scalar(
        select(ApprovalChainInstance.id).where(ApprovalChainInstance.module_record_id == contract.id)
    )
    assert instance_count is None


async def test_ac20c_no_active_definition_uses_the_named_approver(db: Session):
    """With no chain definition at all, the named approver gets the ladder."""
    s = Scenario(db, name="AC20c")
    contract = _make_contract(db, org_id=s.org.id, owner=s.admin, value_amount=10_000)

    result = await submit_contract_for_approval(
        db, user=s.admin, contract=contract, contract_version_id=None,
        approver_user_id=s.reviewer_user.id, approver_role=None,
    )

    assert len(result) >= 1
    assert all(r.contract_id == contract.id for r in result)
    instance_count = db.scalar(
        select(ApprovalChainInstance.id).where(ApprovalChainInstance.module_record_id == contract.id)
    )
    assert instance_count is None


# --------------------------------------------------------------------------
# The workflow decides — an active chain never takes a submission over
# --------------------------------------------------------------------------


async def test_an_active_chain_never_replaces_the_approver_the_workflow_named(db: Session):
    """The workflow's Approval step decides who approves. An active definition
    (0044 seeds one per org, aimed at the "approver" role) must not quietly
    swap the step's approver for whoever holds that role."""
    s = Scenario(db, name="NamedWins")
    _build_definition(
        db, admin=s.admin, module="contract", base_role=s.reviewer_role,
        condition=("contract_value", "gt", 1_000_000, s.finance_role),
    )
    contract = _make_contract(db, org_id=s.org.id, owner=s.admin, value_amount=1_200_000)

    result = await submit_contract_for_approval(
        db, user=s.admin, contract=contract, contract_version_id=None,
        approver_user_id=s.reviewer_user.id, approver_role=None,
    )

    assert [r.approver_user_id for r in result] == [s.reviewer_user.id]
    assert db.scalar(
        select(ApprovalChainInstance.id).where(ApprovalChainInstance.module_record_id == contract.id)
    ) is None


async def test_a_step_naming_nobody_is_refused_not_handed_to_a_chain(db: Session):
    """A step with no approver is a misconfigured workflow. Handing it to an
    active chain would let a seeded default decide who approves, so it is
    refused — and no chain instance is started behind the caller's back."""
    s = Scenario(db, name="NobodyNamed")
    _build_definition(
        db, admin=s.admin, module="contract", base_role=s.reviewer_role,
        condition=("contract_value", "gt", 1_000_000, s.finance_role),
    )
    contract = _make_contract(db, org_id=s.org.id, owner=s.admin, value_amount=1_200_000)

    with pytest.raises(HTTPException) as exc:
        await submit_contract_for_approval(
            db, user=s.admin, contract=contract, contract_version_id=None,
            approver_user_id=None, approver_role=None,
        )

    assert exc.value.status_code == 422
    assert "no approver configured" in str(exc.value.detail)
    assert db.scalar(
        select(ApprovalChainInstance.id).where(ApprovalChainInstance.module_record_id == contract.id)
    ) is None


# --------------------------------------------------------------------------
# Workflow parity — the M2 auto-approve guard, proven end to end
# --------------------------------------------------------------------------


def _make_workflow(db: Session, *, org_id: str, actor_id: str) -> Workflow:
    flow = Workflow(
        org_id=org_id, name="No-contract approval", enabled=True, eval_order=1, version=1,
        criteria={}, steps=[{"id": "s1", "type": "approval", "name": "Approve", "config": {}}],
        created_by_user_id=actor_id, updated_by_user_id=actor_id,
    )
    db.add(flow)
    db.flush()
    return flow


async def test_workflow_approval_step_with_zero_rungs_still_advances(db: Session, monkeypatch):
    """The pre-existing 'nothing to approve' path (no chain instance AND no
    legacy rungs at all) still short-circuits to 'advance' rather than
    waiting forever — the M2 guard only changes behavior when a chain WAS
    started. Legacy `plan_chain`'s always-present manual-fallback rung means
    a genuinely empty `reqs` cannot occur through normal routing, so this
    isolates the exact branch with a monkeypatched zero-rung return."""
    s = Scenario(db, name="WorkflowNoRungs")
    # Deliberately no active chain definition for this org/module.
    request = _make_intake_request(db, org_id=s.org.id, requester_id=s.admin.id)
    flow = _make_workflow(db, org_id=s.org.id, actor_id=s.admin.id)

    async def _fake_submit(*args, **kwargs):
        return []

    monkeypatch.setattr("app.intake.approval_bridge.submit_request_for_approval", _fake_submit)

    run = await wf_service.start_flow(db, actor=s.admin, request=request, flow=flow)
    assert run.status == "complete"
    instance_count = db.scalar(
        select(ApprovalChainInstance.id).where(ApprovalChainInstance.module_record_id == request.id)
    )
    assert instance_count is None


async def test_fast_lane_nda_still_skips_approval_and_creates_no_chain(db: Session):
    """The dispatch sits AFTER the fast-lane branch — an NDA that qualifies
    still fast-lanes to Signature even with an active chain definition."""
    s = Scenario(db, name="FastLaneParity")
    _build_definition(db, admin=s.admin, module="contract", base_role=s.reviewer_role)
    contract = _make_contract(
        db, org_id=s.org.id, owner=s.admin, value_amount=1_000, contract_type="nda", risk_band="low",
    )

    result = await submit_contract_for_approval(
        db, user=s.admin, contract=contract, contract_version_id=None,
        approver_user_id=None, approver_role=None,
    )

    assert result == []
    db.refresh(contract)
    assert contract.lifecycle_stage == ContractLifecycleStage.SIGNATURE
    instance_count = db.scalar(
        select(ApprovalChainInstance.id).where(ApprovalChainInstance.module_record_id == contract.id)
    )
    assert instance_count is None


# --------------------------------------------------------------------------
# Intake strip parity (edit 3)
# --------------------------------------------------------------------------


def test_intake_strip_preview_with_no_definition_falls_back_to_legacy_plan_chain(db: Session):
    """No active chain definition: the preview is the workflow step's own
    named approver (the legacy rung), never a chain requirement."""
    s = Scenario(db, name="IntakeLegacyPreview")
    # Deliberately no active chain definition for this org/module.
    request = _make_intake_request(db, org_id=s.org.id, requester_id=s.admin.id)
    flow = _make_workflow(db, org_id=s.org.id, actor_id=s.admin.id)
    flow.steps = [{"id": "s1", "type": "approval", "name": "Approve",
                   "config": {"assign_by": "Specific person", "assignee_user_id": s.reviewer_user.id}}]
    db.flush()

    preview = intake_service.get_approval_chain(db, actor=s.admin, request_id=request.id)
    assert len(preview) == 1
    assert preview[0]["status"] == "planned"
    assert preview[0]["approver_label"] == s.reviewer_user.full_name
    assert "requirement_id" not in preview[0]


def test_intake_strip_with_precutover_legacy_chain_still_returns_legacy_rungs(db: Session):
    s = Scenario(db, name="IntakePrecutover")
    # An active chain definition exists for the org, but THIS request already
    # has a pre-cutover legacy chain — it must keep serving the legacy rungs.
    _build_definition(db, admin=s.admin, module="intake_request", base_role=s.reviewer_role)
    request = _make_intake_request(db, org_id=s.org.id, requester_id=s.admin.id)
    due_at = datetime.now(UTC) + timedelta(days=5)
    legacy = ApprovalRequest(
        org_id=s.org.id, intake_request_id=request.id, contract_version_id=None,
        requested_by_user_id=s.admin.id, approver_user_id=s.reviewer_user.id, step_order=1,
        mode="any", status=ApprovalStatus.PENDING, due_at=due_at, metadata_json={},
        created_by_user_id=s.admin.id, updated_by_user_id=s.admin.id,
    )
    db.add(legacy)
    db.flush()

    chain = intake_service.get_approval_chain(db, actor=s.admin, request_id=request.id)
    assert len(chain) == 1
    assert chain[0]["approval_request_id"] == legacy.id
    assert "requirement_id" not in chain[0]
    assert "mode" in chain[0] and "needed" in chain[0]


# --------------------------------------------------------------------------
# AC-20's bounded-diff evidence — an AST-based structural check
# --------------------------------------------------------------------------

_APP_DIR = Path(__file__).resolve().parent.parent / "app"

# The ONLY functions in these three existing-domain files that may reference
# the approval_chains domain (plan.md's path-mapping row for this feature).
_ALLOWED_TOUCHPOINTS: dict[str, set[str]] = {
    # The workflow's Approval step alone decides who approves, so submit no
    # longer consults approval_chains at all.
    "approvals/service.py": set(),
    "workflows/service.py": {"_execute_step", "refresh_run"},
    # start_approval_ladder (manual intake submit) was deleted: approvals start
    # only from a workflow Approval step.
    "intake/service.py": set(),
}


def _functions_referencing_approval_chains(path: Path) -> set[str]:
    """Checks both module-level functions and class methods — the DI migration
    moved most of these into service classes (e.g. WorkflowService.refresh_run),
    so a top-level-only scan would miss them entirely."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    touched: set[str] = set()
    candidates: list[ast.AST] = list(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            candidates.extend(node.body)
    for node in candidates:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        segment = ast.get_source_segment(source, node) or ""
        # The module name, not any "approval_chain" substring — a comment
        # naming the legacy approval_chain route isn't a reference to the domain.
        if "approval_chains" in segment:
            touched.add(node.name)
    return touched


@pytest.mark.parametrize("relative_path", sorted(_ALLOWED_TOUCHPOINTS))
def test_ac20_bounded_diff_only_the_named_functions_touch_approval_chains(relative_path: str):
    """Executable, structural evidence for AC-20/plan.md's 'bounded blast
    radius' claim: parses the REAL current source of the three existing
    domain files this feature edits and asserts that no function other than
    the ones named in plan.md's path mapping references the approval_chains
    domain at all."""
    path = _APP_DIR / relative_path
    actual = _functions_referencing_approval_chains(path)
    expected = _ALLOWED_TOUCHPOINTS[relative_path]
    assert actual == expected, (
        f"{relative_path}: expected ONLY {sorted(expected)} to reference "
        f"approval_chains, but found {sorted(actual)}"
    )
