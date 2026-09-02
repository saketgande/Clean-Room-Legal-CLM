from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.matters.models import Matter, MatterMember, MatterShare

PROJECT_UPDATE_ROLES = {"owner", "manager", "editor"}
PROJECT_SHARE_ROLES = {"owner", "manager"}
PROJECT_UPDATE_SHARE_LEVELS = {"update", "share"}
PROJECT_SHARE_SHARE_LEVELS = {"share"}


def project_scope_query(db: Session, *, user: User):
    query = select(Matter).where(Matter.org_id == user.org_id, Matter.deleted_at.is_(None))
    if is_org_admin(user):
        return query
    member_project_ids = select(MatterMember.matter_id).where(
        MatterMember.org_id == user.org_id,
        MatterMember.user_id == user.id,
    )
    shared_project_ids = select(MatterShare.matter_id).where(
        MatterShare.org_id == user.org_id,
        MatterShare.shared_with_user_id == user.id,
        MatterShare.revoked_at.is_(None),
        or_(MatterShare.expires_at.is_(None), MatterShare.expires_at > utcnow()),
    )
    return query.where(
        or_(
            Matter.owner_user_id == user.id,
            Matter.id.in_(member_project_ids),
            Matter.id.in_(shared_project_ids),
        )
    )


def get_project_for_user(
    db: Session,
    *,
    matter_id: str,
    user: User,
    access: str = "read",
) -> Matter:
    project = db.get(Matter, matter_id)
    if project is None or project.org_id != user.org_id or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter not found")
    if not user_can_access_project(db, project=project, user=user, access=access):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter not found")
    return project


def user_can_access_project(
    db: Session,
    *,
    project: Matter,
    user: User,
    access: str = "read",
) -> bool:
    if project.org_id != user.org_id:
        return False
    if is_org_admin(user) or project.owner_user_id == user.id:
        return True

    role = db.scalar(
        select(MatterMember.role).where(
            MatterMember.org_id == user.org_id,
            MatterMember.matter_id == project.id,
            MatterMember.user_id == user.id,
        )
    )
    if role is None:
        share_level = _active_share_level(db, project=project, user=user)
        if share_level is None:
            return False
        if access == "read":
            return True
        if access == "update":
            return share_level in PROJECT_UPDATE_SHARE_LEVELS
        if access == "share":
            return share_level in PROJECT_SHARE_SHARE_LEVELS
        return False
    if access == "read":
        return True
    if access == "update":
        return role in PROJECT_UPDATE_ROLES
    if access == "share":
        return role in PROJECT_SHARE_ROLES
    return False


def user_has_project_access_for_contract(db: Session, *, contract_id: str, user: User) -> bool:
    from app.matters.models import MatterContract

    membership = (
        select(MatterContract.id)
        .join(Matter, Matter.id == MatterContract.matter_id)
        .join(
            MatterMember,
            (MatterMember.matter_id == MatterContract.matter_id)
            & (MatterMember.org_id == MatterContract.org_id),
        )
        .where(
            MatterContract.org_id == user.org_id,
            MatterContract.contract_id == contract_id,
            Matter.deleted_at.is_(None),
            MatterMember.user_id == user.id,
        )
        .exists()
    )
    explicit_share = (
        select(MatterContract.id)
        .join(Matter, Matter.id == MatterContract.matter_id)
        .join(
            MatterShare,
            (MatterShare.matter_id == MatterContract.matter_id)
            & (MatterShare.org_id == MatterContract.org_id),
        )
        .where(
            MatterContract.org_id == user.org_id,
            MatterContract.contract_id == contract_id,
            Matter.deleted_at.is_(None),
            MatterShare.shared_with_user_id == user.id,
            MatterShare.revoked_at.is_(None),
            or_(MatterShare.expires_at.is_(None), MatterShare.expires_at > utcnow()),
        )
        .exists()
    )
    return bool(db.scalar(select(or_(membership, explicit_share))))


def _active_share_level(db: Session, *, project: Matter, user: User) -> str | None:
    return db.scalar(
        select(MatterShare.access_level)
        .where(
            MatterShare.org_id == user.org_id,
            MatterShare.matter_id == project.id,
            MatterShare.shared_with_user_id == user.id,
            MatterShare.revoked_at.is_(None),
            or_(MatterShare.expires_at.is_(None), MatterShare.expires_at > utcnow()),
        )
        .order_by(MatterShare.created_at.desc())
        .limit(1)
    )
