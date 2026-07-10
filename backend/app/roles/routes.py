from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.auth.schemas import UserResponse
from app.core.deps import get_db, require_permission
from app.roles import service
from app.roles.schemas import (
    PermissionInfo,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    UserClearanceUpdate,
    UserRolesUpdate,
)

router = APIRouter(prefix="/roles", tags=["roles"])

# Role administration is an org-admin settings capability.
_MANAGE = require_permission("admin_panel:access")
# Assigning roles to users is its own capability (wires the previously-dead perm).
_ASSIGN = require_permission("user:update_role")


@router.get("", response_model=list[RoleResponse])
def list_roles(db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return service.list_roles(db, current_user.org_id)


@router.get("/permissions", response_model=list[PermissionInfo])
def list_permission_catalog(current_user=Depends(_MANAGE)):
    return service.permission_catalog()


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.create_role(db, current_user, payload)


@router.patch("/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: str,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.update_role(db, current_user, role_id, payload)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    service.delete_role(db, current_user, role_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/user/{user_id}", response_model=UserResponse)
def set_user_roles(
    user_id: str,
    payload: UserRolesUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_ASSIGN),
):
    return service.set_user_roles(db, current_user, user_id, payload)


@router.put("/user/{user_id}/clearance", response_model=UserResponse)
def set_user_clearance(
    user_id: str,
    payload: UserClearanceUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_ASSIGN),
):
    return service.set_user_clearance(db, current_user, user_id, payload)
