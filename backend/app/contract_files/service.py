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
from app.projects.models import Project, ProjectContract


INITIAL_CONTRACT_AI_JOB_TYPES = (
    "metadata_extraction",
    "clause_extraction",
    "embeddings",
)


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

    content = await upload.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Upload exceeds size limit")

    project = None
    if project_id:
        project = db.get(Project, project_id)
        if project is None or project.org_id != user.org_id or project.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    stored = storage_service.save_bytes(
        org_id=user.org_id,
        filename=upload.filename or "contract",
        mime_type=mime_type,
        content=content,
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
    if extraction.needs_ocr:
        ocr = await reducto_client.extract_text(
            filename=stored.filename,
            mime_type=mime_type,
            content=content,
        )
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
        validation_status="complete" if extracted_text else "needs_review",
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

    queued_jobs = _queue_initial_contract_jobs(db, user=user, contract=contract, version=version, snapshot=snapshot)

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
    write_timeline_event(
        db,
        org_id=user.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.uploaded",
        title="Contract uploaded",
        actor_user_id=user.id,
        request_id=request_id,
        details={
            "filename": stored.filename,
            "mime_type": mime_type,
            "extraction_method": extraction_method,
            "extraction_quality_score": quality_score,
        },
    )
    queued_job_ids = [job.id for job in queued_jobs]
    queued_job_types = [job.job_type for job in queued_jobs]
    db.commit()

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


def next_version_number(db: Session, contract_file_id: str) -> int:
    max_version = db.scalar(
        select(func.max(ContractVersion.version_number)).where(
            ContractVersion.contract_file_id == contract_file_id
        )
    )
    return int(max_version or 0) + 1
