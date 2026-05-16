import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_files.models import ContractVersion, StorageObject
from app.contracts.access import user_can_access_contract
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.config import settings
from app.core.deps import get_db, require_permission
from app.core.enums import ContractLifecycleStage, SignatureStatus
from app.core.rbac import has_permission
from app.integrations.docusign import docusign_client, verify_connect_signature
from app.integrations.resend import resend_client
from app.integrations.storage import storage_service
from app.signatures.models import SignatureRecipient, SignatureRequest
from app.signatures.service import sync_signature_request, validate_signature_recipients

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
    validate_signature_recipients(
        db, contract=contract, org_id=current_user.org_id, recipients=payload.recipients
    )
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
        # Notification is best-effort: a mail-provider failure must not roll back
        # an already-created DocuSign envelope / signature request.
        try:
            await resend_client.send_email(
                to=str(recipient.email),
                subject=f"Signature requested: {contract.title}",
                html=(
                    f"<p>You have been requested to sign <b>{contract.title}</b>.</p>"
                    f"<p>Envelope: {envelope.envelope_id or 'mock'}</p>"
                ),
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "signature notification email failed for %s: %s", recipient.email, exc
            )
    transition_contract_stage(
        db,
        contract=contract,
        to_stage=ContractLifecycleStage.SIGNATURE_PENDING,
        actor_user_id=current_user.id,
        reason="Sent for signature",
        override=payload.override_lifecycle,
        override_authorized=payload.override_lifecycle,
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
    if (
        not settings.mock_docusign
        and payload.completed
        and not payload.declined
        and not has_permission(
            current_user.permission_values, "contract:lifecycle_override"
        )
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "With live DocuSign, completion is driven by the verified Connect webhook; "
            "manual completion requires contract:lifecycle_override",
        )
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


@router.post("/webhook/docusign")
async def docusign_connect_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """DocuSign Connect callback — the trusted completion path in live mode.

    Public route: authenticated only by the Connect HMAC over the raw body.
    Idempotent so DocuSign's retries cannot double-activate a contract.
    """
    raw = await request.body()
    if not verify_connect_signature(
        body=raw, signature_header=request.headers.get("X-DocuSign-Signature-1")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid DocuSign signature")
    try:
        event = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed webhook payload")
    if not isinstance(event, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed webhook payload")
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    summary = (
        data.get("envelopeSummary") if isinstance(data.get("envelopeSummary"), dict) else {}
    )
    envelope_id = data.get("envelopeId") or event.get("envelopeId")
    envelope_status = str(
        summary.get("status")
        or data.get("status")
        or event.get("status")
        or event.get("event")
        or ""
    ).lower()
    if not envelope_id:
        return {"status": "ignored", "reason": "missing envelope id"}
    signature = db.scalar(
        select(SignatureRequest).where(
            SignatureRequest.provider_envelope_id == str(envelope_id)
        )
    )
    if signature is None:
        return {"status": "ignored", "reason": "unknown envelope"}
    if signature.status in {
        SignatureStatus.COMPLETED,
        SignatureStatus.VOIDED,
        SignatureStatus.DECLINED,
    }:
        return {"status": "noop", "signature_request_id": signature.id}
    actor = None
    if signature.sent_by_user_id:
        actor = db.get(User, signature.sent_by_user_id)
    if actor is None and signature.created_by_user_id:
        actor = db.get(User, signature.created_by_user_id)
    if actor is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Original sender not found for this envelope"
        )
    completed = envelope_status in {"completed", "envelope-completed"}
    declined = envelope_status in {
        "declined",
        "voided",
        "envelope-declined",
        "envelope-voided",
    }
    sync_signature_request(
        db,
        user=actor,
        signature_request=signature,
        completed=completed,
        declined=declined,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    return {
        "status": "synced",
        "envelope_status": envelope_status,
        "signature_request_id": signature.id,
    }
