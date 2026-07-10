from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.grants import service
from app.grants.schemas import GrantCreate, GrantResponse

router = APIRouter(prefix="/grants", tags=["grants"])


@router.get("", response_model=list[GrantResponse])
def list_grants(
    resource_type: str,
    resource_id: str,
    include_revoked: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Only someone who can share the resource (owner / admin / share-grant) may
    # see who has access to it.
    if not service.can_manage_grants(
        db, user=current_user, resource_type=resource_type, resource_id=resource_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed to view this resource's access")
    return service.list_grants_for_resource(
        db,
        org_id=current_user.org_id,
        resource_type=resource_type,
        resource_id=resource_id,
        include_revoked=include_revoked,
    )


@router.post("", response_model=GrantResponse, status_code=status.HTTP_201_CREATED)
def create_grant(
    payload: GrantCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return service.grant_access(db, actor=current_user, payload=payload)


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_grant(
    grant_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    service.revoke_grant(db, actor=current_user, grant_id=grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
