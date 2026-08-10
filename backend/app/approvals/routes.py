from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.approvals.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRoutingRule,
    ApprovalRoutingStep,
    ApproverGroup,
)
from app.approvals.service import (
    _quorum_needed,
    decide_in_app,
    get_review_context_for_token,
    reassign_rung,
    redeem_token_decision,
    submit_contract_for_approval,
)
from app.auth.models import User
from app.contracts.access import user_can_access_contract
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.config import settings
from app.core.deps import get_db, require_permission
from app.core.enums import UserStatus
from app.core.rate_limit import limiter
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


class ReassignPayload(BaseModel):
    to_user_id: str
    kind: str = Field(default="delegate", pattern="^(delegate|escalate)$")


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


def _validate_org_user(db: Session, org_id: str, user_id: str | None) -> None:
    if not user_id:
        return
    u = db.get(User, user_id)
    if u is None or u.org_id != org_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Approver user must belong to this organization",
        )


def _validate_org_group(db: Session, org_id: str, group_id: str | None) -> None:
    if not group_id:
        return
    g = db.get(ApproverGroup, group_id)
    if g is None or g.org_id != org_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Approver group must belong to this organization",
        )


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
    group_ids = {r.approver_group_id for r in rows if r.approver_group_id}
    group_names = {
        g.id: g.name
        for g in db.scalars(
            select(ApproverGroup).where(ApproverGroup.id.in_(group_ids))
        ).all()
    } if group_ids else {}
    return [
        _serialize_approval(
            row,
            db=db,
            can_decide=_can_decide_approval(db, approval=row, user=current_user),
            group_names=group_names,
        )
        for row in rows
        if _can_view_approval(db, approval=row, user=current_user, contracts=contracts)
    ]


def _serialize_approval(
    req: ApprovalRequest, *, db: Session, can_decide: bool, group_names: dict[str, str] | None = None
) -> dict:
    """Approval row + a server-computed can_decide (so the UI shows the
    Approve/Reject buttons for group members, not just role/user matches)."""
    meta = req.metadata_json or {}
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
        "approver_group_name": (group_names or {}).get(req.approver_group_id or ""),
        "routing_rule_id": req.routing_rule_id,
        "step_order": req.step_order,
        "mode": req.mode,
        "approvals": meta.get("approvals", 0),
        "needed": _quorum_needed(db, req),
        "reassign": meta.get("reassign"),
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
                "mode": req.mode,
                "approvals": (req.metadata_json or {}).get("approvals", 0),
                "needed": _quorum_needed(db, req),
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
        select(ApprovalRoutingRule).where(ApprovalRoutingRule.org_id == current_user.org_id)
    ).all()
    group_names, user_names = _org_name_maps(db, org_id=current_user.org_id)
    return [
        _serialize_rule(r, group_names=group_names, user_names=user_names) for r in rules
    ]


