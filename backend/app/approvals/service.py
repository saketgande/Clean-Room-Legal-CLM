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
    ApproverGroup,
)
from app.auth.models import User
from app.contract_files.models import ContractTextSnapshot, ContractVersion
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

# Functional approver pools seeded for every org. These are *groups* (data), not
# permission-bearing RBAC roles — every member is gated by the single
# ``approval:decide`` permission, so the names don't multiply roles.
DEFAULT_APPROVER_GROUPS: list[tuple[str, str]] = [
    ("Legal Counsel", "Reviews terms, enforceability, and legal risk."),
    ("Finance", "Reviews pricing, payment terms, budget, and revenue impact."),
    ("Procurement", "Reviews supplier terms and sourcing-policy compliance."),
    ("Compliance", "Reviews regulatory, privacy, and security requirements."),
    ("Executive", "Final sign-off for high-value or strategic agreements."),
]


def ensure_default_approver_groups(
    db: Session, *, org_id: str, actor_user_id: str | None = None
) -> list[ApproverGroup]:
    """Idempotently create the default (empty) approver groups for an org so the
    routing form has real options to pick from. Admins then add members."""
    existing = {
        g.name
        for g in db.scalars(select(ApproverGroup).where(ApproverGroup.org_id == org_id)).all()
    }
    created: list[ApproverGroup] = []
    for name, description in DEFAULT_APPROVER_GROUPS:
        if name in existing:
            continue
        group = ApproverGroup(
            org_id=org_id,
            name=name,
            description=description,
            is_active=True,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        db.add(group)
        created.append(group)
    return created


def _matches(rule: ApprovalRoutingRule, contract: Contract) -> bool:
    """Does this routing rule's criteria match the contract?

    Supported criteria keys (all optional; empty criteria = match everything):
      min_value / max_value        — numeric bounds on contract value
      contract_type(s)             — string or list, case-insensitive
      risk_band(s)                 — string or list vs risk_band/risk_level
      any other key                — exact (case-insensitive) match against the
                                     contract attribute of the same name
    """
    criteria = rule.criteria or {}
    if not criteria:
        return True
    for key, expected in criteria.items():
        if key == "min_value":
            if (contract.value_amount or 0) < float(expected):
                return False
        elif key == "max_value":
            if (contract.value_amount or 0) > float(expected):
                return False
        elif key in {"contract_type", "contract_types"}:
            allowed = expected if isinstance(expected, list) else [expected]
            ct = (contract.contract_type or "").strip().lower()
            if ct not in {str(a).strip().lower() for a in allowed}:
                return False
        elif key in {"risk_band", "risk_bands"}:
            allowed = expected if isinstance(expected, list) else [expected]
            band = (contract.risk_band or contract.risk_level or "").strip().lower()
            if band not in {str(a).strip().lower() for a in allowed}:
                return False
        else:
            actual = getattr(contract, key, None)
            if actual is None:
                return False
            if str(actual).strip().lower() != str(expected).strip().lower():
                return False
    return True


_NDA_TYPES = {"nda", "non_disclosure_agreement", "non-disclosure agreement", "mutual nda"}


def _fast_lane_reason(db: Session, *, contract: Contract) -> str | None:
    """Low-risk NDA under the value cap with no open high-severity deviations
    → skip Approval, straight to Signature. Returns the audit reason, or None."""
    from sqlalchemy import func as _func

    from app.playbooks.models import PlaybookDeviation

    if not settings.nda_fast_lane_enabled:
        return None
    if (contract.contract_type or "").strip().lower() not in _NDA_TYPES:
        return None
    band = (getattr(contract, "risk_band", None) or contract.risk_level or "").lower()
    if band != "low":
        return None
    if (contract.value_amount or 0) > settings.nda_fast_lane_max_value:
        return None
    open_high = db.scalar(
        select(_func.count(PlaybookDeviation.id)).where(
            PlaybookDeviation.contract_id == contract.id,
            PlaybookDeviation.status.in_(["open", "needs_review"]),
            PlaybookDeviation.severity.in_(["high", "critical"]),
        )
    )
    if open_high:
        return None
    return (
        "Fast-lane: low-risk NDA auto-approved to signature "
        f"(risk {band or 'low'}, value within cap, no open high-severity deviations)"
    )


def resolve_chain(db: Session, *, contract: Contract, org_id: str) -> list[dict]:
    """Pick the single best-matching active rule (lowest priority number) and
    return its ordered approval chain as a list of step targets:
    ``{routing_rule_id, step_order, approver_user_id, approver_group_id,
    approver_role, mode}``. Empty list means no rule matched (caller falls back
    to the manually-specified approver)."""
    rules = db.scalars(
        select(ApprovalRoutingRule).where(
            ApprovalRoutingRule.org_id == org_id,
            ApprovalRoutingRule.is_active.is_(True),
        )
    ).all()
    matched = sorted(
        (r for r in rules if _matches(r, contract)),
        key=lambda r: (
            int(r.priority) if str(r.priority).isdigit() else 100,
            -len(r.criteria or {}),
        ),
    )
    if not matched:
        return []
    rule = matched[0]
    steps = sorted(rule.steps, key=lambda s: s.step_order)
    if steps:
        return [
            {
                "routing_rule_id": rule.id,
                "step_order": idx + 1,
                "approver_user_id": step.approver_user_id,
                "approver_group_id": step.approver_group_id,
                "approver_role": step.approver_role,
                "mode": step.mode or "any",
            }
            for idx, step in enumerate(steps)
        ]
    # Legacy single-approver rule (no steps) → a one-step chain.
    return [
        {
            "routing_rule_id": rule.id,
            "step_order": 1,
            "approver_user_id": rule.approver_user_id,
            "approver_group_id": None,
            "approver_role": rule.approver_role,
            "mode": "any",
        }
    ]


def _step_recipients(db: Session, *, approval: ApprovalRequest) -> list[User]:
    """Users who should be emailed when ``approval`` becomes active: the named
    user, or every active member of the assigned group. Role-only steps have no
    direct recipients (those approvers act in-app)."""
    if approval.approver_user_id:
        user = db.get(User, approval.approver_user_id)
        return [user] if user and user.org_id == approval.org_id else []
    if approval.approver_group_id:
        group = db.get(ApproverGroup, approval.approver_group_id)
        if group is None or group.org_id != approval.org_id:
            return []
        return [m for m in group.members if m.org_id == approval.org_id]
    return []


async def _activate_step(
    db: Session, *, approval: ApprovalRequest, contract: Contract, requester: User | None
) -> bool | None:
    """Issue single-use tokens + send the approve/reject email to every recipient
    of a now-active step. Returns True/False once a send is attempted, or None
    when there is no direct recipient (role-only / unresolved)."""
    recipients = _step_recipients(db, approval=approval)
    if not recipients:
        return None

    base = settings.app_base_url.rstrip("/")
    ttl_days = APPROVAL_TOKEN_TTL_HOURS // 24
    expiry_note = (
        f"{ttl_days} day{'s' if ttl_days != 1 else ''}"
        if ttl_days
        else f"{APPROVAL_TOKEN_TTL_HOURS} hours"
    )
    safe_title = html.escape(contract.title or "Untitled contract")
    safe_requester = html.escape(
        (requester.full_name or requester.email) if requester else "A colleague"
    )
    actor_id = requester.id if requester else None

    any_sent = False
    for approver in recipients:
        token_secret = create_token_secret("apvl_")
        db.add(
            ApprovalToken(
                org_id=approval.org_id,
                approval_request_id=approval.id,
                intended_approver_email=approver.email.lower(),
                token_hash=hash_token(token_secret),
                expires_at=datetime.now(UTC) + timedelta(hours=APPROVAL_TOKEN_TTL_HOURS),
                created_by_user_id=actor_id,
                updated_by_user_id=actor_id,
            )
        )
        review_url = f"{base}/approve/{token_secret}"
        # A notification-send failure must NOT roll back an approval that was
        # already created + tokenized. Catch per-recipient and log.
        try:
            await resend_client.send_email(
                to=approver.email,
                subject=f"Approval requested: {contract.title}",
                html=(
                    f"<div style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px\">"
                    f"<p style=\"font-size:15px;color:#0f172a\">"
                    f"<b>{safe_requester}</b> requested your approval on "
                    f"<b>{safe_title}</b>.</p>"
                    f"<p style=\"font-size:14px;color:#475569\">"
                    f"Open the secure link to read the document and approve or reject.</p>"
                    f"<p style=\"margin:24px 0\">"
                    f"<a href=\"{review_url}\" "
                    f"style=\"display:inline-block;background:#4f46e5;color:#fff;"
                    f"padding:11px 22px;border-radius:8px;text-decoration:none;"
                    f"font-weight:600\">Review document &amp; decide</a>"
                    f"</p>"
                    f"<p style=\"font-size:12px;color:#64748b\">"
                    f"This secure link expires in {expiry_note} and can be used once. "
                    f"If you didn't expect this request, you can ignore this email.</p>"
                    f"</div>"
                ),
            )
            any_sent = True
        except Exception:
            logger.exception(
                "approval notification email failed",
                extra={"approval_request_id": approval.id},
            )
    return any_sent


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
    # Approval applies to pre-execution contracts. Submitting one that's already
    # in signing / active / closed makes no sense — block it clearly. (From
    # intake/drafting/review the submit auto-advances the stage to APPROVAL.)
    if contract.lifecycle_stage in {
        ContractLifecycleStage.SIGNATURE,
        ContractLifecycleStage.ACTIVE,
        ContractLifecycleStage.CLOSED,
    }:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot submit a contract in '{contract.lifecycle_stage}' for approval",
        )
    target_version_id = contract_version_id or contract.current_authoritative_version_id

    # If a chain is already live for this contract+version, return it unchanged
    # (idempotent re-submit) rather than stacking a second chain.
    existing = db.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == user.org_id,
            ApprovalRequest.contract_id == contract.id,
            ApprovalRequest.contract_version_id == target_version_id,
            ApprovalRequest.status.in_([ApprovalStatus.PENDING, ApprovalStatus.WAITING]),
        )
        .order_by(ApprovalRequest.step_order)
    ).all()
    if existing:
        return existing

    # Routine contracts route themselves: an eligible NDA skips the approval
    # chain entirely and lands in Signature with a full audit trail.
    fast_lane = _fast_lane_reason(db, contract=contract)
    if fast_lane:
        from app.notifications.models import Notification

        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.SIGNATURE,
            actor_user_id=user.id,
            reason=fast_lane,
            override=True,
            override_authorized=True,
            request_id=request_id,
        )
        write_audit_log(
            db,
            action="approval.fast_laned",
            resource_type="contract",
            resource_id=contract.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            request_id=request_id,
            metadata={"reason": fast_lane},
        )
        recipients = {user.id}
        if contract.owner_user_id:
            recipients.add(contract.owner_user_id)
        for uid in recipients:
            db.add(
                Notification(
                    org_id=user.org_id,
                    user_id=uid,
                    channel="in_app",
                    event_type="approval.fast_laned",
                    subject=f"Fast-laned to signature: {contract.title}",
                    body=(
                        f'"{contract.title}" qualified for the NDA fast-lane '
                        "(low risk, within value cap, no open high-severity issues) "
                        "and skipped approval — it is ready to send for signature."
                    ),
                    status="sent",
                )
            )
        db.commit()
        return []

    chain = resolve_chain(db, contract=contract, org_id=user.org_id)
    if not chain:
        # No rule matched → fall back to the explicitly-supplied approver as a
        # single step (preserves the manual "submit to X" behaviour).
        chain = [
            {
                "routing_rule_id": None,
                "step_order": 1,
                "approver_user_id": approver_user_id,
                "approver_group_id": None,
                "approver_role": approver_role,
                "mode": "any",
            }
        ]

    due_at = datetime.now(UTC) + timedelta(days=max(1, settings.approval_default_due_days))
    requests: list[ApprovalRequest] = []
    for target in chain:
        # Validate referenced approver/group belong to this org.
        if target["approver_user_id"]:
            approver = db.get(User, target["approver_user_id"])
            if approver is None or approver.org_id != user.org_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Approver user must belong to this organization",
                )
        if target["approver_group_id"]:
            group = db.get(ApproverGroup, target["approver_group_id"])
            if group is None or group.org_id != user.org_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Approver group must belong to this organization",
                )

        is_first = target["step_order"] == 1
        approval = ApprovalRequest(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_version_id=target_version_id,
            requested_by_user_id=user.id,
            approver_user_id=target["approver_user_id"],
            approver_role=target["approver_role"],
            approver_group_id=target["approver_group_id"],
            routing_rule_id=target["routing_rule_id"],
            step_order=target["step_order"],
            status=ApprovalStatus.PENDING if is_first else ApprovalStatus.WAITING,
            due_at=due_at,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(approval)
        db.flush()

        email_sent: bool | None = None
        if is_first:
            email_sent = await _activate_step(
                db, approval=approval, contract=contract, requester=user
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
                "step_order": approval.step_order,
                "status": approval.status,
                "approver_user_id": approval.approver_user_id,
                "approver_group_id": approval.approver_group_id,
                "approver_role": approval.approver_role,
                "email_sent": email_sent,
            },
        )
        requests.append(approval)

    if contract.lifecycle_stage != ContractLifecycleStage.APPROVAL:
        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.APPROVAL,
            actor_user_id=user.id,
            reason="Submitted for approval",
            override=True,
            override_authorized=True,
            request_id=request_id,
        )
    return requests


