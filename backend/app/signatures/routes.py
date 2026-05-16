from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractVersion, StorageObject
from app.contracts.access import user_can_access_contract
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.core.enums import ContractLifecycleStage, SignatureStatus
from app.core.rbac import has_permission
from app.integrations.docusign import docusign_client
from app.integrations.resend import resend_client
from app.integrations.storage import storage_service
from app.signatures.models import SignatureRecipient, SignatureRequest
from app.signatures.service import sync_signature_request

router = APIRouter(prefix="/signatures", tags=["signatures"])


class SignatureRecipientPayload(BaseModel):
    name: str
    email: EmailStr
    role: str | None = None


class SignatureSendPayload(BaseModel):
    contract_id: str
    contract_version_id: str | None = None
    recipients: list[SignatureRecipientPayload]
    override_lifecycle: bool = False


@router.get("")
def list_signature_requests(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:sign")),
):
    rows = db.scalars(
        select(SignatureRequest).where(SignatureRequest.org_id == current_user.org_id)
    ).all()
    visible = []
    for row in rows:
        contract = db.get(Contract, row.contract_id)
        if contract is not None and user_can_access_contract(db, contract=contract, user=current_user):
            visible.append(row)
    return visible


@router.post("/requests")
async def send_for_signature(
    payload: SignatureSendPayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:sign")),
):
    contract = get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    if payload.override_lifecycle and not has_permission(
        current_user.permission_values, "contract:lifecycle_override"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Overriding the approved-before-signature gate requires contract:lifecycle_override",
        )
    if contract.lifecycle_stage != ContractLifecycleStage.APPROVED and not payload.override_lifecycle:
        raise HTTPException(status.HTTP_409_CONFLICT, "Contract must be approved before signature")
    version_id = payload.contract_version_id or contract.current_authoritative_version_id
    version = db.get(ContractVersion, version_id)
    if version is None or version.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
    storage_object = db.get(StorageObject, version.storage_object_id)
    if storage_object is None or storage_object.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file not found")
    content = storage_service.read_bytes(storage_object.storage_key)
    envelope = await docusign_client.create_envelope(
        filename=storage_object.filename,
        recipients=[recipient.model_dump() for recipient in payload.recipients],
        content=content,
    )
    signature = SignatureRequest(
        org_id=current_user.org_id,
        contract_id=contract.id,
        contract_version_id=version.id,
        provider_envelope_id=envelope.envelope_id,
        status=SignatureStatus.SENT,
        sent_by_user_id=current_user.id,
        sent_at=datetime.now(UTC),
        metadata_json=envelope.metadata,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(signature)
    db.flush()
    for index, recipient in enumerate(payload.recipients, start=1):
        db.add(
            SignatureRecipient(
                org_id=current_user.org_id,
                signature_request_id=signature.id,
                name=recipient.name,
                email=str(recipient.email),
                role=recipient.role,
                routing_order=str(index),
                status="sent",
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
        )
    for recipient in payload.recipients:
        await resend_client.send_email(
            to=str(recipient.email),
            subject=f"Signature requested: {contract.title}",
            html=(
                f"<p>You have been requested to sign <b>{contract.title}</b>.</p>"
                f"<p>Envelope: {envelope.envelope_id or 'mock'}</p>"
            ),
        )
    transition_contract_stage(
        db,
        contract=contract,
        to_stage=ContractLifecycleStage.SIGNATURE_PENDING,
        actor_user_id=current_user.id,
        reason="Sent for signature",
        override=True,
        override_authorized=True,
    )
    db.commit()
    db.refresh(signature)
    return signature


class SignatureSyncPayload(BaseModel):
    completed: bool = True
    declined: bool = False


@router.post("/requests/{signature_request_id}/sync")
def sync_signature(
    signature_request_id: str,
    payload: SignatureSyncPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:sign")),
):
    signature = db.get(SignatureRequest, signature_request_id)
    if signature is None or signature.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signature request not found")
    get_contract_for_user(db, contract_id=signature.contract_id, user=current_user)
    sync_signature_request(
        db,
        user=current_user,
        signature_request=signature,
        completed=payload.completed,
        declined=payload.declined,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    db.refresh(signature)
    return signature
