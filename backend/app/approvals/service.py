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


# Contract attributes a WHEN→THEN condition may test. Fail-closed: a condition
# naming a field outside this allow-list never matches, so a typo can't silently
# widen a rule. ``risk_band`` transparently falls back to ``risk_level``.
_CONDITION_FIELDS = frozenset(
    {
        "value_amount",
        "contract_type",
        "risk_band",
        "risk_level",
        "risk_score",
        "counterparty_name",
        "jurisdiction",
        "currency",
        "title",
    }
)
_NUMERIC_OPS = {"gte", "lte", "gt", "lt"}


def _contract_field(contract: Contract, field: str):
    if field == "risk_band":
        return getattr(contract, "risk_band", None) or getattr(contract, "risk_level", None)
    return getattr(contract, field, None)


def _eval_condition(cond: dict, contract: Contract) -> bool:
    """Evaluate one ``{field, op, value}`` condition against the contract.

    Operators: eq / ne / in / gte / lte / gt / lt / contains / exists. String
    comparisons are case-insensitive; numeric comparisons coerce both sides to
    float and fail-closed on non-numeric input."""
    field = (cond.get("field") or "").strip()
    op = (cond.get("op") or "eq").strip().lower()
    expected = cond.get("value")
    if field not in _CONDITION_FIELDS:
        return False
    actual = _contract_field(contract, field)
    if op == "exists":
        return actual is not None and str(actual).strip() != ""
    if actual is None:
        return False
    if op in _NUMERIC_OPS:
        try:
            a, b = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        return {"gte": a >= b, "lte": a <= b, "gt": a > b, "lt": a < b}[op]
    a = str(actual).strip().lower()
    if op == "in":
        vals = expected if isinstance(expected, list) else [expected]
        return a in {str(v).strip().lower() for v in vals}
    if op == "contains":
        return str(expected).strip().lower() in a
    if op == "ne":
        return a != str(expected).strip().lower()
    return a == str(expected).strip().lower()