async def _apply_decision(
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
    if contract.lifecycle_stage != ContractLifecycleStage.APPROVAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Approval can only be decided while the contract is in the approval stage",
        )

    # The active chain = the other non-cancelled steps for this contract+version.
    # Only one chain is ever live at a time (submit is idempotent), so its
    # WAITING rows uniquely identify the steps still ahead.
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
        # Reject short-circuits the whole chain and sends the contract back.
        for sibling in siblings:
            if sibling.status in (ApprovalStatus.PENDING, ApprovalStatus.WAITING):
                sibling.status = ApprovalStatus.CANCELLED
                sibling.updated_by_user_id = actor_user_id
        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.REVIEW,
            actor_user_id=actor_user_id,
            reason=comment,
            override=True,
            override_authorized=True,
            request_id=request_id,
        )
    else:
        # Approve → activate the next waiting step, or finish the chain.
        waiting_ahead = sorted(
            (
                s
                for s in siblings
                if s.status == ApprovalStatus.WAITING and s.step_order > approval.step_order
            ),
            key=lambda s: s.step_order,
        )
        if waiting_ahead:
            next_step = waiting_ahead[0]
            next_step.status = ApprovalStatus.PENDING
            next_step.updated_by_user_id = actor_user_id
            requester = db.get(User, next_step.requested_by_user_id)
            email_sent = await _activate_step(
                db, approval=next_step, contract=contract, requester=requester
            )
            write_audit_log(
                db,
                action="approval.step_activated",
                resource_type="approval_request",
                resource_id=next_step.id,
                org_id=approval.org_id,
                actor_user_id=actor_user_id,
                request_id=request_id,
                after={"step_order": next_step.step_order, "email_sent": email_sent},
            )
        else:
            # Last step approved → the whole chain is done, so the contract
            # auto-advances into the signature (execution) stage.
            transition_contract_stage(
                db,
                contract=contract,
                to_stage=ContractLifecycleStage.SIGNATURE,
                actor_user_id=actor_user_id,
                reason="Approval chain completed",
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
        after={
            "decision": decision,
            "decided_by": actor_label,
            "step_order": approval.step_order,
            "comment": comment,
        },
    )
    write_timeline_event(
        db,
        org_id=approval.org_id,
        resource_type="contract",
        resource_id=approval.contract_id,
        event_type="approval.decided",
        title=f"Approval {decision}d (step {approval.step_order})",
        actor_user_id=actor_user_id,
        request_id=request_id,
        details={"approval_request_id": approval.id, "decided_by": actor_label},
    )
    return approval


