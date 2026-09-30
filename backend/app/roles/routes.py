from fastapi import APIRouter, Depends, Response, status

from app.auth.schemas import UserResponse
from app.core.deps import require_permission
from app.roles.dependencies import get_role_service
from app.roles.schemas import (
    PermissionInfo,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    UserClearanceUpdate,
    UserRolesUpdate,
)
from app.roles.service import RoleService, permission_catalog

router = APIRouter(prefix="/roles", tags=["roles"])

# Role administration is an org-admin settings capability.
_MANAGE = require_permission("admin_panel:access")
# Assigning roles to users is its own capability (wires the previously-dead perm).
_ASSIGN = require_permission("user:update_role")


@router.get("", response_model=list[RoleResponse])
def list_roles(
    role_service: RoleService = Depends(get_role_service),
    current_user=Depends(_MANAGE),
):
    return role_service.list_roles(current_user.org_id)


@router.get("/permissions", response_model=list[PermissionInfo])
def list_permission_catalog(current_user=Depends(_MANAGE)):
    return permission_catalog()


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    role_service: RoleService = Depends(get_role_service),
    current_user=Depends(_MANAGE),
):
    return role_service.create_role(current_user, payload)


@router.patch("/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: str,
    payload: RoleUpdate,
    role_service: RoleService = Depends(get_role_service),
    current_user=Depends(_MANAGE),
):
    return role_service.update_role(current_user, role_id, payload)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: str,
    role_service: RoleService = Depends(get_role_service),
    current_user=Depends(_MANAGE),
):
    role_service.delete_role(current_user, role_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/user/{user_id}", response_model=UserResponse)
def set_user_roles(
    user_id: str,
    payload: UserRolesUpdate,
    role_service: RoleService = Depends(get_role_service),
    current_user=Depends(_ASSIGN),
):
    return role_service.set_user_roles(current_user, user_id, payload)


@router.put("/user/{user_id}/clearance", response_model=UserResponse)
def set_user_clearance(
    user_id: str,
    payload: UserClearanceUpdate,
    role_service: RoleService = Depends(get_role_service),
    current_user=Depends(_ASSIGN),
):
    return role_service.set_user_clearance(current_user, user_id, payload)
