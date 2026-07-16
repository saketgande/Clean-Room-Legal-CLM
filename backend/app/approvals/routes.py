from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limit import limiter

from app.approvals.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRoutingRule,
    ApprovalRoutingStep,
    ApproverGroup,
)
from app.approvals.service import (
    decide_in_app,
    get_review_context_for_token,
    preview_criteria,
    preview_routing,
    redeem_token_decision,
    submit_contract_for_approval,
)
from app.auth.models import User
from app.contracts.models import Contract
from app.contracts.access import user_can_access_contract
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log
from app.core.deps import get_db, require_permission
from app.core.enums import UserStatus
from app.core.models import AuditLog
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


class ApprovalReviewResponse(BaseModel):
    contract_title: str
    requester_name: str
    status: str
    can_decide: bool
    due_at: datetime | None
    document_text: str
    document_truncated: bool


class RoutingStepPayload(BaseModel):
    """One step of a routing chain. Supply exactly one approver target — a group
    (preferred), a specific user, or a role."""

    approver_group_id: str | None = None
    approver_user_id: str | None = None
    approver_role: str | None = None
    mode: str = Field(default="any", pattern="^(any|all)$")


class RoutingRulePayload(BaseModel):
    name: str
    priority: str = "100"
    criteria: dict = Field(default_factory=dict)
    is_active: bool = True
    # Ordered chain. When provided it is authoritative; the legacy single-approver
    # fields below remain for backward compatibility / one-off rules.
    steps: list[RoutingStepPayload] = Field(default_factory=list)
    approver_role: str | None = None
    approver_user_id: str | None = None


class GroupPayload(BaseModel):
    name: str
    description: str | None = None
    is_active: bool = True


class GroupUpdatePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class GroupMembersPayload(BaseModel):
    user_ids: list[str] = Field(default_factory=list)


# --- Serializers ----------------------------------------------------------
def _user_brief(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "roles": [role.name for role in user.roles],
    }


def _serialize_group(group: ApproverGroup) -> dict:
    return {
        "id": group.id,
        "org_id": group.org_id,
        "name": group.name,
        "description": group.description,
        "is_active": group.is_active,
        "members": [_user_brief(m) for m in group.members],
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }


def _serialize_step(
    step: ApprovalRoutingStep,
    *,
    group_names: dict[str, str],
    user_names: dict[str, str],
) -> dict:
    return {
        "id": step.id,
        "step_order": step.step_order,
        "approver_group_id": step.approver_group_id,
        "approver_group_name": group_names.get(step.approver_group_id or ""),
        "approver_user_id": step.approver_user_id,
        "approver_user_name": user_names.get(step.approver_user_id or ""),
        "approver_role": step.approver_role,
        "mode": step.mode,
    }


