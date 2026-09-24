from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.deps import get_current_user
from app.grants.dependencies import get_grant_service
from app.grants.schemas import GrantCreate, GrantResponse
from app.grants.service import GrantService

router = APIRouter(prefix="/grants", tags=["grants"])


@router.get("", response_model=list[GrantResponse])
def list_grants(
    resource_type: str,
    resource_id: str,
    include_revoked: bool = False,
    grant_service: GrantService = Depends(get_grant_service),
    current_user=Depends(get_current_user),
):
    # Only someone who can share the resource (owner / admin / share-grant) may
    # see who has access to it.
    if not grant_service.can_manage_grants(
        user=current_user, resource_type=resource_type, resource_id=resource_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed to view this resource's access")
    return grant_service.list_grants_for_resource(
        org_id=current_user.org_id,
        resource_type=resource_type,
        resource_id=resource_id,
        include_revoked=include_revoked,
    )


@router.post("", response_model=GrantResponse, status_code=status.HTTP_201_CREATED)
def create_grant(
    payload: GrantCreate,
    grant_service: GrantService = Depends(get_grant_service),
    current_user=Depends(get_current_user),
):
    return grant_service.grant_access(actor=current_user, payload=payload)


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_grant(
    grant_id: str,
    grant_service: GrantService = Depends(get_grant_service),
    current_user=Depends(get_current_user),
):
    grant_service.revoke_grant(actor=current_user, grant_id=grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
