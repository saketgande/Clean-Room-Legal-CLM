from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.projects.models import Project, ProjectMember, ProjectShare

PROJECT_UPDATE_ROLES = {"owner", "manager", "editor"}
PROJECT_SHARE_ROLES = {"owner", "manager"}
PROJECT_UPDATE_SHARE_LEVELS = {"update", "share"}
PROJECT_SHARE_SHARE_LEVELS = {"share"}


def project_scope_query(db: Session, *, user: User):
    query = select(Project).where(Project.org_id == user.org_id, Project.deleted_at.is_(None))
    if is_org_admin(user):
        return query
    member_project_ids = select(ProjectMember.project_id).where(
        ProjectMember.org_id == user.org_id,
        ProjectMember.user_id == user.id,
    )
    shared_project_ids = select(ProjectShare.project_id).where(
        ProjectShare.org_id == user.org_id,
        ProjectShare.shared_with_user_id == user.id,
        ProjectShare.revoked_at.is_(None),
        or_(ProjectShare.expires_at.is_(None), ProjectShare.expires_at > utcnow()),
    )
    return query.where(
        or_(
            Project.owner_user_id == user.id,
            Project.id.in_(member_project_ids),
            Project.id.in_(shared_project_ids),
        )
    )


def get_project_for_user(
    db: Session,
    *,
    project_id: str,
    user: User,
    access: str = "read",
) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.org_id != user.org_id or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if not user_can_access_project(db, project=project, user=user, access=access):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def user_can_access_project(
    db: Session,
    *,
    project: Project,
    user: User,
    access: str = "read",
) -> bool:
    if project.org_id != user.org_id:
        return False
    if is_org_admin(user) or project.owner_user_id == user.id:
        return True

    role = db.scalar(
        select(ProjectMember.role).where(
            ProjectMember.org_id == user.org_id,
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
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
    from app.projects.models import ProjectContract

    membership = (
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectMember,
            (ProjectMember.project_id == ProjectContract.project_id)
            & (ProjectMember.org_id == ProjectContract.org_id),
        )
        .where(
            ProjectContract.org_id == user.org_id,
            ProjectContract.contract_id == contract_id,
            Project.deleted_at.is_(None),
            ProjectMember.user_id == user.id,
        )
        .exists()
    )
    explicit_share = (
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectShare,
            (ProjectShare.project_id == ProjectContract.project_id)
            & (ProjectShare.org_id == ProjectContract.org_id),
        )
        .where(
            ProjectContract.org_id == user.org_id,
            ProjectContract.contract_id == contract_id,
            Project.deleted_at.is_(None),
            ProjectShare.shared_with_user_id == user.id,
            ProjectShare.revoked_at.is_(None),
            or_(ProjectShare.expires_at.is_(None), ProjectShare.expires_at > utcnow()),
        )
        .exists()
    )
    return bool(db.scalar(select(or_(membership, explicit_share))))


def _active_share_level(db: Session, *, project: Project, user: User) -> str | None:
    return db.scalar(
        select(ProjectShare.access_level)
        .where(
            ProjectShare.org_id == user.org_id,
            ProjectShare.project_id == project.id,
            ProjectShare.shared_with_user_id == user.id,
            ProjectShare.revoked_at.is_(None),
            or_(ProjectShare.expires_at.is_(None), ProjectShare.expires_at > utcnow()),
        )
        .order_by(ProjectShare.created_at.desc())
        .limit(1)
    )
