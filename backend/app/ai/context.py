from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract


MAX_FULL_TEXT_CHARS = 25_000


@dataclass(frozen=True)
class ContractAIContext:
    contract: Contract
    version: ContractVersion | None
    snapshot: ContractTextSnapshot | None
    text: str
    text_was_truncated: bool
    manifest: dict


def build_contract_context(
    db: Session,
    *,
    org_id: str,
    contract_id: str,
    contract_version_id: str | None = None,
    text_snapshot_id: str | None = None,
) -> ContractAIContext:
    contract = db.get(Contract, contract_id)
    if contract is None or contract.org_id != org_id or contract.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")

    version = None
    if contract_version_id:
        version = db.get(ContractVersion, contract_version_id)
    elif contract.current_authoritative_version_id:
        version = db.get(ContractVersion, contract.current_authoritative_version_id)

    if version is not None and (version.org_id != org_id or version.contract_id != contract_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")

    snapshot = None
    if text_snapshot_id:
        snapshot = db.get(ContractTextSnapshot, text_snapshot_id)
    elif version is not None and version.text_snapshot_id:
        snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id)

    if snapshot is not None and (snapshot.org_id != org_id or snapshot.contract_id != contract_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract text snapshot not found")

    text = snapshot.text if snapshot else ""
    truncated_text = text[:MAX_FULL_TEXT_CHARS]
    return ContractAIContext(
        contract=contract,
        version=version,
        snapshot=snapshot,
        text=truncated_text,
        text_was_truncated=len(text) > len(truncated_text),
        manifest={
            "contract_id": contract.id,
            "contract_version_id": version.id if version else None,
            "text_snapshot_id": snapshot.id if snapshot else None,
            "text_length": len(text),
            "text_was_truncated": len(text) > len(truncated_text),
        },
    )


def list_contract_handles(db: Session, *, session_id: str) -> list[dict]:
    from app.assistant.models import AssistantContractHandle

    handles = db.scalars(
        select(AssistantContractHandle).where(AssistantContractHandle.session_id == session_id)
    ).all()
    return [
        {
            "handle": handle.handle,
            "contract_id": handle.contract_id,
            "metadata": handle.metadata_json,
        }
        for handle in handles
    ]


def chunk_text(text: str, *, chunk_chars: int = 3600, overlap_chars: int = 500) -> list[dict]:
    if not text:
        return []
    chunks = []
    start = 0
    index = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        chunks.append({"chunk_index": index, "start_char": start, "end_char": end, "text": text[start:end]})
        if end == len(text):
            break
        start = max(0, end - overlap_chars)
        index += 1
    return chunks
