"""Against a real database: deciding a change decides the playbook issue it fixes,
in the same transaction, and a batch is every suggestion on one base version.

The unit tests in test_unified_suggestions.py use fakes; this file checks the
parts that depend on real SQL and real rows (the batch query, the issue closing,
the decision record). Needs the same Postgres as the rest of the suite.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models, so this file also runs on its own
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.contract_files import routes
from app.contract_files.models import ContractEdit, ContractFile, ContractVersion, StorageObject
from app.contract_files.schemas import ContractEditDecisionRequest
from app.contracts.models import Contract
from app.core.database import engine
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization
from app.playbooks.models import (
    Playbook,
    PlaybookDecision,
    PlaybookDeviation,
    PlaybookRun,
    PlaybookVersion,
)
from app.playbooks.service import PlaybooksService


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


def _permission(db: Session, value: str) -> Permission:
    found = db.query(Permission).filter(Permission.value == value).one_or_none()
    if found is None:
        found = Permission(value=value)
        db.add(found)
        db.flush()
    return found


def _user(db: Session, *permissions: str) -> User:
    org = Organization(name="Suggestions Test Org", slug=f"sug-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    root = OrgUnit(org_id=org.id, name="Global", parent_id=None)
    db.add(root)
    role = Role(org_id=org.id, name=f"sug-role-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    role.permissions = [_permission(db, p) for p in permissions]
    db.add(role)
    user = User(
        org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com",
        full_name="Suggestions Tester", hashed_password="hash", status="active",
    )
    db.add(user)
    db.flush()
    db.add(UserRoleGrant(org_id=org.id, user_id=user.id, role_id=role.id, org_unit_id=root.id, created_by_user_id=user.id))
    db.flush()
    return user


class World:
    """A contract on version v1, with an open playbook issue and room for edits."""

    def __init__(self, db: Session, user: User):
        self.db, self.user = db, user
        org = user.org_id
        self.contract = Contract(org_id=org, title="MSA", owner_user_id=user.id)
        db.add(self.contract)
        db.flush()
        self.file = ContractFile(org_id=org, contract_id=self.contract.id, file_label="MSA.docx")
        db.add(self.file)
        db.flush()
        self.v1 = self.version(1)
        self.contract.current_authoritative_version_id = self.v1.id
        playbook = Playbook(org_id=org, name="MSA playbook")
        db.add(playbook)
        db.flush()
        pv = PlaybookVersion(org_id=org, playbook_id=playbook.id, version_number=1)
        db.add(pv)
        db.flush()
        run = PlaybookRun(
            org_id=org, playbook_id=playbook.id, playbook_version_id=pv.id,
            contract_id=self.contract.id, contract_version_id=self.v1.id,
        )
        db.add(run)
        db.flush()
        self.issue = PlaybookDeviation(
            org_id=org, playbook_run_id=run.id, contract_id=self.contract.id,
            severity="high", issue="Payment terms too long",
        )
        db.add(self.issue)
        db.flush()

    def version(self, number: int) -> ContractVersion:
        stored = StorageObject(
            org_id=self.user.org_id, storage_key=f"test/{uuid.uuid4().hex}", filename="MSA.docx",
            mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex,
        )
        self.db.add(stored)
        self.db.flush()
        version = ContractVersion(
            org_id=self.user.org_id, contract_id=self.contract.id, contract_file_id=self.file.id,
            version_number=number, storage_object_id=stored.id,
        )
        self.db.add(version)
        self.db.flush()
        return version

    def edit(self, *, base: ContractVersion, proposal: ContractVersion, issue: PlaybookDeviation | None = None,
             edit_type: str = "replace") -> ContractEdit:
        citation = [
            {"type": "assistant_edit_version", "contract_version_id": proposal.id},
            {"type": "anchor", "start": 0, "end": 7, "matched": True, "applied": True},
        ]
        if issue is not None:
            citation.append({"type": "playbook_deviation", "playbook_deviation_id": issue.id})
        row = ContractEdit(
            org_id=self.user.org_id, contract_id=self.contract.id, contract_version_id=base.id,
            edit_type=edit_type, status="proposed", original_text="Net 30.", replacement_text="Net 60.",
            citation=citation,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def reject(self, edit: ContractEdit, comment: str | None = None):
        return routes.reject_contract_edit(
            self.contract.id, edit.id, ContractEditDecisionRequest(comment=comment),
            db=self.db, playbooks_service=PlaybooksService(self.db), current_user=self.user,
        )


@pytest.fixture
def world(db, monkeypatch):
    user = _user(db, "contract:redline", "playbook:run")
    w = World(db, user)
    monkeypatch.setattr(routes, "get_contract_for_user", lambda _db, **kw: w.contract)
    return w


def test_a_batch_spans_every_proposal_on_one_base_and_no_other_base(world):
    p1, p2, v2 = world.version(2), world.version(3), world.version(4)
    playbook_edit = world.edit(base=world.v1, proposal=p1, edit_type="playbook_redline")
    chat_edit = world.edit(base=world.v1, proposal=p2)
    on_newer_base = world.edit(base=v2, proposal=p2)
    batch = routes._redline_batch(world.db, edit=chat_edit)
    assert {e.id for e in batch} == {playbook_edit.id, chat_edit.id}
    assert on_newer_base.id not in {e.id for e in batch}


def test_rejecting_the_fix_for_a_high_risk_leaves_it_open_until_it_is_closed_with_a_reason(world):
    """Reject used to waive the issue too: one click cleared a high risk and left no reason."""
    from app.playbooks import routes as playbook_routes
    from app.playbooks.schemas import PlaybookDecisionCreate

    edit = world.edit(base=world.v1, proposal=world.version(2), issue=world.issue)
    assert world.issue.severity == "high"
    assert world.reject(edit).status == "rejected"
    world.db.refresh(world.issue)
    assert world.issue.status == "open"
    assert world.db.scalars(
        select(PlaybookDecision).where(PlaybookDecision.playbook_deviation_id == world.issue.id)
    ).all() == []
    with pytest.raises(HTTPException) as refused:
        playbook_routes.decide_deviation(
            world.issue.id, PlaybookDecisionCreate(decision="waived"),
            db=world.db, current_user=world.user, service=PlaybooksService(world.db))
    assert refused.value.status_code == 422
    world.db.refresh(world.issue)
    assert world.issue.status == "open"
    playbook_routes.decide_deviation(
        world.issue.id, PlaybookDecisionCreate(decision="waived", rationale="Kept the original wording. Agreed with the GC"),
        db=world.db, current_user=world.user, service=PlaybooksService(world.db))
    world.db.refresh(world.issue)
    assert world.issue.status == "waived"


def test_rejecting_the_fix_for_an_issue_closes_the_issue_and_records_why(world):
    world.issue.severity = "medium"
    world.db.flush()
    edit = world.edit(base=world.v1, proposal=world.version(2), issue=world.issue)
    result = world.reject(edit, comment="Not worth fighting for")
    assert result.status == "rejected"
    world.db.refresh(world.issue)
    assert world.issue.status == "waived"
    [decision] = world.db.scalars(
        select(PlaybookDecision).where(PlaybookDecision.playbook_deviation_id == world.issue.id)
    ).all()
    assert decision.decision == "waived"
    assert "Kept the original wording" in decision.rationale and "Not worth fighting for" in decision.rationale
    assert decision.decided_by_user_id == world.user.id


def test_the_issue_stays_open_while_another_change_for_it_is_undecided(world):
    world.issue.severity = "medium"
    world.db.flush()
    first = world.edit(base=world.v1, proposal=world.version(2), issue=world.issue)
    second = world.edit(base=world.v1, proposal=world.version(3), issue=world.issue)
    world.reject(first)
    world.db.refresh(world.issue)
    assert world.issue.status == "open"
    world.reject(second)
    world.db.refresh(world.issue)
    assert world.issue.status == "waived"


def test_rejecting_a_change_for_an_older_version_leaves_the_issue_open(world):
    old_proposal = world.version(2)
    stale = world.edit(base=world.v1, proposal=old_proposal, issue=world.issue)
    v_new = world.version(3)
    world.contract.current_authoritative_version_id = v_new.id
    world.db.flush()
    assert world.reject(stale).status == "rejected"
    world.db.refresh(world.issue)
    assert world.issue.status == "open"


def test_rejecting_a_chat_suggestion_touches_no_issue(world):
    chat = world.edit(base=world.v1, proposal=world.version(2))
    world.reject(chat)
    world.db.refresh(world.issue)
    assert world.issue.status == "open"


def test_without_permission_to_run_playbooks_the_issue_stays_open(db, monkeypatch):
    user = _user(db, "contract:redline")
    w = World(db, user)
    monkeypatch.setattr(routes, "get_contract_for_user", lambda _db, **kw: w.contract)
    edit = w.edit(base=w.v1, proposal=w.version(2), issue=w.issue)
    assert w.reject(edit).status == "rejected"
    db.refresh(w.issue)
    assert w.issue.status == "open"
