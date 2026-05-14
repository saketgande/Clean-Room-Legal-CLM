from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import OrgJoinRequest, Permission, Role, User, UserApprovalDecision
from app.auth.schemas import RegisterRequest, SetupAdminRequest
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.enums import UserStatus
from app.core.rbac import ADMIN_ROLE_NAME, DEFAULT_ROLE_PERMISSIONS, ALL_PERMISSIONS
from app.core.security import create_access_token, hash_password, verify_password
from app.organizations.models import Organization


def _normalize_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def _user_response(user: User) -> dict:
    return {
        "id": user.id,
        "org_id": user.org_id,
        "email": user.email,
        "full_name": user.full_name,
        "status": user.status,
        "roles": [role.name for role in user.roles],
    }


def bootstrap_roles(db: Session, org_id: str, actor_user_id: str | None = None) -> dict[str, Role]:
    existing_permissions = {
        permission.value: permission for permission in db.scalars(select(Permission)).all()
    }
    for value in ALL_PERMISSIONS:
        if value not in existing_permissions:
            permission = Permission(value=value)
            db.add(permission)
            existing_permissions[value] = permission

    roles: dict[str, Role] = {}
    for role_name, permission_values in DEFAULT_ROLE_PERMISSIONS.items():
        role = db.scalar(select(Role).where(Role.org_id == org_id, Role.name == role_name))
        if role is None:
            role = Role(
                org_id=org_id,
                name=role_name,
                description=f"Default {role_name} role",
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            db.add(role)
        role.permissions = [existing_permissions[value] for value in sorted(permission_values)]
        roles[role_name] = role
    return roles


def create_first_admin(db: Session, payload: SetupAdminRequest, request_id: str | None = None) -> User:
    if payload.setup_token != settings.setup_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid setup token")

    existing_org = db.scalar(select(Organization))
    if existing_org is not None and existing_org.setup_complete:
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization setup already completed")

    org = existing_org or Organization(
        name=payload.organization_name,
        slug=payload.organization_slug.lower(),
        allowed_domains=[domain.lower() for domain in payload.allowed_domains],
        setup_complete=True,
    )
    org.name = payload.organization_name
    org.slug = payload.organization_slug.lower()
    org.allowed_domains = [domain.lower() for domain in payload.allowed_domains]
    org.setup_complete = True
    db.add(org)
    db.flush()

    roles = bootstrap_roles(db, org.id)
    admin_role = roles[ADMIN_ROLE_NAME]
    user = User(
        org_id=org.id,
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        status=UserStatus.ACTIVE,
        active_role_id=admin_role.id,
        created_by_user_id=None,
        updated_by_user_id=None,
    )
    user.roles = [admin_role]
    db.add(user)
    db.flush()
    user.active_role_id = admin_role.id
    user.created_by_user_id = user.id
    user.updated_by_user_id = user.id
    bootstrap_roles(db, org.id, actor_user_id=user.id)
    write_audit_log(
        db,
        action="organization.setup_completed",
        resource_type="organization",
        resource_id=org.id,
        org_id=org.id,
        actor_user_id=user.id,
        request_id=request_id,
    )
    db.commit()
    db.refresh(user)
    return user


def register_user(db: Session, payload: RegisterRequest) -> tuple[str, User | None]:
    org = db.scalar(select(Organization).where(Organization.setup_complete.is_(True)))
    if org is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization setup is required first")

    domain = _normalize_domain(str(payload.email))
    allowed_domains = [item.lower() for item in (org.allowed_domains or [])]
    existing_user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if existing_user is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists")

    if allowed_domains and domain not in allowed_domains:
        join_request = OrgJoinRequest(
            org_id=org.id,
            email=str(payload.email).lower(),
            full_name=payload.full_name,
            requested_domain=domain,
            message=payload.message,
        )
        db.add(join_request)
        db.commit()
        return "join_request_created", None

    user = User(
        org_id=org.id,
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        status=UserStatus.PENDING_APPROVAL,
    )
    db.add(user)
    db.flush()
    write_audit_log(
        db,
        action="user.self_registered",
        resource_type="user",
        resource_id=user.id,
        org_id=org.id,
        actor_user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return "pending_approval", user


def login_user(db: Session, email: str, password: str, request_id: str | None = None) -> str:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None or not verify_password(password, user.hashed_password):
        write_audit_log(
            db,
            action="auth.login_failed",
            resource_type="user",
            resource_id=None,
            request_id=request_id,
            metadata={"email": email.lower()},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"User status is {user.status}")
    user.last_login_at = datetime.now(UTC)
    write_audit_log(
        db,
        action="auth.login_succeeded",
        resource_type="user",
        resource_id=user.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
    )
    db.commit()
    return create_access_token(user.id, {"org_id": user.org_id})


def decide_user_approval(
    db: Session,
    *,
    target_user_id: str,
    decision: str,
    role_name: str,
    reason: str | None,
    actor: User,
    request_id: str | None = None,
) -> User:
    target_user = db.get(User, target_user_id)
    if target_user is None or target_user.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target_user.status != UserStatus.PENDING_APPROVAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending users can be decided")

    if decision == "approve":
        role = db.scalar(select(Role).where(Role.org_id == actor.org_id, Role.name == role_name))
        if role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
        target_user.status = UserStatus.ACTIVE
        target_user.roles = [role]
        target_user.active_role_id = role.id
        action = "user.approved"
    else:
        target_user.status = UserStatus.REJECTED
        action = "user.rejected"

    target_user.updated_by_user_id = actor.id
    db.add(
        UserApprovalDecision(
            org_id=actor.org_id,
            target_user_id=target_user.id,
            decision=decision,
            reason=reason,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
    )
    write_audit_log(
        db,
        action=action,
        resource_type="user",
        resource_id=target_user.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
        metadata={"role_name": role_name, "reason": reason},
    )
    write_timeline_event(
        db,
        org_id=actor.org_id,
        resource_type="user",
        resource_id=target_user.id,
        event_type=action,
        title=action.replace(".", " "),
        actor_user_id=actor.id,
        request_id=request_id,
    )
    db.commit()
    db.refresh(target_user)
    return target_user


def as_user_response(user: User) -> dict:
    return _user_response(user)
