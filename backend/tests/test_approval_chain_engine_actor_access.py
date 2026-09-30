"""A workflow-engine auto-advance into a contract-approval step must not
404 just because the person who completed the PREVIOUS step (a human_task
assigned via a team, unrelated to the contract) has no direct access to the
contract itself.

Root cause: ``approval_chains.dispatch.start_chain_for_subject`` (the only
caller reached from the engine's auto-reroute in
``app.approvals.service.submit_subject_for_approval``) called
``service.create_instance`` with the SAME actor-record-access enforcement
(``access.get_module_record_or_404``'s ``user_can_access_contract`` check)
written for a human directly hitting ``POST /approval-chains/instances`` —
but that actor is "whoever's HTTP request happened to trigger this hop,"
not someone who needs standing access to the contract. A team-assigned
reviewer with no ownership/matter-membership/grant on the contract got a
misleading 404 "Contract not found" the instant their unrelated step's
completion auto-advanced the run into a contract-approval step under an
active chain definition.

Fixed by adding `enforce_actor_access` (default True — the admin-facing
POST /instances route keeps the existing check unchanged) that
`start_chain_for_subject` passes as False, since the engine itself already
authorized reaching this step; the chain's named approvers still get their
own access check when THEY act.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approval_chains import access, dispatch
from app.approval_chains import service as chain_service
from app.approval_chains.schemas import ChainDefinitionCreate, ChainStepCreate
from app.approvals.service import ContractSubject
from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import engine, new_uuid
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


def _make_org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="Access Co", slug=f"access-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _make_root_unit(db: Session, *, org_id: str) -> OrgUnit:
    unit = OrgUnit(org_id=org_id, name="Root", parent_id=None)
    db.add(unit)
    db.flush()
    return unit


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


def _make_contract(db: Session, *, org_id: str, owner: User) -> Contract:
    contract = Contract(
        org_id=org_id, title="Confidential NDA", owner_user_id=owner.id,
        contract_type="nda", risk_band="low", risk_level="low", jurisdiction="US", currency="USD",
        created_by_user_id=owner.id, updated_by_user_id=owner.id,
    )
    db.add(contract)
    db.flush()
    return contract


def _make_contract_chain_definition(db: Session, *, admin: User) -> dict:
    """A one-step, no-condition active chain definition for the "contract"
    module — enough to reach create_instance's record-access check."""
    definition = chain_service.create_definition(
        db, actor=admin,
        payload=ChainDefinitionCreate(name=f"Def-{uuid.uuid4().hex[:6]}", module="contract"),
    )
    chain_service.create_step(
        db, actor=admin, definition_id=definition["id"],
        payload=ChainStepCreate(step_key="step1", name="Step 1", sequence_order=1),
    )
    return definition


def test_engine_auto_advance_does_not_404_for_an_actor_with_no_contract_access(db: Session):
    org = _make_org(db)
    _make_root_unit(db, org_id=org.id)
    admin = _make_user(db, org_id=org.id, label="admin")
    owner = _make_user(db, org_id=org.id, label="owner")
    bystander = _make_user(db, org_id=org.id, label="bystander")  # completed an unrelated step
    contract = _make_contract(db, org_id=org.id, owner=owner)
    definition_dict = _make_contract_chain_definition(db, admin=admin)
    from app.approval_chains.models import ApprovalChainDefinition

    definition = db.get(ApprovalChainDefinition, definition_dict["id"])

    # Confirm the premise: bystander genuinely has no standing access to this
    # contract (not owner/creator, no matter membership, no grant).
    from app.contracts.access import user_can_access_contract

    assert user_can_access_contract(db, contract=contract, user=bystander) is False

    subject = ContractSubject(contract)
    instance = dispatch.start_chain_for_subject(db, actor=bystander, subject=subject, definition=definition)

    assert instance is not None
    assert instance.module_record_id == contract.id


def test_get_module_record_or_404_still_blocks_direct_admin_api_use(db: Session):
    """The admin-facing POST /approval-chains/instances path (create_instance
    called WITHOUT enforce_actor_access=False) must keep blocking a caller
    with no access to the contract — this fix must not weaken that route."""
    org = _make_org(db)
    owner = _make_user(db, org_id=org.id, label="owner")
    bystander = _make_user(db, org_id=org.id, label="bystander")
    contract = _make_contract(db, org_id=org.id, owner=owner)

    with pytest.raises(HTTPException) as exc:
        access.get_module_record_or_404(db, actor=bystander, module="contract", record_id=contract.id)
    assert exc.value.status_code == 404

    # enforce_actor_access=False (the engine path) skips exactly that check.
    fetched = access.get_module_record_or_404(
        db, actor=bystander, module="contract", record_id=contract.id, enforce_actor_access=False,
    )
    assert fetched.id == contract.id
