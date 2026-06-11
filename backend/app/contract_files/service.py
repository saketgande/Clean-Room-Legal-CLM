import io
import logging
from dataclasses import dataclass

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
from app.contract_files.text_extraction import TextExtractionResult, extract_text
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


@dataclass(frozen=True)
class _ExtractedText:
    """Resolved text + metadata after optional OCR fallback."""

    method: str
    text: str
    quality_score: float
    page_map: dict | None
    ocr_provider: str | None = None
    ocr_error: str | None = None


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


def _scan_for_malware(content: bytes) -> None:
    """Scan ``content`` with ClamAV when ``settings.enable_clamav`` is on.

    No-op (returns immediately) when the flag is off — the default — so local
    dev and CI never need a clamd sidecar and nothing about the upload path
    changes. When enabled, we connect to the clamd daemon at
    ``settings.clamav_host:settings.clamav_port`` and reject on detection.

    The ``clamd`` client is imported lazily *inside* this guarded branch so the
    dependency is only required when the feature is switched on. If the flag is
    enabled but the client or daemon is unavailable we fail closed with a 503
    rather than silently letting an unscanned file through.
    """
    if not settings.enable_clamav:
        return
    try:
        import clamd  # imported lazily; only required when AV scanning is enabled
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        # TODO: vendor the `clamd` client into the AV-enabled deployment image.
        logger.error("ClamAV scanning enabled but the 'clamd' client is not installed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Antivirus scanning is enabled but unavailable.",
        ) from exc
    try:
        scanner = clamd.ClamdNetworkSocket(host=settings.clamav_host, port=settings.clamav_port)
        result = scanner.instream(io.BytesIO(content))
    except Exception as exc:  # pragma: no cover - network/daemon failure path
        logger.error("ClamAV scan failed to reach clamd: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Antivirus scanning is temporarily unavailable.",
        ) from exc
    # clamd returns {"stream": ("FOUND", "<signature>")} on a hit, ("OK", None)
    # otherwise. Treat anything other than an explicit OK as a detection.
    status_tuple = (result or {}).get("stream")
    if status_tuple and status_tuple[0] != "OK":
        signature = status_tuple[1] if len(status_tuple) > 1 else "unknown"
        logger.warning("Rejected uploaded file: ClamAV detected %s", signature)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Uploaded file was rejected by antivirus scanning.",
        )


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


async def _resolve_extracted_text(
    *, content: bytes, mime_type: str, filename: str
) -> _ExtractedText:
    """Run native text extraction and, if the result looks too thin, fall back
    to the OCR provider. Encapsulates the messy OCR-fallback decision tree so
    the upload orchestrator stays linear."""
    extraction: TextExtractionResult = extract_text(content, mime_type=mime_type, filename=filename)
    if not extraction.needs_ocr:
        return _ExtractedText(
            method=extraction.method,
            text=extraction.text,
            quality_score=extraction.quality_score,
            page_map=extraction.page_map,
        )
    try:
        ocr = await reducto_client.extract_text(
            filename=filename, mime_type=mime_type, content=content
        )
    except Exception as exc:
        return _ExtractedText(
            method=f"{extraction.method}_ocr_failed",
            text=extraction.text,
            quality_score=extraction.quality_score,
            page_map=extraction.page_map,
            ocr_provider=reducto_client.provider,
            ocr_error=str(exc),
        )
    if ocr.text:
        return _ExtractedText(
            method="reducto_ocr",
            text=ocr.text,
            quality_score=ocr.quality_score,
            page_map=extraction.page_map,
            ocr_provider=ocr.provider,
        )
    return _ExtractedText(
        method=extraction.method,
        text=extraction.text,
        quality_score=extraction.quality_score,
        page_map=extraction.page_map,
        ocr_provider=ocr.provider,
    )


