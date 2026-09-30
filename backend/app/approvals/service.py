import html
import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.approvals.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalToken,
)
from app.auth.models import Role, User, user_role_table
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import new_uuid
from app.core.enums import ApprovalStatus, ContractLifecycleStage, UserStatus
from app.core.money import to_money
from app.core.rbac import has_permission
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



# --- Approval subjects ----------------------------------------------------
# The ladder engine is subject-agnostic: it drives a *subject* through an
# ordered chain of ApprovalRequest rows. A subject exposes the attributes
# Delegation-of-Authority reads (same names as Contract) and the
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

    # attributes read by _grant_covers (Delegation of Authority)
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
        from app.workflows.service import advance_flow_for_contract, workflow_holds_signature

        if workflow_holds_signature(db, contract_id=self.contract.id):
            # The workflow's own signature step moves the contract on, after the steps
            # between approval and signing (e.g. counterparty negotiation), so terms
            # changed after approval can't reach signing without a new approval.
            db.flush()  # let the workflow see this rung's approval
            advance_flow_for_contract(db, contract=self.contract, actor_user_id=actor_user_id)
            return
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
    if to_money(contract.value_amount) > to_money(settings.nda_fast_lane_max_value):
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


def plan_chain(
    *,
    approver_user_id: str | None = None,
    approver_team_id: str | None = None,
    approver_role: str | None = None,
    mode: str = "any",
) -> list[dict]:
    """The rung a submission creates: the approver the caller named (a workflow
    Approval step, or whoever submitted by hand). Nothing else adds approvers —
    no routing rules, no automatic gates. A target with no approver at all is
    still returned, so submit reports "no approver configured" instead of
    skipping the approval. ``mode`` "all" (every team member must approve)
    only means something for a team."""
    return [{
        "step_order": 1,
        "approver_user_id": approver_user_id,
        "approver_team_id": None if approver_user_id else approver_team_id,
        "approver_role": None if (approver_user_id or approver_team_id) else approver_role,
        "mode": mode if approver_team_id and not approver_user_id else "any",
    }]


def _step_recipients(db: Session, *, approval: ApprovalRequest) -> list[User]:
    """Users who should be emailed when ``approval`` becomes active: the named
    user, or every active member of the assigned team. Role-only steps have no
    direct recipients (those approvers act in-app)."""
    from app.intake.teams import member_users

    if approval.approver_user_id:
        user = db.get(User, approval.approver_user_id)
        return [user] if user and user.org_id == approval.org_id else []
    return member_users(db, team_id=approval.approver_team_id, org_id=approval.org_id)


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


def _rung_config_problem(db: Session, target: dict, org_id: str) -> str | None:
    """Why a planned rung could never be decided, or None. Checked at submit so a
    misconfigured approval step fails loudly instead of sitting in Approval with nobody
    able to act and nobody emailed."""
    if target["approver_user_id"]:
        approver = db.get(User, target["approver_user_id"])
        if approver is not None and approver.status != UserStatus.ACTIVE:
            return "The named approver's account isn't active. Choose another approver."
        return None
    if target["approver_team_id"]:
        from app.intake.models import IntakeTeam
        from app.intake.teams import member_users

        team = db.get(IntakeTeam, target["approver_team_id"])
        if team is not None and not member_users(db, team_id=team.id, org_id=org_id):
            return (
                f"The '{team.name}' team has no members. "
                "Add members in Admin → Teams, then submit again."
            )
        return None
    role = target.get("approver_role")
    if not role:
        return "This approval step has no approver configured."
    holders = db.scalar(
        select(func.count())
        .select_from(User)
        .join(user_role_table, user_role_table.c.user_id == User.id)
        .join(Role, Role.id == user_role_table.c.role_id)
        .where(Role.org_id == org_id, Role.name == role, User.status == UserStatus.ACTIVE)
    )
    if not holders:
        return (
            f"No one in this organization holds the '{role}' approver role. "
            "Assign that role to someone, or give this approval step a team."
        )
    return None