def _serialize_rule(
    rule: ApprovalRoutingRule,
    *,
    group_names: dict[str, str],
    user_names: dict[str, str],
) -> dict:
    steps = sorted(rule.steps, key=lambda s: s.step_order)
    return {
        "id": rule.id,
        "org_id": rule.org_id,
        "name": rule.name,
        "priority": rule.priority,
        "criteria": rule.criteria,
        "is_active": rule.is_active,
        "approver_role": rule.approver_role,
        "approver_user_id": rule.approver_user_id,
        "steps": [
            _serialize_step(s, group_names=group_names, user_names=user_names) for s in steps
        ],
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _org_name_maps(db: Session, *, org_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """Build {id: name} maps for the org's groups and users so step targets can be
    labelled without an N+1 lookup per step."""
    group_names = {
        g.id: g.name
        for g in db.scalars(select(ApproverGroup).where(ApproverGroup.org_id == org_id)).all()
    }
    user_names = {
        u.id: (u.full_name or u.email)
        for u in db.scalars(select(User).where(User.org_id == org_id)).all()
    }
    return group_names, user_names


# --- Approval requests ----------------------------------------------------
@router.get("")
def list_approvals(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:read")),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    rows = db.scalars(
        select(ApprovalRequest)
        .where(ApprovalRequest.org_id == current_user.org_id)
        .order_by(ApprovalRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    # Batch-load the contracts referenced by this page in a single IN query so the
    # per-row visibility check below doesn't fire one db.get() per approval (N+1).
    contracts = _load_contracts_for_approvals(db, approvals=rows, user=current_user)
    return [
        _serialize_approval(
            row, can_decide=_can_decide_approval(db, approval=row, user=current_user)
        )
        for row in rows
        if _can_view_approval(db, approval=row, user=current_user, contracts=contracts)
    ]


def _serialize_approval(req: ApprovalRequest, *, can_decide: bool) -> dict:
    """Approval row + a server-computed can_decide (so the UI shows the
    Approve/Reject buttons for group members, not just role/user matches)."""
    return {
        "id": req.id,
        "org_id": req.org_id,
        "contract_id": req.contract_id,
        "contract_version_id": req.contract_version_id,
        "status": req.status,
        "requested_by_user_id": req.requested_by_user_id,
        "approver_user_id": req.approver_user_id,
        "approver_role": req.approver_role,
        "approver_group_id": req.approver_group_id,
        "routing_rule_id": req.routing_rule_id,
        "step_order": req.step_order,
        "due_at": req.due_at,
        "overdue": bool(
            req.status == "pending"
            and req.due_at is not None
            and req.due_at < datetime.now(UTC)
        ),
        "metadata_json": req.metadata_json,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "can_decide": can_decide,
    }


@router.get("/contracts/{contract_id}/chain")
def approval_chain(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Ordered progress of the contract's LATEST approval submission — which
    step is decided, which is active (and overdue), which is still waiting —
    so a submitter can see exactly where the chain is stuck."""
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    anchor = db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == current_user.org_id,
            ApprovalRequest.contract_id == contract_id,
            ApprovalRequest.step_order == 1,
        )
        .order_by(ApprovalRequest.created_at.desc())
    )
    if anchor is None:
        return {"steps": []}
    # All steps created by that same submission (they're inserted together).
    requests = db.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == current_user.org_id,
            ApprovalRequest.contract_id == contract_id,
            ApprovalRequest.created_at >= anchor.created_at - timedelta(seconds=10),
        )
        .order_by(ApprovalRequest.step_order.asc())
    ).all()

    group_ids = {r.approver_group_id for r in requests if r.approver_group_id}
    user_ids = {r.approver_user_id for r in requests if r.approver_user_id}
    groups = {
        g.id: g.name
        for g in db.scalars(
            select(ApproverGroup).where(ApproverGroup.id.in_(group_ids))
        ).all()
    } if group_ids else {}
    users = {
        u.id: u.full_name
        for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}

    now = datetime.now(UTC)
    steps = []
    for req in requests:
        decision = db.scalar(
            select(ApprovalDecision)
            .where(ApprovalDecision.approval_request_id == req.id)
            .order_by(ApprovalDecision.decided_at.desc())
        )
        decided_by = (
            users.get(decision.approver_user_id)
            if decision and decision.approver_user_id
            else None
        )
        if decision and decision.approver_user_id and decided_by is None:
            decider = db.get(User, decision.approver_user_id)
            decided_by = decider.full_name if decider else None
        steps.append(
            {
                "approval_request_id": req.id,
                "step_order": req.step_order,
                "status": req.status,
                "approver_label": (
                    groups.get(req.approver_group_id)
                    or users.get(req.approver_user_id)
                    or req.approver_role
                    or "Approver"
                ),
                "due_at": req.due_at,
                "overdue": bool(
                    req.status == "pending"
                    and req.due_at is not None
                    and req.due_at < now
                ),
                "decided_at": decision.decided_at if decision else None,
                "decided_by": decided_by,
                "comment": decision.comment if decision else None,
            }
        )
    return {"steps": steps}


# --- Approver groups ------------------------------------------------------
@router.get("/groups")
def list_groups(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    groups = db.scalars(
        select(ApproverGroup)
        .where(ApproverGroup.org_id == current_user.org_id)
        .order_by(ApproverGroup.name)
    ).all()
    return [_serialize_group(g) for g in groups]


@router.post("/groups", status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupPayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Group name is required")
    existing = db.scalar(
        select(ApproverGroup).where(
            ApproverGroup.org_id == current_user.org_id, ApproverGroup.name == name
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A group with that name already exists")
    group = ApproverGroup(
        org_id=current_user.org_id,
        name=name,
        description=payload.description,
        is_active=payload.is_active,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return _serialize_group(group)


@router.patch("/groups/{group_id}")
def update_group(
    group_id: str,
    payload: GroupUpdatePayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    group = db.get(ApproverGroup, group_id)
    if group is None or group.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    if payload.name is not None:
        group.name = payload.name.strip()
    if payload.description is not None:
        group.description = payload.description
    if payload.is_active is not None:
        group.is_active = payload.is_active
    group.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(group)
    return _serialize_group(group)


@router.put("/groups/{group_id}/members")
def set_group_members(
    group_id: str,
    payload: GroupMembersPayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    group = db.get(ApproverGroup, group_id)
    if group is None or group.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    members: list[User] = []
    for user_id in dict.fromkeys(payload.user_ids):  # de-dupe, preserve order
        member = db.get(User, user_id)
        if member is None or member.org_id != current_user.org_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Every member must belong to this organization",
            )
        members.append(member)
    group.members = members
    group.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(group)
    return _serialize_group(group)


@router.get("/eligible-approvers")
def list_eligible_approvers(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    """Active users in the org, for populating approver/member dropdowns."""
    users = db.scalars(
        select(User)
        .where(User.org_id == current_user.org_id, User.status == UserStatus.ACTIVE)
        .order_by(User.full_name)
    ).all()
    return [_user_brief(u) for u in users]


# --- Routing rules --------------------------------------------------------
@router.get("/routing-rules")
def list_routing_rules(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    rules = db.scalars(
        select(ApprovalRoutingRule).where(
            ApprovalRoutingRule.org_id == current_user.org_id,
            ApprovalRoutingRule.deleted_at.is_(None),
        )
    ).all()
    group_names, user_names = _org_name_maps(db, org_id=current_user.org_id)
    return [
        _serialize_rule(r, group_names=group_names, user_names=user_names) for r in rules
    ]


def _validate_org_user(db: Session, *, org_id: str, user_id: str | None) -> None:
    if not user_id:
        return
    approver = db.get(User, user_id)
    if approver is None or approver.org_id != org_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Approver user must belong to this organization",
        )


def _validate_org_group(db: Session, *, org_id: str, group_id: str | None) -> None:
    if not group_id:
        return
    group = db.get(ApproverGroup, group_id)
    if group is None or group.org_id != org_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Approver group must belong to this organization",
        )


def _rebuild_steps(
    db: Session, *, rule: ApprovalRoutingRule, steps, org_id: str, actor_id: str
) -> None:
    """Validate and (re)create a rule's ordered steps. Used by create + update."""
    for idx, step in enumerate(steps):
        if not (step.approver_group_id or step.approver_user_id or step.approver_role):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Step {idx + 1} needs an approver group, user, or role",
            )
        _validate_org_group(db, org_id=org_id, group_id=step.approver_group_id)
        _validate_org_user(db, org_id=org_id, user_id=step.approver_user_id)
        db.add(
            ApprovalRoutingStep(
                org_id=org_id,
                rule_id=rule.id,
                step_order=idx + 1,
                approver_group_id=step.approver_group_id,
                approver_user_id=step.approver_user_id,
                approver_role=step.approver_role,
                mode=step.mode,
                created_by_user_id=actor_id,
                updated_by_user_id=actor_id,
            )
        )


def _rule_snapshot(rule: ApprovalRoutingRule) -> dict:
    """Before/after payload for the routing audit trail."""
    return {
        "name": rule.name,
        "priority": rule.priority,
        "criteria": rule.criteria,
        "is_active": rule.is_active,
        "steps": [
            {
                "step_order": s.step_order,
                "approver_group_id": s.approver_group_id,
                "approver_user_id": s.approver_user_id,
                "approver_role": s.approver_role,
                "mode": s.mode,
            }
            for s in sorted(rule.steps, key=lambda s: s.step_order)
        ],
    }


def _get_owned_rule(db: Session, *, rule_id: str, org_id: str) -> ApprovalRoutingRule:
    rule = db.get(ApprovalRoutingRule, rule_id)
    if rule is None or rule.org_id != org_id or rule.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routing rule not found")
    return rule


class RoutingReorderPayload(BaseModel):
    ordered_ids: list[str] = Field(default_factory=list)


@router.post("/routing-rules", status_code=status.HTTP_201_CREATED)
def create_routing_rule(
    payload: RoutingRulePayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    # Validate the legacy single-approver fields (used only when no steps given).
    _validate_org_user(db, org_id=current_user.org_id, user_id=payload.approver_user_id)

    rule = ApprovalRoutingRule(
        org_id=current_user.org_id,
        name=payload.name,
        priority=payload.priority,
        criteria=payload.criteria,
        approver_role=payload.approver_role if not payload.steps else None,
        approver_user_id=payload.approver_user_id if not payload.steps else None,
        is_active=payload.is_active,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(rule)
    db.flush()
    _rebuild_steps(
        db, rule=rule, steps=payload.steps, org_id=current_user.org_id, actor_id=current_user.id
    )
    db.flush()
    db.refresh(rule)
    write_audit_log(
        db,
        action="routing_rule.created",
        resource_type="approval_routing_rule",
        resource_id=rule.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        after=_rule_snapshot(rule),
    )
    db.commit()
    db.refresh(rule)
    group_names, user_names = _org_name_maps(db, org_id=current_user.org_id)
    return _serialize_rule(rule, group_names=group_names, user_names=user_names)


@router.patch("/routing-rules/{rule_id}")
def update_routing_rule(
    rule_id: str,
    payload: RoutingRulePayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    rule = _get_owned_rule(db, rule_id=rule_id, org_id=current_user.org_id)
    before = _rule_snapshot(rule)
    _validate_org_user(db, org_id=current_user.org_id, user_id=payload.approver_user_id)

    rule.name = payload.name
    rule.priority = payload.priority
    rule.criteria = payload.criteria
    rule.is_active = payload.is_active
    rule.approver_role = payload.approver_role if not payload.steps else None
    rule.approver_user_id = payload.approver_user_id if not payload.steps else None
    rule.updated_by_user_id = current_user.id
    # Full-replace the chain: drop the old steps, rebuild from the payload.
    for old in list(rule.steps):
        db.delete(old)
    db.flush()
    _rebuild_steps(
        db, rule=rule, steps=payload.steps, org_id=current_user.org_id, actor_id=current_user.id
    )
    db.flush()
    db.refresh(rule)
    write_audit_log(
        db,
        action="routing_rule.updated",
        resource_type="approval_routing_rule",
        resource_id=rule.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        before=before,
        after=_rule_snapshot(rule),
    )
    db.commit()
    db.refresh(rule)
    group_names, user_names = _org_name_maps(db, org_id=current_user.org_id)
    return _serialize_rule(rule, group_names=group_names, user_names=user_names)


@router.put("/routing-rules/order")
def reorder_routing_rules(
    payload: RoutingReorderPayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    """Set rule priorities from the given order (10, 20, 30, …). Lower fires
    first, so the list order the admin sees becomes the resolution order."""
    rules = {
        r.id: r
        for r in db.scalars(
            select(ApprovalRoutingRule).where(
                ApprovalRoutingRule.org_id == current_user.org_id,
                ApprovalRoutingRule.deleted_at.is_(None),
            )
        ).all()
    }
    order_log = []
    for idx, rid in enumerate(payload.ordered_ids):
        rule = rules.get(rid)
        if rule is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown routing rule {rid}"
            )
        rule.priority = str((idx + 1) * 10)
        rule.updated_by_user_id = current_user.id
        order_log.append({"id": rule.id, "name": rule.name, "priority": rule.priority})
    write_audit_log(
        db,
        action="routing_rule.reordered",
        resource_type="approval_routing_rule",
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"order": order_log},
    )
    db.commit()
    group_names, user_names = _org_name_maps(db, org_id=current_user.org_id)
    ordered = sorted(
        rules.values(),
        key=lambda r: int(r.priority) if str(r.priority).isdigit() else 100,
    )
    return [_serialize_rule(r, group_names=group_names, user_names=user_names) for r in ordered]


@router.delete("/routing-rules/{rule_id}")
def delete_routing_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    """Soft-delete: the rule stops matching but its row survives so historical
    approval requests keep resolving their originating rule."""
    rule = _get_owned_rule(db, rule_id=rule_id, org_id=current_user.org_id)
    before = _rule_snapshot(rule)
    rule.deleted_at = datetime.now(UTC)
    rule.deleted_by_user_id = current_user.id
    rule.is_active = False
    rule.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="routing_rule.deleted",
        resource_type="approval_routing_rule",
        resource_id=rule.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        before=before,
    )
    db.commit()
    return {"ok": True}


@router.get("/routing-rules/audit")
def routing_rules_audit(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    """Chain-sealed history of routing-rule changes (create / update / reorder /
    delete), newest first, with the acting user resolved to a name."""
    rows = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.org_id == current_user.org_id,
            AuditLog.resource_type == "approval_routing_rule",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    ).all()
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id}
    names = (
        {
            u.id: (u.full_name or u.email)
            for u in db.scalars(select(User).where(User.id.in_(actor_ids))).all()
        }
        if actor_ids
        else {}
    )
    return [
        {
            "id": r.id,
            "action": r.action,
            "resource_id": r.resource_id,
            "actor_name": names.get(r.actor_user_id) or "System",
            "created_at": r.created_at,
            "before": r.before,
            "after": r.after,
            "metadata": r.metadata_json,
        }
        for r in rows
    ]


# --- Dry-run preview ------------------------------------------------------
class CriteriaPreviewPayload(BaseModel):
    criteria: dict = Field(default_factory=dict)
    sample: dict = Field(default_factory=dict)


def _label_chain(
    chain: list[dict], *, group_names: dict[str, str], user_names: dict[str, str]
) -> list[dict]:
    """Attach a human label to each resolved step target for the preview UI."""
    labelled = []
    for t in chain:
        label = (
            group_names.get(t.get("approver_group_id") or "")
            or user_names.get(t.get("approver_user_id") or "")
            or t.get("approver_role")
            or "Approver"
        )
        labelled.append(
            {
                "step_order": t.get("step_order"),
                "approver_group_id": t.get("approver_group_id"),
                "approver_user_id": t.get("approver_user_id"),
                "approver_role": t.get("approver_role"),
                "mode": t.get("mode", "any"),
                "routing_rule_id": t.get("routing_rule_id"),
                "approver_label": label,
            }
        )
    return labelled


@router.get("/contracts/{contract_id}/routing-preview")
def routing_preview(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Dry-run the approval chain this contract would get if submitted now —
    fast-lane verdict, every matched rule (with which ones the composed chain
    actually used), and the ordered, labelled chain. Creates nothing."""
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    result = preview_routing(db, contract=contract, org_id=current_user.org_id)
    group_names, user_names = _org_name_maps(db, org_id=current_user.org_id)
    result["chain"] = _label_chain(
        result["chain"], group_names=group_names, user_names=user_names
    )
    return result


@router.post("/routing-rules/preview")
def routing_rule_preview(
    payload: CriteriaPreviewPayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    """Does a draft rule's criteria match a hypothetical contract? Powers the
    live WHEN→THEN match badge in the rule builder — no rows written."""
    return {"matches": preview_criteria(criteria=payload.criteria, sample=payload.sample)}


# --- Submit & decide ------------------------------------------------------
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
async def decide_approval(
    approval_request_id: str,
    payload: ApprovalDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:decide")),
):
    approval = db.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_request_id)
        .with_for_update()
    )
    if approval is None or approval.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    if not _can_decide_approval(db, approval=approval, user=current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not assigned to decide this approval")
    await decide_in_app(
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
@limiter.limit(settings.rate_limit_token_decision)
async def decide_via_token(
    payload: TokenDecisionPayload,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Token-authenticated approval decision. No session auth: the single-use,
    expiring, email-bound token is the credential. Rate-limited per IP (F-02);
    ``response`` is required so slowapi can inject rate-limit headers."""
    _ = response
    approval = await redeem_token_decision(
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


@router.get("/review/{token}", response_model=ApprovalReviewResponse)
@limiter.limit(settings.rate_limit_token_decision)
def review_via_token(
    token: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Read-only review context for the emailed approver — the document + what
    they're approving. Token-authenticated, no login; does not consume the token."""
    _ = response
    return get_review_context_for_token(db, token=token)


def _user_role_names(user) -> set[str]:
    return {role.name for role in getattr(user, "roles", [])}


def _can_decide_approval(db: Session, *, approval: ApprovalRequest, user) -> bool:
    if has_permission(user.permission_values, "approval:admin"):
        return True
    if approval.requested_by_user_id == user.id:
        return False
    if approval.approver_user_id and approval.approver_user_id == user.id:
        return True
    if approval.approver_role and approval.approver_role in _user_role_names(user):
        return True
    if approval.approver_group_id:
        group = db.get(ApproverGroup, approval.approver_group_id)
        if (
            group is not None
            and group.org_id == user.org_id
            and any(member.id == user.id for member in group.members)
        ):
            return True
    return False


def _load_contracts_for_approvals(
    db: Session, *, approvals: list[ApprovalRequest], user
) -> dict[str, Contract]:
    """Fetch every contract referenced by ``approvals`` in one org-scoped IN query.

    Returns a ``{contract_id: Contract}`` map so the per-row visibility check can
    look up its contract without issuing a db.get() per approval (kills the N+1).
    Org-scoping the query means a stale/cross-org contract_id simply won't appear
    in the map, preserving the tenant boundary enforced by the previous db.get +
    user_can_access_contract path."""
    contract_ids = {a.contract_id for a in approvals if a.contract_id}
    if not contract_ids:
        return {}
    rows = db.scalars(
        select(Contract).where(
            Contract.org_id == user.org_id,
            Contract.id.in_(contract_ids),
        )
    ).all()
    return {contract.id: contract for contract in rows}


def _can_view_approval(
    db: Session,
    *,
    approval: ApprovalRequest,
    user,
    contracts: dict[str, Contract] | None = None,
) -> bool:
    if _can_decide_approval(db, approval=approval, user=user):
        return True
    if approval.requested_by_user_id == user.id:
        return True
    # Use the batched map when provided (list endpoint); fall back to a direct
    # lookup so any other caller keeps working unchanged.
    if contracts is not None:
        contract = contracts.get(approval.contract_id)
    else:
        contract = db.get(Contract, approval.contract_id)
    return bool(contract and user_can_access_contract(db, contract=contract, user=user))
