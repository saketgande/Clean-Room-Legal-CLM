from fastapi import APIRouter, Depends, Response, status

from app.core.deps import require_permission
from app.walls.dependencies import get_wall_service
from app.walls.schemas import WallCreate, WallResponse, WallUpdate
from app.walls.service import WallService

router = APIRouter(prefix="/ethical-walls", tags=["ethical-walls"])

# Ethical walls are a compliance/admin control; gate on the admin-settings
# capability (the same gate role administration uses).
_MANAGE = require_permission("admin_panel:access")


@router.get("", response_model=list[WallResponse])
def list_walls(
    wall_service: WallService = Depends(get_wall_service),
    current_user=Depends(_MANAGE),
):
    return wall_service.list_walls(org_id=current_user.org_id)


@router.post("", response_model=WallResponse, status_code=status.HTTP_201_CREATED)
def create_wall(
    payload: WallCreate,
    wall_service: WallService = Depends(get_wall_service),
    current_user=Depends(_MANAGE),
):
    return wall_service.create_wall(actor=current_user, payload=payload)


@router.patch("/{wall_id}", response_model=WallResponse)
def update_wall(
    wall_id: str,
    payload: WallUpdate,
    wall_service: WallService = Depends(get_wall_service),
    current_user=Depends(_MANAGE),
):
    return wall_service.update_wall(actor=current_user, wall_id=wall_id, payload=payload)


@router.delete("/{wall_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wall(
    wall_id: str,
    wall_service: WallService = Depends(get_wall_service),
    current_user=Depends(_MANAGE),
):
    wall_service.delete_wall(actor=current_user, wall_id=wall_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
