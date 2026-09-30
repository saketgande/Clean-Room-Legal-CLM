from fastapi import APIRouter, Depends, Response, status

from app.authority.dependencies import get_authority_service
from app.authority.schemas import AuthorityCreate, AuthorityResponse, AuthorityUpdate
from app.authority.service import AuthorityService
from app.core.deps import get_current_user, require_permission

router = APIRouter(prefix="/authority-grants", tags=["authority"])

# Managing the Delegation-of-Authority matrix is an admin-settings capability.
_MANAGE = require_permission("admin_panel:access")


@router.get("", response_model=list[AuthorityResponse])
def list_grants(
    include_revoked: bool = False,
    authority_service: AuthorityService = Depends(get_authority_service),
    current_user=Depends(_MANAGE),
):
    return authority_service.list_grants(
        org_id=current_user.org_id, include_revoked=include_revoked
    )


@router.get("/self")
def my_authority(
    authority_service: AuthorityService = Depends(get_authority_service),
    current_user=Depends(get_current_user),
):
    """Effective authority for the current user, per gated action. Any
    authenticated user may read their own authority (for UI hints)."""
    return authority_service.describe_self(user=current_user)


@router.post("", response_model=AuthorityResponse, status_code=status.HTTP_201_CREATED)
def create_grant(
    payload: AuthorityCreate,
    authority_service: AuthorityService = Depends(get_authority_service),
    current_user=Depends(_MANAGE),
):
    return authority_service.create_grant(actor=current_user, payload=payload)


@router.patch("/{grant_id}", response_model=AuthorityResponse)
def update_grant(
    grant_id: str,
    payload: AuthorityUpdate,
    authority_service: AuthorityService = Depends(get_authority_service),
    current_user=Depends(_MANAGE),
):
    return authority_service.update_grant(actor=current_user, grant_id=grant_id, payload=payload)


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_grant(
    grant_id: str,
    authority_service: AuthorityService = Depends(get_authority_service),
    current_user=Depends(_MANAGE),
):
    authority_service.revoke_grant(actor=current_user, grant_id=grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
