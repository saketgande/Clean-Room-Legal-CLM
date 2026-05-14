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


class RegistrationResponse(BaseModel):
    status: str
    message: str
    user: UserResponse | None = None


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    role_name: str = "member"
    reason: str | None = None
