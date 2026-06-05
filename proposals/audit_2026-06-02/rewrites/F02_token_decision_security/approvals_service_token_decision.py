"""F-02 rewrite — ``redeem_token_decision`` (uniform error + audit-on-attempt).

Replaces ``backend/app/approvals/service.py:256-292``. Collapses the
``404/409/409`` triad (not-found / used / expired) into a single 401
"Invalid or expired approval token" response so the endpoint stops
leaking token-state to unauthenticated callers (Agent 2 F-02). Adds an
``approval.token_attempt`` audit row on every redemption call,
successful or not, so abuse detection has a signal. Records actor
identity as ``token:<email>`` and tags the original approver's user_id
when resolvable (so audit reviewers can distinguish a deactivated
approver from a system actor — Agent 2 F-02b).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalRequest, ApprovalToken
from app.approvals.service import _apply_decision  # type: ignore[import-not-found]
from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.security import hash_token

_logger = logging.getLogger(__name__)

_UNIFORM_TOKEN_ERROR_DETAIL: str = "Invalid or expired approval token"


def redeem_token_decision(
    db: Session,
    *,
    token: str,
    decision: str,
    comment: str | None,
    request_id: str | None = None,
    remote_ip: str | None = None,
) -> ApprovalRequest:
    """Apply a token-bound approval decision; uniform 401 on every failure mode."""
    token_hash = hash_token(token)
    row = db.scalar(select(ApprovalToken).where(ApprovalToken.token_hash == token_hash))
    failure_reason: str | None = None
    org_id: str | None = None
    intended_email: str | None = None
    if row is None:
        failure_reason = "not_found"
    else:
        org_id = row.org_id
        intended_email = row.intended_approver_email
        if row.used_at is not None:
            failure_reason = "already_used"
        elif row.expires_at < datetime.now(UTC):
            failure_reason = "expired"

    if failure_reason is not None:
        _audit_token_attempt(
            db,
            request_id=request_id,
            remote_ip=remote_ip,
            outcome="rejected",
            reason=failure_reason,
            org_id=org_id,
            intended_email=intended_email,
            decision=decision,
        )
        _logger.info(
            "approval.token_attempt.rejected",
            extra={
                "reason": failure_reason,
                "request_id": request_id,
                "remote_ip": remote_ip,
                "org_id": org_id,
            },
        )
        # Uniform 401 — caller cannot distinguish missing / used / expired.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, _UNIFORM_TOKEN_ERROR_DETAIL
        )

    assert row is not None  # for type checkers
    approval = db.get(ApprovalRequest, row.approval_request_id)
    if approval is None or approval.org_id != row.org_id:
        _audit_token_attempt(
            db,
            request_id=request_id,
            remote_ip=remote_ip,
            outcome="rejected",
            reason="approval_missing",
            org_id=row.org_id,
            intended_email=row.intended_approver_email,
            decision=decision,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, _UNIFORM_TOKEN_ERROR_DETAIL
        )

    row.used_at = datetime.now(UTC)
    approver = db.scalar(
        select(User).where(
            User.org_id == row.org_id,
            User.email == row.intended_approver_email,
        )
    )
    actor_label = (
        f"token:{row.intended_approver_email}"
        if approver is not None
        else f"token:{row.intended_approver_email} (deactivated_user)"
    )

    _audit_token_attempt(
        db,
        request_id=request_id,
        remote_ip=remote_ip,
        outcome="accepted",
        reason=None,
        org_id=row.org_id,
        intended_email=row.intended_approver_email,
        decision=decision,
        approval_request_id=approval.id,
        approver_user_id=approver.id if approver else None,
    )
    return _apply_decision(
        db,
        approval=approval,
        decision=decision,
        comment=comment,
        actor_user_id=approver.id if approver else None,
        actor_label=actor_label,
        request_id=request_id,
    )


def _audit_token_attempt(
    db: Session,
    *,
    request_id: str | None,
    remote_ip: str | None,
    outcome: str,
    reason: str | None,
    org_id: str | None,
    intended_email: str | None,
    decision: str,
    approval_request_id: str | None = None,
    approver_user_id: str | None = None,
) -> None:
    """Write a per-attempt audit row (success or failure) for abuse-detection."""
    try:
        write_audit_log(
            db,
            action="approval.token_attempt",
            resource_type="approval_token",
            resource_id=approval_request_id,
            org_id=org_id,
            actor_user_id=approver_user_id,
            request_id=request_id,
            after={
                "outcome": outcome,
                "reason": reason,
                "intended_approver_email": intended_email,
                "decision": decision,
                "remote_ip": remote_ip,
            },
        )
    except Exception:  # noqa: BLE001 - audit failure must not break the request path here
        # F-12 will eventually make audit-write failure propagate; until then,
        # log structurally so the gap is visible.
        _logger.exception(
            "approval.token_attempt.audit_failed",
            extra={
                "request_id": request_id,
                "org_id": org_id,
                "outcome": outcome,
                "reason": reason,
            },
        )
