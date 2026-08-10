import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import (
    ApiKey,
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
    PasswordResetConfirmRequest,
    RegisterRequest,
    SetupAdminRequest,
    UserInvitationCreate,
)
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import UserStatus
from app.core.rbac import ADMIN_ROLE_NAME, ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS
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


def _mask_email(email: str | None) -> str | None:
    """Mask the local-part of an email for audit metadata.

    Keeps the domain and the first local-part character so the row is still
    useful for support/forensics ("which mailbox?") without persisting the full
    address as plaintext PII in the audit log. ``alice@acme.com`` -> ``a***@acme.com``.
    Falls back to masking everything after the first char when there's no ``@``.
    """
    if not email:
        return email
    normalized = email.lower()
    local, sep, domain = normalized.partition("@")
    if not sep:
        # Malformed/non-email value: keep only the first char.
        return (normalized[:1] + "***") if normalized else normalized
    masked_local = (local[:1] + "***") if local else "***"
    return f"{masked_local}@{domain}"


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
        "clearance": getattr(user, "clearance", None) or "confidential",
        "permissions": sorted(user.permission_values),
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
    access_token, _ = create_access_token(
        user.id,
        {"org_id": user.org_id, "role_id": user.active_role_id},
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": user.id,
        "org_id": user.org_id,
    }


def _find_refresh_token(db: Session, raw_token: str | None) -> RefreshToken:
    # Missing tokens (cookie cleared after logout + empty body) used to crash
    # in hash_token with a TypeError; surface a 401 the client can handle.
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    # Locked read: both callers (refresh, logout) revoke this row right after
    # fetching it. Without the lock, two concurrent replays of the same stolen
    # refresh token can both pass the revoked_at check before either commits,
    # so both succeed — defeating rotation-based theft detection.
    row = db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(raw_token))
        .with_for_update()
    )
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
    # Constant-time compare: a plain != leaks timing info a network attacker
    # could use to brute-force the token character-by-character.
    if not secrets.compare_digest(payload.setup_token, settings.setup_token):
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
    # Seed the default approver groups (Legal Counsel, Finance, …) so the
    # approval-routing form has real options out of the box. Imported locally to
    # keep auth's import graph light.
    from app.approvals.service import ensure_default_approver_groups

    ensure_default_approver_groups(db, org_id=org.id, actor_user_id=user.id)
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

    existing_user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    # Email-enumeration defense: do not differentiate "address already
    # registered" from "address newly registered". An unauthenticated caller
    # cannot tell whether the email exists in our system. We still audit the
    # collision internally so an admin can see it in the log.
    if existing_user is not None:
        write_audit_log(
            db,
            action="user.self_register_collision",
            resource_type="user",
            resource_id=existing_user.id,
            org_id=org.id,
            metadata={"email": _mask_email(str(payload.email))},
        )
        return "pending_approval", None

    # Single-tenant: no email-domain gating. Anyone who self-registers becomes a
    # PENDING_APPROVAL user the admin vets on the "Users & Access" queue. (The
    # old cross-org join-request fork is gone — there is only one company.)
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


_LOGIN_INVALID_CREDENTIALS = "Invalid email or password"
_DUMMY_PASSWORD_HASH = "$2b$12$OaXwEt/8jCeiZ6W/9ZFURuXp292uSJDCEDX6EiAMMTmAt0JJRci8y"


def login_user(db: Session, email: str, password: str, request_id: str | None = None) -> dict:
    user = db.scalar(select(User).where(User.email == email.lower()))
    # Email enumeration defense: every failure path (unknown email, bad
    # password, non-active status) returns the same 401 with the same body.
    # An attacker can no longer distinguish "this email exists" from "this
    # email doesn't" — closing the spray/stuffing reconnaissance vector.
    invalid_credentials_error = HTTPException(
        status.HTTP_401_UNAUTHORIZED, _LOGIN_INVALID_CREDENTIALS
    )
    password_ok = verify_password(
        password,
        user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH,
    )
    if user is None or not password_ok:
        write_audit_log(
            db,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.id if user else None,
            request_id=request_id,
            metadata={"email": _mask_email(email), "reason": "invalid_credentials"},
        )
        db.commit()
        raise invalid_credentials_error
    if user.status != UserStatus.ACTIVE:
        # Internally distinguish in the audit log so an admin can see why a
        # legitimate user can't log in, but the external response is the same
        # 401 to avoid leaking account state.
        write_audit_log(
            db,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.id,
            org_id=user.org_id,
            request_id=request_id,
            metadata={"email": _mask_email(email), "reason": f"status:{user.status}"},
        )
        db.commit()
        raise invalid_credentials_error
    # Transparently migrate a legacy bcrypt hash to the current SHA-256-prehash
    # scheme on the first successful login after the KDF change.
    if password_needs_rehash(password, user.hashed_password):
        user.hashed_password = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    token_response = _token_response(db, user)
    # Audit and token state commit together; a failed commit leaves neither.
    # commits independently) then committed the main tx — if the main commit
    # failed we'd have an audit entry claiming login succeeded with no
    # refresh token actually persisted. Audit-after-commit is the right
    # invariant: the audit row reflects state that actually exists.
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
    db: Session, *, jti: str, user_id: str | None, token_issued_at: datetime | None = None
) -> bool:
    """True if this access token's ``jti`` was individually revoked (logout)
    or is covered by a user-wide revocation (password reset / forced
    re-auth). A user-wide revocation is a ``RevokedAccessToken`` row for the
    user whose ``jti`` is the ``ALL_USER_TOKENS`` sentinel."""
    if db.scalar(select(RevokedAccessToken.id).where(RevokedAccessToken.jti == jti).limit(1)) is not None:
        return True
    if not user_id:
        return False
    wildcard_rows = db.scalars(
        select(RevokedAccessToken).where(
            RevokedAccessToken.user_id == user_id,
            RevokedAccessToken.jti == ALL_USER_TOKENS,
        )
    ).all()
    if not wildcard_rows:
        return False
    if token_issued_at is None:
        return True
    # >= (not >): a token minted in the same whole second as a wildcard
    # "sign out everywhere" revocation must also be treated as revoked, since
    # the JWT `iat` claim is only second-granular.
    return any(row.created_at.replace(microsecond=0) >= token_issued_at for row in wildcard_rows)


