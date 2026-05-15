from sqlalchemy.orm import Session

from app.core.models import UsageRecord


def add_usage_record(
    db: Session,
    *,
    org_id: str,
    user_id: str | None,
    resource_type: str,
    resource_id: str | None,
    metric: str,
    quantity: float,
    metadata: dict | None = None,
) -> UsageRecord:
    row = UsageRecord(
        org_id=org_id,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        metric=metric,
        quantity=quantity,
        metadata_json=metadata,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )
    db.add(row)
    return row
