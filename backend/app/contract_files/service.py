import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contract_files.text_extraction import extract_text
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.enums import ContractLifecycleStage, ContractVersionSource, StorageBackend
from app.integrations.reducto import reducto_client
from app.integrations.storage import storage_service
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.projects.access import get_project_for_user
from app.projects.models import ProjectContract


logger = logging.getLogger(__name__)


# Magic-byte signatures for the MIME types we accept. Used to refuse a file
# whose declared content-type doesn't match the actual bytes — the client's
# content-type header alone is untrustworthy.
_MAGIC_BYTE_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    # DOCX, XLSX, PPTX are ZIP-based.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    # Legacy .doc — OLE compound document magic.
    "application/msword": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    # text/plain has no magic — accepted by default.
}


def _sniff_mime_type(content: bytes, claimed: str) -> str:
    """Return the canonical mime type for ``content``, or the claimed one
    if we don't have a signature on file.

    Raises HTTPException(415) on a clear mismatch.
    """
    # text/* has no reliable magic; accept the claim.
    if claimed.startswith("text/"):
        return claimed
    signatures = _MAGIC_BYTE_SIGNATURES.get(claimed)
    if signatures is None:
        return claimed
    head = content[: max(len(sig) for sig in signatures)]
    if not any(head.startswith(sig) for sig in signatures):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Uploaded file bytes do not match the declared {claimed} content-type.",
        )
    return claimed


async def _read_upload_with_limit(upload: UploadFile, *, limit: int, chunk_size: int) -> bytes:
    """Read the upload in chunks, refusing once we cross ``limit`` bytes.

    Replaces the previous ``await upload.read()`` which buffered the entire
    stream before the size check — a 5 GB body was fully resident before
    rejection. Now we cut off as soon as the threshold is exceeded.
    """
    buffer = bytearray()
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        if len(buffer) + len(chunk) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "Upload exceeds size limit",
            )
        buffer.extend(chunk)
    return bytes(buffer)


INITIAL_CONTRACT_AI_JOB_TYPES = (
    "metadata_extraction",
    "clause_extraction",
    "embeddings",
)
TEXT_EXTRACTION_COMPLETE_THRESHOLD = 0.55


