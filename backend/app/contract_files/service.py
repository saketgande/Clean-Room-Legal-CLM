import asyncio
import hashlib
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_files.models import (
    ContractDocumentElement,
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contract_files.structure import (
    build_elements,
    elements_from_flat_text,
    read_with_documents_reader,
)
from app.contract_files.text_extraction import TextExtractionResult, extract_text
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import new_uuid, utcnow
from app.core.enums import ContractLifecycleStage, ContractVersionSource, StorageBackend
from app.integrations.ocr import page_map_from_elements
from app.integrations.reducto import reducto_client
from app.integrations.storage import storage_service
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job

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
    # Structured elements from the parser (empty for native/plain extraction —
    # the structure builder falls back to splitting the flat text into clauses).
    elements: list = field(default_factory=list)


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
    # text/* has no magic, but it never contains NUL bytes — that's the same
    # binary heuristic git and file(1) use. Without it an executable uploaded
    # as text/plain was stored and served back as a "contract". (UTF-16 text
    # is rejected too; extraction decodes text as UTF-8 anyway.)
    if claimed.startswith("text/"):
        if b"\x00" in content[:8192]:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Uploaded file bytes are binary, not the declared {claimed} content-type.",
            )
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


def validate_upload_mime(content: bytes, claimed: str | None) -> str:
    """Shared upload gate: enforce the MIME allowlist AND verify the declared
    type against the file's magic bytes. Returns the canonical mime type; raises
    HTTPException(415) on an unsupported type or a bytes/header mismatch. Reuse
    this on every upload entry point — the client's content-type is untrusted."""
    mime_type = claimed or "application/octet-stream"
    if mime_type not in settings.allowed_mime_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported MIME type: {mime_type}"
        )
    return _sniff_mime_type(content, mime_type)


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


@dataclass(frozen=True)
class IngestedUpload:
    filename: str
    mime_type: str
    content: bytes


# For a browser that sends no (or a generic) content-type on a known extension.
_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/plain",
}


async def ingest_upload(upload: UploadFile, *, default_name: str = "document") -> IngestedUpload:
    """The one hardened read every upload endpoint uses: the stream is cut off at
    the size limit (a huge file never sits in memory), an empty file is refused, the
    type must be allowed and match the bytes, and the antivirus scan runs before
    anything is stored or parsed."""
    filename = upload.filename or default_name
    mime_type = upload.content_type or ""
    if not mime_type or mime_type == "application/octet-stream":
        extension = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        mime_type = _MIME_BY_EXTENSION.get(extension, "application/octet-stream")
    content = await _read_upload_with_limit(
        upload,
        limit=settings.max_upload_size_bytes,
        chunk_size=settings.upload_stream_chunk_bytes,
    )
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The uploaded file is empty")
    mime_type = validate_upload_mime(content, mime_type)
    await asyncio.to_thread(_scan_for_malware, content)  # network call to clamd when enabled
    return IngestedUpload(filename=filename, mime_type=mime_type, content=content)


INITIAL_CONTRACT_AI_JOB_TYPES = (
    "metadata_extraction",
    "clause_extraction",
    "embeddings",
)
TEXT_EXTRACTION_COMPLETE_THRESHOLD = 0.55


