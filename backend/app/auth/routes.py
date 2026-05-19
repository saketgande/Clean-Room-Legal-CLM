from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth.schemas import (
    AcceptInvitationRequest,
    ApiKeyCreate,
    ApiKeyResponse,
    ApprovalRequest,
    JoinRequestDecision,
    LoginRequest,
    LogoutRequest,
    OrgJoinRequestResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    RegisterRequest,
    RegistrationResponse,
    RefreshTokenRequest,
    RoleSwitchRequest,
    SetupAdminRequest,
    TokenResponse,
    UserInvitationCreate,
    UserInvitationResponse,
    UserResponse,
)
from app.core.config import settings
from app.core.rate_limit import limiter
from app.auth.service import (
    accept_user_invitation,
    as_user_response,
    confirm_password_reset,
    create_api_key,
    create_first_admin,
    create_user_invitation,
    decide_user_approval,
    decide_join_request,
    list_api_keys,
    list_join_requests,
    list_user_invitations,
    login_user,
    refresh_login_tokens,
    register_user,
    request_password_reset,
    revoke_api_key,
    revoke_refresh_token,
    revoke_user_invitation,
    switch_active_role,
)
from app.core.deps import get_current_user, get_db, require_permission

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Set the refresh token as an HttpOnly cookie scoped to /auth.

    Why a cookie: localStorage made the refresh token reachable from any
    XSS, which gave an attacker a 30-day persistent session. HttpOnly +
    SameSite + Secure removes JS access entirely.
    """
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
    )


def _emit_token_response(response: Response, payload: dict) -> dict:
    """Set the refresh cookie and optionally strip the refresh_token from the body."""
    refresh_token = payload.get("refresh_token")
    if refresh_token:
        _set_refresh_cookie(response, refresh_token)
    if not settings.expose_refresh_token_in_body:
        payload = {k: v for k, v in payload.items() if k != "refresh_token"}
    return payload


def _read_refresh_token(request: Request, body_value: str | None) -> str | None:
    """Prefer the HttpOnly cookie; fall back to the body for legacy clients."""
    cookie_token = request.cookies.get(settings.refresh_cookie_name)
    return cookie_token or body_value


@router.post("/setup/first-admin", response_model=UserResponse)
def setup_first_admin(
    payload: SetupAdminRequest, request: Request, db: Session = Depends(get_db)
):
    user = create_first_admin(db, payload, request_id=getattr(request.state, "request_id", None))
    return as_user_response(user)


@router.post("/register", response_model=RegistrationResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    status, user = register_user(db, payload)
    if user is None:
        return {
            "status": status,
            "message": "A join request was created because the email domain is not allowed yet.",
            "user": None,
        }
    return {
        "status": status,
        "message": "Registration is pending admin approval.",
        "user": as_user_response(user),
    }


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.rate_limit_login)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    tokens = login_user(
        db,
        str(payload.email),
        payload.password,
        request_id=getattr(request.state, "request_id", None),
    )
    return _emit_token_response(response, tokens)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(settings.rate_limit_refresh)
def refresh(
    payload: RefreshTokenRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    refresh_token = _read_refresh_token(request, payload.refresh_token)
    tokens = refresh_login_tokens(
        db,
        refresh_token=refresh_token,
        request_id=getattr(request.state, "request_id", None),
    )
    return _emit_token_response(response, tokens)


@router.post("/logout", status_code=204)
def logout(
    payload: LogoutRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    refresh_token = _read_refresh_token(request, payload.refresh_token)
    # Pull the access-token claims off request.state so we can revoke its jti.
    access_jti = getattr(request.state, "access_token_jti", None)
    access_exp = getattr(request.state, "access_token_exp", None)
    if isinstance(access_exp, int):
        access_exp = datetime.fromtimestamp(access_exp, tz=UTC)
    elif access_exp is None:
        # Fall back to the configured max so the revocation row outlives
        # any token signed with the current settings.
        access_exp = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    revoke_refresh_token(
        db,
        user=current_user,
        refresh_token=refresh_token,
        access_token_jti=access_jti,
        access_token_exp=access_exp,
        request_id=getattr(request.state, "request_id", None),
    )
    _clear_refresh_cookie(response)


@router.post("/active-role", response_model=UserResponse)
def active_role(
    payload: RoleSwitchRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = switch_active_role(
        db,
        actor=current_user,
        role_id=payload.role_id,
        role_name=payload.role_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return as_user_response(user)


@router.post("/invitations/accept", response_model=TokenResponse)
@limiter.limit(settings.rate_limit_invitation_accept)
def accept_invitation(
    payload: AcceptInvitationRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    tokens = accept_user_invitation(
        db,
        payload=payload,
        request_id=getattr(request.state, "request_id", None),
    )
    return _emit_token_response(response, tokens)


@router.post("/password-reset/request", response_model=PasswordResetRequestResponse)
@limiter.limit(settings.rate_limit_password_reset)
def password_reset_request(
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    return request_password_reset(
        db,
        email=str(payload.email),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/password-reset/confirm", status_code=204)
@limiter.limit(settings.rate_limit_password_reset)
def password_reset_confirm(
    payload: PasswordResetConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    confirm_password_reset(
        db,
        payload=payload,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/me", response_model=UserResponse)
def me(current_user=Depends(get_current_user)):
    return as_user_response(current_user)


@users_router.post("/{user_id}/approval", response_model=UserResponse)
def decide_approval(
    user_id: str,
    payload: ApprovalRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    user = decide_user_approval(
        db,
        target_user_id=user_id,
        decision=payload.decision,
        role_name=payload.role_name,
        reason=payload.reason,
        actor=current_user,
        request_id=getattr(request.state, "request_id", None),
    )
    return as_user_response(user)


@users_router.get("/invitations", response_model=list[UserInvitationResponse])
def invitations(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    return list_user_invitations(db, actor=current_user)


@users_router.post(
    "/invitations",
    response_model=UserInvitationResponse,
    status_code=201,
)
def create_invitation(
    payload: UserInvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    return create_user_invitation(
        db,
        payload=payload,
        actor=current_user,
        request_id=getattr(request.state, "request_id", None),
    )


@users_router.post("/invitations/{invitation_id}/revoke", response_model=UserInvitationResponse)
def revoke_invitation(
    invitation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    return revoke_user_invitation(
        db,
        invitation_id=invitation_id,
        actor=current_user,
        request_id=getattr(request.state, "request_id", None),
    )


@users_router.get("/join-requests", response_model=list[OrgJoinRequestResponse])
def join_requests(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    return list_join_requests(db, actor=current_user)


@users_router.post("/join-requests/{join_request_id}/decision", response_model=OrgJoinRequestResponse)
def decide_join(
    join_request_id: str,
    payload: JoinRequestDecision,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    return decide_join_request(
        db,
        join_request_id=join_request_id,
        payload=payload,
        actor=current_user,
        request_id=getattr(request.state, "request_id", None),
    )


@users_router.get("/api-keys", response_model=list[ApiKeyResponse])
def api_keys(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_api_keys(db, actor=current_user)


@users_router.post("/api-keys", response_model=ApiKeyResponse, status_code=201)
def create_key(
    payload: ApiKeyCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return create_api_key(
        db,
        payload=payload,
        actor=current_user,
        request_id=getattr(request.state, "request_id", None),
    )


@users_router.post("/api-keys/{api_key_id}/revoke", response_model=ApiKeyResponse)
def revoke_key(
    api_key_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return revoke_api_key(
        db,
        api_key_id=api_key_id,
        actor=current_user,
        request_id=getattr(request.state, "request_id", None),
    )
