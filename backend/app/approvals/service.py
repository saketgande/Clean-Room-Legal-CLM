import logging
import html
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
from app.core.config import settings
from app.core.enums import ApprovalStatus, ContractLifecycleStage
from app.core.security import create_token_secret, hash_token
from app.integrations.resend import resend_client

logger = logging.getLogger(__name__)

# Approval-link TTL. Reduced from 168h (7d) to 48h to shrink the window in which
# a leaked/forwarded link is live. The decision itself is never made by a GET:
# the email links are hash-fragment URLs ("{base}/#approve?token=...&d=approve")
# whose token+decision live client-side and are NEVER sent to the server on the
# GET. A corporate link-prefetcher / scanner that follows the URL only loads the
# static SPA shell; the state change happens solely on the explicit
# POST /approvals/token-decision the user triggers from that interstitial page.
# This separation is the click-through confirmation, so no server-side GET view
# is added here — doing so would change the API contract the frontend relies on.
APPROVAL_TOKEN_TTL_HOURS = 48  # 2 days


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
    target_version_id = contract_version_id or contract.current_authoritative_version_id
    for target in targets:
        approver_user_id = target.get("approver_user_id")
        approver_role = target.get("approver_role")
        approver = None
        if approver_user_id:
            approver = db.get(User, approver_user_id)
            if approver is None or approver.org_id != user.org_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Approver user must belong to this organization",
                )
        existing_pending = db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.org_id == user.org_id,
                ApprovalRequest.contract_id == contract.id,
                ApprovalRequest.contract_version_id == target_version_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.approver_user_id == approver_user_id,
                ApprovalRequest.approver_role == approver_role,
            )
        )
        if existing_pending is not None:
            requests.append(existing_pending)
            continue
        approval = ApprovalRequest(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_version_id=target_version_id,
            requested_by_user_id=user.id,
            approver_user_id=approver_user_id,
            approver_role=approver_role,
            due_at=datetime.now(UTC)
            + timedelta(days=max(1, settings.approval_default_due_days)),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(approval)
        db.flush()
        token_secret = None
        # None = no email attempted (no resolved approver user). True/False once a
        # send is attempted. Surfaced in the audit record so an operator can see
        # whether the approver was actually notified.
        email_sent: bool | None = None
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
            base = settings.app_base_url.rstrip("/")
            approve_url = f"{base}/#approve?token={token_secret}&d=approve"
            reject_url = f"{base}/#approve?token={token_secret}&d=reject"
            ttl_days = APPROVAL_TOKEN_TTL_HOURS // 24
            expiry_note = (
                f"{ttl_days} day{'s' if ttl_days != 1 else ''}"
                if ttl_days
                else f"{APPROVAL_TOKEN_TTL_HOURS} hours"
            )
            safe_title = html.escape(contract.title or "Untitled contract")
            safe_requester = html.escape(user.full_name or user.email)
            # A notification-send failure must NOT roll back an approval that was
            # already created + tokenized. Catch per-approver, log, and record the
            # outcome via email_sent rather than 500-ing the whole submission.
            try:
                await resend_client.send_email(
                    to=approver.email,
                    subject=f"Approval requested: {contract.title}",
                    html=(
                        f"<div style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px\">"
                        f"<p style=\"font-size:15px;color:#0f172a\">"
                        f"<b>{safe_requester}</b> requested your approval on "
                        f"<b>{safe_title}</b>.</p>"
                        f"<p style=\"margin:24px 0\">"
                        f"<a href=\"{approve_url}\" "
                        f"style=\"display:inline-block;background:#16a34a;color:#fff;"
                        f"padding:11px 22px;border-radius:8px;text-decoration:none;"
                        f"font-weight:600;margin-right:10px\">Approve</a>"
                        f"<a href=\"{reject_url}\" "
                        f"style=\"display:inline-block;background:#dc2626;color:#fff;"
                        f"padding:11px 22px;border-radius:8px;text-decoration:none;"
                        f"font-weight:600\">Reject</a>"
                        f"</p>"
                        f"<p style=\"font-size:12px;color:#64748b\">"
                        f"This secure link expires in {expiry_note} and can be used once. "
                        f"If you didn't expect this request, you can ignore this email.</p>"
                        f"</div>"
                    ),
                )
                email_sent = True
            except Exception:
                logger.exception(
                    "approval notification email failed",
                    extra={"approval_request_id": approval.id},
                )
                email_sent = False
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
                "email_sent": email_sent,
            },
        )
        requests.append(approval)

    if contract.lifecycle_stage != ContractLifecycleStage.APPROVAL_PENDING:
        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.APPROVAL_PENDING,
            actor_user_id=user.id,
            reason="Submitted for approval",
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
    if contract is None or contract.org_id != approval.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    if contract.lifecycle_stage != ContractLifecycleStage.APPROVAL_PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Approval can only be decided while the contract is approval pending",
        )
    siblings = db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.org_id == approval.org_id,
            ApprovalRequest.contract_id == approval.contract_id,
            ApprovalRequest.contract_version_id == approval.contract_version_id,
            ApprovalRequest.id != approval.id,
            ApprovalRequest.status != ApprovalStatus.CANCELLED,
        )
    ).all()
    if decision == "reject":
        for sibling in siblings:
            if sibling.status == ApprovalStatus.PENDING:
                sibling.status = ApprovalStatus.CANCELLED
                sibling.updated_by_user_id = actor_user_id
        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.INTERNAL_REVIEW,
            actor_user_id=actor_user_id,
            reason=comment,
            override=True,
            override_authorized=True,
            request_id=request_id,
        )
    elif all(sibling.status == ApprovalStatus.APPROVED for sibling in siblings):
        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.APPROVED,
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
    # F-02: every token-failure path returns the SAME 401 so the response can't be
    # used as an oracle to distinguish "fake" from "real-but-expired/used" tokens.
    # The specific reason is logged server-side for security monitoring only.
    def _reject(reason: str) -> HTTPException:
        logger.warning("approval token rejected", extra={"reason": reason})
        return HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired approval token"
        )

    row = db.scalar(
        select(ApprovalToken)
        .where(ApprovalToken.token_hash == hash_token(token))
        .with_for_update()
    )
    if row is None:
        raise _reject("not_found")
    if row.used_at is not None:
        raise _reject("already_used")
    if row.expires_at < datetime.now(UTC):
        raise _reject("expired")
    approval = db.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == row.approval_request_id)
        .with_for_update()
    )
    if approval is None or approval.org_id != row.org_id:
        raise _reject("request_missing_or_org_mismatch")
    # Single-use: consume the token atomically with the decision.
    row.used_at = datetime.now(UTC)
    approver = db.scalar(
        select(User).where(
            User.org_id == row.org_id,
            User.email == row.intended_approver_email,
        )
    )
    if approver is not None and approver.id == approval.requested_by_user_id:
        raise _reject("requester_cannot_approve_own_request")
    return _apply_decision(
        db,
        approval=approval,
        decision=decision,
        comment=comment,
        actor_user_id=approver.id if approver else None,
        actor_label=f"token:{row.intended_approver_email}",
        request_id=request_id,
    )
