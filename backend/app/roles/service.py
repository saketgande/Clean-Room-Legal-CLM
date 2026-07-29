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

from app.auth.models import Permission, Role, User, user_role_table
from app.core.audit import write_audit_log
from app.core.rbac import ADMIN_ROLE_NAME, ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS

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


def _resolve_permissions(db: Session, values: list[str]) -> list[Permission]:
    """Map permission value strings to Permission rows, creating any that are
    missing (all values are validated against ALL_PERMISSIONS first)."""
    unknown = [v for v in values if v not in ALL_PERMISSIONS]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
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

def serialize_role(db: Session, role: Role) -> dict:
    user_count = (
        db.scalar(
            select(func.count())
            .select_from(user_role_table)
            .where(user_role_table.c.role_id == role.id)
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
    }


def list_roles(db: Session, org_id: str) -> list[dict]:
    roles = db.scalars(
        select(Role).where(Role.org_id == org_id).order_by(Role.name)
    ).all()
    return [serialize_role(db, r) for r in roles]


def _get_org_role(db: Session, org_id: str, role_id: str) -> Role:
    role = db.get(Role, role_id)
    if role is None or role.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    return role


# --- mutations ------------------------------------------------------------

def create_role(db: Session, actor: User, payload) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Role name is required")
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
        permissions=_resolve_permissions(db, payload.permissions),
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
    return serialize_role(db, role)


def update_role(db: Session, actor: User, role_id: str, payload) -> dict:
    role = _get_org_role(db, actor.org_id, role_id)
    before = {"name": role.name, "permissions": sorted(p.value for p in role.permissions)}

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
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Role name is required")
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
        role.permissions = _resolve_permissions(db, payload.permissions)
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
        after={"name": role.name, "permissions": sorted(p.value for p in role.permissions)},
    )
    db.commit()
    db.refresh(role)
    return serialize_role(db, role)


def delete_role(db: Session, actor: User, role_id: str) -> None:
    role = _get_org_role(db, actor.org_id, role_id)
    if is_builtin(role):
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in roles cannot be deleted")
    holders = (
        db.scalar(
            select(func.count())
            .select_from(user_role_table)
            .where(user_role_table.c.role_id == role.id)
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


def set_user_clearance(db: Session, actor: User, user_id: str, payload) -> dict:
    """Phase 3 (MAC): set a user's confidentiality clearance. Admin-only surface."""
    from app.auth.service import as_user_response

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


def set_user_roles(db: Session, actor: User, user_id: str, payload) -> dict:
    from app.auth.service import as_user_response  # reuse the canonical UserResponse

    target = db.get(User, user_id)
    if target is None or target.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    role_ids = list(dict.fromkeys(payload.role_ids))
    roles = db.scalars(
        select(Role).where(Role.org_id == actor.org_id, Role.id.in_(role_ids))
    ).all()
    if len(roles) != len(role_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "One or more roles not found")

    _assert_actor_can_grant(actor, roles)

    # Lockout guard: never remove the last admin's admin role.
    had_admin = any(r.name == ADMIN_ROLE_NAME for r in target.roles)
    keeps_admin = any(r.name == ADMIN_ROLE_NAME for r in roles)
    if had_admin and not keeps_admin:
        admin_role_ids = select(Role.id).where(
            Role.org_id == actor.org_id, Role.name == ADMIN_ROLE_NAME
        )
        other_admins = db.scalar(
            select(func.count(func.distinct(user_role_table.c.user_id))).where(
                user_role_table.c.role_id.in_(admin_role_ids),
                user_role_table.c.user_id != target.id,
            )
        )
        if not other_admins:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot remove the admin role from the last remaining admin",
            )

    before_roles = sorted(r.name for r in target.roles)
    target.roles = roles

    # Resolve active_role_id: keep it valid, else pick a sensible default.
    valid_ids = {r.id for r in roles}
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
