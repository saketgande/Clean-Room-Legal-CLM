from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.core.access import is_org_admin
from app.projects.models import Project, ProjectContract, ProjectMember


def accessible_contract_filter(user: User):
    if is_org_admin(user):
        return true()
    project_membership = (
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectMember,
            (ProjectMember.project_id == ProjectContract.project_id)
            & (ProjectMember.org_id == ProjectContract.org_id),
        )
        .where(
            ProjectContract.contract_id == Contract.id,
            ProjectContract.org_id == user.org_id,
            Project.deleted_at.is_(None),
            ProjectMember.user_id == user.id,
        )
        .exists()
    )
    return or_(
        Contract.owner_user_id == user.id,
        Contract.created_by_user_id == user.id,
        project_membership,
    )


def user_can_access_contract(db: Session, *, contract: Contract, user: User) -> bool:
    if contract.org_id != user.org_id:
        return False
    if is_org_admin(user) or contract.owner_user_id == user.id or contract.created_by_user_id == user.id:
        return True
    return db.scalar(
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectMember,
            (ProjectMember.project_id == ProjectContract.project_id)
            & (ProjectMember.org_id == ProjectContract.org_id),
        )
        .where(
            ProjectContract.org_id == user.org_id,
            ProjectContract.contract_id == contract.id,
            Project.deleted_at.is_(None),
            ProjectMember.user_id == user.id,
        )
        .limit(1)
    ) is not None
