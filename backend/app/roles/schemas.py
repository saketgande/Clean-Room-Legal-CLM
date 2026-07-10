from pydantic import BaseModel, Field


class PermissionInfo(BaseModel):
    value: str
    group: str
    description: str | None = None


class RoleResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    is_builtin: bool
    permissions: list[str]
    user_count: int


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None


class UserRolesUpdate(BaseModel):
    role_ids: list[str]
    active_role_id: str | None = None


class UserClearanceUpdate(BaseModel):
    # Phase 3 (MAC): the highest confidentiality a user may read.
    clearance: str = Field(pattern="^(public|internal|confidential|restricted)$")
