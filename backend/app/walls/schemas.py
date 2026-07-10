from pydantic import BaseModel, Field


class WallPrincipalIn(BaseModel):
    principal_type: str = Field(pattern="^(user|role)$")
    principal_id: str


class WallPrincipalOut(BaseModel):
    id: str
    principal_type: str
    principal_id: str
    principal_label: str


class WallCreate(BaseModel):
    name: str
    reason: str | None = None
    scope_type: str = Field(pattern="^(contract|project)$")
    scope_id: str
    principals: list[WallPrincipalIn] = Field(default_factory=list)


class WallUpdate(BaseModel):
    name: str | None = None
    reason: str | None = None
    active: bool | None = None
    principals: list[WallPrincipalIn] | None = None


class WallResponse(BaseModel):
    id: str
    name: str
    reason: str | None = None
    scope_type: str
    scope_id: str
    scope_label: str | None = None
    active: bool
    principals: list[WallPrincipalOut] = Field(default_factory=list)
    created_at: str | None = None
