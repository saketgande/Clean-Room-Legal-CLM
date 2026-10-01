"""The FR-10/FR-12/FR-15/FR-28 cross-cutting integration test (feature
003-menu-screen-security, T019 — the final wave-4 verification task).

This file's whole purpose is the three-way check the security spec calls out
explicitly: for a given user/screen/action combination, (1) the menu-tree
node's ``action_level``, (2) ``GET /screen-access/me``'s resolved level, and
(3) the API's actual accept/reject decision must always agree — a test
checking only one of the three can miss a real divergence (FR-15, AC-6).

This repo's test suite has no ``TestClient``/HTTP harness wired to a live DB
(see ``test_sprint1_criticals.py``'s docstring and every other
``backend/tests/test_*_api.py`` file, all of which call FastAPI dependency
functions and route handlers directly against a real Postgres session rather
than going through Starlette's test client). This file follows the identical,
established convention: for "the API's actual accept/reject behavior" it
calls the EXACT ``Depends(...)`` objects each tranche-1 route declares in its
signature — imported directly from that domain's ``routes.py`` module, never
reconstructed — which is byte-identical to what FastAPI resolves before
invoking the route body for a real HTTP request. A passing dependency chain
is what lets a real request reach the handler (a 2xx path); a raised
``HTTPException(403, ...)`` from either dependency is exactly the 403 an HTTP
client would receive.

Representative route chosen per tranche-1 domain (all PATCH/EDIT, matching
AC-6's own example of "EDIT on Matters"; Matters has since been removed):

    contracts   -> PATCH /contracts/{id}            (require_permission("contract:update"),  _CONTRACTS_EDIT)
    trademarks  -> PATCH /trademarks/{id}            (require_permission("trademark:update"), _TRADEMARKS_EDIT)
    notices     -> PATCH /notices/{id}               (require_permission("notice:update"),    _NOTICES_EDIT)
    intake      -> PATCH /intake/requests/{id}       (require_permission("intake:update"),    _INTAKE_EDIT)

FR/AC coverage: AC-3, AC-5, AC-6, AC-12, AC-17.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.contracts.routes as contracts_routes
import app.intake.routes as intake_routes
import app.notices.routes as notices_routes
import app.trademarks.routes as trademarks_routes
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core import screen_access
from app.core.database import SessionLocal, engine, new_uuid
from app.core.deps import require_permission
from app.core.enums import UserStatus
from app.core.models import AuditLog
from app.menu_security import service
from app.menu_security.models import ActionLevel, RoleScreenAccess, Screen
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization

# ---------------------------------------------------------------------------
# Fixtures / builders (same real-Postgres, one-transaction-per-test style as
# test_screen_access_resolver.py / test_menu_tree_api.py / test_screen_access_grants_api.py)
# ---------------------------------------------------------------------------


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


def _make_org(db: Session, *, name: str) -> Organization:
    org = Organization(id=new_uuid(), name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _make_unit(db: Session, *, org_id: str, name: str, parent_id: str | None) -> OrgUnit:
    unit = OrgUnit(org_id=org_id, name=name, parent_id=parent_id)
    db.add(unit)
    db.flush()
    return unit


def _get_or_create_permission(db: Session, value: str) -> Permission:
    existing = db.query(Permission).filter(Permission.value == value).one_or_none()
    if existing is not None:
        return existing
    perm = Permission(value=value)
    db.add(perm)
    db.flush()
    return perm


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


def _make_role_grant(db: Session, *, user: User, role: Role, org_unit: OrgUnit) -> UserRoleGrant:
    grant = UserRoleGrant(user_id=user.id, role_id=role.id, org_id=user.org_id, org_unit_id=org_unit.id)
    db.add(grant)
    db.flush()
    return grant


def _get_screen(db: Session, code: str) -> Screen:
    """Tranche-1 screens are seeded by the 0043 migration — never created here."""
    return db.query(Screen).filter(Screen.code == code).one()


def _get_action_level(db: Session, code: str) -> ActionLevel:
    return db.query(ActionLevel).filter(ActionLevel.code == code).one()


def _grant_screen_level(
    db: Session, *, org_id: str, role: Role, screen_code: str, level_code: str
) -> RoleScreenAccess:
    screen = _get_screen(db, screen_code)
    level = _get_action_level(db, level_code)
    row = RoleScreenAccess(
        org_id=org_id,
        role_id=role.id,
        screen_id=screen.id,
        org_unit_id=None,
        max_action_level_id=level.id,
    )
    db.add(row)
    db.flush()
    return row


class Org:
    def __init__(self, db: Session) -> None:
        self.org = _make_org(db, name="ScreenEnforcementTest")
        self.root = _make_unit(db, org_id=self.org.id, name="Global", parent_id=None)


@pytest.fixture
def org(db: Session) -> Org:
    return Org(db)


# ---------------------------------------------------------------------------
# The tranche-1 domains under test (matters was removed) — (screen_code, permission, module,
# screen-dependency-attr-name-on-that-module).
# ---------------------------------------------------------------------------

# ``has_menu_node`` is False only for ``trademarks``: per plan.md's screen
# catalog, the trademarks LIST screen (unlike its detail/calendar/etc.
# sub-pages) has no menu_item row today — the current rail simply has no
# "Trademarks" link — so its menu-tree presence cannot be asserted; the
# /screen-access/me + API-enforcement two-way agreement is still checked.
DOMAINS = [
    ("contracts", "contract:update", contracts_routes, "_CONTRACTS_EDIT", True),
    ("trademarks", "trademark:update", trademarks_routes, "_TRADEMARKS_EDIT", False),
    ("notices", "notice:update", notices_routes, "_NOTICES_EDIT", True),
    ("intake", "intake:update", intake_routes, "_INTAKE_EDIT", True),
]


def _three_way_check(
    db: Session,
    org: Org,
    *,
    screen_code: str,
    permission: str,
    routes_module,
    dep_attr: str,
    sufficient: bool,
    has_menu_node: bool = True,
):
    """Builds a user seeded at EDIT (sufficient) or VIEW (insufficient) on
    ``screen_code``, holding ``permission`` throughout, and asserts the
    menu-tree node, the /screen-access/me entry, and the literal route
    dependency chain from ``routes_module`` all agree for that exact
    user/screen/action combination (FR-15, AC-6)."""
    level_code = "EDIT" if sufficient else "VIEW"
    role = _make_role(db, org_id=org.org.id, name=f"{screen_code}-role", permission_values=[permission])
    user = _make_user(db, org_id=org.org.id, label=f"{screen_code}-user")
    _make_role_grant(db, user=user, role=role, org_unit=org.root)
    _grant_screen_level(db, org_id=org.org.id, role=role, screen_code=screen_code, level_code=level_code)

    # Signal 1: the resolver itself (the single source of truth, FR-6).
    resolved = screen_access.resolve_screen_access(db, user=user, screen_code=screen_code)
    assert resolved.level == level_code

    # Signal 2: GET /menu-tree's node for this screen (where one exists).
    tree = service.get_menu_tree(db, actor=user)

    def _find(nodes):
        for node in nodes:
            if node.screen_code == screen_code:
                return node
            found = _find(node.children)
            if found is not None:
                return found
        return None

    menu_node = _find(tree["nodes"])
    if has_menu_node:
        assert menu_node is not None, f"{screen_code} screen_link node missing from menu tree"
        assert menu_node.action_level == level_code
    else:
        assert menu_node is None, f"{screen_code} unexpectedly gained a menu node"

    # Signal 3: GET /screen-access/me for this screen.
    my_access = service.get_my_screen_access(db, actor=user, screen_code=screen_code)
    assert len(my_access["screens"]) == 1
    assert my_access["screens"][0]["action_level"] == level_code

    # Every signal available for this screen agrees exactly (FR-15's
    # single-rule guarantee) — the resolver, /screen-access/me, and (where a
    # menu node exists) the menu tree.
    assert my_access["screens"][0]["action_level"] == resolved.level
    if has_menu_node:
        assert menu_node.action_level == my_access["screens"][0]["action_level"]

    # Signal 4: the ACTUAL API enforcement chokepoint — the literal
    # Depends(...) objects the real route declares, imported from the real
    # routes module (never reconstructed).
    permission_dep = require_permission(permission)
    screen_dep = getattr(routes_module, dep_attr)

    # The caller always holds the plain permission string in this test (FR-12
    # is independent and additive — this isolates the screen-level axis).
    assert permission_dep(current_user=user, db=db) is user

    if sufficient:
        result = screen_dep(current_user=user, db=db)
        assert result.level == "EDIT"
        assert result.rank >= screen_access.LEVEL_RANK["EDIT"]
    else:
        with pytest.raises(HTTPException) as exc_info:
            screen_dep(current_user=user, db=db)
        assert exc_info.value.status_code == 403

    return user, resolved


@pytest.mark.parametrize("screen_code,permission,routes_module,dep_attr,has_menu_node", DOMAINS)
def test_three_way_agreement_when_access_is_sufficient(
    db: Session, org: Org, screen_code, permission, routes_module, dep_attr, has_menu_node
):
    """AC-6: a user with EDIT resolves EDIT on the menu tree, EDIT on
    /screen-access/me, and the route's screen dependency accepts (2xx-eligible)
    — all for the same user/screen/action."""
    _three_way_check(
        db, org,
        screen_code=screen_code, permission=permission,
        routes_module=routes_module, dep_attr=dep_attr,
        sufficient=True, has_menu_node=has_menu_node,
    )


@pytest.mark.parametrize("screen_code,permission,routes_module,dep_attr,has_menu_node", DOMAINS)
def test_three_way_agreement_when_access_is_insufficient(
    db: Session, org: Org, screen_code, permission, routes_module, dep_attr, has_menu_node
):
    """AC-3: a user with VIEW only resolves VIEW on the menu tree, VIEW on
    /screen-access/me, and the route's screen dependency rejects with 403 —
    regardless of the caller holding the plain permission string (FR-13)."""
    _three_way_check(
        db, org,
        screen_code=screen_code, permission=permission,
        routes_module=routes_module, dep_attr=dep_attr,
        sufficient=False, has_menu_node=has_menu_node,
    )


# ---------------------------------------------------------------------------
# AC-5: both gates are independently required (permission-string AND screen
# level) — neither one substitutes for the other (FR-12).
# ---------------------------------------------------------------------------


def test_permission_held_but_screen_level_missing_is_rejected(db: Session, org: Org):
    role = _make_role(db, org_id=org.org.id, name="perm-only", permission_values=["contract:update"])
    user = _make_user(db, org_id=org.org.id, label="perm-only")
    _make_role_grant(db, user=user, role=role, org_unit=org.root)
    # No role_screen_access row at all for "contracts" -> below VIEW.

    assert require_permission("contract:update")(current_user=user, db=db) is user
    with pytest.raises(HTTPException) as exc_info:
        contracts_routes._CONTRACTS_EDIT(current_user=user, db=db)
    assert exc_info.value.status_code == 403


def test_screen_level_held_but_permission_missing_is_rejected(db: Session, org: Org):
    role = _make_role(db, org_id=org.org.id, name="screen-only", permission_values=[])
    user = _make_user(db, org_id=org.org.id, label="screen-only")
    _make_role_grant(db, user=user, role=role, org_unit=org.root)
    _grant_screen_level(db, org_id=org.org.id, role=role, screen_code="contracts", level_code="DELETE")

    # Screen level alone is more than sufficient...
    resolved = contracts_routes._CONTRACTS_EDIT(current_user=user, db=db)
    assert resolved.level == "DELETE"
    # ...but the separate, additive permission-string gate still rejects.
    with pytest.raises(HTTPException) as exc_info:
        require_permission("contract:update")(current_user=user, db=db)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# AC-12 / FR-28: an enforcement denial is recorded on the access-decision
# audit trail, naming the user, screen, attempted action and resolved level.
# ---------------------------------------------------------------------------


def test_denied_screen_level_check_writes_an_access_denied_audit_row(db: Session, org: Org, monkeypatch):
    from app.core import authz

    role = _make_role(db, org_id=org.org.id, name="denied-audit", permission_values=["contract:update"])
    user = _make_user(db, org_id=org.org.id, label="denied-audit")
    _make_role_grant(db, user=user, role=role, org_unit=org.root)
    _grant_screen_level(db, org_id=org.org.id, role=role, screen_code="contracts", level_code="VIEW")

    # record_decision writes off-thread (it must not wait on the request's own
    # audit advisory lock). Keep that, but wait for this denial's write to land.
    writes = []
    real_writer = authz._WRITER
    monkeypatch.setattr(authz, "_WRITER", type("W", (), {
        "submit": staticmethod(lambda *a, **kw: writes.append(real_writer.submit(*a, **kw))),
    })())

    with pytest.raises(HTTPException):
        contracts_routes._CONTRACTS_EDIT(current_user=user, db=db)
    assert writes, "the denial never queued an audit write"
    for write in writes:
        write.result(timeout=10)

    # record_decision writes on its OWN isolated session (SessionLocal), so it
    # survives this test's transaction rollback — read it back on a fresh one.
    audit_session = SessionLocal()
    try:
        row = audit_session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "access.denied",
                AuditLog.actor_user_id == user.id,
                AuditLog.resource_type == "screen",
            )
            .order_by(AuditLog.created_at.desc())
        )
        assert row is not None, "no access.denied audit row was written for the denied screen check"
        assert row.org_id == user.org_id
        assert row.metadata_json.get("outcome") == "denied"
        assert row.metadata_json.get("permission") == "screen:contracts:EDIT"
        # "reason" carries the resolver's reason code for the CURRENT resolved
        # state ("granted" because a lower VIEW grant does exist) — this is the
        # resolved-level evidence FR-28/AC-12 requires the trail to carry.
        assert row.metadata_json.get("reason") == "granted"
    finally:
        audit_session.close()


# ---------------------------------------------------------------------------
# AC-17: a non-tranche-1 screen (renewals) is NOT independently API-enforced
# in this feature, while its screen record still resolves for menu purposes.
# ---------------------------------------------------------------------------


def test_non_tranche_screen_has_no_screen_level_dependency_but_still_resolves(db: Session, org: Org):
    import ast
    import inspect

    import app.renewals.routes as renewals_routes

    source = inspect.getsource(renewals_routes)
    assert "require_screen_level" not in source, (
        "renewals is explicitly out of the FR-17 tranche-1 retrofit; it must not "
        "gain a require_screen_level dependency in this feature"
    )
    # Confirm this isn't just a lazy no-op — no such name is even referenced.
    tree_ast = ast.parse(source)
    names = {node.id for node in ast.walk(tree_ast) if isinstance(node, ast.Name)}
    assert "require_screen_level" not in names

    # The renewals screen itself still exists and still resolves for menu
    # visibility (FR-16), independent of API-layer enforcement being deferred.
    role = _make_role(db, org_id=org.org.id, name="renewals-viewer", permission_values=["contract:read"])
    user = _make_user(db, org_id=org.org.id, label="renewals-viewer")
    _make_role_grant(db, user=user, role=role, org_unit=org.root)
    _grant_screen_level(db, org_id=org.org.id, role=role, screen_code="renewals", level_code="VIEW")

    resolved = screen_access.resolve_screen_access(db, user=user, screen_code="renewals")
    assert resolved.level == "VIEW"
    my_access = service.get_my_screen_access(db, actor=user, screen_code="renewals")
    assert my_access["screens"][0]["action_level"] == "VIEW"


# ---------------------------------------------------------------------------
# FR-7/FR-8 pruning cross-check: a user below VIEW never sees the screen's
# menu node, and /screen-access/me reports no entry for it either — the two
# signals must agree on absence, not just on presence.
# ---------------------------------------------------------------------------


def test_user_below_view_sees_no_menu_node_and_no_my_access_entry(db: Session, org: Org):
    role = _make_role(db, org_id=org.org.id, name="no-access", permission_values=["notice:update"])
    user = _make_user(db, org_id=org.org.id, label="no-access")
    _make_role_grant(db, user=user, role=role, org_unit=org.root)
    # Deliberately no role_screen_access row at all for "notices".

    resolved = screen_access.resolve_screen_access(db, user=user, screen_code="notices")
    assert resolved.level is None
    assert resolved.reason == "no_grant"

    tree = service.get_menu_tree(db, actor=user)

    def _contains(nodes, code):
        for node in nodes:
            if node.screen_code == code:
                return True
            if _contains(node.children, code):
                return True
        return False

    assert _contains(tree["nodes"], "notices") is False, (
        "a screen_link node must be PRUNED (absent), not merely marked with a "
        "null action_level, when the user is below VIEW"
    )

    my_access = service.get_my_screen_access(db, actor=user, screen_code="notices")
    assert my_access["screens"] == []

    # And the underlying API enforcement chokepoint independently agrees: an
    # ADD/EDIT/DELETE attempt on this screen is rejected too.
    with pytest.raises(HTTPException) as exc_info:
        notices_routes._NOTICES_EDIT(current_user=user, db=db)
    assert exc_info.value.status_code == 403