def _matches(rule: ApprovalRoutingRule, contract: Contract) -> bool:
    """Does this routing rule's criteria match the contract?

    Supported criteria keys (all optional; empty criteria = match everything):
      conditions                   — list of ``{field, op, value}`` WHEN→THEN
                                     conditions, ALL of which must hold (see
                                     ``_eval_condition``)
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
        if key == "conditions":
            conds = expected if isinstance(expected, list) else []
            if not all(_eval_condition(c, contract) for c in conds):
                return False
        elif key == "min_value":
            try:
                if (contract.value_amount or 0) < float(expected):
                    return False
            except (TypeError, ValueError):
                return False
        elif key == "max_value":
            try:
                if (contract.value_amount or 0) > float(expected):
                    return False
            except (TypeError, ValueError):
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


def _rule_specificity(rule: ApprovalRoutingRule) -> int:
    """How many independent predicates a rule declares — used as the tie-break so
    a more-specific rule fires before a catch-all at the same priority."""
    cr = rule.criteria or {}
    n = 0
    for key, val in cr.items():
        if key == "conditions":
            n += len(val) if isinstance(val, list) else 0
        else:
            n += 1
    return n


def _rule_sort_key(rule: ApprovalRoutingRule):
    return (
        int(rule.priority) if str(rule.priority).isdigit() else 100,
        -_rule_specificity(rule),
    )


def _target_key(target: dict) -> str | None:
    """Stable identity of a step's approver target, for de-duplication when
    composing chains across rules. Returns None for an empty target."""
    if target.get("approver_group_id"):
        return f"g:{target['approver_group_id']}"
    if target.get("approver_user_id"):
        return f"u:{target['approver_user_id']}"
    if target.get("approver_role"):
        return f"r:{str(target['approver_role']).strip().lower()}"
    return None


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


def _matched_rules(db: Session, *, contract: Contract, org_id: str) -> list[ApprovalRoutingRule]:
    """Active rules whose criteria match the contract, ordered by priority then
    specificity (the order their steps enter a composed chain)."""
    rules = db.scalars(
        select(ApprovalRoutingRule).where(
            ApprovalRoutingRule.org_id == org_id,
            ApprovalRoutingRule.is_active.is_(True),
            ApprovalRoutingRule.deleted_at.is_(None),
        )
    ).all()
    return sorted((r for r in rules if _matches(r, contract)), key=_rule_sort_key)


def _condition_met(condition, contract: Contract) -> bool:
    """A step's ``condition`` (list of {field, op, value}) all-match, or no
    condition at all → the step is included in the chain."""
    if not condition:
        return True
    conds = condition if isinstance(condition, list) else []
    return all(_eval_condition(c, contract) for c in conds)


def _rule_targets(rule: ApprovalRoutingRule) -> list[dict]:
    """The approver targets a single rule contributes, ordered by (stage, step).
    Steps sharing a stage are parallel. A rule with no steps falls back to its
    legacy single-approver columns as one sequential step."""
    steps = sorted(rule.steps, key=lambda s: (s.stage or s.step_order, s.step_order))
    if steps:
        return [
            {
                "routing_rule_id": rule.id,
                "approver_user_id": s.approver_user_id,
                "approver_group_id": s.approver_group_id,
                "approver_role": s.approver_role,
                "mode": s.mode or "any",
                "stage": s.stage or s.step_order,
                "condition": s.condition,
            }
            for s in steps
        ]
    return [
        {
            "routing_rule_id": rule.id,
            "approver_user_id": rule.approver_user_id,
            "approver_group_id": None,
            "approver_role": rule.approver_role,
            "mode": "any",
            "stage": 1,
            "condition": None,
        }
    ]


def resolve_chain(db: Session, *, contract: Contract, org_id: str) -> list[dict]:
    """Resolve the approval chain for a contract as an ordered list of step
    targets: ``{routing_rule_id, step_order, approver_user_id, approver_group_id,
    approver_role, mode}``. Empty list means no rule matched (caller falls back
    to the manually-specified approver).

    Composable (default, ``settings.routing_compose_matched_rules``): the steps
    of every matching rule are merged in (priority, step) order and de-duplicated
    by approver target — so no reviewer a matching rule asked for is ever dropped.
    A duplicate target upgrades to ``mode="all"`` if any contributor demands it.

    Legacy (flag off): only the single best-matching rule's steps are used."""
    matched = _matched_rules(db, contract=contract, org_id=org_id)
    if not matched:
        return []
    if not settings.routing_compose_matched_rules:
        matched = matched[:1]

    merged: list[dict] = []
    seen: dict[str, dict] = {}
    # Map each contributing (rule, source-stage) to a global stage so parallel
    # steps stay parallel while rules sequence one after another.
    stage_map: dict[tuple, int] = {}
    next_stage = 0
    for rule in matched:
        for target in _rule_targets(rule):
            key = _target_key(target)
            if key is None:
                continue
            if key in seen:
                if target["mode"] == "all":
                    seen[key]["mode"] = "all"  # stricter wins
                continue
            source = (target["routing_rule_id"], target["stage"])
            if source not in stage_map:
                next_stage += 1
                stage_map[source] = next_stage
            target["stage"] = stage_map[source]
            seen[key] = target
            merged.append(target)

    for idx, target in enumerate(merged):
        target["step_order"] = idx + 1
    return merged


def preview_routing(db: Session, *, contract: Contract, org_id: str) -> dict:
    """Read-only dry-run: what would happen if this contract were submitted for
    approval right now — without creating a single row. Powers the "Preview
    chain" affordance so admins can trust routing before it fires."""
    fast_lane = _fast_lane_reason(db, contract=contract)
    matched = _matched_rules(db, contract=contract, org_id=org_id)
    chain = [] if fast_lane else resolve_chain(db, contract=contract, org_id=org_id)
    # Flag conditional steps that this contract wouldn't actually trigger, so the
    # preview can show them as skipped.
    for t in chain:
        t["skipped"] = not _condition_met(t.get("condition"), contract)
    used_rule_ids = {t.get("routing_rule_id") for t in chain if not t["skipped"]}
    return {
        "fast_lane_reason": fast_lane,
        "compose": settings.routing_compose_matched_rules,
        "matched_rules": [
            {
                "id": r.id,
                "name": r.name,
                "priority": r.priority,
                # A matched rule is "shadowed" when composition deduped away every
                # step it would have contributed (all its targets already present).
                "used": r.id in used_rule_ids,
            }
            for r in matched
        ],
        "chain": chain,
    }


