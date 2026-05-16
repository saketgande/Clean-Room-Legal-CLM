from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
)
from app.contract_files.service import (
    next_version_number,
    queue_activation_ai_jobs,
    requeue_contract_ai_jobs,
)
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract, ContractParty
from app.core.audit import write_audit_log, write_timeline_event
from app.core.enums import (
    ContractLifecycleStage,
    ContractVersionSource,
    SignatureStatus,
)
from app.signatures.models import SignatureEvent, SignatureRecipient, SignatureRequest


def validate_signature_recipients(
    db: Session,
    *,
    contract: Contract,
    org_id: str,
    recipients: list,
) -> None:
    """Every recipient must be a contract party or an organization user.

    The signing document and a signature link are emailed to these addresses,
    so an arbitrary caller- or model-supplied address is a document
    exfiltration vector. The allowlist is server-derived, never trusted input.
    """
    if not recipients:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "At least one recipient is required"
        )
    allowed = {
        email.lower()
        for email in db.scalars(
            select(ContractParty.contact_email).where(
                ContractParty.org_id == org_id,
                ContractParty.contract_id == contract.id,
                ContractParty.contact_email.is_not(None),
            )
        )
        if email
    }
    allowed |= {
        email.lower()
        for email in db.scalars(select(User.email).where(User.org_id == org_id))
        if email
    }
    invalid = sorted(
        {
            str(recipient.email).lower()
            for recipient in recipients
            if str(recipient.email).lower() not in allowed
        }
    )
    if invalid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Recipients must be a contract party or an organization user: "
            + ", ".join(invalid),
        )


def _create_signed_version(
    db: Session,
    *,
    user: User,
    contract: Contract,
    base_version: ContractVersion,
) -> ContractVersion:
    contract_file = db.get(ContractFile, base_version.contract_file_id)
    if contract_file is None or contract_file.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract file not found")
    signed = ContractVersion(
        org_id=user.org_id,
        contract_id=contract.id,
        contract_file_id=contract_file.id,
        version_number=next_version_number(db, contract_file.id),
        storage_object_id=base_version.storage_object_id,
        source=ContractVersionSource.SIGNED,
        change_summary="Executed/signed copy",
        is_authoritative=True,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    db.add(signed)
    db.flush()
    base_snapshot = (
        db.get(ContractTextSnapshot, base_version.text_snapshot_id)
        if base_version.text_snapshot_id
        else None
    )
    if base_snapshot is not None:
        snapshot = ContractTextSnapshot(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_version_id=signed.id,
            extraction_method=base_snapshot.extraction_method,
            extraction_quality_score=base_snapshot.extraction_quality_score,
            text=base_snapshot.text,
            page_map=base_snapshot.page_map,
            ocr_provider=base_snapshot.ocr_provider,
            validation_status=base_snapshot.validation_status,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(snapshot)
        db.flush()
        signed.text_snapshot_id = snapshot.id
    versions = db.scalars(
        select(ContractVersion).where(
            ContractVersion.org_id == user.org_id,
            ContractVersion.contract_id == contract.id,
            ContractVersion.deleted_at.is_(None),
        )
    ).all()
    for row in versions:
        row.is_authoritative = row.id == signed.id
        row.updated_by_user_id = user.id
    contract_file.current_version_id = signed.id
    contract_file.updated_by_user_id = user.id
    contract.current_contract_file_id = contract_file.id
    contract.current_authoritative_version_id = signed.id
    contract.updated_by_user_id = user.id
    return signed


def sync_signature_request(
    db: Session,
    *,
    user: User,
    signature_request: SignatureRequest,
    completed: bool = True,
    declined: bool = False,
    request_id: str | None = None,
) -> SignatureRequest:
    """Sync provider status. On completion, create the signed authoritative
    ContractVersion, move the contract to ACTIVE, and re-run extraction.

    In mock DocuSign mode this is invoked manually; with real credentials the
    same path is driven by the provider webhook."""
    if signature_request.status in {SignatureStatus.COMPLETED, SignatureStatus.VOIDED}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Signature request already finalized")

    if declined:
        signature_request.status = SignatureStatus.DECLINED
        signature_request.updated_by_user_id = user.id
        for rcpt in db.scalars(
            select(SignatureRecipient).where(
                SignatureRecipient.signature_request_id == signature_request.id
            )
        ):
            rcpt.status = "declined"
        db.add(
            SignatureEvent(
                org_id=user.org_id,
                signature_request_id=signature_request.id,
                event_type="declined",
                payload={"synced_by": user.id},
                created_by_user_id=user.id,
                updated_by_user_id=user.id,
            )
        )
        write_audit_log(
            db,
            action="signature.declined",
            resource_type="signature_request",
            resource_id=signature_request.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            request_id=request_id,
            after={"contract_id": signature_request.contract_id},
        )
        return signature_request

    if not completed:
        signature_request.status = SignatureStatus.DELIVERED
        signature_request.updated_by_user_id = user.id
        db.add(
            SignatureEvent(
                org_id=user.org_id,
                signature_request_id=signature_request.id,
                event_type="delivered",
                payload={"synced_by": user.id},
                created_by_user_id=user.id,
                updated_by_user_id=user.id,
            )
        )
        return signature_request

    contract = db.get(Contract, signature_request.contract_id)
    if contract is None or contract.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    base_version = db.get(ContractVersion, signature_request.contract_version_id)
    if base_version is None or base_version.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signed base version not found")

    signed_version = _create_signed_version(
        db, user=user, contract=contract, base_version=base_version
    )

    signature_request.status = SignatureStatus.COMPLETED
    signature_request.completed_at = datetime.now(UTC)
    signature_request.signed_contract_version_id = signed_version.id
    signature_request.updated_by_user_id = user.id
    for rcpt in db.scalars(
        select(SignatureRecipient).where(
            SignatureRecipient.signature_request_id == signature_request.id
        )
    ):
        rcpt.status = "completed"
        rcpt.updated_by_user_id = user.id
    db.add(
        SignatureEvent(
            org_id=user.org_id,
            signature_request_id=signature_request.id,
            event_type="completed",
            payload={"signed_contract_version_id": signed_version.id, "synced_by": user.id},
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
    )

    # A SIGNED authoritative version now exists, so the ACTIVE lifecycle gate
    # is satisfied without an override.
    transition_contract_stage(
        db,
        contract=contract,
        to_stage=ContractLifecycleStage.ACTIVE,
        actor_user_id=user.id,
        reason="Signature completed",
        request_id=request_id,
    )
    write_audit_log(
        db,
        action="signature.completed",
        resource_type="signature_request",
        resource_id=signature_request.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
        after={
            "contract_id": contract.id,
            "signed_contract_version_id": signed_version.id,
        },
    )
    write_timeline_event(
        db,
        org_id=user.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="signature.completed",
        title="Contract signed and activated",
        actor_user_id=user.id,
        request_id=request_id,
        details={"signed_contract_version_id": signed_version.id},
    )
    # Re-run metadata/clause/embeddings on the final executed version, then
    # extract obligations + renewal terms now that the contract is ACTIVE.
    requeue_contract_ai_jobs(db, user=user, contract=contract, version=signed_version)
    queue_activation_ai_jobs(db, user=user, contract=contract, version=signed_version)
    return signature_request
