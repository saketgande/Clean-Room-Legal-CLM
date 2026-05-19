from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.models import (
    ApiKey,
    OrgJoinRequest,
    PasswordResetToken,
    Permission,
    RefreshToken,
    RevokedAccessToken,
    Role,
    User,
    UserApprovalDecision,
    UserInvitation,
)
from app.auth.schemas import (
    AcceptInvitationRequest,
    ApiKeyCreate,
    JoinRequestDecision,
    PasswordResetConfirmRequest,
    RegisterRequest,
    SetupAdminRequest,
    UserInvitationCreate,
)
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import UserStatus
from app.core.rbac import ADMIN_ROLE_NAME, DEFAULT_ROLE_PERMISSIONS, ALL_PERMISSIONS
from app.core.security import (
    create_access_token,
    create_token_secret,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.organizations.models import Organization


def _normalize_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def _user_response(user: User) -> dict:
    active_role = next((role for role in user.roles if role.id == user.active_role_id), None)
    return {
        "id": user.id,
        "org_id": user.org_id,
        "email": user.email,
        "full_name": user.full_name,
        "status": user.status,
        "roles": [role.name for role in user.roles],
        "active_role_id": user.active_role_id,
        "active_role_name": active_role.name if active_role else None,
    }


def _role_by_name(db: Session, *, org_id: str, role_name: str) -> Role:
    role = db.scalar(select(Role).where(Role.org_id == org_id, Role.name == role_name))
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    return role


def _token_response(db: Session, user: User) -> dict:
    refresh_token = create_token_secret()
    expires_at = utcnow() + timedelta(days=settings.refresh_token_expire_days)
    db.add(
        RefreshToken(
            org_id=user.org_id,
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=expires_at,
        )
    )
    access_token = create_access_token(
        user.id,
        {"org_id": user.org_id, "role_id": user.active_role_id},
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": user.id,
        "org_id": user.org_id,
    }


def _find_refresh_token(db: Session, raw_token: str) -> RefreshToken:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    now = utcnow()
    if row.revoked_at is not None or row.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    return row


def _public_invitation(row: UserInvitation, token: str | None = None) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "role_name": row.role_name,
        "expires_at": row.expires_at,
        "accepted_at": row.accepted_at,
        "revoked_at": row.revoked_at,
        "token": token,
    }


def _public_join_request(row: OrgJoinRequest, invitation_token: str | None = None) -> dict:
    return {
        "id": row.id,
        "org_id": row.org_id,
        "email": row.email,
        "full_name": row.full_name,
        "requested_domain": row.requested_domain,
        "message": row.message,
        "status": row.status,
        "decision_reason": row.decision_reason,
        "invitation_token": invitation_token,
    }


def _public_api_key(row: ApiKey, api_key: str | None = None) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "created_at": row.created_at,
        "api_key": api_key,
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


def login_user(db: Session, email: str, password: str, request_id: str | None = None) -> dict:
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
    # Transparently migrate a legacy raw-bcrypt hash to the current scheme on
    # the first successful login after the KDF change (committed below).
    if password_needs_rehash(password, user.hashed_password):
        user.hashed_password = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    token_response = _token_response(db, user)
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
    return token_response


def refresh_login_tokens(
    db: Session,
    *,
    refresh_token: str,
    request_id: str | None = None,
) -> dict:
    row = _find_refresh_token(db, refresh_token)
    user = db.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    row.revoked_at = utcnow()
    token_response = _token_response(db, user)
    write_audit_log(
        db,
        action="auth.token_refreshed",
        resource_type="refresh_token",
        resource_id=row.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
    )
    db.commit()
    return token_response


ALL_USER_TOKENS = "*"  # sentinel jti = "every access token for this user"


def is_access_token_revoked(
    db: Session, *, jti: str, user_id: str | None
) -> bool:
    """True if this access token's ``jti`` was individually revoked (logout)
    or is covered by a user-wide revocation (password reset / forced
    re-auth). A user-wide revocation is a ``RevokedAccessToken`` row for the
    user whose ``jti`` is the ``ALL_USER_TOKENS`` sentinel."""
    conditions = [RevokedAccessToken.jti == jti]
    if user_id:
        conditions.append(
            and_(
                RevokedAccessToken.user_id == user_id,
                RevokedAccessToken.jti == ALL_USER_TOKENS,
            )
        )
    return (
        db.scalar(
            select(RevokedAccessToken.id).where(or_(*conditions)).limit(1)
        )
        is not None
    )


