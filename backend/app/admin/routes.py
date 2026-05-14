from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.core.models import AdminSetting

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminSettingUpsert(BaseModel):
    key: str
    value: dict | list | str | int | float | bool | None
    is_secret: bool = False


@router.get("/settings")
def list_settings(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    return db.scalars(select(AdminSetting).where(AdminSetting.org_id == current_user.org_id)).all()


@router.put("/settings")
def upsert_setting(
    payload: AdminSettingUpsert,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    setting = db.scalar(
        select(AdminSetting).where(
            AdminSetting.org_id == current_user.org_id,
            AdminSetting.key == payload.key,
        )
    )
    if setting is None:
        setting = AdminSetting(
            org_id=current_user.org_id,
            key=payload.key,
            created_by_user_id=current_user.id,
        )
        db.add(setting)
    setting.value = payload.value
    setting.is_secret = payload.is_secret
    setting.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(setting)
    return setting
