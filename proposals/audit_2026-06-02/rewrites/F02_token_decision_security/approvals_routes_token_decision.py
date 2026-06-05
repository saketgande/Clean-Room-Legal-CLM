"""F-02 rewrite — ``decide_via_token`` route (rate-limit + uniform error + comment cap).

Replaces ``backend/app/approvals/routes.py:141-162`` and the
``TokenDecisionPayload`` Pydantic schema at ``:33-36``. Adds the
slowapi decorator (using ``settings.approval_token_rate_limit``),
caps ``comment`` at 4_000 chars (resolves F-25 too), and stops
leaking the token's HTTP-state (404/409 oracle in F-02).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.approvals.service import redeem_token_decision  # rewritten — see service file
from app.core.config import settings
from app.core.deps import get_db
from app.core.rate_limit import limiter

router = APIRouter(prefix="/approvals", tags=["approvals"])

_logger = logging.getLogger(__name__)


class TokenDecisionPayload(BaseModel):
    """Payload for an unauthenticated token-bound approval decision."""

    token: str = Field(min_length=16, max_length=200)
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str | None = Field(default=None, max_length=4_000)


@router.post("/token-decision")
@limiter.limit(lambda: settings.approval_token_rate_limit)
def decide_via_token(
    payload: TokenDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Token-authenticated approval decision; uniform error to defeat oracle."""
    approval = redeem_token_decision(
        db,
        token=payload.token,
        decision=payload.decision,
        comment=payload.comment,
        request_id=getattr(request.state, "request_id", None),
        remote_ip=request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(approval)
    return {
        "approval_request_id": approval.id,
        "status": approval.status,
        "contract_id": approval.contract_id,
    }
