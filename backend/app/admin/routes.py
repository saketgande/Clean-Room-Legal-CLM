from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit_log
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
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    setting = db.scalar(
        select(AdminSetting).where(
            AdminSetting.org_id == current_user.org_id,
            AdminSetting.key == payload.key,
        )
    )
    before = None if setting is None else _audit_setting_value(setting.value, setting.is_secret)
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
    write_audit_log(
        db,
        action="admin.setting_updated",
        resource_type="admin_setting",
        resource_id=payload.key,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        request_id=getattr(request.state, "request_id", None),
        before={"key": payload.key, "value": before},
        after={
            "key": payload.key,
            "value": _audit_setting_value(payload.value, payload.is_secret),
            "is_secret": payload.is_secret,
        },
    )
    db.commit()
    db.refresh(setting)
    return setting


def _audit_setting_value(value, is_secret: bool):
    return "<secret>" if is_secret else value