def _persist_document_elements(db, snapshot: ContractTextSnapshot, *, elements: list) -> None:
    """Phase 1 of the structured-document migration: break a freshly-created
    snapshot into ContractDocumentElement rows (clauses, headings, tables). Uses
    the parser's real elements when present, else splits the flat text.

    Purely additive and defensive: it never raises into the upload path, never
    touches snapshot.text, and leaves the snapshot 'flat_only' if the elements
    don't slice back out of the text exactly. So structuring can only help — it
    can't corrupt an upload."""
    try:
        if elements and "char_start" in elements[0]:
            # Already placed by the Documents reader, offsets into this text.
            rows, ok = [dict(e) for e in elements], True
        else:
            rows, ok = (
                build_elements(elements, snapshot.text)
                if elements
                else elements_from_flat_text(snapshot.text)
            )
        if not ok or not rows:
            return
        # Idempotent: clear any prior elements for this snapshot before writing,
        # so re-running (e.g. a backfill) rebuilds cleanly instead of duplicating.
        db.query(ContractDocumentElement).filter_by(text_snapshot_id=snapshot.id).delete()
        if any(snapshot.text[r["char_start"]:r["char_end"]] != r["text"] for r in rows):
            return  # invariant broken — do not persist misaligned offsets
        # The reader's tree names parents by position; ids are assigned up front
        # so a child can point at its parent before anything is flushed.
        ids = [new_uuid() for _ in rows]
        for i, r in enumerate(rows):
            parent_seq = r.pop("parent_seq", None)
            db.add(
                ContractDocumentElement(
                    id=ids[i],
                    parent_id=ids[parent_seq] if parent_seq is not None and parent_seq < len(ids) else None,
                    org_id=snapshot.org_id,
                    contract_id=snapshot.contract_id,
                    contract_version_id=snapshot.contract_version_id,
                    text_snapshot_id=snapshot.id,
                    **r,
                )
            )
        snapshot.structure_status = "structured"
        snapshot.element_count = len(rows)
    except Exception:
        logging.getLogger(__name__).warning(
            "structuring snapshot %s failed; leaving flat_only",
            getattr(snapshot, "id", "?"),
            exc_info=True,
        )


def backfill_document_elements(db, *, limit: int | None = None, batch_size: int = 200) -> dict:
    """Phase 2: give existing contracts a clause index by structuring snapshots
    that are still 'flat_only'. Derives elements from the stored flat text — no
    re-OCR, so it costs nothing and can't fail an extraction. Idempotent: it only
    touches flat_only snapshots and rebuilds each cleanly, so it is safe to run,
    re-run, or resume. Returns a small summary for logging.

    ponytail: reconstructs structure from flat text (loses tables/confidence).
    Re-parsing the original file gives higher fidelity; add that as an opt-in
    pass when a contract's structure actually needs it.
    """
    from sqlalchemy import select as _select

    query = _select(ContractTextSnapshot).where(
        ContractTextSnapshot.structure_status == "flat_only"
    )
    if limit is not None:
        query = query.limit(limit)
    snapshots = db.scalars(query).all()

    structured = skipped = 0
    for i, snapshot in enumerate(snapshots, start=1):
        if not (snapshot.text or "").strip():
            skipped += 1  # nothing to structure (e.g. signed PDF with empty text)
            continue
        _persist_document_elements(db, snapshot, elements=[])
        if snapshot.structure_status == "structured":
            structured += 1
        else:
            skipped += 1
        if i % batch_size == 0:
            db.commit()
    db.commit()
    return {"total": len(snapshots), "structured": structured, "skipped": skipped}


def _ocr_providers() -> list:
    """Configured OCR providers. Reducto has no `enabled` flag, so its key is the test."""
    providers = []
    if settings.reducto_api_key:
        providers.append(reducto_client)
    return providers


