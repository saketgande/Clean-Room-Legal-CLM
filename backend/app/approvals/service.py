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
from app.core.database import new_uuid
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


# --- Approval subjects ----------------------------------------------------
# The ladder engine is subject-agnostic: it drives a *subject* through an
# ordered chain of ApprovalRequest rows. A subject exposes the attributes the
# routing rules + Delegation-of-Authority read (same names as Contract) and the
# lifecycle hooks that fire as the chain starts / rejects / completes. Contracts
# and intake requests each supply their own; the engine never names either.
#
# The intake subject lives in app/intake/approval_bridge.py and is reached by a
# lazy import (below) so this lower-level package keeps no static dep on intake.


class ContractSubject:
    """A contract driven through the approval ladder — preserves the original
    contract-only behaviour exactly (stage guards, fast-lane, stage transitions)."""

    kind = "contract"
    allow_fast_lane = True

    def __init__(self, contract: Contract, *, version_id: str | None = None):
        self.contract = contract
        self.version_id = version_id or contract.current_authoritative_version_id

    # attributes read by _matches (routing) + _grant_covers (authority)
    @property
    def id(self) -> str:
        return self.contract.id

    @property
    def org_id(self) -> str:
        return self.contract.org_id

    @property
    def title(self) -> str:
        return self.contract.title or "Untitled contract"

    @property
    def value_amount(self):
        return self.contract.value_amount

    @property
    def contract_type(self):
        return self.contract.contract_type

    @property
    def risk_band(self):
        return self.contract.risk_band

    @property
    def risk_level(self):
        return self.contract.risk_level

    @property
    def currency(self):
        return self.contract.currency

    @property
    def jurisdiction(self):
        return self.contract.jurisdiction

    def precheck(self, db: Session) -> None:
        # Approval applies to pre-execution contracts. Submitting one that's
        # already in signing / active / closed makes no sense — block it clearly.
        if self.contract.lifecycle_stage in {
            ContractLifecycleStage.SIGNATURE,
            ContractLifecycleStage.ACTIVE,
            ContractLifecycleStage.CLOSED,
        }:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Cannot submit a contract in '{self.contract.lifecycle_stage}' for approval",
            )

    def try_fast_lane(self, db: Session, *, user: User, request_id: str | None) -> bool:
        """Routine contracts route themselves: an eligible NDA skips the approval
        chain entirely and lands in Signature with a full audit trail. Returns
        True when fast-laned (the caller then creates no chain)."""
        reason = _fast_lane_reason(db, contract=self.contract)
        if not reason:
            return False
        from app.notifications.models import Notification

        transition_contract_stage(
            db,
            contract=self.contract,
            to_stage=ContractLifecycleStage.SIGNATURE,
            actor_user_id=user.id,
            reason=reason,
            override=True,
            override_authorized=True,
            request_id=request_id,
        )
        write_audit_log(
            db,
            action="approval.fast_laned",
            resource_type="contract",
            resource_id=self.contract.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            request_id=request_id,
            metadata={"reason": reason},
        )
        recipients = {user.id}
        if self.contract.owner_user_id:
            recipients.add(self.contract.owner_user_id)
        for uid in recipients:
            db.add(
                Notification(
                    org_id=user.org_id,
                    user_id=uid,
                    channel="in_app",
                    event_type="approval.fast_laned",
                    subject=f"Fast-laned to signature: {self.contract.title}",
                    body=(
                        f'"{self.contract.title}" qualified for the NDA fast-lane '
                        "(low risk, within value cap, no open high-severity issues) "
                        "and skipped approval — it is ready to send for signature."
                    ),
                    status="sent",
                )
            )
        db.commit()
        return True

    def guard_can_decide(self, db: Session) -> None:
        if self.contract.lifecycle_stage != ContractLifecycleStage.APPROVAL:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Approval can only be decided while the contract is in the approval stage",
            )

    def forced_rungs(self, db: Session) -> list[dict]:
        return []  # contracts have no Tier-0 gate rungs (Phase-1 behaviour unchanged)

    def on_submit(self, db: Session, *, actor_user_id: str | None, request_id: str | None) -> None:
        if self.contract.lifecycle_stage != ContractLifecycleStage.APPROVAL:
            transition_contract_stage(
                db,
                contract=self.contract,
                to_stage=ContractLifecycleStage.APPROVAL,
                actor_user_id=actor_user_id,
                reason="Submitted for approval",
                override=True,
                override_authorized=True,
                request_id=request_id,
            )

    def on_reject(
        self, db: Session, *, actor_user_id: str | None, comment: str | None, request_id: str | None
    ) -> None:
        transition_contract_stage(
            db,
            contract=self.contract,
            to_stage=ContractLifecycleStage.REVIEW,
            actor_user_id=actor_user_id,
            reason=comment,
            override=True,
            override_authorized=True,
            request_id=request_id,
        )

    def on_complete(
        self, db: Session, *, actor_user_id: str | None, request_id: str | None
    ) -> None:
        transition_contract_stage(
            db,
            contract=self.contract,
            to_stage=ContractLifecycleStage.SIGNATURE,
            actor_user_id=actor_user_id,
            reason="Approval chain completed",
            override=True,
            override_authorized=True,
            request_id=request_id,
        )