def _persist_intake_records(
    db: Session,
    *,
    user: User,
    stored,
    mime_type: str,
    title: str | None,
    counterparty_name: str | None,
    extracted: _ExtractedText,
) -> tuple[StorageObject, Contract, ContractFile, ContractVersion, ContractTextSnapshot]:
    """Create the storage object → contract → file → version → text snapshot
    chain in one place. Returns the persisted instances so the caller can
    keep wiring them together without re-reading the DB."""
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

    snapshot = ContractTextSnapshot(
        org_id=user.org_id,
        contract_id=contract.id,
        contract_version_id=version.id,
        extraction_method=extracted.method,
        extraction_quality_score=extracted.quality_score,
        text=extracted.text,
        page_map=extracted.page_map,
        ocr_provider=extracted.ocr_provider,
        validation_status=_text_snapshot_validation_status(extracted.text, extracted.quality_score),
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    db.add(snapshot)
    db.flush()

    version.text_snapshot_id = snapshot.id
    contract_file.current_version_id = version.id
    contract.current_contract_file_id = contract_file.id
    contract.current_authoritative_version_id = version.id
    return storage_object, contract, contract_file, version, snapshot


def _dispatch_initial_jobs(
    db: Session,
    *,
    queued_jobs,
    user: User,
    contract: Contract,
    request_id: str | None,
) -> tuple[list[str], list[dict]]:
    """Best-effort dispatch of queued jobs. Returns ``(dispatched, errors)``
    where errors are surfaced in the API response so the client can detect
    partial success rather than seeing a 201 with silent enqueue failures."""
    dispatched: list[str] = []
    errors: list[dict] = []
    for job in queued_jobs:
        live_job = db.get(JobRun, job.id)
        if live_job is None:
            continue
        try:
            dispatch_job(db, job=live_job)
            dispatched.append(live_job.job_type)
        except Exception as exc:
            errors.append({"job_id": live_job.id, "job_type": live_job.job_type, "error": str(exc)})
    if dispatched or errors:
        write_timeline_event(
            db,
            org_id=user.org_id,
            resource_type="contract",
            resource_id=contract.id,
            event_type="contract.ai_jobs_dispatched",
            title="Contract AI jobs dispatched",
            actor_user_id=user.id,
            request_id=request_id,
            details={"dispatched_job_types": dispatched, "dispatch_errors": errors},
        )
        db.commit()
    return dispatched, errors


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
    """Orchestrate a contract intake: validate, store, extract text, persist
    rows, queue AI jobs, audit, dispatch. Split into focused helpers so the
    rollback-on-exception path is obvious — anything before the storage save
    can fail freely; once bytes are on disk, exceptions must delete them."""
    mime_type = upload.content_type or "application/octet-stream"
    if mime_type not in settings.allowed_mime_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported MIME type: {mime_type}"
        )
    content = await _read_upload_with_limit(
        upload,
        limit=settings.max_upload_size_bytes,
        chunk_size=settings.upload_stream_chunk_bytes,
    )
    # Re-verify MIME against actual bytes — client content-type is untrusted.
    mime_type = _sniff_mime_type(content, mime_type)
    # Optional antivirus scan (no-op unless settings.enable_clamav). Runs before
    # we persist any bytes so an infected upload never lands in storage.
    _scan_for_malware(content)
    if project_id:
        get_project_for_user(db, project_id=project_id, user=user, access="update")

    stored = storage_service.save_bytes(
        org_id=user.org_id,
        filename=upload.filename or "contract",
        mime_type=mime_type,
        content=content,
    )
    try:
        extracted = await _resolve_extracted_text(
            content=content, mime_type=mime_type, filename=stored.filename
        )
        storage_object, contract, contract_file, version, snapshot = _persist_intake_records(
            db,
            user=user,
            stored=stored,
            mime_type=mime_type,
            title=title,
            counterparty_name=counterparty_name,
            extracted=extracted,
        )
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
        timeline_details: dict = {
            "filename": stored.filename,
            "mime_type": mime_type,
            "extraction_method": extracted.method,
            "extraction_quality_score": extracted.quality_score,
            "validation_status": snapshot.validation_status,
        }
        if extracted.ocr_error:
            timeline_details["ocr_error"] = extracted.ocr_error
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
        # Once bytes are on disk we MUST delete them on rollback — otherwise
        # the storage backend retains orphan files referenced by no row.
        db.rollback()
        storage_service.delete_bytes_permanently(stored.storage_key)
        raise

    dispatched_job_types, dispatch_errors = _dispatch_initial_jobs(
        db, queued_jobs=queued_jobs, user=user, contract=contract, request_id=request_id
    )
    db.refresh(contract)
    return {
        "contract": contract,
        "contract_file_id": contract_file.id,
        "contract_version_id": version.id,
        "text_snapshot_id": snapshot.id,
        "extraction_method": extracted.method,
        "extraction_quality_score": extracted.quality_score,
        "queued_jobs": [job.job_type for job in queued_jobs],
        # Surface dispatch errors so the API client can detect a partial
        # success (intake landed; one or more AI jobs failed to enqueue).
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
    db.scalar(
        select(ContractFile.id)
        .where(ContractFile.id == contract_file_id)
        .with_for_update()
    )
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