async def submit_subject_for_approval(
    db: Session,
    *,
    user: User,
    subject,
    approver_user_id: str | None = None,
    approver_team_id: str | None = None,
    approver_role: str | None = None,
    mode: str = "any",
    request_id: str | None = None,
) -> list[ApprovalRequest]:
    """Materialise a subject's approval ladder: one ApprovalRequest per rung, step 1 PENDING (emailed) and the rest WAITING, activated in order as
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

    # The named approver — the only rung.
    chain = plan_chain(
        approver_user_id=approver_user_id, approver_team_id=approver_team_id,
        approver_role=approver_role, mode=mode,
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
        # Validate referenced approver/team belong to this org.
        if target["approver_user_id"]:
            approver = db.get(User, target["approver_user_id"])
            if approver is None or approver.org_id != user.org_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Approver user must belong to this organization",
                )
        if target["approver_team_id"]:
            from app.intake.models import IntakeTeam

            team = db.get(IntakeTeam, target["approver_team_id"])
            if team is None or team.org_id != user.org_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Approver team must belong to this organization",
                )

        problem = _rung_config_problem(db, target, user.org_id)
        if problem:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)

        # Fix an "all members must approve" rung's requirement NOW: later team
        # edits must not change how many (or which) approvals it needs.
        quorum: dict = {}
        if target.get("mode") == "all" and target["approver_team_id"]:
            from app.intake.teams import member_users

            member_ids = sorted(m.id for m in member_users(db, team_id=target["approver_team_id"],
                                                           org_id=user.org_id))
            quorum = {"required_approver_ids": member_ids, "quorum_needed": max(1, len(member_ids))}

        is_first = target["step_order"] == 1
        approval = ApprovalRequest(
            org_id=user.org_id,
            contract_id=subject.id if is_contract else None,
            intake_request_id=None if is_contract else subject.id,
            contract_version_id=subject.version_id,
            requested_by_user_id=user.id,
            approver_user_id=target["approver_user_id"],
            approver_role=target["approver_role"],
            approver_team_id=target["approver_team_id"],
            step_order=target["step_order"],
            mode=target.get("mode", "any"),
            status=ApprovalStatus.PENDING if is_first else ApprovalStatus.WAITING,
            due_at=due_at,
            metadata_json={"submission_batch_id": submission_batch_id, **quorum},
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
                "approver_team_id": approval.approver_team_id,
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
    request_id: str | None = None,
    approver_team_id: str | None = None,
    mode: str = "any",
) -> list[ApprovalRequest]:
    """Thin contract wrapper over the subject-agnostic ladder — the public API
    the contract routes / AI tools already call is unchanged."""
    subject = ContractSubject(contract, version_id=contract_version_id)
    return await submit_subject_for_approval(
        db,
        user=user,
        subject=subject,
        approver_user_id=approver_user_id,
        approver_team_id=approver_team_id,
        mode=mode,
        approver_role=approver_role,
        request_id=request_id,
    )


def _quorum_needed(db: Session, approval: ApprovalRequest) -> int:
    """How many distinct approvals a rung needs before it advances: 1 for an
    'any' rung / a named user / a role, or the full active team size for 'all'."""
    if (approval.mode or "any") != "all":
        return 1
    snapshot = (approval.metadata_json or {}).get("quorum_needed")
    if isinstance(snapshot, int):
        return max(1, snapshot)
    # Rungs created before the snapshot existed: fall back to current membership.
    if approval.approver_team_id:
        from app.intake.teams import member_users

        return max(1, len(member_users(db, team_id=approval.approver_team_id, org_id=approval.org_id)))
    return 1


def _approvals_toward_quorum(approval: ApprovalRequest, prior, actor_user_id: str) -> int:
    """Distinct approvers counted toward the rung. When the rung snapshotted its
    required approvers, only their approvals count."""
    approvers = {d.approver_user_id for d in prior if d.decision == "approve"} | {actor_user_id}
    required = set((approval.metadata_json or {}).get("required_approver_ids") or [])
    return len(approvers & required) if required else len(approvers)


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
    approval.approver_team_id = None
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
    actor_user_id: str,
    actor_label: str,
    request_id: str | None = None,
) -> ApprovalRequest:
    if decision == "reject" and not comment:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Rejection requires a comment")
    # Fail closed: the duplicate-decision and authority checks key off the
    # approver's identity, so "couldn't identify you" must never mean "exempt".
    if not actor_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Approver could not be identified")
    # Lock the rung before reading its state: two approvers deciding at once must
    # see each other's decision, or a 2-of-2 rung stays pending with both recorded
    # and the next rung gets activated (and emailed) twice.
    db.refresh(approval, with_for_update=True)
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval request is already decided")

    # One person can't decide the same rung twice — that would double-count a
    # quorum or flip-flop a decision. Token single-use covers the emailed path;
    # this covers a repeat in-app click.
    prior = db.scalars(
        select(ApprovalDecision).where(ApprovalDecision.approval_request_id == approval.id)
    ).all()
    if any(d.approver_user_id == actor_user_id for d in prior):
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
        # Quorum: an "all" rung needs every team member; anything else needs one.
        needed = _quorum_needed(db, approval)
        approvals = _approvals_toward_quorum(approval, prior, actor_user_id)
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


def _user_role_names(user) -> set[str]:
    return {role.name for role in getattr(user, "roles", [])}


def _user_eligible_to_decide(db: Session, *, approval: ApprovalRequest, user) -> bool:
    """Who is *allowed* to decide this step (identity/role/team), ignoring stage."""
    if has_permission(user.permission_values, "approval:admin"):
        return True
    if approval.requested_by_user_id == user.id:
        return False
    if approval.approver_user_id and approval.approver_user_id == user.id:
        return True
    if approval.approver_role and approval.approver_role in _user_role_names(user):
        return True
    if approval.approver_team_id:
        from app.intake.teams import member_users

        if any(m.id == user.id for m in member_users(db, team_id=approval.approver_team_id,
                                                     org_id=user.org_id)):
            return True
    return False


def _enforce_approve_authority(
    db: Session, *, approval: ApprovalRequest, user: User, request_id: str | None
) -> None:
    """Phase 4 ABAC gate: approving commits the company, so the decider's delegated
    authority must cover the subject's value/type/jurisdiction/risk. Rejections are
    never gated. Dormant until the org has a policy for the action."""
    from app.authority.service import enforce_authority

    # DoA reads value/type/jurisdiction/risk off the subject — a contract or
    # (duck-typed identically) an intake-request approval subject.
    gate_subject = None
    if approval.contract_id:
        gate_subject = db.get(Contract, approval.contract_id)
    elif approval.intake_request_id:
        from app.intake.approval_bridge import build_intake_subject

        gate_subject = build_intake_subject(db, approval.intake_request_id, org_id=approval.org_id)
    if gate_subject is not None:
        enforce_authority(
            db, user=user, action="contract:approve", contract=gate_subject,
            resource_type="approval_request", resource_id=approval.id, request_id=request_id,
        )


async def decide_in_app(
    db: Session,
    *,
    user: User,
    approval: ApprovalRequest,
    decision: str,
    comment: str | None,
    request_id: str | None = None,
) -> ApprovalRequest:
    # Every in-app way of deciding (web route, assistant tool) comes through here,
    # so these checks can't be skipped by calling from somewhere else.
    if not _user_eligible_to_decide(db, approval=approval, user=user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not assigned to decide this approval")
    if decision == "approve":
        _enforce_approve_authority(db, approval=approval, user=user, request_id=request_id)
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
            User.status == UserStatus.ACTIVE,
        )
    )
    if approver is None:
        # Tokens are only issued to directory users; one that no longer resolves
        # (user removed or email changed) can't be deduplicated or held to an
        # authority ceiling, so it can't decide anything.
        raise _reject("approver_not_in_directory")
    if approver.id == approval.requested_by_user_id:
        raise _reject("requester_cannot_approve_own_request")
    # Parity with the in-app path: approving via the emailed token must still
    # respect the approver's delegated authority, or an approver over their cap
    # could bypass it by clicking the email link.
    if decision == "approve":
        _enforce_approve_authority(db, approval=approval, user=approver, request_id=request_id)
    subject = _subject_for(db, approval)
    return await _apply_decision(
        db,
        approval=approval,
        subject=subject,
        decision=decision,
        comment=comment,
        actor_user_id=approver.id,
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
