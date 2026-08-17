"""Access-control characterization — the migration record and what is still open.

Originally this file pinned the behaviour of the ELEVEN overlapping mechanisms
(Step 0 of the access-control consolidation). Contracts and projects have since
been switched over to the single evaluator in ``app/core/policy.py``, so their
behavioural matrices now live in tests/test_access_policy.py — the one place
that specifies access.

What remains here:

    A. the RBAC switch and its (now much smaller) blast radius
    B. the guards that were always enforced, and still are
    C. clearance/MAC — recorded now that it actually applies
    D/E. structural defects that are STILL OPEN
    F. the migration itself: contracts and projects delegate to the policy

────────────────────────────────────────────────────────────────────────────
THE RBAC SWITCH
────────────────────────────────────────────────────────────────────────────
``app/core/rbac.py`` — ``has_permission()`` — exists in two states:

  * COMMITTED (git, CI, prod): the real membership test.
  * LOCAL DEV (uncommitted edit in the main checkout, which the backend
    container bind-mounts): ``return True`` — "RBAC disabled by request",
    intentional, because the old role model was unworkable.

It used to follow that every user was an org admin, every permission gate was a
no-op, and clearance was unenforced. **That is no longer true for contracts and
projects**: the new policy keys admin off the role NAME, never
``has_permission``, so object access now decides the same way in both states.
The ``rbac`` fixture still runs the relevant tests under both to prove it.

Still open (tagged HOLE #n, from the audit):

    #4 "effective roles" = active-role for RBAC, all-roles for grants/walls/DoA
    #5 confidentiality is ordinary editable metadata
    #2 RESOURCE_TYPES still advertises grants the readers only partly honour
    (the legacy ``is_org_admin`` is now DELETED — see the tests below)

House style follows tests/test_sprint1_criticals.py: models built in memory, a
tiny stub session, no database, no fixtures framework, no new dependencies.
"""

import inspect

import pytest

from app.auth.models import Permission, Role, User
from app.contracts.access import clearance_permits, user_can_access_contract
from app.contracts.models import Contract
from app.core.access import is_admin
from app.core.enums import UserStatus
from app.grants.service import LEVELS, RESOURCE_TYPES, _levels_at_least, user_has_grant
from app.projects.access import project_scope_query, user_can_access_project
from app.projects.models import Project

ORG = "org-1"
OTHER_ORG = "org-2"


def _real_has_permission(user_permissions, required_permission) -> bool:
    """The committed implementation, per rbac.py's own revert instructions."""
    permissions = set(user_permissions)
    return required_permission in permissions or "*" in permissions


@pytest.fixture
def rbac(request, monkeypatch):
    """Run a test with RBAC on or off, to prove the policy is unaffected."""
    if request.param == "on":
        monkeypatch.setattr("app.core.rbac.has_permission", _real_has_permission)
    else:
        monkeypatch.setattr("app.core.rbac.has_permission", lambda *a, **kw: True)
    return request.param


both_modes = pytest.mark.parametrize("rbac", ["on", "off"], indirect=True)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def _role(name: str, permissions: list[str] | None = None, role_id: str | None = None) -> Role:
    role = Role(id=role_id or name, org_id=ORG, name=name)
    role.permissions = [Permission(value=v) for v in (permissions or [])]
    return role


def _user(*roles: Role, user_id="user-1", org_id=ORG, clearance="confidential", active_role_id=None) -> User:
    user = User(
        id=user_id, org_id=org_id, email=f"{user_id}@example.com", full_name="Test User",
        hashed_password="hash", status=UserStatus.ACTIVE, clearance=clearance,
        active_role_id=active_role_id,
    )
    user.roles = list(roles)
    return user


def _member(**kw) -> User:
    return _user(_role("member", ["contract:read"]), **kw)


def _admin(**kw) -> User:
    return _user(_role("admin", ["admin_panel:access"]), **kw)


def _contract(*, org_id=ORG, owner="owner-9", creator="creator-9", confidentiality="internal") -> Contract:
    return Contract(
        id="contract-1", org_id=org_id, title="Test Contract",
        owner_user_id=owner, created_by_user_id=creator, confidentiality=confidentiality,
    )


