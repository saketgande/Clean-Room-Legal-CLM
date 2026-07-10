"""Central authorization choke-point + access-decision logging.

This is the single seam the legal-RBAC pipeline plugs into. Today it wraps the
existing additive RBAC (``has_permission``) plus an optional object-level
predicate; later phases layer the deny-overrides (clearance / MAC, ethical
walls) and the unified ``resource_grant`` ALLOW layer *here*, in one place, so
every caller gets the same decision in the same order.

Method 8 (audit) baseline: every DENY — and optionally every ALLOW — is written
to the immutable, hash-chained audit log. Decision logging uses its OWN
short-lived session so it is isolated from (and survives the rollback of) the
request transaction that a 403 aborts.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from app.core.rbac import has_permission

logger = logging.getLogger(__name__)


def check(user, action: str) -> bool:
    """Pure RBAC capability check (no side effects, no object context)."""
    return has_permission(user.permission_values, action)


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


def authorize(
    *,
    user,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    allow: bool = True,
    log_allow: bool = False,
    request_id: str | None = None,
):
    """Enforce ``action`` for ``user``.

    ``allow`` is an optional pre-computed object-level predicate (pass the result
    of a row-access check; defaults to True for type-only checks). Raises 403 and
    logs the denial when either the RBAC capability or the object predicate fails.
    Returns ``user`` on success. This is the one function to grow the legal-RBAC
    deny/grant pipeline in.
    """
    has_cap = check(user, action)
    permitted = has_cap and bool(allow)
    if not permitted:
        record_decision(
            user=user,
            action=action,
            outcome="denied",
            resource_type=resource_type,
            resource_id=resource_id,
            reason="missing_permission" if not has_cap else "object_denied",
            request_id=request_id,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Not authorized: {action}")
    if log_allow:
        record_decision(
            user=user,
            action=action,
            outcome="allowed",
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
        )
    return user