async def create_contract_from_upload(
    db: Session,
    *,
    upload: UploadFile,
    user: User,
    project_id: str | None = None,
    title: str | None = None,
    counterparty_name: str | None = None,
    request_id: str | None = None,
) -> dict:
    mime_type = upload.content_type or "application/octet-stream"
    if mime_type not in settings.allowed_mime_types:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported MIME type: {mime_type}")

    content = await _read_upload_with_limit(
        upload,
        limit=settings.max_upload_size_bytes,
        chunk_size=settings.upload_stream_chunk_bytes,
    )
    # Re-verify the MIME against the actual bytes — the client's content-type
    # header is untrustworthy. A user with contract:create could otherwise
    # upload arbitrary bytes labelled as application/pdf.
    mime_type = _sniff_mime_type(content, mime_type)

    if project_id:
        get_project_for_user(db, project_id=project_id, user=user, access="update")

    stored = storage_service.save_bytes(
        org_id=user.org_id,
        filename=upload.filename or "contract",
        mime_type=mime_type,
        content=content,
    )
    try:
        storage_object = StorageObject(
            org_id=user.org_id,
            storage_key=stored.storage_key,
            filename=stored.filename,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            sha256_hash=stored.sha256_hash,
            storage_backend=StorageBackend.LOCAL_VOLUME,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(storage_object)
        db.flush()

        contract = Contract(
            org_id=user.org_id,
            title=title or stored.filename,
            counterparty_name=counterparty_name,
            lifecycle_stage=ContractLifecycleStage.INTAKE,
            owner_user_id=user.id,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(contract)
        db.flush()

        contract_file = ContractFile(
            org_id=user.org_id,
            contract_id=contract.id,
            file_label=stored.filename,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(contract_file)
        db.flush()

        version = ContractVersion(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_file_id=contract_file.id,
            version_number=1,
            storage_object_id=storage_object.id,
            source=ContractVersionSource.UPLOAD,
            change_summary="Original upload",
            is_authoritative=True,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(version)
        db.flush()

        extraction = extract_text(content, mime_type=mime_type, filename=stored.filename)
        ocr_provider = None
        ocr_error = None
        if extraction.needs_ocr:
            try:
                ocr = await reducto_client.extract_text(
                    filename=stored.filename,
                    mime_type=mime_type,
                    content=content,
                )
            except Exception as exc:
                extraction_method = f"{extraction.method}_ocr_failed"
                extracted_text = extraction.text
                quality_score = extraction.quality_score
                ocr_provider = reducto_client.provider
                ocr_error = str(exc)
            else:
                if ocr.text:
                    extraction_method = "reducto_ocr"
                    extracted_text = ocr.text
                    quality_score = ocr.quality_score
                    ocr_provider = ocr.provider
                else:
                    extraction_method = extraction.method
                    extracted_text = extraction.text
                    quality_score = extraction.quality_score
                    ocr_provider = ocr.provider
        else:
            extraction_method = extraction.method
            extracted_text = extraction.text
            quality_score = extraction.quality_score

        snapshot = ContractTextSnapshot(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_version_id=version.id,
            extraction_method=extraction_method,
            extraction_quality_score=quality_score,
            text=extracted_text,
            page_map=extraction.page_map,
            ocr_provider=ocr_provider,
            validation_status=_text_snapshot_validation_status(extracted_text, quality_score),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(snapshot)
        db.flush()

        version.text_snapshot_id = snapshot.id
        contract_file.current_version_id = version.id
        contract.current_contract_file_id = contract_file.id
        contract.current_authoritative_version_id = version.id

        if project_id:
            db.add(
                ProjectContract(
                    org_id=user.org_id,
                    project_id=project_id,
                    contract_id=contract.id,
                    created_by_user_id=user.id,
                    updated_by_user_id=user.id,
                )
            )

        queued_jobs = _queue_initial_contract_jobs(
            db, user=user, contract=contract, version=version, snapshot=snapshot
        )

        write_audit_log(
            db,
            action="contract.uploaded",
            resource_type="contract",
            resource_id=contract.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            request_id=request_id,
            after={
                "contract_file_id": contract_file.id,
                "contract_version_id": version.id,
                "storage_object_id": storage_object.id,
            },
        )
        timeline_details = {
            "filename": stored.filename,
            "mime_type": mime_type,
            "extraction_method": extraction_method,
            "extraction_quality_score": quality_score,
            "validation_status": snapshot.validation_status,
        }
        if ocr_error:
            timeline_details["ocr_error"] = ocr_error
        write_timeline_event(
            db,
            org_id=user.org_id,
            resource_type="contract",
            resource_id=contract.id,
            event_type="contract.uploaded",
            title="Contract uploaded",
            actor_user_id=user.id,
            request_id=request_id,
            details=timeline_details,
        )
        db.commit()
    except Exception:
        db.rollback()
        storage_service.delete_bytes_permanently(stored.storage_key)
        raise

    queued_job_ids = [job.id for job in queued_jobs]
    queued_job_types = [job.job_type for job in queued_jobs]

    dispatched_job_types = []
    dispatch_errors = []
    for job_id in queued_job_ids:
        job = db.get(JobRun, job_id)
        if job is None:
            continue
        try:
            dispatch_job(db, job=job)
            dispatched_job_types.append(job.job_type)
        except Exception as exc:
            dispatch_errors.append({"job_id": job_id, "job_type": job.job_type, "error": str(exc)})
    if dispatched_job_types or dispatch_errors:
        write_timeline_event(
            db,
            org_id=user.org_id,
            resource_type="contract",
            resource_id=contract.id,
            event_type="contract.ai_jobs_dispatched",
            title="Contract AI jobs dispatched",
            actor_user_id=user.id,
            request_id=request_id,
            details={
                "dispatched_job_types": dispatched_job_types,
                "dispatch_errors": dispatch_errors,
            },
        )
        db.commit()
    db.refresh(contract)
    return {
        "contract": contract,
        "contract_file_id": contract_file.id,
        "contract_version_id": version.id,
        "text_snapshot_id": snapshot.id,
        "extraction_method": extraction_method,
        "extraction_quality_score": quality_score,
        "queued_jobs": queued_job_types,
        # Surface dispatch errors so the API client can detect a partial
        # success (contract intake landed; one or more AI jobs failed to
        # enqueue) rather than seeing a 201 with no signal.
        "dispatch_errors": dispatch_errors,
    }


def _queue_initial_contract_jobs(
    db: Session,
    *,
    user: User,
    contract: Contract,
    version: ContractVersion,
    snapshot: ContractTextSnapshot,
):
    jobs = []
    for job_type in INITIAL_CONTRACT_AI_JOB_TYPES:
        jobs.append(
            create_job(
                db,
                org_id=user.org_id,
                job_type=job_type,
                resource_type="contract",
                resource_id=contract.id,
                created_by_user_id=user.id,
                idempotency_key=f"{job_type}:{version.id}:{snapshot.id}",
                metadata={
                    "contract_version_id": version.id,
                    "text_snapshot_id": snapshot.id,
                },
            )
        )
    return jobs


ACTIVATION_AI_JOB_TYPES = (
    "obligation_extraction",
    "renewal_extraction",
)


def queue_activation_ai_jobs(
    db: Session,
    *,
    user: User,
    contract: Contract,
    version: ContractVersion,
) -> None:
    """When a contract becomes ACTIVE, extract obligations and renewal terms
    from the authoritative version."""
    snapshot = (
        db.get(ContractTextSnapshot, version.text_snapshot_id)
        if version.text_snapshot_id
        else None
    )
    if snapshot is None:
        return
    jobs = []
    for job_type in ACTIVATION_AI_JOB_TYPES:
        jobs.append(
            create_job(
                db,
                org_id=user.org_id,
                job_type=job_type,
                resource_type="contract",
                resource_id=contract.id,
                created_by_user_id=user.id,
                idempotency_key=f"{job_type}:{version.id}:{snapshot.id}",
                metadata={
                    "contract_version_id": version.id,
                    "text_snapshot_id": snapshot.id,
                },
            )
        )
    db.flush()
    job_ids = [job.id for job in jobs]
    db.commit()
    for job_id in job_ids:
        job = db.get(JobRun, job_id)
        if job is not None:
            try:
                dispatch_job(db, job=job)
            except Exception:
                pass
    db.commit()


def _text_snapshot_validation_status(text: str, quality_score: float) -> str:
    if not text or quality_score < TEXT_EXTRACTION_COMPLETE_THRESHOLD:
        return "needs_review"
    return "complete"


def next_version_number(db: Session, contract_file_id: str) -> int:
    max_version = db.scalar(
        select(func.max(ContractVersion.version_number)).where(
            ContractVersion.contract_file_id == contract_file_id
        )
    )
    return int(max_version or 0) + 1


def requeue_contract_ai_jobs(
    db: Session,
    *,
    user: User,
    contract: Contract,
    version: ContractVersion,
) -> None:
    """Re-run metadata/clause/embeddings extraction for a newly-authoritative
    version so derived data does not go stale after an accepted edit or restore."""
    snapshot = (
        db.get(ContractTextSnapshot, version.text_snapshot_id)
        if version.text_snapshot_id
        else None
    )
    if snapshot is None:
        return
    queued = _queue_initial_contract_jobs(
        db, user=user, contract=contract, version=version, snapshot=snapshot
    )
    db.flush()
    job_ids = [job.id for job in queued]
    db.commit()
    for job_id in job_ids:
        job = db.get(JobRun, job_id)
        if job is not None:
            try:
                dispatch_job(db, job=job)
            except Exception:
                pass
    db.commit()