def _subject_clause(subject):
    """WHERE clause selecting the ApprovalRequest rows belonging to a subject."""
    if subject.kind == "contract":
        return ApprovalRequest.contract_id == subject.id
    return ApprovalRequest.intake_request_id == subject.id


def _subject_for(db: Session, approval: ApprovalRequest):
    """Reconstruct the subject a stored approval belongs to, so a decision can
    fire the right lifecycle transitions."""
    if approval.contract_id:
        contract = db.get(Contract, approval.contract_id)
        if contract is None or contract.org_id != approval.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
        return ContractSubject(contract, version_id=approval.contract_version_id)
    if approval.intake_request_id:
        from app.intake.approval_bridge import build_intake_subject

        return build_intake_subject(db, approval.intake_request_id, org_id=approval.org_id)
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Approval has no subject")


def _matches(rule: ApprovalRoutingRule, subject) -> bool:
    """Does this routing rule's criteria match the subject?

    Supported criteria keys (all optional; empty criteria = match everything):
      min_value / max_value        — numeric bounds on the subject's value
      contract_type(s)             — string or list, case-insensitive
      risk_band(s)                 — string or list vs risk_band/risk_level
      any other key                — exact (case-insensitive) match against the
                                     subject attribute of the same name
    """
    criteria = rule.criteria or {}
    if not criteria:
        return True
    for key, expected in criteria.items():
        if key == "min_value":
            if (subject.value_amount or 0) < float(expected):
                return False
        elif key == "max_value":
            if (subject.value_amount or 0) > float(expected):
                return False
        elif key in {"contract_type", "contract_types"}:
            allowed = expected if isinstance(expected, list) else [expected]
            ct = (subject.contract_type or "").strip().lower()
            if ct not in {str(a).strip().lower() for a in allowed}:
                return False
        elif key in {"risk_band", "risk_bands"}:
            allowed = expected if isinstance(expected, list) else [expected]
            band = (subject.risk_band or subject.risk_level or "").strip().lower()
            if band not in {str(a).strip().lower() for a in allowed}:
                return False
        else:
            actual = getattr(subject, key, None)
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


def resolve_chain(
    db: Session, *, subject, org_id: str, rule_id: str | None = None
) -> list[dict]:
    """Return an active rule's ordered approval chain as a list of step targets:
    ``{routing_rule_id, step_order, approver_user_id, approver_group_id,
    approver_role, mode}``.

    ``rule_id`` pins a specific rule (a workflow step that chose its route) — it
    is used verbatim, skipping criteria matching, as long as it is active in this
    org; otherwise we fall back to the best-matching active rule (lowest priority
    number, then most-specific criteria). Empty list means nothing applied (the
    caller falls back to the manually-specified approver)."""
    rules = db.scalars(
        select(ApprovalRoutingRule).where(
            ApprovalRoutingRule.org_id == org_id,
            ApprovalRoutingRule.is_active.is_(True),
        )
    ).all()
    rule = next((r for r in rules if r.id == rule_id), None) if rule_id else None
    if rule is None:
        matched = sorted(
            (r for r in rules if _matches(r, subject)),
            key=lambda r: (
                int(r.priority) if str(r.priority).isdigit() else 100,
                -len(r.criteria or {}),
            ),
        )
        rule = matched[0] if matched else None
    if rule is None:
        return []
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


