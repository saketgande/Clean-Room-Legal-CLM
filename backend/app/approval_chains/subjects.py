"""Resolves ``(module, module_record_id)`` back to the EXISTING subject object
(``ContractSubject`` | ``IntakeApprovalSubject``) so the chain engine drives the
same lifecycle hooks the legacy ladder drives (plan.md "Lifecycle parity").

No lifecycle logic is reimplemented here — every hook call goes straight
through to the existing subject protocol methods (``precheck``,
``guard_can_decide``, ``on_submit``, ``on_reject``, ``on_complete``). Every
import across the ``approvals``/``intake`` <-> ``approval_chains`` boundary is
function-local in both directions, matching this codebase's existing
lazy-import convention for that boundary.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session


def resolve_subject(db: Session, *, module: str, record_id: str, org_id: str) -> Any:
    """Rebuild the subject object driving lifecycle hooks for ``module``.

    ``module`` is exactly ``"contract"`` or ``"intake_request"`` — the same
    two values ``ApprovalChainInstance.module`` is constrained to. Raises
    HTTPException(404) when the underlying record is missing or belongs to
    another org (via the domain's own lookup), HTTPException(422) for an
    unrecognized module (fails closed, matching ``facts.build_facts``).
    """
    if module == "contract":
        from app.approvals.service import ContractSubject
        from app.contracts.models import Contract

        contract = db.get(Contract, record_id)
        if contract is None or contract.org_id != org_id:
            raise HTTPException(404, "Contract not found")
        return ContractSubject(contract)

    if module == "intake_request":
        from app.intake.approval_bridge import build_intake_subject

        return build_intake_subject(db, record_id, org_id=org_id)

    raise HTTPException(422, f"Unknown module '{module}'")