async def decide_in_app(
    db: Session,
    *,
    user: User,
    approval: ApprovalRequest,
    decision: str,
    comment: str | None,
    request_id: str | None = None,
) -> ApprovalRequest:
    return await _apply_decision(
        db,
        approval=approval,
        decision=decision,
        comment=comment,
        actor_user_id=user.id,
        actor_label=f"user:{user.id}",
        request_id=request_id,
    )


async def redeem_token_decision(
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
    return await _apply_decision(
        db,
        approval=approval,
        decision=decision,
        comment=comment,
        actor_user_id=approver.id if approver else None,
        actor_label=f"token:{row.intended_approver_email}",
        request_id=request_id,
    )


def get_review_context_for_token(db: Session, *, token: str) -> dict:
    """Read-only context for the emailed approver: the document text plus what
    they're being asked to approve. Does NOT consume the token — that only
    happens when they actually decide via /approvals/token-decision."""

    def _reject() -> HTTPException:
        return HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired approval link"
        )

    row = db.scalar(select(ApprovalToken).where(ApprovalToken.token_hash == hash_token(token)))
    if row is None or row.expires_at < datetime.now(UTC):
        raise _reject()
    approval = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == row.approval_request_id)
    )
    if approval is None or approval.org_id != row.org_id:
        raise _reject()

    contract = db.get(Contract, approval.contract_id)
    requester = db.get(User, approval.requested_by_user_id)
    version_id = approval.contract_version_id or (
        contract.current_authoritative_version_id if contract else None
    )
    text = ""
    if version_id:
        version = db.get(ContractVersion, version_id)
        snapshot = (
            db.get(ContractTextSnapshot, version.text_snapshot_id)
            if version and version.text_snapshot_id
            else None
        )
        text = snapshot.text if snapshot and snapshot.text else ""

    return {
        "contract_title": contract.title if contract else "Contract",
        "requester_name": (requester.full_name or requester.email)
        if requester
        else "A colleague",
        "status": approval.status,
        "can_decide": approval.status == ApprovalStatus.PENDING and row.used_at is None,
        "due_at": approval.due_at,
        "document_text": text[:200_000],
        "document_truncated": len(text) > 200_000,
    }
