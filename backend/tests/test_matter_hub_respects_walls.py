"""Ethical walls and clearance must hold on the matter hub, not only when a
contract is opened directly.

Before the fix: the matter overview and the unfiled tray listed contracts with no
access filter; any contract or request could be filed under a matter without an
access check; and a project-scoped wall missed contracts filed the canonical way
(Contract.matter_id, with no MatterContract row).

The database tests build their rows inside one transaction and roll it back, so
nothing is left behind. They skip when no database is reachable.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.ai import tool_runtime as tr
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.core.database import SessionLocal
from app.matters import routes as matters
from app.matters.models import Matter
from app.walls.models import EthicalWall, EthicalWallPrincipal
from app.walls.service import user_is_walled

MEMBER = SimpleNamespace(id="u-1", org_id="org-1", roles=[], permission_values=set())
ADMIN = {"admin_panel:access"}


def _not_found(*_args, **_kwargs):
    raise HTTPException(404, "not found")


# --- no database needed -------------------------------------------------------


@pytest.mark.parametrize(
    ("item_type", "gate"), [("contract", "get_contract_for_user"), ("intake", "get_request")]
)
def test_filing_an_item_under_a_matter_checks_access_to_the_item(monkeypatch, item_type, gate):
    monkeypatch.setattr(matters, "_get_project", lambda db, **kw: SimpleNamespace(id="m-1"))
    monkeypatch.setattr(matters, gate, _not_found)
    payload = SimpleNamespace(item_type=item_type, item_id="item-1")
    with pytest.raises(HTTPException):
        matters.assign_item_to_matter("m-1", payload, db=None, current_user=MEMBER)


def test_the_assistant_matter_contract_list_is_access_filtered(monkeypatch):
    statements = []

    class DB:
        def scalars(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: ["c-1"] if len(statements) == 1 else [])

    monkeypatch.setattr(tr, "get_project_for_user", lambda db, **kw: None)
    tr.tool_runtime._list_project_contracts(DB(), payload=SimpleNamespace(matter_id="m-1"), user=MEMBER)
    assert "ethical_wall" in str(statements[-1].compile())


# --- against the database -----------------------------------------------------


def _wall(db, hub, scope_type, scope_id):
    wall = EthicalWall(org_id=hub.org_id, name="hub-test", scope_type=scope_type, scope_id=scope_id, active=True)
    wall.principals = [EthicalWallPrincipal(org_id=hub.org_id, principal_type="user", principal_id=hub.user_id)]
    db.add(wall)
    db.flush()


def _user(hub, permissions):
    return SimpleNamespace(id=hub.user_id, org_id=hub.org_id, roles=[], permission_values=permissions)


@pytest.fixture
def hub():
    db = SessionLocal()
    try:
        owner = db.execute(text('SELECT org_id, id FROM "user" LIMIT 1')).fetchone()
    except OperationalError:
        db.close()
        pytest.skip("needs the dev database")
    if owner is None:
        db.close()
        pytest.skip("no user in the database to own fixture rows")
    tag = uuid.uuid4().hex[:8]
    hub = SimpleNamespace(db=db, org_id=owner[0], user_id=owner[1])
    hub.matter = Matter(org_id=hub.org_id, name=f"hub-test-{tag}", owner_user_id=hub.user_id)
    db.add(hub.matter)
    db.flush()

    def contract(name, **fields):
        row = Contract(org_id=hub.org_id, title=f"hub-test-{name}-{tag}", owner_user_id=hub.user_id, **fields)
        db.add(row)
        return row

    hub.walled = contract("walled", matter_id=hub.matter.id)
    hub.open = contract("open", matter_id=hub.matter.id)
    hub.restricted = contract("restricted", matter_id=hub.matter.id, confidentiality="restricted")
    hub.unfiled_walled = contract("unfiled-walled")
    hub.unfiled_open = contract("unfiled-open")
    db.flush()
    _wall(db, hub, "contract", hub.walled.id)
    _wall(db, hub, "contract", hub.unfiled_walled.id)
    try:
        yield hub
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("permissions", [set(), ADMIN], ids=["member", "admin"])
def test_the_matter_overview_hides_walled_contracts(hub, permissions):
    overview = matters.matter_overview(hub.matter.id, db=hub.db, current_user=_user(hub, permissions))
    shown = {item.id for item in overview.contracts}
    assert hub.walled.id not in shown
    assert hub.open.id in shown
    assert overview.counts.contracts == len(shown)


def test_the_matter_overview_hides_contracts_above_the_users_clearance(hub):
    as_member = matters.matter_overview(hub.matter.id, db=hub.db, current_user=_user(hub, set()))
    as_admin = matters.matter_overview(hub.matter.id, db=hub.db, current_user=_user(hub, ADMIN))
    assert hub.restricted.id not in {item.id for item in as_member.contracts}
    assert hub.restricted.id in {item.id for item in as_admin.contracts}  # admins administer clearance


def test_the_unfiled_tray_hides_walled_contracts(hub):
    items = matters.unfiled_items(item_type="contract", db=hub.db, current_user=_user(hub, ADMIN))
    shown = {item.id for item in items}
    assert hub.unfiled_walled.id not in shown
    assert hub.unfiled_open.id in shown


def test_a_project_wall_covers_a_contract_filed_only_by_matter_id(hub):
    member = _user(hub, set())
    _wall(hub.db, hub, "project", hub.matter.id)
    assert user_is_walled(hub.db, user=member, contract=hub.open)
    visible = hub.db.scalars(
        select(Contract.id).where(Contract.id == hub.open.id, accessible_contract_filter(member))
    ).all()
    assert visible == []
