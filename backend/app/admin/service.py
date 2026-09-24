from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import AdminSetting


def serialize_setting(setting: AdminSetting) -> dict:
    # Never return a secret value over the wire — the client masks it in the UI
    # anyway, and shipping the cleartext lets anyone with API access read it.
    return {
        "id": setting.id,
        "org_id": setting.org_id,
        "key": setting.key,
        "value": None if setting.is_secret else setting.value,
        "is_secret": setting.is_secret,
        "created_at": setting.created_at,
        "updated_at": setting.updated_at,
    }


def audit_setting_value(value, is_secret: bool):
    return "<secret>" if is_secret else value


class AdminService:
    def __init__(self, db: Session):
        self.db = db

    def list_settings(self, *, org_id: str) -> list[dict]:
        rows = self.db.scalars(
            select(AdminSetting).where(AdminSetting.org_id == org_id)
        ).all()
        return [serialize_setting(s) for s in rows]
