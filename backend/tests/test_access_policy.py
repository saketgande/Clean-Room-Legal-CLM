"""The new unified access policy — app/core/policy.py.

Companion to tests/test_authz_characterization.py. That file records what the
OLD eleven mechanisms do; this one specifies what the ONE replacement does, and
proves it closes the audit's holes:

    HOLE #1  levels are enforced — a read grant no longer satisfies an update
    HOLE #2  project and playbook grants actually grant something
    HOLE #6  one definition of admin, immune to the disabled RBAC switch
    HOLE #7  ethical walls now cover projects, not just contracts

Nothing in the app calls this module yet, so none of these behaviours are live.
Wiring the call sites is the next step; these tests are the proof it is safe to.

Same house style: in-memory models, a tiny stub session, no database.
"""

import pytest

from app.auth.models import Permission, Role, User
from app.contracts.models import Contract
from app.core.enums import UserStatus
from app.core.policy import LEVELS, can, is_admin, kind_of
from app.playbooks.models import Playbook
from app.projects.models import Project

ORG = "org-1"
OTHER_ORG = "org-2"


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def _role(name: str, permissions: list[str] | None = None) -> Role:
    role = Role(id=name, org_id=ORG, name=name)
    role.permissions = [Permission(value=v) for v in (permissions or [])]
    return role


def _user(*roles: Role, user_id: str = "user-1", org_id: str = ORG, clearance: str = "confidential") -> User:
    user = User(
        id=user_id,
        org_id=org_id,
        email=f"{user_id}@example.com",
        full_name="Test User",
        hashed_password="hash",
        status=UserStatus.ACTIVE,
        clearance=clearance,
    )
    user.roles = list(roles)
    return user


def _member(**kw) -> User:
    return _user(_role("member", ["contract:read"]), **kw)


def _admin_user(**kw) -> User:
    return _user(_role("admin"), **kw)


def _contract(*, org_id=ORG, owner="owner-9", creator="creator-9", confidentiality="internal") -> Contract:
    return Contract(
        id="contract-1",
        org_id=org_id,
        title="Test Contract",
        owner_user_id=owner,
        created_by_user_id=creator,
        confidentiality=confidentiality,
    )


def _project(*, org_id=ORG, owner="owner-9", creator="creator-9") -> Project:
    return Project(
        id="project-1", org_id=org_id, name="Matter",
        owner_user_id=owner, created_by_user_id=creator,
    )


def _playbook(*, org_id=ORG, creator="creator-9") -> Playbook:
    return Playbook(id="playbook-1", org_id=org_id, name="NDA Playbook", created_by_user_id=creator)


class _StubDB:
    def scalar(self, *_a, **_kw):
        return None

    def scalars(self, *_a, **_kw):
        class _Empty:
            def all(self_inner):
                return []

        return _Empty()


@pytest.fixture(autouse=True)
def layers(monkeypatch):
    """All DB-backed layers off by default; each test switches on what it needs."""

    def _apply(*, walled=False, project_walled=False, grant_level=None,
               project_level=None, contract_project_level=None, pending_approver=False):
        from app.core.policy import _at_least

        monkeypatch.setattr("app.core.policy.user_is_walled", lambda db, *, user, contract: walled)
        monkeypatch.setattr(
            "app.core.policy.user_is_walled_from_project", lambda db, *, user, project: project_walled
        )
        monkeypatch.setattr(
            "app.core.policy.user_has_grant",
            lambda db, *, user, resource_type, resource_id, min_level="read": _at_least(
                grant_level, min_level
            ),
        )
        monkeypatch.setattr(
            "app.core.policy._project_level", lambda db, *, user, project: project_level
        )
        monkeypatch.setattr(
            "app.core.policy._contract_project_level",
            lambda db, *, user, contract: contract_project_level,
        )
        monkeypatch.setattr(
            "app.core.policy._is_pending_approver", lambda db, *, contract, user: pending_approver
        )

    _apply()
    return _apply


# ==========================================================================
# Shape
# ==========================================================================

def test_one_ladder_for_every_resource_type():
    assert LEVELS == ["read", "comment", "update", "share", "owner"]


def test_kind_detection():
    assert kind_of(_contract()) == "contract"
    assert kind_of(_project()) == "project"
    assert kind_of(_playbook()) == "playbook"


def test_unknown_resource_is_a_loud_error_not_a_silent_allow():
    with pytest.raises(TypeError):
        kind_of(object())


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError):
        can(_StubDB(), user=_member(), resource=_contract(), level="superuser")


# ==========================================================================
# 1. Tenant boundary
# ==========================================================================

@pytest.mark.parametrize("resource", [_contract(), _project(), _playbook()])
def test_other_org_is_always_denied(resource):
    """Not even an admin crosses the tenant boundary."""
    assert can(_StubDB(), user=_admin_user(org_id=OTHER_ORG), resource=resource) is False


# ==========================================================================
# 2. Deny-overrides beat every allow
# ==========================================================================