def revoke_refresh_token(
    db: Session,
    *,
    user: User,
    refresh_token: str | None,
    request_id: str | None = None,
) -> None:
    if refresh_token:
        row = _find_refresh_token(db, refresh_token)
        if row.user_id != user.id or row.org_id != user.org_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
        row.revoked_at = utcnow()
        resource_id = row.id
    else:
        rows = db.scalars(
            select(RefreshToken).where(
                RefreshToken.org_id == user.org_id,
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        ).all()
        now = utcnow()
        for row in rows:
            row.revoked_at = now
        resource_id = user.id
    write_audit_log(
        db,
        action="auth.logout",
        resource_type="refresh_token",
        resource_id=resource_id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
    )
    db.commit()


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


def create_user_invitation(
    db: Session,
    *,
    payload: UserInvitationCreate,
    actor: User,
    request_id: str | None = None,
) -> dict:
    _role_by_name(db, org_id=actor.org_id, role_name=payload.role_name)
    existing_user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if existing_user is not None and existing_user.status == UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists")
    token = create_token_secret()
    invitation = UserInvitation(
        org_id=actor.org_id,
        email=str(payload.email).lower(),
        role_name=payload.role_name,
        token_hash=hash_token(token),
        expires_at=utcnow() + timedelta(days=payload.expires_in_days),
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(invitation)
    db.flush()
    write_audit_log(
        db,
        action="user.invitation_created",
        resource_type="user_invitation",
        resource_id=invitation.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
        metadata={"email": invitation.email, "role_name": invitation.role_name},
    )
    db.commit()
    db.refresh(invitation)
    return _public_invitation(invitation, token=token)


def list_user_invitations(db: Session, *, actor: User) -> list[dict]:
    rows = db.scalars(
        select(UserInvitation)
        .where(UserInvitation.org_id == actor.org_id)
        .order_by(UserInvitation.created_at.desc())
        .limit(100)
    ).all()
    return [_public_invitation(row) for row in rows]


def revoke_user_invitation(
    db: Session,
    *,
    invitation_id: str,
    actor: User,
    request_id: str | None = None,
) -> dict:
    invitation = db.get(UserInvitation, invitation_id)
    if invitation is None or invitation.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if invitation.accepted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Accepted invitations cannot be revoked")
    invitation.revoked_at = utcnow()
    invitation.updated_by_user_id = actor.id
    write_audit_log(
        db,
        action="user.invitation_revoked",
        resource_type="user_invitation",
        resource_id=invitation.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
    )
    db.commit()
    db.refresh(invitation)
    return _public_invitation(invitation)


def accept_user_invitation(
    db: Session,
    *,
    payload: AcceptInvitationRequest,
    request_id: str | None = None,
) -> dict:
    invitation = db.scalar(
        select(UserInvitation).where(UserInvitation.token_hash == hash_token(payload.token))
    )
    now = utcnow()
    if (
        invitation is None
        or invitation.revoked_at is not None
        or invitation.accepted_at is not None
        or invitation.expires_at <= now
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid invitation token")
    role = _role_by_name(db, org_id=invitation.org_id, role_name=invitation.role_name)
    existing_user = db.scalar(select(User).where(User.email == invitation.email))
    if existing_user is not None and existing_user.status == UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists")
    user = existing_user or User(
        org_id=invitation.org_id,
        email=invitation.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        status=UserStatus.ACTIVE,
        created_by_user_id=invitation.created_by_user_id,
        updated_by_user_id=invitation.created_by_user_id,
    )
    user.full_name = payload.full_name
    user.hashed_password = hash_password(payload.password)
    user.status = UserStatus.ACTIVE
    user.roles = [role]
    user.active_role_id = role.id
    db.add(user)
    db.flush()
    invitation.accepted_at = now
    invitation.updated_by_user_id = user.id
    token_response = _token_response(db, user)
    write_audit_log(
        db,
        action="user.invitation_accepted",
        resource_type="user_invitation",
        resource_id=invitation.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
    )
    db.commit()
    return token_response


def list_join_requests(db: Session, *, actor: User) -> list[dict]:
    rows = db.scalars(
        select(OrgJoinRequest)
        .where(OrgJoinRequest.org_id == actor.org_id)
        .order_by(OrgJoinRequest.created_at.desc())
        .limit(100)
    ).all()
    return [_public_join_request(row) for row in rows]


def decide_join_request(
    db: Session,
    *,
    join_request_id: str,
    payload: JoinRequestDecision,
    actor: User,
    request_id: str | None = None,
) -> dict:
    join_request = db.get(OrgJoinRequest, join_request_id)
    if join_request is None or join_request.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Join request not found")
    if join_request.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Join request already decided")
    invitation_token = None
    if payload.decision == "approve":
        invitation = create_user_invitation(
            db,
            payload=UserInvitationCreate(
                email=join_request.email,
                role_name=payload.role_name,
                expires_in_days=payload.invitation_expires_in_days,
            ),
            actor=actor,
            request_id=request_id,
        )
        invitation_token = invitation["token"]
        join_request.status = "approved"
    else:
        join_request.status = "rejected"
    join_request.decided_by_user_id = actor.id
    join_request.decision_reason = payload.reason
    write_audit_log(
        db,
        action=f"org_join_request.{join_request.status}",
        resource_type="org_join_request",
        resource_id=join_request.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
        metadata={"role_name": payload.role_name, "reason": payload.reason},
    )
    db.commit()
    db.refresh(join_request)
    return _public_join_request(join_request, invitation_token=invitation_token)


def request_password_reset(
    db: Session,
    *,
    email: str,
    request_id: str | None = None,
) -> dict:
    user = db.scalar(select(User).where(User.email == email.lower()))
    reset_token = None
    if user is not None and user.status == UserStatus.ACTIVE:
        reset_token = create_token_secret()
        db.add(
            PasswordResetToken(
                org_id=user.org_id,
                user_id=user.id,
                token_hash=hash_token(reset_token),
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        write_audit_log(
            db,
            action="auth.password_reset_requested",
            resource_type="user",
            resource_id=user.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            request_id=request_id,
        )
        db.commit()
    if not (settings.mock_resend or settings.environment.lower() in {"local", "development", "dev", "test"}):
        reset_token = None
    return {"status": "ok", "reset_token": reset_token}


def confirm_password_reset(
    db: Session,
    *,
    payload: PasswordResetConfirmRequest,
    request_id: str | None = None,
) -> None:
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(payload.token))
    )
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password reset token")
    user = db.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password reset token")
    user.hashed_password = hash_password(payload.new_password)
    user.updated_by_user_id = user.id
    row.used_at = utcnow()
    for refresh_token in db.scalars(
        select(RefreshToken).where(
            RefreshToken.org_id == user.org_id,
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
    ):
        refresh_token.revoked_at = row.used_at
    write_audit_log(
        db,
        action="auth.password_reset_completed",
        resource_type="user",
        resource_id=user.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
    )
    db.commit()


def create_api_key(db: Session, *, payload: ApiKeyCreate, actor: User, request_id: str | None = None) -> dict:
    raw_key = create_token_secret(prefix="clm_")
    row = ApiKey(
        org_id=actor.org_id,
        user_id=actor.id,
        name=payload.name,
        key_hash=hash_token(raw_key),
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(row)
    db.flush()
    write_audit_log(
        db,
        action="api_key.created",
        resource_type="api_key",
        resource_id=row.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
    )
    db.commit()
    db.refresh(row)
    return _public_api_key(row, api_key=raw_key)


def list_api_keys(db: Session, *, actor: User) -> list[dict]:
    rows = db.scalars(
        select(ApiKey)
        .where(ApiKey.org_id == actor.org_id, ApiKey.user_id == actor.id)
        .order_by(ApiKey.created_at.desc())
        .limit(100)
    ).all()
    return [_public_api_key(row) for row in rows]


def revoke_api_key(
    db: Session,
    *,
    api_key_id: str,
    actor: User,
    request_id: str | None = None,
) -> dict:
    row = db.get(ApiKey, api_key_id)
    if row is None or row.org_id != actor.org_id or row.user_id != actor.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    row.revoked_at = utcnow()
    row.updated_by_user_id = actor.id
    write_audit_log(
        db,
        action="api_key.revoked",
        resource_type="api_key",
        resource_id=row.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
    )
    db.commit()
    db.refresh(row)
    return _public_api_key(row)


def authenticate_api_key(db: Session, raw_key: str) -> User | None:
    if not raw_key.startswith("clm_"):
        return None
    row = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_token(raw_key)))
    if row is None or row.revoked_at is not None:
        return None
    user = db.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        return None
    row.last_used_at = utcnow()
    db.commit()
    return user


def switch_active_role(
    db: Session,
    *,
    actor: User,
    role_id: str | None,
    role_name: str | None,
    request_id: str | None = None,
) -> User:
    if not role_id and not role_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "role_id or role_name is required")
    role = next(
        (
            item
            for item in actor.roles
            if (role_id and item.id == role_id) or (role_name and item.name == role_name)
        ),
        None,
    )
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role is not assigned to user")
    actor.active_role_id = role.id
    actor.updated_by_user_id = actor.id
    write_audit_log(
        db,
        action="auth.active_role_switched",
        resource_type="user",
        resource_id=actor.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        request_id=request_id,
        metadata={"role_id": role.id, "role_name": role.name},
    )
    db.commit()
    db.refresh(actor)
    return actor


def as_user_response(user: User) -> dict:
    return _user_response(user)
