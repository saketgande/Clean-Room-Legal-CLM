from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.core.enums import PlaybookStatus
from app.playbooks.models import Playbook, PlaybookVersion

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


class PlaybookCreate(BaseModel):
    name: str
    description: str | None = None


@router.get("")
def list_playbooks(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:read")),
):
    return db.scalars(
        select(Playbook).where(Playbook.org_id == current_user.org_id, Playbook.deleted_at.is_(None))
    ).all()


@router.post("")
def create_playbook(
    payload: PlaybookCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:create")),
):
    playbook = Playbook(
        org_id=current_user.org_id,
        name=payload.name,
        description=payload.description,
        status=PlaybookStatus.DRAFT,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(playbook)
    db.flush()
    version = PlaybookVersion(
        org_id=current_user.org_id,
        playbook_id=playbook.id,
        version_number=1,
        status=PlaybookStatus.DRAFT,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(version)
    db.flush()
    playbook.current_version_id = version.id
    db.commit()
    db.refresh(playbook)
    return playbook


@router.post("/{playbook_id}/publish")
def publish_playbook(
    playbook_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:publish")),
):
    playbook = db.get(Playbook, playbook_id)
    if playbook is None or playbook.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Playbook not found")
    version = db.get(PlaybookVersion, playbook.current_version_id)
    playbook.status = PlaybookStatus.PUBLISHED
    if version:
        version.status = PlaybookStatus.PUBLISHED
    playbook.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(playbook)
    return playbook
