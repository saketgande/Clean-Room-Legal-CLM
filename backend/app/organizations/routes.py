from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import require_permission
from app.organizations.dependencies import get_organizations_service
from app.organizations.service import OrganizationsService

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    allowed_domains: list[str]
    default_role_name: str


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    allowed_domains: list[str] | None = None
    default_role_name: str | None = None


@router.get("/current", response_model=OrganizationResponse)
def current_organization(
    current_user=Depends(require_permission("user:read")),
    service: OrganizationsService = Depends(get_organizations_service),
):
    return service.get_current_organization(org_id=current_user.org_id)


@router.patch("/current", response_model=OrganizationResponse)
def update_organization(
    payload: OrganizationUpdate,
    current_user=Depends(require_permission("admin_panel:access")),
    service: OrganizationsService = Depends(get_organizations_service),
):
    return service.update_organization(org_id=current_user.org_id, payload=payload)
