"""F-09 rewrite — ``assistant/routes._ensure_contract_handle`` delegates to CC-2.

Replaces ``backend/app/assistant/routes.py:539-587``. The old body had
its own ``len(...).all()`` + INSERT race, plus a unique check on the
``requested_handle`` path that diverged from the controller copy
(Agent 2 F-30). All of that now lives in ``allocate_contract_handle``
(CC-2). This route helper becomes a thin wrapper that preserves the
public function name for ``add_contract_handle`` and ``stream_session``
callers.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.session_state import allocate_contract_handle
from app.assistant.models import AssistantContractHandle, AssistantSession
from app.auth.models import User


def _ensure_contract_handle(
    db: Session,
    *,
    session: AssistantSession,
    contract_id: str,
    current_user: User,
    requested_handle: str | None = None,
) -> AssistantContractHandle:
    """Return (allocating if needed) the per-session handle for this contract."""
    return allocate_contract_handle(
        db,
        org_id=current_user.org_id,
        session_id=session.id,
        contract_id=contract_id,
        user_id=current_user.id,
        requested_handle=requested_handle,
    )