async def _resolve_extracted_text(
    *, content: bytes, mime_type: str, filename: str
) -> _ExtractedText:
    """Run native text extraction and, if the result looks too thin, fall back
    to the OCR provider. Encapsulates the messy OCR-fallback decision tree so
    the upload orchestrator stays linear."""
    # The Documents reader first: page furniture removed, Word numbering and
    # tracked insertions kept, a clause tree. When it can't do better (scan,
    # damaged file, unsupported type) the path below runs exactly as before.
    # PDF/DOCX parsing is CPU-bound: run it off the event loop, or every request stalls behind it.
    read = await asyncio.to_thread(
        read_with_documents_reader, content, mime_type=mime_type, filename=filename
    )
    if read is not None:
        return _ExtractedText(
            method=read["method"],
            text=read["text"],
            quality_score=read["quality"],
            page_map=read["page_map"],
            elements=read["elements"],
        )
    extraction: TextExtractionResult = await asyncio.to_thread(
        extract_text, content, mime_type=mime_type, filename=filename
    )
    if not extraction.needs_ocr:
        return _ExtractedText(
            method=extraction.method,
            text=extraction.text,
            quality_score=extraction.quality_score,
            page_map=extraction.page_map,
        )
    # Every configured provider in turn: "has credentials" says nothing about
    # "works", and a scanned contract that silently holds no text is the most
    # expensive failure in this pipeline. All return OCRResult.
    ocr_errors: list[str] = []
    for ocr_client in _ocr_providers():
        try:
            ocr = await ocr_client.extract_text(
                filename=filename, mime_type=mime_type, content=content
            )
        except Exception as exc:
            ocr_errors.append(f"{ocr_client.provider}: {exc}")
            continue
        if ocr.text:
            return _ExtractedText(
                method=f"{ocr.provider}_ocr",
                text=ocr.text,
                quality_score=ocr.quality_score,
                # NOT extraction.page_map: those offsets were measured against
                # the native text we are about to discard. Rebuild from the OCR
                # provider's own elements, or carry no map at all — a page
                # citation that points into the wrong string is worse than none.
                page_map=page_map_from_elements(ocr.text, ocr.elements),
                ocr_provider=ocr.provider,
                elements=ocr.elements,
            )
        ocr_errors.append(f"{ocr_client.provider}: returned no text")
    if ocr_errors:
        return _ExtractedText(
            method=f"{extraction.method}_ocr_failed",
            text=extraction.text,
            quality_score=extraction.quality_score,
            page_map=extraction.page_map,
            ocr_provider=_ocr_providers()[0].provider if _ocr_providers() else None,
            # Every provider's reason, not just the first: an operator looking
            # at an empty contract needs to know whether one key is wrong or
            # the document is genuinely unreadable.
            ocr_error="; ".join(ocr_errors),
        )
    # No provider is configured at all, so OCR did not fail — it never ran.
    # Keep the native result unlabelled rather than reporting a broken key
    # that does not exist.
    return _ExtractedText(
        method=extraction.method,
        text=extraction.text,
        quality_score=extraction.quality_score,
        page_map=extraction.page_map,
    )


def _dedupe_extension(filename: str) -> str:
    """Collapse a doubled trailing extension so titles read cleanly.

    Some upload clients append an extension to a name that already has one
    (the Word add-in did this), producing 'MSA.docx.docx'. Choke point for
    every upload path, so a single guard here covers all of them.
    """
    stem, dot, ext = filename.strip().rpartition(".")
    if dot and stem.lower().endswith(f".{ext.lower()}"):
        return stem  # 'foo.docx.docx' -> 'foo.docx'
    return filename.strip()


def _persist_intake_records(
    db: Session,
    *,
    user: User,
    stored,
    mime_type: str,
    title: str | None,
    counterparty_name: str | None,
    contract_type: str | None,
    extracted: _ExtractedText | None,
) -> tuple[StorageObject, Contract, ContractFile, ContractVersion, ContractTextSnapshot | None]:
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
        title=title or _dedupe_extension(stored.filename),
        counterparty_name=counterparty_name,
        contract_type=contract_type,
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

    # A deferred upload has no text yet: process_uploaded_document adds the snapshot.
    snapshot = (
        _persist_text_snapshot(db, user=user, contract=contract, version=version, extracted=extracted)
        if extracted is not None
        else None
    )
    contract_file.current_version_id = version.id
    contract.current_contract_file_id = contract_file.id
    contract.current_authoritative_version_id = version.id
    return storage_object, contract, contract_file, version, snapshot


def _persist_text_snapshot(
    db: Session, *, user: User, contract: Contract, version: ContractVersion, extracted: _ExtractedText
) -> ContractTextSnapshot:
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
    _persist_document_elements(db, snapshot, elements=extracted.elements)
    version.text_snapshot_id = snapshot.id
    return snapshot


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


def _advance_upload_to_review(db: Session, *, contract: Contract, user: User, request_id: str | None) -> None:
    """An uploaded document is a real contract in flight, not a blank "intake"
    request you're about to draft, so send it straight to REVIEW, where the actual
    work (AI analysis, redlines, comments) happens. The Review stage-entry trigger
    won't re-dispatch analysis jobs already dispatched (celery_task_id guard in
    stage_triggers). Best-effort: a hiccup here must never fail the upload."""
    try:
        from app.contracts.lifecycle import transition_contract_stage

        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.REVIEW,
            actor_user_id=user.id,
            reason="Uploaded document — moved to review automatically",
            request_id=request_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("auto-advance to review failed for contract %s", contract.id, exc_info=True)


