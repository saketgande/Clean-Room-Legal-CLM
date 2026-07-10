from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.walls import service
from app.walls.schemas import WallCreate, WallResponse, WallUpdate

router = APIRouter(prefix="/ethical-walls", tags=["ethical-walls"])

# Ethical walls are a compliance/admin control; gate on the admin-settings
# capability (the same gate role administration uses).
_MANAGE = require_permission("admin_panel:access")


@router.get("", response_model=list[WallResponse])
def list_walls(db: Session = Depends(get_db), current_user=Depends(_MANAGE)):
    return service.list_walls(db, org_id=current_user.org_id)


@router.post("", response_model=WallResponse, status_code=status.HTTP_201_CREATED)
def create_wall(
    payload: WallCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.create_wall(db, actor=current_user, payload=payload)


@router.patch("/{wall_id}", response_model=WallResponse)
def update_wall(
    wall_id: str,
    payload: WallUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    return service.update_wall(db, actor=current_user, wall_id=wall_id, payload=payload)


@router.delete("/{wall_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wall(
    wall_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(_MANAGE),
):
    service.delete_wall(db, actor=current_user, wall_id=wall_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
