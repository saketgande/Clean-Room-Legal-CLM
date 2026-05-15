from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.access import is_org_admin
from app.projects.models import Project, ProjectMember

PROJECT_UPDATE_ROLES = {"owner", "manager", "editor"}
PROJECT_SHARE_ROLES = {"owner", "manager"}


def project_scope_query(db: Session, *, user: User):
    query = select(Project).where(Project.org_id == user.org_id, Project.deleted_at.is_(None))
    if is_org_admin(user):
        return query
    member_project_ids = select(ProjectMember.project_id).where(
        ProjectMember.org_id == user.org_id,
        ProjectMember.user_id == user.id,
    )
    return query.where(or_(Project.owner_user_id == user.id, Project.id.in_(member_project_ids)))


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
        return False
    if access == "read":
        return True
    if access == "update":
        return role in PROJECT_UPDATE_ROLES
    if access == "share":
        return role in PROJECT_SHARE_ROLES
    return False
