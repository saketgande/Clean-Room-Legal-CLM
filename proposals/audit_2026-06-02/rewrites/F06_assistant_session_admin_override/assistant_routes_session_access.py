"""F-06 rewrite — assistant session read/write split with admin-read override.

Replaces ``backend/app/assistant/routes.py:512-520`` (the single
``_get_session_for_user`` helper). The original raised 404 on any
non-creator access, even for org admins investigating a runaway tool
loop. Now there are TWO helpers:

- ``_get_session_for_user_read(...)`` — allows org admins (CC-1) to
  read any session in their org. Each admin-read writes an
  ``assistant.session.admin_read`` audit row.
- ``_get_session_for_user_write(...)`` — creator-only, identical to
  the original behavior for mutating operations.

Compatible call-site updates: every read-only endpoint
(``GET /sessions/{id}``, ``GET /sessions/{id}/messages``,
``GET /sessions/{id}/runs``, etc.) switches to ``_read``; everything
mutating (``PATCH /sessions/{id}``, ``POST /sessions/{id}/contracts``,
``POST /sessions/{id}/stream``) stays on ``_write``.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.assistant.models import AssistantSession
from app.auth.models import User
from app.core.access_policy import ContractAccessPolicy
from app.core.audit import write_audit_log

_logger = logging.getLogger(__name__)


def _get_session_for_user_write(
    db: Session,
    *,
    session_id: str,
    current_user: User,
) -> AssistantSession:
    """Fetch session for a mutating operation; creator-only (no admin bypass)."""
    session = db.get(AssistantSession, session_id)
    if (
        session is None
        or session.org_id != current_user.org_id
        or session.created_by_user_id != current_user.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")
    return session


def _get_session_for_user_read(
    db: Session,
    *,
    session_id: str,
    current_user: User,
    request_id: str | None = None,
) -> AssistantSession:
    """Fetch session for a read operation; allows org admins on same-org sessions."""
    session = db.get(AssistantSession, session_id)
    if session is None or session.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")
    if not ContractAccessPolicy.can_read_session(session, current_user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")
    if session.created_by_user_id != current_user.id:
        # Admin reading another user's session — leave a tamper-evident trace.
        try:
            write_audit_log(
                db,
                action="assistant.session.admin_read",
                resource_type="assistant_session",
                resource_id=session.id,
                org_id=session.org_id,
                actor_user_id=current_user.id,
                request_id=request_id,
                after={
                    "session_creator_user_id": session.created_by_user_id,
                },
            )
        except Exception:  # noqa: BLE001 - audit failure must not break read
            _logger.exception(
                "assistant.session.admin_read.audit_failed",
                extra={
                    "session_id": session.id,
                    "actor_user_id": current_user.id,
                },
            )
    return session
