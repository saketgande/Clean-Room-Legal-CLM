from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.core.enums import UserStatus


class SetupAdminRequest(BaseModel):
    setup_token: str
    organization_name: str = Field(min_length=2, max_length=255)
    organization_slug: str = Field(min_length=2, max_length=120)
    allowed_domains: list[str] = Field(default_factory=list)
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=10)


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=10)
    message: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    org_id: str


class UserResponse(BaseModel):
    id: str
    org_id: str
    email: EmailStr
    full_name: str
    status: UserStatus | str
    roles: list[str] = Field(default_factory=list)
    active_role_id: str | None = None
    active_role_name: str | None = None


class RegistrationResponse(BaseModel):
    status: str
    message: str
    user: UserResponse | None = None


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    role_name: str = "member"
    reason: str | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class RoleSwitchRequest(BaseModel):
    role_id: str | None = None
    role_name: str | None = None


class UserInvitationCreate(BaseModel):
    email: EmailStr
    role_name: str = "member"
    expires_in_days: int = Field(default=7, ge=1, le=30)


class UserInvitationResponse(BaseModel):
    id: str
    email: EmailStr
    role_name: str
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    token: str | None = None


class AcceptInvitationRequest(BaseModel):
    token: str
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=10)


class JoinRequestDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    role_name: str = "member"
    reason: str | None = None
    invitation_expires_in_days: int = Field(default=7, ge=1, le=30)


class OrgJoinRequestResponse(BaseModel):
    id: str
    org_id: str | None
    email: EmailStr
    full_name: str
    requested_domain: str | None
    message: str | None
    status: str
    decision_reason: str | None = None
    invitation_token: str | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetRequestResponse(BaseModel):
    status: str
    reset_token: str | None = None


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=10)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    api_key: str | None = None