def _project(*, org_id=ORG, owner="owner-9", creator="creator-9") -> Project:
    return Project(
        id="project-1", org_id=org_id, name="Matter",
        owner_user_id=owner, created_by_user_id=creator,
    )


class _StubDB:
    def scalar(self, *_a, **_kw):
        return None

    def scalars(self, *_a, **_kw):
        class _Empty:
            def all(self_inner):
                return []

        return _Empty()


@pytest.fixture
def policy_layers(monkeypatch):
    """Drive the DB-backed layers of app/core/policy.can()."""

    def _apply(*, walled=False, project_walled=False, has_grant=False,
               project_level=None, contract_project_level=None, pending_approver=False):
        monkeypatch.setattr("app.core.policy.user_is_walled", lambda db, *, user, contract: walled)
        monkeypatch.setattr(
            "app.core.policy.user_is_walled_from_project", lambda db, *, user, project: project_walled
        )
        monkeypatch.setattr(
            "app.core.policy.user_has_grant",
            lambda db, *, user, resource_type, resource_id, min_level="read": has_grant,
        )
        monkeypatch.setattr("app.core.policy._project_level", lambda db, *, user, project: project_level)
        monkeypatch.setattr(
            "app.core.policy._contract_project_level", lambda db, *, user, contract: contract_project_level
        )
        monkeypatch.setattr(
            "app.core.policy._is_pending_approver", lambda db, *, contract, user: pending_approver
        )
        # deny-override logging opens its own session; neutralise it.
        monkeypatch.setattr("app.core.policy._log_deny_override", lambda *a, **kw: None)
        monkeypatch.setattr("app.contracts.access.user_is_walled", lambda db, *, user, contract: walled)

    _apply()
    return _apply


# ==========================================================================
# A. The RBAC switch — and the fact the policy no longer depends on it
# ==========================================================================

def test_the_rbac_switch_no_longer_reaches_admin_status():
    """It used to: ``is_org_admin`` consulted ``has_permission`` first, so with
    RBAC disabled every user was an org admin and every check built on it was a
    no-op. That function is now deleted and nothing routes admin through the
    switch, so the blast radius is gone.
    """
    from app.core.rbac import has_permission

    grants_everything = has_permission(set(), "admin_panel:access")
    assert has_permission({"admin_panel:access"}, "admin_panel:access") is True
    # Whatever the switch says, an ordinary member is NOT an admin.
    assert is_admin(_member()) is False
    assert grants_everything in (True, False)  # signpost: records this env's state


@both_modes
def test_the_new_admin_test_is_immune_to_the_switch(rbac):
    """``is_admin`` keys off the role name, so it answers identically either way."""
    assert is_admin(_member()) is False
    assert is_admin(_admin()) is True
    assert is_admin(_user()) is False


@both_modes
def test_object_access_now_decides_identically_in_both_modes(rbac, policy_layers):
    """The headline improvement: a stranger is denied whether RBAC is on or off.
    Before the migration this row returned True with RBAC off."""
    policy_layers()
    stranger = _member(user_id="nobody")
    assert user_can_access_contract(_StubDB(), contract=_contract(), user=stranger) is False
    assert user_can_access_project(_StubDB(), project=_project(), user=stranger) is False


def test_legacy_is_org_admin_is_deleted_everywhere():
    """The deletion pass: one admin definition, referenced from nowhere.

    Was: six modules still called it, so they treated everyone as an admin while
    RBAC is off. All six now use ``is_admin``.
    """
    import subprocess

    hits = subprocess.run(
        ["grep", "-rn", "is_org_admin", "app/"], capture_output=True, text=True
    ).stdout.splitlines()
    # Only prose in comments/docstrings may mention the old name.
    code_hits = [h for h in hits if "#" not in h.split(":", 2)[-1][:4] and "``" not in h]
    assert code_hits == [], code_hits


# ==========================================================================
# B. Enforced in both modes: org isolation, ethical walls, DoA
# ==========================================================================