def plan_chain(
    db: Session,
    *,
    subject,
    org_id: str,
    approver_user_id: str | None = None,
    approver_group_id: str | None = None,
    approver_role: str | None = None,
    routing_rule_id: str | None = None,
) -> list[dict]:
    """The ordered rung targets for a subject BEFORE persistence — the routed
    chain plus forced Tier-0 gate rungs (deduped, senior last), or the manual
    fallback approver when nothing matched. Shared by submit and the read-only
    ladder preview so both show the same rungs. ``routing_rule_id`` pins a
    specific route (a workflow Approval step's choice)."""
    chain = resolve_chain(db, subject=subject, org_id=org_id, rule_id=routing_rule_id)
    forced = subject.forced_rungs(db)
    if not chain and not forced:
        chain = [
            {
                "routing_rule_id": None,
                "step_order": 1,
                "approver_user_id": approver_user_id,
                "approver_group_id": approver_group_id,
                "approver_role": approver_role,
                "mode": "any",
            }
        ]
    if forced:
        existing = {
            (t.get("approver_group_id"), t.get("approver_role"), t.get("approver_user_id"))
            for t in chain
        }
        for rung in forced:
            key = (rung.get("approver_group_id"), rung.get("approver_role"),
                   rung.get("approver_user_id"))
            if key in existing:
                continue
            existing.add(key)
            chain.append(rung)
    for idx, target in enumerate(chain):
        target["step_order"] = idx + 1
    return chain


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
    db: Session, *, approval: ApprovalRequest, subject, requester: User | None
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
    safe_title = html.escape(subject.title or "Untitled")
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
                subject=f"Approval requested: {subject.title}",
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


async def submit_subject_for_approval(
    db: Session,
    *,
    user: User,
    subject,
    approver_user_id: str | None = None,
    approver_group_id: str | None = None,
    approver_role: str | None = None,
    routing_rule_id: str | None = None,
    request_id: str | None = None,
) -> list[ApprovalRequest]:
    """Materialise a subject's approval ladder: one ApprovalRequest per routing
    step, step 1 PENDING (emailed) and the rest WAITING, activated in order as
    each preceding step is approved. Subject-agnostic — contracts and intake
    requests both flow through here."""
    subject.precheck(db)

    # If a chain is already live for this subject (+version), return it unchanged
    # (idempotent re-submit) rather than stacking a second chain.
    existing = db.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == user.org_id,
            _subject_clause(subject),
            ApprovalRequest.contract_version_id == subject.version_id,
            ApprovalRequest.status.in_([ApprovalStatus.PENDING, ApprovalStatus.WAITING]),
        )
        .order_by(ApprovalRequest.step_order)
    ).all()
    if existing:
        return existing

    if subject.allow_fast_lane and subject.try_fast_lane(
        db, user=user, request_id=request_id
    ):
        return []

    # Routed rungs + forced Tier-0 gate rungs (intake only; [] for contracts),
    # or the manual fallback approver. Same planner the ladder preview uses.
    chain = plan_chain(
        db, subject=subject, org_id=user.org_id,
        approver_user_id=approver_user_id, approver_group_id=approver_group_id,
        approver_role=approver_role, routing_rule_id=routing_rule_id,
    )

    due_at = datetime.now(UTC) + timedelta(days=max(1, settings.approval_default_due_days))
    is_contract = subject.kind == "contract"
    # Stable identifier shared by every step this submission creates, so the
    # chain-progress view (approval_chain in routes.py) can group by an exact
    # ID instead of a "created within 10 seconds" heuristic that can conflate
    # two chains submitted close together.
    submission_batch_id = new_uuid()
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
            contract_id=subject.id if is_contract else None,
            intake_request_id=None if is_contract else subject.id,
            contract_version_id=subject.version_id,
            requested_by_user_id=user.id,
            approver_user_id=target["approver_user_id"],
            approver_role=target["approver_role"],
            approver_group_id=target["approver_group_id"],
            routing_rule_id=target["routing_rule_id"],
            step_order=target["step_order"],
            mode=target.get("mode", "any"),
            status=ApprovalStatus.PENDING if is_first else ApprovalStatus.WAITING,
            due_at=due_at,
            metadata_json={"submission_batch_id": submission_batch_id},
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(approval)
        db.flush()

        email_sent: bool | None = None
        if is_first:
            email_sent = await _activate_step(
                db, approval=approval, subject=subject, requester=user
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
                "subject_type": subject.kind,
                "contract_id": approval.contract_id,
                "intake_request_id": approval.intake_request_id,
                "step_order": approval.step_order,
                "status": approval.status,
                "approver_user_id": approval.approver_user_id,
                "approver_group_id": approval.approver_group_id,
                "approver_role": approval.approver_role,
                "email_sent": email_sent,
            },
        )
        requests.append(approval)

    subject.on_submit(db, actor_user_id=user.id, request_id=request_id)
    return requests


