"""Bridge: run a Legal Intake request through the shared approval ladder.

``app/approvals`` owns the ladder mechanics (ordered ApprovalRequest steps,
per-step activation, decisions, tokens, audit). This module supplies the
*subject* that engine drives for an intake request — the attributes
Delegation-of-Authority reads (same names as Contract) plus the lifecycle
hooks that move the request as the chain starts / rejects / completes.

Kept separate from ``intake/service.py`` so ``app/approvals`` can lazily import
it without a package cycle (approvals ← intake.approval_bridge → intake.service).
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.config import settings
from app.intake.models import IntakeRequest

# Field keys we try, in order, to read a monetary value off a request's captured
# fields — so authority limits can be checked. Free-form; refined later.
_VALUE_KEYS = ("value", "new_value", "renew_value", "amount", "contract_value", "deal_value", "annual_value")
# Intake priority already aligns 1:1 with the DoA risk bands.
_PRIORITY_TO_BAND = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


class IntakeApprovalSubject:
    """An intake request driven through the approval ladder."""

    kind = "intake_request"
    allow_fast_lane = False
    version_id = None  # intake requests have no contract version

    def __init__(self, request: IntakeRequest, *, type_key: str | None):
        self.request = request
        self._type_key = type_key

    # --- attributes read by Delegation of Authority (_grant_covers) ---------
    @property
    def id(self) -> str:
        return self.request.id

    @property
    def org_id(self) -> str:
        return self.request.org_id

    @property
    def title(self) -> str:
        return f"{self.request.ref} · {self.request.type_label}"

    @property
    def value_amount(self):
        fv = self.request.field_values or {}
        for key in _VALUE_KEYS:
            raw = fv.get(key)
            if raw is None:
                continue
            try:
                return float(str(raw).replace(",", "").replace("$", "").strip())
            except (TypeError, ValueError):
                continue
        return None

    @property
    def contract_type(self):
        return self._type_key or self.request.type_label

    @property
    def currency(self):
        fv = self.request.field_values or {}
        # The forms ask for a currency beside the value; older ones had none.
        return fv.get("currency") or (settings.default_currency if fv.get("request_form") else None)

    @property
    def jurisdiction(self):
        fv = self.request.field_values or {}
        return fv.get("jurisdiction") or fv.get("governing_law")

    @property
    def risk_band(self):
        return _PRIORITY_TO_BAND.get((self.request.priority or "").lower())

    @property
    def risk_level(self):
        return self.risk_band

    # --- lifecycle hooks ------------------------------------------------------
    def precheck(self, db: Session) -> None:
        if self.request.status == "closed":
            raise HTTPException(409, "Request is closed — file a follow-up")

    def try_fast_lane(self, db: Session, *, user: User, request_id: str | None) -> bool:
        return False

    def guard_can_decide(self, db: Session) -> None:
        if self.request.status == "closed":
            raise HTTPException(409, "Request is closed — file a follow-up")

    def _transition(
        self, db: Session, *, actor_user_id: str | None, request_id: str | None, **kwargs
    ) -> None:
        from app.intake.service import _transition

        actor = db.get(User, actor_user_id) if actor_user_id else None
        _transition(db, request=self.request, actor=actor, request_id=request_id, **kwargs)

    def on_submit(self, db: Session, *, actor_user_id: str | None, request_id: str | None) -> None:
        # M6: no to_stage. "approval" is not a stage on the intake spine
        # (new → assigned → review → complete); writing it reset the governance
        # tracker to step 0. Submitting for approval keeps the current stage and
        # records the transition in the audit + timeline only.
        self._transition(
            db,
            actor_user_id=actor_user_id,
            request_id=request_id,
            audit_action="intake.submitted_for_approval",
            timeline_title="Submitted for approval",
        )

    def on_reject(
        self, db: Session, *, actor_user_id: str | None, comment: str | None, request_id: str | None
    ) -> None:
        # M6: to_status only, no to_stage. "triage" is not a stage on the intake
        # spine — resetting the stage on reject bounced the governance tracker to
        # step 0. Reopen the request (status) and keep it on its current stage.
        self._transition(
            db,
            actor_user_id=actor_user_id,
            request_id=request_id,
            to_status="open",
            audit_action="intake.approval_rejected",
            after={"comment": comment},
            timeline_title="Approval rejected — back to queue",
        )

    def finalize_approved(
        self, db: Session, *, actor_user_id: str | None, request_id: str | None = None
    ) -> None:
        """The single terminal 'intake request approved' transition. Reached by
        the ladder-only path (``on_complete`` when no workflow is driving the
        request) and by a workflow completing (the flow owns the terminal — see
        app/flows/service._finalize_intake_if_approved). Idempotent: a no-op once
        the request is already terminal."""
        if self.request.status in ("approved", "closed"):
            return
        self._transition(
            db,
            actor_user_id=actor_user_id,
            request_id=request_id,
            to_status="approved",
            to_stage="complete",
            audit_action="intake.approved",
            timeline_title="Approved — approval complete",
        )

    def on_complete(
        self, db: Session, *, actor_user_id: str | None, request_id: str | None
    ) -> None:
        # Workflow owns the terminal when a workflow is driving this request: a
        # single ladder chain is one gate of possibly several, so record the
        # gate passing and let the flow finalize once every step is done. A
        # request with no active flow (submitted straight into the ladder)
        # finalizes here.
        from app.workflows.service import get_run_for_request

        run = get_run_for_request(db, request_id=self.request.id, org_id=self.request.org_id)
        if run is not None and run.status in ("running", "waiting"):
            self._transition(
                db,
                actor_user_id=actor_user_id,
                request_id=request_id,
                audit_action="intake.approval_gate_passed",
                timeline_title="Approval gate cleared — workflow continues",
            )
            return
        self.finalize_approved(
            db, actor_user_id=actor_user_id, request_id=request_id
        )


def build_intake_subject(db: Session, request_id: str, *, org_id: str) -> IntakeApprovalSubject:
    request = db.get(IntakeRequest, request_id)
    if request is None or request.org_id != org_id:
        raise HTTPException(404, "Request not found")
    return IntakeApprovalSubject(request, type_key=None)


async def submit_request_for_approval(
    db: Session,
    *,
    actor: User,
    request: IntakeRequest,
    approver_user_id: str | None = None,
    approver_team_id: str | None = None,
    approver_role: str | None = None,
    mode: str = "any",
    request_id: str | None = None,
):
    """Start the approval ladder for an intake request with the named approver
    (see app/approvals.plan_chain)."""
    from app.approvals.service import submit_subject_for_approval

    subject = IntakeApprovalSubject(request, type_key=None)
    return await submit_subject_for_approval(
        db,
        user=actor,
        subject=subject,
        approver_user_id=approver_user_id,
        approver_team_id=approver_team_id,
        approver_role=approver_role,
        mode=mode,
        request_id=request_id,
    )
