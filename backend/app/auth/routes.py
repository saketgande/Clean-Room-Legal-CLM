from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.schemas import (
    ApprovalRequest,
    LoginRequest,
    RegisterRequest,
    RegistrationResponse,
    SetupAdminRequest,
    TokenResponse,
    UserResponse,
)
from app.auth.service import (
    as_user_response,
    create_first_admin,
    decide_user_approval,
    login_user,
    register_user,
)
from app.core.deps import get_current_user, get_db, require_permission

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


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
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    token = login_user(
        db,
        str(payload.email),
        payload.password,
        request_id=getattr(request.state, "request_id", None),
    )
    current = get_current_user_from_tokenless_context(db, str(payload.email))
    return {"access_token": token, "user_id": current.id, "org_id": current.org_id}


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


def get_current_user_from_tokenless_context(db: Session, email: str):
    from sqlalchemy import select

    from app.auth.models import User

    return db.scalar(select(User).where(User.email == email.lower()))
