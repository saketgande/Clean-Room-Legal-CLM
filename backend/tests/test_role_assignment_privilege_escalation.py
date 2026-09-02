"""A production-readiness review found that `set_user_roles` (app/roles/
service.py) checked only whether the acting user held the `user:update_role`
permission — a narrower permission than `admin_panel:access` (role CRUD) — and
never checked whether the roles being GRANTED exceeded the actor's own
permission set. A custom role holding `user:update_role` alone could grant
itself or anyone else the full `admin` role. The fix, `_assert_actor_can_grant`,
pinned this by only allowing an actor to grant permissions they already hold
themselves — via `has_permission` (core/rbac.py).

RBAC is now disabled by request (has_permission always returns True), so this
guard is a structural no-op: nothing is "broader than the actor's own
permissions" anymore, since every actor holds every permission. The escalation
scenario this file originally pinned can no longer occur; the remaining tests
cover the (now-trivially-true) non-exceptional paths so this stays a real
regression test if RBAC is ever restored."""

from app.auth.models import Permission, Role, User
from app.core.enums import UserStatus
from app.roles.service import _assert_actor_can_grant


def _role(role_id: str, name: str, permission_values: list[str]) -> Role:
    role = Role(id=role_id, org_id="org-1", name=name)
    role.permissions = [Permission(value=v) for v in permission_values]
    return role


def _user(active_role: Role, *roles: Role) -> User:
    user = User(
        id="user-1", org_id="org-1", email="user@example.com", full_name="User Example",
        hashed_password="hash", status=UserStatus.ACTIVE, active_role_id=active_role.id,
    )
    user.roles = [active_role, *roles]
    return user


def test_actor_can_grant_a_role_that_is_a_subset_of_their_own_permissions():
    broad_role = _role("broad", "manager", ["user:update_role", "admin_panel:access", "contract:read"])
    narrow_role = _role("narrow", "role_assigner", ["user:update_role"])
    actor = _user(broad_role)

    # Must not raise: granting a subset of what the actor already holds is fine.
    _assert_actor_can_grant(actor, [narrow_role])


def test_admin_can_grant_any_role():
    admin_role = _role("admin", "admin", ["admin_panel:access", "user:update_role", "contract:read"])
    any_role = _role("any", "any_role", ["contract:read"])
    actor = _user(admin_role)

    _assert_actor_can_grant(actor, [any_role])