def test_contract_wall_beats_admin_owner_and_grant(layers):
    layers(walled=True, grant_level="owner")
    for user in (_admin_user(), _member(user_id="owner-9"), _member(user_id="nobody")):
        assert can(_StubDB(), user=user, resource=_contract(owner="owner-9")) is False


def test_project_wall_now_blocks_the_project_itself(layers):
    """HOLE #7 — this is the leak the old code had: the same wall blocked the
    matter's contracts but never the matter."""
    layers(project_walled=True, grant_level="owner")
    for user in (_admin_user(), _member(user_id="owner-9")):
        assert can(_StubDB(), user=user, resource=_project(owner="owner-9")) is False


def test_clearance_blocks_the_owner_of_a_restricted_contract(layers):
    layers()
    owner = _member(user_id="owner-9", clearance="internal")
    assert can(_StubDB(), user=owner, resource=_contract(owner="owner-9", confidentiality="restricted")) is False


def test_admin_still_bypasses_clearance_by_design(layers):
    layers()
    assert can(_StubDB(), user=_admin_user(clearance="public"),
               resource=_contract(confidentiality="restricted")) is True


# ==========================================================================
# 3. One definition of admin, immune to the RBAC switch
# ==========================================================================

def test_admin_is_the_named_role_only():
    """HOLE #6 — one definition. And crucially it does NOT consult
    has_permission, which is currently disabled and would make everyone admin."""
    assert is_admin(_admin_user()) is True
    assert is_admin(_member()) is False
    assert is_admin(_user()) is False


def test_policy_does_not_depend_on_the_disabled_rbac_switch(monkeypatch):
    """Even with has_permission forced wide open, an ordinary member is denied."""
    monkeypatch.setattr("app.core.rbac.has_permission", lambda *a, **kw: True)
    assert can(_StubDB(), user=_member(user_id="nobody"), resource=_contract()) is False


# ==========================================================================
# 4. Levels are actually enforced  (HOLE #1)
# ==========================================================================

@pytest.mark.parametrize(
    "grant_level,requested,expected",
    [
        ("read", "read", True),
        ("read", "comment", False),
        ("read", "update", False),   # the exploit: read-only share editing a contract
        ("read", "share", False),
        ("read", "owner", False),
        ("update", "read", True),
        ("update", "update", True),
        ("update", "share", False),
        ("owner", "owner", True),
        (None, "read", False),
    ],
)
def test_grant_levels_are_enforced(layers, grant_level, requested, expected):
    """HOLE #1 — the ladder is no longer decorative. A read grant satisfies read
    and nothing above it."""
    layers(grant_level=grant_level)
    assert can(_StubDB(), user=_member(user_id="nobody"), resource=_contract(), level=requested) is expected


def test_project_membership_maps_onto_the_one_ladder(layers):
    """editor -> update: the old ProjectMember vocabulary, folded in."""
    layers(project_level="update")
    user = _member(user_id="nobody")
    assert can(_StubDB(), user=user, resource=_project(), level="read") is True
    assert can(_StubDB(), user=user, resource=_project(), level="update") is True
    assert can(_StubDB(), user=user, resource=_project(), level="share") is False


def test_contract_inherits_its_project_level(layers):
    layers(contract_project_level="read")
    user = _member(user_id="nobody")
    assert can(_StubDB(), user=user, resource=_contract(), level="read") is True
    assert can(_StubDB(), user=user, resource=_contract(), level="update") is False


# ==========================================================================
# 5. Grants work for every resource type  (HOLE #2)
# ==========================================================================

@pytest.mark.parametrize("resource", [_contract(), _project(), _playbook()])
def test_grants_now_apply_to_projects_and_playbooks_too(layers, resource):
    """HOLE #2 — granting a project or playbook used to succeed and grant
    nothing at all. One evaluator reads grants for every type."""
    layers(grant_level="update")
    user = _member(user_id="nobody")
    assert can(_StubDB(), user=user, resource=resource, level="update") is True


# ==========================================================================
# 6. Ownership, creator, approver
# ==========================================================================

def test_owner_and_creator_are_allowed(layers):
    layers()
    assert can(_StubDB(), user=_member(user_id="owner-9"), resource=_contract(owner="owner-9")) is True
    assert can(_StubDB(), user=_member(user_id="creator-9"), resource=_contract()) is True
    assert can(_StubDB(), user=_member(user_id="creator-9"), resource=_playbook()) is True


def test_stranger_is_denied(layers):
    layers()
    for resource in (_contract(), _project(), _playbook()):
        assert can(_StubDB(), user=_member(user_id="nobody"), resource=resource) is False


def test_pending_approver_gets_read_only(layers):
    """An approver may read what they must decide on — and nothing more."""
    layers(pending_approver=True)
    approver = _member(user_id="nobody")
    assert can(_StubDB(), user=approver, resource=_contract(), level="read") is True
    assert can(_StubDB(), user=approver, resource=_contract(), level="update") is False


def test_pending_approver_read_is_still_beaten_by_a_wall(layers):
    layers(pending_approver=True, walled=True)
    assert can(_StubDB(), user=_member(user_id="nobody"), resource=_contract(), level="read") is False