def _recent_duplicate_upload(db: Session, *, user: User, content: bytes) -> dict | None:
    """The same person sending the same file again within a few minutes is a retry
    (a cut connection, a double click): hand back the contract already created."""
    digest = hashlib.sha256(content).hexdigest()
    version = db.scalar(
        select(ContractVersion)
        .join(StorageObject, StorageObject.id == ContractVersion.storage_object_id)
        .where(
            StorageObject.org_id == user.org_id,
            StorageObject.created_by_user_id == user.id,
            StorageObject.sha256_hash == digest,
            StorageObject.created_at >= utcnow() - timedelta(minutes=10),
            ContractVersion.source == ContractVersionSource.UPLOAD,
            ContractVersion.deleted_at.is_(None),
        )
        .order_by(StorageObject.created_at.desc())
        .limit(1)
    )
    if version is None:
        return None
    contract = db.get(Contract, version.contract_id)
    if contract is None or contract.deleted_at is not None:
        return None
    snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version.text_snapshot_id else None
    return {
        "contract": contract,
        "contract_file_id": version.contract_file_id,
        "contract_version_id": version.id,
        "text_snapshot_id": version.text_snapshot_id,
        "extraction_method": snapshot.extraction_method if snapshot else "pending",
        "extraction_quality_score": snapshot.extraction_quality_score if snapshot else 0.0,
        "queued_jobs": [],
        "dispatch_errors": [],
    }


async def process_uploaded_document(db: Session, *, job: JobRun) -> dict:
    """The background half of an HTTP upload: extract the text (OCR when needed),
    fill the metadata, queue the AI analysis and move the contract into Review.
    Safe to re-run: a version that already has its text is left alone."""
    version = db.get(ContractVersion, (job.metadata_json or {}).get("contract_version_id"))
    if version is None:
        raise RuntimeError("Contract version not found for text extraction")
    if version.text_snapshot_id:
        return {"text_snapshot_id": version.text_snapshot_id, "already_processed": True}
    contract = db.get(Contract, version.contract_id)
    storage_object = db.get(StorageObject, version.storage_object_id)
    user = db.get(User, job.created_by_user_id) if job.created_by_user_id else None
    if contract is None or storage_object is None or user is None:
        raise RuntimeError("Upload records missing for text extraction")

    content = await asyncio.to_thread(storage_service.read_bytes, storage_object.storage_key)
    extracted = await _resolve_extracted_text(
        content=content, mime_type=storage_object.mime_type, filename=storage_object.filename
    )
    snapshot = _persist_text_snapshot(db, user=user, contract=contract, version=version, extracted=extracted)
    queued_jobs = _queue_initial_contract_jobs(db, user=user, contract=contract, version=version, snapshot=snapshot)
    details = {
        "extraction_method": extracted.method,
        "extraction_quality_score": extracted.quality_score,
        "validation_status": snapshot.validation_status,
    }
    if extracted.ocr_error:
        details["ocr_error"] = extracted.ocr_error
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.text_extracted",
        title="Document text extracted",
        actor_user_id=user.id,
        details=details,
    )
    db.commit()
    _dispatch_initial_jobs(db, queued_jobs=queued_jobs, user=user, contract=contract, request_id=None)
    if contract.lifecycle_stage == ContractLifecycleStage.INTAKE:
        _advance_upload_to_review(db, contract=contract, user=user, request_id=None)
    return {"text_snapshot_id": snapshot.id, "extraction_method": extracted.method}


