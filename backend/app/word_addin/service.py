import hashlib
import logging

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractVersion, StorageObject
from app.contract_files.service import _read_upload_with_limit, create_contract_from_upload
from app.contracts.models import Contract
from app.core.config import settings

logger = logging.getLogger(__name__)


def _stage_value(contract: Contract) -> str | None:
    stage = contract.lifecycle_stage
    return getattr(stage, "value", None) or (str(stage) if stage is not None else None)


async def resolve_contract_for_document(db, *, upload: UploadFile, user, request_id: str | None = None) -> dict:
    """Auto-link the open Word document to a contract.

    Matches the uploaded ``.docx`` to an existing contract by file content hash
    (so re-opening the same document links back to the same record); otherwise
    creates a new contract from it. Lets the add-in link silently on open with
    no manual step."""
    content = await _read_upload_with_limit(
        upload,
        limit=settings.max_upload_size_bytes,
        chunk_size=settings.upload_stream_chunk_bytes,
    )
    sha = hashlib.sha256(content).hexdigest()

    existing = db.scalars(
        select(StorageObject).where(
            StorageObject.org_id == user.org_id,
            StorageObject.sha256_hash == sha,
        )
    ).first()
    if existing is not None:
        version = db.scalars(
            select(ContractVersion).where(
                ContractVersion.org_id == user.org_id,
                ContractVersion.storage_object_id == existing.id,
                ContractVersion.deleted_at.is_(None),
            )
        ).first()
        if version is not None:
            contract = db.get(Contract, version.contract_id)
            if contract is not None and contract.deleted_at is None:
                return {
                    "contract_id": contract.id,
                    "title": contract.title,
                    "lifecycle_stage": _stage_value(contract),
                    "created": False,
                }

    # No match — create a new contract from the document. Reset the stream
    # first since we already consumed it to hash the bytes.
    await upload.seek(0)
    result = await create_contract_from_upload(db, upload=upload, user=user, request_id=request_id)
    contract = result["contract"]
    return {
        "contract_id": contract.id,
        "title": contract.title,
        "lifecycle_stage": _stage_value(contract),
        "created": True,
    }