async def submit_contract_for_approval(
    db: Session,
    *,
    user: User,
    contract: Contract,
    contract_version_id: str | None,
    approver_user_id: str | None,
    approver_role: str | None,
    routing_rule_id: str | None = None,
    request_id: str | None = None,
) -> list[ApprovalRequest]:
    """Thin contract wrapper over the subject-agnostic ladder — the public API
    the contract routes / AI tools already call is unchanged."""
    subject = ContractSubject(contract, version_id=contract_version_id)
    return await submit_subject_for_approval(
        db,
        user=user,
        subject=subject,
        approver_user_id=approver_user_id,
        approver_role=approver_role,
        routing_rule_id=routing_rule_id,
        request_id=request_id,
    )


def _quorum_needed(db: Session, approval: ApprovalRequest) -> int:
    """How many distinct approvals a rung needs before it advances: 1 for an
    'any' rung / a named user / a role, or the full active group size for 'all'."""
    if (approval.mode or "any") != "all":
        return 1
    if approval.approver_group_id:
        group = db.get(ApproverGroup, approval.approver_group_id)
        members = [m for m in (group.members if group else []) if m.org_id == approval.org_id]
        return max(1, len(members))
    return 1


async def reassign_rung(
    db: Session,
    *,
    approval: ApprovalRequest,
    to_user: User,
    kind: str,
    actor: User,
    request_id: str | None = None,
) -> ApprovalRequest:
    """Hand a pending rung to another person — 'delegate' (sideways) or 'escalate'
    (up). Pins the rung to that named user, re-issues their token + email, and
    audits who moved it. Only a PENDING rung can be reassigned."""
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending approval can be reassigned")
    if to_user.org_id != approval.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target user not found")
    if to_user.id == approval.requested_by_user_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The requester can't approve their own request")

    prev = approval.approver_user_id
    approval.approver_user_id = to_user.id
    approval.approver_group_id = None
    approval.approver_role = None
    approval.mode = "any"  # a reassign pins the rung to one named approver
    approval.updated_by_user_id = actor.id
    approval.metadata_json = {
        **(approval.metadata_json or {}),
        "reassign": {"kind": kind, "from_user_id": prev, "by_user_id": actor.id},
    }
    db.flush()

    subject = _subject_for(db, approval)
    requester = db.get(User, approval.requested_by_user_id)
    email_sent = await _activate_step(db, approval=approval, subject=subject, requester=requester)
    write_audit_log(
        db, action=f"approval.{kind}", resource_type="approval_request",
        resource_id=approval.id, org_id=approval.org_id, actor_user_id=actor.id,
        request_id=request_id,
        after={"to_user_id": to_user.id, "from_user_id": prev, "email_sent": email_sent},
    )
    write_timeline_event(
        db, org_id=approval.org_id, resource_type=subject.kind, resource_id=subject.id,
        event_type=f"approval.{kind}",
        title=f"Approval {kind}d to {to_user.full_name or to_user.email} (step {approval.step_order})",
        actor_user_id=actor.id, request_id=request_id,
        details={"approval_request_id": approval.id},
    )
    return approval


