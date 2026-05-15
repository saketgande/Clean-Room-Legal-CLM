from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.projects.access import get_project_for_user, project_scope_query
from app.projects.models import (
    Project,
    ProjectActivity,
    ProjectContract,
    ProjectFolder,
    ProjectMember,
    ProjectShare,
)
from app.projects.schemas import (
    ProjectContractAdd,
    ProjectContractResponse,
    ProjectContractUpdate,
    ProjectCreate,
    ProjectFolderCreate,
    ProjectFolderResponse,
    ProjectFolderUpdate,
    ProjectMemberResponse,
    ProjectMemberUpsert,
    ProjectResponse,
    ProjectShareCreate,
    ProjectShareResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    return db.scalars(project_scope_query(db, user=current_user).order_by(Project.updated_at.desc())).all()


def _get_project(db: Session, *, project_id: str, current_user, access: str = "read") -> Project:
    return get_project_for_user(db, project_id=project_id, user=current_user, access=access)


def _get_project_folder(
    db: Session,
    *,
    folder_id: str | None,
    project_id: str,
    org_id: str,
    required: bool = False,
) -> ProjectFolder | None:
    if folder_id is None:
        if required:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
        return None
    folder = db.get(ProjectFolder, folder_id)
    if (
        folder is None
        or folder.org_id != org_id
        or folder.project_id != project_id
        or folder.deleted_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    return folder


def _record_project_activity(
    db: Session,
    *,
    project: Project,
    actor_user_id: str,
    activity_type: str,
    title: str,
    details: dict | None = None,
) -> None:
    db.add(
        ProjectActivity(
            org_id=project.org_id,
            project_id=project.id,
            actor_user_id=actor_user_id,
            activity_type=activity_type,
            title=title,
            details=details,
            occurred_at=utcnow(),
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:create")),
):
    project = Project(
        org_id=current_user.org_id,
        name=payload.name,
        description=payload.description,
        project_type=payload.project_type,
        owner_user_id=current_user.id,
        metadata_json=payload.metadata_json,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(project)
    db.flush()
    db.add(
        ProjectMember(
            org_id=current_user.org_id,
            project_id=project.id,
            user_id=current_user.id,
            role="owner",
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
    )
    write_audit_log(
        db,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="project",
        resource_id=project.id,
        event_type="project.created",
        title="Project created",
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    return _get_project(db, project_id=project_id, current_user=current_user)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    project.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="project.updated",
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:delete")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    project.deleted_at = utcnow()
    project.deleted_by_user_id = current_user.id
    project.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="project.deleted",
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )
    db.commit()


@router.get("/{project_id}/folders", response_model=list[ProjectFolderResponse])
def list_project_folders(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    _get_project(db, project_id=project_id, current_user=current_user)
    return db.scalars(
        select(ProjectFolder)
        .where(
            ProjectFolder.org_id == current_user.org_id,
            ProjectFolder.project_id == project_id,
            ProjectFolder.deleted_at.is_(None),
        )
        .order_by(ProjectFolder.name.asc())
    ).all()


@router.post(
    "/{project_id}/folders",
    response_model=ProjectFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_folder(
    project_id: str,
    payload: ProjectFolderCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    _get_project_folder(
        db,
        folder_id=payload.parent_folder_id,
        project_id=project_id,
        org_id=current_user.org_id,
    )
    folder = ProjectFolder(
        org_id=current_user.org_id,
        project_id=project_id,
        parent_folder_id=payload.parent_folder_id,
        name=payload.name,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(folder)
    db.flush()
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.folder_created",
        title="Project folder created",
        details={"folder_id": folder.id, "name": folder.name},
    )
    write_audit_log(
        db,
        action="project.folder_created",
        resource_type="project_folder",
        resource_id=folder.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"project_id": project.id},
    )
    db.commit()
    db.refresh(folder)
    return folder


@router.patch("/{project_id}/folders/{folder_id}", response_model=ProjectFolderResponse)
def update_project_folder(
    project_id: str,
    folder_id: str,
    payload: ProjectFolderUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    folder = _get_project_folder(
        db,
        folder_id=folder_id,
        project_id=project_id,
        org_id=current_user.org_id,
        required=True,
    )
    updates = payload.model_dump(exclude_unset=True)
    if "parent_folder_id" in updates:
        if updates["parent_folder_id"] == folder_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Folder cannot be its own parent")
        _get_project_folder(
            db,
            folder_id=updates["parent_folder_id"],
            project_id=project_id,
            org_id=current_user.org_id,
        )
    for key, value in updates.items():
        setattr(folder, key, value)
    folder.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.folder_updated",
        title="Project folder updated",
        details={"folder_id": folder.id},
    )
    db.commit()
    db.refresh(folder)
    return folder


@router.delete("/{project_id}/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_folder(
    project_id: str,
    folder_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    folder = _get_project_folder(
        db,
        folder_id=folder_id,
        project_id=project_id,
        org_id=current_user.org_id,
        required=True,
    )
    folder.deleted_at = utcnow()
    folder.deleted_by_user_id = current_user.id
    folder.updated_by_user_id = current_user.id
    for project_contract in db.scalars(
        select(ProjectContract).where(
            ProjectContract.org_id == current_user.org_id,
            ProjectContract.project_id == project_id,
            ProjectContract.folder_id == folder_id,
        )
    ):
        project_contract.folder_id = None
        project_contract.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.folder_deleted",
        title="Project folder deleted",
        details={"folder_id": folder.id},
    )
    db.commit()


@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
def list_project_members(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    _get_project(db, project_id=project_id, current_user=current_user)
    return db.scalars(
        select(ProjectMember)
        .where(ProjectMember.org_id == current_user.org_id, ProjectMember.project_id == project_id)
        .order_by(ProjectMember.created_at.asc())
    ).all()


@router.put("/{project_id}/members", response_model=ProjectMemberResponse)
def upsert_project_member(
    project_id: str,
    payload: ProjectMemberUpsert,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="share"
    )
    user = db.get(User, payload.user_id)
    if user is None or user.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.org_id == current_user.org_id,
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == payload.user_id,
        )
    )
    if member is None:
        member = ProjectMember(
            org_id=current_user.org_id,
            project_id=project_id,
            user_id=payload.user_id,
            role=payload.role,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(member)
        action = "project.member_added"
    else:
        member.role = payload.role
        member.updated_by_user_id = current_user.id
        action = "project.member_updated"
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type=action,
        title="Project member updated",
        details={"user_id": payload.user_id, "role": payload.role},
    )
    write_audit_log(
        db,
        action=action,
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"user_id": payload.user_id, "role": payload.role},
    )
    db.commit()
    db.refresh(member)
    return member


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_member(
    project_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="share"
    )
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.org_id == current_user.org_id,
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project member not found")
    if user_id == project.owner_user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Project owner cannot be removed")
    db.delete(member)
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.member_removed",
        title="Project member removed",
        details={"user_id": user_id},
    )
    db.commit()


@router.get("/{project_id}/shares", response_model=list[ProjectShareResponse])
def list_project_shares(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    _get_project(db, project_id=project_id, current_user=current_user, access="share")
    return db.scalars(
        select(ProjectShare)
        .where(
            ProjectShare.org_id == current_user.org_id,
            ProjectShare.project_id == project_id,
            ProjectShare.revoked_at.is_(None),
        )
        .order_by(ProjectShare.created_at.desc())
    ).all()


@router.post(
    "/{project_id}/shares",
    response_model=ProjectShareResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_share(
    project_id: str,
    payload: ProjectShareCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="share"
    )
    user = db.get(User, payload.user_id)
    if user is None or user.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    share = ProjectShare(
        org_id=current_user.org_id,
        project_id=project_id,
        shared_with_user_id=payload.user_id,
        access_level=payload.access_level,
        expires_at=payload.expires_at,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(share)
    db.flush()
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.share_created",
        title="Project share created",
        details={"share_id": share.id, "user_id": payload.user_id, "access_level": payload.access_level},
    )
    write_audit_log(
        db,
        action="project.share_created",
        resource_type="project_share",
        resource_id=share.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"project_id": project_id, "user_id": payload.user_id, "access_level": payload.access_level},
    )
    db.commit()
    db.refresh(share)
    return share


@router.post("/{project_id}/shares/{share_id}/revoke", response_model=ProjectShareResponse)
def revoke_project_share(
    project_id: str,
    share_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="share"
    )
    share = db.get(ProjectShare, share_id)
    if (
        share is None
        or share.org_id != current_user.org_id
        or share.project_id != project_id
        or share.revoked_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project share not found")
    share.revoked_at = utcnow()
    share.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.share_revoked",
        title="Project share revoked",
        details={"share_id": share.id, "user_id": share.shared_with_user_id},
    )
    write_audit_log(
        db,
        action="project.share_revoked",
        resource_type="project_share",
        resource_id=share.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"project_id": project_id, "user_id": share.shared_with_user_id},
    )
    db.commit()
    db.refresh(share)
    return share


@router.get("/{project_id}/contracts", response_model=list[ProjectContractResponse])
def list_project_contracts(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    _get_project(db, project_id=project_id, current_user=current_user)
    return db.scalars(
        select(ProjectContract)
        .where(ProjectContract.org_id == current_user.org_id, ProjectContract.project_id == project_id)
        .order_by(ProjectContract.created_at.asc())
    ).all()


@router.put("/{project_id}/contracts", response_model=ProjectContractResponse)
def add_project_contract(
    project_id: str,
    payload: ProjectContractAdd,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    _get_project_folder(
        db,
        folder_id=payload.folder_id,
        project_id=project_id,
        org_id=current_user.org_id,
    )
    row = db.scalar(
        select(ProjectContract).where(
            ProjectContract.org_id == current_user.org_id,
            ProjectContract.project_id == project_id,
            ProjectContract.contract_id == payload.contract_id,
        )
    )
    if row is None:
        row = ProjectContract(
            org_id=current_user.org_id,
            project_id=project_id,
            contract_id=payload.contract_id,
            folder_id=payload.folder_id,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(row)
        activity_type = "project.contract_added"
    else:
        row.folder_id = payload.folder_id
        row.updated_by_user_id = current_user.id
        activity_type = "project.contract_moved"
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type=activity_type,
        title="Project contract updated",
        details={"contract_id": payload.contract_id, "folder_id": payload.folder_id},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{project_id}/contracts/{contract_id}", response_model=ProjectContractResponse)
def update_project_contract(
    project_id: str,
    contract_id: str,
    payload: ProjectContractUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    _get_project_folder(
        db,
        folder_id=payload.folder_id,
        project_id=project_id,
        org_id=current_user.org_id,
    )
    row = db.scalar(
        select(ProjectContract).where(
            ProjectContract.org_id == current_user.org_id,
            ProjectContract.project_id == project_id,
            ProjectContract.contract_id == contract_id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project contract not found")
    row.folder_id = payload.folder_id
    row.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.contract_moved",
        title="Project contract moved",
        details={"contract_id": contract_id, "folder_id": payload.folder_id},
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{project_id}/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_contract(
    project_id: str,
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, project_id=project_id, current_user=current_user, access="update"
    )
    row = db.scalar(
        select(ProjectContract).where(
            ProjectContract.org_id == current_user.org_id,
            ProjectContract.project_id == project_id,
            ProjectContract.contract_id == contract_id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project contract not found")
    db.delete(row)
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.contract_removed",
        title="Project contract removed",
        details={"contract_id": contract_id},
    )
    db.commit()
