from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.grants.service import granted_resource_ids, user_has_grant
from app.matters.models import Matter, MatterContract, MatterMember, MatterShare
from app.walls.service import user_is_walled, wall_block_filter

# --- Phase 3: mandatory access control (confidentiality / clearance) -------
# An ordered classification ladder. A user may read a contract only when its
# classification rank is <= the user's clearance rank. Admins bypass clearance
# (they administer it); ethical walls, by contrast, bind admins too.
CLEARANCE_LEVELS = ["public", "internal", "confidential", "restricted"]
_DEFAULT_CONTRACT_LEVEL = "internal"
_DEFAULT_USER_CLEARANCE = "confidential"


def _rank(level: str | None, default: str) -> int:
    try:
        return CLEARANCE_LEVELS.index(level or default)
    except ValueError:
        return CLEARANCE_LEVELS.index(default)


def user_clearance_rank(user: User) -> int:
    return _rank(getattr(user, "clearance", None), _DEFAULT_USER_CLEARANCE)


def _clearance_ok_sql(user: User):
    """SQL predicate: the row's confidentiality rank <= this user's clearance.
    Correlated on ``Contract``; the user's rank is a constant at build time."""
    limit = user_clearance_rank(user)
    contract_rank = case(
        {level: idx for idx, level in enumerate(CLEARANCE_LEVELS)},
        value=Contract.confidentiality,
        else_=CLEARANCE_LEVELS.index(_DEFAULT_CONTRACT_LEVEL),
    )
    return contract_rank <= limit


def clearance_permits(user: User, contract: Contract) -> bool:
    """Row check: may this user's clearance read this contract's classification?
    Admins bypass (they set the policy)."""
    if is_org_admin(user):
        return True
    contract_rank = _rank(getattr(contract, "confidentiality", None), _DEFAULT_CONTRACT_LEVEL)
    return contract_rank <= user_clearance_rank(user)


def _is_pending_approver(db: Session, *, contract: Contract, user: User) -> bool:
    """True if the user is assigned (directly, by role, or via an approver group)
    to a still-pending approval request on this contract."""
    from app.approvals.models import ApprovalRequest, ApproverGroup
    from app.core.enums import ApprovalStatus

    requests = db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.org_id == user.org_id,
            ApprovalRequest.contract_id == contract.id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
    ).all()
    if not requests:
        return False
    role_names = {role.name for role in getattr(user, "roles", [])}
    for req in requests:
        if req.approver_user_id and req.approver_user_id == user.id:
            return True
        if req.approver_role and req.approver_role in role_names:
            return True
        if req.approver_group_id:
            group = db.get(ApproverGroup, req.approver_group_id)
            if group is not None and any(m.id == user.id for m in group.members):
                return True
    return False


def accessible_contract_filter(user: User):
    # F-03: org scoping is ALWAYS enforced, including for admins. Previously this
    # returned true() for admins, which dropped the tenant boundary entirely and
    # leaked cross-org rows through any caller that JOINed Contract without its own
    # org_id filter. Admins now escalate WITHIN their org, never across it — matching
    # the row-level guard in user_can_access_contract().
    org_scope = Contract.org_id == user.org_id
    # Phase 3 deny-overrides, evaluated BEFORE any allow layer:
    #   * ethical walls bind everyone, including admins (a conflicted admin must
    #     still be sealed off);
    #   * clearance/MAC binds ordinary users; admins bypass it below.
    not_walled = wall_block_filter(user)
    if is_org_admin(user):
        return and_(org_scope, not_walled)
    project_membership = (
        select(MatterContract.id)
        .join(Matter, Matter.id == MatterContract.matter_id)
        .join(
            MatterMember,
            (MatterMember.matter_id == MatterContract.matter_id)
            & (MatterMember.org_id == MatterContract.org_id),
        )
        .where(
            MatterContract.contract_id == Contract.id,
            MatterContract.org_id == user.org_id,
            Matter.deleted_at.is_(None),
            MatterMember.user_id == user.id,
        )
        .exists()
    )
    matter_share = (
        select(MatterContract.id)
        .join(Matter, Matter.id == MatterContract.matter_id)
        .join(
            MatterShare,
            (MatterShare.matter_id == MatterContract.matter_id)
            & (MatterShare.org_id == MatterContract.org_id),
        )
        .where(
            MatterContract.contract_id == Contract.id,
            MatterContract.org_id == user.org_id,
            Matter.deleted_at.is_(None),
            MatterShare.shared_with_user_id == user.id,
            MatterShare.revoked_at.is_(None),
            or_(MatterShare.expires_at.is_(None), MatterShare.expires_at > utcnow()),
        )
        .exists()
    )
    return and_(
        org_scope,
        not_walled,
        _clearance_ok_sql(user),
        or_(
            Contract.owner_user_id == user.id,
            Contract.created_by_user_id == user.id,
            project_membership,
            matter_share,
            # Phase 2: a direct, time-bound resource grant on this contract.
            Contract.id.in_(granted_resource_ids(user, "contract")),
        ),
    )


def _log_deny_override(user: User, contract: Contract, reason: str) -> None:
    """Method-8 audit: a Phase-3 deny-override sealed this user off. Best-effort;
    isolated session inside record_decision so it survives any rollback."""
    from app.core.authz import record_decision

    record_decision(
        user=user,
        action="contract:read",
        outcome="denied",
        resource_type="contract",
        resource_id=contract.id,
        reason=reason,
    )


def user_can_access_contract(db: Session, *, contract: Contract, user: User) -> bool:
    if contract.org_id != user.org_id:
        return False
    # Phase 3 deny-overrides come FIRST and beat every allow — including admin,
    # owner and creator. An ethical wall or insufficient clearance is absolute.
    if user_is_walled(db, user=user, contract=contract):
        _log_deny_override(user, contract, "ethical_wall")
        return False
    if not clearance_permits(user, contract):
        _log_deny_override(user, contract, "insufficient_clearance")
        return False
    if is_org_admin(user) or contract.owner_user_id == user.id or contract.created_by_user_id == user.id:
        return True
    # An assigned approver can read the contract they're being asked to approve,
    # while a decision is pending.
    if _is_pending_approver(db, contract=contract, user=user):
        return True
    # Phase 2: a direct, time-bound resource grant on this contract.
    if user_has_grant(db, user=user, resource_type="contract", resource_id=contract.id):
        return True
    membership = (
        select(MatterContract.id)
        .join(Matter, Matter.id == MatterContract.matter_id)
        .join(
            MatterMember,
            (MatterMember.matter_id == MatterContract.matter_id)
            & (MatterMember.org_id == MatterContract.org_id),
        )
        .where(
            MatterContract.org_id == user.org_id,
            MatterContract.contract_id == contract.id,
            Matter.deleted_at.is_(None),
            MatterMember.user_id == user.id,
        )
        .limit(1)
    )
    if db.scalar(membership) is not None:
        return True
    return db.scalar(
        select(MatterContract.id)
        .join(Matter, Matter.id == MatterContract.matter_id)
        .join(
            MatterShare,
            (MatterShare.matter_id == MatterContract.matter_id)
            & (MatterShare.org_id == MatterContract.org_id),
        )
        .where(
            MatterContract.org_id == user.org_id,
            MatterContract.contract_id == contract.id,
            Matter.deleted_at.is_(None),
            MatterShare.shared_with_user_id == user.id,
            MatterShare.revoked_at.is_(None),
            or_(MatterShare.expires_at.is_(None), MatterShare.expires_at > utcnow()),
        )
        .limit(1)
    ) is not None
