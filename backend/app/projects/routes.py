from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit_log, write_timeline_event
from app.core.deps import get_db, require_permission
from app.projects.models import Project, ProjectMember
from app.projects.schemas import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    return db.scalars(
        select(Project)
        .where(Project.org_id == current_user.org_id, Project.deleted_at.is_(None))
        .order_by(Project.updated_at.desc())
    ).all()


@router.post("", response_model=ProjectResponse)
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
    project = db.get(Project, project_id)
    if project is None or project.org_id != current_user.org_id or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = db.get(Project, project_id)
    if project is None or project.org_id != current_user.org_id or project.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
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
