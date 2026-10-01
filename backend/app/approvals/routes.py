from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.dependencies import get_approvals_service
from app.approvals.models import (
    ApprovalDecision,
    ApprovalRequest,
)
from app.approvals.service import ApprovalsService, _user_eligible_to_decide
from app.auth.models import User
from app.contracts.access import user_can_access_contract
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.config import settings
from app.core.deps import get_db, require_permission
from app.core.enums import ContractLifecycleStage, UserStatus
from app.core.rate_limit import limiter

router = APIRouter(prefix="/approvals", tags=["approvals"])


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



def _team_names(db: Session, ids: set[str]) -> dict[str, str]:
    from app.intake.models import IntakeTeam

    if not ids:
        return {}
    return {t.id: t.name for t in db.scalars(select(IntakeTeam).where(IntakeTeam.id.in_(ids))).all()}


def _user_brief(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "roles": [role.name for role in user.roles],
    }



# --- Approval requests ----------------------------------------------------
@router.get("")
def list_approvals(
    db: Session = Depends(get_db),
    approvals_service: ApprovalsService = Depends(get_approvals_service),
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
    team_names = _team_names(db, {r.approver_team_id for r in rows if r.approver_team_id})
    return [
        _serialize_approval(
            row,
            approvals_service=approvals_service,
            can_decide=_can_decide_approval(db, approval=row, user=current_user, contracts=contracts),
            team_names=team_names,
            contracts=contracts,
        )
        for row in rows
        if _can_view_approval(db, approval=row, user=current_user, contracts=contracts)
    ]


def _serialize_approval(
    req: ApprovalRequest,
    *,
    approvals_service: ApprovalsService,
    can_decide: bool,
    team_names: dict[str, str] | None = None,
    contracts: dict[str, Contract] | None = None,
) -> dict:
    """Approval row + a server-computed can_decide (so the UI shows the
    Approve/Reject buttons for team members, not just role/user matches).

    Carries the contract's own title and type: the queue previously resolved
    names against the contracts *list*, which excludes soft-deleted rows, so an
    approval still in flight against an archived contract rendered as "Untitled
    contract" even though the contract had a perfectly good name.
    """
    meta = req.metadata_json or {}
    contract = (contracts or {}).get(req.contract_id or "")
    return {
        "id": req.id,
        "org_id": req.org_id,
        "contract_id": req.contract_id,
        "contract_title": contract.title if contract else None,
        "contract_type": contract.contract_type if contract else None,
        "contract_archived": bool(contract is not None and contract.deleted_at is not None),
        "contract_version_id": req.contract_version_id,
        "status": req.status,
        "requested_by_user_id": req.requested_by_user_id,
        "approver_user_id": req.approver_user_id,
        "approver_role": req.approver_role,
        "approver_team_id": req.approver_team_id,
        "approver_team_name": (team_names or {}).get(req.approver_team_id or ""),
        "step_order": req.step_order,
        "mode": req.mode,
        "approvals": meta.get("approvals", 0),
        "needed": approvals_service._quorum_needed(req),
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
    approvals_service: ApprovalsService = Depends(get_approvals_service),
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
    # Grouped by the stable submission_batch_id stamped at creation time
    # (see submit_for_approval in service.py) rather than a time window, which
    # could conflate two chains submitted close together. Chains created
    # before that field existed have no batch id on the anchor — fall back to
    # the time-window heuristic for those.
    candidates = db.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == current_user.org_id,
            ApprovalRequest.contract_id == contract_id,
        )
        .order_by(ApprovalRequest.step_order.asc())
    ).all()
    anchor_batch_id = (anchor.metadata_json or {}).get("submission_batch_id")
    if anchor_batch_id:
        requests = [
            r for r in candidates
            if (r.metadata_json or {}).get("submission_batch_id") == anchor_batch_id
        ]
    else:
        requests = [
            r for r in candidates
            if r.created_at >= anchor.created_at - timedelta(seconds=10)
        ]

    user_ids = {r.approver_user_id for r in requests if r.approver_user_id}
    teams = _team_names(db, {r.approver_team_id for r in requests if r.approver_team_id})
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
                "needed": approvals_service._quorum_needed(req),
                "approver_label": (
                    teams.get(req.approver_team_id)
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


# --- Decide -----------------------------------------------------------------
# Approvals are only ever started by a workflow's Approval step
# (workflows.service → submit_contract_for_approval); there is no manual submit.
@router.post("/requests/{approval_request_id}/decision")
async def decide_approval(
    approval_request_id: str,
    payload: ApprovalDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
    approvals_service: ApprovalsService = Depends(get_approvals_service),
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
    # decide_in_app re-checks eligibility and applies the delegated-authority gate,
    # so every entry point (this route, the assistant tool) gets both.
    await approvals_service.decide_in_app(
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
    approvals_service: ApprovalsService = Depends(get_approvals_service),
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
    await approvals_service.reassign_rung(
        approval=approval, to_user=to_user, kind=payload.kind, actor=current_user,
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
    approvals_service: ApprovalsService = Depends(get_approvals_service),
):
    """Token-authenticated approval decision. No session auth: the single-use,
    expiring, email-bound token is the credential. Rate-limited per IP (F-02);
    ``response`` is required so slowapi can inject rate-limit headers."""
    _ = response
    approval = await approvals_service.redeem_token_decision(
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
    approvals_service: ApprovalsService = Depends(get_approvals_service),
):
    """Read-only review context for the emailed approver — the document + what
    they're approving. Token-authenticated, no login; does not consume the token."""
    _ = response
    return approvals_service.get_review_context_for_token(token=token)


def _can_decide_approval(
    db: Session,
    *,
    approval: ApprovalRequest,
    user,
    contracts: dict[str, Contract] | None = None,
) -> bool:
    """Whether the Approve/Reject buttons should show — eligibility AND the
    contract-stage gate. Mirrors ApprovalSubject.guard_can_decide so can_decide
    never promises an action that would 409 (a pending approval on a contract
    that isn't in the approval stage is not actionable yet)."""
    if not _user_eligible_to_decide(db, approval=approval, user=user):
        return False
    if approval.contract_id:
        contract = (
            contracts.get(approval.contract_id)
            if contracts is not None
            else db.get(Contract, approval.contract_id)
        )
        if (
            contract is not None
            and contract.lifecycle_stage != ContractLifecycleStage.APPROVAL
        ):
            return False
    return True


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