async def _apply_decision(
    db: Session,
    *,
    approval: ApprovalRequest,
    subject,
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

    # One person can't decide the same rung twice — that would double-count a
    # quorum or flip-flop a decision. Token single-use covers the emailed path;
    # this covers a repeat in-app click.
    prior = db.scalars(
        select(ApprovalDecision).where(ApprovalDecision.approval_request_id == approval.id)
    ).all()
    if actor_user_id and any(d.approver_user_id == actor_user_id for d in prior):
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already decided this approval")

    subject.guard_can_decide(db)
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

    # The active chain = the other non-cancelled steps for this subject+version.
    # Only one chain is ever live at a time (submit is idempotent), so its
    # WAITING rows uniquely identify the steps still ahead.
    siblings = db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.org_id == approval.org_id,
            _subject_clause(subject),
            ApprovalRequest.contract_version_id == approval.contract_version_id,
            ApprovalRequest.id != approval.id,
            ApprovalRequest.status != ApprovalStatus.CANCELLED,
        )
    ).all()

    if decision == "reject":
        # Reject short-circuits the whole chain and sends the subject back.
        approval.status = ApprovalStatus.REJECTED
        for sibling in siblings:
            if sibling.status in (ApprovalStatus.PENDING, ApprovalStatus.WAITING):
                sibling.status = ApprovalStatus.CANCELLED
                sibling.updated_by_user_id = actor_user_id
        subject.on_reject(
            db, actor_user_id=actor_user_id, comment=comment, request_id=request_id
        )
    else:
        # Quorum: an "all" rung needs every group member; anything else needs one.
        needed = _quorum_needed(db, approval)
        approvals = sum(1 for d in prior if d.decision == "approve") + 1
        approval.metadata_json = {**(approval.metadata_json or {}), "approvals": approvals, "needed": needed}
        if approvals < needed:
            # Not enough sign-offs yet — record this one, keep the rung PENDING,
            # do NOT advance. The other members still have to approve.
            write_audit_log(
                db, action="approval.partial_approved", resource_type="approval_request",
                resource_id=approval.id, org_id=approval.org_id, actor_user_id=actor_user_id,
                request_id=request_id,
                after={"approvals": approvals, "needed": needed, "step_order": approval.step_order},
            )
            write_timeline_event(
                db, org_id=approval.org_id, resource_type=subject.kind, resource_id=subject.id,
                event_type="approval.partial",
                title=f"Approval signed {approvals} of {needed} (step {approval.step_order})",
                actor_user_id=actor_user_id, request_id=request_id,
                details={"approval_request_id": approval.id, "decided_by": actor_label},
            )
            return approval

        # Quorum met → this rung is approved; activate the next step or finish.
        approval.status = ApprovalStatus.APPROVED
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
                db, approval=next_step, subject=subject, requester=requester
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
            # Last step approved → the whole chain is done; the subject advances
            # (contract → signature; intake request → approved).
            subject.on_complete(
                db, actor_user_id=actor_user_id, request_id=request_id
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
        resource_type=subject.kind,
        resource_id=subject.id,
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
    subject = _subject_for(db, approval)
    return await _apply_decision(
        db,
        approval=approval,
        subject=subject,
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
    # Phase 4 ABAC gate — parity with the in-app decide route: approving via the
    # emailed token must still respect the approver's delegated authority
    # (value/type/jurisdiction/risk ceilings). Without this an approver over their
    # cap could bypass it by clicking the email link. Rejections are never gated.
    if decision == "approve" and approver is not None:
        from app.authority.service import enforce_authority
        from app.contracts.models import Contract

        gate_subject = None
        if approval.contract_id:
            gate_subject = db.get(Contract, approval.contract_id)
        elif approval.intake_request_id:
            from app.intake.approval_bridge import build_intake_subject

            gate_subject = build_intake_subject(db, approval.intake_request_id, org_id=approval.org_id)
        if gate_subject is not None:
            enforce_authority(
                db, user=approver, action="contract:approve", contract=gate_subject,
                resource_type="approval_request", resource_id=approval.id, request_id=request_id,
            )
    subject = _subject_for(db, approval)
    return await _apply_decision(
        db,
        approval=approval,
        subject=subject,
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

    requester = db.get(User, approval.requested_by_user_id)
    requester_name = (
        (requester.full_name or requester.email) if requester else "A colleague"
    )
    can_decide = approval.status == ApprovalStatus.PENDING and row.used_at is None

    if not approval.contract_id:
        # Intake-request approval — no contract document; show the request itself.
        from app.intake.models import IntakeRequest

        req = db.get(IntakeRequest, approval.intake_request_id)
        text = (req.description if req and req.description else "") or ""
        return {
            "contract_title": (f"{req.ref} · {req.type_label}" if req else "Request"),
            "requester_name": requester_name,
            "status": approval.status,
            "can_decide": can_decide,
            "due_at": approval.due_at,
            "document_text": text[:200_000],
            "document_truncated": len(text) > 200_000,
        }

    contract = db.get(Contract, approval.contract_id)
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
        "requester_name": requester_name,
        "status": approval.status,
        "can_decide": can_decide,
        "due_at": approval.due_at,
        "document_text": text[:200_000],
        "document_truncated": len(text) > 200_000,
    }
