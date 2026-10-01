from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.core.access import is_org_admin
from app.grants.service import granted_resource_ids, user_has_grant
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
    return and_(
        org_scope,
        not_walled,
        _clearance_ok_sql(user),
        or_(
            Contract.owner_user_id == user.id,
            Contract.created_by_user_id == user.id,
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


class ContractAccessService:
    """DB-backed contract access checks.

    Part of the DI migration (see backend/DI_MIGRATION.md). The pure predicate
    builders above (``accessible_contract_filter``, ``clearance_permits``)
    don't touch the database and stay as module-level functions — only the
    functions that actually query ``db`` move here.
    """

    def __init__(self, db: Session):
        self.db = db

    def _is_pending_approver(self, *, contract: Contract, user: User) -> bool:
        """True if the user is assigned (directly, by role, or via a team)
        to a still-pending approval request on this contract."""
        from app.approvals.models import ApprovalRequest
        from app.core.enums import ApprovalStatus
        from app.intake.teams import member_users

        requests = self.db.scalars(
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
            if req.approver_team_id and any(
                m.id == user.id
                for m in member_users(self.db, team_id=req.approver_team_id, org_id=user.org_id)
            ):
                return True
        return False

    def _is_workflow_assignee(self, *, contract: Contract, user: User) -> bool:
        """True if the user is (or was) the assignee of any step on this contract's
        governance workflow run — e.g. the "Legal Review" human_task reviewer.
        Unlike an "approval" step (modeled as an ApprovalRequest and already
        covered by _is_pending_approver), a human_task/ai_task/counterparty step
        only ever records its assignee on WorkflowStepRun.assignee_user_id — with
        no grant or ApprovalRequest created alongside it. Without this, the very
        person a workflow assigns to review a contract gets 404s on every one of
        its endpoints."""
        from app.workflows.models import WorkflowRun, WorkflowStepRun

        return self.db.scalar(
            select(WorkflowStepRun.id)
            .join(WorkflowRun, WorkflowRun.id == WorkflowStepRun.flow_run_id)
            .where(
                WorkflowRun.contract_id == contract.id,
                WorkflowRun.org_id == user.org_id,
                WorkflowStepRun.assignee_user_id == user.id,
            )
            .limit(1)
        ) is not None

    def user_can_access_contract(self, *, contract: Contract, user: User) -> bool:
        db = self.db
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
        if self._is_pending_approver(contract=contract, user=user):
            return True
        # Same idea for a non-approval workflow step (human_task/ai_task/etc.)
        # assigned directly to this user.
        if self._is_workflow_assignee(contract=contract, user=user):
            return True
        # Phase 2: a direct, time-bound resource grant on this contract.
        return user_has_grant(db, user=user, resource_type="contract", resource_id=contract.id)


# DI-MIGRATION: temporary wrapper — remove once all callers use
# get_contract_access_service(). Tracked in backend/DI_MIGRATION.md
def user_can_access_contract(db: Session, *, contract: Contract, user: User) -> bool:
    return ContractAccessService(db).user_can_access_contract(contract=contract, user=user)