async def create_contract_from_upload(
    db: Session,
    *,
    upload: UploadFile,
    user: User,
    title: str | None = None,
    counterparty_name: str | None = None,
    contract_type: str | None = None,
    request_id: str | None = None,
    defer_processing: bool = False,
    on_created: Callable[[Contract], None] | None = None,
) -> dict:
    """Orchestrate a contract intake: validate, store, extract text, persist
    rows, queue AI jobs, audit, dispatch. Split into focused helpers so the
    rollback-on-exception path is obvious — anything before the storage save
    can fail freely; once bytes are on disk, exceptions must delete them.

    ``on_created`` runs on the new contract inside the same transaction, before
    any job is dispatched — so what a caller already knows (a request's form
    facts, the auto-review flag) is on the row before the AI jobs read it,
    instead of racing them with a second commit."""
    mime_type = upload.content_type or "application/octet-stream"
    if mime_type not in settings.allowed_mime_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported MIME type: {mime_type}"
        )
    ingested = await ingest_upload(upload, default_name="contract")
    content, mime_type = ingested.content, ingested.mime_type
    if defer_processing:
        duplicate = _recent_duplicate_upload(db, user=user, content=content)
        if duplicate is not None:
            return duplicate

    stored = await asyncio.to_thread(
        storage_service.save_bytes,
        org_id=user.org_id,
        filename=upload.filename or "contract",
        mime_type=mime_type,
        content=content,
    )
    try:
        # Deferred (the HTTP upload): extraction, OCR and metadata run in a background
        # job, so the request returns in seconds instead of timing out.
        extracted = (
            None
            if defer_processing
            else await _resolve_extracted_text(content=content, mime_type=mime_type, filename=stored.filename)
        )
        storage_object, contract, contract_file, version, snapshot = _persist_intake_records(
            db,
            user=user,
            stored=stored,
            mime_type=mime_type,
            title=title,
            counterparty_name=counterparty_name,
            contract_type=contract_type,
            extracted=extracted,
        )
        if on_created is not None:
            on_created(contract)
        if defer_processing:
            queued_jobs = [
                create_job(
                    db,
                    org_id=user.org_id,
                    job_type="document_text_extraction",
                    resource_type="contract",
                    resource_id=contract.id,
                    created_by_user_id=user.id,
                    idempotency_key=f"document_text_extraction:{version.id}",
                    metadata={"contract_version_id": version.id},
                )
            ]
        else:
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
        timeline_details: dict = {"filename": stored.filename, "mime_type": mime_type}
        if extracted is not None:
            timeline_details.update(
                extraction_method=extracted.method,
                extraction_quality_score=extracted.quality_score,
                validation_status=snapshot.validation_status,
            )
            if extracted.ocr_error:
                timeline_details["ocr_error"] = extracted.ocr_error
        else:
            timeline_details["text_extraction"] = "queued"
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

    _dispatched_job_types, dispatch_errors = _dispatch_initial_jobs(
        db, queued_jobs=queued_jobs, user=user, contract=contract, request_id=request_id
    )

    if not defer_processing:
        _advance_upload_to_review(db, contract=contract, user=user, request_id=request_id)

    db.refresh(contract)
    return {
        "contract": contract,
        "contract_file_id": contract_file.id,
        "contract_version_id": version.id,
        "text_snapshot_id": snapshot.id if snapshot else None,
        "extraction_method": extracted.method if extracted else "pending",
        "extraction_quality_score": extracted.quality_score if extracted else 0.0,
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
                logger.warning("failed to dispatch job %s", job_id, exc_info=True)
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
                logger.warning("failed to dispatch job %s", job_id, exc_info=True)
    db.commit()


def _resolve_contract_file(db: Session, *, contract: Contract, org_id: str) -> ContractFile:
    """Return the contract's current ContractFile (or its first file), 404-ing
    if the contract has no file to attach a new version to."""
    contract_file = None
    if contract.current_contract_file_id:
        contract_file = db.get(ContractFile, contract.current_contract_file_id)
    if contract_file is None:
        contract_file = db.scalars(
            select(ContractFile)
            .where(
                ContractFile.org_id == org_id,
                ContractFile.contract_id == contract.id,
                ContractFile.deleted_at.is_(None),
            )
            .order_by(ContractFile.created_at.asc())
        ).first()
    if contract_file is None or contract_file.org_id != org_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Contract has no file to attach a version to"
        )
    return contract_file


