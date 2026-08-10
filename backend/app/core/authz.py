"""Access-decision logging (Method 8 audit baseline).

Real enforcement lives in ``require_permission`` (core/deps.py) and each
resource's own access predicate (e.g. ``user_can_access_contract`` in
contracts/access.py) — they call ``has_permission``/RBAC directly and log
through ``record_decision`` below. This module used to also define a
``check``/``authorize`` chokepoint that nothing ever called into; it was
removed rather than finished, since every real call site already does its own
check-then-log and routing them through here would have been a rename, not a
behavior change.

``record_decision`` writes on its OWN short-lived session so it is isolated
from (and survives the rollback of) the request transaction that a 403
aborts.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def record_decision(
    *,
    user,
    action: str,
    outcome: str,  # "allowed" | "denied"
    resource_type: str | None = None,
    resource_id: str | None = None,
    reason: str | None = None,
    request_id: str | None = None,
) -> None:
    """Append an access-decision row to the immutable audit log, on an isolated
    session so it persists even when the request transaction is rolled back by a
    403. Best-effort: a logging failure must never change the access outcome."""
    from app.core.audit import write_audit_log
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        write_audit_log(
            session,
            action=f"access.{outcome}",
            resource_type=resource_type or "authz",
            resource_id=resource_id,
            org_id=getattr(user, "org_id", None),
            actor_user_id=getattr(user, "id", None),
            request_id=request_id,
            metadata={
                "permission": action,
                "outcome": outcome,
                **({"reason": reason} if reason else {}),
            },
        )
        session.commit()
    except Exception:  # pragma: no cover - logging must not break auth
        session.rollback()
        logger.warning("failed to record access decision", exc_info=True)
    finally:
        session.close()
