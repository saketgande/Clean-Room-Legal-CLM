from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contract_files.schemas import (
    ContractFileResponse,
    ContractTextSnapshotResponse,
    ContractVersionResponse,
)
from app.contracts.service import get_contract_for_user
from app.core.config import settings
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/contracts/{contract_id}", tags=["contract-files"])


@router.get("/files", response_model=list[ContractFileResponse])
def list_contract_files(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return db.scalars(
        select(ContractFile).where(
            ContractFile.org_id == current_user.org_id,
            ContractFile.contract_id == contract_id,
            ContractFile.deleted_at.is_(None),
        )
    ).all()


@router.get("/versions", response_model=list[ContractVersionResponse])
def list_contract_versions(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return db.scalars(
        select(ContractVersion)
        .where(
            ContractVersion.org_id == current_user.org_id,
            ContractVersion.contract_id == contract_id,
            ContractVersion.deleted_at.is_(None),
        )
        .order_by(ContractVersion.version_number.asc())
    ).all()


@router.get("/versions/{version_id}/text", response_model=ContractTextSnapshotResponse)
def get_version_text_snapshot(
    contract_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    version = db.get(ContractVersion, version_id)
    if version is None or version.contract_id != contract_id or version.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
    snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Text snapshot not found")
    return snapshot


@router.get("/versions/{version_id}/download")
def download_contract_version(
    contract_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    version = db.get(ContractVersion, version_id)
    if version is None or version.contract_id != contract_id or version.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
    storage_object = db.get(StorageObject, version.storage_object_id)
    if storage_object is None or storage_object.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file not found")
    path = Path(settings.storage_root) / storage_object.storage_key
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file bytes not found")
    return FileResponse(path, media_type=storage_object.mime_type, filename=storage_object.filename)
