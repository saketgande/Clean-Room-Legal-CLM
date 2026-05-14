from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalDecision, ApprovalRequest
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.core.enums import ApprovalStatus, ContractLifecycleStage

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalSubmit(BaseModel):
    contract_id: str
    contract_version_id: str | None = None
    approver_user_id: str | None = None
    approver_role: str | None = None


class ApprovalDecisionPayload(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str | None = None


@router.get("")
def list_approvals(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:read")),
):
    return db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.org_id == current_user.org_id)
    ).all()


@router.post("/requests")
def submit_for_approval(
    payload: ApprovalSubmit,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:approve")),
):
    contract = get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    approval = ApprovalRequest(
        org_id=current_user.org_id,
        contract_id=payload.contract_id,
        contract_version_id=payload.contract_version_id or contract.current_authoritative_version_id,
        requested_by_user_id=current_user.id,
        approver_user_id=payload.approver_user_id,
        approver_role=payload.approver_role,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(approval)
    transition_contract_stage(
        db,
        contract=contract,
        to_stage=ContractLifecycleStage.APPROVAL_PENDING,
        actor_user_id=current_user.id,
        reason="Submitted for approval",
        override=True,
    )
    db.commit()
    db.refresh(approval)
    return approval


@router.post("/requests/{approval_request_id}/decision")
def decide_approval(
    approval_request_id: str,
    payload: ApprovalDecisionPayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:decide")),
):
    approval = db.get(ApprovalRequest, approval_request_id)
    if approval is None or approval.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval request is already decided")
    if payload.decision == "reject" and not payload.comment:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Rejection requires a comment")
    approval.status = ApprovalStatus.APPROVED if payload.decision == "approve" else ApprovalStatus.REJECTED
    db.add(
        ApprovalDecision(
            org_id=current_user.org_id,
            approval_request_id=approval.id,
            approver_user_id=current_user.id,
            decision=payload.decision,
            comment=payload.comment,
            decided_at=datetime.now(UTC),
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
    )
    contract = get_contract_for_user(db, contract_id=approval.contract_id, user=current_user)
    transition_contract_stage(
        db,
        contract=contract,
        to_stage=ContractLifecycleStage.APPROVED
        if payload.decision == "approve"
        else ContractLifecycleStage.INTERNAL_REVIEW,
        actor_user_id=current_user.id,
        reason=payload.comment,
        override=True,
    )
    db.commit()
    db.refresh(approval)
    return approval
