from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.authority import service
from app.authority.schemas import AuthorityCreate, AuthorityResponse, AuthorityUpdate
from app.core.deps import get_current_user, get_db, require_permission

router = APIRouter(prefix="/authority-grants", tags=["authority"])

# Managing the Delegation-of-Authority matrix is an admin-settings capability.
_MANAGE = require_permission("admin_panel:access")


@router.get("", response_model=list[AuthorityResponse])
def list_grants(
    include_revoked: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.list_grants(
        db, org_id=current_user.org_id, include_revoked=include_revoked
    )


@router.get("/self")
def my_authority(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Effective authority for the current user, per gated action. Any
    authenticated user may read their own authority (for UI hints)."""
    return service.describe_self(db, user=current_user)


@router.post("", response_model=AuthorityResponse, status_code=status.HTTP_201_CREATED)
def create_grant(
    payload: AuthorityCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.create_grant(db, actor=current_user, payload=payload)


@router.patch("/{grant_id}", response_model=AuthorityResponse)
def update_grant(
    grant_id: str,
    payload: AuthorityUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.update_grant(db, actor=current_user, grant_id=grant_id, payload=payload)


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_grant(
    grant_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    service.revoke_grant(db, actor=current_user, grant_id=grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