@both_modes
def test_org_isolation_holds(rbac, policy_layers):
    policy_layers()
    outsider = _admin(org_id=OTHER_ORG)
    assert user_can_access_contract(_StubDB(), contract=_contract(), user=outsider) is False
    assert user_can_access_project(_StubDB(), project=_project(), user=outsider) is False


@both_modes
def test_ethical_wall_beats_admin_owner_and_grant(rbac, policy_layers):
    policy_layers(walled=True, project_walled=True, has_grant=True)
    for user in (_admin(), _member(user_id="owner-9")):
        assert user_can_access_contract(_StubDB(), contract=_contract(owner="owner-9"), user=user) is False
        assert user_can_access_project(_StubDB(), project=_project(owner="owner-9"), user=user) is False


def test_authority_has_no_admin_shortcut():
    import app.authority.service as authority

    assert "is_org_admin" not in inspect.getsource(authority)
    assert "is_admin" not in inspect.getsource(authority)


# ==========================================================================
# C. Clearance / MAC — now actually enforced
# ==========================================================================

CLEARANCE_GRID = [
    ("public",       "public",       True),
    ("public",       "internal",     False),
    ("public",       "confidential", False),
    ("public",       "restricted",   False),
    ("internal",     "public",       True),
    ("internal",     "internal",     True),
    ("internal",     "confidential", False),
    ("internal",     "restricted",   False),
    ("confidential", "public",       True),
    ("confidential", "internal",     True),
    ("confidential", "confidential", True),
    ("confidential", "restricted",   False),
    ("restricted",   "public",       True),
    ("restricted",   "internal",     True),
    ("restricted",   "confidential", True),
    ("restricted",   "restricted",   True),
]


@both_modes
@pytest.mark.parametrize("user_clearance,classification,allowed", CLEARANCE_GRID)
def test_clearance_grid(rbac, user_clearance, classification, allowed):
    """All 16 cells, identical in both RBAC modes.

    Six of these returned True regardless of clearance before the migration,
    because the admin bypass fired for everyone. They are the protection that
    switching clearance_permits to is_admin bought back.
    """
    user = _member(clearance=user_clearance)
    assert clearance_permits(user, _contract(confidentiality=classification)) is allowed


@both_modes
def test_real_admins_still_bypass_clearance_by_design(rbac):
    assert clearance_permits(_admin(clearance="public"), _contract(confidentiality="restricted")) is True


def test_clearance_ladder_internals():
    from app.core.policy import CLEARANCE_LEVELS, _clearance_rank, user_clearance_rank

    assert CLEARANCE_LEVELS == ["public", "internal", "confidential", "restricted"]
    assert _clearance_rank(None, "internal") == 1
    assert user_clearance_rank(_member(clearance=None)) == 2


def test_confidentiality_is_still_ordinary_editable_metadata():
    """HOLE #5 — STILL OPEN. The value MAC trusts remains a normal PATCHable
    field, so anyone who can edit a contract can reclassify it down to 'public'.

    Now that clearance is genuinely enforced, this is the next thing to close.
    """
    from app.contracts.schemas import ContractUpdate

    assert "confidentiality" in ContractUpdate.model_fields


# ==========================================================================
# D. Effective roles — HOLE #4, still open
# ==========================================================================

def test_permission_values_uses_only_the_active_role():
    member = _role("member", ["contract:read"], role_id="r-member")
    approver = _role("approver", ["approval:decide"], role_id="r-approver")
    assert _user(member, approver, active_role_id="r-member").permission_values == {"contract:read"}


def test_grant_and_authority_principals_still_ignore_the_active_role():
    """HOLE #4 — STILL OPEN. grants/walls/authority match every role the user
    holds; permission_values respects the active one. Two answers to "which
    roles count", so switching to a low role sheds verbs but not grants.
    """
    from app.authority.service import _principal_clause as authority_clause
    from app.grants.service import _principal_clause as grants_clause

    for clause in (grants_clause, authority_clause):
        src = inspect.getsource(clause)
        assert 'getattr(user, "roles", [])' in src
        assert "active_role_id" not in src


# ==========================================================================
# E. The level ladder — HOLE #1 closed at the write path
# ==========================================================================

def test_level_ladder_order():
    assert LEVELS == ["read", "comment", "update", "share", "owner"]