@router.post("/routing-rules", status_code=status.HTTP_201_CREATED)
def create_routing_rule(
    payload: RoutingRulePayload,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    def _validate_user(user_id: str | None) -> None:
        if not user_id:
            return
        approver = db.get(User, user_id)
        if approver is None or approver.org_id != current_user.org_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Approver user must belong to this organization",
            )

    def _validate_group(group_id: str | None) -> None:
        if not group_id:
            return
        group = db.get(ApproverGroup, group_id)
        if group is None or group.org_id != current_user.org_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Approver group must belong to this organization",
            )

    # Validate the legacy single-approver fields (used only when no steps given).
    _validate_user(payload.approver_user_id)

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

    for idx, step in enumerate(payload.steps):
        if not (step.approver_group_id or step.approver_user_id or step.approver_role):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Step {idx + 1} needs an approver group, user, or role",
            )
        _validate_group(step.approver_group_id)
        _validate_user(step.approver_user_id)
        db.add(
            ApprovalRoutingStep(
                org_id=current_user.org_id,
                rule_id=rule.id,
                step_order=idx + 1,
                approver_group_id=step.approver_group_id,
                approver_user_id=step.approver_user_id,
                approver_role=step.approver_role,
                mode=step.mode,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
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
    """Replace a routing rule in place (name, priority, criteria, active, chain).
    The whole rule is authoritative — the payload is the same shape as create."""
    rule = db.get(ApprovalRoutingRule, rule_id)
    if rule is None or rule.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routing rule not found")

    _validate_org_user(db, current_user.org_id, payload.approver_user_id)
    rule.name = payload.name
    rule.priority = payload.priority
    rule.criteria = payload.criteria
    rule.is_active = payload.is_active
    rule.approver_role = payload.approver_role if not payload.steps else None
    rule.approver_user_id = payload.approver_user_id if not payload.steps else None
    rule.updated_by_user_id = current_user.id

    # Replace the ordered chain (delete-orphan cascade clears the old steps).
    for old in list(rule.steps):
        db.delete(old)
    db.flush()
    for idx, step in enumerate(payload.steps):
        if not (step.approver_group_id or step.approver_user_id or step.approver_role):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Step {idx + 1} needs an approver group, user, or role",
            )
        _validate_org_group(db, current_user.org_id, step.approver_group_id)
        _validate_org_user(db, current_user.org_id, step.approver_user_id)
        db.add(
            ApprovalRoutingStep(
                org_id=current_user.org_id,
                rule_id=rule.id,
                step_order=idx + 1,
                approver_group_id=step.approver_group_id,
                approver_user_id=step.approver_user_id,
                approver_role=step.approver_role,
                mode=step.mode,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
        )

    db.commit()
    db.refresh(rule)
    group_names, user_names = _org_name_maps(db, org_id=current_user.org_id)
    return _serialize_rule(rule, group_names=group_names, user_names=user_names)


@router.delete("/routing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routing_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:admin")),
):
    rule = db.get(ApprovalRoutingRule, rule_id)
    if rule is None or rule.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routing rule not found")
    # Existing approvals keep their materialised chain; drop the provenance FK so
    # it doesn't block deletion.
    db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.routing_rule_id == rule_id)
        .values(routing_rule_id=None)
    )
    db.delete(rule)  # steps cascade (delete-orphan)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    # Phase 4 ABAC gate: approving commits the company — verify the decider's
    # delegated authority covers this contract's value/type/jurisdiction/risk.
    # Rejections are never gated. Dormant until a policy for the action exists.
    if payload.decision == "approve":
        from app.authority.service import enforce_authority
        from app.contracts.models import Contract

        # DoA gate reads value/type/jurisdiction/risk off the subject — a contract
        # or (duck-typed identically) an intake-request approval subject.
        gate_subject = None
        if approval.contract_id:
            gate_subject = db.get(Contract, approval.contract_id)
        elif approval.intake_request_id:
            from app.intake.approval_bridge import build_intake_subject

            gate_subject = build_intake_subject(
                db, approval.intake_request_id, org_id=current_user.org_id
            )
        if gate_subject is not None:
            enforce_authority(
                db,
                user=current_user,
                action="contract:approve",
                contract=gate_subject,
                resource_type="approval_request",
                resource_id=approval.id,
                request_id=getattr(request.state, "request_id", None),
            )
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


@router.post("/requests/{approval_request_id}/reassign")
async def reassign_approval(
    approval_request_id: str,
    payload: ReassignPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("approval:decide")),
):
    """Delegate or escalate a pending rung to another person. Allowed for the
    rung's current approver (or an approval admin) — same gate as deciding it."""
    approval = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_request_id).with_for_update()
    )
    if approval is None or approval.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    if not _can_decide_approval(db, approval=approval, user=current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not assigned to move this approval")
    to_user = db.get(User, payload.to_user_id)
    if to_user is None or to_user.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target user not found")
    await reassign_rung(
        db, approval=approval, to_user=to_user, kind=payload.kind, actor=current_user,
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