def promote_version(db: Session, *, contract: Contract, version: ContractVersion, actor_user_id: str | None) -> None:
    """Make ``version`` the contract's one authoritative version.

    Demote the others and flush FIRST: the database allows a single authoritative
    version per contract (uq_contract_version_authoritative), so promoting before
    demoting is rejected.
    """
    db.execute(
        update(ContractVersion)
        .where(
            ContractVersion.contract_id == contract.id,
            ContractVersion.id != version.id,
            ContractVersion.is_authoritative.is_(True),
        )
        .values(is_authoritative=False, updated_by_user_id=actor_user_id)
        .execution_options(synchronize_session="fetch")
    )
    db.flush()
    version.is_authoritative = True
    version.updated_by_user_id = actor_user_id
    db.flush()
    contract.current_authoritative_version_id = version.id
    contract.updated_by_user_id = actor_user_id


async def add_version_from_upload(
    db: Session,
    *,
    contract: Contract,
    upload: UploadFile,
    user: User,
    change_summary: str | None = None,
    source: str = ContractVersionSource.MANUAL_UPLOAD,
    request_id: str | None = None,
) -> ContractVersion:
    """Create a new authoritative version of an EXISTING contract from an
    uploaded ``.docx`` (e.g. the edited draft pushed from the Word add-in).

    Mirrors ``create_contract_from_upload`` (validate → store → extract → persist
    → requeue AI jobs) and the version-promotion logic of ``restore_contract_version``,
    but targets a contract that already exists. The caller is expected to have
    already authorized access via ``get_contract_for_user``."""
    mime_type = upload.content_type or "application/octet-stream"
    if mime_type not in settings.allowed_mime_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported MIME type: {mime_type}"
        )
    ingested = await ingest_upload(upload, default_name="contract.docx")
    content, mime_type = ingested.content, ingested.mime_type

    contract_file = _resolve_contract_file(db, contract=contract, org_id=user.org_id)

    stored = await asyncio.to_thread(
        storage_service.save_bytes,
        org_id=user.org_id,
        filename=upload.filename or "contract.docx",
        mime_type=mime_type,
        content=content,
    )
    try:
        extracted = await _resolve_extracted_text(
            content=content, mime_type=mime_type, filename=stored.filename
        )
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

        version = ContractVersion(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_file_id=contract_file.id,
            version_number=next_version_number(db, contract_file.id),
            storage_object_id=storage_object.id,
            source=source,
            change_summary=change_summary or "New version uploaded from Word",
            is_authoritative=False,  # promoted below, once the current one is demoted
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
            validation_status=_text_snapshot_validation_status(
                extracted.text, extracted.quality_score
            ),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(snapshot)
        db.flush()
        _persist_document_elements(db, snapshot, elements=extracted.elements)
        version.text_snapshot_id = snapshot.id

        promote_version(db, contract=contract, version=version, actor_user_id=user.id)
        contract_file.current_version_id = version.id
        contract_file.updated_by_user_id = user.id
        contract.current_contract_file_id = contract_file.id

        write_audit_log(
            db,
            action="contract.version_uploaded",
            resource_type="contract_version",
            resource_id=version.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            request_id=request_id,
            after={
                "contract_id": contract.id,
                "contract_file_id": contract_file.id,
                "version_number": version.version_number,
            },
        )
        write_timeline_event(
            db,
            org_id=user.org_id,
            resource_type="contract",
            resource_id=contract.id,
            event_type="contract.version_uploaded",
            title="New version uploaded",
            actor_user_id=user.id,
            request_id=request_id,
            details={
                "contract_version_id": version.id,
                "version_number": version.version_number,
                "source": "word_addin",
            },
        )
        db.commit()
    except Exception:
        # Bytes are on disk now; delete them on rollback so storage has no
        # orphan referenced by no row.
        db.rollback()
        storage_service.delete_bytes_permanently(stored.storage_key)
        raise

    requeue_contract_ai_jobs(db, user=user, contract=contract, version=version)
    db.refresh(version)
    return version
