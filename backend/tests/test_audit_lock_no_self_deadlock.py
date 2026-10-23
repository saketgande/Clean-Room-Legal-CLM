"""An access-decision log must never make a request wait on its own audit lock.

``write_audit_log`` takes the app-wide audit-chain advisory lock, held until
the transaction commits. ``record_decision`` wrote on a second session inline:
a request that had already written an audit row and then hit an access check
waited on itself forever, and every other audit writer queued behind it — the
whole app froze for 25 minutes on 2026-09-28.
"""

import time
import uuid

from sqlalchemy import select, text

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core import authz
from app.core.audit import write_audit_log
from app.core.database import SessionLocal
from app.core.models import AuditLog


def test_a_request_holding_the_audit_lock_can_still_log_an_access_decision():
    held = SessionLocal()
    marker = f"deadlock-check-{uuid.uuid4().hex[:8]}"
    try:
        user = held.scalar(select(User).order_by(User.created_at))
        write_audit_log(held, action="test.hold", resource_type="test", resource_id=marker,
                        org_id=user.org_id, actor_user_id=user.id)  # lock now held, not committed

        started = time.monotonic()
        authz.record_decision(user=user, action="test:read", outcome="denied",
                              resource_type="test", resource_id=marker)
        assert time.monotonic() - started < 2, "record_decision blocked on the caller's own lock"

        held.commit()  # the request finishes; the decision row can now land
        check = SessionLocal()
        try:
            for _ in range(50):
                if check.scalar(select(AuditLog.id).where(AuditLog.resource_id == marker,
                                                          AuditLog.action == "access.denied")):
                    break
                time.sleep(0.1)
                check.rollback()
            else:
                raise AssertionError("the access decision was never written")
        finally:
            check.close()
    finally:
        held.rollback()
        held.close()


def test_sessions_end_a_transaction_left_open_too_long():
    """The safety net: Postgres ends a session idle inside a transaction, so a
    stuck request can't hold its locks for ever."""
    s = SessionLocal()
    try:
        assert s.execute(text("SHOW idle_in_transaction_session_timeout")).scalar() not in ("0", "0ms")
    finally:
        s.close()