def revoke_refresh_token(
    db: Session,
    *,
    user: User,
    refresh_token: str | None,
    access_token_jti: str | None = None,
    access_token_exp: datetime | None = None,
    request_id: str | None = None,
) -> None:
    """Revoke the user's refresh token(s) and, when supplied, also persist a
    ``RevokedAccessToken`` row for the caller's access-token ``jti`` so the
    bearer they just used is killed immediately rather than living out the
    rest of its 60-minute window. Without this, logout only cut off
    *renewal* — the still-valid access token kept working until exp."""
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
    # Persist the access-token jti so subsequent requests with this bearer
    # fail at decode time. Without this, the access token outlives logout
    # until its natural exp (default 60 min). Bound to access_token_exp so
    # the row can be pruned once the JWT itself would be rejected anyway.
    if access_token_jti and access_token_jti != ALL_USER_TOKENS:
        existing = db.scalar(
            select(RevokedAccessToken.id).where(RevokedAccessToken.jti == access_token_jti)
        )
        if existing is None:
            db.add(
                RevokedAccessToken(
                    user_id=user.id,
                    org_id=user.org_id,
                    jti=access_token_jti,
                    expires_at=access_token_exp or (utcnow() + timedelta(minutes=settings.access_token_expire_minutes)),
                    reason="logout",
                )
            )
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


def list_org_users(
    db: Session,
    *,
    actor: User,
    status_filter: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List users in the actor's org, optionally filtered by status.

    Surfaces the pending-approval queue to admins. Without this, self-
    registered users with no domain restriction land in the ``user`` table
    with status ``pending_approval`` and were unreachable from the UI — the
    join-request list only shows the *domain-rejected* registrations.
    """
    stmt = (
        select(User)
        .where(User.org_id == actor.org_id)
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        # Match either the enum value or its string form so callers can pass
        # "pending_approval" without knowing the enum class.
        stmt = stmt.where(User.status == status_filter)
    rows = db.scalars(stmt).all()
    return [_user_response(row) for row in rows]


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
        metadata={"email": _mask_email(invitation.email), "role_name": invitation.role_name},
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
    # If a PENDING_APPROVAL user already exists for this email, accepting an
    # invitation overwrites their password, full_name, and role. Capture the
    # prior state in the audit record so the overwrite is forensically
    # traceable — without this the audit log only shows "invitation accepted"
    # and there's no signal that a prior account was rewritten.
    prior_state: dict | None = None
    if existing_user is not None:
        prior_state = {
            "user_id": existing_user.id,
            "status": str(existing_user.status),
            "full_name": existing_user.full_name,
            "active_role_id": existing_user.active_role_id,
        }
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
        before=prior_state,
        after={
            "user_id": user.id,
            "status": str(user.status),
            "full_name": user.full_name,
            "active_role_id": user.active_role_id,
        },
    )
    db.commit()
    return token_response


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
    # Explicit boolean flag rather than an env-name match. A typo like
    # ENVIRONMENT=prod (vs "production") used to silently expose reset tokens
    # in the response body; gating on a dedicated flag closes that.
    # ``validate_runtime_settings`` refuses to boot a non-local environment
    # with this flag enabled, so accidental exposure is also caught at startup.
    if not settings.expose_password_reset_token_in_response:
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
    # Force-invalidate every outstanding access token for this user. Without
    # this, a previously-issued access token survives until its natural exp
    # — defeating the point of "password reset signs you out everywhere".
    # ``is_access_token_revoked`` honors the wildcard jti per-user.
    existing_wildcard = db.scalar(
        select(RevokedAccessToken.id).where(
            RevokedAccessToken.user_id == user.id,
            RevokedAccessToken.jti == ALL_USER_TOKENS,
            RevokedAccessToken.expires_at > utcnow(),
        )
    )
    if existing_wildcard is None:
        db.add(
            RevokedAccessToken(
                user_id=user.id,
                org_id=user.org_id,
                jti=ALL_USER_TOKENS,
                expires_at=utcnow() + timedelta(minutes=settings.access_token_expire_minutes),
                reason="password_reset",
            )
        )
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
