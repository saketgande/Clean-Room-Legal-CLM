from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalRequest, ApprovalRoutingRule
from app.approvals.service import (
    decide_in_app,
    redeem_token_decision,
    submit_contract_for_approval,
)
from app.contracts.models import Contract
from app.contracts.access import user_can_access_contract
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.core.rbac import has_permission

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalSubmit(BaseModel):
    contract_id: str
    contract_version_id: str | None = None
    approver_user_id: str | None = None
    approver_role: str | None = None


class ApprovalDecisionPayload(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str | None = None


class TokenDecisionPayload(BaseModel):
    token: str = Field(min_length=8)
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str | None = None


class RoutingRulePayload(BaseModel):
    name: str
    priority: str = "100"
    criteria: dict = Field(default_factory=dict)
    approver_role: str | None = None
    approver_user_id: str | None = None
    is_active: bool = True


@router.get("")
def list_approvals(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:read")),
):
    rows = db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.org_id == current_user.org_id)
    ).all()
    return [row for row in rows if _can_view_approval(db, approval=row, user=current_user)]


@router.get("/routing-rules")
def list_routing_rules(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    return db.scalars(
        select(ApprovalRoutingRule).where(ApprovalRoutingRule.org_id == current_user.org_id)
    ).all()


@router.post("/routing-rules", status_code=status.HTTP_201_CREATED)
def create_routing_rule(
    payload: RoutingRulePayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    rule = ApprovalRoutingRule(
        org_id=current_user.org_id,
        name=payload.name,
        priority=payload.priority,
        criteria=payload.criteria,
        approver_role=payload.approver_role,
        approver_user_id=payload.approver_user_id,
        is_active=payload.is_active,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def submit_for_approval(
    payload: ApprovalSubmit,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:approve")),
):
    contract = get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    requests = await submit_contract_for_approval(
        db,
        user=current_user,
        contract=contract,
        contract_version_id=payload.contract_version_id,
        approver_user_id=payload.approver_user_id,
        approver_role=payload.approver_role,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    for approval in requests:
        db.refresh(approval)
    return requests


@router.post("/requests/{approval_request_id}/decision")
def decide_approval(
    approval_request_id: str,
    payload: ApprovalDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:decide")),
):
    approval = db.get(ApprovalRequest, approval_request_id)
    if approval is None or approval.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    if not _can_decide_approval(approval=approval, user=current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not assigned to decide this approval")
    decide_in_app(
        db,
        user=current_user,
        approval=approval,
        decision=payload.decision,
        comment=payload.comment,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    db.refresh(approval)
    return approval


@router.post("/token-decision")
def decide_via_token(
    payload: TokenDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """Token-authenticated approval decision. No session auth: the single-use,
    expiring, email-bound token is the credential."""
    approval = redeem_token_decision(
        db,
        token=payload.token,
        decision=payload.decision,
        comment=payload.comment,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    db.refresh(approval)
    return {
        "approval_request_id": approval.id,
        "status": approval.status,
        "contract_id": approval.contract_id,
    }


def _user_role_names(user) -> set[str]:
    return {role.name for role in getattr(user, "roles", [])}


def _can_decide_approval(*, approval: ApprovalRequest, user) -> bool:
    if has_permission(user.permission_values, "approval:admin"):
        return True
    if approval.approver_user_id and approval.approver_user_id == user.id:
        return True
    if approval.approver_role and approval.approver_role in _user_role_names(user):
        return True
    return False


def _can_view_approval(db: Session, *, approval: ApprovalRequest, user) -> bool:
    if _can_decide_approval(approval=approval, user=user):
        return True
    if approval.requested_by_user_id == user.id:
        return True
    contract = db.get(Contract, approval.contract_id)
    return bool(contract and user_can_access_contract(db, contract=contract, user=user))