@pytest.mark.parametrize(
    "min_level,expected",
    [
        ("read", ["read", "comment", "update", "share", "owner"]),
        ("update", ["update", "share", "owner"]),
        ("owner", ["owner"]),
        ("nonsense", ["read", "comment", "update", "share", "owner"]),
    ],
)
def test_levels_at_least(min_level, expected):
    """Note the last row: an unknown level still silently widens to EVERYTHING."""
    assert _levels_at_least(min_level) == expected


def test_grant_lookup_still_defaults_to_the_lowest_level():
    """Unchanged by design — the DEFAULT is 'read'; what matters is that callers
    now pass the level the operation needs (see below)."""
    assert inspect.signature(user_has_grant).parameters["min_level"].default == "read"


def test_access_checks_now_take_a_level():
    """HOLE #1 CLOSED (structurally). Both row checks can express "needs update";
    previously the read gate was reused verbatim to guard writes."""
    assert inspect.signature(user_can_access_contract).parameters["access"].default == "read"
    assert inspect.signature(user_can_access_project).parameters["access"].default == "read"

    from app.contracts.service import get_contract_for_user

    assert inspect.signature(get_contract_for_user).parameters["access"].default == "read"


def test_contract_write_routes_request_update_level():
    """HOLE #1 CLOSED (in practice) — every route gated by contract:update now
    fetches the row at 'update', so a read-only grant no longer passes it."""
    import re

    import app.contracts.routes as routes

    src = inspect.getsource(routes)
    for block in re.split(r"(?=@router\.)", src):
        if 'require_permission("contract:update")' in block and "get_contract_for_user" in block:
            name = re.search(r"def (\w+)\(", block).group(1)
            assert 'access="update"' in block, f"{name} still fetches at read level"


def test_grants_resource_types():
    """HOLE #2 — project and playbook grants are now READ by the policy, so they
    finally grant something. RESOURCE_TYPES is therefore honest again."""
    assert RESOURCE_TYPES == {"contract", "project", "playbook"}

    import app.core.policy as policy

    assert "user_has_grant" in inspect.getsource(policy)


# ==========================================================================
# F. The migration itself
# ==========================================================================

def test_contracts_delegate_to_the_one_policy():
    src = inspect.getsource(user_can_access_contract)
    assert "can(db, user=user, resource=contract, level=access)" in src
    # ...and the list view delegates too, rather than carrying its own SQL.
    from app.contracts.access import accessible_contract_filter

    assert 'accessible_filter(user, "contract")' in inspect.getsource(accessible_contract_filter)


def test_projects_delegate_to_the_one_policy():
    src = inspect.getsource(user_can_access_project)
    assert "can(db, user=user, resource=project, level=access)" in src
    assert 'accessible_filter(user, "project")' in inspect.getsource(project_scope_query)


def test_list_queries_stay_in_lockstep_with_the_row_checks():
    """Both list filters and both row checks are now built from ONE function, so
    they cannot drift. The deletion pass removed the duplicate SQL that used to
    live in contracts/access.py and projects/access.py."""
    from app.core.policy import accessible_filter

    src = inspect.getsource(accessible_filter)
    # walls applied before the admin early-return, for both resource types
    assert src.index("wall_block_filter(user)") < src.index("if is_admin(user)")
    assert src.index("wall_block_filter_project(user)") < src.rindex("if is_admin(user)")
    assert "is_org_admin" not in src


def test_project_list_grants_are_honoured():
    from app.core.policy import accessible_filter

    assert 'granted_resource_ids(user, "project")' in inspect.getsource(accessible_filter)


@both_modes
def test_deny_overrides_still_beat_the_allow_layers_end_to_end(rbac, policy_layers):
    """A grant never rescues a walled or under-cleared user."""
    policy_layers(has_grant=True, walled=True)
    assert user_can_access_contract(_StubDB(), contract=_contract(), user=_member(user_id="x")) is False

    policy_layers(has_grant=True)
    low = _member(user_id="x", clearance="internal")
    assert user_can_access_contract(_StubDB(), contract=_contract(confidentiality="restricted"), user=low) is False
