import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contract_files.models import (
    ContractEdit,
    ContractFile,
    ContractShare,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contract_files.schemas import (
    ContractFileResponse,
    ContractEditDecisionRequest,
    ContractEditResponse,
    ContractShareCreate,
    ContractShareCreateResponse,
    ContractShareResponse,
    ContractTextSnapshotResponse,
    ContractVersionResponse,
    ExternalShareResponse,
)
from app.contract_files.service import next_version_number, requeue_contract_ai_jobs
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import ContractVersionSource, ShareAccessMode
from app.integrations.storage import storage_service

router = APIRouter(prefix="/contracts/{contract_id}", tags=["contract-files"])
external_share_router = APIRouter(prefix="/external-shares", tags=["external-shares"])

EXTERNAL_TEXT_EXCERPT_CHARS = 12_000


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
    if version.text_snapshot_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Text snapshot not found")
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
    try:
        path = storage_service.path_for_read(storage_object.storage_key)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file bytes not found")
    return FileResponse(path, media_type=storage_object.mime_type, filename=storage_object.filename)


@router.get("/edits", response_model=list[ContractEditResponse])
def list_contract_edits(
    contract_id: str,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:redline")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    query = select(ContractEdit).where(
        ContractEdit.org_id == current_user.org_id,
        ContractEdit.contract_id == contract_id,
    )
    if status_filter:
        query = query.where(ContractEdit.status == status_filter)
    return db.scalars(query.order_by(ContractEdit.created_at.desc())).all()


@router.post("/edits/{edit_id}/accept", response_model=ContractEditResponse)
def accept_contract_edit(
    contract_id: str,
    edit_id: str,
    payload: ContractEditDecisionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:redline")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    edit = _get_contract_edit(db, contract_id=contract_id, edit_id=edit_id, org_id=current_user.org_id)
    if edit.status != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only proposed edits can be accepted")
    proposal_version = _proposal_version_for_edit(db, edit=edit, org_id=current_user.org_id)
    contract_file = db.get(ContractFile, proposal_version.contract_file_id)
    if contract_file is None or contract_file.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract file not found")
    versions = db.scalars(
        select(ContractVersion).where(
            ContractVersion.org_id == current_user.org_id,
            ContractVersion.contract_id == contract_id,
            ContractVersion.deleted_at.is_(None),
        )
    ).all()
    for version in versions:
        version.is_authoritative = version.id == proposal_version.id
        version.updated_by_user_id = current_user.id
    proposal_version.change_summary = _decision_summary(
        proposal_version.change_summary,
        decision="accepted",
        comment=payload.comment,
    )
    contract.current_authoritative_version_id = proposal_version.id
    contract.current_contract_file_id = contract_file.id
    contract_file.current_version_id = proposal_version.id
    edit.status = "accepted"
    edit.updated_by_user_id = current_user.id
    contract.updated_by_user_id = current_user.id
    contract_file.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="contract.edit_accepted",
        resource_type="contract_edit",
        resource_id=edit.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={
            "contract_id": contract_id,
            "contract_version_id": proposal_version.id,
            "comment": payload.comment,
        },
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.edit_accepted",
        title="Assistant tracked change accepted",
        actor_user_id=current_user.id,
        details={"contract_edit_id": edit.id, "contract_version_id": proposal_version.id},
    )
    requeue_contract_ai_jobs(
        db, user=current_user, contract=contract, version=proposal_version
    )
    db.commit()
    db.refresh(edit)
    return edit


@router.post("/edits/{edit_id}/reject", response_model=ContractEditResponse)
def reject_contract_edit(
    contract_id: str,
    edit_id: str,
    payload: ContractEditDecisionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:redline")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    edit = _get_contract_edit(db, contract_id=contract_id, edit_id=edit_id, org_id=current_user.org_id)
    if edit.status != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only proposed edits can be rejected")
    proposal_version = _proposal_version_for_edit(db, edit=edit, org_id=current_user.org_id)
    proposal_version.change_summary = _decision_summary(
        proposal_version.change_summary,
        decision="rejected",
        comment=payload.comment,
    )
    edit.status = "rejected"
    edit.updated_by_user_id = current_user.id
    proposal_version.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="contract.edit_rejected",
        resource_type="contract_edit",
        resource_id=edit.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={
            "contract_id": contract_id,
            "contract_version_id": proposal_version.id,
            "comment": payload.comment,
        },
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.edit_rejected",
        title="Assistant tracked change rejected",
        actor_user_id=current_user.id,
        details={"contract_edit_id": edit.id, "contract_version_id": proposal_version.id},
    )
    db.commit()
    db.refresh(edit)
    return edit


@router.post("/versions/{version_id}/restore", response_model=ContractVersionResponse)
def restore_contract_version(
    contract_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:update")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    version = db.get(ContractVersion, version_id)
    if version is None or version.contract_id != contract_id or version.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
    contract_file = db.get(ContractFile, version.contract_file_id)
    if contract_file is None or contract_file.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract file not found")
    restored_version = ContractVersion(
        org_id=current_user.org_id,
        contract_id=contract_id,
        contract_file_id=contract_file.id,
        version_number=next_version_number(db, contract_file.id),
        storage_object_id=version.storage_object_id,
        source=ContractVersionSource.RESTORED,
        change_summary=f"Restored from version {version.version_number}",
        is_authoritative=True,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(restored_version)
    db.flush()

    source_snapshot = (
        db.get(ContractTextSnapshot, version.text_snapshot_id) if version.text_snapshot_id else None
    )
    if source_snapshot is not None:
        restored_snapshot = ContractTextSnapshot(
            org_id=current_user.org_id,
            contract_id=contract_id,
            contract_version_id=restored_version.id,
            extraction_method=source_snapshot.extraction_method,
            extraction_quality_score=source_snapshot.extraction_quality_score,
            text=source_snapshot.text,
            page_map=source_snapshot.page_map,
            ocr_provider=source_snapshot.ocr_provider,
            validation_status=source_snapshot.validation_status,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(restored_snapshot)
        db.flush()
        restored_version.text_snapshot_id = restored_snapshot.id

    existing_versions = db.scalars(
        select(ContractVersion).where(
            ContractVersion.org_id == current_user.org_id,
            ContractVersion.contract_id == contract_id,
            ContractVersion.deleted_at.is_(None),
        )
    ).all()
    for row in existing_versions:
        row.is_authoritative = row.id == restored_version.id
        row.updated_by_user_id = current_user.id
    contract_file.current_version_id = restored_version.id
    contract_file.updated_by_user_id = current_user.id
    contract.current_contract_file_id = contract_file.id
    contract.current_authoritative_version_id = restored_version.id
    contract.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="contract.version_restored",
        resource_type="contract_version",
        resource_id=restored_version.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={
            "contract_id": contract.id,
            "contract_file_id": contract_file.id,
            "restored_from_version_id": version.id,
            "restored_from_version_number": version.version_number,
        },
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.version_restored",
        title="Contract version restored",
        actor_user_id=current_user.id,
        details={
            "contract_version_id": restored_version.id,
            "version_number": restored_version.version_number,
            "restored_from_version_id": version.id,
            "restored_from_version_number": version.version_number,
        },
    )
    requeue_contract_ai_jobs(
        db, user=current_user, contract=contract, version=restored_version
    )
    db.commit()
    db.refresh(restored_version)
    return restored_version


@router.delete("/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract_version(
    contract_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:delete")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    version = db.get(ContractVersion, version_id)
    if version is None or version.contract_id != contract_id or version.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
    if version.is_authoritative:
        raise HTTPException(status.HTTP_409_CONFLICT, "Authoritative version cannot be deleted")
    contract_file = db.get(ContractFile, version.contract_file_id)
    if contract_file is not None and contract_file.current_version_id == version_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Current file version cannot be deleted")

    version.deleted_at = utcnow()
    version.deleted_by_user_id = current_user.id
    version.updated_by_user_id = current_user.id
    if version.text_snapshot_id:
        snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id)
        if snapshot is not None and snapshot.deleted_at is None:
            snapshot.deleted_at = version.deleted_at
            snapshot.deleted_by_user_id = current_user.id
            snapshot.updated_by_user_id = current_user.id

    active_storage_refs = db.scalar(
        select(func.count(ContractVersion.id)).where(
            ContractVersion.org_id == current_user.org_id,
            ContractVersion.storage_object_id == version.storage_object_id,
            ContractVersion.deleted_at.is_(None),
            ContractVersion.id != version.id,
        )
    )
    if not active_storage_refs:
        storage_object = db.get(StorageObject, version.storage_object_id)
        if storage_object is not None and storage_object.deleted_at is None:
            storage_object.deleted_at = version.deleted_at
            storage_object.deleted_by_user_id = current_user.id
            storage_object.updated_by_user_id = current_user.id

    write_audit_log(
        db,
        action="contract.version_deleted",
        resource_type="contract_version",
        resource_id=version.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"contract_id": contract_id, "storage_object_id": version.storage_object_id},
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="contract",
        resource_id=contract_id,
        event_type="contract.version_deleted",
        title="Contract version deleted",
        actor_user_id=current_user.id,
        details={"contract_version_id": version.id, "version_number": version.version_number},
    )
    db.commit()


@router.get("/shares", response_model=list[ContractShareResponse])
def list_contract_shares(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:share")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return db.scalars(
        select(ContractShare)
        .where(
            ContractShare.org_id == current_user.org_id,
            ContractShare.contract_id == contract_id,
            ContractShare.deleted_at.is_(None),
        )
        .order_by(ContractShare.created_at.desc())
    ).all()


@router.post(
    "/shares",
    response_model=ContractShareCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_contract_share(
    contract_id: str,
    payload: ContractShareCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:share")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    if payload.contract_version_id:
        version = db.get(ContractVersion, payload.contract_version_id)
        if version is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
    elif contract.current_authoritative_version_id:
        version = db.get(ContractVersion, contract.current_authoritative_version_id)
    else:
        version = None
    if version is not None and (version.org_id != current_user.org_id or version.contract_id != contract_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
    if payload.access_mode == ShareAccessMode.VIEW_ONLY and payload.download_allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "View-only shares cannot allow downloads")
    token = secrets.token_urlsafe(32)
    share = ContractShare(
        org_id=current_user.org_id,
        contract_id=contract_id,
        contract_version_id=version.id if version else None,
        token_hash=_hash_secret(token),
        passcode_hash=_hash_secret(payload.passcode) if payload.passcode else None,
        access_mode=payload.access_mode,
        expires_at=payload.expires_at,
        download_allowed=payload.download_allowed or payload.access_mode == ShareAccessMode.DOWNLOAD_ALLOWED,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(share)
    db.flush()
    write_audit_log(
        db,
        action="contract.share_created",
        resource_type="contract_share",
        resource_id=share.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={
            "contract_id": contract_id,
            "contract_version_id": share.contract_version_id,
            "download_allowed": share.download_allowed,
            "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        },
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="contract",
        resource_id=contract_id,
        event_type="contract.share_created",
        title="Contract share created",
        actor_user_id=current_user.id,
        details={"share_id": share.id, "download_allowed": share.download_allowed},
    )
    db.commit()
    db.refresh(share)
    return {"share": share, "token": token}


@router.post("/shares/{share_id}/revoke", response_model=ContractShareResponse)
def revoke_contract_share(
    contract_id: str,
    share_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:share")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    share = db.get(ContractShare, share_id)
    if (
        share is None
        or share.org_id != current_user.org_id
        or share.contract_id != contract_id
        or share.deleted_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract share not found")
    share.revoked_at = utcnow()
    share.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="contract.share_revoked",
        resource_type="contract_share",
        resource_id=share.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"contract_id": contract_id},
    )
    db.commit()
    db.refresh(share)
    return share


@external_share_router.get("/{token}", response_model=ExternalShareResponse)
def view_external_share(
    token: str,
    passcode: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    share = _get_active_share(db, token=token, passcode=passcode)
    contract = db.get(Contract, share.contract_id)
    if contract is None or contract.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shared contract not found")
    version = _share_version(db, share=share, contract=contract)
    storage_object = db.get(StorageObject, version.storage_object_id) if version else None
    snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version and version.text_snapshot_id else None
    text = snapshot.text if snapshot else ""
    write_audit_log(
        db,
        action="contract.external_share_viewed",
        resource_type="contract_share",
        resource_id=share.id,
        org_id=share.org_id,
        metadata={"contract_id": contract.id, "contract_version_id": version.id if version else None},
    )
    db.commit()
    return ExternalShareResponse(
        contract_id=contract.id,
        contract_version_id=version.id if version else None,
        title=contract.title,
        filename=storage_object.filename if storage_object else None,
        access_mode=share.access_mode,
        download_allowed=share.download_allowed,
        text_excerpt=text[:EXTERNAL_TEXT_EXCERPT_CHARS] if text else None,
        text_truncated=len(text) > EXTERNAL_TEXT_EXCERPT_CHARS,
    )


@external_share_router.get("/{token}/download")
def download_external_share(
    token: str,
    passcode: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    share = _get_active_share(db, token=token, passcode=passcode)
    if not share.download_allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Download is disabled for this share")
    contract = db.get(Contract, share.contract_id)
    if contract is None or contract.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shared contract not found")
    version = _share_version(db, share=share, contract=contract)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shared contract version not found")
    storage_object = db.get(StorageObject, version.storage_object_id)
    if storage_object is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file not found")
    try:
        path = storage_service.path_for_read(storage_object.storage_key)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file bytes not found")
    write_audit_log(
        db,
        action="contract.external_share_downloaded",
        resource_type="contract_share",
        resource_id=share.id,
        org_id=share.org_id,
        metadata={"contract_id": contract.id, "contract_version_id": version.id},
    )
    db.commit()
    return FileResponse(path, media_type=storage_object.mime_type, filename=storage_object.filename)


def _get_active_share(db: Session, *, token: str, passcode: str | None) -> ContractShare:
    share = db.scalar(
        select(ContractShare).where(
            ContractShare.token_hash == _hash_secret(token),
            ContractShare.deleted_at.is_(None),
        )
    )
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")
    now = utcnow()
    if share.revoked_at is not None or (share.expires_at is not None and share.expires_at < now):
        raise HTTPException(status.HTTP_410_GONE, "Share is no longer active")
    if share.passcode_hash and not secrets.compare_digest(
        _hash_secret(passcode or ""), share.passcode_hash
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Passcode required")
    return share


def _share_version(db: Session, *, share: ContractShare, contract: Contract) -> ContractVersion | None:
    version_id = share.contract_version_id or contract.current_authoritative_version_id
    if not version_id:
        return None
    version = db.get(ContractVersion, version_id)
    if version is None or version.org_id != share.org_id or version.contract_id != contract.id:
        return None
    return version


def _get_contract_edit(db: Session, *, contract_id: str, edit_id: str, org_id: str) -> ContractEdit:
    edit = db.get(ContractEdit, edit_id)
    if edit is None or edit.org_id != org_id or edit.contract_id != contract_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract edit not found")
    return edit


def _proposal_version_for_edit(db: Session, *, edit: ContractEdit, org_id: str) -> ContractVersion:
    proposal_version_id = None
    for citation in edit.citation or []:
        if isinstance(citation, dict) and citation.get("type") == "assistant_edit_version":
            proposal_version_id = citation.get("contract_version_id")
            break
    if not proposal_version_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Edit has no assistant version to apply")
    version = db.get(ContractVersion, proposal_version_id)
    if version is None or version.org_id != org_id or version.contract_id != edit.contract_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant edit version not found")
    return version


def _decision_summary(summary: str | None, *, decision: str, comment: str | None) -> str:
    suffix = f"Assistant edit {decision}."
    if comment:
        suffix = f"{suffix} Comment: {comment}"
    if not summary:
        return suffix
    return f"{summary}\n{suffix}"


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
