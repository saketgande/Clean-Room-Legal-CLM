from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import OrgJoinRequest, UserInvitation
from app.core.deps import get_db, require_permission
from app.organizations.models import Organization

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
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:read")),
):
    org = db.get(Organization, current_user.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


@router.patch("/current", response_model=OrganizationResponse)
def update_organization(
    payload: OrganizationUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    org = db.get(Organization, current_user.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    if payload.name is not None:
        org.name = payload.name
    if payload.allowed_domains is not None:
        org.allowed_domains = [domain.lower() for domain in payload.allowed_domains]
    if payload.default_role_name is not None:
        org.default_role_name = payload.default_role_name
    db.commit()
    db.refresh(org)
    return org


@router.get("/join-requests")
def list_join_requests(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    rows = db.scalars(
        select(OrgJoinRequest).where(OrgJoinRequest.org_id == current_user.org_id)
    ).all()
    return rows


@router.get("/invitations")
def list_invitations(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user:approve")),
):
    return db.scalars(
        select(UserInvitation).where(UserInvitation.org_id == current_user.org_id)
    ).all()
