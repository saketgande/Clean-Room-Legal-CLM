from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.projects.models import Project, ProjectContract, ProjectMember, ProjectShare


def accessible_contract_filter(user: User):
    # F-03: org scoping is ALWAYS enforced, including for admins. Previously this
    # returned true() for admins, which dropped the tenant boundary entirely and
    # leaked cross-org rows through any caller that JOINed Contract without its own
    # org_id filter. Admins now escalate WITHIN their org, never across it — matching
    # the row-level guard in user_can_access_contract().
    org_scope = Contract.org_id == user.org_id
    if is_org_admin(user):
        return org_scope
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
    project_share = (
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectShare,
            (ProjectShare.project_id == ProjectContract.project_id)
            & (ProjectShare.org_id == ProjectContract.org_id),
        )
        .where(
            ProjectContract.contract_id == Contract.id,
            ProjectContract.org_id == user.org_id,
            Project.deleted_at.is_(None),
            ProjectShare.shared_with_user_id == user.id,
            ProjectShare.revoked_at.is_(None),
            or_(ProjectShare.expires_at.is_(None), ProjectShare.expires_at > utcnow()),
        )
        .exists()
    )
    return and_(
        org_scope,
        or_(
            Contract.owner_user_id == user.id,
            Contract.created_by_user_id == user.id,
            project_membership,
            project_share,
        ),
    )


def user_can_access_contract(db: Session, *, contract: Contract, user: User) -> bool:
    if contract.org_id != user.org_id:
        return False
    if is_org_admin(user) or contract.owner_user_id == user.id or contract.created_by_user_id == user.id:
        return True
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
            ProjectContract.contract_id == contract.id,
            Project.deleted_at.is_(None),
            ProjectMember.user_id == user.id,
        )
        .limit(1)
    )
    if db.scalar(membership) is not None:
        return True
    return db.scalar(
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectShare,
            (ProjectShare.project_id == ProjectContract.project_id)
            & (ProjectShare.org_id == ProjectContract.org_id),
        )
        .where(
            ProjectContract.org_id == user.org_id,
            ProjectContract.contract_id == contract.id,
            Project.deleted_at.is_(None),
            ProjectShare.shared_with_user_id == user.id,
            ProjectShare.revoked_at.is_(None),
            or_(ProjectShare.expires_at.is_(None), ProjectShare.expires_at > utcnow()),
        )
        .limit(1)
    ) is not None