def preview_criteria(*, criteria: dict, sample: dict) -> bool:
    """Does a draft rule's ``criteria`` match a hypothetical contract described by
    ``sample``? No DB, no rows — drives the live match badge in the rule builder."""
    from types import SimpleNamespace

    contract = SimpleNamespace(
        value_amount=sample.get("value_amount"),
        contract_type=sample.get("contract_type"),
        risk_band=sample.get("risk_band"),
        risk_level=sample.get("risk_level") or sample.get("risk_band"),
        risk_score=sample.get("risk_score"),
        counterparty_name=sample.get("counterparty_name"),
        jurisdiction=sample.get("jurisdiction"),
        currency=sample.get("currency"),
        title=sample.get("title"),
    )
    rule = SimpleNamespace(criteria=criteria or {})
    return _matches(rule, contract)  # type: ignore[arg-type]


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
                "stage": 1,
                "condition": None,
                "approver_user_id": approver_user_id,
                "approver_group_id": None,
                "approver_role": approver_role,
                "mode": "any",
            }
        ]

    # Evaluate each step's condition. A step whose condition fails is recorded
    # SKIPPED (never activated). The first stage that still has a live step
    # starts PENDING; the rest WAITING.
    for target in chain:
        target["_skipped"] = not _condition_met(target.get("condition"), contract)
    active_targets = [t for t in chain if not t["_skipped"]]
    active_stage = min((t["stage"] for t in active_targets), default=None)

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

        if target["_skipped"]:
            step_status = ApprovalStatus.SKIPPED
        elif target["stage"] == active_stage:
            step_status = ApprovalStatus.PENDING
        else:
            step_status = ApprovalStatus.WAITING
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
            stage=target["stage"],
            status=step_status,
            due_at=due_at,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(approval)
        db.flush()

        # Every step of the first live stage is activated together (parallel).
        email_sent: bool | None = None
        if step_status == ApprovalStatus.PENDING:
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
                "stage": approval.stage,
                "status": approval.status,
                "approver_user_id": approval.approver_user_id,
                "approver_group_id": approval.approver_group_id,
                "approver_role": approval.approver_role,
                "email_sent": email_sent,
            },
        )
        requests.append(approval)

    if not active_targets:
        # Pathological config — every step was conditional-skipped. There is
        # nothing to approve, so the contract advances straight to signature.
        transition_contract_stage(
            db,
            contract=contract,
            to_stage=ContractLifecycleStage.SIGNATURE,
            actor_user_id=user.id,
            reason="All approval steps were conditional-skipped",
            override=True,
            override_authorized=True,
            request_id=request_id,
        )
    elif contract.lifecycle_stage != ContractLifecycleStage.APPROVAL:
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
        # Reject short-circuits the whole chain (every stage) and sends it back.
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
        # Approve. A parallel stage only advances once every live step in it is
        # approved, so check the current stage before moving on.
        this_stage = approval.stage if approval.stage is not None else approval.step_order
        stage_peers = [
            s
            for s in siblings
            if (s.stage if s.stage is not None else s.step_order) == this_stage
            and s.status not in (ApprovalStatus.SKIPPED, ApprovalStatus.CANCELLED)
        ]
        stage_complete = all(s.status == ApprovalStatus.APPROVED for s in stage_peers)

        if not stage_complete:
            # Other parallel approvers in this stage haven't decided yet — wait.
            pass
        else:
            waiting_ahead = [
                s
                for s in siblings
                if s.status == ApprovalStatus.WAITING
                and (s.stage if s.stage is not None else s.step_order) > this_stage
            ]
            if waiting_ahead:
                next_stage = min(
                    (s.stage if s.stage is not None else s.step_order) for s in waiting_ahead
                )
                for next_step in waiting_ahead:
                    if (next_step.stage if next_step.stage is not None else next_step.step_order) != next_stage:
                        continue
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
                        after={
                            "step_order": next_step.step_order,
                            "stage": next_step.stage,
                            "email_sent": email_sent,
                        },
                    )
            else:
                # Final stage cleared → the whole chain is done, so the contract
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
