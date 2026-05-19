from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.service import authenticate_api_key, is_access_token_revoked
from app.auth.models import User
from app.core.database import SessionLocal
from app.core.enums import UserStatus
from app.core.rbac import has_permission
from app.core.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a session, rolling back on uncaught exceptions.

    The previous shape relied on SQLAlchemy's implicit close-time rollback,
    which works in practice but can mask half-applied state when mixed with
    the audit-log autonomous-session pattern. An explicit rollback here
    documents the invariant and makes the failure mode visible in traces.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        api_key_user = authenticate_api_key(db, credentials.credentials)
        if api_key_user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
        request.state.current_user = api_key_user
        return api_key_user

    user_id = payload.get("sub")
    jti = payload.get("jti")
    # JWT revocation: reject tokens whose jti has been individually revoked
    # (logout) or covered by a wildcard "all tokens for user" revocation
    # (password reset). Without this, a stolen access token stays valid for
    # the rest of its natural lifetime.
    if jti and is_access_token_revoked(db, jti=jti, user_id=user_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked")
    user = db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Active user required")
    role_id = payload.get("role_id")
    if role_id and user.active_role_id != role_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token role is no longer active")
    request.state.current_user = user
    # Stash these so endpoints (e.g. /auth/logout) can revoke this specific
    # access token without re-decoding.
    request.state.access_token_jti = jti
    request.state.access_token_exp = payload.get("exp")
    return user


def require_permission(permission: str):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not has_permission(current_user.permission_values, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return current_user

    return dependency
