"""Custom role management — CRUD for org-scoped roles + user role assignment.

Closes the biggest RBAC gap: orgs can now define legal-tuned job-function roles
(GC, paralegal, outside counsel, requester, ...) and tune permissions without a
code change + re-seed. Built on the existing Role / Permission / role_permission
/ user_role tables. Built-in roles (admin/member/legal_reviewer/approver) are
protected: they cannot be deleted or renamed, and `admin` cannot be de-scoped.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import Permission, Role, User, UserRoleGrant, user_role_table
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.rbac import ADMIN_ROLE_NAME, ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS
from app.org_structure.access import get_org_root

BUILTIN_ROLE_NAMES = set(DEFAULT_ROLE_PERMISSIONS)


def is_builtin(role: Role) -> bool:
    return role.name in BUILTIN_ROLE_NAMES


# --- permission catalog ---------------------------------------------------

_PERMISSION_DESCRIPTIONS: dict[str, str] = {
    # only a few non-obvious ones; the rest read fine as "<group> <action>"
    "contract:lifecycle_override": "Move a contract between stages, overriding gates",
    "contract:redline": "Propose and manage redlines",
    "assistant:use_ai_tools": "Let the assistant run mutating AI tools",
    "admin_panel:access": "Access org admin settings, including roles",
    "user:update_role": "Assign or change other users' roles",
    "user:approve": "Approve pending user registrations",
    "approval:admin": "Administer approval routing rules and groups",
}


def permission_catalog() -> list[dict]:
    catalog: list[dict] = []
    for value in sorted(ALL_PERMISSIONS):
        group = value.split(":", 1)[0]
        action = value.split(":", 1)[1].replace("_", " ") if ":" in value else value
        catalog.append(
            {
                "value": value,
                "group": group,
                "description": _PERMISSION_DESCRIPTIONS.get(
                    value, f"{group.replace('_', ' ').title()} — {action}"
                ),
            }
        )
    return catalog


def _assert_actor_can_grant(actor: User, roles: list[Role]) -> None:
    """Privilege-escalation guard: `user:update_role` is a narrower permission
    than `admin_panel:access` (role CRUD), so an actor could hold the former
    without the latter. Without this check they could still grant ANY
    role — including admin — to anyone (including themselves), regardless
    of their own permission set. Only allow granting permissions the actor
    already holds themselves."""
    from app.core.rbac import has_permission

    granted_permissions = {p.value for r in roles for p in r.permissions}
    actor_permissions = actor.permission_values
    ungranted = {p for p in granted_permissions if not has_permission(actor_permissions, p)}
    if ungranted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Cannot assign permission(s) you do not hold yourself: {', '.join(sorted(ungranted))}",
        )


class RoleService:
    """Org-scoped role CRUD and user role/clearance assignment.

    Part of the DI migration (see backend/DI_MIGRATION.md). Constructed with
    a ``db`` session; every function that took ``db`` first is now a method
    reading ``self.db``. ``is_builtin``, ``permission_catalog``, and
    ``_assert_actor_can_grant`` stay module-level — none of them touch ``db``.
    """

    def __init__(self, db: Session):
        self.db = db

    def _resolve_permissions(self, values: list[str]) -> list[Permission]:
        """Map permission value strings to Permission rows, creating any that are
        missing (all values are validated against ALL_PERMISSIONS first)."""
        db = self.db
        unknown = [v for v in values if v not in ALL_PERMISSIONS]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Unknown permission(s): {', '.join(sorted(unknown))}",
            )
        wanted = list(dict.fromkeys(values))  # de-dup, preserve order
        existing = {
            p.value: p
            for p in db.scalars(select(Permission).where(Permission.value.in_(wanted))).all()
        }
        resolved: list[Permission] = []
        for value in wanted:
            perm = existing.get(value)
            if perm is None:
                perm = Permission(value=value)
                db.add(perm)
                db.flush()
            resolved.append(perm)
        return resolved

    # --- serialization --------------------------------------------------------

    def serialize_role(self, role: Role) -> dict:
        user_count = (
            self.db.scalar(
                select(func.count())
                .select_from(user_role_table)
                .where(
                    user_role_table.c.role_id == role.id,
                    user_role_table.c.deleted_at.is_(None),
                )
            )
            or 0
        )
        return {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "is_builtin": is_builtin(role),
            "permissions": sorted(p.value for p in role.permissions),
            "user_count": int(user_count),
            "allows_hierarchy_rollup": role.allows_hierarchy_rollup,
        }

    def list_roles(self, org_id: str) -> list[dict]:
        roles = self.db.scalars(
            select(Role).where(Role.org_id == org_id).order_by(Role.name)
        ).all()
        return [self.serialize_role(r) for r in roles]

    def _get_org_role(self, org_id: str, role_id: str) -> Role:
        role = self.db.get(Role, role_id)
        if role is None or role.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
        return role

    # --- mutations ------------------------------------------------------------

    def create_role(self, actor: User, payload) -> dict:
        db = self.db
        name = payload.name.strip()
        if not name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Role name is required")
        if name in BUILTIN_ROLE_NAMES:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f'"{name}" is a reserved built-in role name'
            )
        exists = db.scalar(
            select(Role.id).where(Role.org_id == actor.org_id, Role.name == name)
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, f'A role named "{name}" already exists')
        role = Role(
            org_id=actor.org_id,
            name=name,
            description=(payload.description or "").strip() or None,
            permissions=self._resolve_permissions(payload.permissions),
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(role)
        db.flush()
        write_audit_log(
            db,
            action="role.created",
            resource_type="role",
            resource_id=role.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            after={"name": role.name, "permissions": sorted(payload.permissions)},
        )
        db.commit()
        db.refresh(role)
        return self.serialize_role(role)

    def update_role(self, actor: User, role_id: str, payload) -> dict:
        db = self.db
        role = self._get_org_role(actor.org_id, role_id)
        before = {
            "name": role.name,
            "permissions": sorted(p.value for p in role.permissions),
            "allows_hierarchy_rollup": role.allows_hierarchy_rollup,
        }

        if role.name == ADMIN_ROLE_NAME:
            # The admin role is the org's break-glass; never de-scope or rename it.
            raise HTTPException(
                status.HTTP_409_CONFLICT, "The built-in admin role cannot be modified"
            )
        if payload.name is not None and payload.name.strip() != role.name:
            if is_builtin(role):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Built-in roles cannot be renamed"
                )
            new_name = payload.name.strip()
            if not new_name:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Role name is required")
            if new_name in BUILTIN_ROLE_NAMES:
                raise HTTPException(status.HTTP_409_CONFLICT, "Reserved built-in role name")
            clash = db.scalar(
                select(Role.id).where(
                    Role.org_id == actor.org_id, Role.name == new_name, Role.id != role.id
                )
            )
            if clash:
                raise HTTPException(status.HTTP_409_CONFLICT, "A role with that name already exists")
            role.name = new_name
        if payload.description is not None:
            role.description = payload.description.strip() or None
        if payload.permissions is not None:
            role.permissions = self._resolve_permissions(payload.permissions)
        if payload.allows_hierarchy_rollup is not None:
            role.allows_hierarchy_rollup = payload.allows_hierarchy_rollup
        role.updated_by_user_id = actor.id
        db.flush()
        write_audit_log(
            db,
            action="role.updated",
            resource_type="role",
            resource_id=role.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            before=before,
            after={
                "name": role.name,
                "permissions": sorted(p.value for p in role.permissions),
                "allows_hierarchy_rollup": role.allows_hierarchy_rollup,
            },
        )
        db.commit()
        db.refresh(role)
        return self.serialize_role(role)

    def delete_role(self, actor: User, role_id: str) -> None:
        db = self.db
        role = self._get_org_role(actor.org_id, role_id)
        if is_builtin(role):
            raise HTTPException(status.HTTP_409_CONFLICT, "Built-in roles cannot be deleted")
        holders = (
            db.scalar(
                select(func.count())
                .select_from(user_role_table)
                .where(
                    user_role_table.c.role_id == role.id,
                    user_role_table.c.deleted_at.is_(None),
                )
            )
            or 0
        )
        if holders:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Reassign the {holders} user(s) holding this role before deleting it",
            )
        write_audit_log(
            db,
            action="role.deleted",
            resource_type="role",
            resource_id=role.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            before={"name": role.name},
        )
        db.delete(role)
        db.commit()

    def set_user_clearance(self, actor: User, user_id: str, payload) -> dict:
        """Phase 3 (MAC): set a user's confidentiality clearance. Admin-only surface."""
        from app.auth.service import as_user_response

        db = self.db
        target = db.get(User, user_id)
        if target is None or target.org_id != actor.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        before = target.clearance
        target.clearance = payload.clearance
        target.updated_by_user_id = actor.id
        db.flush()
        write_audit_log(
            db,
            action="user.clearance_updated",
            resource_type="user",
            resource_id=target.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            before={"clearance": before},
            after={"clearance": target.clearance},
        )
        db.commit()
        db.refresh(target)
        return as_user_response(target)

    def _assert_actor_can_grant(self, actor: User, roles: list[Role]) -> None:
        """Privilege-escalation guard: `user:update_role` is a narrower permission
        than `admin_panel:access` (role CRUD), so an actor could hold the former
        without the latter. Without this check they could still grant ANY
        role — including admin — to anyone (including themselves), regardless
        of their own permission set. Only allow granting permissions the actor
        already holds themselves.

        FR-9: resolves the actor's own permissions via
        ``org_access.effective_permission_values`` (expiry-aware — independently
        excludes an expired-but-not-revoked grant) rather than
        ``actor.permission_values``/``User.roles``, which per this feature's
        design does not filter ``valid_to`` expiry."""
        from app.core.org_access import effective_permission_values
        from app.core.rbac import has_permission

        granted_permissions = {p.value for r in roles for p in r.permissions}
        actor_permissions = effective_permission_values(self.db, user=actor)
        ungranted = {p for p in granted_permissions if not has_permission(actor_permissions, p)}
        if ungranted:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Cannot assign permission(s) you do not hold yourself: {', '.join(sorted(ungranted))}",
            )


    def set_user_roles(self, actor: User, user_id: str, payload) -> dict:
        """Legacy flat role-assignment screen: no UI concept of org-unit scoping,
        so every grant it writes/revokes targets the organization's root org unit
        (preserving today's org-wide semantics). Persists via ``UserRoleGrant``
        rows rather than ``target.roles`` (now viewonly — see app/auth/models.py)
        by diffing against the user's currently ACTIVE (non-revoked) grants: only
        roles actually being added/removed get a new/soft-deleted grant row, so
        unchanged roles don't needlessly churn the audit trail."""
        from app.auth.service import as_user_response  # reuse the canonical UserResponse

        db = self.db
        target = db.get(User, user_id)
        if target is None or target.org_id != actor.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        role_ids = list(dict.fromkeys(payload.role_ids))
        roles = db.scalars(
            select(Role).where(Role.org_id == actor.org_id, Role.id.in_(role_ids))
        ).all()
        if len(roles) != len(role_ids):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "One or more roles not found")

        self._assert_actor_can_grant(actor, roles)

        active_grants = db.scalars(
            select(UserRoleGrant).where(
                UserRoleGrant.org_id == actor.org_id,
                UserRoleGrant.user_id == target.id,
                UserRoleGrant.deleted_at.is_(None),
            )
        ).all()
        current_role_ids = {g.role_id for g in active_grants}
        current_roles_by_name = {g.role.name for g in active_grants if g.role is not None}

        # Lockout guard: never remove the last admin's admin role.
        had_admin = ADMIN_ROLE_NAME in current_roles_by_name
        keeps_admin = any(r.name == ADMIN_ROLE_NAME for r in roles)
        if had_admin and not keeps_admin:
            admin_role_ids = select(Role.id).where(
                Role.org_id == actor.org_id, Role.name == ADMIN_ROLE_NAME
            )
            other_admins = db.scalar(
                select(func.count(func.distinct(user_role_table.c.user_id))).where(
                    user_role_table.c.role_id.in_(admin_role_ids),
                    user_role_table.c.user_id != target.id,
                    user_role_table.c.deleted_at.is_(None),
                )
            )
            if not other_admins:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Cannot remove the admin role from the last remaining admin",
                )

        before_roles = sorted(current_roles_by_name)

        wanted_ids = {r.id for r in roles}
        to_add = [r for r in roles if r.id not in current_role_ids]
        to_remove = [g for g in active_grants if g.role_id not in wanted_ids]

        if to_add or to_remove:
            root = get_org_root(db, actor.org_id)
            now = utcnow()
            for role in to_add:
                db.add(
                    UserRoleGrant(
                        org_id=actor.org_id,
                        user_id=target.id,
                        role_id=role.id,
                        org_unit_id=root.id,
                        created_by_user_id=actor.id,
                        updated_by_user_id=actor.id,
                    )
                )
            for grant in to_remove:
                grant.deleted_at = now
                grant.deleted_by_user_id = actor.id
                grant.updated_by_user_id = actor.id

        # Resolve active_role_id: keep it valid, else pick a sensible default.
        valid_ids = wanted_ids
        if payload.active_role_id and payload.active_role_id in valid_ids:
            target.active_role_id = payload.active_role_id
        elif target.active_role_id not in valid_ids:
            target.active_role_id = roles[0].id if roles else None

        target.updated_by_user_id = actor.id
        db.flush()
        write_audit_log(
            db,
            action="user.roles_updated",
            resource_type="user",
            resource_id=target.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            before={"roles": before_roles},
            after={"roles": sorted(r.name for r in roles), "active_role_id": target.active_role_id},
        )
        db.commit()
        db.refresh(target)
        return as_user_response(target)
