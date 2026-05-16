from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRoutingRule,
    ApprovalToken,
)
from app.auth.models import User
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.enums import ApprovalStatus, ContractLifecycleStage
from app.core.security import create_token_secret, hash_token
from app.integrations.resend import resend_client

APPROVAL_TOKEN_TTL_HOURS = 168  # 7 days


def _matches(rule: ApprovalRoutingRule, contract: Contract) -> bool:
    criteria = rule.criteria or {}
    if not criteria:
        return True
    for key, expected in criteria.items():
        if key == "min_value":
            if (contract.value_amount or 0) < float(expected):
                return False
            continue
        actual = getattr(contract, key, None)
        if actual is None:
            return False
        if str(actual).strip().lower() != str(expected).strip().lower():
            return False
    return True


def evaluate_routing(db: Session, *, contract: Contract, org_id: str) -> list[dict]:
    """Return [{approver_user_id, approver_role}] from active routing rules that
    match the contract. Empty list means no rule matched (caller falls back)."""
    rules = db.scalars(
        select(ApprovalRoutingRule).where(
            ApprovalRoutingRule.org_id == org_id,
            ApprovalRoutingRule.is_active.is_(True),
        )
    ).all()
    matched = sorted(
        (r for r in rules if _matches(r, contract)),
        key=lambda r: int(r.priority) if str(r.priority).isdigit() else 100,
    )
    return [
        {"approver_user_id": r.approver_user_id, "approver_role": r.approver_role}
        for r in matched
    ]


async def submit_contract_for_approval(
    db: Session,
    *,
    user: User,
    contract: Contract,
    contract_version_id: str | None,
    approver_user_id: str | None,
    approver_role: str | None,
    request_id: str | None = None,
) -> list[ApprovalRequest]:
    targets = evaluate_routing(db, contract=contract, org_id=user.org_id)
    if not targets:
        targets = [{"approver_user_id": approver_user_id, "approver_role": approver_role}]

    requests: list[ApprovalRequest] = []
    for target in targets:
        approval = ApprovalRequest(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_version_id=contract_version_id or contract.current_authoritative_version_id,
            requested_by_user_id=user.id,
            approver_user_id=target.get("approver_user_id"),
            approver_role=target.get("approver_role"),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(approval)
        db.flush()
        token_secret = None
        approver = (
            db.get(User, approval.approver_user_id) if approval.approver_user_id else None
        )
        if approver is not None:
            token_secret = create_token_secret("apvl_")
            db.add(
                ApprovalToken(
                    org_id=user.org_id,
                    approval_request_id=approval.id,
                    intended_approver_email=approver.email.lower(),
                    token_hash=hash_token(token_secret),
                    expires_at=datetime.now(UTC) + timedelta(hours=APPROVAL_TOKEN_TTL_HOURS),
                    created_by_user_id=user.id,
                    updated_by_user_id=user.id,
                )
            )
            await resend_client.send_email(
                to=approver.email,
                subject=f"Approval requested: {contract.title}",
                html=(
                    f"<p>{user.full_name} requested your approval on "
                    f"<b>{contract.title}</b>.</p>"
                    f"<p>Decision token: <code>{token_secret}</code></p>"
                ),
            )
        write_audit_log(
            db,
            action="approval.requested",
            resource_type="approval_request",
            resource_id=approval.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            request_id=request_id,
            after={
                "contract_id": contract.id,
                "approver_user_id": approval.approver_user_id,
                "approver_role": approval.approver_role,
                "token_issued": token_secret is not None,
            },
        )
        requests.append(approval)

    transition_contract_stage(
        db,
        contract=contract,
        to_stage=ContractLifecycleStage.APPROVAL_PENDING,
        actor_user_id=user.id,
        reason="Submitted for approval",
        override=True,
        override_authorized=True,
        request_id=request_id,
    )
    return requests


def _apply_decision(
    db: Session,
    *,
    approval: ApprovalRequest,
    decision: str,
    comment: str | None,
    actor_user_id: str | None,
    actor_label: str,
    request_id: str | None = None,
) -> ApprovalRequest:
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval request is already decided")
    if decision == "reject" and not comment:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Rejection requires a comment")
    approval.status = (
        ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
    )
    approval.updated_by_user_id = actor_user_id
    db.add(
        ApprovalDecision(
            org_id=approval.org_id,
            approval_request_id=approval.id,
            approver_user_id=actor_user_id,
            decision=decision,
            comment=comment,
            decided_at=datetime.now(UTC),
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
    )
    contract = db.get(Contract, approval.contract_id)
    transition_contract_stage(
        db,
        contract=contract,
        to_stage=ContractLifecycleStage.APPROVED
        if decision == "approve"
        else ContractLifecycleStage.INTERNAL_REVIEW,
        actor_user_id=actor_user_id,
        reason=comment,
        override=True,
        override_authorized=True,
        request_id=request_id,
    )
    write_audit_log(
        db,
        action="approval.decided",
        resource_type="approval_request",
        resource_id=approval.id,
        org_id=approval.org_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        after={"decision": decision, "decided_by": actor_label, "comment": comment},
    )
    write_timeline_event(
        db,
        org_id=approval.org_id,
        resource_type="contract",
        resource_id=approval.contract_id,
        event_type="approval.decided",
        title=f"Approval {decision}d",
        actor_user_id=actor_user_id,
        request_id=request_id,
        details={"approval_request_id": approval.id, "decided_by": actor_label},
    )
    return approval


def decide_in_app(
    db: Session,
    *,
    user: User,
    approval: ApprovalRequest,
    decision: str,
    comment: str | None,
    request_id: str | None = None,
) -> ApprovalRequest:
    return _apply_decision(
        db,
        approval=approval,
        decision=decision,
        comment=comment,
        actor_user_id=user.id,
        actor_label=f"user:{user.id}",
        request_id=request_id,
    )


def redeem_token_decision(
    db: Session,
    *,
    token: str,
    decision: str,
    comment: str | None,
    request_id: str | None = None,
) -> ApprovalRequest:
    row = db.scalar(
        select(ApprovalToken).where(ApprovalToken.token_hash == hash_token(token))
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval token not found")
    if row.used_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval token already used")
    if row.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval token expired")
    approval = db.get(ApprovalRequest, row.approval_request_id)
    if approval is None or approval.org_id != row.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    # Single-use: consume the token atomically with the decision.
    row.used_at = datetime.now(UTC)
    approver = db.scalar(
        select(User).where(
            User.org_id == row.org_id,
            User.email == row.intended_approver_email,
        )
    )
    return _apply_decision(
        db,
        approval=approval,
        decision=decision,
        comment=comment,
        actor_user_id=approver.id if approver else None,
        actor_label=f"token:{row.intended_approver_email}",
        request_id=request_id,
    )
